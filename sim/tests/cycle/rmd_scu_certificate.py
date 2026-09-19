#!/usr/bin/env python3
"""Certify the production HP1 residual path with real Rust/Verilated execution.

Artifacts are external and exclusive. This is a finite test runner, not op-trace
replay or a system timing model. No recorded RTL duration enters the cycle API.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import platform
import shlex
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from sim.tests.cycle.rtl_hardening import PROFILES, run_logged, sha256

EVENTS = ('work', 'load_issue', 'execute_issue', 'store_issue', 'context',
          'load_dma', 'read_request', 'read_response', 'scratchpad_read',
          'array_input', 'raw_completed', 'array_output', 'accumulator_write',
          'accumulator_commit', 'store_dma', 'write_request', 'write_completion',
          'loop_done', 'logical_done')


def build(golden: Path, cargo: Path, cycle: Path, config: Path,
          out: Path, profile: str) -> Path:
    dest = out / profile
    dest.mkdir(exist_ok=False)
    command = shlex.split((golden / 'cycle-rtl/current' / profile / 'build-command.txt').read_text())
    flags = shlex.split(command[command.index('-CFLAGS') + 1])
    flags = [f for f in flags if not f.startswith((
        '-DIM2P_GEMMINI_EXTERNAL_EXECUTOR_ONLY=',
        '-DGGML_GEMMINI_EXECUTION_BACKEND_FPGA_UART=', '-DGGML_GEMMINI_ENABLE_RMD='))]
    headers = ROOT.parent / 'RISC-V-DynDNN-gemmini-include'
    flags = [f for f in flags if f != '-I' + str(headers)]
    flags += ['-isystem', str(headers), '-I' + str(config),
              '-DIM2P_SIM_IMPLEMENTATION_GEMMINI_HP1=1',
              '-DGGML_GEMMINI_EXECUTION_BACKEND_IM2P_SIM=1',
              '-DGGML_GEMMINI_ENABLE_RMD=1', '-DGGML_GEMMINI_TESTING=1',
              '-DLOG_CYCLE=0', '-DLOG_DEBUG=0', '-DCYCLE_DETAIL=0']
    llama = ROOT.parent / 'llama.cpp-gemmini/ggml/src'
    backend = llama / 'ggml-gemmini'
    sources = [ROOT / 'sim/tests/cycle/rmd_scu_probe.cpp',
               ROOT / 'frontend/src/im2p_gemmini_frontend.cpp',
               ROOT / 'fpga/gemmini_hp1/host/rmd.cpp',
               ROOT / 'fpga/gemmini_hp1/host/uart.cpp',
               ROOT / 'fpga/gemmini_hp1/host/ggml-gemmini-fpga.cpp']
    sources += [backend / ('residual/rmd/' + name + '.cpp') for name in (
        'rmd-builder', 'rmd-compose', 'rmd-executor', 'rmd-im2p-executor', 'rmd-reference')]
    sources += [backend / path for path in (
        'quants/common/weight_reader.cpp', 'quants/common/dequant.cpp',
        'quants/act/dispatch.cpp', 'quants/act/exsia/exsia.cpp', 'ggml-gemmini-telemetry.cpp')]
    sources += [llama / ('ggml-gemmini-utils/src/' + x + '.cpp') for x in ('cycle', 'debug', 'optrace')]
    archives = [cargo / profile / 'debug/libim2p_sim.a', cycle / 'libim2p_cycle_model.a',
                golden / 'after-matrix' / profile / 'host-build/libgemmini_hp1_ggml_numeric.a']
    if any(not f.is_file() for f in [*sources, *archives]):
        raise ValueError('missing current source/archive for ' + profile)
    exe = dest / 'rmd-scu-probe'
    args = ['c++', *flags, '-O2', '-ffunction-sections', '-fdata-sections',
            *map(str, sources), *map(str, archives), '-pthread', '-o', str(exe)]
    if platform.system() == 'Darwin':
        args += ['-Wl,-dead_strip', '-mmacosx-version-min=' + platform.mac_ver()[0]]
    else:
        args += ['-Wl,--gc-sections']
    rc = run_logged(out, args, dest / 'build.log')
    if rc:
        raise RuntimeError(f'{profile} compilation failed, exit {rc}')
    (dest / 'source-provenance.json').write_text(json.dumps({
        'sources': {str(f): sha256(f) for f in sources},
        'archives': {str(f): sha256(f) for f in archives},
        'executable_sha256': sha256(exe), 'production_source_compiled_directly': True,
        'runtime_test_hooks': True}, indent=2) + '\n')
    return exe


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--golden-root', type=Path, required=True)
    p.add_argument('--cargo-root', type=Path, required=True)
    p.add_argument('--cycle-build', type=Path, required=True)
    p.add_argument('--llama-config', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--profiles', nargs='+', choices=PROFILES, default=list(PROFILES))
    a = p.parse_args()
    out = a.out.resolve()
    if out.is_relative_to(ROOT):
        p.error('use fresh external evidence directory')
    out.mkdir(parents=True, exist_ok=False)
    results = []
    failure = None
    try:
        for profile in a.profiles:
            exe = build(a.golden_root.resolve(), a.cargo_root.resolve(), a.cycle_build.resolve(),
                        a.llama_config.resolve(), out, profile)
            dest = exe.parent / 'evidence'
            dest.mkdir()
            rc = run_logged(out, [str(exe), str(dest)], exe.parent / 'run.log')
            rows = [json.loads(s) for s in (exe.parent / 'run.log').read_text().splitlines()
                    if s.startswith('{')]
            summary = rows[-1] if rows else {}
            if rc or summary.get('status') != 'PASS' or 'profile' not in summary:
                raise RuntimeError(f'{profile} numerical/paired certificate failed, exit {rc}')
            summary['log'] = str(exe.parent / 'run.log')
            summary['invocations'] = str(dest / 'invocations.jsonl')
            summary['cases'] = rows[:-1]
            results.append(summary)
            print(profile, 'production residual SCU numerical/pairs PASS', flush=True)
    except Exception as exc:
        failure = str(exc)
    result = {'status': 'PASS' if not failure else 'FAIL',
              'profiles_total': len(a.profiles), 'profiles_passed': len(results),
              'first_failure': failure, 'profiles': results,
              'physical_fpga': 'NOT_RUN', 'provider_timing_model_claim': False}
    (out / 'residual-six-profile.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('status', 'profiles_passed', 'first_failure')}))
    raise SystemExit(1 if failure else 0)


if __name__ == '__main__':
    main()
