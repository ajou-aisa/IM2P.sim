#!/usr/bin/env python3
"""M9 harness infrastructure tests. No compiler, RTL, or physical device is run."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from activation_guard_support import (
    PRIMITIVES, REQUIRED_SOURCE_FILES, changed_inputs, changed_source_inputs,
    digest, discover_toolchain,
    primitive_directory, source_inputs, warning_inventory,
)

HERE = Path(__file__).resolve().parent
RUNNER = HERE / 'run_activation_guard.py'


class GuardInfrastructureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='m9-infrastructure-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def source(self):
        root = self.root / 'source'
        for name in REQUIRED_SOURCE_FILES:
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('test input for hash validation only\n')
        return root

    def marker_tool(self, name='bsc'):
        # Executability markers ONLY. Never invoked as a compiler in these tests.
        p = self.root / 'toolchain/bin' / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('#!/bin/sh\nexit 99\n')
        p.chmod(0o700)
        return p

    def primitives(self, layout):
        p = self.root / 'toolchain' / layout
        p.mkdir(parents=True)
        for name in PRIMITIVES:
            (p / name).write_text('// unit test path marker\n')
        return p

    def cli(self, *arguments, optimized=False):
        env = os.environ.copy()
        env['BSC'] = str(self.root / 'not-visible-bsc')
        command = [sys.executable] + (['-O'] if optimized else [])
        return subprocess.run(command + [str(RUNNER), *map(str, arguments)],
                              env=env, text=True, capture_output=True, check=False)

    def test_current_source_inventory_and_no_generated_rtl(self):
        source = self.source()
        extra = source / 'src/core/Extra.bsv'
        extra.write_text('additional source\n')
        (source / 'build').mkdir()
        (source / 'build/old.v').write_text('not an input\n')
        found = source_inputs(source)
        self.assertEqual(len(found), len(REQUIRED_SOURCE_FILES) + 1)
        self.assertIn(str(extra), found)
        self.assertFalse(any('/build/' in p for p in found))
        self.assertEqual(changed_inputs(found), [])

    def test_source_changes_are_detected(self):
        source = self.source()
        found = source_inputs(source)
        target = source / REQUIRED_SOURCE_FILES[0]
        target.write_text('changed source\n')
        self.assertEqual(changed_inputs(found), [str(target)])

    def test_deleted_input_is_reported_not_traceback(self):
        source = self.source()
        found = source_inputs(source)
        target = source / REQUIRED_SOURCE_FILES[0]
        target.unlink()
        self.assertEqual(changed_inputs(found), [str(target)])

    def test_new_source_package_is_detected(self):
        source = self.source()
        original = source_inputs(source)
        added = source / 'src/core/NewDependency.bsv'
        added.write_text('new package changes selected input set\n')
        self.assertEqual(changed_source_inputs(source, original), [str(added)])

    def test_removed_required_package_is_detected(self):
        source = self.source()
        original = source_inputs(source)
        removed = source / REQUIRED_SOURCE_FILES[0]
        removed.unlink()
        self.assertIn(str(removed), changed_source_inputs(source, original))

    def test_incomplete_source_rejected(self):
        source = self.source()
        (source / REQUIRED_SOURCE_FILES[0]).unlink()
        with self.assertRaises(FileNotFoundError):
            source_inputs(source)

    def test_source_symlink_escape_rejected(self):
        source = self.source()
        outside = self.root / 'outside.bsv'
        outside.write_text('not selected\n')
        (source / 'src/core/Escape.bsv').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'escapes'):
            source_inputs(source)

    def test_bsc_lib_layout(self):
        bsc = self.marker_tool()
        directory = self.primitives('lib/Verilog')
        self.assertEqual(primitive_directory(bsc), directory)

    def test_bsc_libexec_layout(self):
        bsc = self.marker_tool()
        directory = self.primitives('libexec/lib/Verilog')
        self.assertEqual(primitive_directory(bsc), directory)

    def test_bad_override_does_not_silently_fall_back(self):
        bsc = self.marker_tool()
        self.primitives('lib/Verilog')
        with self.assertRaisesRegex(ValueError, 'unavailable/incomplete'):
            primitive_directory(bsc, str(self.root / 'bad-override'))

    def test_incomplete_primitives_rejected(self):
        bsc = self.marker_tool()
        directory = self.primitives('lib/Verilog')
        (directory / 'BRAM2.v').unlink()
        with self.assertRaises(ValueError):
            primitive_directory(bsc)

    def test_explicit_tool_paths_and_realpaths(self):
        bsc = self.marker_tool()
        verilator = self.marker_tool('verilator')
        directory = self.primitives('libexec/lib/Verilog')
        alias = self.root / 'bsc-alias'
        alias.symlink_to(bsc)
        found = discover_toolchain({'PATH': '', 'BSC': str(alias), 'VERILATOR': str(verilator)})
        self.assertEqual(found, {'bsc': str(bsc), 'verilator': str(verilator), 'bsc_verilog': str(directory)})

    def test_missing_bsc_reports_visibility_not_installation_absence(self):
        with self.assertRaisesRegex(ValueError, 'not visible/executable'):
            discover_toolchain({'PATH': str(self.root)})

    def test_warning_codes_and_text_are_retained(self):
        text = ('compile preamble\nWarning: "A.bsv", line 1 (G0117)\n  action shadowed\n'
                'Warning: "B.bsv", line 2 (G0117)\n  second conflict\n'
                'Warning: "C.bsv" (G0010)\n  unrelated\n')
        found = warning_inventory(text)
        self.assertEqual(found['counts'], {'G0010': 1, 'G0117': 2})
        self.assertEqual(found['g0117_count'], 2)
        self.assertEqual(len(found['diagnostics']), 3)
        self.assertIn('second conflict', found['diagnostics'][1])

    def test_preflight_does_not_create_partial_out(self):
        out = self.root / 'result'
        result = self.cli('--source', self.source(), '--out', out)
        self.assertEqual(result.returncode, 2)
        self.assertIn('bsc is not visible/executable', result.stderr)
        self.assertNotIn('TypeError', result.stderr)
        self.assertFalse(out.exists())

    def test_fresh_source_rejects_historical_production_reuse(self):
        out = self.root / 'result'
        result = self.cli('--source', self.source(), '--out', out,
                          '--production-build', self.root / 'old-production')
        self.assertEqual(result.returncode, 2)
        self.assertIn('fresh RTL is required', result.stderr)
        self.assertFalse(out.exists())

    def test_source_and_snapshot_are_mutually_exclusive(self):
        result = self.cli('--source', self.root, '--snapshot', self.root, '--out', self.root / 'result')
        self.assertEqual(result.returncode, 2)
        self.assertIn('not allowed with argument', result.stderr)

    def test_optimized_python_cannot_disable_validation(self):
        result = self.cli('--source', self.root, '--out', self.root / 'result', optimized=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('assertions are required', result.stderr)

    def test_invalid_parallelism_rejected(self):
        result = self.cli('--source', self.root, '--out', self.root / 'result', '--jobs', '0')
        self.assertEqual(result.returncode, 2)
        self.assertIn('--jobs must be positive', result.stderr)

    def test_existing_evidence_preserved(self):
        out = self.root / 'result'
        out.mkdir()
        sentinel = out / 'original.json'
        sentinel.write_text('original evidence\n')
        before = digest(sentinel)
        result = self.cli('--source', self.root, '--out', out)
        self.assertEqual(result.returncode, 2)
        self.assertIn('never overwritten', result.stderr)
        self.assertEqual(digest(sentinel), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
