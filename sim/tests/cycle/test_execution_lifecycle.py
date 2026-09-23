from __future__ import annotations

import copy
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from sim.cycle.execution_ir import ExecutionError
from sim.cycle.execution_lifecycle import project_lifecycle, token_fingerprint
from sim.cycle.npu_trace_schema import Record, object_value
from sim.cycle.reconstruct_graph import array, json_records, read_manifest
from sim.tests.cycle.test_reconstruct import graph_records, write_rows


def paired_graphs() -> list[Record]:
    rows = graph_records('POTAL_COLLECTION')
    object_value(rows[0]['producer'])['git_commit'] = 'candidate'
    object_value(rows[0]['workload'])['generated_tokens'] = 2
    following = copy.deepcopy(rows[1:5])
    for row in following:
        if 'phase_kind' in row:
            row.update(phase_kind='decode', decode_index=0)
        if 'semantic_phase_kind' in row:
            row.update(semantic_phase_kind='decode', semantic_decode_index=0)
        if row['kind'] == 'PHASE':
            row.update(input_tokens=1, token_fingerprint=token_fingerprint(41))
    rows[-1:-1] = following
    rows[-1].update(expected_node_count=4, executed_node_count=4, graph_count=2)
    for index, row in enumerate(rows):
        row['sequence'] = index
    return rows


