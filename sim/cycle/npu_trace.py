"""Offline NPU-only replay: modeled per-work cycles, never a system timeline."""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import TextIO

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.gemmini_replay_contract import compatible, hardware_contract
from scripts.gemmini_resolve_profile import BuildFailure
from sim.cycle import cli
from sim.cycle.input_snapshot import snapshot_file
from sim.cycle.certificate_contract import read_document, validate_certificate
from sim.cycle.npu_trace_integrity import read_records, start_trace
from sim.cycle.npu_trace_schema import INPUT_KEYS, Record, SCHEMA, VERSION, Work, integer, object_value, require
from sim.cycle.optrace_schema import REPLAY_MAX_CYCLES
from sim.cycle.run_aware_certificate import validate_run_certificate


def validate_trace(path: Path) -> Record:
    records = read_records(path)
    state = start_trace(records)
    for record in records:
        state.consume(record)
    summary = state.summary()
    summary.update(validation_scope='STRUCTURAL_ONLY', cycle_model_validation='NOT_CHECKED')
    return summary


def model_document(profile: str, work: Work) -> Record:
    keys = ('m', 'n', 'k', 'tile_i', 'tile_j', 'tile_k', *INPUT_KEYS[6:])
    request: Record = dict(zip(keys, work.inputs, strict=True))
    request.update(accepted_cycle=1, logical_work_id=work.identity, submission='planner-blocks', record_events=0)
    document: Record = {'profile': profile, 'limits': {'max_cycles': REPLAY_MAX_CYCLES}, 'request': request}
    if work.provenance == 'residual':
        require(work.original_k is not None and bool(work.runs),
                'legacy block-local residual trace cannot be current run-aware work')
        document['original_k'] = work.original_k
        document['runs'] = [{'original_block_id': span.original_block_id,
                             'original_k_mask': span.original_k_mask,
                             'compact_k_begin': span.compact_k_begin,
                             'compact_k_count': span.compact_k_count} for span in work.runs]
    return document


