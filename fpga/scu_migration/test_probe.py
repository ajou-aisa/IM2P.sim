#!/usr/bin/env python3
"""Device-free unit checks. Temporary files are test inputs, not device nodes."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location('scu_migration_probe', Path(__file__).with_name('probe.py'))
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class ProbeTests(unittest.TestCase):
    def test_import_and_help_do_not_collect_or_execute(self):
        with patch.object(probe.subprocess, 'run', side_effect=AssertionError('command on import')):
            imported = importlib.util.module_from_spec(SPEC)
            SPEC.loader.exec_module(imported)
            with patch.object(imported, 'collect', side_effect=AssertionError('collection on help')):
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as stopped:
                    imported.main(['--help'])
                self.assertEqual(stopped.exception.code, 0)

    def test_explicit_compile_gates_before_collection(self):
        for args in (['--compile-probe'], ['--run-native'], ['--out', '/tmp/not-created'],
                     ['--compile-probe', '--cxx', 'c++', '--out', '/tmp/not-created']):
            with patch.object(probe, 'collect', side_effect=AssertionError('premature collection')):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
                    probe.main(args)
                self.assertEqual(stopped.exception.code, 2)

    def test_command_preserves_failed_and_timeout_output(self):
        completed = subprocess.CompletedProcess(['tool', '--version'], 3, 'raw stdout\n', 'raw stderr\n')
        with patch.object(probe.subprocess, 'run', return_value=completed) as called:
            result = probe.command(['/a path/tool', '--version'])
        self.assertEqual(result['status'], 'command_failed')
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(result['stdout'], completed.stdout)
        self.assertEqual(result['stderr'], completed.stderr)
        self.assertGreaterEqual(result['elapsed_seconds'], 0)
        self.assertEqual(called.call_args[0][0], ['/a path/tool', '--version'])
        self.assertNotIn('shell', called.call_args[1])
        error = subprocess.TimeoutExpired(['tool'], 10, output=b'partial', stderr=b'error')
        with patch.object(probe.subprocess, 'run', side_effect=error):
            result = probe.command(['tool'])
        self.assertEqual((result['stdout'], result['stderr'], result['exit_code']), ('partial', 'error', None))
        self.assertFalse(result['output_complete'])

    def test_missing_is_not_zero_or_empty_success(self):
        with tempfile.TemporaryDirectory(prefix='im2p-probe-unit-') as folder:
            self.assertEqual(probe.text_file(Path(folder) / 'missing')['status'], 'unavailable')
            self.assertEqual(probe.filesystem(Path(folder) / 'missing')['status'], 'unavailable')
        with patch.object(probe.shutil, 'which', return_value=None):
            self.assertEqual(probe.version('absent')['status'], 'unavailable')

    def test_usb_reads_metadata_not_device_contents(self):
        with tempfile.TemporaryDirectory(prefix='im2p-probe-unit-') as folder:
            root = Path(folder)
            usb, serial = root / 'usb', root / 'by-id'
            device = usb / '1-2'
            device.mkdir(parents=True)
            serial.mkdir()
            for name, value in (('idVendor', '1234'), ('idProduct', '5678'), ('busnum', '1'), ('devnum', '2')):
                (device / name).write_text(value + '\n')
            node = root / 'fake-device'
            node.write_text('must not be read')
            (serial / 'test-board').symlink_to(node)
            original = Path.read_bytes
            def guarded(path):
                self.assertNotEqual(path.resolve(), node)
                return original(path)
            with patch.object(Path, 'read_bytes', guarded), patch.object(probe.subprocess, 'run', side_effect=AssertionError('USB command')):
                result = probe.usb_inventory(usb, serial)
            self.assertEqual(len(result['usb']['entries']), 1)
            self.assertEqual(result['usb']['entries'][0]['manufacturer']['status'], 'unavailable')
            self.assertEqual(result['serial_by_id']['entries'][0]['resolved_path'], str(node))

    def test_compile_only_is_explicit_and_never_executes_binary(self):
        with tempfile.TemporaryDirectory(prefix='im2p-probe-unit-') as folder:
            out = Path(folder) / 'new-output'
            compiler = Path('/bin/true')
            with patch.object(probe, 'command', return_value={'status': 'ok', 'exit_code': 0}) as called:
                result = probe.compile_probe(compiler, out)
            self.assertEqual(called.call_count, 1)
            self.assertIn('-std=c++20', called.call_args[0][0])
            self.assertEqual(result['native_run']['status'], 'not_run')
            self.assertEqual(json.loads((out / 'result.json').read_text()), result)
            with self.assertRaises(FileExistsError):
                probe.compile_probe(compiler, out)


if __name__ == '__main__':
    unittest.main()
