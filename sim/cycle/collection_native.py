from __future__ import annotations

from dataclasses import dataclass
import json
import hashlib
import os
from pathlib import Path
import re
import shlex
import subprocess

from scripts.gemmini_replay_contract import contract_digest
from sim.cycle.collect import validate_build_role
from sim.cycle.collection_build import (compile_input_snapshot, cpu_kernel_contract, project_artifacts,
    runtime_dependencies, translation_units)
from sim.cycle.npu_trace_schema import NpuTraceError, Record, integer, object_value, require, text, unique_pairs
from sim.cycle.reconstruct_graph import array, json_records, read_manifest, sha256

EXECUTABLE = 'llama-eval-workload'


def object_hashes(build: Path, sources: frozenset[Path] | None = None) -> Record:
    return {str(unit.directory / unit.object_file): sha256(unit.directory / unit.object_file)
            for unit in translation_units(build, sources)}


def target_sources(build: Path) -> frozenset[Path]:
    output = subprocess.check_output(['ninja', '-C', str(build), '-t', 'inputs', 'bin/' + EXECUTABLE],
                                     text=True, timeout=30)
    inputs = {(Path(line) if Path(line).is_absolute() else build / line).resolve()
              for line in output.splitlines() if line.strip()}
    rows = array(json.loads((build / 'compile_commands.json').read_text(), object_pairs_hook=unique_pairs))
    candidates: set[Path] = set()
    for raw in rows:
        row = object_value(raw)
        command = shlex.split(text(row, 'command'))
        require('-o' in command, 'selected native compile command lacks output')
        candidates.add(Path(text(row, 'file')).resolve(strict=True))
        candidates.add((Path(text(row, 'directory')) / command[command.index('-o') + 1]).resolve())
    return frozenset(candidates & inputs)


def validate_receipt(receipt: Record) -> None:
    require(receipt.get('schema') == 'im2p-native-build-receipt' and type(receipt.get('version')) is int and receipt.get('version') == 1,
            'unsupported native build receipt')
    require(receipt.get('kind') == 'BUILD_ONCE_MEASURE_MANY' and type(receipt.get('build_exit_code')) is int and receipt.get('build_exit_code') == 0,
            'native build was not successful')
    require(receipt.get('sha256') == contract_digest(receipt), 'native build receipt digest mismatch')
    require(receipt.get('executable') == EXECUTABLE, 'native build receipt selected wrong executable')
    command = array(receipt.get('build_command'))
    require(bool(command) and all(isinstance(value, str) and value for value in command), 'native build command missing')
    require(command[:2] == ['cmake', '--build'] and '--target' in command and EXECUTABLE in command,
            'native receipt does not build selected official target')
    for key in ('actual_compile_inputs', 'cpu_object_sha256', 'producer_object_sha256', 'project_artifacts'):
        entries = object_value(receipt.get(key))
        require(bool(entries) and all(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value)
                                     for value in entries.values()), 'incomplete native build binding: ' + key)
    info = object_value(receipt.get('build_info'))
    require(type(info.get('activation_metrics')) is int and type(info.get('residual_metrics')) is int and
            info.get('activation_metrics') == 0 and info.get('residual_metrics') == 0,
            'native source collection requires metric-OFF binary')
    require(receipt.get('source_role') in ('FULL_CPU', 'POTAL_COLLECTION') and
            info.get('cycle_sim') == (1 if receipt['source_role'] == 'POTAL_COLLECTION' else 0),
            'native build role mismatch')
    for key in ('cmake_cache_sha256', 'compile_commands_sha256', 'cpu_kernel_contract_sha256'):
        require(isinstance(receipt.get(key), str) and re.fullmatch('[0-9a-f]{64}', str(receipt[key])) is not None,
                'native build content binding missing: ' + key)


@dataclass(frozen=True, slots=True)
class NativeBuild:
    root: Path
    role: str
    receipt: Record
    kernel: Record
    sources: frozenset[Path]

    def verify(self) -> None:
        require(self.sources == target_sources(self.root) and
                self.receipt['actual_compile_inputs'] == compile_input_snapshot(self.root, sources=self.sources) and
                self.receipt['cmake_cache_sha256'] == sha256(self.root / 'CMakeCache.txt') and
                self.receipt['cpu_object_sha256'] == object_hashes(self.root) and
                self.receipt['producer_object_sha256'] == object_hashes(self.root, self.sources) and
                self.receipt['project_artifacts'] == project_artifacts(self.root, EXECUTABLE) and
                self.receipt['runtime_dependencies'] == runtime_dependencies(self.root, EXECUTABLE) and
                self.kernel == cpu_kernel_contract(self.root), 'native build changed during collection/reuse')


