from __future__ import annotations

import json
import graphlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts.gemmini_replay_contract import contract_digest
from sim.cycle.npu_trace import ReplayArtifacts, ReplayOutputs, replay
from sim.cycle.npu_trace_schema import Record, object_value
from sim.cycle import reconstruct as reconstruct_module
from sim.cycle.reconstruct import Inputs, reconstruct
from sim.cycle.reconstruct_cpu import CollectionFiles, duration_sample
from sim.cycle.reconstruct_graph import array, json_records, sha256
from sim.cycle.reconstruct_npu import NpuFiles
from sim.tests.cycle.test_npu_trace import records, resequence
from sim.tests.cycle.test_reconstruct import graph_records, write_rows


def cpu_record(role: str, ordinal: int, duration: int) -> Record:
    return dict(schema='gemmini.cycle', version=2, record_type='CYCLE_INTERVAL', source='host_tick', unit='tick',
        op='cpu.add' if ordinal == 0 else 'cpu.mul_mat', layer='same', run_id=0, stripe_id=None, slot=None,
        node_id=ordinal, worker_id=0, worker_count=1, start=100, end=100+duration, delta=duration, valid=True,
        cpu_work_cycles=duration, cpu_work_cycles_source='fixture_cpu_cycles', cpu_work_cycles_unit='cycle',
        cpu_work_cycles_valid=True, cpu_work_cycles_reason=None, thread_cpu_ns=duration*2,
        thread_cpu_valid=True, thread_cpu_reason=None, host_elapsed_ns=duration, host_elapsed_valid=True,
        host_elapsed_reason=None, host_start_ns=100, host_end_ns=100+duration,
        host_execution_id='fixture-execution', thread_id=1, interval_class='PER_WORKER_CPU_WORK',
        cpu_service=True, semantic_phase_kind='prefill', semantic_decode_index=None,
        semantic_graph_occurrence=0, semantic_node_ordinal=ordinal, run_config_id='workload-0', source_role=role,
        duration_source=role, duration_role='ORDINARY_CPU_REFERENCE' if role == 'FULL_CPU' else 'OBSERVATION_ONLY',
        host_stage_id=None)


def refresh_proofs(inputs: Inputs) -> None:
    kernel: Record = dict(schema='im2p-ordinary-cpu-kernel-contract', version=1,
        scope='SYNTHETIC_STRUCTURAL_FIXTURE_NOT_BUILD_EVIDENCE', units={'fixture.c': '1'*64},
        dependencies={'fixture.h': '2'*64}, compilers={'fixture-cc': '3'*64})
    kernel['sha256'] = contract_digest(kernel)
    for role, files in (('FULL_CPU', inputs.full_cpu), ('POTAL_COLLECTION', inputs.potal)):
        artifacts: Record = {'cycle_log': {'sha256': sha256(files.log)}, 'semantic_graph': {'sha256': sha256(files.graph)}}
        if role == 'POTAL_COLLECTION': artifacts['npu_trace'] = {'sha256': sha256(inputs.npu.trace)}
        proof: Record = dict(schema='im2p-collection-provenance', version=1, source_role=role,
            process_exit_code=0, collection_success=True, build_inputs_unchanged=True, model_sha256='4'*64,
            executable_sha256='5'*64, compile_commands_sha256='6'*64, cpu_kernel_contract=kernel,
            command_arguments=['SYNTHETIC_FIXTURE'], input_files={}, project_libraries=[], artifacts=artifacts,
            fresh_build=dict(kind='FRESH_CONFIGURE_AND_COMPILE', reference_cache_sha256='7'*64,
                             configure_command=['fixture'], build='fixture', actual_compile_inputs={'fixture.c':'8'*64}),
            runtime_dependencies={'fixture-runtime': {'kind':'EXTERNAL', 'sha256':'9'*64}},
            scope='SYNTHETIC_STRUCTURAL_FIXTURE_NOT_RUNTIME_COLLECTION')
        files.provenance.write_text(json.dumps(proof))


