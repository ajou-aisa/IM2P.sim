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
import json
import re
import shlex
import subprocess
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.gemmini_resolve_profile import JsonValue
from scripts.gemmini_rtl_build_binding import verify_build
from sim.cycle.execution_lifecycle import project_lifecycle
from sim.cycle.execution_pipeline_contract import (
    OWNER_FIELDS,
    PARENT_FIELDS,
    parse_pipeline,
    required_ids,
)
from sim.cycle.npu_trace import work_binding
from sim.cycle.npu_trace_integrity import read_records, start_trace
from sim.cycle.npu_trace_schema import INPUT_KEYS, Record, integer, object_value
from sim.cycle.reconstruct_graph import array, json_records, read_manifest, sha256

SOURCE: Final = ROOT / 'sim/tests/cycle/compositional_sequence_probe.cpp'
BASE: Final = ROOT / 'sim/tests/cycle/production_sequence_probe.cpp'
POLICY: Final = 'public-ready-or-later-arrival-v1'


def project(trace: Path, lifecycle: Path, semantic: Path, delays: tuple[int, ...],
            parent_indices: tuple[int, ...] = (0, 1)) -> Record:
    graph = read_manifest(semantic)
    projected_lifecycle = project_lifecycle(json_records(lifecycle), graph)
    records = read_records(trace)
    state = start_trace(records)
    pairs = [(row, work) for row in records if (work := state.consume(row)) is not None]
    all_parents = [{key: row[key] for key in PARENT_FIELDS} for row in projected_lifecycle.pipeline_parents]
    all_owners = [{key: row[key] for key in OWNER_FIELDS} for row in projected_lifecycle.pipeline_owners]
    contract: Record = {'pipeline_parents': list[JsonValue](all_parents),
                        'pipeline_owners': list[JsonValue](all_owners)}
    _ = parse_pipeline(contract, {'npu:' + str(integer(row, 'work_id')): row for row, _ in pairs})
    expected = [work_id for parent in all_parents for work_id in required_ids(parent, 'required_work_ids')]
    if sorted(expected) != sorted(work.identity for _, work in pairs):
        raise ValueError('producer parent/fence work coverage differs from trace')
    if not parent_indices or parent_indices[0] < 0 or parent_indices != tuple(range(parent_indices[0],
            parent_indices[0] + len(parent_indices))) or parent_indices[-1] >= len(all_parents):
        raise ValueError('select consecutive producer parent indices')
    parents = [all_parents[index] for index in parent_indices]
    selected = {work_id for parent in parents for work_id in required_ids(parent, 'required_work_ids')}
    pairs = [(row, work) for row, work in pairs if work.identity in selected]
    if len(pairs) <= 4 or len(delays) != len(pairs) or any(value < 0 for value in delays):
        raise ValueError('selected parents require >4 works and one nonnegative delay per work')
    if delays[0] >= 5:
        raise ValueError('first delay is a start phase in 0..4')
    owners = [row for row in all_owners if integer(row, 'parent_id') in
              {integer(parent, 'parent_id') for parent in parents}]
    dense_slots = {work.identity: integer(row, 'host_slot') for row, work in pairs
                   if work.scope == 'stripe'}
    residual_slots = {integer(binding, 'work_id'): dense_slots[integer(binding, 'dense_work_id')]
                      for parent in parents for binding in map(object_value, array(parent['residual_bindings']))}
    works: list[JsonValue] = []
    for ordinal, ((record, work), delay) in enumerate(zip(pairs, delays, strict=True)):
        slot = dense_slots.get(work.identity, residual_slots.get(work.identity))
        if slot not in (0, 1) or record['host_slot'] not in (None, slot):
            raise ValueError('producer workspace ownership differs from trace')
        works.append({'ordinal': ordinal, 'work_id': work.identity, 'scope': work.scope,
                      'slot': slot, 'arrival_delay': delay, 'work_binding': work_binding(work),
                      'input': dict(zip(INPUT_KEYS, work.inputs, strict=True)),
                      'original_k': work.original_k, 'runs': [asdict(run) for run in work.runs],
                      'trace_record': record})
    return {'schema': 'im2p-compositional-sequence-projection', 'version': 1,
            'offer_policy': POLICY, 'profile': state.run.profile,
            'selected_parent_indices': list(parent_indices),
            'hardware_contract': state.run.contract, 'npu_summary': state.summary(),
            'producer_artifacts': {name: {'path': str(path), 'sha256': sha256(path)}
                                   for name, path in (('trace', trace), ('lifecycle', lifecycle),
                                                      ('semantic_graph', semantic))},
            'pipeline_parents': list[JsonValue](parents),
            'pipeline_owners': list[JsonValue](owners), 'works': works}


