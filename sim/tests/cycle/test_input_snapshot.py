from __future__ import annotations

import errno
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from sim.cycle.npu_trace import snapshot_inputs, verify_input_snapshots


class SnapshotTests(unittest.TestCase):
    def test_snapshot_when_source_mutated_is_independent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.bin'
            source.write_bytes(b'original' * 8192)
            captured = snapshot_inputs((source,), root)[0]
            self.assertEqual(captured.snapshot.read_bytes(), source.read_bytes())
            self.assertNotEqual(captured.snapshot.stat().st_ino, source.stat().st_ino)
            source.write_bytes(b'changed!')
            self.assertEqual(captured.snapshot.read_bytes(), b'original' * 8192)
            with self.assertRaisesRegex(ValueError, 'input changed'):
                verify_input_snapshots((captured,), 'test')

    def test_existing_destination_when_snapshot_requested_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'source.bin').write_bytes(b'new')
            destination = root / '00-source.bin'
            destination.write_bytes(b'owned by user')
            with self.assertRaises(FileExistsError):
                snapshot_inputs((root / 'source.bin',), root)
            self.assertEqual(destination.read_bytes(), b'owned by user')

    @unittest.skipUnless(sys.platform == 'darwin', 'native APFS clone probe')
    def test_darwin_when_clone_available_uses_cow(self) -> None:
        from sim.cycle.input_snapshot import snapshot_file
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.write_bytes(b'bytes' * 8192)
            with source.open('rb') as stream:
                method = snapshot_file(stream.fileno(), root / 'copy')
            self.assertEqual(method, 'DARWIN_FCLONEFILEAT')
            self.assertEqual((root / 'copy').read_bytes(), source.read_bytes())

    def test_unsupported_clone_when_space_sufficient_falls_back(self) -> None:
        from sim.cycle import input_snapshot
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(input_snapshot, '_clone_fd', return_value=False):
            root = Path(directory)
            source = root / 'source'
            source.write_bytes(b'fallback')
            with source.open('rb') as stream:
                method = input_snapshot.snapshot_file(stream.fileno(), root / 'copy')
            self.assertEqual(method, 'EXCLUSIVE_COPY')
            self.assertEqual((root / 'copy').read_bytes(), b'fallback')

    def test_unsupported_clone_when_budget_insufficient_fails_before_copy(self) -> None:
        from sim.cycle import input_snapshot
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.write_bytes(b'budget')
            usage = shutil.disk_usage(root)._replace(free=0)
            with source.open('rb') as stream, mock.patch.object(input_snapshot, '_clone_fd', return_value=False), \
                    mock.patch.object(input_snapshot.shutil, 'disk_usage', return_value=usage):
                with self.assertRaisesRegex(OSError, 'snapshot copy budget'):
                    input_snapshot.snapshot_file(stream.fileno(), root / 'copy')
            self.assertFalse((root / 'copy').exists())

    def test_clone_io_failure_when_not_unsupported_never_falls_back(self) -> None:
        from sim.cycle import input_snapshot
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.write_bytes(b'error')
            with source.open('rb') as stream, mock.patch.object(input_snapshot, '_clone_fd', side_effect=OSError(errno.EIO, 'I/O failure')):
                with self.assertRaises(OSError):
                    input_snapshot.snapshot_file(stream.fileno(), root / 'copy')
            self.assertFalse((root / 'copy').exists())

    def test_corrupted_clone_when_size_matches_hash_still_rejects(self) -> None:
        from sim.cycle import input_snapshot
        def corrupted(_descriptor: int, destination: Path) -> bool:
            destination.write_bytes(b'corrupt!')
            return True
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.write_bytes(b'original')
            with source.open('rb') as stream, mock.patch.object(input_snapshot, '_clone_fd', side_effect=corrupted):
                with self.assertRaisesRegex(OSError, 'byte identity mismatch'):
                    input_snapshot.snapshot_file(stream.fileno(), root / 'copy')

    def test_python310_when_file_digest_unavailable_still_snapshots(self) -> None:
        from sim.cycle import input_snapshot
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.write_bytes(b'python310')
            with source.open('rb') as stream, mock.patch.object(input_snapshot.hashlib, 'file_digest', None, create=True):
                input_snapshot.snapshot_file(stream.fileno(), root / 'copy')
            self.assertEqual((root / 'copy').read_bytes(), b'python310')


if __name__ == '__main__':
    unittest.main()
