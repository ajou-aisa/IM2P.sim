#!/usr/bin/env python3
"""Explicit integration snapshots; reuse the verified FULL freeze/build helpers."""
import argparse
import importlib.util
import io
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('full_build', ROOT / 'fpga/full_replay/build.py')
full = importlib.util.module_from_spec(spec)
spec.loader.exec_module(full)
LOCK = json.loads((HERE / 'deployment-lock.json').read_text())
BASE_CORE_COMMIT = LOCK['base_core']['commit']
HOST_PIN = LOCK['host']['commit']
PARAMS_PIN = LOCK['params']['commit']


def archive(repository, pin, destination):
    destination.mkdir()
    data = subprocess.check_output(['git', '-C', str(repository), 'archive', pin])
    with tarfile.open(fileobj=io.BytesIO(data)) as stream:
        stream.extractall(destination)


def freeze(out, sim_archive=None, host_repo=None, params_repo=None):
    if json.loads((ROOT / 'fpga/full_replay/baseline.json').read_text())['head'] != BASE_CORE_COMMIT:
        raise ValueError('FULL baseline does not match the locked core commit')
    full.freeze(out)
    selected = ['synth/DensePipeline.bsv']
    provider = out / 'source/synth/DensePipeline.bsv'
    shutil.copy2(ROOT / selected[0], provider)
    for file in HERE.rglob('*'):
        if file.is_file() and '__pycache__' not in file.parts:
            name = file.relative_to(ROOT)
            target = out / 'source' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, target)
            selected.append(str(name))
    # Record every explicit overlay; the FULL base remains separately identified.
    source_manifest = {str(p.relative_to(out / 'source')): full.digest(p)
                       for p in (out / 'source').rglob('*') if p.is_file()}
    (out / 'source-sha256.json').write_text(json.dumps(source_manifest, indent=2) + '\n')
    selection = json.loads((out / 'selection.json').read_text())
    selection['explicit_replacements'].extend(selected)
    (out / 'selection.json').write_text(json.dumps(selection, indent=2) + '\n')
    archive(host_repo or ROOT.parent / 'llama.cpp-gemmini', HOST_PIN, out / 'host')
    full.command(['patch', '--batch', '-p1', '-i', out / 'source/fpga/dense_pipeline/host-integration.patch'],
                 out / 'host', out / 'host-patch')
    full.command(['patch', '--batch', '-p1', '-i', out / 'source/fpga/dense_pipeline/producer-observation.patch'],
                 out / 'host', out / 'producer-observation-patch')
    archive(params_repo or ROOT.parent / 'RISC-V-DynDNN-gemmini-include', PARAMS_PIN, out / 'params')
    for name, expected in LOCK['verified_source_sha256'].items():
        if full.digest(out / 'source' / name) != expected:
            raise ValueError('verified numerical/host input changed: ' + name)
    manifest = {str(p.relative_to(out)): full.digest(p) for name in ('source', 'host', 'params')
                for p in (out / name).rglob('*') if p.is_file()}
    (out / 'integration-sha256.json').write_text(json.dumps(manifest, indent=2) + '\n')
    identity = {'host_pin': HOST_PIN, 'params_pin': PARAMS_PIN, 'backend': 'FPGA_UART',
                'base_core_commit': BASE_CORE_COMMIT, 'machine': platform.machine(),
                'sim_archive': str(sim_archive) if sim_archive else None,
                'sim_archive_sha256': full.digest(sim_archive) if sim_archive else None,
                'explicit_integration_files': selected, 'source_sha256': full.digest(out / 'integration-sha256.json'),
                'physical_board_access': False}
    (out / 'identity.json').write_text(json.dumps(identity, indent=2) + '\n')


def verify(out, require_archive=False):
    for name, expected in json.loads((out / 'integration-sha256.json').read_text()).items():
        if full.digest(out / name) != expected:
            raise ValueError('frozen integration changed: ' + name)
    identity = json.loads((out / 'identity.json').read_text())
    if full.digest(out / 'integration-sha256.json') != identity['source_sha256']:
        raise ValueError('frozen integration manifest changed')
    if require_archive and not identity.get('sim_archive'):
        raise ValueError('run native-sim before host; no simulator archive selected')
    if identity.get('sim_archive') and full.digest(Path(identity['sim_archive'])) != identity['sim_archive_sha256']:
        raise ValueError('simulator archive changed')
    return identity


