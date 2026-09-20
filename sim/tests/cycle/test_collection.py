from __future__ import annotations

from pathlib import Path
import os
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.real_lib_manifest import sha256
from sim.cycle.collect import collect, collection_environment, input_arguments, validate_build_role, validate_sidecar_role
from sim.cycle.collection_build import (TranslationUnit, compile_input_snapshot, fresh_configuration,
    normalized_flags, project_artifacts, runtime_dependencies)
from sim.cycle.npu_trace_schema import NpuTraceError, Record
from sim.cycle.reconstruct_graph import WORKLOAD


class CollectionProvenanceTests(unittest.TestCase):
    def test_wrong_full_cpu_role_rejected_before_any_child_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / 'model.gguf'
            model.write_bytes(b'not loaded by this negative test')
            (root / 'CMakeCache.txt').write_text(
                f'CMAKE_HOME_DIRECTORY:INTERNAL={root}\nCYCLE_SIM:STRING=0\nLOG_CYCLE:STRING=1\n'
                'GGML_CPU_CYCLE_LOG:BOOL=ON\nGGML_GEMMINI_OPTION:STRING=WS\n'
                'GGML_GEMMINI_EXECUTION_BACKEND:STRING=IM2P_SIM\n')
            with patch.dict(os.environ, {'PATH': '/usr/bin'}, clear=True), \
                    patch('sim.cycle.collect.subprocess.run', side_effect=AssertionError('child process must not run')):
                with self.assertRaises(NpuTraceError):
                    collect(root, model, root / 'output', 'FULL_CPU', ['--no-warmup'])
            self.assertFalse((root / 'output').exists())

    def test_prompt_content_bound_and_model_override_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prompt = Path(directory) / 'prompt.txt'
            prompt.write_text('same input')
            prepared = input_arguments(['-f', str(prompt), '-n', '5'])
            self.assertEqual(prepared.normalized, ('--file', 'sha256:' + sha256(prompt), '--predict', '5'))
            self.assertEqual(prepared.input_files, {str(prompt.resolve()): sha256(prompt)})
            self.assertEqual(prepared.execution, ('--file', str(prompt.resolve()), '--predict', '5'))
        for option in ('-m', '--model', '--model=x', '--model-url=x', '--gemmini-cycle-log',
                       '-hfr', '--lora', '--control-vector', '--prompt-cache'):
            with self.subTest(option=option), self.assertRaises(NpuTraceError):
                input_arguments([option, 'other'])

    def test_fresh_role_requires_actual_semantic_and_cpu_compile_definitions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'CMakeCache.txt').write_text('CYCLE_SIM:STRING=0\nLOG_CYCLE:STRING=1\n'
                'GGML_CPU_CYCLE_LOG:BOOL=ON\nGGML_GEMMINI_OPTION:STRING=CPU\n'
                'GGML_GEMMINI_EXECUTION_BACKEND:STRING=HARDWARE\n')
            rows = [{'file': '/source/ggml-gemmini-utils/src/semantic.cpp',
                     'command': 'c++ -DLOG_CYCLE=1 -DCYCLE_SIM=0 -DGEMMINI_SEMANTIC_CPU_ONLY_BUILD=1'},
                    {'file': '/source/ggml-cpu/ggml-cpu.c', 'command': 'cc -DCYCLE_LOG=1'}]
            (root / 'compile_commands.json').write_text(json.dumps(rows))
            validate_build_role(root, 'FULL_CPU', configured=True)
            with self.assertRaises(NpuTraceError): validate_build_role(root, 'POTAL_COLLECTION')
            for command in ('c++ -DLOG_CYCLE=1 -DCYCLE_SIM=0',
                            'c++ -DLOG_CYCLE=1 -DCYCLE_SIM=0 -DGEMMINI_SEMANTIC_CPU_ONLY_BUILD=0',
                            'c++ -DLOG_CYCLE=1 -DCYCLE_SIM=0 -DGEMMINI_SEMANTIC_CPU_ONLY_BUILD=1 -DGEMMINI_SEMANTIC_CPU_ONLY_BUILD=0'):
                rows[0]['command'] = command
                (root / 'compile_commands.json').write_text(json.dumps(rows))
                with self.subTest(command=command), self.assertRaises(NpuTraceError):
                    validate_build_role(root, 'FULL_CPU', configured=True)

    def test_actual_graph_role_and_cpu_proof_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'semantic-graph.jsonl'
            records: list[Record] = [
                {'kind': 'RUN', 'source_role': 'FULL_CPU', 'run_config_id': 'workload-0',
                 'workload': {key: 0 for key in WORKLOAD}, 'producer': {'warmup_excluded': True,
                 'reserve_measure_graphs_excluded': True}, 'cpu_only_build': True},
                {'kind': 'PHASE', 'phase_kind': 'prefill', 'decode_index': None, 'input_tokens': 1,
                 'token_fingerprint': 'fnv1a64-le-i32:0000000000000000'},
                {'kind': 'GRAPH', 'phase_kind': 'prefill', 'decode_index': None, 'graph_occurrence': 0,
                 'leaves': [], 'nodes': [{'node_ordinal': 0, 'excluded': False, 'payload': {'op': 'ADD',
                 'name': 'repeated', 'type': 'f32', 'shape': [1, 1, 1, 1], 'parameters_hex': '',
                 'parameters_status': 'STATIC_BYTES', 'inputs': []}}]},
                {'kind': 'NODE_EXECUTION', 'semantic_phase_kind': 'prefill', 'semantic_decode_index': None,
                 'semantic_graph_occurrence': 0, 'semantic_node_ordinal': 0, 'source_role': 'FULL_CPU',
                 'run_config_id': 'workload-0', 'actual_backend': 'CPU', 'execution_class': 'ORDINARY_CPU', 'success': True},
                {'kind': 'RUN_END', 'success': True, 'expected_node_count': 1, 'executed_node_count': 1,
                 'cpu_only_proven': True, 'graph_count': 1}]
            def write() -> None:
                path.write_text(''.join(json.dumps({'schema': 'im2p-semantic-graph', 'version': 1,
                    'sequence': i, **record}) + '\n' for i, record in enumerate(records)))
            write()
            validate_sidecar_role(path, 'FULL_CPU')
            with self.assertRaises(NpuTraceError): validate_sidecar_role(path, 'POTAL_COLLECTION')
            records[-1]['cpu_only_proven'] = False
            write()
            with self.assertRaises(NpuTraceError): validate_sidecar_role(path, 'FULL_CPU')

    def test_relative_prompt_is_resolved_before_child_cwd_change(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            prompt = Path(directory) / 'prompt.txt'
            prompt.write_text('portable prompt')
            prepared = input_arguments(['--file=' + str(prompt.relative_to(Path.cwd()))])
            self.assertEqual(prepared.execution, ('--file', str(prompt.resolve())))
        for arguments in (['--temp'], ['-n', '5', '--predict', '6'], ['--no-warmup=1']):
            with self.subTest(arguments=arguments), self.assertRaises(NpuTraceError):
                input_arguments(arguments)

    def test_unbound_environment_is_rejected_and_log_directory_is_absolute(self) -> None:
        for key in ('LLAMA_ARG_MODEL_URL', 'LLAMA_ARG_TEMP', 'GEMMINI_RMD_BACKEND', 'EXSIA_THREADS',
                    'DYLD_LIBRARY_PATH', 'OMP_NUM_THREADS', 'GGML_BACKEND_PATH'):
            with self.subTest(key=key), patch.dict(os.environ, {key: 'unbound'}, clear=True), self.assertRaises(NpuTraceError):
                collection_environment(Path('relative-output'))
        with patch.dict(os.environ, {'PATH': '/usr/bin'}, clear=True):
            environment = collection_environment(Path('relative-output'))
            self.assertTrue(Path(environment['GEMMINI_LOG_DIR']).is_absolute())

    def test_fresh_configuration_reuses_options_not_objects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / 'reference'
            reference.mkdir()
            (reference / 'CMakeCache.txt').write_text(
                f'CMAKE_HOME_DIRECTORY:INTERNAL={root}\nCYCLE_SIM:STRING=1\n'
                'GGML_GEMMINI:UNINITIALIZED=ON\n'
                'GGML_GEMMINI_OPTION:STRING=WS\nCMAKE_CXX_FLAGS:STRING=-O3\n'
                'CMAKE_EXE_LINKER_FLAGS:STRING=-fsanitize=address\nCMAKE_INTERPROCEDURAL_OPTIMIZATION:BOOL=ON\n'
                'CMAKE_BUILD_RPATH:STRING=/bound/external-libraries\n'
                'CMAKE_CXX_COMPILER_LAUNCHER:STRING=ccache\nCMAKE_CACHEFILE_DIR:INTERNAL=/old\n')
            destination = root / 'fresh'
            command = fresh_configuration(reference, destination)
            self.assertIn('-DCYCLE_SIM:STRING=1', command)
            self.assertIn('-DGGML_GEMMINI=ON', command)
            self.assertIn('-DCMAKE_CXX_FLAGS:STRING=-O3', command)
            self.assertIn('-DCMAKE_EXE_LINKER_FLAGS:STRING=-fsanitize=address', command)
            self.assertIn('-DCMAKE_INTERPROCEDURAL_OPTIMIZATION:BOOL=ON', command)
            self.assertIn('-DCMAKE_BUILD_RPATH:STRING=/bound/external-libraries', command)
            self.assertIn('-DCMAKE_CXX_COMPILER_LAUNCHER=', command)
            self.assertNotIn('ccache', command)
            destination.mkdir()
            with self.assertRaises(NpuTraceError):
                fresh_configuration(reference, destination)

    def test_only_instrumentation_and_bound_search_paths_are_normalized(self) -> None:
        unit = TranslationUnit(Path('/source/test.c'), Path('/build'), (
            '/usr/bin/cc', '-DCYCLE_SIM=1', '-DGGML_USE_ACCELERATE', '-O3',
            '-mcpu=native', '-I/headers', '-I', '/other', '-isystem', '/sdk',
            '-o', 'test.o', '-c', '/source/test.c'), 'test.o')
        self.assertEqual(normalized_flags(unit), ['-DGGML_USE_ACCELERATE', '-O3', '-mcpu=native'])

    def test_project_library_change_changes_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            (build / 'bin').mkdir()
            executable = build / 'bin/llama-cli'
            executable.write_bytes(b'executable fixture')
            library = build / 'bin/libggml-cpu.dylib'
            library.write_bytes(b'kernel A')
            before = project_artifacts(build)
            library.write_bytes(b'kernel B')
            after = project_artifacts(build)
            self.assertEqual(before['llama-cli'], after['llama-cli'])
            self.assertNotEqual(before['libggml-cpu.dylib'], after['libggml-cpu.dylib'])

    def test_actual_fresh_compiler_dependencies_and_link_closure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            kernels = source / 'ggml-cpu'
            kernels.mkdir(parents=True)
            header = kernels / 'value.h'
            header.write_text('#define VALUE 7\n')
            (kernels / 'kernel.c').write_text('#include "value.h"\nint value(void) { return VALUE; }\n')
            (source / 'main.c').write_text('int value(void);\nint main(void) { return value() != 7; }\n')
            (source / 'CMakeLists.txt').write_text(
                'cmake_minimum_required(VERSION 3.20)\nproject(collection_binding C)\n'
                'set(CMAKE_LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")\n'
                'set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")\n'
                'add_library(ggml-cpu SHARED ggml-cpu/kernel.c)\n'
                'add_executable(llama-cli main.c)\ntarget_link_libraries(llama-cli PRIVATE ggml-cpu)\n')
            reference = root / 'reference'
            reference.mkdir()
            (reference / 'CMakeCache.txt').write_text(f'CMAKE_HOME_DIRECTORY:INTERNAL={source}\n')
            build = root / 'fresh'
            subprocess.run(fresh_configuration(reference, build), check=True, capture_output=True)
            before = compile_input_snapshot(build, prebuild=True)
            self.assertFalse((build / 'bin/llama-cli').exists())
            subprocess.run(['cmake', '--build', str(build)], check=True, capture_output=True)
            self.assertEqual(before, compile_input_snapshot(build))
            subprocess.run([str(build / 'bin/llama-cli')], check=True, capture_output=True)
            dependencies = runtime_dependencies(build)
            self.assertTrue(any('libggml-cpu' in path for path in dependencies))
            times = header.stat()
            header.write_text('#define VALUE 8\n')
            os.utime(header, ns=(times.st_atime_ns, times.st_mtime_ns))
            self.assertNotEqual(before, compile_input_snapshot(build))


if __name__ == '__main__':
    unittest.main()
