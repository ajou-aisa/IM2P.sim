"""Bind the ordinary ggml CPU kernels to an actual native Ninja build."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import platform
import shlex
import subprocess
from typing import Final

from scripts.gemmini_replay_contract import contract_digest
from scripts.real_lib_manifest import sha256
from sim.cycle.npu_trace_schema import Record, object_value, require, text, unique_pairs

INSTRUMENTATION: Final = {'CYCLE_SIM', 'LOG_CYCLE', 'CYCLE_LOG', 'LOG_DEBUG', 'CYCLE_DETAIL'}
INSTRUMENTATION_HEADERS: Final = {'cycle_sim_context.h', 'cpu_log_context.hpp', 'semantic.h',
    'semantic.hpp', 'log.h', 'log.hpp', 'cycle_reader.h', 'cycle_reader.hpp'}
CMAKE_OPTIONS: Final = {'CMAKE_BUILD_TYPE', 'CMAKE_C_COMPILER', 'CMAKE_CXX_COMPILER',
    'CMAKE_OSX_ARCHITECTURES', 'CMAKE_OSX_DEPLOYMENT_TARGET', 'CMAKE_OSX_SYSROOT',
    'CMAKE_PREFIX_PATH', 'CMAKE_TOOLCHAIN_FILE', 'CMAKE_SYSTEM_PROCESSOR', 'CMAKE_SYSTEM_NAME',
    'CMAKE_SYSROOT', 'CMAKE_C_COMPILER_TARGET', 'CMAKE_CXX_COMPILER_TARGET',
    'CMAKE_BUILD_RPATH', 'CMAKE_INSTALL_RPATH', 'CMAKE_SKIP_RPATH', 'CMAKE_SKIP_BUILD_RPATH',
    'CMAKE_BUILD_WITH_INSTALL_RPATH', 'CMAKE_INSTALL_RPATH_USE_LINK_PATH', 'CMAKE_POSITION_INDEPENDENT_CODE',
    'BUILD_SHARED_LIBS', 'GEMMINI_SW_PATH', 'OpenMP_ROOT'}


def cache_values(build: Path) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for line in (build / 'CMakeCache.txt').read_text().splitlines():
        if line.startswith(('#', '//')) or '=' not in line or ':' not in line.split('=', 1)[0]:
            continue
        name_type, value = line.split('=', 1)
        name, kind = name_type.split(':', 1)
        result[name] = (kind, value)
    return result


def fresh_configuration(reference: Path, destination: Path) -> list[str]:
    require(not destination.exists(), 'fresh native-build destination must not exist')
    values = cache_values(reference)
    require('CMAKE_HOME_DIRECTORY' in values, 'reference source root missing')
    require(values.get('CMAKE_SYSTEM_NAME', ('', platform.system()))[1] == platform.system(),
            'collection requires a native build configuration')
    source = Path(values['CMAKE_HOME_DIRECTORY'][1]).resolve(strict=True)
    command = ['cmake', '-S', str(source), '-B', str(destination.resolve()), '-G', 'Ninja']
    for name, (kind, value) in sorted(values.items()):
        selected = name in CMAKE_OPTIONS or name.startswith(('GGML_', 'LLAMA_', 'IM2P_', 'LOG_', 'CYCLE_',
            'CMAKE_C_FLAGS', 'CMAKE_CXX_FLAGS', 'CMAKE_EXE_LINKER_FLAGS', 'CMAKE_SHARED_LINKER_FLAGS',
            'CMAKE_MODULE_LINKER_FLAGS', 'CMAKE_STATIC_LINKER_FLAGS', 'CMAKE_INTERPROCEDURAL_OPTIMIZATION', 'OpenMP_'))
        if selected and kind in ('BOOL', 'STRING', 'PATH', 'FILEPATH'):
            command.append(f'-D{name}:{kind}={value}')
        elif selected and kind == 'UNINITIALIZED':
            command.append(f'-D{name}={value}')
    return command + ['-DCMAKE_EXPORT_COMPILE_COMMANDS=ON', '-DCMAKE_C_COMPILER_LAUNCHER=',
                      '-DCMAKE_CXX_COMPILER_LAUNCHER=']


@dataclass(frozen=True, slots=True)
class TranslationUnit:
    source: Path
    directory: Path
    arguments: tuple[str, ...]
    object_file: str


def translation_units(build: Path, sources: frozenset[Path] | None = None) -> list[TranslationUnit]:
    rows = json.loads((build / 'compile_commands.json').read_text(), object_pairs_hook=unique_pairs)
    require(isinstance(rows, list), 'compile database must be an array')
    result: list[TranslationUnit] = []
    for raw in rows:
        row = object_value(raw)
        source = Path(text(row, 'file')).resolve(strict=True)
        args = shlex.split(text(row, 'command'))
        # Only kernels supplying ordinary ggml CPU costs, not replaced Gemmini CPU GEMMs.
        if sources is not None and source not in sources:
            continue
        if sources is None and '/ggml-cpu/' not in source.as_posix() and source.name not in ('ggml.c', 'ggml-quants.c'):
            continue
        require('-o' in args and '-c' in args, 'unsupported compile command')
        if sources is not None and (Path(text(row, 'directory')) / args[args.index('-o') + 1]).resolve() not in sources:
            continue
        result.append(TranslationUnit(source, Path(text(row, 'directory')), tuple(args), args[args.index('-o') + 1]))
    require(bool(result), 'no ordinary CPU kernel translation units')
    require(len({unit.source for unit in result}) == len(result), 'duplicate CPU kernel variants require explicit support')
    return result


def normalized_flags(unit: TranslationUnit) -> list[str]:
    """Include resolution is bound by Ninja's real dependency bytes, not search paths."""
    flags: list[str] = []
    args = iter(unit.arguments[1:])
    for argument in args:
        if argument in ('-o', '-c', '-I', '-isystem', '-iquote'):
            require(next(args, None) is not None, 'truncated compile option')
            continue
        if argument.startswith(('-I', '-isystem', '-iquote')):
            continue
        if argument.startswith('-D') and argument[2:].split('=', 1)[0] in INSTRUMENTATION:
            continue
        flags.append(argument)
    return flags


