#!/usr/bin/env python3
"""Explicit SCU candidate snapshots. No device access or historical lock mutation."""
import argparse
import importlib.util
import json
from pathlib import Path
import platform
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('dense_build', ROOT / 'fpga/dense_pipeline/build.py')
dense = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dense)
full = dense.full
EDITABLE_BASE = '3aeb5feee6872f88ec1f6a5dc0d77fb1bb8babf8'


def refresh_core_patch():
    # Only the numerical delta is applied to the board core. Its A1 arithmetic
    # and schedulers must not be replaced with similarly named root files.
    patch = subprocess.check_output(['git', '-C', str(ROOT), 'diff', '--binary',
                                     EDITABLE_BASE, '--', 'src'])
    if not patch:
        raise ValueError('empty SCU core delta')
    (HERE / 'core.patch').write_bytes(patch)


def freeze(out, host_repo, params_repo):
    full.freeze(out)
    full.command(['patch', '--batch', '--fuzz=0', '-p1', '-i', HERE / 'core.patch'],
                 out / 'source', out / 'scu-core-patch')
    selected = ['Makefile', 'synth/ScuPipeline.bsv', 'fpga/dense_pipeline/build.py',
                'fpga/dense_pipeline/deployment-lock.json',
                'fpga/dense_pipeline/host-integration.patch',
                'fpga/dense_pipeline/producer-observation.patch']
    # These trees have no board-only arithmetic/scheduler variant. Every input
    # is versionable; ignored build products are excluded by Git's file list.
    paths = subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '--cached',
                                     '--others', '--exclude-standard', '--',
                                     'sim', 'scripts', 'config', 'frontend',
                                     'tests', 'synth/Synth*.bsv', 'fpga/scu_block_scale'], text=True)
    selected += paths.splitlines()
    for name in sorted(set(selected)):
        target = out / 'source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    dense.archive(host_repo, dense.HOST_PIN, out / 'host')
    for label, patch in [('host-integration', ROOT / 'fpga/dense_pipeline/host-integration.patch'),
                         ('producer-observation', ROOT / 'fpga/dense_pipeline/producer-observation.patch'),
                         ('scu-host-companion', HERE / 'host-companion.patch')]:
        full.command(['patch', '--batch', '--fuzz=0', '-p1', '-i', patch],
                     out / 'host', out / label)
    dense.archive(params_repo, dense.PARAMS_PIN, out / 'params')
    profile = json.loads((out / 'source/config/im2p_profiles.json').read_text())
    manifest = {str(p.relative_to(out)): full.digest(p)
                for tree in ('source', 'host', 'params') for p in (out / tree).rglob('*') if p.is_file()}
    (out / 'integration-sha256.json').write_text(json.dumps(manifest, indent=2) + '\n')
    identity = dict(base_core_commit=dense.BASE_CORE_COMMIT, editable_base=EDITABLE_BASE,
                    host_pin=dense.HOST_PIN, params_pin=dense.PARAMS_PIN,
                    fixed_core_patch=full.digest(ROOT / 'fpga/full_replay/fixed-core.patch'),
                    scu_core_patch=full.digest(HERE / 'core.patch'),
                    explicit_overlay=sorted(set(selected)),
                    numerical_revision=profile['numerical_semantics_revision'], abi=5,
                    profile='a8-w8-d16', scale_storage_bytes=4,
                    backend='IM2P_SIM_SCU', machine=platform.machine(),
                    source_sha256=full.digest(out / 'integration-sha256.json'),
                    sim_archive=None, physical_board_access=False)
    (out / 'identity.json').write_text(json.dumps(identity, indent=2) + '\n')


def native(out):
    identity = dense.verify(out)
    full.command(['make', 'gemmini-frontend-real-lib',
                  'GEMMINI_ROOT=' + str(out / 'host'),
                  'GEMMINI_PARAMS_ROOT=' + str(out / 'params/include')],
                 out / 'source', out / 'native-build')
    archive = out / 'source/build/selected/a8-w8-d16/current/libim2p_sim.a'
    identity.update(sim_archive=str(archive), sim_archive_sha256=full.digest(archive),
                    sim_archive_machine=dense.archive_machine(archive))
    (out / 'identity.json').write_text(json.dumps(identity, indent=2) + '\n')
    dense.verify(out, require_archive=True)


def host(out, jobs, fpga=False):
    identity = dense.verify(out, require_archive=True)
    name = 'fpga-host' if fpga else 'host'
    build = out / (name + '-build')
    full.command(['cmake', '-S', out / 'source/fpga/scu_block_scale', '-B', build,
                  '-DHOST_ROOT=' + str(out / 'host'), '-DCORE_ROOT=' + str(out / 'source'),
                  '-DIM2P_SIM_ROOT=' + str(out / 'source'), '-DGEMMINI_SW_PATH=' + str(out / 'params'),
                  '-DSCU_FPGA_UART=' + ('ON' if fpga else 'OFF'), '-DSCU_BUILD_ID=' + identity['source_sha256'],
                  '-DCMAKE_BUILD_TYPE=Release'], out, out / (name + '-configure'))
    full.command(['cmake', '--build', build, '-j', str(jobs)], out, out / (name + '-build-command'))
    (out / (name + '-build-sha256.json')).write_text(json.dumps(
        {str(p.relative_to(out)): full.digest(p) for p in build.rglob('*')
         if p.is_file() and (p.suffix == '.a' or p.name.startswith('scu_') or p.name == 'CMakeCache.txt')},
        indent=2) + '\n')


