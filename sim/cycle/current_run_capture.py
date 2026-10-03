from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Final

from scripts.real_lib_manifest import sha256, verify_manifest
from sim.cycle.certificate_contract import array_value, digest_value, read_document
from sim.cycle.collection_build import (
    cache_values,
    runtime_dependencies,
)
from sim.cycle.npu_trace_schema import Record, object_value, require, text, unique_pairs

ROOT: Final = Path(__file__).resolve().parents[2]
WORKSPACE: Final = ROOT.parent
EXECUTABLE: Final = 'test-gemmini-rmd-im2p-provider'
PRODUCER: Final = 'llama.cpp-gemmini/tests/test-gemmini-rmd-im2p-provider.cpp'
PRODUCER_SHA256: Final = 'd67a18d53c5c0e3403d80f1158a4be1cb075f8cd1f785b28b1327a09534cbda2'


def reference(value: Record) -> Path:
    path = Path(text(value, 'path'))
    require(set(value) == {'path', 'sha256'} and path.is_absolute() and
            path.resolve(strict=True) == path and
            sha256(path) == digest_value(value.get('sha256'), 'artifact hash'),
            'current producer artifact changed: ' + str(path))
    return path


def dependency_paths(build: Path) -> set[Path]:
    result: set[Path] = set()
    objects = {path for path in build_artifacts(build) if path.suffix == '.o'}
    units: dict[Path, Record] = {}
    for value in array_value(json.loads((build / 'compile_commands.json').read_text(), object_pairs_hook=unique_pairs), 'compile commands'):
        unit = object_value(value)
        args = shlex.split(text(unit, 'command'))
        require('-o' in args and '-c' in args, 'native compile source/output missing')
        obj = (Path(text(unit, 'directory')) / args[args.index('-o') + 1]).resolve()
        if obj in objects:
            require(obj not in units, 'duplicate selected native object')
            require((Path(text(unit, 'directory')) / args[args.index('-c') + 1]).resolve() == Path(text(unit, 'file')).resolve(),
                    'selected compiler source differs from compile database')
            units[obj] = unit
    require(bool(objects) and set(units) == objects, 'selected native compile units incomplete')
    for obj, unit in units.items():
        depfile = Path(str(obj) + '.d')
        content = depfile.read_text().replace('\\\n', ' ')
        require(':' in content, 'malformed compiler dependency file')
        target, inputs = content.split(':', 1)
        targets = shlex.split(target)
        require(len(targets) == 1 and (build / targets[0]).resolve() == obj,
                'selected compiler depfile names a different object')
        paths = {(build / name).resolve(strict=True) for name in shlex.split(inputs)}
        require(Path(text(unit, 'file')).resolve(strict=True) in paths,
                'selected compiler depfile omits actual translation unit')
        result.update(paths)
    require(WORKSPACE / PRODUCER in result, 'current native producer was not compiled')
    return result


def build_artifacts(build: Path) -> set[Path]:
    links: dict[Path, tuple[Path, list[str]]] = {}
    for path in build.rglob('link.txt'):
        args = shlex.split(path.read_text().splitlines()[0])
        if '-o' in args:
            output = args[args.index('-o') + 1]
        elif len(args) > 2 and Path(args[0]).name in ('ar', 'llvm-ar'):
            output = args[2]
        else:
            continue
        target = (path.parents[2] / output).resolve()
        require(target not in links, 'duplicate native link output')
        links[target] = (path, args)
    paths: set[Path] = set()
    pending = [build / 'bin' / EXECUTABLE]
    while pending:
        target = pending.pop()
        require(target.is_file(), 'selected native link artifact missing: ' + str(target))
        target = target.resolve(strict=True)
        if target in paths:
            continue
        paths.add(target)
        if target.suffix == '.o':
            paths.add(Path(str(target) + '.d').resolve(strict=True))
        elif target.is_relative_to(build):
            require(target in links, 'selected native artifact lacks a link command')
            link, args = links[target]
            paths.update((link, (link.parent / 'flags.make').resolve(strict=True)))
            for word in args:
                if not word.startswith(('-', '@')) and (word.endswith(('.o', '.a', '.dylib')) or '.so' in Path(word).suffixes):
                    dependency = (link.parents[2] / word).resolve()
                    if dependency != target:
                        pending.append(dependency)
    paths.update(build / name for name in ('CMakeCache.txt', 'compile_commands.json'))
    return paths


def check_hashes(record: Record, paths: set[Path], label: str) -> None:
    require(set(record) == {str(path) for path in paths}, label + ' closure incomplete')
    for path in paths:
        require(record[str(path)] == sha256(path), label + ' changed: ' + str(path))