def archive_machine(archive):
    members = subprocess.check_output(['ar', 't', str(archive)], text=True).splitlines()
    member = next(name for name in members if name.endswith('.o'))
    data = subprocess.check_output(['ar', 'p', str(archive), member])
    if data[:4] != b'\x7fELF':
        raise ValueError('simulator archive member is not ELF')
    machine = int.from_bytes(data[18:20], 'little' if data[5] == 1 else 'big')
    expected = {'x86_64': 62, 'aarch64': 183}.get(platform.machine())
    if expected is None or machine != expected:
        raise ValueError(f'simulator archive architecture mismatch: ELF={machine}, host={platform.machine()}')
    return platform.machine()


def native_sim(out, jobs):
    identity = verify(out)
    if identity.get('sim_archive'):
        raise ValueError('simulator archive already selected; use a fresh snapshot')
    native = out / ('native-' + platform.machine())
    native.mkdir()
    full.command(['make', 'verilator-a8-w8-d16', 'BUILD_DIR=' + str(native)],
                 out / 'source', native / 'generate')
    target = native / 'cargo/a8-w8-d16'
    environment = {'IM2P_REPO_ROOT': str(out / 'source'), 'IM2P_BUILD_DIR': str(native),
                   'IM2P_ACTIVATION_BITS': '8', 'IM2P_WEIGHT_BITS': '8', 'IM2P_DIM': '16',
                   'CARGO_TARGET_DIR': str(target), 'CARGO_PROFILE_DEV_DEBUG': '0',
                   'CARGO_PROFILE_TEST_DEBUG': '0', 'CARGO_INCREMENTAL': '0',
                   'CARGO_BUILD_JOBS': str(jobs)}
    full.command(['env', *(f'{key}={value}' for key, value in environment.items()),
                  'cargo', 'build', '--locked', '--manifest-path', 'sim/Cargo.toml', '--lib'],
                 out / 'source', native / 'archive-build')
    selected = target / 'debug/libim2p_sim.a'
    identity.update(sim_archive=str(selected), sim_archive_sha256=full.digest(selected),
                    sim_archive_machine=archive_machine(selected), native_environment=environment)
    tools = {}
    for command in (['bsc', '-v'], ['verilator', '--version'], ['c++', '--version'],
                    ['rustc', '-Vv'], ['cargo', '--version'], ['cmake', '--version']):
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True)
        tools[command[0]] = {'executable': shutil.which(command[0]), 'version': result.stdout.strip()}
    tools['python'] = {'executable': sys.executable, 'version': sys.version}
    generated = {str(p.relative_to(native)): full.digest(p) for folder in ('rtl', 'verilator')
                 for p in (native / folder).rglob('*') if p.is_file()}
    (native / 'generated-sha256.json').write_text(json.dumps(generated, indent=2) + '\n')
    identity['native_toolchain'] = tools
    (out / 'identity.json').write_text(json.dumps(identity, indent=2) + '\n')
    verify(out, require_archive=True)


def host(out, jobs=2):
    identity = verify(out, require_archive=True)
    archive_machine(identity['sim_archive'])
    full.command(['cmake', '-S', out / 'source/fpga/dense_pipeline', '-B', out / 'host-build',
                  '-DCMAKE_BUILD_TYPE=Release', '-DHOST_ROOT=' + str(out / 'host'),
                  '-DCORE_ROOT=' + str(out / 'source'), '-DGEMMINI_SW_PATH=' + str(out / 'params'),
                  '-DSIM_ARCHIVE=' + identity['sim_archive'],
                  '-DFPGA_BUILD_ID=' + identity['source_sha256']], out, out / 'host-configure')
    full.command(['cmake', '--build', out / 'host-build', '--target', 'dense_host_dispatch',
                  'persistent_replay', '-j', str(jobs)], out, out / 'host-build-command')
    artifacts = {str(p.relative_to(out)): full.digest(p) for p in
                 (out / 'host-build/dense_host_dispatch', out / 'host-build/persistent_replay',
                  out / 'host-build/CMakeCache.txt', out / 'identity.json', out / 'integration-sha256.json')}
    (out / 'host-build-sha256.json').write_text(json.dumps(artifacts, indent=2) + '\n')