def bsc(out, asserted):
    dense.verify(out)
    directory = out / ('asserted' if asserted else 'production')
    directory.mkdir()
    for name in ('bsc', 'info', 'rtl', 'primitives'):
        (directory / name).mkdir()
    command = ['bsc', '-u', '-verilog'] + (['-check-assert'] if asserted else [])
    command += ['-p', '+:src/common:src/io:src/array:src/vector:src/accumulator:src/control:src/core:synth',
                '-steps', '4000000', '-steps-warn-interval', '1000000', '-steps-max-intervals', '20',
                '+RTS', '-K256M', '-RTS', '-bdir', directory / 'bsc', '-info-dir', directory / 'info',
                '-vdir', directory / 'rtl', '-g', 'mkScuPipeline', 'synth/ScuPipeline.bsv']
    full.command(command, out / 'source', directory / 'build')
    primitives = Path(shutil.which('bsc')).resolve().parents[1] / 'lib/Verilog'
    for name in ('FIFO2.v', 'BRAM1.v', 'BRAM2.v', 'RegFile.v'):
        shutil.copy2(primitives / name, directory / 'primitives' / name)
    (directory / 'generated-sha256.json').write_text(json.dumps(
        {str(p.relative_to(directory)): full.digest(p) for folder in ('rtl', 'primitives')
         for p in (directory / folder).glob('*.v')}, indent=2) + '\n')


def simulation(out, jobs):
    directory = dense.generated(out)
    source = out / 'source/fpga/scu_block_scale'
    full.command(['verilator', '--cc', '--exe', '--build', '-j', str(jobs), '--timing', '--assert',
                  '--timescale', '1ns/1ps', '--public-flat-rw', '-Wno-fatal',
                  '--top-module', 'scu_uart_shell', '--Mdir', directory / 'obj_dir',
                  '--output-split', '20000', '--output-split-cfuncs', '500',
                  '-MAKEFLAGS', 'OPT_FAST=-O3 OPT_SLOW=-O3', '-CFLAGS', '-O3 -g0 -std=c++20',
                  out / 'source/fpga/full_replay/uart.sv', source / 'scu_uart.sv',
                  directory / 'rtl/mkScuPipeline.v', *sorted((directory / 'primitives').glob('*.v')),
                  source / 'rtl_driver.cpp'], out, directory / 'verilator')


def board_simulation(out, itinerary, jobs, publications):
    directory = dense.generated(out)
    target = out / 'board-top'
    target.mkdir()
    shutil.copy2(itinerary, target / 'itinerary.txt')
    source = out / 'source/fpga/scu_block_scale'
    files = [source / 'arty_scu_top.sv', source / 'scu_uart.sv',
             out / 'source/fpga/full_replay/uart.sv', directory / 'rtl/mkScuPipeline.v',
             *sorted((directory / 'primitives').glob('*.v')),
             full.VIVADO / 'data/verilog/src/glbl.v', source / 'board_top_tb.sv']
    (target / 'source-sha256.json').write_text(json.dumps(
        {str(p): full.digest(p) for p in [*files, target / 'itinerary.txt']}, indent=2) + '\n')
    full.command([full.VIVADO / 'bin/xvlog', '--sv', *files], target, target / 'compile')
    full.command([full.VIVADO / 'bin/xelab', '--timescale', '1ns/1ps', '-L', 'unisims_ver',
                  'work.board_top_tb', 'work.glbl', '-s', 'board_top', '--debug', 'off'], target, target / 'elaborate')
    full.command([full.VIVADO / 'bin/xsim', 'board_top', '--runall',
                  '--testplusarg', f'expected_jobs={jobs}', '--testplusarg', f'expected_publications={publications}',
                  '--testplusarg', f'expected_stripe_acks={publications}'], target, target / 'simulate')
    log = (target / 'simulate.log').read_text()
    if 'SCU_BOARD_TOP_COMPLETE' not in log or 'Fatal:' in log or 'Dynamic assertion failed' in log:
        raise RuntimeError('board-top completion evidence missing')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['refresh-core-patch', 'freeze', 'native', 'host', 'host-fpga', 'verify', 'bsc', 'sim', 'board-sim', 'route'])
    parser.add_argument('out', type=Path, nargs='?')
    parser.add_argument('--host-repo', type=Path, default=ROOT.parent / 'llama.cpp-gemmini')
    parser.add_argument('--params-repo', type=Path, default=ROOT.parent / 'RISC-V-DynDNN-gemmini-include')
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--asserted', action='store_true')
    parser.add_argument('--itinerary', type=Path)
    parser.add_argument('--expected-jobs', type=int)
    parser.add_argument('--expected-publications', type=int, default=0)
    args = parser.parse_args()
    if args.stage == 'refresh-core-patch':
        refresh_core_patch()
    elif args.out is None:
        parser.error('out is required')
    elif args.stage == 'freeze':
        freeze(args.out.resolve(), args.host_repo.resolve(), args.params_repo.resolve())
    elif args.stage == 'native':
        native(args.out.resolve())
    elif args.stage in ('host', 'host-fpga'):
        host(args.out.resolve(), args.jobs, args.stage == 'host-fpga')
    elif args.stage == 'bsc':
        bsc(args.out.resolve(), args.asserted)
    elif args.stage == 'sim':
        simulation(args.out.resolve(), args.jobs)
    elif args.stage == 'board-sim':
        if args.itinerary is None or args.expected_jobs is None:
            parser.error('board-sim requires itinerary and expected-jobs')
        board_simulation(args.out.resolve(), args.itinerary.resolve(), args.expected_jobs, args.expected_publications)
    elif args.stage == 'route':
        out = args.out.resolve()
        dense.generated(out)
        full.command([full.VIVADO / 'bin/vivado', '-mode', 'batch', '-nojournal', '-source',
                      out / 'source/fpga/full_replay/route.tcl', '-tclargs', out, 'scu'], out, out / 'vivado')
    else:
        dense.verify(args.out.resolve())
