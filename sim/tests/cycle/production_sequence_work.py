#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# 1. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run: uv run sim/tests/cycle/production_sequence_work.py --help
# ──────────────────
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.gemmini_resolve_profile import JsonValue
from scripts.gemmini_rtl_build_binding import verify_build
from sim.cycle.execution_lifecycle import project_lifecycle
from sim.cycle.execution_pipeline_contract import OWNER_FIELDS, PARENT_FIELDS, REQUIRED_EVENTS, parse_pipeline
from sim.cycle.npu_trace import work_binding
from sim.cycle.npu_trace_integrity import read_records, start_trace
from sim.cycle.npu_trace_schema import INPUT_KEYS, Record, integer, object_value
from sim.cycle.reconstruct_graph import array, json_records, read_manifest, sha256
from sim.tests.cycle.production_tag_carry import proof_document

SOURCE_PATHS: Final = (
    'sim/tests/cycle/production_sequence_probe.cpp',
    'sim/tests/cycle/production_sequence_work.py',
    'sim/tests/cycle/production_tag_carry.py',
    'sim/tests/cycle/service_boundary_probe.cpp',
    'fpga/gemmini_hp1/host/test_ws_rtl.cpp',
    'fpga/gemmini_hp1/host/run_aware_rtl_driver.inc',
    'sim/common/gemmini_schedule.cpp',
)


def parse_probe_log(raw: str) -> tuple[Record, list[Record], Counter[tuple[int, int, int, str]],
                                        Counter[tuple[int, int, int, str]]]:
    runs: list[Record] = []
    works: list[Record] = []
    rtl: Counter[tuple[int, int, int, str]] = Counter()
    model: Counter[tuple[int, int, int, str]] = Counter()
    for line in raw.splitlines():
        if line.startswith('SEQUENCE_RUN '):
            runs.append(object_value(json.loads(line.removeprefix('SEQUENCE_RUN '))))
        elif line.startswith('SEQUENCE_WORK '):
            works.append(object_value(json.loads(line.removeprefix('SEQUENCE_WORK '))))
        elif line.startswith(('RTL_EVENT ', 'MODEL_EVENT ')):
            kind, ordinal, work_id, cycle, name = line.split()
            row = (int(ordinal), int(work_id), int(cycle), name)
            (rtl if kind == 'RTL_EVENT' else model)[row] += 1
    if len(runs) != 1:
        raise ValueError('continuous RTL run row missing or duplicate')
    return runs[0], works, rtl, model


def validate_probe_log(raw: str, expected: list[tuple[int, str]]) -> list[Record]:
    run, works, rtl, model = parse_probe_log(raw)
    if run.get('instance_count') != 1 or run.get('reset_count') != 1:
        raise ValueError('continuous RTL instance/reset evidence missing')
    if [(row.get('work_id'), row.get('work_binding')) for row in works] != expected or \
            [row.get('ordinal') for row in works] != list(range(len(expected))):
        raise ValueError('producer work order/binding differs from RTL log')
    if not rtl or rtl != model or {row[0] for row in rtl} != set(range(len(expected))):
        raise ValueError('selected model/RTL event stream differs')
    return works


def project_inputs(trace: Path, lifecycle: Path, semantic: Path, phases: tuple[int, ...]) -> Record:
    graph = read_manifest(semantic)
    lifecycle_projection = project_lifecycle(json_records(lifecycle), graph)
    records = read_records(trace)
    state = start_trace(records)
    raw_work: list[Record] = []
    parsed_work = []
    for record in records:
        work = state.consume(record)
        if work is not None:
            raw_work.append(record)
            parsed_work.append(work)
    summary = state.summary()
    results = {'npu:' + str(integer(row, 'work_id')): row for row in raw_work}
    parents: list[JsonValue] = [{key: row[key] for key in PARENT_FIELDS}
                                for row in lifecycle_projection.pipeline_parents]
    owners: list[JsonValue] = [{key: row[key] for key in OWNER_FIELDS}
                               for row in lifecycle_projection.pipeline_owners]
    contract: Record = {'pipeline_parents': parents, 'pipeline_owners': owners}
    pipeline = parse_pipeline(contract, results)
    if len(pipeline.parents) != 1 or len(parsed_work) != 4 or len(phases) != 4 or \
            [work.scope for work in parsed_work] != ['stripe', 'residual_compact', 'stripe', 'stripe']:
        raise ValueError('producer sequence must be three stripes plus one run-aware residual')
    parent_id, parent = next(iter(pipeline.parents.items()))
    stripes = pipeline.stripes[parent_id]
    if [integer(row, 'work_id') for row in stripes] != [0, 2, 3] or \
            [row['host_slot'] for row in stripes] != [0, 1, 0] or \
            parent['required_work_ids'] != [0, 1, 2, 3] or \
            parent['fence_required_work_ids'] != [0, 1, 2, 3] or \
            len(parsed_work[1].runs) < 2:
        raise ValueError('producer parent/slot/fence/run sequence differs')
    for stripe in range(3):
        group = pipeline.owners[parent_id, stripe]
        if not REQUIRED_EVENTS <= {(resource, transition) for resource, transition, _ in group}:
            raise ValueError('producer owner transition missing')
    residual_bindings = array(parent['residual_bindings'])
    if len(residual_bindings) != 1:
        raise ValueError('residual work lacks exact stripe binding')
    binding = object_value(residual_bindings[0])
    if binding['work_id'] != 1 or binding['dense_work_id'] != 0:
        raise ValueError('residual work lacks exact stripe binding')
    slots = (0, 0, 1, 0)
    projected: list[JsonValue] = []
    for ordinal, (record, work, slot, phase) in enumerate(zip(raw_work, parsed_work, slots, phases, strict=True)):
        if record['host_slot'] not in (None, slot) or not 0 <= phase < 5:
            raise ValueError('work slot or phase differs from producer contract')
        projected.append({'ordinal': ordinal, 'work_id': work.identity, 'scope': work.scope,
                          'slot': slot, 'accepted_phase': phase, 'work_binding': work_binding(work),
                          'input': {key: value for key, value in zip(INPUT_KEYS, work.inputs, strict=True)},
                          'original_k': work.original_k, 'runs': [asdict(run) for run in work.runs],
                          'row_map': [asdict(row) for row in work.row_map], 'trace_record': record})
    return {'schema': 'im2p-producer-continuous-rtl-projection', 'version': 1,
            'profile': state.run.profile, 'hardware_contract': state.run.contract,
            'producer_artifacts': {name: {'path': str(path.resolve(strict=True)), 'sha256': sha256(path)}
                                   for name, path in (('trace', trace), ('lifecycle', lifecycle),
                                                      ('semantic_graph', semantic))},
            'npu_summary': summary, 'pipeline_parent': parent,
            'pipeline_owners': list[JsonValue](lifecycle_projection.pipeline_owners), 'works': projected}


