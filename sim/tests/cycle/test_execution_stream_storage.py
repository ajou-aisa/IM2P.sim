from __future__ import annotations

import errno
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from typing import BinaryIO
from unittest import mock

from sim.cycle import execution_stream, input_snapshot
from sim.cycle.execution_adapter import AdapterFiles
from sim.cycle.execution_services import PhaseTable
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.scheduler import Scenario
from sim.cycle.scheduler_sqlite import SqliteScheduleInputs, schedule_sqlite
from sim.tests.cycle.test_execution_cli import fixture_files


class StreamStorageTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('IM2P_LARGE_STORAGE_TEST') == '1', 'opt-in >64 MiB storage workload')
    def test_large_dataset_exceeds_json_64mib_boundary(self) -> None:
        import resource

        # Given: 100001 valid added services with a dataset above 64 MiB.
        with tempfile.TemporaryDirectory(prefix='im2p-large-storage-') as directory:
            root = Path(directory)
            fixture_files(root)
            dataset = root / 'dataset.jsonl'
            contract = root / 'lifecycle.json'
            suffix = 'x' * 450
            count = 100_001
            with dataset.open('a') as stream:
                for index in range(count):
                    row = {'kind': 'SERVICE', 'node_id': f'host:{suffix}:{index:06d}',
                           'operation_node_id': 'operation:b', 'node_class': 'POTAL_HOST', 'dependencies': [],
                           'duration_source': 'POTAL_COLLECTION',
                           'duration': {'worker_id': 0, 'thread_cpu_valid': True, 'thread_cpu_ns': 1}}
                    _ = stream.write(json.dumps(row) + '\n')
            declaration = json.loads(contract.read_text())
            declaration['worker_resources']['POTAL_COLLECTION:0'] = 'cpu:0'
            declaration['dataset_sha256'] = sha256(dataset)
            _ = contract.write_text(json.dumps(declaration))
            dataset_bytes = dataset.stat().st_size
            self.assertGreater(dataset_bytes, 64 * 1024 * 1024)
            with dataset.open() as stream:
                self.assertEqual(sum(1 for _ in stream), count + 4)
            source_digest = sha256(dataset)
            ir_path = root / 'execution.sqlite'
            schedule_path = root / 'schedule.sqlite'
            # When: streaming IR adaptation and SQLite scheduling complete.
            started = time.perf_counter()
            ir = execution_stream.adapt_stream(AdapterFiles(dataset, contract, root / 'npu.jsonl'), ir_path)
            adapted = time.perf_counter()
            schedule = schedule_sqlite(ir_path, schedule_path,
                SqliteScheduleInputs(PhaseTable(1, {}), Scenario(1_000_000_000, 'SYNTHETIC')))
            scheduled = time.perf_counter()
            # Then: every input and scheduled node is represented without the JSON finite cap.
            self.assertEqual(ir['input_record_count'], count + 4)
            self.assertEqual(ir['dataset_sha256'], source_digest)
            self.assertEqual(ir['node_count'], count + 6)
            self.assertEqual(schedule['node_count'], count + 6)
            self.assertEqual(schedule['input_sqlite_sha256'], sha256(ir_path))
            last = f'host:{suffix}:{count - 1:06d}'
            with closing(sqlite3.connect(ir_path)) as database:
                self.assertEqual(database.execute('SELECT COUNT(*) FROM nodes').fetchone()[0], count + 6)
                self.assertIsNotNone(database.execute('SELECT 1 FROM nodes WHERE identity=?', (last,)).fetchone())
            with closing(sqlite3.connect(schedule_path)) as database:
                self.assertEqual(database.execute('SELECT COUNT(*) FROM results').fetchone()[0], count + 6)
                self.assertIsNotNone(database.execute('SELECT 1 FROM results WHERE identity=?', (last,)).fetchone())
            peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
            print(json.dumps({'dataset_bytes': dataset_bytes, 'dataset_sha256': source_digest,
                              'input_records': count + 4, 'ir_nodes': count + 6,
                              'ir_bytes': ir_path.stat().st_size, 'schedule_bytes': schedule_path.stat().st_size,
                              'adapt_seconds': round(adapted - started, 3),
                              'schedule_seconds': round(scheduled - adapted, 3),
                              'peak_rss_bytes': peak_rss}, sort_keys=True))

    def test_small_adapter_checks_capacity_before_writing(self) -> None:
        # Given: a valid small dataset but space only for the fixed reserve.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_files(root)
            files = AdapterFiles(root / 'dataset.jsonl', root / 'lifecycle.json', root / 'npu.jsonl')
            output = root / 'execution.sqlite'
            usage = shutil.disk_usage(root)._replace(free=input_snapshot.COPY_RESERVE_BYTES)
            # When: streaming adaptation starts.
            with mock.patch.object(shutil, 'disk_usage', return_value=usage), self.assertRaises(OSError) as failure:
                execution_stream.adapt_stream(files, output)
            # Then: input-sized SQLite/index/journal/temp budget is enforced before publication.
            self.assertEqual(failure.exception.errno, errno.ENOSPC)
            self.assertFalse(output.exists())
            self.assertFalse(list(root.glob('execution-store-*')))

    def test_sqlite_failure_closes_connection_and_keeps_output_absent(self) -> None:
        # Given: a valid fixture and a SQLite failure after the staged connection opens.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_files(root)
            output = root / 'execution.sqlite'
            opened: list[sqlite3.Connection] = []

            def fail_validation(store: execution_stream.ExecutionStore) -> None:
                opened.append(store.db)
                raise sqlite3.OperationalError('database or disk is full')

            # When: validation fails before the final commit.
            with mock.patch.object(execution_stream.ExecutionStore, 'validate', fail_validation), \
                 self.assertRaises(sqlite3.OperationalError):
                execution_stream.adapt_stream(
                    AdapterFiles(root / 'dataset.jsonl', root / 'lifecycle.json', root / 'npu.jsonl'), output)
            # Then: no artifact or open connection survives.
            self.assertFalse(output.exists())
            self.assertFalse(list(root.glob('execution-store-*')))
            self.assertEqual(len(opened), 1)
            with self.assertRaises(sqlite3.ProgrammingError):
                opened[0].execute('SELECT 1')

    def test_snapshot_preflight_runs_before_clone(self) -> None:
        # Given: a source larger than the available bytes above the reserve.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.write_bytes(b'budget')
            usage = shutil.disk_usage(root)._replace(free=input_snapshot.COPY_RESERVE_BYTES)
            # When: a snapshot is requested, including the clone-capable path.
            with source.open('rb') as stream, \
                 mock.patch.object(shutil, 'disk_usage', return_value=usage), \
                 mock.patch.object(input_snapshot, '_clone_fd') as clone, \
                 self.assertRaises(OSError) as failure:
                input_snapshot.snapshot_file(stream.fileno(), root / 'copy')
            # Then: no clone or published snapshot consumes the short disk.
            self.assertEqual(failure.exception.errno, errno.ENOSPC)
            clone.assert_not_called()
            self.assertFalse((root / 'copy').exists())

    def test_snapshot_copy_error_keeps_destination_absent(self) -> None:
        # Given: an error after a partial staged copy has been written.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.write_bytes(b'original')

            def fail_copy(reader: BinaryIO, writer: BinaryIO, size: int) -> None:
                del reader, size
                writer.write(b'partial')
                raise OSError(errno.ENOSPC, 'disk full')

            # When: the copy fails during snapshot construction.
            with source.open('rb') as stream, \
                 mock.patch.object(input_snapshot, '_clone_fd', return_value=False), \
                 mock.patch.object(shutil, 'copyfileobj', fail_copy), \
                 self.assertRaises(OSError) as failure:
                input_snapshot.snapshot_file(stream.fileno(), root / 'copy')
            # Then: neither the destination nor staging files remain.
            self.assertEqual(failure.exception.errno, errno.ENOSPC)
            self.assertEqual(list(root.iterdir()), [source])

    def test_snapshot_fd_error_keeps_destination_absent(self) -> None:
        # Given: verification raises an EMFILE error after the staged copy.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.write_bytes(b'original')
            # When: hashing the staged snapshot begins.
            with source.open('rb') as stream, \
                 mock.patch.object(input_snapshot, '_clone_fd', return_value=False), \
                 mock.patch.object(input_snapshot, '_digest', side_effect=OSError(errno.EMFILE, 'too many files')), \
                 self.assertRaises(OSError) as failure:
                input_snapshot.snapshot_file(stream.fileno(), root / 'copy')
            # Then: the snapshot remains unpublished.
            self.assertEqual(failure.exception.errno, errno.EMFILE)
            self.assertEqual(list(root.iterdir()), [source])


if __name__ == '__main__':
    unittest.main()