def fixture(root: Path, artifacts: ReplayArtifacts) -> Inputs:
    full = CollectionFiles(root/'full-cycle.jsonl', root/'full-graph.jsonl', root/'full-provenance.json')
    potal = CollectionFiles(root/'potal-cycle.jsonl', root/'potal-graph.jsonl', root/'potal-provenance.json')
    write_rows(full.graph, graph_records('FULL_CPU')); write_rows(potal.graph, graph_records('POTAL_COLLECTION'))
    write_rows(full.log, [cpu_record('FULL_CPU', 0, 10), cpu_record('FULL_CPU', 1, 90000)])
    host = cpu_record('POTAL_COLLECTION', 1, 20); host.update(
        op='recompose', host_stage_id=0, duration_role='POTAL_HOST', interval_class='CANONICAL_ADDITIVE')
    write_rows(potal.log, [cpu_record('POTAL_COLLECTION', 0, 999), host])
    rows = records()
    stage: Record = dict(kind='HOST_STAGE', phase_id=0, operation_id=0, node_id=0,
        parent_id=0, call_id=0, host_stage_id=0, execution_class='POTAL_HOST', stage_name='recompose',
        source_owner='llama.cpp-gemmini', source_location='ggml/src/residual.cpp:recompose',
        required_work_ids=[0], required_host_stage_ids=[])
    rows[7:7] = [dict(stage, event='BEGIN', status='declared'), dict(stage, event='END', status='success')]
    rows[-1].update(host_stage_count=1, completed_host_stage_count=1, potal_host_count=1)
    resequence(rows)
    for row in rows:
        if 'semantic_node_ordinal' in row: row['semantic_node_ordinal'] = 1
        if 'layer' in row: row['layer'] = 'same'
    trace = root/'npu.jsonl'; write_rows(trace, rows)
    result = ReplayOutputs(root/'result.jsonl', root/'replay-summary.json')
    replay(trace, artifacts, result)
    inputs = Inputs(full, potal, NpuFiles(trace, result.results, artifacts))
    refresh_proofs(inputs)
    return inputs


