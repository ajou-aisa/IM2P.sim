# How to run: PYTHONPATH=IM2P.sim python3 -B -m sim.cycle.local_replay TRACE --library LIB --local-validation RECEIPT \
#   --output RESULTS.jsonl --summary SUMMARY.json [--workers N]
"""Host-local isolated NPU replay under a NANO_LOCAL_VALIDATED receipt; never a certified replay.

The work loop, per-work model documents, binding cache and summary arithmetic are those of sim.cycle.npu_trace;
the authority differs. Instead of the reviewed certificate chain, a sim.cycle.local_validation receipt must name
the exact library bytes and the current cycle-model source closure. Rows keep the certified result layout, but
`cycle_model_validation`/`validation_scope` say NANO_LOCAL_VALIDATED (or NANO_LOCAL_CANDIDATE while a receipt is
being built), so the certified join, IR and publication stages reject them. Hardware compatibility is checked
against the repository profile contract only; reference memory is not bound.

`--workers N` is the FAST_EVALUATION variant: the one trace pass submits each binding-cache miss to a process pool
while it parses, then publishes the rows in trace order once every answer is back. Estimates are pure functions of
(library bytes, model document), and the pool receives exactly the documents the serial replay would estimate (the
same 4096-entry FIFO binding cache), so both variants must publish identical bytes.

`--task-cache PATH` adds the persistent per-work answer cache of sim.cycle.npu_task_cache: a binding-cache miss is
first looked up there, and every newly computed answer is committed as soon as it arrives, so an interrupted replay
resumes from its completed works. Cached answers are the same pure-function values, so the published bytes do not
depend on the cache; hit/miss/model-call/persisted counters go to the evaluation statistics only.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from typing import TextIO

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.gemmini_replay_contract import compatible, hardware_contract
from scripts.gemmini_resolve_profile import BuildFailure
from sim.cycle import cli
from sim.cycle.local_validation import admit_library
from sim.cycle.npu_trace import ReplayOutputs, model_document, snapshot_inputs, verify_input_snapshots, work_binding
from sim.cycle.npu_trace_integrity import read_records, start_trace
from sim.cycle.npu_task_cache import TaskCache, implementation
from sim.cycle.npu_trace_schema import SCHEMA, Record, integer, object_value, require
from sim.cycle.optrace_schema import REPLAY_MAX_CYCLES


@dataclass(frozen=True, slots=True)
class LocalArtifacts:
    library: Path
    receipt: Path


BINDING_CACHE_ENTRIES = 4096  # the npu_trace._replay FIFO binding cache


def _estimate(library: str, document: Record) -> Record:
    return cli.estimate(Path(library), document)


def _answer(answer: Record | Future[Record]) -> Record:
    resolved = answer.result() if isinstance(answer, Future) else answer
    require(resolved.get('status') == 'PASS', 'cycle model rejected target work')
    return object_value(resolved['result'])


class _Persist:
    """Commits each PASS answer as it completes; a failed commit fails the replay once every answer is back."""

    def __init__(self, cache: TaskCache) -> None:
        self.cache = cache
        self.errors: list[str] = []

    def answer(self, document: Record, answer: Record) -> None:
        if answer.get('status') != 'PASS':
            return
        try:
            self.cache.store(document, answer)
        except (OSError, ValueError, sqlite3.Error) as error:
            self.errors.append(str(error))

    def future(self, document: Record, future: Future[Record]) -> None:
        if not future.cancelled() and future.exception() is None:
            self.answer(document, future.result())


def _replay(trace_path: Path, artifacts: LocalArtifacts, stream: TextIO,
            pool: ProcessPoolExecutor | None = None, stats: Record | None = None,
            task_cache: Path | None = None) -> Record:
    started = time.monotonic()
    receipt, validation = admit_library(artifacts.receipt, artifacts.library)
    receipt_sha256 = hashlib.sha256(artifacts.receipt.read_bytes()).hexdigest()
    records = read_records(trace_path)
    state = start_trace(records)
    compatible(state.run.contract, hardware_contract(state.run.profile))
    library_sha256 = hashlib.sha256(artifacts.library.read_bytes()).hexdigest()
    persisted = (None if task_cache is None else
                 _Persist(TaskCache(task_cache, implementation(
                     library_sha256, str(receipt['semantic_options_sha256']), state.run.profile))))
    identities: Record = {'profile': state.run.profile, 'cycle_library_sha256': library_sha256,
                          'certificate_sha256': receipt_sha256,
                          'certificate_schema': receipt['schema'], 'certificate_version': receipt['version'],
                          'transition_certificate_sha256': None,
                          'producer_execution_kind': 'CPU_FUNCTIONAL', 'target_work_validation': 'PASS',
                          'cycle_model_validation': validation,
                          'actual_rtl_acceptance_in_collection': 'NOT_APPLICABLE',
                          'run_aware_certificate_sha256': None, 'run_aware_certificate_scope': validation,
                          'accounting_kind': 'isolated-work-accounting', 'cycle_unit': 'cycles'}
    per_layer: defaultdict[str, int] = defaultdict(int)
    per_phase: defaultdict[int, int] = defaultdict(int)
    large_k: dict[tuple[str, int, tuple[int, ...]], Record] = {}
    # One pass: validate every record and issue one estimate per binding-cache miss (serially, or to the pool while
    # parsing continues); rows are written in trace order once their answers are known.
    cache: dict[str, Record | Future[Record]] = {}
    works: list[tuple[Record, int, str, int, str, tuple[int, ...], Record | Future[Record]]] = []
    misses = model_calls = 0
    for record in records:
        work = state.consume(record)
        if work is None:
            continue
        require(work.provenance != 'residual' or bool(work.runs),
                'legacy block-local residual trace cannot be upgraded to run-aware replay')
        binding = work_binding(work)
        if binding not in cache:
            document = model_document(state.run.profile, work)
            answer: Record | Future[Record] | None = persisted.cache.lookup(document) if persisted else None
            if answer is None:
                model_calls += 1
                if pool is not None:
                    answer = pool.submit(_estimate, str(artifacts.library), document)
                    if persisted is not None:
                        answer.add_done_callback(lambda future, document=document: persisted.future(document, future))
                else:
                    answer = cli.estimate(artifacts.library, document)
                    _answer(answer)
                    if persisted is not None:
                        persisted.answer(document, answer)
            if len(cache) == BINDING_CACHE_ENTRIES:
                cache.pop(next(iter(cache)))
            cache[binding] = answer
            misses += 1
        works.append((record, work.identity, binding, work.phase, work.layer, work.inputs, cache[binding]))
    parsed = time.monotonic()
    results: dict[int, Record] = {}
    for record, identity, binding, phase, layer, inputs, answer in works:
        result = results.get(id(answer))
        if result is None:
            result = results[id(answer)] = _answer(answer)
        row: Record = {**record, **identities, 'schema': 'im2p-npu-cycle-result', 'kind': 'NPU_WORK_RESULT',
                       'trace_sequence': record['sequence'], 'sequence': identity,
                       'run_view_sha256': binding, 'modeled': result}
        stream.write(json.dumps(row, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n')
        cycles = integer(result, 'total_cycles')
        per_layer[layer] += cycles
        per_phase[phase] += cycles
        if inputs[2] >= 3072:
            key = (layer, phase, inputs)
            if key not in large_k:
                large_k[key] = {'layer': layer, 'phase_id': phase,
                                'shape': list(inputs[:3]), 'tile_counts': list(inputs[3:6]),
                                'model_status': 'PASS', 'isolated_cycles_per_work': cycles, 'work_count': 0,
                                'planner_loop_count': integer(result, 'planner_loop_count'),
                                'submission_count': integer(result, 'loop_count'),
                                'fragment_count': integer(result, 'fragment_count'),
                                'scale_request_count': integer(result, 'scale_request_count')}
            large_k[key]['work_count'] = integer(large_k[key], 'work_count') + 1
    if persisted is not None:
        persisted.cache.close()
        require(not persisted.errors, 'NPU task cache write failed: ' + '; '.join(persisted.errors[:3]))
    if stats is not None:
        stats.update(npu_work_count=len(works), binding_misses=misses, model_calls=model_calls, trace_passes=1,
                     parse_seconds=round(parsed - started, 3), publish_seconds=round(time.monotonic() - parsed, 3))
        if persisted is not None:
            stats['npu_task_cache'] = {'path': str(task_cache), **persisted.cache.counters,
                                       'implementation_sha256': persisted.cache.implementation_sha256}
    summary = state.summary()
    summary.update(identities)
    summary.update(schema='im2p-npu-cycle-summary', version=2, status='PASS',
        trace_schema=SCHEMA, trace_version=state.run.trace_version,
        residual_work_revision=state.run.residual_work_revision,
        producer_integration_validation='NOT_CERTIFIED_BY_FIXTURE' if summary['residual_work_count'] else 'NOT_APPLICABLE',
        producer_execution_kind='CPU_FUNCTIONAL',
        validation_scope=validation, cycle_model_validation=validation, certification='NOT_CERTIFIED',
        local_validation_receipt_sha256=receipt_sha256,
        actual_rtl_acceptance_in_collection='NOT_APPLICABLE',
        accounting_kind='isolated-work-accounting', measured_answer_injection=False,
        reference_memory=None, isolated_cycle_sum=sum(per_phase.values()),
        per_layer_cycle_sums=dict(sorted(per_layer.items())), prefill_cycle_sum=per_phase[0],
        per_phase_cycle_sums={str(index): per_phase[index] for index in range(len(state.phases))},
        per_decode_token_cycle_sums={str(phase['decode_index']): per_phase[index]
                                    for index, phase in enumerate(state.phases) if phase['phase_kind'] == 'decode'},
        large_k_works=list(large_k.values()), software_limits={'max_cycles_per_work': REPLAY_MAX_CYCLES},
        not_modeled=['CPU/NPU scheduling', 'pipeline overlap', 'system timeline', 'TTFT/TPOT', 'frequency conversion',
                     'RTL equivalence of this host build'])
    return summary


def _publish(trace_path: Path, artifacts: LocalArtifacts, outputs: ReplayOutputs,
             pool: ProcessPoolExecutor | None, stats: Record | None, task_cache: Path | None = None) -> Record:
    """Replay through immutable input snapshots; outputs appear only after a complete PASS."""
    inputs = (trace_path, artifacts.receipt, artifacts.library)
    paths = tuple(path.resolve() for path in inputs) + (outputs.results.resolve(), outputs.summary.resolve())
    require(len(set(paths)) == len(paths), 'input/output paths must be distinct')
    require(not outputs.results.exists() and not outputs.summary.exists(), 'outputs must be new files')
    with tempfile.TemporaryDirectory(prefix='local-snapshot-', dir=outputs.results.parent) as snapshot_dir, \
         tempfile.TemporaryDirectory(prefix='local-result-', dir=outputs.results.parent) as result_dir:
        started = time.monotonic()
        snapshots = snapshot_inputs(inputs, Path(snapshot_dir))
        if stats is not None:
            stats['snapshot_seconds'] = round(time.monotonic() - started, 3)
        trace, receipt, library = (snapshot.snapshot for snapshot in snapshots)
        result, summary_path = Path(result_dir) / 'result.jsonl', Path(result_dir) / 'summary.json'
        with result.open('x', encoding='utf-8') as stream:
            summary = _replay(trace, LocalArtifacts(library, receipt), stream, pool, stats, task_cache)
        verify_input_snapshots(snapshots, 'local replay')
        with summary_path.open('x', encoding='utf-8') as stream:
            json.dump(summary, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write('\n')
        os.link(result, outputs.results)
        os.link(summary_path, outputs.summary)
    return summary


def replay(trace_path: Path, artifacts: LocalArtifacts, outputs: ReplayOutputs, task_cache: Path | None = None) -> Record:
    """Serial local replay: every binding-cache miss is estimated in this process (or read from the task cache)."""
    return _publish(trace_path, artifacts, outputs, None, None, task_cache)


def replay_fast(trace: Path, artifacts: LocalArtifacts, outputs: ReplayOutputs, workers: int,
                task_cache: Path | None = None) -> tuple[Record, Record]:
    """FAST_EVALUATION: the same single pass, with the binding-cache misses estimated by a pool of `workers`."""
    require(workers > 0, 'positive worker count required')
    started = time.monotonic()
    stats: Record = {'mode': 'FAST_EVALUATION_ISOLATED', 'workers': workers}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        summary = _publish(trace, artifacts, outputs, pool, stats, task_cache)
    require(summary['npu_work_count'] == stats['npu_work_count'], 'local fast replay work count mismatch')
    stats.update(precomputed_documents=stats['model_calls'], fallback_calls=0,
                 library_sha256=hashlib.sha256(artifacts.library.read_bytes()).hexdigest(),
                 total_seconds=round(time.monotonic() - started, 3))
    return summary, stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace', type=Path)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--local-validation', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--summary', type=Path, required=True)
    parser.add_argument('--workers', type=int, help='FAST_EVALUATION parallel memo; omit for the serial replay')
    parser.add_argument('--task-cache', type=Path, help='persistent per-work answer cache (sim.cycle.npu_task_cache)')
    parser.add_argument('--stats', type=Path, help='write the evaluation statistics (counters) to this new file')
    args = parser.parse_args()
    artifacts, outputs = LocalArtifacts(args.library, args.local_validation), ReplayOutputs(args.output, args.summary)
    try:
        stats: Record | None = None
        if args.workers is None:
            summary = replay(args.trace, artifacts, outputs, args.task_cache)
        else:
            summary, stats = replay_fast(args.trace, artifacts, outputs, args.workers, args.task_cache)
        if args.stats is not None:
            with args.stats.open('x', encoding='utf-8') as stream:
                json.dump(stats, stream, indent=2, sort_keys=True)
                stream.write('\n')
        print(json.dumps({**{key: summary[key] for key in ('status', 'npu_work_count', 'isolated_cycle_sum',
                                                              'cycle_model_validation')}, 'evaluation': stats},
                         sort_keys=True))
    except (OSError, ValueError, BuildFailure, RuntimeError) as error:
        print(f'local NPU replay failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
