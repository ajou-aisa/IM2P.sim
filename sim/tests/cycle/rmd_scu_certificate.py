#!/usr/bin/env python3
"""Certify the production HP1 residual path with real Rust/Verilated execution.

Artifacts are external and exclusive. This is a finite test runner, not op-trace
replay or a system timing model. No recorded RTL duration enters the cycle API.
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import platform
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rtl_hardening import PROFILES, run_logged, sha256

EVENTS = ('work', 'load_issue', 'execute_issue', 'store_issue', 'context',
          'load_dma', 'read_request', 'read_response', 'scratchpad_read',
          'array_input', 'raw_completed', 'array_output', 'accumulator_write',
          'accumulator_commit', 'store_dma', 'write_request', 'write_completion',
          'loop_done', 'logical_done')


@dataclass(frozen=True, slots=True)
class CertificateInputError(ValueError):
    detail: str

    def __str__(self) -> str:
        return self.detail


def clean_head(llama: Path) -> str:
    root = subprocess.run(['git', '-C', str(llama), 'rev-parse', '--show-toplevel'],
                          check=True, capture_output=True, text=True).stdout.strip()
    head = subprocess.run(['git', '-C', str(llama), 'rev-parse', 'HEAD'],
                          check=True, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(['git', '-C', str(llama), 'status', '--porcelain=v1',
                            '--untracked-files=all'], check=True, capture_output=True,
                           text=True).stdout
    if Path(root).resolve() != llama.resolve() or dirty:
        raise CertificateInputError('llama root must be a clean Git worktree root')
    return head


def build(golden: Path | None, cargo: Path, cycle: Path, config: Path | None,
          out: Path, profile: str, *, llama_root: Path | None = None,
          build_root: Path | None = None) -> Path:
    headers = ROOT.parent / 'RISC-V-DynDNN-gemmini-include'
    if build_root is None:
        assert golden is not None and config is not None
        command = shlex.split((golden / 'cycle-rtl/current' / profile / 'build-command.txt').read_text())
        flags = shlex.split(command[command.index('-CFLAGS') + 1])
        llama_root = ROOT.parent / 'llama.cpp-gemmini'
        archives = [cargo / profile / 'debug/libim2p_sim.a', cycle / 'libim2p_cycle_model.a',
                    golden / 'after-matrix' / profile / 'host-build/libgemmini_hp1_ggml_numeric.a']
        extra_sources = ('cycle', 'debug', 'optrace')
        provenance = {}
    else:
        assert llama_root is not None
        llama_root = llama_root.resolve()
        build_root = build_root.resolve()
        result_path = build_root / 'result.json'
        result = json.loads(result_path.read_text())
        if result['stage'] != 'host-test' or result['status'] != 'PASS':
            raise CertificateInputError('fresh official host-test result is not PASS')
        matches = [row for row in result['profiles'] if row['profile'] == profile]
        if len(matches) != 1 or matches[0]['status'] != 'PASS':
            raise CertificateInputError('fresh official profile is not PASS: ' + profile)
        row = matches[0]
        head = clean_head(llama_root)
        if (Path(row['llama_source']['root']).resolve() != llama_root or
                row['llama_source']['head'] != head):
            raise CertificateInputError('official profile llama source does not match clean root')
        manifest = Path(row['resolved_profile']).resolve()
        profile_dir = manifest.parent
        if not profile_dir.is_relative_to(build_root) or not manifest.is_file():
            raise CertificateInputError('official profile manifest is outside build root')
        resolved = json.loads(manifest.read_text())
        if (resolved['profile'] != profile or resolved['llama_source'] != row['llama_source']):
            raise CertificateInputError('official hardware contract does not match profile/source')
        commands = [entry['arguments'] for entry in row['command_results']
                    if entry['returncode'] == 0 and '-CFLAGS' in entry['arguments']]
        if len(commands) != 1 or any(entry['returncode'] != 0 for entry in row['command_results']):
            raise CertificateInputError('official profile CFLAGS are incomplete')
        command = commands[0]
        flags = shlex.split(command[command.index('-CFLAGS') + 1])
        allowed = {path.resolve() for path in (
            profile_dir / 'host-params', ROOT / 'fpga/gemmini_hp1/host',
            ROOT / 'frontend/include', ROOT / 'sim/include', ROOT / 'sim/ffi',
            llama_root / 'ggml/src/ggml-gemmini',
            llama_root / 'ggml/src/ggml-gemmini-utils/include',
            llama_root / 'ggml/include', llama_root / 'ggml/src',
            llama_root / 'common', headers)}
        if (any(Path(flag[2:]).resolve() not in allowed for flag in flags if flag.startswith('-I'))
                or any(flag in ('-I', '-isystem', '-iquote', '-include') for flag in flags)):
            raise CertificateInputError('official CFLAGS contain an unbound include path')
        archives = [cargo / profile / 'debug/libim2p_sim.a', cycle / 'libim2p_cycle_model.a',
                    profile_dir / 'host-build/libgemmini_hp1_ggml_numeric.a',
                    profile_dir / 'host-build/gemmini-utils/libggml-gemmini-utils.a']
        extra_sources = ()
        cache = (profile_dir / 'host-build/CMakeCache.txt').read_text()
        capabilities = [line.partition('=')[2] for line in cache.splitlines()
                        if line.startswith('IM2P_PRODUCTION_TRACE_ENABLED:BOOL=')]
        if len(capabilities) != 1 or capabilities[0] not in ('ON', 'OFF'):
            raise CertificateInputError('official host trace capability is missing')
        trace_on = capabilities[0] == 'ON'
        utility = llama_root / 'ggml/src/ggml-gemmini-utils'
        if trace_on and not all(path.is_file() for path in (
                utility / 'include/gemmini/optrace.hpp', utility / 'src/optrace.cpp')):
            raise CertificateInputError('trace-ON requires real pinned optrace API')
        flags += ['-DIM2P_PRODUCTION_TRACE_ENABLED=' + str(int(trace_on))]
        provenance = {'llama_root': str(llama_root), 'llama_head': head,
                      'host_result': str(result_path), 'host_result_sha256': sha256(result_path),
                      'resolved_profile': str(manifest), 'resolved_profile_sha256': sha256(manifest),
                      'host_cache_sha256': sha256(profile_dir / 'host-build/CMakeCache.txt'),
                      'host_params_sha256': sha256(profile_dir / 'host-params/gemmini_params.h'),
                      'hardware_header_sha256': sha256(profile_dir / 'im2p_gemmini_hardware.h'),
                      'trace_enabled': trace_on}
    flags = [f for f in flags if not f.startswith((
        '-DIM2P_GEMMINI_EXTERNAL_EXECUTOR_ONLY=',
        '-DGGML_GEMMINI_EXECUTION_BACKEND_FPGA_UART=', '-DGGML_GEMMINI_ENABLE_RMD='))]
    flags = [f for f in flags if f != '-I' + str(headers)]
    flags += ['-isystem', str(headers),
              '-DIM2P_SIM_IMPLEMENTATION_GEMMINI_HP1=1',
              '-DGGML_GEMMINI_EXECUTION_BACKEND_IM2P_SIM=1',
              '-DGGML_GEMMINI_ENABLE_RMD=1', '-DGGML_GEMMINI_TESTING=1',
              '-DLOG_CYCLE=0', '-DLOG_DEBUG=0', '-DCYCLE_DETAIL=0']
    if config is not None:
        flags += ['-I' + str(config)]
    llama = llama_root / 'ggml/src'
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
        'quants/act/dispatch.cpp', 'quants/act/exsia/exsia.cpp', 'ggml-gemmini-telemetry.cpp')
        if build_root is None or path != 'ggml-gemmini-telemetry.cpp']
    sources += [llama / ('ggml-gemmini-utils/src/' + x + '.cpp') for x in extra_sources]
    if any(not f.is_file() for f in [*sources, *archives]):
        raise ValueError('missing current source/archive for ' + profile)
    dest = out / profile
    dest.mkdir(exist_ok=False)
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
        'runtime_test_hooks': True, **provenance}, indent=2) + '\n')
    return exe


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--golden-root', type=Path)
    p.add_argument('--build-root', type=Path)
    p.add_argument('--llama-root', type=Path)
    p.add_argument('--cargo-root', type=Path, required=True)
    p.add_argument('--cycle-build', type=Path, required=True)
    p.add_argument('--llama-config', type=Path)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--profiles', nargs='+', choices=PROFILES, default=list(PROFILES))
    a = p.parse_args()
    if bool(a.build_root) != bool(a.llama_root):
        p.error('--build-root and --llama-root must be used together')
    if a.build_root is None and (a.golden_root is None or a.llama_config is None):
        p.error('legacy mode requires --golden-root and --llama-config')
    out = a.out.resolve()
    if out.is_relative_to(ROOT):
        p.error('use fresh external evidence directory')
    out.mkdir(parents=True, exist_ok=False)
    results = []
    failure = None
    try:
        for profile in a.profiles:
            exe = build(a.golden_root.resolve() if a.golden_root else None,
                        a.cargo_root.resolve(), a.cycle_build.resolve(),
                        a.llama_config.resolve() if a.llama_config else None, out, profile,
                        llama_root=a.llama_root, build_root=a.build_root)
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