def numeric_projection_text(projection: Record, period: int) -> str:
    works = projection['works']
    if not isinstance(works, list):
        raise ValueError('projected works missing')
    lines = ['IM2P_PRODUCTION_SEQUENCE_V1', f'PROFILE {projection["profile"]}',
             f'PERIOD {period}', f'WORKS {len(works)}']
    for raw in works:
        row = object_value(raw)
        trace = object_value(row['trace_record'])
        inputs = object_value(row['input'])
        runs = row['runs']
        if not isinstance(runs, list):
            raise ValueError('runs missing')
        values = [row['ordinal'], row['work_id'], 'R' if row['scope'] == 'residual_compact' else 'D',
                  row['slot'], row['accepted_phase'], trace['parent_id'], trace['call_id'],
                  trace['stripe_id'] if trace['stripe_id'] is not None else 0,
                  trace['row_begin'], trace['parent_m'], *(inputs[key] for key in INPUT_KEYS),
                  row['original_k'] if row['original_k'] is not None else 0,
                  row['work_binding'], len(runs)]
        lines.append('W ' + ' '.join(map(str, values)))
        for run in runs:
            span = object_value(run)
            lines.append('R ' + ' '.join(str(span[key]) for key in
                        ('original_block_id', 'original_k_mask', 'compact_k_begin', 'compact_k_count')))
    return '\n'.join(lines) + '\n'


def command(argv: list[str], log: Path, timeout: int) -> str:
    with log.open('x') as stream:
        completed = subprocess.run(argv, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                   check=False, timeout=timeout)
    if completed.returncode:
        raise ValueError(f'command exit {completed.returncode}; log: {log}')
    return log.read_text()


def compile_probe(build: Path, library: Path, out: Path, profile: str) -> Path:
    binding = verify_build(build, profile)
    resolved = json.loads((build / 'resolved-profile.json').read_text())
    makefile = (build / 'rtl-test-obj/VIM2PGemminiWSHP1RtlTest.mk').read_text()
    flags_match = re.search(r'VM_USER_CFLAGS = \\\n(.*?)\n\n', makefile, re.S)
    root_match = re.search(r'^VERILATOR_ROOT = (.+)$', makefile, re.M)
    if flags_match is None or root_match is None:
        raise ValueError('official Verilator compile inputs missing')
    flags = shlex.split(flags_match[1].replace('\\\n', ' ').rstrip().removesuffix('\\'))
    selected_top = resolved.get('selected_top')
    if not isinstance(selected_top, str) or not re.fullmatch(r'IM2PGemminiWSHP1A[48]W[48]D(?:16|32|64)', selected_top):
        raise ValueError('official generated top missing')
    flags.append('-DIM2P_RTL_SELECTED_TOP=' + selected_top)
    source = resolved.get('llama_source')
    if source is not None:
        recorded = object_value(source).get('root')
        if not isinstance(recorded, str):
            raise ValueError('recorded llama root missing')
        flags = [flag.replace(recorded, str(ROOT.parent / 'llama.cpp-gemmini')) for flag in flags]
    objects = build / 'rtl-test-obj'
    include = Path(root_match[1]) / 'include'
    flags += ['-ffunction-sections', '-fdata-sections', f'-I{objects}', f'-I{include}',
              f'-I{include / "vltstd"}', '-O1']
    probe = ROOT / SOURCE_PATHS[0]
    binary = out / 'production-sequence-probe'
    link = (['-Wl,-dead_strip', '-Wl,-U,__Z15vl_time_stamp64v,-U,__Z13sc_time_stampv']
            if sys.platform == 'darwin' else ['-Wl,--gc-sections'])
    argv = ['c++', *flags, str(probe), str(ROOT / 'sim/common/gemmini_schedule.cpp'),
            str(objects / 'VIM2PGemminiWSHP1RtlTest__ALL.a'), str(objects / 'verilated.o'),
            str(objects / 'verilated_threads.o'), str(library), f'-Wl,-rpath,{library.parent}',
            *link, '-pthread', '-o', str(binary)]
    (out / 'compile-command.json').write_text(json.dumps(argv, indent=2) + '\n')
    command(argv, out / 'compile.log', 300)
    if binding['hardware_contract'] != object_value(json.loads((out / 'projection.json').read_text()))['hardware_contract']:
        raise ValueError('RTL build hardware contract differs from producer trace')
    return binary