def capture_root(row: Record) -> Path:
    binary = reference(object_value(row.get('executable')))
    fixture_root = Path(text(row, 'fixture_root'))
    receipt_path = reference(object_value(row.get('capture_receipt')))
    receipt = read_document(receipt_path)
    context = read_document(reference(object_value(row.get('capture_context'))))
    before = read_document(reference(object_value(context.get('pre_capture_context'))))
    require(before.get('binary_sha256') == sha256(binary), 'native pre-capture binary changed')
    require(before.get('build_artifacts') == row.get('build_artifacts') and
            before.get('runtime_dependencies') == row.get('runtime_dependencies'),
            'native binaries changed across capture')
    require(fixture_root.is_absolute() and fixture_root.resolve(strict=True) == fixture_root and
            receipt.get('command') == before.get('command') == [str(binary), '--case', 'production-fixtures'] and
            context.get('capture_receipt_sha256') == sha256(receipt_path) and
            before.get('cwd') == context.get('cwd') == str(ROOT) and
            before.get('environment') == context.get('environment') and
            object_value(context.get('environment')).get('GEMMINI_LOG_DIR') == str(fixture_root),
            'native capture route differs')
    return fixture_root


def verify_numerical_library(manifest_path: Path, profile: str) -> None:
    bits, dim = profile[1], profile.split('-d')[1].split('-')[0]
    valid, reason = verify_manifest(manifest_path, expected_identity=f'a{bits}-w{bits}-d{dim}',
                                    expected_implementation='GEMMINI_HP1', expected_block_size=32)
    require(valid, 'current numerical library binding failed: ' + reason)
    manifest = read_document(manifest_path)
    command = [sys.executable, '-B', str(ROOT / 'scripts/real_matrix_fingerprint.py'),
               '--bits', bits, '--weight-bits', bits, '--dim', dim, '--block-size', '32',
               '--implementation', 'GEMMINI_HP1', '--gemmini-root', str(WORKSPACE / 'llama.cpp-gemmini'),
               '--params-root', str(WORKSPACE / 'RISC-V-DynDNN-gemmini-include/include')]
    identity = object_value(manifest['identity'])
    for key in ('platform', 'platform_release', 'arch'):
        name = 'release' if key == 'platform_release' else key
        command += ['--config', f'host.{name}={text(identity, key)}']
    for key, value in sorted(object_value(manifest['toolchains']).items()):
        command += ['--config', f'tool.{key}={json.dumps(value, sort_keys=True)}']
    for key, value in sorted(object_value(manifest['build_config']).items()):
        command += ['--config', f'build.{key}={value}']
    fingerprint = subprocess.check_output(command, text=True, timeout=60).strip()
    require(fingerprint == manifest.get('fingerprint'), 'current numerical library sources changed')


