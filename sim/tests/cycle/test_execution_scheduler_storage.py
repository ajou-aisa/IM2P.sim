from __future__ import annotations

import errno
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sim.cycle import input_snapshot, scheduler_sqlite
from sim.cycle.execution_ir import ExecutionIR, Kind, ServiceId
from sim.cycle.execution_services import PhaseTable, Services
from sim.cycle.scheduler import Scenario, ScheduleInputs
from sim.tests.cycle.test_execution_scheduler import cpu, node
from sim.tests.cycle.test_scheduler_sqlite import write_store


class SqliteStorageTests(unittest.TestCase):
    def make_source(self, root: Path) -> Path:
        source = root / 'input.sqlite'
        inputs = ScheduleInputs(ExecutionIR((node('a', Kind.CPU),), 'SYNTHETIC', 'test'),
                                Services({ServiceId('a'): cpu('a', 1)}, {}), PhaseTable(1, {}))
        write_store(source, inputs)
        return source

    def test_small_schedule_checks_source_sized_budget_before_snapshot(self) -> None:
        # Given: a valid tiny input and free space just above the old fixed threshold.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.make_source(root)
            output = root / 'schedule.sqlite'
            usage = shutil.disk_usage(root)._replace(free=input_snapshot.COPY_RESERVE_BYTES + 1)
            # When: scheduling begins.
            with mock.patch.object(shutil, 'disk_usage', return_value=usage), \
                 mock.patch.object(scheduler_sqlite, 'snapshot_inputs') as snapshots, \
                 self.assertRaises(OSError) as failure:
                scheduler_sqlite.schedule_sqlite(source, output,
                    scheduler_sqlite.SqliteScheduleInputs(PhaseTable(1, {}), Scenario(1_000_000_000, 'SYNTHETIC')))
            # Then: the source copy and SQLite growth are budgeted before staging.
            self.assertEqual(failure.exception.errno, errno.ENOSPC)
            snapshots.assert_not_called()
            self.assertFalse(output.exists())
            self.assertFalse(list(root.glob('schedule-sqlite-*')))

    def test_staged_sqlite_errors_and_timeout_close_connection(self) -> None:
        # Given: failures during the staged schedule write.
        for failure in (sqlite3.OperationalError('database or disk is full'), TimeoutError('deadline')):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = self.make_source(root)
                output = root / 'schedule.sqlite'
                # When: the scheduler raises after opening SQLite.
                with mock.patch.object(scheduler_sqlite, '_run', side_effect=failure) as runner, \
                     self.assertRaises(type(failure)):
                    scheduler_sqlite.schedule_sqlite(source, output,
                        scheduler_sqlite.SqliteScheduleInputs(PhaseTable(1, {}), Scenario(1_000_000_000, 'SYNTHETIC')))
                # Then: the output is absent and all staged SQLite handles are closed.
                self.assertFalse(output.exists())
                self.assertFalse(list(root.glob('schedule-sqlite-*')))
                self.assertIsNotNone(runner.call_args)
                self.assertIsInstance(runner.call_args.args[0], sqlite3.Connection)
                with self.assertRaises(sqlite3.ProgrammingError):
                    runner.call_args.args[0].execute('SELECT 1')

    def test_verification_checks_temp_database_budget(self) -> None:
        # Given: a valid published schedule and insufficient temp filesystem space.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.make_source(root)
            output = root / 'schedule.sqlite'
            inputs = scheduler_sqlite.SqliteScheduleInputs(PhaseTable(1, {}), Scenario(1_000_000_000, 'SYNTHETIC'))
            scheduler_sqlite.schedule_sqlite(source, output, inputs)
            original = output.read_bytes()
            usage = shutil.disk_usage(root)._replace(free=input_snapshot.COPY_RESERVE_BYTES)
            # When: verification tries to create its temporary state database.
            with mock.patch.object(shutil, 'disk_usage', return_value=usage), self.assertRaises(OSError) as failure:
                scheduler_sqlite.verify_schedule_sqlite(source, output, inputs)
            # Then: verification refuses the write and preserves the published artifact.
            self.assertEqual(failure.exception.errno, errno.ENOSPC)
            self.assertEqual(output.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