@unittest.skipUnless(os.getenv('IM2P_CYCLE_LIBRARY') and os.getenv('IM2P_CYCLE_CERTIFICATE'), 'actual certified C model required')
class ReconstructionTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        library, certificate = self.root/'library.dylib', self.root/'certificate.json'
        shutil.copyfile(Path(os.environ['IM2P_CYCLE_LIBRARY']), library)
        shutil.copyfile(Path(os.environ['IM2P_CYCLE_CERTIFICATE']), certificate)
        self.inputs = fixture(self.root, ReplayArtifacts(library, certificate))
        self.outputs = ReplayOutputs(self.root/'dataset.jsonl', self.root/'summary.json')

    def test_three_sources_choose_owned_durations_without_aggregation(self):
        summary = reconstruct(self.inputs, self.outputs)
        rows = list(json_records(self.outputs.results))
        ordinary = next(row for row in rows if row['kind'] == 'SERVICE' and row['node_class'] == 'ORDINARY_CPU')
        vector = object_value(ordinary['duration'])['worker_intervals']
        original = list(json_records(self.inputs.full_cpu.log))[0]
        self.assertEqual(vector, [duration_sample(dict(original, source_line=1))])
        host = next(row for row in rows if row['kind'] == 'SERVICE' and row['node_class'] == 'POTAL_HOST')
        self.assertEqual(object_value(host['duration'])['delta'], 20)
        self.assertEqual(summary['npu_work_count'], 1)
        self.assertEqual(summary['system_latency'], 'NOT_MODELED')
        self.assertTrue(any(row.get('reason') == 'REPLACED_BY_NPU' for row in rows))
        self.assertTrue(all(isinstance(row['node_id'], str) for row in rows))
        ids = {str(row['node_id']) for row in rows}
        self.assertEqual(len(ids), len(rows))
        for row in rows:
            self.assertTrue(set(array(row['dependencies'])) <= ids)
        dependencies = {str(row['node_id']): tuple(str(value) for value in array(row['dependencies'])) for row in rows}
        self.assertEqual(len(tuple(graphlib.TopologicalSorter(dependencies).static_order())), len(rows))
        self.assertEqual(summary['execution_node_counts'], {'ORDINARY_CPU': 1, 'POTAL_HOST': 1, 'TARGET_NPU': 1,
            'FUNCTIONAL_EMULATION': 0, 'EXCLUDED': 0, 'UNSUPPORTED': 0})
        self.assertEqual(summary['execution_node_count'], sum(row['is_execution_node'] is True for row in rows))

    def test_thread_cpu_clock_is_a_valid_cpu_duration_source(self):
        rows = list(json_records(self.inputs.full_cpu.log))
        rows[0].update(source='thread_cpu_clock', unit='nanosecond', start=1000, end=1010, delta=10)
        write_rows(self.inputs.full_cpu.log, rows)
        refresh_proofs(self.inputs)
        reconstruct(self.inputs, self.outputs)
        ordinary = next(row for row in json_records(self.outputs.results)
                        if row['kind'] == 'SERVICE' and row['node_class'] == 'ORDINARY_CPU')
        sample = object_value(array(object_value(ordinary['duration'])['worker_intervals'])[0])
        self.assertEqual(sample['source'], 'thread_cpu_clock')

    def test_missing_and_duplicate_costs_rejected_after_rehash(self):
        full, potal = list(json_records(self.inputs.full_cpu.log)), list(json_records(self.inputs.potal.log))
        for source, rows in ((self.inputs.full_cpu.log, full[1:]), (self.inputs.full_cpu.log, full+full[:1]),
                             (self.inputs.potal.log, potal[:1]), (self.inputs.potal.log, potal+potal[1:])):
            write_rows(self.inputs.full_cpu.log, full); write_rows(self.inputs.potal.log, potal)
            write_rows(source, rows); refresh_proofs(self.inputs)
            with self.assertRaises(ValueError): reconstruct(self.inputs, self.outputs)
            self.assertFalse(self.outputs.results.exists() or self.outputs.summary.exists())

    def test_extra_stage_and_worker_configuration_mismatch_rejected(self):
        rows = list(json_records(self.inputs.potal.log)); extra = dict(rows[1], host_stage_id=99)
        write_rows(self.inputs.potal.log, rows+[extra]); refresh_proofs(self.inputs)
        with self.assertRaisesRegex(ValueError, 'extra host'): reconstruct(self.inputs, self.outputs)
        write_rows(self.inputs.potal.log, rows)
        full = list(json_records(self.inputs.full_cpu.log)); full[0]['worker_count'] = 2
        write_rows(self.inputs.full_cpu.log, full); refresh_proofs(self.inputs)
        with self.assertRaises(ValueError): reconstruct(self.inputs, self.outputs)

    def test_npu_result_missing_extra_or_geometry_mismatch_rejected(self):
        result = list(json_records(self.inputs.npu.results))
        altered = dict(result[0], tile_i_count=2)
        for rows in ([], result+result, [altered]):
            write_rows(self.inputs.npu.results, rows)
            with self.assertRaises(ValueError): reconstruct(self.inputs, self.outputs)

    def test_model_and_cpu_kernel_provenance_mismatch_rejected(self):
        proof = json.loads(self.inputs.potal.provenance.read_text()); proof['model_sha256'] = 'a'*64
        self.inputs.potal.provenance.write_text(json.dumps(proof))
        with self.assertRaisesRegex(ValueError, 'model contents'): reconstruct(self.inputs, self.outputs)
        refresh_proofs(self.inputs)
        proof = json.loads(self.inputs.potal.provenance.read_text()); proof['cpu_kernel_contract']['units']['fixture.c'] = 'a'*64
        proof['cpu_kernel_contract']['sha256'] = contract_digest(proof['cpu_kernel_contract'])
        self.inputs.potal.provenance.write_text(json.dumps(proof))
        with self.assertRaisesRegex(ValueError, 'kernel/build'): reconstruct(self.inputs, self.outputs)

    def test_sampler_argument_and_missing_file_hash_bindings_rejected(self):
        proof = json.loads(self.inputs.potal.provenance.read_text())
        proof['command_arguments'] += ['--temp', '0.7']
        self.inputs.potal.provenance.write_text(json.dumps(proof))
        with self.assertRaisesRegex(ValueError, 'arguments differ'): reconstruct(self.inputs, self.outputs)
        refresh_proofs(self.inputs)
        proof = json.loads(self.inputs.potal.provenance.read_text())
        proof['command_arguments'] += ['--file', 'sha256:'+'a'*64]
        self.inputs.potal.provenance.write_text(json.dumps(proof))
        with self.assertRaisesRegex(ValueError, 'content-hash coverage'): reconstruct(self.inputs, self.outputs)

    def test_potal_ordinary_observation_absence_is_not_a_missing_cost(self):
        rows = list(json_records(self.inputs.potal.log)); write_rows(self.inputs.potal.log, rows[1:]); refresh_proofs(self.inputs)
        self.assertEqual(reconstruct(self.inputs, self.outputs)['ordinary_cpu_bijection'], 'PASS')

    def test_existing_softmax_profiler_aliases_keep_semantic_identity(self):
        for opcode, stage in (('SOFT_MAX', 'cpu.softmax'), ('SOFT_MAX_BACK', 'cpu.softmax_back')):
            for files in (self.inputs.full_cpu, self.inputs.potal):
                graph = list(json_records(files.graph))
                nodes = graph[2]['nodes']
                self.assertIsInstance(nodes, list)
                if not isinstance(nodes, list): raise AssertionError('fixture nodes missing')
                object_value(object_value(nodes[0])['payload'])['op'] = opcode
                write_rows(files.graph, graph)
                rows = list(json_records(files.log)); rows[0]['op'] = stage; write_rows(files.log, rows)
            refresh_proofs(self.inputs)
            output = ReplayOutputs(self.root/(opcode+'.jsonl'), self.root/(opcode+'.json'))
            self.assertEqual(reconstruct(self.inputs, output)['ordinary_cpu_bijection'], 'PASS')

    def test_functional_stage_is_excluded_without_fabricated_cpu_cost(self):
        rows = list(json_records(self.inputs.npu.trace))
        original = next(row for row in rows if row['kind'] == 'HOST_STAGE' and row['event'] == 'BEGIN')
        begin = dict(original, host_stage_id=1, execution_class='FUNCTIONAL_EMULATION',
                     stage_name='functional.compute', required_host_stage_ids=[0])
        rows[-2:-2] = [begin, dict(begin, event='END', status='success')]
        rows[-1].update(host_stage_count=2, completed_host_stage_count=2, functional_emulation_count=1)
        for sequence, row in enumerate(rows): row['sequence'] = sequence
        write_rows(self.inputs.npu.trace, rows); refresh_proofs(self.inputs)
        summary = reconstruct(self.inputs, self.outputs)
        excluded = next(row for row in json_records(self.outputs.results) if row.get('node_class') == 'FUNCTIONAL_EMULATION')
        self.assertIsNone(excluded['duration'])
        self.assertIs(excluded['cost_included'], False)
        self.assertEqual(summary['functional_emulation_count'], 1)
        self.assertEqual(excluded['node_id'], 'host:1')
        self.assertEqual(summary['execution_node_count'], 4)

    def test_excluded_graph_nodes_have_zero_cost_and_separate_parent_ledger(self):
        for files in (self.inputs.full_cpu, self.inputs.potal):
            graph = list(json_records(files.graph))
            excluded: Record = dict(node_ordinal=2, excluded=True, payload=dict(op='VIEW', name='view', type='F32',
                shape=[1, 1, 1, 1], parameters_hex='00', parameters_status='STATIC_BYTES', inputs=[dict(kind='node', ordinal=1)]))
            array(graph[2]['nodes']).append(excluded)
            graph[-1:-1] = [dict(graph[4], semantic_node_ordinal=2, execution_class='EXCLUDED')]
            graph[-1].update(expected_node_count=3, executed_node_count=3)
            for sequence, row in enumerate(graph): row['sequence'] = sequence
            write_rows(files.graph, graph)
        refresh_proofs(self.inputs)
        summary = reconstruct(self.inputs, self.outputs)
        self.assertEqual(object_value(summary['execution_node_counts'])['EXCLUDED'], 1)
        self.assertEqual(summary['execution_node_count'], 4)
        self.assertEqual(summary['logical_operation_container_count'], 3)

    def test_official_cli_is_byte_deterministic(self):
        reconstruct(self.inputs, self.outputs)
        args = [sys.executable, '-B', '-m', 'sim.cycle.reconstruct']
        for source, files in (('full-cpu', self.inputs.full_cpu), ('potal', self.inputs.potal)):
            for name, path in (('log', files.log), ('graph', files.graph), ('provenance', files.provenance)):
                args += ['--'+source+'-'+name, str(path)]
        args += ['--npu-trace', str(self.inputs.npu.trace), '--npu-results', str(self.inputs.npu.results),
                 '--library', str(self.inputs.npu.artifacts.library), '--cycle-certificate', str(self.inputs.npu.artifacts.certificate),
                 '--output', str(self.root/'again.jsonl'), '--summary', str(self.root/'again.json')]
        result = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root/'again.jsonl').read_bytes(), self.outputs.results.read_bytes())
        self.assertEqual((self.root/'again.json').read_bytes(), self.outputs.summary.read_bytes())

    def test_reconstruction_rejects_input_replaced_after_early_parse(self):
        source = self.inputs.full_cpu.graph
        original_read_manifest = reconstruct_module.read_manifest
        replaced = False

        def read_manifest(path: Path):
            nonlocal replaced
            manifest = original_read_manifest(path)
            if not replaced:
                replaced = True
                replacement = self.root/'graph-replacement.jsonl'
                shutil.copyfile(source, replacement)
                os.replace(replacement, source)
            return manifest

        with mock.patch.object(reconstruct_module, 'read_manifest', side_effect=read_manifest):
            with self.assertRaisesRegex(ValueError, 'input changed during reconstruction'):
                reconstruct(self.inputs, self.outputs)
        self.assertFalse(self.outputs.results.exists() or self.outputs.summary.exists())


if __name__ == '__main__':
    unittest.main()