@unittest.skipUnless('POTAL_LIFECYCLE_FIXTURE' in os.environ, 'compiled actual producer recorder required')
class ProducerLifecycleTests(unittest.TestCase):
    def fixture(self, root: Path):
        subprocess.run([os.environ['POTAL_LIFECYCLE_FIXTURE'], str(root / 'lifecycle.jsonl')],
                       check=True, capture_output=True, timeout=10)
        write_rows(root / 'graph.jsonl', paired_graphs())
        return list(json_records(root / 'lifecycle.jsonl')), read_manifest(root / 'graph.jsonl')

    def test_actual_producer_when_phase_dispatch_sample_records_join(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events, graph = self.fixture(Path(directory))
            result = project_lifecycle(events, graph)
            self.assertEqual(len(result.entry_dependencies), 4)
            self.assertEqual(result.expected_samples, 2)
            self.assertEqual(len(array(result.application_steps[0]['next_decode_entries'])), 2)
            self.assertEqual(result.application_steps[-1]['next_decode_entries'], [])

    def test_changed_sample_when_next_decode_trajectory_differs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events, graph = self.fixture(Path(directory))
            events[4]['token_id'] = 99
            with self.assertRaisesRegex(ExecutionError, 'trajectory mismatch'):
                project_lifecycle(events, graph)

    def test_incomplete_graph_when_producer_window_shrinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events, graph = self.fixture(Path(directory))
            events[3]['graph_end'] = 0
            with self.assertRaisesRegex(ExecutionError, 'dispatched semantic graph'):
                project_lifecycle(events, graph)

    def test_forced_cpu_when_potal_application_projection_requested(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run([os.environ['POTAL_LIFECYCLE_FIXTURE'], str(root / 'free.jsonl'), str(root / 'forced.jsonl')],
                           check=True, capture_output=True, timeout=10)
            write_rows(root / 'graph.jsonl', paired_graphs())
            with self.assertRaisesRegex(ExecutionError, 'lifecycle framing'):
                project_lifecycle(json_records(root / 'forced.jsonl'), read_manifest(root / 'graph.jsonl'))


def forced_graphs() -> list[Record]:
    rows = graph_records('FULL_CPU')
    object_value(rows[0]['producer']).update(git_commit='candidate', execution_kind='FORCED_CPU_COST_ONLY', trajectory_source='POTAL')
    object_value(rows[0]['workload'])['generated_tokens'] = 128
    template = copy.deepcopy(rows[1:5])
    for index in range(127):
        following = copy.deepcopy(template)
        for row in following:
            if 'phase_kind' in row:
                row.update(phase_kind='decode', decode_index=index)
            if 'semantic_phase_kind' in row:
                row.update(semantic_phase_kind='decode', semantic_decode_index=index)
            if row['kind'] == 'PHASE':
                row.update(input_tokens=1, token_fingerprint=token_fingerprint(100 + index))
        rows[-1:-1] = following
    rows[-1].update(expected_node_count=256, executed_node_count=256, graph_count=128)
    for index, row in enumerate(rows):
        row['sequence'] = index
    return rows


@unittest.skipUnless('POTAL_LIFECYCLE_FIXTURE' in os.environ, 'compiled forced producer recorder required')
class ForcedLifecycleTests(unittest.TestCase):
    def test_exact_128_when_cost_only_trajectory_verified(self) -> None:
        from sim.cycle.execution_lifecycle import validate_forced_lifecycle
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run([os.environ['POTAL_LIFECYCLE_FIXTURE'], str(root / 'free.jsonl'), str(root / 'forced.jsonl')],
                           check=True, capture_output=True, timeout=10)
            write_rows(root / 'graph.jsonl', forced_graphs())
            tokens = tuple(range(100, 228))
            result = validate_forced_lifecycle(json_records(root / 'forced.jsonl'), read_manifest(root / 'graph.jsonl'), tokens)
            self.assertEqual(result, tokens)

    def test_changed_last_token_when_all_128_ids_must_match(self) -> None:
        from sim.cycle.execution_lifecycle import validate_forced_lifecycle
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run([os.environ['POTAL_LIFECYCLE_FIXTURE'], str(root / 'free.jsonl'), str(root / 'forced.jsonl')],
                           check=True, capture_output=True, timeout=10)
            write_rows(root / 'graph.jsonl', forced_graphs())
            tokens = tuple(range(100, 227)) + (999,)
            with self.assertRaisesRegex(ExecutionError, 'forced token trajectory'):
                validate_forced_lifecycle(json_records(root / 'forced.jsonl'), read_manifest(root / 'graph.jsonl'), tokens)


class PipelineLifecycleTests(unittest.TestCase):
    def test_pipeline_owners_are_preserved_with_source_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rows(root / 'graph.jsonl', paired_graphs())
            graph = read_manifest(root / 'graph.jsonl')
            events: list[Record] = [
                {'kind': 'RUN', 'source_role': 'potal_collection', 'source_commit': 'candidate',
                 'expected_samples': 2, 'execution_policy': 'blocking-llama-decode-synchronize-v1',
                 'graph_policy': 'semantic-session-completes-before-next-graph-v1',
                 'operation_exit_policy': 'ALL_MEMBER_COMPLETIONS',
                 'target_mode_scope': 'STRIPE_PIPELINE_ONLY',
                 'actual_rtl_acceptance_in_collection': 'NOT_APPLICABLE',
                 'producer_ownership_source': 'CPU_FUNCTIONAL',
                 'workspace_slot_domain': 'EXSIA_SCRATCH',
                 'target_npu_slot_domain': 'UNDECLARED',
                 'requires_binary_manifest_binding': True},
                {'kind': 'PHASE', 'phase_kind': 'prefill', 'decode_index': None,
                 'graph_begin': 0, 'phase_ordinal': 0},
                {'kind': 'REQUEST_START', 'request_id': 0, 'phase_kind': 'prefill',
                 'decode_index': None, 'graph_begin': 0},
                {'kind': 'PREFILL_BATCH_READY', 'batch_index': 0, 'dispatch_id': 0,
                 'graph_begin': 0, 'phase_kind': 'prefill', 'decode_index': None},
                {'kind': 'DISPATCH_BEGIN', 'dispatch_id': 0, 'graph_begin': 0,
                 'phase_kind': 'prefill', 'decode_index': None},
                {'kind': 'DISPATCH_END', 'dispatch_id': 0, 'graph_end': 1, 'status': 0,
                 'synchronized': True, 'phase_kind': 'prefill', 'decode_index': None},
                {'kind': 'SAMPLE', 'sample_index': 0, 'token_id': 41, 'graph_end': 1,
                 'dispatch_id': 0, 'token_ready': True, 'phase_kind': 'prefill', 'decode_index': None},
                {'kind': 'PHASE', 'phase_kind': 'decode', 'decode_index': 0,
                 'graph_begin': 1, 'phase_ordinal': 1},
                {'kind': 'DISPATCH_BEGIN', 'dispatch_id': 1, 'graph_begin': 1,
                 'phase_kind': 'decode', 'decode_index': 0},
                {'kind': 'DISPATCH_END', 'dispatch_id': 1, 'graph_end': 2, 'status': 0,
                 'synchronized': True, 'phase_kind': 'decode', 'decode_index': 0},
                {'kind': 'SAMPLE', 'sample_index': 1, 'token_id': 42, 'graph_end': 2,
                 'dispatch_id': 1, 'token_ready': True, 'phase_kind': 'decode', 'decode_index': 0},
                {'kind': 'PIPELINE_PARENT', 'phase_id': 0, 'operation_id': 5, 'parent_id': 3,
                 'required_work_ids': [10], 'production_geometry_version': 1,
                 'activation_bits': 8, 'weight_bits': 8, 'dim': 16,
                 'parent_m': 3, 'n': 4, 'k': 32, 'tile_i_count': 1,
                 'tile_j_count': 1, 'tile_k_count': 1, 'scope': 'STREAM',
                 'fence_call_id': 11, 'fence_required_work_ids': [10],
                 'residual_bindings': []},
                {'kind': 'PIPELINE_OWNER', 'producer_sequence': 0, 'phase_id': 0,
                 'operation_id': 5, 'parent_id': 3, 'work_id': 10,
                 'producer_run_id': 42, 'stripe_id': 0, 'workspace_slot': 0,
                 'target_npu_slot': None, 'row_begin': 0, 'row_end': 3,
                 'resource': 'FRONTEND_QUEUE', 'transition': 'ENQUEUE',
                 'required_work_ids': [], 'required_call_ids': [],
                 'observed_call_id': None, 'source_owner': 'IM2P.sim',
                 'rmd_packet': False, 'direct_residual': False,
                 'source_location': 'test-producer'},
                {'kind': 'RUN_END', 'success': True, 'samples': 2,
                 'phases': 2, 'dispatches': 2, 'graphs': 2},
            ]
            for sequence, event in enumerate(events):
                event.update(schema='potal-execution-lifecycle', version=3, sequence=sequence)
            projection = project_lifecycle(events, graph)
            self.assertEqual(projection.pipeline_parents[0]['parent_id'], 3)
            self.assertEqual(projection.pipeline_owners[0]['producer_sequence'], 0)
            from sim.cycle.execution_lifecycle_cli import producer_payload
            from sim.cycle.execution_pipeline_contract import OWNER_FIELDS, PARENT_FIELDS
            self.assertEqual(set(producer_payload(projection.pipeline_parents[0])), PARENT_FIELDS)
            self.assertEqual(set(producer_payload(projection.pipeline_owners[0])), OWNER_FIELDS)
            self.assertEqual(projection.prefill_steps, [{'batch_index': 0, 'dispatch_id': 0, 'graph_begin': 0}])
            self.assertEqual(projection.expected_samples, 2)
            missing_start = [dict(event) for event in events if event['kind'] != 'REQUEST_START']
            for sequence, event in enumerate(missing_start):
                event['sequence'] = sequence
            with self.assertRaisesRegex(ValueError, 'preparation boundary'):
                project_lifecycle(missing_start, graph)


if __name__ == '__main__':
    unittest.main()
