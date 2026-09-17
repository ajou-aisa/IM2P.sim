#!/usr/bin/env python3
"""Check captured tiling authority without claiming an end-to-end production trace.

The C++ probe calls the real shared tiler and real HP1 prepare boundary, then
intentionally refuses execution. Optional RTL checks project captured FULL
geometry through the shared lowerer under the reference adapter. Neither result
certifies the generic IM2P_SIM path, which lacks its tile-count companion.
All generated files and logs go to a new external output directory.
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
from sim.cycle import cli
from sim.tests.cycle.production_block_certificate import (
    RESULT_MAP, TIMING_KEYS, first_event_difference,
)
from sim.tests.cycle.rtl_hardening import (
    PROFILES, build_probe, normalized_model_events, run_logged, run_probe,
)

SOURCE = ROOT / 'sim/tests/cycle/schedule_authority_probe.cpp'
SHAPES = {(1, 1, 32), (2, 3, 64), (65, 67, 96), (129, 129, 96)}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_identity(repo: Path) -> dict[str, str]:
    def read(*args: str) -> str:
        return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
    return {'root': str(repo), 'head': read('rev-parse', 'HEAD'),
            'branch': read('branch', '--show-current'),
            'tracked_diff_sha256': hashlib.sha256(
                read('diff', '--binary').encode()).hexdigest()}


def captured_request(row: dict[str, Any], profile: str, accepted: int, offset: int) -> dict[str, Any]:
    """Never invent missing factors or project PIPELINE intent as a FULL timing case."""
    if (row.get('kind') != 'prepare_capture' or row.get('mode') != 'full'
            or row.get('companion_exact') is not True or row.get('lowering_exact') is not True):
        raise ValueError('an exact FULL prepare capture is required for an isolated projection')
    if profile != f"a{row.get('bits')}w{row.get('bits')}-d{row.get('dim')}-hp1":
        raise ValueError('capture and hardware profile differ')
    shape, tiles = row.get('shape'), row.get('tile')
    if not isinstance(shape, list) or not isinstance(tiles, list) or len(shape) != 3 or len(tiles) != 3:
        raise ValueError('shape and final production tile factors are required')
    if any(type(x) is not int or x <= 0 for x in shape + tiles):
        raise ValueError('shape and selected tile factors must be positive integers')
    stripe_rows = row.get('stripe_rows')
    if type(stripe_rows) is not int or stripe_rows != tiles[0] * row['dim']:
        raise ValueError('captured production stripe geometry is missing or inconsistent')
    return {
        'profile': profile, 'timing_profile': 'rtl-regression',
        'request': dict(zip(('m', 'n', 'k', 'tile_i', 'tile_j', 'tile_k'), shape + tiles),
                        accepted_cycle=accepted, submission='planner-blocks', record_events=1),
        'timing': dict(zip(TIMING_KEYS, (3, 13, 17, 11, 5, offset))),
    }


def check_projection(probes: Path, executable: Path, library: Path,
                     capture: dict[str, Any], profile: str) -> dict[str, Any]:
    # Validate BEFORE invoking RTL. Only captured geometry enters either side.
    request = captured_request(capture, profile, 0, 5)
    geometry = request['request']
    rtl = run_probe(probes, executable, f"captured-{capture['case']:03}",
                    m=geometry['m'], n=geometry['n'], k=geometry['k'],
                    tile_i=geometry['tile_i'], tile_j=geometry['tile_j'], tile_k=geometry['tile_k'],
                    timing=(3, 13, 17, 11, 5), raw=False)
    if rtl['returncode'] or len(rtl['cases']) != 1 or not rtl['work_events']:
        raise RuntimeError(f"captured geometry RTL failed: {rtl['log']}")
    accepted, offset = int(rtl['work_events'][0][1]), int(rtl['cases'][0][14])
    request = captured_request(capture, profile, accepted, offset)
    model = cli.estimate(library, request)
    differences = {
        key: {'rtl': rtl['summary'][key], 'model': model['result'][field]}
        for key, field in RESULT_MAP.items() if rtl['summary'][key] != model['result'][field]
    }
    first = first_event_difference(normalized_model_events(model['events']), rtl['selected_events'])
    return {
        'profile': profile, 'capture_case': capture['case'], 'request': request,
        'captured_geometry': {key: capture[key] for key in ('shape', 'tile', 'mode', 'stripe_rows')},
        'scope': 'prepare-captured FULL geometry; isolated slot0/reference-memory projection',
        'status': 'PASS' if not differences and first is None else 'FAIL',
        'rtl': {key: rtl['summary'][key] for key in RESULT_MAP},
        'model': {key: model['result'][field] for key, field in RESULT_MAP.items()},
        'delta_cycles': model['result']['total_cycles'] - rtl['summary']['cycles'],
        'differences': differences, 'selected_event_multiset_exact': first is None,
        'first_event_divergence': first, 'rtl_log': rtl['log'], 'rtl_events': rtl['events_path'],
        'end_to_end_production_certified': False,
    }


def check(golden: Path, cycle_build: Path, out: Path,
          profiles: list[str], with_rtl: bool) -> dict[str, Any]:
    if out.is_relative_to(ROOT):
        raise ValueError('output must be outside the repository')
    out.mkdir(parents=True, exist_ok=False)
    library = cycle_build / ('libim2p_cycle_model.dylib' if platform.system() == 'Darwin'
                             else 'libim2p_cycle_model.so')
    archive = cycle_build / 'libim2p_cycle_model.a'
    if not library.is_file() or not archive.is_file():
        raise ValueError('build the current cycle library before running authority checks')
    results: list[dict[str, Any]] = []
    projections: list[dict[str, Any]] = []
    failure = None
    try:
        for profile in profiles:
            dest = out / profile
            dest.mkdir()
            manifest = golden / 'after-matrix' / profile / 'resolved-profile.json'
            host = dest / 'host'
            stages = [
                ('host-configure', ['cmake', '-S', str(ROOT / 'fpga/gemmini_hp1/host'), '-B', str(host),
                                    '-DCMAKE_BUILD_TYPE=Release',
                                    '-DIM2P_GEMMINI_RESOLVED_PROFILE=' + str(manifest)]),
                ('host-build', ['cmake', '--build', str(host), '--parallel', '2']),
                ('host-ctest', ['ctest', '--test-dir', str(host), '--output-on-failure']),
            ]
            for label, argv in stages:
                if run_logged(out, argv, dest / (label + '.log')):
                    raise RuntimeError(f'{profile}: {label} failed; original log retained')
            flags_command = shlex.split((golden / 'cycle-rtl/current' / profile / 'build-command.txt').read_text())
            flags = shlex.split(flags_command[flags_command.index('-CFLAGS') + 1])
            executable = dest / 'authority-probe'
            depfile = dest / 'authority-probe.d'
            dead_strip = ['-Wl,-dead_strip'] if platform.system() == 'Darwin' else []
            command = ['c++', *flags, '-O2', '-MMD', '-MF', str(depfile), str(SOURCE),
                       str(archive), str(host / 'libgemmini_hp1_host_common.a'),
                       str(host / 'libgemmini_hp1_ggml_numeric.a'),
                       *dead_strip, '-pthread', '-o', str(executable)]
            if run_logged(out, command, dest / 'probe-build.log'):
                raise RuntimeError(f'{profile}: probe compile failed; original log retained')
            dependencies = depfile.read_text().replace('\\\n', ' ')
            header = ROOT.parent / 'RISC-V-DynDNN-gemmini-include/gemmini.h'
            params = manifest.parent / 'host-params/gemmini_params.h'
            if str(header) not in dependencies or str(params) not in dependencies:
                raise ValueError('actual compiler dependency record lacks expected header/profile authority')
            if run_logged(out, [str(executable)], dest / 'capture.jsonl'):
                raise RuntimeError(f'{profile}: boundary capture failed; original log retained')
            rows = [json.loads(line) for line in (dest / 'capture.jsonl').read_text().splitlines()
                    if line.startswith('{')]
            captures = [row for row in rows if row.get('kind') == 'prepare_capture']
            counterexample = [row for row in rows if row.get('kind') == 'generic_projection']
            expected = {(shape, mode) for shape in SHAPES for mode in ('full', 'pipeline')}
            if (len(captures) != 8 or
                {(tuple(row['shape']), row['mode']) for row in captures} != expected or
                any(row['companion_exact'] is not True or row['lowering_exact'] is not True for row in captures)):
                raise ValueError('complete capture and shared-lowerer coverage missing')
            if len(counterexample) != 1 or counterexample[0]['distinct_factors_same_descriptor'] is not True:
                raise ValueError('public-ABI geometry information-loss counterexample missing')
            result = {'profile': profile, 'status': 'PASS', 'prepare_captures': captures,
                      'public_abi_counterexample': counterexample[0],
                      'production_certificate': 'BLOCKED_MISSING_TILE_COMPANION',
                      'compiler_provenance': {
                          'effective_header': str(header), 'header_sha256': sha(header),
                          'effective_params': str(params), 'params_sha256': sha(params),
                          'compiler_dependencies': str(depfile), 'dependencies_sha256': sha(depfile),
                          'probe_sha256': sha(SOURCE), 'executable_sha256': sha(executable),
                          'cycle_archive_sha256': sha(archive),
                          'fresh_host_library_sha256': sha(host / 'libgemmini_hp1_host_common.a'),
                      }}
            results.append(result)
            if with_rtl:
                probes = dest / 'rtl-projection'
                probes.mkdir()
                rtl_executable = build_probe(golden, probes, profile)
                for row in captures:
                    if row['mode'] == 'full':
                        projections.append(check_projection(probes, rtl_executable, library, row, profile))
            print(profile, 'captured geometry 8/8; public-ABI missing companion reproduced', flush=True)
    except Exception as error:
        failure = str(error)
    passed = (failure is None and len(results) == len(profiles) and
              (not with_rtl or len(projections) == 4 * len(profiles)) and
              all(row['status'] == 'PASS' for row in projections))
    final = {
        'status': 'PASS' if passed else 'FAIL',
        'scope': 'real shared tiler + real HP1 prepare capture + deterministic lowering checks',
        'profiles': results, 'prepare_captures_total': sum(len(r['prepare_captures']) for r in results),
        'generic_public_abi': 'BLOCKED_MISSING_TILE_COMPANION',
        'generic_geometry_counterexamples': len(results),
        'end_to_end_production_certified': False,
        'reference_memory_projection': {
            'status': ('PASS' if passed else 'FAIL') if with_rtl else 'NOT_RUN',
            'cases': projections, 'cases_total': len(projections),
            'cases_exact': sum(row['status'] == 'PASS' for row in projections),
            'max_abs_delta_cycles': max((abs(row['delta_cycles']) for row in projections), default=None),
            'scope': 'FULL captures only; fresh/reset slot0; rtl-regression memory; not actual production timing',
        },
        'failure': failure,
        'source_identity': {
            'im2p': git_identity(ROOT),
            'llama': git_identity(ROOT.parent / 'llama.cpp-gemmini'),
            'headers': git_identity(ROOT.parent / 'RISC-V-DynDNN-gemmini-include'),
        },
    }
    (out / 'authority-check.json').write_text(json.dumps(final, indent=2) + '\n')
    return final


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--golden-root', type=Path, required=True)
    parser.add_argument('--cycle-build', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--profiles', nargs='+', choices=PROFILES, default=list(PROFILES))
    parser.add_argument('--with-rtl', action='store_true')
    args = parser.parse_args()
    if len(set(args.profiles)) != len(args.profiles):
        parser.error('duplicate profiles are not allowed')
    result = check(args.golden_root.resolve(), args.cycle_build.resolve(), args.out.resolve(),
                   args.profiles, args.with_rtl)
    print(json.dumps({key: result[key] for key in ('status', 'prepare_captures_total',
        'generic_public_abi', 'end_to_end_production_certified', 'failure')}))
    raise SystemExit(0 if result['status'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