def compile_input_snapshot(build: Path, *, prebuild: bool = False, sources: frozenset[Path] | None = None) -> Record:
    result: Record = {}
    for unit in translation_units(build, sources):
        if prebuild:
            args: list[str] = [unit.arguments[0]]
            values = iter(unit.arguments[1:])
            for arg in values:
                if arg in ('-o', '-MF', '-MT', '-MQ'):
                    require(next(values, None) is not None, 'truncated compiler output flag')
                elif arg not in ('-c', '-MD', '-MMD', '-MP'):
                    args.append(arg)
            output = subprocess.check_output([*args, '-M', '-MT', 'im2p_collection'], cwd=unit.directory, text=True)
            require(output.startswith('im2p_collection:'), 'unexpected compiler dependency output')
            paths = shlex.split(output.replace('\\\n', ' ').split(':', 1)[1])
        else:
            output = subprocess.check_output(['ninja', '-C', str(build), '-t', 'deps', unit.object_file], text=True)
            lines = output.splitlines()
            require(bool(lines) and '(VALID)' in lines[0], 'fresh CPU object dependency binding missing/stale')
            paths = [line.strip() for line in lines[1:] if line.startswith('    ')]
        for raw in paths:
            path = Path(raw)
            path = (path if path.is_absolute() else unit.directory / path).resolve(strict=True)
            result[str(path)] = sha256(path)
        compiler = Path(unit.arguments[0]).resolve(strict=True)
        result[str(compiler)] = sha256(compiler)
    result[str((build / 'compile_commands.json').resolve())] = sha256(build / 'compile_commands.json')
    return result


def cpu_kernel_contract(build: Path) -> Record:
    """Ordinary kernel compatibility; fresh compilation binding is recorded separately."""
    source_root: Path | None = None
    for name, (_, value) in cache_values(build).items():
        if name == 'CMAKE_HOME_DIRECTORY':
            source_root = Path(value).resolve(strict=True)
    require(source_root is not None, 'missing CMake source root')
    if source_root is None:
        raise RuntimeError('missing source root')
    units = translation_units(build)
    dependencies: Record = {}
    compiler_versions: Record = {}
    unit_records: Record = {}
    for unit in units:
        compiler = Path(unit.arguments[0]).resolve(strict=True)
        compiler_key = str(compiler)
        if compiler_key not in compiler_versions:
            compiler_versions[compiler_key] = {
                'sha256': sha256(compiler),
                'version': subprocess.check_output([str(compiler), '--version'], text=True).strip(),
            }
        unit_records[unit.source.relative_to(source_root).as_posix()] = {
            'compiler': compiler_key, 'flags': list(normalized_flags(unit)),
            'source_sha256': sha256(unit.source),
        }
    for raw, digest in compile_input_snapshot(build).items():
        dependency = Path(raw)
        if dependency.name == 'compile_commands.json' or raw in compiler_versions:
            continue
        if '/ggml-gemmini-utils/' in raw and dependency.name in INSTRUMENTATION_HEADERS:
            continue
        key = (dependency.relative_to(source_root).as_posix() if dependency.is_relative_to(source_root)
               else '$BUILD/' + dependency.relative_to(build).as_posix() if dependency.is_relative_to(build) else raw)
        dependencies[key] = digest
    result: Record = {
        'schema': 'im2p-ordinary-cpu-kernel-contract', 'version': 1,
        'scope': 'ordinary-ggml-cpu-kernels; excludes replaced Gemmini CPU GEMM costs',
        'host': list(platform.uname()), 'units': unit_records,
        'dependencies': dependencies, 'compilers': compiler_versions,
        'excluded_instrumentation_definitions': list(sorted(INSTRUMENTATION)),
        'excluded_instrumentation_headers': list(sorted(INSTRUMENTATION_HEADERS)),
    }
    result['sha256'] = contract_digest(result)
    return result


