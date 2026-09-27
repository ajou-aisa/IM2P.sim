#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# uv run sim/tests/cycle/compositional_sequence_work.py --help
# ──────────────────
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.npu_trace_schema import (
    Record,
    integer,
    object_value,
    unique_pairs,
)
from sim.cycle.reconstruct_graph import array, sha256
from sim.tests.cycle.compositional_sequence_repeat import (
    REPEAT_KIND,
    build_repeat_stimulus,
    frozen_stimulus_text,
)
from sim.tests.cycle.compositional_sequence_v1_runtime import validate_log
from sim.tests.cycle.compositional_sequence_v2_compile import compile_probe
from sim.tests.cycle.compositional_sequence_v2_runtime import (
    validate_absolute_log,
    validate_absolute_log_stream,
)
from sim.tests.cycle.compositional_sequence_v2_stimulus import (
    ABSOLUTE_POLICY,
    BASE,
    POLICY,
    SOURCE,
    STIMULUS_SCHEMA,
    STIMULUS_SOURCES,
    AbsoluteOfferError,
    absolute_stimulus,
    project,
    stimulus_digest,
    stimulus_source_hashes,
    validate_stimulus,
)
from sim.tests.cycle.compositional_sequence_v2_stream import tag_pressure_rows
from sim.tests.cycle.compositional_sequence_v2_text import (
    absolute_numeric_text,
    numeric_text,
)
from sim.tests.cycle.compositional_sequence_v2_values import value_argv, value_report

__all__ = (
    'ABSOLUTE_POLICY', 'BASE', 'POLICY', 'ROOT', 'SOURCE', 'STIMULUS_SCHEMA',
    'STIMULUS_SOURCES', 'absolute_numeric_text', 'absolute_stimulus',
    'build_repeat_stimulus', 'compile_probe', 'main', 'numeric_text', 'project', 'run', 'run_absolute',
    'stimulus_digest', 'stimulus_source_hashes', 'validate_absolute_log',
    'validate_log', 'validate_stimulus',
)


def run(trace: Path, lifecycle: Path, semantic: Path, build: Path, library: Path,
        out: Path, delays: tuple[int, ...], parent_indices: tuple[int, ...]) -> None:
    projection = project(trace, lifecycle, semantic, delays, parent_indices)
    out.mkdir(parents=True, exist_ok=False)
    (out / 'projection.json').write_text(json.dumps(projection, indent=2, sort_keys=True) + '\n')
    (out / 'projection.txt').write_text(numeric_text(projection))
    binary = compile_probe(build, library, out, projection)
    with (out / 'rtl.log').open('x') as log, (out / 'rtl-diagnostics.log').open('x') as diagnostics:
        completed = subprocess.run([str(binary), str(out / 'projection.txt')], cwd=ROOT,
                                   stdout=log, stderr=diagnostics, check=False, timeout=1800)
    if completed.returncode:
        raise AbsoluteOfferError(f'probe execution failed: {out / "rtl.log"}')
    works = validate_log((out / 'rtl.log').read_text(), projection)
    if any(sha256(path) != object_value(object_value(projection['producer_artifacts'])[name])['sha256']
           for name, path in (('trace', trace), ('lifecycle', lifecycle), ('semantic_graph', semantic))):
        raise AbsoluteOfferError('producer artifact changed during probe')
    report = {'schema': 'im2p-compositional-sequence-run', 'version': 1,
              'status': 'SINGLE_PARENT_DIAGNOSTIC' if len(parent_indices) == 1 else 'PASS_DIAGNOSTIC_ONLY',
              'offer_policy': POLICY, 'selected_parent_indices': parent_indices,
              'profile': projection['profile'], 'work_count': len(works),
              'producer_artifacts': projection['producer_artifacts'],
              'source_sha256': {str(path.relative_to(ROOT)): sha256(path) for path in (SOURCE, BASE)},
              'rtl_build_binding_sha256': sha256(build / 'rtl-build-binding.json'),
              'cycle_library_sha256': sha256(library),
              'projection_sha256': sha256(out / 'projection.json'),
              'numeric_projection_sha256': sha256(out / 'projection.txt'),
              'generated_base_sha256': sha256(out / 'compositional-base.inc'),
              'compile_command_sha256': sha256(out / 'compile-command.json'),
              'binary_sha256': sha256(binary), 'rtl_log_sha256': sha256(out / 'rtl.log'),
              'rtl_diagnostics_sha256': sha256(out / 'rtl-diagnostics.log'),
              'works': works}
    (out / 'report.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': report['status'], 'work_count': len(works),
                      'report': str(out / 'report.json')}))