def run(trace: Path, lifecycle: Path, semantic: Path, build: Path, library: Path,
        out: Path, period: int, phases: tuple[int, ...]) -> None:
    if period != 5 or any(not 0 <= phase < period for phase in phases):
        raise ValueError('run-aware sequence supports period 5 and phases 0..4')
    projection = project_inputs(trace, lifecycle, semantic, phases)
    profile = projection['profile']
    if not isinstance(profile, str):
        raise ValueError('projected profile missing')
    out.mkdir(parents=True, exist_ok=False)
    (out / 'projection.json').write_text(json.dumps(projection, indent=2, sort_keys=True) + '\n')
    (out / 'projection.txt').write_text(numeric_projection_text(projection, period))
    binary = compile_probe(build, library, out, profile)
    raw = command([str(binary), str(out / 'projection.txt')], out / 'rtl.log', 1800)
    works = object_value({'works': projection['works']})['works']
    if not isinstance(works, list):
        raise ValueError('projected works missing')
    expected = [(integer(object_value(row), 'work_id'), str(object_value(row)['work_binding'])) for row in works]
    observed = validate_probe_log(raw, expected)
    proof = proof_document(observed, sha256(out / 'rtl.log'))
    (out / 'tag-carry-proof.json').write_text(json.dumps(proof, indent=2, sort_keys=True) + '\n')
    event = next(line for line in raw.splitlines() if line.startswith('MODEL_EVENT '))
    parts = event.split()
    parts[3] = str(int(parts[3]) + 1)
    mutated = raw.replace(event, ' '.join(parts), 1)
    rejected = False
    try:
        validate_probe_log(mutated, expected)
    except ValueError:
        rejected = True
    if not rejected:
        raise ValueError('event +1 mutation was accepted')
    if any(sha256(path) != object_value(object_value(projection['producer_artifacts'])[name])['sha256']
           for name, path in (('trace', trace), ('lifecycle', lifecycle), ('semantic_graph', semantic))):
        raise ValueError('producer artifact changed while probing')
    report = {'schema': 'im2p-producer-continuous-rtl-run', 'version': 1,
              'profile': profile, 'period': period, 'phases': phases, 'work_count': len(observed),
              'status': proof['status'], 'reset_count': 1, 'instance_count': 1,
              'event_plus_one_mutation_rejected': rejected,
              'producer_artifacts': projection['producer_artifacts'],
              'source_sha256': {name: sha256(ROOT / name) for name in SOURCE_PATHS},
              'rtl_build_binding': {'path': str(build / 'rtl-build-binding.json'),
                                    'sha256': sha256(build / 'rtl-build-binding.json')},
              'cycle_library': {'path': str(library), 'sha256': sha256(library)},
              'projection_sha256': sha256(out / 'projection.json'),
              'numeric_projection_sha256': sha256(out / 'projection.txt'),
              'compile_command_sha256': sha256(out / 'compile-command.json'),
              'binary_sha256': sha256(binary), 'rtl_log_sha256': sha256(out / 'rtl.log'),
              'tag_carry_proof_sha256': sha256(out / 'tag-carry-proof.json'),
              'works': observed}
    (out / 'report.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': report['status'], 'profile': profile, 'work_count': len(observed),
                      'report': str(out / 'report.json')}))


def main() -> int:
    parser = argparse.ArgumentParser(description='Run exact producer sequence through one RTL instance')
    for name in ('trace', 'lifecycle', 'semantic-graph', 'rtl-build', 'library', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--period', type=int, default=5)
    parser.add_argument('--phase', type=int, default=0)
    parser.add_argument('--phases', help='comma-separated per-work phase vector')
    args = parser.parse_args()
    try:
        phases = tuple(map(int, args.phases.split(','))) if args.phases else (args.phase,) * 4
        run(args.trace.resolve(), args.lifecycle.resolve(), args.semantic_graph.resolve(),
            args.rtl_build.resolve(), args.library.resolve(), args.out.resolve(), args.period, phases)
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as error:
        print(f'production sequence: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