def work_binding(work: Work) -> str:
    view = {'inputs': work.inputs, 'original_k': work.original_k,
            'runs': [(span.original_block_id, span.original_k_mask,
                      span.compact_k_begin, span.compact_k_count) for span in work.runs],
            'row_map': [(row.source_row, row.lane_id) for row in work.row_map],
            'residual_work_revision': work.residual_work_revision}
    return hashlib.sha256(json.dumps(view, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ReplayArtifacts:
    library: Path
    certificate: Path
    run_certificate: Path | None = None


@dataclass(frozen=True, slots=True)
class ReplayOutputs:
    results: Path
    summary: Path


@dataclass(frozen=True, slots=True)
class InputSnapshot:
    """One immutable input copy plus the source identity to recheck at publication."""
    source: Path
    snapshot: Path
    identity: tuple[int, int, int, int, int]


def _identity(path: Path) -> tuple[int, int, int, int, int]:
    state = path.stat()
    return state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns, state.st_ctime_ns


def snapshot_inputs(paths: tuple[Path, ...], directory: Path) -> tuple[InputSnapshot, ...]:
    """Copy each source through one descriptor before parsing any source content."""
    snapshots: list[InputSnapshot] = []
    for index, original in enumerate(paths):
        source = original.resolve(strict=True)
        descriptor = os.open(source, os.O_RDONLY)
        try:
            before = _identity(source)
            snapshot = directory / f'{index:02d}-{source.name}'
            snapshot_file(descriptor, snapshot)
            descriptor_state = os.fstat(descriptor)
            descriptor_identity = (descriptor_state.st_dev, descriptor_state.st_ino, descriptor_state.st_size,
                                   descriptor_state.st_mtime_ns, descriptor_state.st_ctime_ns)
            require(before == _identity(source) == descriptor_identity,
                    'input changed while snapshotting')
            snapshots.append(InputSnapshot(source, snapshot, before))
        finally:
            os.close(descriptor)
    return tuple(snapshots)


def verify_input_snapshots(snapshots: tuple[InputSnapshot, ...], context: str) -> None:
    """Reject changed sources before a result can be published."""
    require(all(snapshot.identity == _identity(snapshot.source) for snapshot in snapshots),
            'input changed during ' + context)


def _replay(trace_path: Path, artifacts: ReplayArtifacts, stream: TextIO) -> Record:
    cert = read_document(artifacts.certificate)
    validate_certificate(cert, artifacts.library)
    run_scope = (validate_run_certificate(artifacts.run_certificate, artifacts.library)
                 if artifacts.run_certificate is not None else None)
    records = read_records(trace_path)
    state = start_trace(records)
    contracts = object_value(cert['hardware_contracts'])
    compatible(state.run.contract, object_value(contracts.get(state.run.profile)))
    compatible(state.run.contract, hardware_contract(state.run.profile))
    identities: Record = {'profile': state.run.profile,
                          'cycle_library_sha256': hashlib.sha256(artifacts.library.read_bytes()).hexdigest(),
                          'certificate_sha256': hashlib.sha256(artifacts.certificate.read_bytes()).hexdigest(),
                          'certificate_schema': cert['schema'], 'certificate_version': cert['version'],
                          'producer_execution_kind': 'CPU_FUNCTIONAL', 'target_work_validation': 'PASS',
                          'cycle_model_validation': 'CURRENT_CERTIFIED',
                          'actual_rtl_acceptance_in_collection': 'NOT_APPLICABLE',
                          'run_aware_certificate_sha256': (hashlib.sha256(artifacts.run_certificate.read_bytes()).hexdigest()
                                                           if artifacts.run_certificate is not None else None),
                          'run_aware_certificate_scope': run_scope,
                          'accounting_kind': 'isolated-work-accounting', 'cycle_unit': 'cycles'}
    per_layer: defaultdict[str, int] = defaultdict(int)
    per_phase: defaultdict[int, int] = defaultdict(int)
    large_k: dict[tuple[str, int, tuple[int, ...]], Record] = {}

    cache: dict[str, Record] = {}
    for record in records:
        work = state.consume(record)
        if work is None:
            continue
        require(work.provenance != 'residual' or bool(work.runs),
                'legacy block-local residual trace cannot be upgraded to run-aware replay')
        require(work.provenance != 'residual' or run_scope == 'PRODUCTION_GENERATED',
                'production-generated run-aware certificate required for current residual replay')
        binding = work_binding(work)
        if binding not in cache:
            answer = cli.estimate(artifacts.library, model_document(state.run.profile, work))
            require(answer.get('status') == 'PASS', 'cycle model rejected target work')
            if len(cache) == 4096:
                cache.pop(next(iter(cache)))
            cache[binding] = object_value(answer['result'])
        result = cache[binding]
        row: Record = {**record, **identities, 'schema': 'im2p-npu-cycle-result', 'kind': 'NPU_WORK_RESULT',
                       'trace_sequence': record['sequence'], 'sequence': work.identity,
                       'run_view_sha256': binding, 'modeled': result}
        stream.write(json.dumps(row, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n')
        cycles = integer(result, 'total_cycles')
        per_layer[work.layer] += cycles
        per_phase[work.phase] += cycles
        if work.inputs[2] >= 3072:
            key = (work.layer, work.phase, work.inputs)
            if key not in large_k:
                large_k[key] = {'layer': work.layer, 'phase_id': work.phase,
                                'shape': list(work.inputs[:3]), 'tile_counts': list(work.inputs[3:6]),
                                'model_status': 'PASS', 'isolated_cycles_per_work': cycles, 'work_count': 0,
                                'planner_loop_count': integer(result, 'planner_loop_count'),
                                'submission_count': integer(result, 'loop_count'),
                                'fragment_count': integer(result, 'fragment_count'),
                                'scale_request_count': integer(result, 'scale_request_count')}
            large_k[key]['work_count'] = integer(large_k[key], 'work_count') + 1
    summary = state.summary()
    summary.update(identities)
    summary.update(schema='im2p-npu-cycle-summary', version=2, status='PASS',
        trace_schema=SCHEMA, trace_version=state.run.trace_version,
        residual_work_revision=state.run.residual_work_revision,
        producer_integration_validation='NOT_CERTIFIED_BY_FIXTURE' if summary['residual_work_count'] else 'NOT_APPLICABLE',
        producer_execution_kind='CPU_FUNCTIONAL',
        validation_scope='CURRENT_CERTIFIED', cycle_model_validation='CURRENT_CERTIFIED',
        actual_rtl_acceptance_in_collection='NOT_APPLICABLE',
        accounting_kind='isolated-work-accounting', measured_answer_injection=False,
        reference_memory=cert['reference_memory'], isolated_cycle_sum=sum(per_phase.values()),
        per_layer_cycle_sums=dict(sorted(per_layer.items())), prefill_cycle_sum=per_phase[0],
        per_phase_cycle_sums={str(index): per_phase[index] for index in range(len(state.phases))},
        per_decode_token_cycle_sums={str(phase['decode_index']): per_phase[index]
                                    for index, phase in enumerate(state.phases) if phase['phase_kind'] == 'decode'},
        large_k_works=list(large_k.values()), software_limits={'max_cycles_per_work': REPLAY_MAX_CYCLES},
        not_modeled=['CPU/NPU scheduling', 'pipeline overlap', 'system timeline', 'TTFT/TPOT', 'frequency conversion'])
    return summary


def replay(trace_path: Path, artifacts: ReplayArtifacts, outputs: ReplayOutputs) -> Record:
    inputs = (trace_path, artifacts.certificate, artifacts.library) + (
        (artifacts.run_certificate,) if artifacts.run_certificate is not None else ())
    paths = tuple(path.resolve() for path in inputs) + (outputs.results.resolve(), outputs.summary.resolve())
    require(len(set(paths)) == len(paths), 'input/output paths must be distinct')
    require(not outputs.results.exists() and not outputs.summary.exists(), 'outputs must be new files')
    with tempfile.TemporaryDirectory(prefix='npu-snapshot-', dir=outputs.results.parent) as snapshot_dir, \
         tempfile.TemporaryDirectory(prefix='npu-result-', dir=outputs.results.parent) as result_dir, \
         tempfile.TemporaryDirectory(prefix='npu-summary-', dir=outputs.summary.parent) as summary_dir:
        snapshots = snapshot_inputs(inputs, Path(snapshot_dir))
        trace_snapshot, certificate_snapshot, library_snapshot = (snapshot.snapshot for snapshot in snapshots[:3])
        run_snapshot = snapshots[3].snapshot if len(snapshots) == 4 else None
        result = Path(result_dir)/'result.jsonl'
        summary_path = Path(summary_dir)/'summary.json'
        with result.open('x', encoding='utf-8') as stream:
            summary = _replay(trace_snapshot, ReplayArtifacts(library_snapshot, certificate_snapshot, run_snapshot), stream)
        verify_input_snapshots(snapshots, 'replay')
        with summary_path.open('x', encoding='utf-8') as stream:
            json.dump(summary, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write('\n')
        os.link(result, outputs.results)
        os.link(summary_path, outputs.summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace', type=Path)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--cycle-certificate', type=Path, required=True)
    parser.add_argument('--run-aware-certificate', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--summary', type=Path, required=True)
    args = parser.parse_args()
    try:
        summary = replay(args.trace, ReplayArtifacts(args.library, args.cycle_certificate,
                                                     args.run_aware_certificate),
                         ReplayOutputs(args.output, args.summary))
        print(json.dumps({key: summary[key] for key in ('status', 'npu_work_count', 'isolated_cycle_sum')}))
    except (OSError, ValueError, BuildFailure, RuntimeError) as error:
        print(f'NPU trace replay failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