def prepare_native_build(build: Path, output: Path) -> NativeBuild:
    build = build.resolve(strict=True)
    configure = ['cmake', '--build', str(build), '--target', 'rebuild_cache']
    with (output / 'native-configure.log').open('x') as log:
        configured = subprocess.run(configure, check=False, stdout=log, stderr=subprocess.STDOUT, timeout=120)
    require(configured.returncode == 0, 'native configuration refresh failed; see native-configure.log')
    sources = target_sources(build)
    before = compile_input_snapshot(build, prebuild=True, sources=sources)
    with (output / 'native-compile-inputs-before.json').open('x') as stream:
        json.dump(before, stream, indent=2, sort_keys=True); stream.write('\n')
    command = ['cmake', '--build', str(build), '--target', EXECUTABLE, '--parallel', '2']
    with (output / 'native-build.log').open('x') as log:
        process = subprocess.run(command, check=False, stdout=log, stderr=subprocess.STDOUT, timeout=600)
    require(process.returncode == 0, 'native build failed; see native-build.log')
    after = compile_input_snapshot(build, sources=sources)
    with (output / 'native-compile-inputs-after.json').open('x') as stream:
        json.dump(after, stream, indent=2, sort_keys=True); stream.write('\n')
    require(before == after, 'compiler inputs changed during native build')
    binary = build / 'bin' / EXECUTABLE
    info = object_value(json.loads(subprocess.check_output([str(binary), '--build-info'], text=True, timeout=15),
                                   object_pairs_hook=unique_pairs))
    role = 'POTAL_COLLECTION' if info.get('cycle_sim') == 1 else 'FULL_CPU'
    validate_build_role(build, role, configured=True)
    kernel = cpu_kernel_contract(build)
    receipt: Record = {'schema': 'im2p-native-build-receipt', 'version': 1,
        'kind': 'BUILD_ONCE_MEASURE_MANY', 'build_exit_code': process.returncode,
        'source_role': role,
        'configure_command': list(configure), 'configure_exit_code': configured.returncode,
        'build': str(build), 'build_command': list(command), 'executable': EXECUTABLE,
        'build_info': info, 'actual_compile_inputs': before, 'cpu_object_sha256': object_hashes(build),
        'producer_object_sha256': object_hashes(build, sources),
        'project_artifacts': project_artifacts(build, EXECUTABLE),
        'runtime_dependencies': runtime_dependencies(build, EXECUTABLE),
        'cpu_kernel_contract_sha256': kernel['sha256'],
        'compile_commands_sha256': sha256(build / 'compile_commands.json'),
        'cmake_cache_sha256': sha256(build / 'CMakeCache.txt')}
    receipt['sha256'] = contract_digest(receipt)
    validate_receipt(receipt)
    with (output / 'native-build-receipt.json').open('x') as stream:
        json.dump(receipt, stream, indent=2, sort_keys=True); stream.write('\n')
    return NativeBuild(build, role, receipt, kernel, sources)


@dataclass(frozen=True, slots=True)
class NativeCollection:
    build: NativeBuild
    output: Path
    command: tuple[str, ...]
    model: Path
    inputs: Record


def start_native_collection(build: NativeBuild, output: Path, command: tuple[str, ...]) -> NativeCollection:
    build.verify()
    model = Path(command[command.index('--model') + 1]).resolve(strict=True)
    dataset = Path(command[command.index('--file') + 1]).resolve(strict=True)
    paths = [model, dataset]
    if '--forced-token-ids' in command:
        require(build.role == 'FULL_CPU', 'forced token input belongs only to FullCPU cost collection')
        paths.append(Path(command[command.index('--forced-token-ids') + 1]).resolve(strict=True))
    return NativeCollection(build, output, command, model, {str(path): sha256(path) for path in paths})