def numeric_text(projection: Record) -> str:
    lines = ['IM2P_COMPOSITIONAL_SEQUENCE_V1 ' + str(projection['profile']), 'PERIOD 5']
    works = array(projection['works'])
    lines.append(f'WORKS {len(works)}')
    for raw in works:
        row = object_value(raw)
        trace = object_value(row['trace_record'])
        inputs = object_value(row['input'])
        runs = array(row['runs'])
        fields = (row['ordinal'], row['work_id'], 'R' if row['scope'] == 'residual_compact' else 'D',
                  row['slot'], row['arrival_delay'], trace['parent_id'], trace['call_id'],
                  trace['stripe_id'] if trace['stripe_id'] is not None else 0,
                  trace['row_begin'], trace['parent_m'], *(inputs[key] for key in INPUT_KEYS),
                  row['original_k'] if row['original_k'] is not None else 0,
                  row['work_binding'], len(runs))
        lines.append('W ' + ' '.join(map(str, fields)))
        for run in runs:
            span = object_value(run)
            lines.append('R ' + ' '.join(str(span[key]) for key in
                        ('original_block_id', 'original_k_mask', 'compact_k_begin', 'compact_k_count')))
    return '\n'.join(lines) + '\n'


def compile_probe(build: Path, library: Path, out: Path, projection: Record) -> Path:
    if verify_build(build, str(projection['profile']))['hardware_contract'] != projection['hardware_contract']:
        raise ValueError('RTL build hardware contract differs from producer')
    source = BASE.read_text()
    marker = '#undef IM2P_SERVICE_PROBE_NO_MAIN\n'
    event = '''void event(const char *kind, std::uint64_t cycle) {
  if (current_ordinal != UINT32_MAX)
    std::cout << "RTL_EVENT " << current_ordinal << ' ' << current_work_id << ' '
              << cycle << ' ' << kind << '\\n';
}'''
    atomic_event = '''void event(const char *kind, std::uint64_t cycle) {
  if (current_ordinal != UINT32_MAX)
    std::fprintf(stdout, "RTL_EVENT %u %u %llu %s\\n", current_ordinal, current_work_id, static_cast<unsigned long long>(cycle), kind);
}'''
    if source.count(marker) != 1 or source.count(event) != 1:
        raise ValueError('base probe inclusion seam changed')
    generated = out / 'compositional-base.inc'
    generated.write_text('#include <cstdio>\n' + source.replace(
        marker, marker + '#define main legacy_sequence_main\n').replace(event, atomic_event))
    resolved = json.loads((build / 'resolved-profile.json').read_text())
    makefile = (build / 'rtl-test-obj/VIM2PGemminiWSHP1RtlTest.mk').read_text()
    flags_match = re.search(r'VM_USER_CFLAGS = \\\n(.*?)\n\n', makefile, re.DOTALL)
    root_match = re.search(r'^VERILATOR_ROOT = (.+)$', makefile, re.MULTILINE)
    if flags_match is None or root_match is None:
        raise ValueError('official Verilator compile inputs missing')
    flags = shlex.split(flags_match[1].replace('\\\n', ' ').rstrip().removesuffix('\\'))
    top = resolved.get('selected_top')
    if not isinstance(top, str) or not re.fullmatch(r'IM2PGemminiWSHP1A[48]W[48]D(?:16|32|64)', top):
        raise ValueError('official generated top missing')
    flags.append('-DIM2P_RTL_SELECTED_TOP=' + top)
    if resolved.get('llama_source') is not None:
        recorded = object_value(resolved['llama_source'])['root']
        flags = [flag.replace(str(recorded), str(ROOT.parent / 'llama.cpp-gemmini')) for flag in flags]
    objects = build / 'rtl-test-obj'
    include = Path(root_match[1]) / 'include'
    flags += ['-ffunction-sections', '-fdata-sections', f'-I{objects}', f'-I{include}',
              f'-I{include / "vltstd"}', '-O1', f'-I{SOURCE.parent}',
              '-DIM2P_COMPOSITIONAL_BASE_SOURCE="' + str(generated) + '"']
    binary = out / 'compositional-probe'
    link = (['-Wl,-dead_strip', '-Wl,-U,__Z15vl_time_stamp64v,-U,__Z13sc_time_stampv']
            if sys.platform == 'darwin' else ['-Wl,--gc-sections'])
    argv = ['c++', *flags, str(SOURCE), str(ROOT / 'sim/common/gemmini_schedule.cpp'),
            str(objects / 'VIM2PGemminiWSHP1RtlTest__ALL.a'), str(objects / 'verilated.o'),
            str(objects / 'verilated_threads.o'), str(library), f'-Wl,-rpath,{library.parent}',
            *link, '-pthread', '-o', str(binary)]
    (out / 'compile-command.json').write_text(json.dumps(argv, indent=2) + '\n')
    with (out / 'compile.log').open('x') as log:
        completed = subprocess.run(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                   check=False, timeout=300)
    if completed.returncode:
        raise ValueError(f'probe compile failed: {out / "compile.log"}')
    return binary


