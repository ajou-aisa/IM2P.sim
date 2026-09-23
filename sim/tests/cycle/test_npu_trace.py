from __future__ import annotations

import copy
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts.gemmini_replay_contract import contract_digest, hardware_contract
from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle import optrace
from sim.cycle.npu_trace_schema import Record, object_value


def records(bits: int = 8, dim: int = 16) -> list[Record]:
    contract = hardware_contract(f'a{bits}w{bits}-d{dim}-hp1')
    call: Record = dict(kind='NPU_CALL', phase_id=0, operation_id=0, node_id=0,
                        parent_id=0, call_id=0, call_kind='FULL', required_work_ids=[])
    rows: list[Record] = [
        dict(kind='RUN', model='fixture', profile=contract['profile'], activation_bits=bits,
             weight_bits=bits, dim=dim, prompt_tokens=256, requested_generated_tokens=5,
             producer_execution_kind='CPU_FUNCTIONAL', actual_rtl_acceptance_in_collection='NOT_APPLICABLE',
             hardware_contract=contract, hardware_contract_sha256=contract['sha256'], collection_scope='prompt_and_generation'),
        dict(kind='PHASE', phase_id=0, phase_kind='prefill', decode_index=None, input_tokens=256),
        dict(call, stage='PREPARE'), dict(call, stage='INVOKE'),
        dict(kind='NPU_WORK', phase_id=0, operation_id=0, node_id=0, parent_id=0, call_id=0, work_id=0,
             layer='blk.0.ffn_down', operation='MUL_MAT', provenance='dense_main', scope='full',
             activation_bits=bits, weight_bits=bits, dim=dim, m=1, n=1, k=3072,
             tile_i_count=1, tile_j_count=1, tile_k_count=3, parent_m=1,
             production_geometry_version=1, row_begin=0, row_count=1, stripe_id=None,
             host_slot=None, original_block_id=None, activation_stride_bytes=3072,
             weight_stride_bytes=1, output_stride_bytes=4, scale_stride_elements=1,
             block_size=32, vector_op=5, output_domain=2, work_context=0,
             source_row_begin=0, source_row_count=1, column_begin=0, group_index=0,
             rmd_raw=False, host_integer_block_multiply=False, hardware_contract_sha256=contract['sha256'],
             required_host_stage_ids=[]),
        dict(call, stage='COMPLETE_REQUIRED', required_work_ids=[0]), dict(call, stage='CONTINUATION'),
        dict(kind='TARGET_OPERATION', phase_id=0, operation_id=0, node_id=0, layer='blk.0.ffn_down',
             operation='MUL_MAT', actual_backend='Gemmini', activation_type='F32', weight_type='Q8_HP1',
             m=1, n=1, k=3072, target_eligible=True, selected_target='TARGET_NPU', reason='selected work',
             status='success', npu_work_count=1),
        dict(kind='RUN_END', status='success', reason='', registered_operation_count=1,
             completed_operation_count=1, target_npu_count=1, target_cpu_count=0,
             unsupported_count=0, excluded_count=0, npu_work_count=1, call_count=1, phase_count=1,
             host_stage_count=0, completed_host_stage_count=0, potal_host_count=0, functional_emulation_count=0),
    ]
    return resequence(rows)


def resequence(rows: list[Record]) -> list[Record]:
    for sequence, row in enumerate(rows):
        row.update(schema='im2p-npu-cycle-trace', version=1, run_id='run-0', collection_run_id=1,
                   run_config_id='workload-0', source_role='POTAL_COLLECTION', sequence=sequence)
        if row['kind'] in ('TARGET_OPERATION', 'NPU_WORK', 'NPU_CALL', 'HOST_STAGE'):
            row.update(semantic_phase_kind='prefill', semantic_decode_index=None,
                       semantic_graph_occurrence=0, semantic_node_ordinal=row['node_id'])
        if row.get('selected_target') == 'TARGET_CPU': row['selected_target'] = 'ORDINARY_CPU'
        if 'target_cpu_count' in row: row['ordinary_cpu_count'] = row.pop('target_cpu_count')
    return rows