def finish_native_collection(collection: NativeCollection) -> Path:
    collection.build.verify()
    require(all(sha256(Path(path)) == digest for path, digest in collection.inputs.items()),
            'native collection model/dataset changed')
    native = collection.output / 'native'
    process_path = collection.output / 'command.json'
    process = object_value(json.loads(process_path.read_text(), object_pairs_hook=unique_pairs))
    require(type(process.get('exit_code')) is int and process['exit_code'] == 0 and
            process.get('argv') == list(collection.command) and process.get('cwd') == str(collection.output),
            'native process receipt missing/mismatched/failed')
    workload = object_value(json.loads((native / 'workload.json').read_text(), object_pairs_hook=unique_pairs))
    require(workload.get('complete') is True and workload.get('selected_chunks') == 1 and
            workload.get('workload') == 'E2E_GENERATION_256_128', 'native collection incomplete/wrong workload')
    chunks = array(workload.get('chunks'))
    require(len(chunks) == 1, 'one native chunk required per measurement')
    chunk = object_value(chunks[0])
    chunk_id = integer(chunk, 'chunk_id')
    require(str(chunk_id) == collection.command[collection.command.index('--chunk-index') + 1], 'native chunk mapping differs')
    log_root = native / ('chunk-' + str(chunk_id))
    endpoint_path = native / 'application.jsonl'
    endpoint = object_value(json.loads(endpoint_path.read_text(), object_pairs_hook=unique_pairs))
    execution_kind = text(workload, 'execution_kind')
    forced = execution_kind == 'FORCED_CPU_COST_ONLY'
    require(forced == ('--forced-token-ids' in collection.command), 'forced execution kind/command mismatch')
    require(endpoint.get('execution_kind') == execution_kind and integer(endpoint, 'decode_calls') == 127 and
            integer(endpoint, 'actual_sampler_calls') == (0 if forced else 128), 'native sampling/decode execution mismatch')
    paths = {'cycle_log': log_root / 'cycle-log.jsonl', 'semantic_graph': log_root / 'semantic-graph.jsonl',
             'execution_lifecycle': log_root / 'execution-lifecycle.jsonl', 'application_endpoints': endpoint_path}
    if collection.build.role == 'POTAL_COLLECTION':
        paths['npu_trace'] = log_root / 'npu-cycle-trace.jsonl'
        paths['application_cpu'] = native / 'application-cpu.jsonl'
    manifest = read_manifest(paths['semantic_graph'])
    require(manifest.run['source_role'] == collection.build.role, 'actual semantic collection role differs')
    normalized: list[str] = []
    arguments = iter(collection.command[1:])
    for option in arguments:
        value = next(arguments, None)
        if value is None:
            raise NpuTraceError('native collection requires explicit option values')
        if option in ('--output-dir', '--run-id'):
            continue
        normalized.append(option)
        normalized.append('sha256:' + str(collection.inputs[str(Path(value).resolve())])
                          if option in ('--model', '--file', '--forced-token-ids') else str(value))
    receipt = collection.build.receipt
    artifacts = object_value(receipt['project_artifacts'])
    libraries: list[Record] = [{'path': str(collection.build.root / 'bin' / name), 'sha256': value}
                             for name, value in artifacts.items() if name != EXECUTABLE]
    proof: Record = {'schema': 'im2p-collection-provenance', 'version': 2,
        'source_role': collection.build.role, 'process_exit_code': 0, 'collection_success': True,
        'execution_kind': execution_kind, 'trajectory_source': 'POTAL' if forced else 'NATIVE_SAMPLING',
        'actual_sampler_calls': 0 if forced else 128, 'decode_calls': 127, 'chunk_id': chunk_id,
        'recipe_id': workload.get('recipe_id'),
        'input_tokens_sha256': hashlib.sha256(json.dumps(chunk['input_tokens'], separators=(',', ':')).encode()).hexdigest(),
        'output_tokens_sha256': hashlib.sha256(json.dumps(endpoint['generated_tokens'], separators=(',', ':')).encode()).hexdigest(),
        'build_inputs_unchanged': True, 'model_sha256': collection.inputs[str(collection.model)],
        'model_path': str(collection.model), 'executable_sha256': artifacts[EXECUTABLE],
        'cpu_kernel_contract': collection.build.kernel, 'project_libraries': list(libraries),
        'command_arguments': list(normalized), 'command': list(collection.command),
        'cwd': str(collection.output), 'input_files': collection.inputs, 'native_build': receipt,
        'runtime_dependencies': receipt['runtime_dependencies'],
        'compile_commands_sha256': receipt['compile_commands_sha256'],
        'process_receipt_sha256': sha256(process_path),
        'artifacts': {name: {'path': str(path), 'sha256': sha256(path)} for name, path in paths.items()},
        'scope': 'native build-once/source/content binding; not a signature or timing model'}
    if forced:
        require(endpoint.get('cost_only') is True and endpoint.get('samples') == 0 and
                endpoint.get('trajectory_source') == 'POTAL', 'forced CPU cannot claim free sampling')
        pairing = object_value(json.loads((collection.output / 'paired-trajectory.json').read_text(), object_pairs_hook=unique_pairs))
        require(pairing.get('token_vector_sha256') == proof['output_tokens_sha256'] and
                pairing.get('input_tokens_sha256') == proof['input_tokens_sha256'], 'forced native trajectory differs from paired PoTal')
        from sim.cycle.execution_lifecycle import validate_forced_lifecycle
        forced_path = Path(collection.command[collection.command.index('--forced-token-ids') + 1])
        token_values = array(json.loads(forced_path.read_text()))
        require(all(type(value) is int and value >= 0 for value in token_values), 'invalid forced input token vector')
        tokens = tuple(value for value in token_values if isinstance(value, int))
        validate_forced_lifecycle(json_records(paths['execution_lifecycle']), manifest, tokens)
        proof['paired_trajectory'] = pairing
    output = collection.output / 'collection-provenance.json'
    temporary = output.with_suffix('.partial')
    with temporary.open('x') as stream:
        json.dump(proof, stream, indent=2, sort_keys=True); stream.write('\n')
    os.link(temporary, output)
    temporary.unlink()
    return output