def validate_log(raw: str, projection: Record) -> list[Record]:
    works = []
    runs = []
    rtl: Counter[tuple[int, int, int, str]] = Counter()
    model: Counter[tuple[int, int, int, str]] = Counter()
    counts: dict[int, tuple[int, ...]] = {}
    for line in raw.splitlines():
        if line.startswith('COMPOSITION_RUN '):
            runs.append(object_value(json.loads(line.removeprefix('COMPOSITION_RUN '))))
        elif line.startswith('COMPOSITION_WORK '):
            works.append(object_value(json.loads(line.removeprefix('COMPOSITION_WORK '))))
        elif line.startswith(('RTL_EVENT ', 'MODEL_EVENT ')):
            kind, ordinal, work_id, cycle, name = line.split()
            (rtl if kind == 'RTL_EVENT' else model)[int(ordinal), int(work_id), int(cycle), name] += 1
        elif line.startswith('MODEL_COUNTS '):
            _, ordinal, _, *values = line.split()
            counts[int(ordinal)] = tuple(map(int, values))
    expected = [object_value(row) for row in array(projection['works'])]
    if len(runs) != 1 or runs[0].get('instance_count') != 1 or runs[0].get('reset_count') != 1 or \
            runs[0].get('work_count') != len(expected) or len(works) != len(expected):
        raise ValueError('one-instance >4-work run evidence missing')
    if not rtl or rtl != model or {row[0] for row in rtl} != set(range(len(expected))):
        raise ValueError('selected RTL/model event stream differs')
    for index, (observed, requested) in enumerate(zip(works, expected, strict=True)):
        if (observed['ordinal'], observed['work_id'], observed['work_binding'], observed['slot']) != \
                (index, requested['work_id'], requested['work_binding'], requested['slot']):
            raise ValueError('producer identity/order/slot differs')
        if observed['offered'] != observed['predicted_offered'] or \
                observed['accepted'] != observed['predicted_accepted']:
            raise ValueError('independent acceptance epoch differs')
        if index and observed['offered'] != works[index-1]['resource_ready'] + requested['arrival_delay']:
            raise ValueError('successor offer policy differs')
        if observed['resource_ready'] != observed['predicted_resource_ready'] or \
                observed['numeric_pass'] is not True:
            raise ValueError('resource prediction or numeric result differs')
        modeled = counts.get(index)
        actual = tuple(observed[key] for key in ('submissions', 'scale_read_requests',
                        'scale_read_responses', 'scale_release_count', 'load_requests',
                        'load_responses', 'store_requests', 'store_responses',
                        'result_ready', 'final_scale_release', 'resource_ready',
                        'next_scratchpad_half', 'next_accumulator_half'))
        if modeled != actual:
            raise ValueError(f'composition counters/endpoints differ at work {index}')
    return works


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
        raise ValueError(f'probe execution failed: {out / "rtl.log"}')
    works = validate_log((out / 'rtl.log').read_text(), projection)
    if any(sha256(path) != object_value(object_value(projection['producer_artifacts'])[name])['sha256']
           for name, path in (('trace', trace), ('lifecycle', lifecycle), ('semantic_graph', semantic))):
        raise ValueError('producer artifact changed during probe')
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


def main() -> int:
    parser = argparse.ArgumentParser(description='Run producer composition on one RTL instance')
    for name in ('trace', 'lifecycle', 'semantic-graph', 'rtl-build', 'library', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--delays', required=True, help='first start phase, then arrival cycles after public ready')
    parser.add_argument('--parent-indices', default='0,1', help='increasing producer parent indices')
    args = parser.parse_args()
    try:
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