def run_absolute(stimulus_path: Path, case_id: str, out: Path,
                 build: Path | None, library: Path | None, *,
                 expected_stimulus_sha256: str | None = None,
                 expected_repeats: int | None = None,
                 require_queue_payload_v2: bool = False) -> None:
    raw_stimulus = stimulus_path.read_bytes()
    file_sha256 = hashlib.sha256(raw_stimulus).hexdigest()
    stimulus = object_value(json.loads(raw_stimulus, object_pairs_hook=unique_pairs))
    repeat_mode = stimulus.get('fixture_kind') == REPEAT_KIND
    require_boundary_v2 = 'BOUNDARY_V2' in array(stimulus.get('required_observations', []))
    if repeat_mode and (expected_stimulus_sha256 is None or expected_repeats is None):
        raise AbsoluteOfferError('expected repeat authority missing')
    if expected_stimulus_sha256 is not None and file_sha256 != expected_stimulus_sha256:
        raise AbsoluteOfferError('frozen stimulus file digest mismatch')
    validate_stimulus(stimulus, expected_stimulus_sha256=expected_stimulus_sha256,
                      expected_repeats=expected_repeats)
    if stimulus['case_id'] != case_id:
        raise AbsoluteOfferError('stimulus case identity differs')
    if (build is None) != (library is None):
        raise AbsoluteOfferError('RTL build and native library must be provided together')
    if require_queue_payload_v2 and (build is None or library is None):
        raise AbsoluteOfferError('queue payload v2 requires an RTL and native runtime')
    if library is not None and sha256(library) != stimulus['cycle_library_sha256']:
        raise AbsoluteOfferError('cycle library digest mismatch before run')
    out.mkdir(parents=True, exist_ok=False)
    (out / 'projection.json').write_text(json.dumps(stimulus, indent=2, sort_keys=True) + '\n')
    (out / 'projection.txt').write_text(absolute_numeric_text(
        stimulus, expected_stimulus_sha256=expected_stimulus_sha256,
        expected_repeats=expected_repeats))
    report: Record = {
        'schema': 'im2p-compositional-sequence-run', 'version': 2,
        'status': 'STATIC_VALIDATED_RUNTIME_PENDING', 'case_id': case_id,
        'profile': stimulus['profile'], 'work_count': len(array(stimulus['works'])),
        'stimulus_sha256': stimulus['stimulus_sha256'], 'stimulus_file_sha256': file_sha256,
        'numeric_projection_sha256': sha256(out / 'projection.txt'),
        'fixture_kind': stimulus.get('fixture_kind', 'GENUINE_COMPLETE_PARENT'),
        'producer_artifacts': stimulus.get('producer_artifacts', {}),
        'diagnostic_source': stimulus.get('diagnostic_source'), **value_report(stimulus, build is not None),
        'required_observations': stimulus.get('required_observations', []),
        'tag_full_pressure_status': (
            'UNRESOLVED' if 'TAG_FULL_PRESSURE' in array(stimulus.get('required_observations', []))
            else 'NOT_RUN'),
    }
    if build is not None and library is not None:
        binary = compile_probe(build, library, out, stimulus)
        observer = ['--tag-observer-v2'] if repeat_mode or require_boundary_v2 or require_queue_payload_v2 else []
        if require_boundary_v2 or require_queue_payload_v2:
            observer.append('--boundary-schema=2')
        if require_queue_payload_v2:
            observer.append('--queue-edge-schema=2')
        with (out / 'rtl.log').open('x') as log, (out / 'rtl-diagnostics.log').open('x') as diagnostics:
            completed = subprocess.run([str(binary), str(out / 'projection.txt'),
                                        *value_argv(stimulus), *observer], cwd=ROOT,
                                       stdout=log, stderr=diagnostics, check=False,
                                       timeout=1800)
        if completed.returncode:
            raise AbsoluteOfferError(f'absolute probe execution failed: {out / "rtl.log"}')
        if repeat_mode or require_boundary_v2 or require_queue_payload_v2:
            log_path = out / 'rtl.log'
            ids = tuple(integer(object_value(row), 'work_id') for row in array(stimulus['works']))
            works = validate_absolute_log_stream(
                log_path, stimulus, ids,
                expected_stimulus_sha256=expected_stimulus_sha256,
                expected_repeats=expected_repeats,
                require_boundary_v2=require_boundary_v2 or require_queue_payload_v2,
                require_queue_payload_v2=require_queue_payload_v2)
            if repeat_mode:
                pressure = tag_pressure_rows(log_path, ids)
                report['tag_pressure_v2'] = list[JsonValue](pressure)
                report['tag_full_pressure_status'] = ('FULL_STALL_OBSERVED' if any(
                    integer(row, 'full_stall_cycles') for row in pressure) else 'FULL_STALL_NOT_OBSERVED')
        else:
            works = validate_absolute_log((out / 'rtl.log').read_text(), stimulus, expected_stimulus_sha256=expected_stimulus_sha256)
        if sha256(library) != stimulus['cycle_library_sha256']:
            raise AbsoluteOfferError('cycle library digest changed during run')
        report['status'] = ('PASS_TEST_ONLY_REPEAT_TEMPLATE' if repeat_mode else
                            'PASS_TEST_ONLY_RUN_AWARE_DIAGNOSTIC'
                            if stimulus.get('fixture_kind') == 'TEST_ONLY_RUN_AWARE_DIAGNOSTIC'
                            else 'PASS_ABSOLUTE_MODEL_RTL')
        report['works'] = list[JsonValue](works)
        report['source_sha256'] = {str(path.relative_to(ROOT)): sha256(path)
                                   for path in (SOURCE, BASE)}
        report['rtl_build_binding_sha256'] = sha256(build / 'rtl-build-binding.json')
        report['cycle_library_sha256'] = stimulus['cycle_library_sha256']
        report['binary_sha256'] = sha256(binary)
        report['rtl_log_sha256'] = sha256(out / 'rtl.log')
        report['rtl_diagnostics_sha256'] = sha256(out / 'rtl-diagnostics.log')
        if require_queue_payload_v2:
            report['queue_payload_schema'] = 2
    if repeat_mode:
        if expected_stimulus_sha256 is None or expected_repeats is None:
            raise AbsoluteOfferError('expected repeat authority missing before publication')
        report['source_template'] = stimulus['source_template']
        report['repeat_count'] = stimulus['repeat_count']
        report['synthetic_parent_occurrences'] = stimulus['synthetic_parent_occurrences']
        report['repeat_authority'] = {
            'expected_stimulus_sha256': expected_stimulus_sha256,
            'expected_repeats': expected_repeats,
            'expected_work_count': 12 * expected_repeats,
            'expected_parent_occurrences': 2 * expected_repeats,
        }
        if sha256(stimulus_path) != expected_stimulus_sha256:
            raise AbsoluteOfferError('frozen stimulus file digest changed before publication')
    (out / 'report.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': report['status'], 'work_count': report['work_count'],
                      'report': str(out / 'report.json')}))