def write_trace(path: Path, rows: list[Record]) -> None:
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows))


class NpuTraceTests(unittest.TestCase):
    def consume(self, rows: list[Record]) -> Record:
        from sim.cycle import npu_trace
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'trace.jsonl'; write_trace(path, rows)
            return npu_trace.validate_trace(path)

    def test_all_profiles_and_explicit_work_contract(self):
        for bits in (4, 8):
            for dim in (16, 32, 64):
                self.assertEqual(self.consume(records(bits, dim))['npu_work_count'], 1)

    def test_cpu_schemas_records_and_fields_rejected(self):
        for index, field, value in ((0, 'schema', 'im2p-cycle-sim'), (0, 'schema', 'im2p-production-optrace'),
                                   (4, 'kind', 'CPU_INTERVAL'), (4, 'cpu_cycles', 5), (2, 'start', 1),
                                   (4, 'observed_rtl_elapsed', 50), (4, 'weights', [])):
            rows = records(); rows[index][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): self.consume(rows)
        with self.assertRaises(ValueError): optrace.validate_records(records())

    def test_geometry_profile_slot_and_residual_negatives(self):
        for field, value in (('tile_i_count', 0), ('dim', 32), ('host_slot', 999), ('rmd_raw', True),
                             ('host_integer_block_multiply', True), ('output_stride_bytes', 3), ('parent_m', 2)):
            rows = records(); rows[4][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): self.consume(rows)

    def test_call_order_identity_and_dependencies_rejected(self):
        changes: tuple[tuple[int, str, JsonValue], ...] = (
            (2, 'stage', 'INVOKE'), (3, 'stage', 'CONTINUATION'), (5, 'required_work_ids', []),
            (5, 'required_work_ids', [99]), (5, 'required_work_ids', [0, 0]), (4, 'call_id', 1),
            (4, 'node_id', 2), (4, 'parent_id', 1), (6, 'parent_id', 1), (7, 'node_id', 1))
        for index, field, value in changes:
            rows = records(); rows[index][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): self.consume(rows)
        rows = records(); rows[3], rows[4] = rows[4], rows[3]
        with self.assertRaises(ValueError): self.consume(resequence(rows))

    def test_host_preparation_may_declare_parent_before_first_npu_call(self):
        rows = records()
        stage: Record = dict(kind='HOST_STAGE', phase_id=0, operation_id=0, node_id=0,
                             parent_id=0, call_id=None, host_stage_id=0,
                             execution_class='POTAL_HOST', stage_name='prepare',
                             source_owner='IM2P.sim', source_location='frontend/src/prepare.cpp:prepare',
                             required_work_ids=[], required_host_stage_ids=[])
        rows[2:2] = [dict(stage, event='BEGIN', status='declared'),
                     dict(stage, event='END', status='success')]
        rows[-1].update(host_stage_count=1, completed_host_stage_count=1, potal_host_count=1)
        self.assertEqual(self.consume(resequence(rows))['npu_work_count'], 1)
        for record in rows[2:4]:
            record['parent_id'] = 99
        with self.assertRaisesRegex(ValueError, 'host-declared parent has no NPU call'):
            self.consume(resequence(rows))

    def test_independent_counts_and_stale_phase_rejected(self):
        for index, field, value in ((8, 'registered_operation_count', 2), (8, 'call_count', 2),
                                   (8, 'npu_work_count', 2), (7, 'npu_work_count', 0), (4, 'phase_id', 1),
                                   (7, 'selected_target', 'TARGET_CPU'), (4, 'collection_run_id', 2)):
            rows = records(); rows[index][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): self.consume(rows)
        with self.assertRaises(ValueError): self.consume(records()[:-1])

    def test_duplicate_json_key_rejected(self):
        from sim.cycle import npu_trace
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory)/'trace.jsonl'
            trace.write_text('{"schema":"im2p-npu-cycle-trace","schema":"im2p-npu-cycle-trace"}\n')
            with self.assertRaises(ValueError): npu_trace.validate_trace(trace)

    def test_gzip_trace_input_matches_plain_validation(self):
        from sim.cycle import npu_trace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plain, compressed = root/'trace.jsonl', root/'trace.jsonl.gz'
            write_trace(plain, records())
            with gzip.open(compressed, 'wt', encoding='utf-8') as stream:
                stream.write(plain.read_text())
            self.assertEqual(npu_trace.validate_trace(compressed), npu_trace.validate_trace(plain))

    def test_snapshot_rejects_replaced_input(self):
        from sim.cycle import npu_trace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, replacement = root/'source.jsonl', root/'replacement.jsonl'
            source.write_text('before\n')
            snapshots = npu_trace.snapshot_inputs((source,), root)
            replacement.write_text('before\n')
            os.replace(replacement, source)
            with self.assertRaisesRegex(ValueError, 'input changed during replay'):
                npu_trace.verify_input_snapshots(snapshots, 'replay')

    def test_same_shape_operations_may_interleave(self):
        rows = records(); second = copy.deepcopy(rows[2:8])
        for row in second:
            row['operation_id'] = row['node_id'] = 1
            if 'call_id' in row: row['call_id'] = 1
            if 'parent_id' in row: row['parent_id'] = 1
            if row['kind'] == 'NPU_WORK': row['work_id'] = 1
            if row.get('stage') == 'COMPLETE_REQUIRED': row['required_work_ids'] = [1]
        rows[5:5] = second
        rows[-1].update(registered_operation_count=2, completed_operation_count=2,
                        target_npu_count=2, npu_work_count=2, call_count=2)
        self.assertEqual(self.consume(resequence(rows))['operation_count'], 2)

    def test_cpu_route_and_excluded_operations_need_no_npu_calls(self):
        rows = records(); rows = [rows[0], rows[1], rows[7], rows[8]]
        rows[2].update(selected_target='TARGET_CPU', actual_backend='CPU', npu_work_count=0)
        rows[-1].update(target_npu_count=0, target_cpu_count=1, npu_work_count=0, call_count=0)
        self.assertEqual(self.consume(resequence(rows))['eligible_cpu_operation_count'], 1)
        rows[2].update(selected_target='EXCLUDED', m=0)
        rows[-1].update(ordinary_cpu_count=0, excluded_count=1)
        self.assertEqual(object_value(self.consume(rows)['operation_counts'])['EXCLUDED'], 1)

    def test_host_stage_registry_is_metadata_only_and_complete(self):
        rows = records()
        stage: Record = dict(kind='HOST_STAGE', phase_id=0, operation_id=0, node_id=0,
            parent_id=0, call_id=0, host_stage_id=0, execution_class='POTAL_HOST', stage_name='recompose',
            source_owner='llama.cpp-gemmini', source_location='ggml/src/residual.cpp:recompose',
            required_work_ids=[0], required_host_stage_ids=[])
        rows[7:7] = [dict(stage, event='BEGIN', status='declared'), dict(stage, event='END', status='success')]
        rows[-1].update(host_stage_count=1, completed_host_stage_count=1, potal_host_count=1)
        self.assertEqual(self.consume(resequence(rows))['host_stage_count'], 1)
        for field, value in (('delta', 123), ('host_stage_id', 1), ('stage_name', 'other')):
            broken = copy.deepcopy(rows); broken[8][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): self.consume(broken)

    def test_stripe_call_publication_and_parent_coverage(self):
        rows = records(); rows[4].update(scope='stripe', stripe_id=0, host_slot=0, parent_m=2)
        for row in rows:
            if row['kind'] == 'NPU_CALL': row['call_kind'] = 'STRIPE'
        rows[7]['m'] = 2
        rows.insert(5, dict(rows[3], stage='PUBLISH'))
        with self.assertRaises(ValueError): self.consume(resequence(rows))
        second = copy.deepcopy(rows[2:8])
        for row in second:
            row['call_id'] = 1
            if row['kind'] == 'NPU_WORK': row.update(work_id=1, stripe_id=1, host_slot=1, row_begin=1)
            if row.get('stage') == 'COMPLETE_REQUIRED': row['required_work_ids'] = [1]
        rows[8:8] = second; rows[-2]['npu_work_count'] = 2
        rows[-1].update(npu_work_count=2, call_count=2)
        self.assertEqual(self.consume(resequence(rows))['npu_work_count'], 2)
        for field, value in (('stripe_id', 0), ('row_begin', 0), ('parent_m', 3), ('host_slot', 999)):
            broken = copy.deepcopy(rows); broken[10][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): self.consume(broken)
        del rows[5]
        with self.assertRaises(ValueError): self.consume(resequence(rows))

    def test_residual_compact_and_fence_are_explicit_calls(self):
        rows = records(); rows[4].update(scope='residual_compact', provenance='residual', k=31,
                                        tile_k_count=2, original_block_id=96)
        for row in rows:
            if row['kind'] == 'NPU_CALL': row['call_kind'] = 'RESIDUAL_COMPACT'
        self.assertEqual(self.consume(rows)['residual_work_count'], 1)
        fence = dict(rows[2], call_id=1, call_kind='FENCE', parent_id=None)
        rows[7:7] = [dict(fence, stage='PREPARE'), dict(fence, stage='INVOKE'),
                     dict(fence, stage='COMPLETE_REQUIRED', required_work_ids=[0]),
                     dict(fence, stage='FENCE'), dict(fence, stage='CONTINUATION')]
        rows[-1]['call_count'] = 2
        self.assertEqual(self.consume(resequence(rows))['call_count'], 2)
        del rows[10]
        with self.assertRaises(ValueError): self.consume(resequence(rows))

    @unittest.skipUnless(os.getenv('IM2P_CYCLE_LIBRARY') and os.getenv('IM2P_CYCLE_CERTIFICATE'), 'certified C library required')
    def test_cli_per_work_results_deterministic_and_failure_unpublished(self):
        from sim.cycle import npu_trace
        library, certificate = Path(os.environ['IM2P_CYCLE_LIBRARY']), Path(os.environ['IM2P_CYCLE_CERTIFICATE'])
        artifacts = npu_trace.ReplayArtifacts(library, certificate)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); trace = root/'trace.jsonl'; rows = records()
            second = copy.deepcopy(rows[2:8])
            for row in second:
                row['operation_id'] = row['node_id'] = 1
                if 'call_id' in row: row['call_id'] = row['parent_id'] = 1
                if row['kind'] == 'NPU_WORK': row['work_id'] = 1
                if row.get('stage') == 'COMPLETE_REQUIRED': row['required_work_ids'] = [1]
            rows[-1:-1] = second
            rows[-1].update(registered_operation_count=2, completed_operation_count=2,
                            target_npu_count=2, npu_work_count=2, call_count=2)
            write_trace(trace, resequence(rows))
            os.utime(trace, ns=(1, trace.stat().st_mtime_ns))
            output = npu_trace.ReplayOutputs(root/'result.jsonl', root/'summary.json')
            answer = npu_trace.replay(trace, artifacts, output)
            results = [json.loads(line) for line in output.results.read_text().splitlines()]
            selected = [row for row in rows if row['kind'] == 'NPU_WORK']
            self.assertEqual(len(results), 2)
            for work, original in zip(results, selected, strict=True):
                for key in ('work_id', 'operation_id', 'node_id', 'parent_id', 'call_id', 'stripe_id', 'host_slot'):
                    self.assertEqual(work[key], original[key])
            self.assertEqual(sum(work['modeled']['total_cycles'] for work in results), answer['isolated_cycle_sum'])
            process = subprocess.run([sys.executable, '-B', '-m', 'sim.cycle.npu_trace', str(trace),
                '--library', str(library), '--cycle-certificate', str(certificate),
                '--output', str(root/'again.jsonl'), '--summary', str(root/'again.json')], capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual((root/'again.jsonl').read_bytes(), output.results.read_bytes())
            self.assertEqual((root/'again.json').read_bytes(), output.summary.read_bytes())
            rows[-1]['npu_work_count'] = 99; write_trace(trace, rows)
            failed = npu_trace.ReplayOutputs(root/'failed.jsonl', root/'failed.json')
            with self.assertRaises(ValueError): npu_trace.replay(trace, artifacts, failed)
            self.assertFalse(failed.results.exists() or failed.summary.exists())
            write_trace(trace, records())
            certificate_bad = json.loads(certificate.read_text())
            certificate_bad['model_library_sha256'] = '0'*64
            (root/'bad-certificate.json').write_text(json.dumps(certificate_bad))
            with self.assertRaises(ValueError):
                npu_trace.replay(trace, npu_trace.ReplayArtifacts(library, root/'bad-certificate.json'), failed)
            self.assertFalse(failed.results.exists() or failed.summary.exists())
            rows = records(); altered = object_value(rows[0]['hardware_contract'])
            object_value(altered['facts'])['scale_mapping_revision'] = 'global-fragment-address-v0'
            altered['sha256'] = contract_digest(altered)
            rows[0]['hardware_contract_sha256'] = rows[4]['hardware_contract_sha256'] = altered['sha256']
            write_trace(trace, rows)
            with self.assertRaisesRegex(ValueError, 'hardware lowering contract mismatch'):
                npu_trace.replay(trace, artifacts, failed)
            self.assertFalse(failed.results.exists())

    @unittest.skipUnless(os.getenv('IM2P_CYCLE_LIBRARY') and os.getenv('IM2P_CYCLE_CERTIFICATE'), 'certified C library required')
    def test_replay_rejects_each_replaced_source_after_snapshot(self):
        """A replay must not publish an answer from a source replaced mid-run."""
        from sim.cycle import npu_trace
        library = Path(os.environ['IM2P_CYCLE_LIBRARY'])
        certificate = Path(os.environ['IM2P_CYCLE_CERTIFICATE'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('trace', 'certificate', 'library'):
                with self.subTest(source=name):
                    trace, local_certificate, local_library = root/'trace.jsonl', root/'certificate.json', root/'library.dylib'
                    write_trace(trace, records())
                    shutil.copyfile(certificate, local_certificate)
                    shutil.copyfile(library, local_library)
                    source = {'trace': trace, 'certificate': local_certificate, 'library': local_library}[name]
                    output = npu_trace.ReplayOutputs(root/(name+'-result.jsonl'), root/(name+'-summary.json'))
                    original_estimate = npu_trace.cli.estimate
                    mutated = False

                    def estimate(replay_library: Path, document: Record) -> Record:
                        nonlocal mutated
                        if not mutated:
                            mutated = True
                            replacement = root/(name+'-replacement')
                            shutil.copyfile(source, replacement)
                            os.replace(replacement, source)
                            if name == 'library':
                                self.assertNotEqual(replay_library.resolve(), source.resolve())
                        return original_estimate(replay_library, document)

                    with mock.patch.object(npu_trace.cli, 'estimate', side_effect=estimate):
                        with self.assertRaisesRegex(ValueError, 'input changed during replay'):
                            npu_trace.replay(trace, npu_trace.ReplayArtifacts(local_library, local_certificate), output)
                    self.assertFalse(output.results.exists() or output.summary.exists())


if __name__ == '__main__':
    unittest.main()