def validate_producer(row: Record) -> Path:
    profile = text(row, 'profile')
    bits, dim = profile[1], profile.split('-d')[1].split('-')[0]
    build = Path(text(row, 'build_root'))
    require(build.is_absolute() and build.resolve(strict=True) == build, 'native build path invalid')
    binary = reference(object_value(row.get('executable')))
    require(binary == build / 'bin' / EXECUTABLE and bool(binary.stat().st_mode & 0o111),
            'current producer executable route differs')
    values = cache_values(build)
    expected = {'GGML_GEMMINI': 'ON', 'LLAMA_BUILD_TESTS': 'ON',
                'GGML_GEMMINI_EXECUTION_BACKEND': 'IM2P_SIM', 'IM2P_SIM_IMPLEMENTATION': 'GEMMINI_HP1',
                'GGML_GEMMINI_ACTIVATION_BITS': bits, 'GGML_GEMMINI_WEIGHT_BITS': bits,
                'GGML_GEMMINI_DIM': dim, 'GGML_GEMMINI_BLOCK_SIZE': '32',
                'GGML_GEMMINI_OPTION': 'WS', 'GGML_GEMMINI_ENABLE_RMD': 'ON', 'CYCLE_SIM': '0',
                'CMAKE_HOME_DIRECTORY': str(WORKSPACE / 'llama.cpp-gemmini')}
    require(all(values.get(key, ('', ''))[1] == value for key, value in expected.items()),
            'current native build profile/role differs')
    commands = array_value(json.loads((build / 'compile_commands.json').read_text(), object_pairs_hook=unique_pairs), 'compile commands')
    producer_units = [object_value(value) for value in commands if
                      object_value(value).get('file') == str(WORKSPACE / PRODUCER)]
    require(len(producer_units) == 1, 'current producer compile command missing/duplicate')
    arguments = shlex.split(text(producer_units[0], 'command'))
    require({'-DCYCLE_SIM=0', '-DGGML_GEMMINI_EXECUTION_BACKEND_IM2P_SIM=1',
             f'-DGGML_GEMMINI_ACTIVATION_BITS={bits}', f'-DGGML_GEMMINI_WEIGHT_BITS={bits}',
             '-DGGML_GEMMINI_BLOCK_SIZE=32'} <= set(arguments),
            'current producer compiled role differs')
    artifacts = build_artifacts(build)
    producer_object = (Path(text(producer_units[0], 'directory')) /
                       arguments[arguments.index('-o') + 1]).resolve(strict=True)
    require(producer_object in artifacts, 'native producer object not linked into selected executable')
    inputs = dependency_paths(build)
    require(row.get('compile_inputs_before') == row.get('compile_inputs'),
            'compiler inputs changed across compilation/capture')
    check_hashes(object_value(row.get('compile_inputs')), inputs, 'compiled dependency')
    precompile = read_document(reference(object_value(row.get('precompile_snapshot'))))
    check_hashes(object_value(precompile.get('inputs')), inputs, 'native precompile snapshot')
    dependency_commands: list[list[str]] = []
    for value in commands:
        unit = object_value(value)
        args = shlex.split(text(unit, 'command'))
        require('-o' in args and args.index('-o') + 1 < len(args), 'native compile output missing')
        if (Path(text(unit, 'directory')) / args[args.index('-o') + 1]).resolve() not in artifacts:
            continue
        normalized = [args[0]]
        flags = iter(args[1:])
        for flag in flags:
            if flag in ('-o', '-MF', '-MT', '-MQ'):
                require(next(flags, None) is not None, 'truncated native output flag')
            elif flag not in ('-c', '-MD', '-MMD', '-MP'):
                normalized.append(flag)
        dependency_commands.append(normalized + ['-M', '-MT', 'im2p_collection'])
    recorded_commands = array_value(precompile.get('compiler_dependency_commands'), 'dependency commands')
    require(sorted(json.dumps(command) for command in recorded_commands) ==
            sorted(json.dumps(command) for command in dependency_commands),
            'native compilation commands differ from precompile snapshot')
    sources = {path for path in inputs if path.is_relative_to(WORKSPACE) and not path.is_relative_to(build)}
    before, after = object_value(row.get('source_before')), object_value(row.get('source_after'))
    require(before == after, 'native sources changed across compilation/capture')
    check_hashes(before, sources, 'current producer source')
    require(before.get(str(WORKSPACE / PRODUCER)) == PRODUCER_SHA256,
            'unreviewed native producer source')
    check_hashes(object_value(row.get('build_artifacts')), artifacts, 'native build artifact')
    require(row.get('runtime_dependencies') == runtime_dependencies(build, EXECUTABLE),
            'native runtime dependencies changed')
    require(all(Path(path) in artifacts for path, detail in object_value(row.get('runtime_dependencies')).items()
                if object_value(detail).get('kind') == 'PROJECT'), 'native runtime library not in selected link graph')
    real_lib = reference(object_value(row.get('real_lib_manifest')))
    verify_numerical_library(real_lib, profile)
    cxx = object_value(object_value(read_document(real_lib)['toolchains'])['cxx'])
    require(bool(dependency_commands) and all(sha256(Path(command[0])) == cxx.get('executable_sha256')
                                             for command in dependency_commands),
            'native compiler differs from earlier numerical toolchain identity')
    archives = {path for path in artifacts if path.suffix == '.a' and not path.is_relative_to(build)}
    require(archives == {real_lib.parent / 'libim2p_sim.a'},
            'native producer did not link the verified numerical library')
    build_receipt = read_document(reference(object_value(row.get('build_receipt'))))
    capture_receipt = read_document(reference(object_value(row.get('capture_receipt'))))
    for receipt in (build_receipt, capture_receipt):
        require(receipt.get('reason') == 'EXIT' and type(receipt.get('returncode')) is int and
                receipt.get('returncode') == 0 and type(receipt.get('child_returncode')) is int and
                receipt.get('child_returncode') == 0 and
                receipt.get('reaped') is True, 'native build/capture unsuccessful')
    build_command = array_value(build_receipt.get('command'), 'build command')
    require(build_command[:3] == ['cmake', '--build', str(build)] and
            '--target' in build_command and EXECUTABLE in build_command,
            'native receipt did not build producer')
    return capture_root(row)