def freeze_repeat(template: Path, matrix: Path, case_id: str, repeats: int, out: Path) -> None:
    repeated = build_repeat_stimulus(template, matrix, case_id, repeats)
    frozen = frozen_stimulus_text(repeated)
    file_sha256 = hashlib.sha256(frozen.encode()).hexdigest()
    validate_stimulus(repeated, expected_stimulus_sha256=file_sha256,
                      expected_repeats=repeats)
    out.mkdir(parents=True, exist_ok=False)
    (out / 'stimulus.json').write_text(frozen)
    (out / 'projection.txt').write_text(absolute_numeric_text(
        repeated, expected_stimulus_sha256=file_sha256, expected_repeats=repeats))
    print(json.dumps({'status': 'FROZEN_TEST_ONLY_REPEAT_TEMPLATE',
                      'work_count': len(array(repeated['works'])),
                      'synthetic_parent_occurrences': len(array(repeated['synthetic_parent_occurrences'])),
                      'stimulus_file_sha256': file_sha256,
                      'stimulus': str(out / 'stimulus.json')}))


def main() -> int:
    parser = argparse.ArgumentParser(description='Run producer composition on one RTL instance')
    for name in ('trace', 'lifecycle', 'semantic-graph', 'rtl-build', 'library', 'out'):
        parser.add_argument('--' + name, type=Path, required=name == 'out')
    parser.add_argument('--delays', help='v1: first start phase, then arrival cycles after public ready')
    parser.add_argument('--stimulus', type=Path, help='sealed v2 absolute-offer JSON manifest')
    parser.add_argument('--repeat-template', type=Path, help='frozen Todo13 A4W4-D16 source template')
    parser.add_argument('--repeat-matrix-receipt', type=Path, help='frozen Todo13 matrix receipt')
    parser.add_argument('--repeats', type=int, help='test-only source-template repetitions (1..32)')
    parser.add_argument('--expected-stimulus-sha256', help='reviewed frozen stimulus file SHA256')
    parser.add_argument('--expected-repeats', type=int, help='reviewed repeat denominator (1..32)')
    parser.add_argument('--require-queue-payload-v2', action='store_true',
                        help='require source-bound RTL/native QUEUE_EDGE_V2 payload parity')
    parser.add_argument('--case', help='v2 case identity declared in stimulus')
    parser.add_argument('--parent-indices', default='0,1', help='increasing producer parent indices')
    args = parser.parse_args()
    try:
        if args.repeat_template is not None:
            if (args.case is None or args.repeat_matrix_receipt is None or args.repeats is None or
                    any(value is not None for value in (args.stimulus, args.rtl_build, args.library,
                                                         args.trace, args.lifecycle,
                                                         args.semantic_graph, args.delays,
                                                         args.expected_stimulus_sha256,
                                                         args.expected_repeats,
                                                         args.require_queue_payload_v2))):
                raise AbsoluteOfferError('repeat freeze requires source, matrix, case and repeats only')
            freeze_repeat(args.repeat_template.resolve(), args.repeat_matrix_receipt.resolve(),
                          args.case, args.repeats, args.out.resolve())
        elif args.stimulus is not None:
            if args.case is None or any(value is not None for value in
                    (args.trace, args.lifecycle, args.semantic_graph, args.delays,
                     args.repeat_matrix_receipt, args.repeats)):
                raise AbsoluteOfferError('v2 requires --case and excludes v1 trace/delays arguments')
            run_absolute(args.stimulus.resolve(), args.case, args.out.resolve(),
                         args.rtl_build.resolve() if args.rtl_build else None,
                         args.library.resolve() if args.library else None,
                         expected_stimulus_sha256=args.expected_stimulus_sha256,
                         expected_repeats=args.expected_repeats,
                         require_queue_payload_v2=args.require_queue_payload_v2)
        else:
            if (args.case is not None or args.repeat_matrix_receipt is not None or
                    args.repeats is not None or args.expected_stimulus_sha256 is not None or
                    args.expected_repeats is not None or args.require_queue_payload_v2 or
                    any(value is None for value in
                    (args.trace, args.lifecycle, args.semantic_graph, args.rtl_build,
                     args.library, args.delays))):
                raise AbsoluteOfferError('v1 requires trace, lifecycle, semantic graph, RTL build, library and delays')
            delays = tuple(map(int, args.delays.split(',')))
            parents = tuple(map(int, args.parent_indices.split(',')))
            run(args.trace.resolve(), args.lifecycle.resolve(), args.semantic_graph.resolve(),
                args.rtl_build.resolve(), args.library.resolve(), args.out.resolve(), delays, parents)
    except (OSError, ValueError, IndexError, KeyError, subprocess.TimeoutExpired) as error:
        print(f'compositional sequence: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