def generated(out, asserted=False):
    verify(out)
    directory = out / ('asserted' if asserted else 'production')
    for name, expected in json.loads((directory / 'generated-sha256.json').read_text()).items():
        if full.digest(directory / name) != expected:
            raise ValueError('generated RTL/primitive changed: ' + name)
    return directory


def bsc(out, asserted):
    verify(out)
    directory = out / ('asserted' if asserted else 'production')
    directory.mkdir()
    for name in ('bsc', 'info', 'rtl', 'primitives'):
        (directory / name).mkdir()
    command = ['bsc', '-u', '-verilog'] + (['-check-assert'] if asserted else [])
    command += ['-p', '+:src/common:src/io:src/array:src/vector:src/accumulator:src/control:src/core:synth',
                '-steps', '4000000', '-steps-warn-interval', '1000000', '-steps-max-intervals', '20',
                '+RTS', '-K256M', '-RTS', '-bdir', directory / 'bsc', '-info-dir', directory / 'info',
                '-vdir', directory / 'rtl', '-g', 'mkDensePipeline', 'synth/DensePipeline.bsv']
    full.command(command, out / 'source', directory / 'build')
    primitives = Path(shutil.which('bsc')).resolve().parents[1] / 'lib/Verilog'
    for name in ('FIFO2.v', 'BRAM1.v', 'BRAM2.v', 'RegFile.v'):
        shutil.copy2(primitives / name, directory / 'primitives' / name)
    manifest = {str(p.relative_to(directory)): full.digest(p)
                for folder in ('rtl', 'primitives') for p in (directory / folder).glob('*.v')}
    (directory / 'generated-sha256.json').write_text(json.dumps(manifest, indent=2) + '\n')
    verify(out)


def simulation(out, asserted):
    directory = generated(out, asserted)
    source = out / 'source/fpga/dense_pipeline'
    full.command(['verilator', '--cc', '--exe', '--build', '-j', '2', '--timing', '--assert',
                  '--timescale', '1ns/1ps', '--public-flat-rw', '-Wno-fatal',
                  '--top-module', 'dense_uart_shell', '--Mdir', directory / 'obj_dir',
                  '--output-split', '20000', '--output-split-cfuncs', '500',
                  '-MAKEFLAGS', 'OPT_FAST=-O3 OPT_SLOW=-O3',
                  '-CFLAGS', '-O3 -g0 -std=c++20', out / 'source/fpga/full_replay/uart.sv',
                  source / 'dense_uart.sv', directory / 'rtl/mkDensePipeline.v',
                  *sorted((directory / 'primitives').glob('*.v')), source / 'rtl_driver.cpp'],
                 out, directory / 'verilator')
    generated(out, asserted)


