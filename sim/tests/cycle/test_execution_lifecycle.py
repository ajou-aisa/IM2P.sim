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


if __name__ == '__main__':
    unittest.main()
