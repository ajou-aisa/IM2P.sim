#!/usr/bin/env python3
"""Passive event observer for the retained numerical fixture; never an estimator.

Relink an unchanged, preserved RTL model with an instrumented copy of its fixture.
Outputs go to a new external directory. No model, golden, or repository source is
rewritten. The comparison runner does not make these observer files model inputs.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[3]


def run(command: list[str], out: Path, name: str, env: dict[str, str] | None = None) -> None:
    with (out / 'commands.jsonl').open('a') as f:
        f.write(json.dumps({'argv': command, 'cwd': str(ROOT), 'log': name}) + '\n')
    with (out / name).open('x') as f:
        result = subprocess.run(command, cwd=ROOT, env=env, stdout=f,
                                stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise RuntimeError(f'{name}: exit {result.returncode}')


def observe(golden: Path, output: Path, profiles: list[str], value_pair_phase: bool = False) -> None:
    output.mkdir(parents=True, exist_ok=False)
    source = golden / 'cycle_probe_test_ws_rtl.cpp'
    original = source.read_text()
    # This token is after input setup/eval and before any edge/counter updates.
    anchor = '    dut.eval();\n    if (dut.io_error) {'
    assert original.count(anchor) == 1
    injected = r'''
    auto ev = [&](const char *kind, std::uint64_t a = 0, std::uint64_t b = 0,
                  std::uint64_t c = 0) {
      observer << observer_case << ',' << dut.io_coreCycle << ',' << kind
               << ',' << a << ',' << b << ',' << c << '\n';
    };
    if (dut.io_work_valid && dut.io_work_ready) {
      ev("work", dut.io_work_bits_maxI, dut.io_work_bits_maxJ, dut.io_work_bits_maxK);
      observer << "LOOP," << observer_case << ',' << dut.io_coreCycle << ','
               << unsigned(dut.io_work_bits_firstLoop) << ',' << unsigned(dut.io_work_bits_finalLoop)
               << ',' << unsigned(dut.io_work_bits_accumulate) << ','
               << dut.io_work_bits_fragmentBase << ',' << unsigned(dut.io_work_bits_hostSlot)
               << ',' << dut.io_work_bits_scaleBase << '\n';
    }
    if (dut.io_events_loadIssued) ev("load_issue");
    if (dut.io_events_executeIssued) ev("execute_issue", ROOT_EX_FUNCT);
    if (dut.io_events_storeIssued) ev("store_issue");
    if (dut.io_events_outputContextIssued) ev("context");
    if (dut.io_events_loadDmaAccepted)
      ev("load_dma", dut.io_events_loadLocalAddressRaw,
         dut.io_events_loadColumnsElements, dut.io_events_loadVaddrBytes);
    if (dut.io_readRequest_valid && dut.io_readRequest_ready)
      ev("read_request", dut.io_readRequest_bits_id, dut.io_readRequest_bits_address);
    if (dut.io_readBeat_valid && dut.io_readBeat_ready) ev("read_response", dut.io_readBeat_bits_id);
    if (dut.io_events_scratchpadReadAcceptedBankMask)
      ev("scratchpad_read", dut.io_events_scratchpadReadAcceptedBankMask);
    if (ROOT_MESH_INPUT) ev("array_input", ROOT_MESH_ID, ROOT_MESH_ROW);
    if (ROOT_RAW_COMPLETED) ev("raw_completed", ROOT_RAW_ROB);
    if (dut.io_events_rawRow) ev("array_output");
    if (dut.io_events_scaledRow) ev("accumulator_write");
    if (dut.io_events_accumulatorCommitted) ev("accumulator_commit");
    if (dut.io_events_storeDmaAccepted)
      ev("store_dma", dut.io_events_storeLocalAddressRaw, dut.io_events_storeLengthElements);
    if (dut.io_writeRequest_valid && dut.io_writeRequest_ready)
      ev("write_request", dut.io_writeRequest_bits_id, dut.io_writeRequest_bits_address);
    if (dut.io_writeCompletion_valid && dut.io_writeCompletion_ready)
      ev("write_completion", dut.io_writeCompletion_bits_id);
    if (dut.io_loopDone_valid && dut.io_loopDone_ready) ev("loop_done");
    if (dut.io_logicalDone_valid) ev("logical_done");
    if (dut.io_scaleRelease_valid && dut.io_scaleRelease_ready)
      ev("scale_release", dut.io_scaleRelease_bits_address, dut.io_scaleRelease_bits_column);
'''
    # Input facts only; no golden timing result is present in this record.
    request = r'''
  ++observer_case;
  observer << "CASE," << observer_case << ',' << work.rows << ',' << work.columns << ',' << work.k
           << ',' << work.row_begin << ',' << work.plan.tile_i << ',' << work.plan.tile_j
           << ',' << work.plan.tile_k << ',' << state.read_latency << ',' << state.reorder_latency
           << ',' << state.scale_latency << ',' << state.write_latency << ',' << state.read_ready_period
           << ',' << state.cycle - state.dut.io_coreCycle << '\n';
'''
    anchor_request = '  const auto old_contexts = state.contexts, old_raw = state.raw_rows, old_commits = state.commits;'
    assert original.count(anchor_request) == 1
    v = subprocess.check_output(['verilator', '-V'], text=True)
    vroot_match = re.search(r'VERILATOR_ROOT\s*=\s*(\S+)', v)
    if vroot_match is None:
        raise ValueError('Verilator include root not reported')
    vroot = Path(vroot_match.group(1))
    for profile in profiles:
        out = output / profile
        out.mkdir()
        build = golden / 'cycle-rtl/current' / profile
        obj = build / 'obj'
        command = shlex.split((build / 'build-command.txt').read_text())
        flags = shlex.split(command[command.index('-CFLAGS') + 1])
        ldflags = shlex.split(command[command.index('-LDFLAGS') + 1])
        top = command[command.index('--top-module') + 1]
        root_prefix = 'dut.rootp->' + top + '__DOT__control__DOT__'
        text = original.replace(anchor, '    dut.eval();\n' + injected + '\n    if (dut.io_error) {')
        if value_pair_phase:
            value_alignment = r'''
  if (observer_value_pair) {
    observer << "VALUE_INPUT," << (observer_case + 1) << ','
             << std::count_if(work.activations.begin(), work.activations.end(), [](auto v) { return v != 0; })
             << ',' << std::count_if(work.weights.begin(), work.weights.end(), [](auto v) { return v != 0; }) << '\n';
  }
'''
            text = text.replace(anchor_request, value_alignment + request + anchor_request)
            begin = '    RmdRawWork raw_boundary;'
            end = '    std::cout << "WS_RMD_RAW_BOUNDARY'
            assert text.count(begin) == text.count(end) == 1
            text = text.replace(begin, '    observer_value_pair = true;\n' + begin)
            text = text.replace(end, '    observer_value_pair = false;\n' + end)
            accepted = '    dut.io_work_valid = 1;\n    accept([&] { return dut.io_work_ready; }, "loop descriptor stalled");'
            aligned = r'''    if (observer_value_pair) {
      dut.io_work_valid = 0;
      dut.eval();
      std::uint64_t alignment_wait = 0;
      while (cycle % read_ready_period || !dut.io_work_ready) {
        check(++alignment_wait < 1000, "value-pair acceptance alignment stalled");
        step();
      }
    }
'''
            assert text.count(accepted) == 1
            text = text.replace(accepted, aligned + accepted)
        else:
            text = text.replace(anchor_request, request + anchor_request)
        includes = '''#include <fstream>
#include <cstdlib>
#include <VIM2PGemminiWSHP1RtlTest___024root.h>
static std::ofstream observer(std::getenv("IM2P_CYCLE_OBSERVER"));
static unsigned observer_case = 0;
static bool observer_value_pair = false;
'''
        if not value_pair_phase:
            includes = includes.replace('static bool observer_value_pair = false;\n', '')
        pos = text.index('\nnamespace {')
        text = text[:pos] + '\n' + includes + text[pos:]
        names = {
            'ROOT_EX_FUNCT': '_reservation_io_issue_ex_cmd_cmd_inst_funct',
            'ROOT_MESH_INPUT': 'execute__DOT__mesh__DOT__input_next_row_into_spatial_array',
            'ROOT_MESH_ID': 'execute__DOT__mesh__DOT__matmul_id',
            'ROOT_MESH_ROW': 'execute__DOT__mesh__DOT__fire_counter',
            'ROOT_RAW_COMPLETED': '_execute_io_completed_valid',
            'ROOT_RAW_ROB': '_execute_io_completed_bits',
        }
        header = (obj / 'VIM2PGemminiWSHP1RtlTest___024root.h').read_text()
        for token, suffix in names.items():
            symbol = root_prefix + suffix
            assert symbol.split('->')[1] in header, symbol
            text = text.replace(token, symbol)
        probe = out / 'observer.cpp'
        probe.write_text(text)
        executable = out / 'observer'
        retained = [obj / name for name in ('frontend_rtl_fixture.o', 'rmd_rtl_fixture.o',
                    'bound_rmd_rtl_fixture.o', 'VIM2PGemminiWSHP1RtlTest__ALL.a',
                    'verilated.o', 'verilated_threads.o')]
        weak_time_link = ['-Wl,-U,__Z13sc_time_stampv'] if platform.system() == 'Darwin' else []
        compile_cmd = ['c++', *flags, '-O2', '-isystem', str(obj), '-isystem', str(vroot / 'include'),
                       '-isystem', str(vroot / 'include/vltstd'), str(probe),
                       *map(str, retained), *ldflags, *weak_time_link, '-pthread', '-o', str(executable)]
        run(compile_cmd, output, profile + '-build.log')
        run([str(executable)], output, profile + '-run.log',
            dict(os.environ, IM2P_CYCLE_OBSERVER=str(out / 'events.csv')))
        expected = (build / 'run.log').read_bytes()
        actual = (output / (profile + '-run.log')).read_bytes()
        if value_pair_phase:
            assert b'WS_RMD_RAW_BOUNDARY compact_k=32 extremal_exact=1 zero_replace=1' in actual
        else:
            assert expected == actual, f'observation changed runtime output: {profile}'
        (out / 'provenance.json').write_text(json.dumps({
            'observer_only': not value_pair_phase, 'runtime_log_byte_identical': expected == actual,
            'value_pair_phase_aligned_test': value_pair_phase,
            'original_numerical_assertions_preserved': True,
            'original_fixture': str(source), 'original_fixture_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'compiled_model': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in retained},
            'observer_source_sha256': hashlib.sha256(probe.read_bytes()).hexdigest(),
        }, indent=2) + '\n')
        print(profile + ' passive RTL observation PASS', flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--golden-root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--profiles', nargs='+', default=[f'a{b}w{b}-d{d}-hp1'
                        for b in (4, 8) for d in (16, 32, 64)])
    a = parser.parse_args()
    observe(a.golden_root.resolve(), a.out.resolve(), a.profiles)


if __name__ == '__main__':
    main()