def board_simulation(out, itinerary, expected_jobs=None, expected_publications=None,
                     expected_stripe_acks=None, board_output=None):
    directory = generated(out)
    source = out / 'source/fpga/dense_pipeline'
    plan = itinerary.resolve()
    jobs = publications = 0
    response_paths = set()
    for line in plan.read_text().splitlines():
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 2:
            raise ValueError('itinerary requires two fields per row')
        request, response = fields
        if request in ('@clocks', '@reset'):
            if request == '@clocks' and not (0 <= int(response) <= 1_000_000_000):
                raise ValueError('invalid simulation clock delay')
            continue
        request_path, response_path = Path(request), Path(response)
        if not request_path.is_absolute() or not response_path.is_absolute():
            raise ValueError('board simulation itinerary paths must be absolute')
        if response_path.exists() or response_path in response_paths:
            raise ValueError('refusing response overwrite: ' + response)
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_paths.add(response_path)
        packet = request_path.read_bytes()
        if len(packet) >= 6:
            jobs += packet[5] in (1, 4)
            publications += packet[5] == 5
    counts = (jobs if expected_jobs is None else expected_jobs,
              publications if expected_publications is None else expected_publications,
              publications if expected_stripe_acks is None else expected_stripe_acks)
    if min(counts) < 0:
        raise ValueError('negative expected completion count')
    simulation = board_output.resolve() if board_output else out / 'board-top'
    simulation.mkdir()
    shutil.copy2(plan, simulation / 'itinerary.txt')
    files = [source / 'arty_dense_top.sv', source / 'dense_uart.sv',
             out / 'source/fpga/full_replay/uart.sv', directory / 'rtl/mkDensePipeline.v',
             *sorted((directory / 'primitives').glob('*.v')),
             full.VIVADO / 'data/verilog/src/glbl.v', source / 'board_top_tb.sv']
    (simulation / 'source-sha256.json').write_text(json.dumps(
        {str(p): full.digest(p) for p in [*files, simulation / 'itinerary.txt']}, indent=2) + '\n')
    full.command([full.VIVADO / 'bin/xvlog', '--sv', *files], simulation, simulation / 'compile')
    full.command([full.VIVADO / 'bin/xelab', '--timescale', '1ns/1ps', '-L', 'unisims_ver',
                  'work.board_top_tb', 'work.glbl', '-s', 'board_top', '--debug', 'off'],
                 simulation, simulation / 'elaborate')
    plusargs = []
    for key, count in zip(('expected_jobs', 'expected_publications', 'expected_stripe_acks'), counts):
        plusargs += ['--testplusarg', f'{key}={count}']
    full.command([full.VIVADO / 'bin/xsim', 'board_top', '--runall', *plusargs],
                 simulation, simulation / 'simulate')
    log = (simulation / 'simulate.log').read_text()
    if ('DENSE_BOARD_TOP_COMPLETE' not in log or 'Fatal:' in log or
            'Dynamic assertion failed' in log or 'Error:' in log):
        raise RuntimeError('board-top did not complete; exit zero alone is insufficient')
    (simulation / 'response-sha256.json').write_text(json.dumps(
        {str(p): full.digest(p) for p in sorted(response_paths)}, indent=2) + '\n')
    generated(out)


def route(out):
    generated(out)
    full.command([full.VIVADO / 'bin/vivado', '-mode', 'batch', '-nojournal', '-source',
                  out / 'source/fpga/full_replay/route.tcl', '-tclargs', out, 'dense'], out, out / 'vivado')
    generated(out)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['freeze', 'native-sim', 'host', 'verify', 'bsc', 'sim', 'board-sim', 'route'])
    parser.add_argument('out', type=Path)
    parser.add_argument('--sim-archive', type=Path)
    parser.add_argument('--host-repo', type=Path)
    parser.add_argument('--params-repo', type=Path)
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--asserted', action='store_true')
    parser.add_argument('--itinerary', type=Path)
    parser.add_argument('--board-output', type=Path)
    parser.add_argument('--expected-jobs', type=int)
    parser.add_argument('--expected-publications', type=int)
    parser.add_argument('--expected-stripe-acks', type=int)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    out = args.out.resolve()
    if args.stage == 'freeze':
        freeze(out, args.sim_archive.resolve() if args.sim_archive else None,
               args.host_repo.resolve() if args.host_repo else None,
               args.params_repo.resolve() if args.params_repo else None)
    elif args.stage == 'native-sim':
        native_sim(out, args.jobs)
    elif args.stage == 'host':
        host(out, args.jobs)
    elif args.stage == 'bsc':
        bsc(out, args.asserted)
    elif args.stage == 'sim':
        simulation(out, args.asserted)
    elif args.stage == 'board-sim':
        if args.itinerary is None:
            parser.error('--itinerary required')
        board_simulation(out, args.itinerary, args.expected_jobs,
                         args.expected_publications, args.expected_stripe_acks, args.board_output)
    elif args.stage == 'route':
        route(out)
    else:
        verify(out)