def project_artifacts(build: Path, executable: str = 'llama-cli') -> Record:
    """Bind every project shared library beside the executable, including backend plugins."""
    result: Record = {}
    for path in sorted((build / 'bin').iterdir()):
        if path.is_file() and (path.name == executable or '.dylib' in path.name or '.so' in path.name):
            result[path.name] = sha256(path)
    require(executable in result, 'selected collection executable missing')
    return result


def runtime_dependencies(build: Path, executable: str = 'llama-cli') -> Record:
    result: Record = {}
    native_build = build.resolve(strict=True)
    executable_dir = native_build / 'bin'
    pending = [executable_dir / name for name in project_artifacts(build, executable)]
    visited: set[Path] = set()
    while pending:
        binary = pending.pop().resolve(strict=True)
        if binary in visited:
            continue
        visited.add(binary)
        if platform.system() == 'Darwin':
            names = [line.strip().split(' (', 1)[0] for line in
                     subprocess.check_output(['otool', '-L', str(binary)], text=True).splitlines()[1:]]
            detail = subprocess.check_output(['otool', '-l', str(binary)], text=True).splitlines()
            rpaths: list[str] = []
            in_rpath = False
            for line in detail:
                stripped = line.strip()
                if stripped.startswith('cmd '):
                    in_rpath = stripped == 'cmd LC_RPATH'
                elif in_rpath and stripped.startswith('path '):
                    rpaths.append(stripped[5:].split(' (offset ', 1)[0])
            resolved: list[Path] = []
            for name in names:
                expand = lambda value: value.replace('@loader_path', str(binary.parent)).replace('@executable_path', str(executable_dir))
                if name.startswith('@rpath/'):
                    candidates = {Path(expand(root)) / name[7:] for root in [*rpaths, str(executable_dir)]}
                    matches = {path.resolve() for path in candidates if path.is_file()}
                    require(len(matches) == 1, 'ambiguous/missing runtime library: ' + name)
                    resolved.append(matches.pop())
                else:
                    resolved.append(Path(expand(name)))
        else:
            require(platform.system() == 'Linux', 'only native macOS/Linux collection is supported')
            output = subprocess.check_output(['ldd', str(binary)], text=True)
            require('not found' not in output, 'unresolved runtime library')
            resolved = []
            for line in output.splitlines():
                path = line.split('=>', 1)[-1].strip().split(' (', 1)[0]
                if path.startswith('/'):
                    resolved.append(Path(path))
        for dependency in resolved:
            system = str(dependency).startswith(('/usr/lib/', '/System/Library/', '/lib/', '/lib64/'))
            if not dependency.is_file():
                require(platform.system() == 'Darwin' and system, 'runtime dependency has no file: ' + str(dependency))
                result[str(dependency)] = {'kind': 'SYSTEM_SHARED_CACHE', 'os': list(platform.uname())}
                continue
            dependency = dependency.resolve(strict=True)
            require(not dependency.name.startswith(('libggml', 'libllama', 'libcommon', 'libim2p')) or
                    dependency.is_relative_to(native_build), 'project library resolved outside fresh build: ' + str(dependency))
            kind = 'PROJECT' if dependency.is_relative_to(native_build) else 'SYSTEM' if system else 'EXTERNAL'
            result[str(dependency)] = {'kind': kind, 'sha256': sha256(dependency)}
            if not system:
                pending.append(dependency)
    return result
