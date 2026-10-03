from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sim.cycle.execution_adapter import AdapterFiles
from sim.cycle.execution_stream import adapt_stream
from sim.cycle.npu_trace_schema import Record, object_value
from sim.cycle.reconstruct_graph import sha256
from sim.tests.cycle.test_execution_adapter import contract


def fixture(root: Path, pipeline: str | None = None) -> AdapterFiles:
    rows: list[Record] = [{'kind': 'OPERATION_CONTAINER', 'node_id': 'operation:a', 'dependencies': [],
                          'phase': {'kind': 'prefill', 'decode_index': None}}]
    for call, kind in ((0, 'FENCE'), (1, 'FULL')):
        for stage in ('PREPARE', 'INVOKE'):
            rows.append({'kind': 'CALL_BOUNDARY', 'node_id': f'call:{call}:{stage}', 'call_id': call,
                         'call_kind': kind, 'stage': stage, 'operation_node_id': 'operation:a',
                         'node_class': 'TARGET_NPU', 'is_execution_node': False,
                         'dependencies': [] if stage == 'PREPARE' else [f'call:{call}:PREPARE']})
    rows.append({'kind': 'SERVICE', 'node_class': 'TARGET_NPU', 'node_id': 'npu:0',
                 'operation_node_id': 'operation:a', 'dependencies': ['call:1:INVOKE']})
    for call, kind in ((1, 'FULL'), (0, 'FENCE')):
        stages = ('COMPLETE_REQUIRED', 'CONTINUATION') if call == 1 else ('COMPLETE_REQUIRED', 'FENCE', 'CONTINUATION')
        previous = f'call:{call}:INVOKE'
        for stage in stages:
            dependencies = [previous] + (['npu:0'] if stage == 'COMPLETE_REQUIRED' else [])
            rows.append({'kind': 'CALL_BOUNDARY', 'node_id': f'call:{call}:{stage}', 'call_id': call,
                         'call_kind': kind, 'stage': stage, 'operation_node_id': 'operation:a',
                         'node_class': 'TARGET_NPU', 'is_execution_node': False,
                         'dependencies': [value for value in dependencies]})
            previous = f'call:{call}:{stage}'
    if pipeline == 'STRIPE':
        rows[1]['call_kind'] = 'STRIPE'
    if pipeline == 'PUBLISH':
        rows[1]['stage'] = 'PUBLISH'
    result: Record = {'schema': 'im2p-npu-cycle-result', 'cycle_model_validation': 'CURRENT_CERTIFIED',
        'scope': 'stripe' if pipeline == 'WORK_SCOPE' else 'full', 'host_slot': None, 'work_id': 0, 'call_id': 1,
        'profile': 'a8w8-d16-hp1', 'run_view_sha256': 'fixture'}
    (root / 'dataset.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    (root / 'npu.jsonl').write_text(json.dumps(result) + '\n')
    declaration = contract()
    declaration.update(entry_dependencies={'operation:a': []}, call_slots={'1': None}, submission_order=['npu:0'],
                       dataset_sha256=sha256(root / 'dataset.jsonl'), npu_results_sha256=sha256(root / 'npu.jsonl'))
    (root / 'lifecycle.json').write_text(json.dumps(declaration))
    return AdapterFiles(root / 'dataset.jsonl', root / 'lifecycle.json', root / 'npu.jsonl')


class FullFenceTests(unittest.TestCase):
    def test_full_fence_when_shared_frontend_joins_main_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = adapt_stream(fixture(root), root / 'execution.sqlite')
            self.assertEqual(result['service_count'], 1)
            self.assertEqual(object_value(result['class_counts'])['TARGET_NPU'], 1)
            self.assertEqual(object_value(result['class_counts'])['STRUCTURAL'], 9)
            with closing(sqlite3.connect(root / 'execution.sqlite')) as database:
                self.assertEqual(database.execute("SELECT COUNT(*) FROM nodes WHERE identity LIKE 'call:0:%'").fetchone()[0], 5)
                required = database.execute('SELECT parent,milestone FROM edges WHERE node=?', ('call:0:COMPLETE_REQUIRED',)).fetchall()
                fence = database.execute('SELECT parent,milestone FROM edges WHERE node=?', ('call:0:FENCE',)).fetchall()
                self.assertIn(('npu:0', 'RESULT_READY'), required)
                self.assertIn(('call:0:COMPLETE_REQUIRED', 'RESULT_READY'), fence)
                self.assertNotIn(('npu:0', 'RESOURCE_READY'), required)

    def test_pipeline_when_real_discriminators_present_still_rejects(self) -> None:
        for discriminator in ('STRIPE', 'PUBLISH', 'WORK_SCOPE'):
            with self.subTest(discriminator=discriminator), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with self.assertRaises(ValueError):
                    adapt_stream(fixture(root, discriminator), root / 'execution.sqlite')
                self.assertFalse((root / 'execution.sqlite').exists())

    def test_actual_unused_work_when_fence_ownership_is_structural_still_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = fixture(root)
            extra = json.loads(files.npu_results.read_text())
            extra.update(work_id=99, call_id=98)
            with files.npu_results.open('a') as stream:
                stream.write(json.dumps(extra) + '\n')
            declaration = json.loads(files.lifecycle.read_text())
            declaration['call_slots']['98'] = None
            declaration['submission_order'].append('npu:99')
            declaration['npu_results_sha256'] = sha256(files.npu_results)
            files.lifecycle.write_text(json.dumps(declaration))
            with self.assertRaisesRegex(ValueError, 'unused NPU service results'):
                adapt_stream(files, root / 'execution.sqlite')
            self.assertFalse((root / 'execution.sqlite').exists())


if __name__ == '__main__':
    unittest.main()
