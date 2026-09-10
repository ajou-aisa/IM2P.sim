#!/usr/bin/env python3
"""Portable plan sealing only: synthetic build files, no numerical/device execution."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import prepare_measurement as prepare
from measure import digest, verify


class PortablePlan(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.host, self.here = root / 'native', root / 'tools'
        self.host.mkdir()
        self.here.mkdir()
        self.bit = root / 'synthetic.bit'
        self.bit.write_bytes(b'synthetic unit-test artifact, never programmed')
        self.out = root / 'plan.json'
        self.build = self.host / 'host-build'
        self.build.mkdir()
        for name in ('persistent_replay', 'dense_host_dispatch'):
            (self.build / name).write_text('Synthetic executable identity, never executed.\n')
        (self.build / 'CMakeCache.txt').write_text(
            'IM2P_FPGA_PROTOCOL_VERSION:STRING=2\n'
            'GGML_GEMMINI_EXECUTION_BACKEND:STRING=FPGA_UART\n'
            'GGML_GEMMINI_ACTIVATION_BITS:STRING=8\n'
            'GGML_GEMMINI_WEIGHT_BITS:STRING=8\n'
            'GGML_GEMMINI_DIM:STRING=16\n'
            'GGML_GEMMINI_BLOCK_SIZE:STRING=32\n'
            'GGML_GEMMINI_ACTIVATION_QUANT:STRING=EXSIA\n'
            'GGML_GEMMINI_ENABLE_RMD:BOOL=OFF\n')
        self.source = self.host / 'source.txt'
        self.source.write_text('sealed source\n')
        manifest = self.host / 'integration-sha256.json'
        manifest.write_text(json.dumps({'source.txt': digest(self.source)}))
        self.archive = self.host / 'native.a'
        self.archive.write_text('synthetic native archive\n')
        (self.host / 'identity.json').write_text(json.dumps({
            'backend': 'FPGA_UART', 'host_pin': prepare.HOST_PIN, 'params_pin': prepare.PARAMS_PIN,
            'source_sha256': digest(manifest), 'sim_archive': str(self.archive),
            'sim_archive_sha256': digest(self.archive), 'machine': 'synthetic'}))
        fixture_root = self.here / 'fixtures'
        index = {}
        for name in ('m16n16k32', 'm321n48k64', 'm321n48k96'):
            directory = fixture_root / name
            directory.mkdir(parents=True)
            members = {}
            for member in ('fixture.bin', 'staging.bin', 'expected-raw.bin', 'expected-fout.bin', 'manifest.json'):
                target = directory / member
                target.write_bytes((name + '/' + member).encode())
                members[member] = digest(target)
            (directory / 'sha256.json').write_text(json.dumps(members))
            target = directory / 'input-f32.bin'
            target.write_bytes(b'synthetic activation input')
            index[name + '/input-f32.bin'] = digest(target)
        (fixture_root / 'sha256.json').write_text(json.dumps(index))
        reference = (prepare.HERE / 'full-cycle-reference.txt').read_text()
        (self.here / 'full-cycle-reference.txt').write_text(
            reference.replace(prepare.BITSTREAM_SHA256, digest(self.bit)))
        for name in ('measure.py', 'build.py', 'deployment-lock.json'):
            (self.here / name).write_text('synthetic tool identity\n')
        self.addCleanup(patch.stopall)
        patch.object(prepare, 'HERE', self.here).start()
        patch.object(prepare, 'BITSTREAM_SHA256', digest(self.bit)).start()

    def create(self):
        return prepare.prepare_portable(self.host, self.bit, self.out)

    def test_sealed_plan_and_no_execution(self):
        with patch('subprocess.run', side_effect=AssertionError('plan must not execute anything')):
            result = self.create()
        self.assertTrue(result['verified'])
        self.assertFalse(result['physical_access'])
        plan = json.loads(self.out.read_text())
        verify(plan)
        self.assertEqual(plan['status'], 'Awaiting approval')
        self.assertEqual(sum(len(c['fixtures']) for c in plan['conditions']) * 6, 54)
        self.assertEqual(plan['logical_invocations_per_backend'], 54)
        self.assertNotIn('preservation_mapping', plan)
        for name in ('build.py', 'deployment-lock.json', 'fixtures/m16n16k32/input-f32.bin'):
            self.assertIn(str(self.here / name), plan['files'])
        self.assertTrue(all(Path(path).is_relative_to(self.temporary.name) or
                            Path(path) == Path(prepare.__file__).resolve()
                            for path in plan['files']))
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            self.create()
        self.source.write_text('changed after seal')
        with self.assertRaisesRegex(ValueError, 'sealed artifact changed'):
            verify(plan)

    def test_source_and_archive_mismatch(self):
        for path in (self.source, self.archive):
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'preserved artifact mismatch'):
                    self.create()
                self.assertFalse(self.out.exists())
                path.write_bytes(original)

    def test_relocated_fixtures(self):
        fixtures = Path(self.temporary.name) / 'relocated-fixtures'
        shutil.copytree(self.here / 'fixtures', fixtures)
        prepare.prepare_portable(self.host, self.bit, self.out, fixtures)
        plan = json.loads(self.out.read_text())
        verify(plan)
        self.assertTrue(all(Path(path).is_relative_to(fixtures)
                            for condition in plan['conditions'] for path in condition['fixtures']))
        self.assertIn(str(fixtures / 'm321n48k96/input-f32.bin'), plan['files'])

    def test_wrong_bit_profile_and_pins(self):
        wrong_pin = json.loads((self.host / 'identity.json').read_text())
        wrong_pin['host_pin'] = '0' * 40
        paths = [(self.bit, b'wrong bit'),
                 (self.build / 'CMakeCache.txt', b'GGML_GEMMINI_DIM:STRING=32\n'),
                 (self.host / 'identity.json', json.dumps({'backend': 'IM2P_SIM'}).encode()),
                 (self.host / 'identity.json', json.dumps(wrong_pin).encode())]
        for path, content in paths:
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(content)
                with self.assertRaises(ValueError):
                    self.create()
                self.assertFalse(self.out.exists())
                path.write_bytes(original)

    def test_fixture_input_and_reference_mismatch(self):
        paths = [self.here / 'fixtures/m16n16k32/input-f32.bin',
                 self.here / 'fixtures/m321n48k64/input-f32.bin',
                 self.here / 'fixtures/m321n48k64/expected-raw.bin',
                 self.here / 'full-cycle-reference.txt']
        for path in paths:
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(b'changed')
                with self.assertRaises(ValueError):
                    self.create()
                self.assertFalse(self.out.exists())
                path.write_bytes(original)


if __name__ == '__main__':
    unittest.main()
