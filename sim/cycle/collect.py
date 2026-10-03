#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# How to run: python3 -B -m sim.cycle.collect --help
"""Run one source collection and bind its model, native build and output bytes."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Final

from scripts.real_lib_manifest import sha256
from sim.cycle.collection_build import (cache_values, compile_input_snapshot, cpu_kernel_contract,
    fresh_configuration, project_artifacts, runtime_dependencies)
from sim.cycle.npu_trace_schema import Record, object_value, require, text, unique_pairs
from sim.cycle.reconstruct_graph import read_manifest

ARTIFACTS: Final = {'cycle_log': 'cycle-log.jsonl', 'semantic_graph': 'semantic-graph.jsonl',
                    'npu_trace': 'npu-cycle-trace.jsonl'}


ALIASES: Final = {'-f': '--file', '-p': '--prompt', '-n': '--predict', '-t': '--threads',
    '-tb': '--threads-batch', '-b': '--batch-size', '-ub': '--ubatch-size', '-c': '--ctx-size',
    '-s': '--seed', '-ngl': '--gpu-layers', '--n-gpu-layers': '--gpu-layers', '-dev': '--device'}
VALUE_OPTIONS: Final = {'--file', '--prompt', '--predict', '--threads', '--threads-batch',
    '--batch-size', '--ubatch-size', '--ctx-size', '--seed', '--temp', '--top-k', '--top-p',
    '--min-p', '--repeat-penalty', '--repeat-last-n', '--gpu-layers', '--device'}
FLAGS: Final = {'--no-warmup', '--no-conversation', '--no-display-prompt', '--ignore-eos',
    '--simple-io', '--no-perf', '--no-mmap', '--no-context-shift', '--no-kv-offload'}


@dataclass(frozen=True, slots=True)
class CollectionArguments:
    execution: tuple[str, ...]
    normalized: tuple[str, ...]
    input_files: Record


def input_arguments(arguments: list[str]) -> CollectionArguments:
    """Accept only bound, single-use workload options; resolve files before changing cwd."""
    normalized: list[str] = []
    execution: list[str] = []
    inputs: Record = {}
    seen: set[str] = set()
    values = iter(arguments)
    for argument in values:
        raw, equals, inline = argument.partition('=')
        option = ALIASES.get(raw, raw)
        require(option in VALUE_OPTIONS | FLAGS and option not in seen, 'unsupported or duplicate collection argument: ' + raw)
        seen.add(option)
        execution.append(option)
        normalized.append(option)
        if option in FLAGS:
            require(not equals, 'flag cannot have a value: ' + option)
            continue
        value = inline if equals else next(values, None)
        require(value is not None, 'missing argument value: ' + option)
        value = value or ''
        if option == '--file':
            path = Path(value).resolve(strict=True)
            require(path.is_file(), 'prompt must be a regular file')
            digest = sha256(path)
            execution.append(str(path))
            normalized.append('sha256:' + digest)
            inputs[str(path)] = digest
        else:
            execution.append(value)
            normalized.append(value)
    require(not {'--file', '--prompt'} <= seen, 'choose one prompt source')
    return CollectionArguments(tuple(execution), tuple(normalized), inputs)


def collection_environment(output: Path) -> dict[str, str]:
    prefixes = ('DYLD_', 'LD_', 'LLAMA_ARG_', 'HF_', 'GGML_', 'OMP_', 'GOMP_', 'KMP_',
                'OPENBLAS_', 'VECLIB_', 'EXSIA_')
    require(not any(key.startswith(prefixes) or (key.startswith('GEMMINI_') and key != 'GEMMINI_LOG_DIR')
                    for key in os.environ), 'unbound model, loader, backend, or CPU tuning environment override')
    return {**os.environ, 'GEMMINI_LOG_DIR': str(output.resolve() / 'output/log')}


def validate_build_role(build: Path, role: str, *, configured: bool = False) -> None:
    values = cache_values(build)
    cycles = {'FULL_CPU': '0', 'POTAL_COLLECTION': '1'}
    require(role in cycles, 'unsupported source role')
    cycle = cycles[role]
    require(values.get('CYCLE_SIM', ('', ''))[1] == cycle, 'source role/build CYCLE_SIM mismatch')
    require(values.get('LOG_CYCLE', ('', ''))[1] == '1' and
            values.get('GGML_CPU_CYCLE_LOG', ('', ''))[1].upper() in ('1', 'ON', 'TRUE', 'YES'),
            'both collection roles require ordinary CycleLog and CPU instrumentation')
    require(role != 'FULL_CPU' or (values.get('GGML_GEMMINI_OPTION', ('', ''))[1] == 'CPU' and
            values.get('GGML_GEMMINI_EXECUTION_BACKEND', ('', ''))[1] == 'HARDWARE'),
            'FullCPU collection requires existing CPU/HARDWARE numerical route')
    if not configured:
        return
    rows = json.loads((build / 'compile_commands.json').read_text(), object_pairs_hook=unique_pairs)
    require(isinstance(rows, list), 'compile database must be an array')
    found: set[str] = set()
    for raw in rows:
        row = object_value(raw)
        path = text(row, 'file')
        semantic = path.endswith('/ggml-gemmini-utils/src/semantic.cpp')
        cpu = path.endswith('/ggml-cpu/ggml-cpu.c')
        if not semantic and not cpu:
            continue
        args = shlex.split(text(row, 'command'))
        expected = {'LOG_CYCLE': '1', 'CYCLE_SIM': cycle} if semantic else {'CYCLE_LOG': '1'}
        if semantic and role == 'FULL_CPU': expected['GEMMINI_SEMANTIC_CPU_ONLY_BUILD'] = '1'
        for name, value in expected.items():
            require([arg for arg in args if arg.startswith('-D' + name + '=')] == ['-D' + name + '=' + value],
                    'fresh compiled role definition missing/ambiguous: ' + name)
        found.add('semantic' if semantic else 'cpu')
    require(found == {'semantic', 'cpu'}, 'fresh role proof lacks semantic/CPU translation units')


def validate_sidecar_role(path: Path, role: str) -> None:
    manifest = read_manifest(path)
    require(manifest.run['source_role'] == role, 'actual semantic run role differs from requested collection')


def collect(build: Path, model: Path, output: Path, role: str, arguments: list[str]) -> int:
    build, model = build.resolve(strict=True), model.resolve(strict=True)
    require(role in ('FULL_CPU', 'POTAL_COLLECTION'), 'unsupported source role')
    validate_build_role(build, role)
    prepared = input_arguments(arguments)
    require('--no-warmup' in prepared.execution, 'collection requires explicit --no-warmup')
    output = output.resolve()
    environment = collection_environment(output)
    output.mkdir(parents=True, exist_ok=False)
    reference = build
    reference_cache_sha = sha256(reference / 'CMakeCache.txt')
    build = output / 'native-build'
    configuration = fresh_configuration(reference, build)
    with (output / 'configure.log').open('x') as log:
        subprocess.run(configuration, check=True, stdout=log, stderr=subprocess.STDOUT)
    validate_build_role(build, role, configured=True)
    inputs_before = compile_input_snapshot(build, prebuild=True)
    with (output / 'build.log').open('x') as log:
        subprocess.run(['cmake', '--build', str(build), '--target', 'llama-cli', '--parallel', '2'],
                       check=True, stdout=log, stderr=subprocess.STDOUT)
    inputs_after = compile_input_snapshot(build)
    require(inputs_before == inputs_after, 'actual compiler inputs changed during fresh build')
    before = cpu_kernel_contract(build)
    artifacts = project_artifacts(build)
    libraries_before = runtime_dependencies(build)
    model_hash = sha256(model)
    command = [str(build / 'bin/llama-cli'), '-m', str(model), *prepared.execution]
    with (output / 'process.log').open('x') as log:
        process = subprocess.run(command, cwd=output, env=environment, stdout=log, stderr=subprocess.STDOUT)
    bindings: Record = {}
    for name, filename in ARTIFACTS.items():
        path = output / 'output/log' / filename
        if path.is_file():
            bindings[name] = {'path': str(path), 'sha256': sha256(path)}
    unchanged = artifacts == project_artifacts(build) and model_hash == sha256(model) and before == cpu_kernel_contract(build)
    unchanged = unchanged and inputs_before == compile_input_snapshot(build) and libraries_before == runtime_dependencies(build)
    unchanged = unchanged and all(sha256(Path(path)) == value for path, value in prepared.input_files.items())
    needed = {'cycle_log', 'semantic_graph'} | ({'npu_trace'} if role == 'POTAL_COLLECTION' else set())
    role_admitted, role_error = False, ''
    if process.returncode == 0 and 'semantic_graph' in bindings:
        try:
            validate_sidecar_role(output / 'output/log/semantic-graph.jsonl', role)
            role_admitted = True
        except (OSError, ValueError) as error:
            role_error = str(error)
    success = process.returncode == 0 and needed <= set(bindings) and unchanged and role_admitted
    if role == 'FULL_CPU' and 'npu_trace' in bindings:
        success = False
    libraries: list[Record] = [{'path': str(build / 'bin' / name), 'sha256': value}
                             for name, value in artifacts.items() if name != 'llama-cli']
    document: Record = {
        'schema': 'im2p-collection-provenance', 'version': 1, 'source_role': role,
        'process_exit_code': process.returncode, 'collection_success': success,
        'model_sha256': model_hash, 'model_path': str(model),
        'cpu_kernel_contract': before, 'executable_sha256': artifacts['llama-cli'],
        'project_libraries': list(libraries), 'artifacts': bindings,
        'command_arguments': list(prepared.normalized), 'command': list(command), 'cwd': str(output),
        'input_files': prepared.input_files, 'build_inputs_unchanged': unchanged,
        'semantic_role_admission': {'accepted': role_admitted, 'reason': role_error},
        'fresh_build': {'kind': 'FRESH_CONFIGURE_AND_COMPILE', 'reference_cache_sha256': reference_cache_sha,
                        'configure_command': list(configuration), 'build': str(build), 'actual_compile_inputs': inputs_before},
        'runtime_dependencies': libraries_before,
        'compile_commands_sha256': sha256(build / 'compile_commands.json'),
        'scope': 'content/build/run binding; not a signature or timing model',
    }
    with (output / 'collection-provenance.json').open('x') as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write('\n')
    return 0 if success else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--role', choices=('FULL_CPU', 'POTAL_COLLECTION'), required=True)
    parser.add_argument('llama_arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.llama_arguments
    if arguments[:1] == ['--']:
        arguments = arguments[1:]
    try:
        return collect(args.build, args.model, args.output, args.role, arguments)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'collection: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
