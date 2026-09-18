#!/usr/bin/env python3
"""Real generic transport/acceptance certificate; no second tiler or golden rewrite.

Runtime libraries must be built from the current sources with test-hooks. Each
probe uses the production llama adapter and public numerical API. Any separate
reference-memory timing projection is explicitly identified, not provider time.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import shlex
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from sim.tests.cycle.rtl_hardening import PROFILES, profile_bits_dim, run_logged


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_native(golden: Path, cargo: Path, cycle: Path, llama_config: Path,
                 out: Path, profile: str, api_only: bool = False) -> Path:
    dest = out / profile
    dest.mkdir(exist_ok=False)
    old = shlex.split((golden / 'cycle-rtl/current' / profile / 'build-command.txt').read_text())
    flags = shlex.split(old[old.index('-CFLAGS') + 1])
    remove = ('-DIM2P_GEMMINI_EXTERNAL_EXECUTOR_ONLY=',
              '-DGGML_GEMMINI_EXECUTION_BACKEND_FPGA_UART=', '-DGGML_GEMMINI_ENABLE_RMD=')
    flags = [x for x in flags if not x.startswith(remove)]
    header_root = ROOT.parent / 'RISC-V-DynDNN-gemmini-include'
    flags = [x for x in flags if x != '-I' + str(header_root)]
    flags += ['-isystem', str(header_root), '-I' + str(llama_config)]
    if not (llama_config / 'ggml-gemmini-matmul-config.hpp').is_file():
        raise ValueError('provide the current CMake-generated matmul configuration directory')
    flags += ['-DIM2P_SIM_IMPLEMENTATION_GEMMINI_HP1=1',
              '-DGGML_GEMMINI_EXECUTION_BACKEND_IM2P_SIM=1', '-DGGML_GEMMINI_ENABLE_RMD=0',
              '-DLOG_CYCLE=0', '-DLOG_DEBUG=0', '-DCYCLE_DETAIL=0']
    llama = ROOT.parent / 'llama.cpp-gemmini'
    adapter = llama / 'ggml/src/ggml-gemmini/ggml-gemmini-im2p.cpp'
    frontend = ROOT / 'frontend/src/im2p_gemmini_frontend.cpp'
    source = ROOT / 'sim/tests/cycle' / (
        'production_geometry_api_test.cpp' if api_only else 'production_geometry_probe.cpp')
    archive = cargo / profile / 'debug/libim2p_sim.a'
    if not archive.is_file():
        raise ValueError(f'missing current Rust/runtime archive: {archive}')
    host = golden / 'after-matrix' / profile / 'host-build'
    executable = dest / 'production-geometry-probe'
    dep = dest / 'probe.d'
    support = [llama / 'ggml/src/ggml-gemmini/ggml-gemmini-telemetry.cpp',
               llama / 'ggml/src/ggml-gemmini/quants/act/exsia/exsia.cpp',
               llama / 'ggml/src/ggml-gemmini-utils/src/cycle.cpp',
               llama / 'ggml/src/ggml-gemmini-utils/src/debug.cpp']
    command = ['c++', *flags, '-O2', '-MMD', '-MF', str(dep),
               str(source), str(frontend), str(adapter), *map(str, support),
               str(archive), str(cycle / 'libim2p_cycle_model.a'),
               str(host / 'libgemmini_hp1_host_common.a'),
               str(host / 'libgemmini_hp1_ggml_numeric.a'),
               '-pthread', '-o', str(executable)]
    if platform.system() == 'Darwin':
        command += ['-Wl,-dead_strip', '-mmacosx-version-min=' + platform.mac_ver()[0]]
    if run_logged(out, command, dest / 'compile.log'):
        raise RuntimeError(f'{profile}: native generic-path probe failed to build')
    provenance = {'profile': profile, 'runtime_archive': str(archive),
                  'runtime_archive_sha256': sha(archive), 'probe_sha256': sha(source),
                  'frontend_sha256': sha(frontend), 'llama_adapter_sha256': sha(adapter),
                  'cycle_archive_sha256': sha(cycle / 'libim2p_cycle_model.a'),
                  'compiler_dependencies': str(dep), 'executable_sha256': sha(executable),
                  'matmul_config_sha256': sha(llama_config / 'ggml-gemmini-matmul-config.hpp'),
                  'support_sources': {str(x): sha(x) for x in support},
                  'rmd_enabled': False, 'observer': 'actual io.work.valid && io.work.ready'}
    (dest / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    return executable


def run_case(out: Path, executable: Path, shape: tuple[int, int, int], mode: str) -> dict[str, Any]:
    case = f'{mode}-m{shape[0]}-n{shape[1]}-k{shape[2]}'
    dest = executable.parent / case
    dest.mkdir(exist_ok=False)
    observations = dest / 'accepted.jsonl'
    argv = [str(executable), *map(str, shape), mode, str(observations)]
    rc = run_logged(out, argv, dest / 'run.log')
    rows = [json.loads(x) for x in (dest / 'run.log').read_text().splitlines() if x.startswith('{')]
    summary: dict[str, Any] = rows[-1] if rows else {'status': 'FAIL'}
    summary.update(returncode=rc, log=str(dest / 'run.log'), observations=str(observations))
    if rc:
        summary['status'] = 'FAIL'
    (dest / 'result.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--golden-root', type=Path, required=True)
    p.add_argument('--cargo-root', type=Path, required=True)
    p.add_argument('--cycle-build', type=Path, required=True)
    p.add_argument('--llama-config', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--profiles', nargs='+', choices=PROFILES, default=list(PROFILES))
    p.add_argument('--smoke', action='store_true')
    p.add_argument('--api-only', action='store_true', help='real public API snapshot/isolation and passive-observer tests')
    a = p.parse_args()
    out = a.out.resolve()
    if out.is_relative_to(ROOT):
        p.error('evidence must be outside the repository')
    out.mkdir(parents=True, exist_ok=False)
    results = []
    error = None
    try:
        for profile in a.profiles:
            _, dim = profile_bits_dim(profile)
            exe = build_native(a.golden_root.resolve(), a.cargo_root.resolve(),
                               a.cycle_build.resolve(), a.llama_config.resolve(), out, profile, a.api_only)
            if a.api_only:
                rc = run_logged(out, [str(exe)], exe.parent / 'api-test.log')
                summaries = [json.loads(line) for line in (exe.parent / 'api-test.log').read_text().splitlines()
                             if line.startswith('{')]
                expected_tests = {
                    'full_isolation_and_observer_passivity',
                    'stripe_snapshot_admission',
                    'large_k_scale_cache_admission',
                }
                passed = (
                    rc == 0
                    and {r.get('test') for r in summaries} == expected_tests
                    and all(r.get('status') == 'PASS' for r in summaries)
                )
                results.append({'profile': profile, 'status': 'PASS' if passed else 'FAIL',
                                'returncode': rc, 'tests': summaries, 'log': str(exe.parent / 'api-test.log')})
                if not passed:
                    raise RuntimeError(f'public geometry API contract failed: {exe.parent / "api-test.log"}')
                print(profile, 'real API isolation/copy/negative/passivity tests PASS', flush=True)
                continue
            shapes = [(129, 129, 96)] if a.smoke else [
                (1, 1, 32), (2, 3, 64), (65, 67, 96), (129, 129, 96), (dim * 9 + 1, 1, 4096)]
            for shape in shapes:
                result = run_case(out, exe, shape, 'full')
                results.append(result)
                if result['status'] != 'PASS':
                    raise RuntimeError(f'generic FULL failed: {result["log"]}')
            for shape in [(129, 129, 96)]:
                result = run_case(out, exe, shape, 'pipeline')
                results.append(result)
                if result['status'] != 'PASS':
                    raise RuntimeError(f'generic PIPELINE failed: {result["log"]}')
            print(profile, 'generic FULL and PIPELINE actual descriptor comparison PASS', flush=True)
    except Exception as exc:
        error = str(exc)
    result = {'status': 'PASS' if error is None else 'FAIL', 'cases': results,
              'first_failure': error, 'scope': 'generic llama adapter to actual integrated RTL acceptance',
              'pipeline_system_timing': 'NOT_IMPLEMENTED', 'provider_timing_model_certificate': False}
    (out / 'geometry-propagation.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('status', 'first_failure')}, indent=2))
    raise SystemExit(0 if result['status'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
