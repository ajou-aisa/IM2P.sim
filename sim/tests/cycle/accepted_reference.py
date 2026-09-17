#!/usr/bin/env python3
"""Certify already-accepted generic FULL descriptors under rtl-regression memory.

This is a finite test fixture, not an op-trace API or system simulator. Generic
provider timing is recorded separately. Only scalar geometry/timing, never RTL
durations or events, is supplied to the value-free model.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import platform
import re
import shlex
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from sim.cycle import cli
from sim.tests.cycle import rtl_hardening as rtl
from sim.tests.cycle.production_block_certificate import RESULT_MAP, first_event_difference

FIELDS = {
    'max_i': 'maxI', 'max_j': 'maxJ', 'max_k': 'maxK',
    'pad_i': 'padI', 'pad_j': 'padJ', 'pad_k': 'padK',
    'a_address': 'aAddress', 'b_address': 'bAddress', 'c_address': 'cAddress',
    'scale_address': 'scaleBackingAddress', 'a_stride': 'aStrideBytes',
    'b_stride': 'bStrideBytes', 'c_stride': 'cStrideBytes',
    'scale_base': 'scaleBase', 'scale_generation': 'scaleGeneration',
    'fragment_base': 'fragmentBase', 'work_base': 'workBase',
    'accumulate': 'accumulate', 'final_fragment': 'finalFragment',
    'first_loop': 'firstLoop', 'final_loop': 'finalLoop',
    'logical_work_id': 'logicalWorkId', 'host_slot': 'hostSlot', 'rmd_raw': 'rmdRaw',
}
META = ('i', 'j', 'k', 'rows', 'columns', 'reduction', 'fragment_count')


def replace(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f'fixture source anchor changed: {old[:90]!r}')
    return text.replace(old, new)


def header(cases: list[dict[str, Any]]) -> str:
    names = (*META, *FIELDS)
    out = ['#pragma once', 'struct AcceptedDescriptor {']
    out += [f'  std::uint64_t {name};' for name in names]
    out += ['};', 'struct AcceptedCase {',
            '  std::uint64_t m,n,k,ti,tj,tk,output_stride;',
            '  std::vector<AcceptedDescriptor> work;', '};',
            'static const std::vector<AcceptedCase> accepted_cases = {']
    for case in cases:
        rows = [json.loads(line) for line in Path(case['observations']).read_text().splitlines()]
        accepted = [row for row in rows if row['event'] == 3]
        if not accepted or any(row['host_slot'] or row['rmd_raw'] for row in accepted):
            raise ValueError('only accepted FULL/slot0/dense scope is supported')
        g = accepted[0]['geometry']
        out.append('  {' + ','.join(str(g[x]) + 'ULL' for x in
                   ('m', 'n', 'k', 'tile_i_count', 'tile_j_count', 'tile_k_count')) +
                   ',' + str(accepted[0]['c_stride']) + 'ULL,{')
        for row in accepted:
            out.append('    {' + ','.join(str(row[name]) + 'ULL' for name in names) + '},')
        out.append('  }},')
    out += ['};', 'static const AcceptedCase *accepted_case = nullptr;',
            'static const AcceptedDescriptor *accepted_descriptor = nullptr;']
    if any('numerical_input' in case for case in cases):
        if not all('numerical_input' in case for case in cases):
            raise ValueError('mixed numerical/reference fixture corpus')
        out += ['struct AcceptedNumerical {',
                '  std::vector<std::int8_t> a, w;',
                '  std::vector<std::uint32_t> carriers;',
                '  std::vector<std::int32_t> expected;', '};',
                'static const std::vector<AcceptedNumerical> accepted_numerical = {']
        for case in cases:
            data = json.loads(Path(case['numerical_input']).read_text())
            g = data['geometry']
            m, n, k = g['m'], g['n'], g['k']
            av = [data['a'][i * data['a_stride'] + q] for i in range(m) for q in range(k)]
            wv = [data['w'][q * data['b_stride'] + j] for q in range(k) for j in range(n)]
            if k > 32 or len(data['carriers']) != n or len(data['output']) != m * n:
                raise ValueError('invalid compact numerical certificate input')
            vectors = [av, wv, data['carriers'], data['output']]
            out.append('  {' + ','.join('{' + ','.join(str(v) + ('ULL' if i == 2 else 'LL')
                       for v in values) + '}' for i, values in enumerate(vectors)) + '},')
        out += ['};']
    return '\n'.join(out) + '\n'


def fixture_source(golden: Path, numerical: bool = False) -> str:
    text = rtl.generate_probe_source(golden)
    text = replace(text, 'static unsigned hardening_case = 0;',
                   'static unsigned hardening_case = 0;\n#include "accepted-cases.hpp"')
    anchor = '    if (dut.io_work_valid && dut.io_work_ready) {\n'
    observation = '      hardening_observer << "ACCEPT"'
    for field in FIELDS.values():
        observation += f" << ',' << std::uint64_t(dut.io_work_bits_{field})"
    observation += " << '\\n';\n"
    text = replace(text, anchor, anchor + observation)
    drive = '    dut.io_work_bits_rmdRaw = work.plan.kind == WorkKind::rmd_raw;\n'
    override = '    check(accepted_descriptor != nullptr, "missing accepted descriptor");\n'
    override += '\n'.join(f'    dut.io_work_bits_{signal} = accepted_descriptor->{field};'
                           for field, signal in FIELDS.items()) + '\n'
    text = replace(text, drive, drive + override)
    start = text.index('  std::uint64_t planner_loop_count = 0, planner_fragment_count = 0;')
    end = text.index('  check(state.logical_done - old_done == 1,', start)
    text = text[:start] + '''  std::uint64_t planner_loop_count = 0, planner_fragment_count = 0;
  check(accepted_case != nullptr, "missing accepted case");
  for (const auto &a : accepted_case->work) {
    accepted_descriptor = &a;
    if (a.k == 0) {
      state.output_ids.assign(a.max_i * a.max_j, false);
      expected_outputs += a.max_i * a.max_j;
    }
    expected_contexts += a.fragment_count;
    expected_rows += a.rows * a.max_j * a.max_k;
    expected_scale_reads += ((a.reduction + 31) / 32) * a.max_j;
    state.loop(work, a.i, a.j, a.rows, a.columns, a.k, a.k + a.reduction,
               0, a.first_loop, a.final_loop, a.scale_generation);
    ++planner_loop_count;
    planner_fragment_count += a.fragment_count;
    if (a.final_fragment)
      check(std::all_of(state.output_ids.begin(), state.output_ids.end(),
                       [](bool done) { return done; }), "missing accepted output");
  }
''' + text[end:]
    text = replace(text, '  state.c_stride[state.active_slot] = padded(work.columns) * 4;',
                   '  state.c_stride[state.active_slot] = accepted_case->output_stride;')
    text = replace(text, '      padded(work.rows) * state.c_stride[state.active_slot], 0xA5);',
                   '      padded(work.rows) * state.c_stride[state.active_slot] + acc_bytes, 0xA5);')
    text = replace(text, '    check(offset % acc_bytes == 0, "RTL store is not an INT32 row");',
                   '    check(offset % 4 == 0, "RTL store is not INT32 aligned");')
    # The logical-row/padding mask oracle is unchanged. Guard bytes merely allow
    # a masked bus beat past a tightly packed final C row; latency is unchanged.
    main = text.index('int main(int argc, char **argv)')
    text = text[:main] + r'''int main(int argc, char **argv) {
  try {
    check(argc == 2, "accepted case index required");
    accepted_case = &accepted_cases.at(std::stoull(argv[1]));
    const auto &a = *accepted_case;
    VerilatedContext context;
    context.commandArgs(argc, argv);
    Dut dut{&context};
    Adapter state{dut, context};
    dut.io_work_valid = 0; dut.io_loopDone_ready = 1;
    dut.io_scaleRelease_valid = 0; dut.io_readRequest_ready = 1;
    dut.io_readBeat_valid = 0; dut.io_writeRequest_ready = 1;
    dut.io_writeCompletion_valid = 0;
    dut.reset = 1;
    for (unsigned edge = 0; edge < 5; ++edge) state.clock();
    dut.reset = 0; state.clock();
    FrontendRtlWork work{};
    work.work_id = a.work.front().logical_work_id;
    work.rows = a.m; work.columns = a.n; work.k = a.k;
    work.plan = {a.m,a.n,a.k,a.ti,a.tj,a.tk,a.m,Mode::full,WorkKind::dense_hp1_final};
    work.activations.assign(a.m * a.k, 1);
    work.weights.assign(a.k * a.n, 1);
    work.carriers.assign(((a.k + 31) / 32) * a.n, 0);
    std::vector<std::int32_t> output;
    FrontendRtlTiming timing{};
    check(execute(&state, work, output, timing) == IM2P_OK, "reference work failed");
    check(std::all_of(output.begin(), output.end(), [&](std::int32_t x) {
      return x == std::int32_t(a.k);
    }), "accepted descriptor reference numerical mismatch");
    dut.final();
    std::cout << "ACCEPTED_REFERENCE_PASS descriptors=" << a.work.size() << '\n';
  } catch (const std::exception &e) {
    std::cerr << "ACCEPTED_REFERENCE_FAIL " << e.what() << '\n';
    return 1;
  }
}
#endif
'''
    if numerical:
        text = replace(text, '    work.activations.assign(a.m * a.k, 1);\n'
                       '    work.weights.assign(a.k * a.n, 1);\n'
                       '    work.carriers.assign(((a.k + 31) / 32) * a.n, 0);',
                       '    const auto &numeric = accepted_numerical.at(std::stoull(argv[1]));\n'
                       '    work.activations = numeric.a;\n'
                       '    work.weights = numeric.w;\n'
                       '    work.carriers = numeric.carriers;')
        text = replace(text,
                       '    check(std::all_of(output.begin(), output.end(), [&](std::int32_t x) {\n'
                       '      return x == std::int32_t(a.k);\n'
                       '    }), "accepted descriptor reference numerical mismatch");',
                       '    check(output == numeric.expected, "accepted residual reference numerical mismatch");')
        text = replace(text, '    if (dut.io_events_rawRow) hardening_event("array_output");',
                       '    if (dut.io_events_rawRow) {\n'
                       '      hardening_event("array_output");\n'
                       '      hardening_observer << "SCU";\n'
                       '      for (unsigned j = 0; j < dim; ++j)\n'
                       '        hardening_observer << \',\' << std::int32_t(ROOT_SCU_DATA[j]);\n'
                       '      hardening_observer << \'\\n\';\n'
                       '    }')
    return text


def build(golden: Path, out: Path, profile: str, cases: list[dict[str, Any]]) -> Path:
    dest = out / profile
    dest.mkdir()
    (dest / 'accepted-cases.hpp').write_text(header(cases))
    original = golden / 'cycle-rtl/current' / profile
    obj = original / 'obj'
    command = shlex.split((original / 'build-command.txt').read_text())
    flags = shlex.split(command[command.index('-CFLAGS') + 1])
    ldflags = shlex.split(command[command.index('-LDFLAGS') + 1])
    top = command[command.index('--top-module') + 1]
    source = fixture_source(golden, numerical=bool(cases and 'numerical_input' in cases[0]))
    root = (obj / 'VIM2PGemminiWSHP1RtlTest___024root.h').read_text()
    names = {'ROOT_EX_FUNCT': '_reservation_io_issue_ex_cmd_cmd_inst_funct',
             'ROOT_MESH_INPUT': 'execute__DOT__mesh__DOT__input_next_row_into_spatial_array',
             'ROOT_MESH_ID': 'execute__DOT__mesh__DOT__matmul_id',
             'ROOT_MESH_ROW': 'execute__DOT__mesh__DOT__fire_counter',
             'ROOT_RAW_COMPLETED': '_execute_io_completed_valid',
             'ROOT_RAW_ROB': '_execute_io_completed_bits',
             'ROOT_SCU_DATA': 'writeback__DOT__writes__DOT__queue__DOT____Vcellinp__ram_ext__W0_data'}
    for token, suffix in names.items():
        signal = top + '__DOT__control__DOT__' + suffix
        if signal not in root:
            raise ValueError('pinned observer signal missing')
        source = source.replace(token, 'dut.rootp->' + signal)
    cpp = dest / 'accepted-reference.cpp'
    cpp.write_text(source)
    version = subprocess.check_output(['verilator', '-V'], text=True)
    found = re.search(r'VERILATOR_ROOT\s*=\s*(\S+)', version)
    if found is None:
        raise ValueError('Verilator include root unavailable')
    vroot = Path(found[1])
    objects = [obj / name for name in (
        'frontend_rtl_fixture.o', 'rmd_rtl_fixture.o', 'bound_rmd_rtl_fixture.o',
        'VIM2PGemminiWSHP1RtlTest__ALL.a', 'verilated.o', 'verilated_threads.o')]
    executable = dest / 'accepted-reference'
    argv = ['c++', *flags, '-O2', '-I' + str(ROOT / 'sim/common'),
            '-isystem', str(obj), '-isystem', str(vroot / 'include'),
            '-isystem', str(vroot / 'include/vltstd'), str(cpp), *map(str, objects),
            *ldflags, '-pthread', '-o', str(executable)]
    if platform.system() == 'Darwin':
        argv += ['-Wl,-U,__Z13sc_time_stampv', '-mmacosx-version-min=' + platform.mac_ver()[0]]
    if rtl.run_logged(out, argv, dest / 'compile.log'):
        raise RuntimeError(f'{profile}: accepted reference fixture build failed')
    (dest / 'provenance.json').write_text(json.dumps({
        'source_sha256': rtl.sha256(cpp), 'accepted_header_sha256': rtl.sha256(dest / 'accepted-cases.hpp'),
        'rtl_objects': {str(p): rtl.sha256(p) for p in objects},
        'timing': 'unchanged Adapter::step; serialized frames and release policy',
        'layout_extension': 'native C stride; INT32 alignment; guard tail, unchanged masked byte oracle',
    }, indent=2) + '\n')
    return executable


def compare(generic: Path, golden: Path, library: Path, out: Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=False)
    prior = json.loads(generic.read_text())
    if prior['status'] != 'PASS':
        raise ValueError('generic acceptance proof must pass before cycle comparison')
    cases = [c for c in prior['cases'] if c['mode'] == 'full']
    results: list[dict[str, Any]] = []
    for profile in sorted({c['profile'] for c in cases}):
        selected = [c for c in cases if c['profile'] == profile]
        exe = build(golden, out, profile, selected)
        for index, c in enumerate(selected):
            dest = exe.parent / f'case-{index:03}'
            dest.mkdir()
            events_path = dest / 'events.csv'
            import os
            rc = rtl.run_logged(out, [str(exe), str(index)], dest / 'run.log',
                                dict(os.environ, IM2P_CYCLE_OBSERVER=str(events_path)))
            if rc:
                raise RuntimeError(f'reference RTL failed: {dest / "run.log"}')
            events = list(csv.reader(events_path.open()))
            original = [json.loads(x) for x in Path(c['observations']).read_text().splitlines()]
            accepted = [r for r in original if r['event'] == 3]
            expected_payload = [[int(r[k]) for k in FIELDS] for r in accepted]
            actual_payload = [list(map(int, r[1:])) for r in events if r[0] == 'ACCEPT']
            descriptor_exact = expected_payload == actual_payload
            summary_line = next(line for line in (dest / 'run.log').read_text().splitlines()
                                if line.startswith('WS RTL '))
            summary = {k: int(v) for k, v in re.findall(r'\b(\w+)=(\d+)\b', summary_line)}
            case_input = next(r for r in events if r[0] == 'CASE')
            epoch = int(next(r[1] for r in events if r[0].isdigit() and r[2] == 'work'))
            g = accepted[0]['geometry']
            request = {'profile': profile, 'timing_profile': 'rtl-regression', 'request': {
                'm': g['m'], 'n': g['n'], 'k': g['k'], 'tile_i': g['tile_i_count'],
                'tile_j': g['tile_j_count'], 'tile_k': g['tile_k_count'],
                'activation_stride_bytes': accepted[0]['activation_host_stride'],
                'weight_stride_bytes': accepted[0]['weight_host_stride'],
                'scale_stride_elements': accepted[0]['scale_host_stride'] // 4,
                'output_stride_bytes': accepted[0]['output_host_stride'],
                'logical_work_id': accepted[0]['logical_work_id'], 'accepted_cycle': epoch,
                'submission': 'planner-blocks', 'record_events': 1},
                'timing': {'backing_cycle_offset': int(case_input[14])}}
            (dest / 'model-request.json').write_text(json.dumps(request, indent=2) + '\n')
            # RTL duration/done/counters/events never enter this API.
            model = cli.estimate(library, request)
            (dest / 'model-result.json').write_text(json.dumps(model, indent=2) + '\n')
            differences = {k: {'rtl': summary[k], 'model': model['result'][v]}
                           for k, v in RESULT_MAP.items() if summary[k] != model['result'][v]}
            reference_events = sorted((int(r[1]), r[2]) for r in events
                                      if r[0].isdigit() and r[2] in rtl.SELECTED_EVENTS)
            model_events = rtl.normalized_model_events(model['events'])
            first = first_event_difference(model_events, reference_events)
            delta = model['result']['total_cycles'] - summary['cycles']
            results.append({'profile': profile, 'shape': c['shape'], 'tile': c['production_tile'],
                'status': 'PASS' if descriptor_exact and not differences and first is None else 'FAIL',
                'generic_to_reference_accepted_payload_exact': descriptor_exact,
                'accepted_work_count': len(accepted), 'expected_work_count': c['expected_work_count'],
                'runtime_observation_sha256': rtl.sha256(Path(c['observations'])),
                'reference': {k: summary[k] for k in RESULT_MAP},
                'model': {k: model['result'][v] for k, v in RESULT_MAP.items()},
                'delta_cycles': delta, 'endpoint_counter_differences': differences,
                'selected_event_multiset_exact': first is None, 'first_event_divergence': first,
                'case_directory': str(dest)})
        print(profile, 'accepted FULL reference/model', sum(r['status'] == 'PASS' for r in results
              if r['profile'] == profile), '/', len(selected), flush=True)
    failures = [r for r in results if r['status'] != 'PASS']
    result = {'status': 'PASS' if results and not failures else 'FAIL', 'cases_total': len(results),
              'cases_exact': sum(r['status'] == 'PASS' for r in results),
              'max_abs_delta_cycles': max(abs(r['delta_cycles']) for r in results) if results else None,
              'first_mismatch': failures[0] if failures else None, 'cases': results,
              'scope': 'actual generic accepted FULL payloads, rtl-regression rev1 reference-memory/frame policy',
              'generic_provider_elapsed_prediction': False, 'physical_latency': 'NOT_CLAIMED',
              'model_library_sha256': rtl.sha256(library), 'generic_certificate_sha256': rtl.sha256(generic)}
    (out / 'full-rtl-cycle-comparison.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--generic-certificate', type=Path, required=True)
    p.add_argument('--golden-root', type=Path, required=True)
    p.add_argument('--library', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    result = compare(a.generic_certificate.resolve(), a.golden_root.resolve(),
                     a.library.resolve(), a.out.resolve())
    print(json.dumps({k: result[k] for k in ('status', 'cases_total', 'cases_exact', 'max_abs_delta_cycles')}))
    raise SystemExit(0 if result['status'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
