from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sim.cycle.execution_adapter import AdapterFiles
from sim.tests.cycle.test_execution_cli import fixture_files


class StreamingTests(unittest.TestCase):
    def test_sqlite_ir_when_actual_adapter_fixture_used(self) -> None:
        from sim.cycle.execution_stream import adapt_stream
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_files(root)
            summary = adapt_stream(AdapterFiles(root / 'dataset.jsonl', root / 'lifecycle.json', root / 'npu.jsonl'),
                                   root / 'execution.sqlite')
            self.assertEqual(summary['input_record_count'], 4)
            self.assertEqual(summary['node_count'], 6)
            with closing(sqlite3.connect(root / 'execution.sqlite')) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM services').fetchone()[0], 2)
                row = db.execute('SELECT parent FROM edges WHERE node=?', ('operation:b:enter',)).fetchone()
                self.assertEqual(row[0], 'operation:a:exit')

    def test_legacy_cap_when_more_than_100000_services(self) -> None:
        from sim.cycle.execution_stream import adapt_stream
        from sim.cycle.reconstruct_graph import sha256
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_files(root)
            with (root / 'dataset.jsonl').open('a') as stream:
                for index in range(100_001):
                    row = {'kind': 'SERVICE', 'node_id': f'host:{index}', 'operation_node_id': 'operation:b',
                           'node_class': 'POTAL_HOST', 'dependencies': [], 'duration_source': 'POTAL_COLLECTION',
                           'duration': {'worker_id': 0, 'thread_cpu_valid': True, 'thread_cpu_ns': 1}}
                    stream.write(json.dumps(row) + '\n')
            contract = json.loads((root / 'lifecycle.json').read_text())
            contract['worker_resources']['POTAL_COLLECTION:0'] = 'cpu:0'
            contract['dataset_sha256'] = sha256(root / 'dataset.jsonl')
            (root / 'lifecycle.json').write_text(json.dumps(contract))
            result = adapt_stream(AdapterFiles(root / 'dataset.jsonl', root / 'lifecycle.json', root / 'npu.jsonl'),
                                  root / 'execution.sqlite')
            self.assertEqual(result['input_record_count'], 100_005)
            self.assertEqual(result['node_count'], 100_007)
            self.assertEqual(result['service_count'], 100_003)

    def test_cycle_when_dataset_edges_conflict(self) -> None:
        from sim.cycle.execution_stream import adapt_stream
        from sim.cycle.reconstruct_graph import sha256
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_files(root)
            rows = [json.loads(line) for line in (root / 'dataset.jsonl').read_text().splitlines()]
            rows[2]['dependencies'] = ['ordinary:b']
            (root / 'dataset.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
            declaration = json.loads((root / 'lifecycle.json').read_text())
            declaration['dataset_sha256'] = sha256(root / 'dataset.jsonl')
            (root / 'lifecycle.json').write_text(json.dumps(declaration))
            with self.assertRaisesRegex(ValueError, 'cyclic SQLite'):
                adapt_stream(AdapterFiles(root / 'dataset.jsonl', root / 'lifecycle.json', root / 'npu.jsonl'),
                             root / 'execution.sqlite')
            self.assertFalse((root / 'execution.sqlite').exists())


if __name__ == '__main__':
    unittest.main()
