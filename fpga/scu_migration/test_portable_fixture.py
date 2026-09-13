#!/usr/bin/env python3
"""Synthetic unit controls; these do not claim host quantizer or RTL execution."""
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
import zlib
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location('migration_portable_fixture', Path(__file__).with_name('portable_fixture.py'))
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)


def synthetic(directory):
    m, n, k = fixture.CONTRACT['shape']
    data = bytearray(struct.pack('<I13Qhhi', 0x31584649, m, n, k, 1, 1, 1,
                                16, 0, k, 19, 1, 1, 1, 0, 0, 0))
    data += struct.pack('<h', 0) + bytes([1]) * (m * k)
    for column in range(n):
        for code in (0, 255):
            data += struct.pack('<32bBfH', *([1] * 32), code, 1 / 256, 1)
    (directory / 'fixture.bin').write_bytes(data)
    a, w = fixture.fp32_inputs()
    (directory / 'input-a-f32.bin').write_bytes(a)
    (directory / 'input-w-f32.bin').write_bytes(w)
    counts = fixture.derive(directory)
    (directory / 'manifest.json').write_text(json.dumps({
        'contract': fixture.CONTRACT, 'quantizer_execution': 'NOT_RUN_SYNTHETIC_UNIT',
        'sha256': {name: fixture.sha(directory / name) for name in fixture.FILES}}))
    return counts


class PortableFixtureTests(unittest.TestCase):
    def test_changed_helper_rejected_before_import(self):
        with tempfile.TemporaryDirectory(prefix='im2p-portable-unit-') as folder:
            root = Path(folder)
            for relative in fixture.HELPERS:
                source = fixture.ROOT / relative
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
            golden = root / 'tests/scu_block_scale/golden.py'
            golden.write_bytes(golden.read_bytes() + b'\n# changed helper\n')
            with self.assertRaisesRegex(ValueError, 'helper SHA256 mismatch'):
                fixture.helpers(root)

    def test_native_elf_gate_rejects_foreign_executable_and_archive(self):
        def header(machine):
            return b'\x7fELF\x02\x01' + bytes(12) + machine.to_bytes(2, 'little')
        with tempfile.TemporaryDirectory(prefix='im2p-portable-unit-') as folder:
            path = Path(folder) / 'executable'
            path.write_bytes(header(62))
            with patch.object(fixture.platform, 'machine', return_value='x86_64'):
                self.assertEqual(fixture.native_architecture(path)['ELF_machine'], 62)
                path.write_bytes(header(183))
                with self.assertRaisesRegex(ValueError, 'native architecture mismatch'):
                    fixture.native_architecture(path)
                with patch.object(fixture.subprocess, 'check_output', side_effect=['member.o\n', header(183)]):
                    with self.assertRaisesRegex(ValueError, 'native architecture mismatch'):
                        fixture.native_architecture(Path(folder) / 'foreign.a')

    def test_source_pin_verifier_precedes_build(self):
        with tempfile.TemporaryDirectory(prefix='im2p-portable-unit-') as folder:
            with patch.object(fixture, 'verify_frozen', side_effect=ValueError('sealed pin mismatch')) as verified:
                with patch.object(fixture, 'native_architecture', side_effect=AssertionError('too late')):
                    with self.assertRaisesRegex(ValueError, 'sealed pin mismatch'):
                        fixture.build_exporter(folder, Path(folder) / 'unused', '/bin/true')
            verified.assert_called_once_with(Path(folder).resolve())

    def test_explicit_fp32_inputs_are_deterministic(self):
        a, w = fixture.fp32_inputs()
        self.assertEqual((len(a), len(w)), (4096, 4096))
        self.assertEqual((a, w), fixture.fp32_inputs())
        self.assertNotEqual(a, w)

    def test_bigint_G1_G2_wire_layout_and_crc(self):
        with tempfile.TemporaryDirectory(prefix='im2p-portable-unit-') as folder:
            out = Path(folder)
            counts = synthetic(out)
            self.assertEqual(counts['logical_values'], 256)
            self.assertEqual(counts['padding_values'], 48)
            self.assertEqual(struct.unpack('<256i', (out / 'expected-final-raw.bin').read_bytes()), (8224,) * 256)
            values = struct.unpack('<304I', (out / 'expected-fout.bin').read_bytes())
            for row in range(16):
                self.assertEqual(values[row * 19:row * 19 + 16], (0x42008000,) * 16)
                self.assertEqual(values[row * 19 + 16:(row + 1) * 19], (0x41880000,) * 3)
            request = (out / 'full-run.request.bin').read_bytes()
            self.assertEqual(request[:8], b'IFR3\x03\x01\x10\x08')
            self.assertEqual(len(request), 32 + 16 * 128 + 64 * 64 + 2 * 256 + 4)
            self.assertEqual(zlib.crc32(request[:-4]), struct.unpack('<I', request[-4:])[0])
            scale = (out / 'wire-scale.bin').read_bytes()
            self.assertEqual(struct.unpack_from('<16I', scale, 0), (1,) * 16)
            self.assertEqual(struct.unpack_from('<16I', scale, 256), (256,) * 16)
            self.assertEqual(scale[64:256], bytes(192))
            self.assertEqual(scale[320:512], bytes(192))

    def test_compare_equal_corrupt_and_different_sealed_bytes(self):
        with tempfile.TemporaryDirectory(prefix='im2p-portable-unit-') as folder:
            left, right = Path(folder) / 'left', Path(folder) / 'right'
            left.mkdir()
            synthetic(left)
            shutil.copytree(left, right)
            self.assertEqual(fixture.compare(left, right)['status'], 'PASS')
            path = right / 'beta-u32le.bin'
            data = bytearray(path.read_bytes())
            data[0] ^= 1
            path.write_bytes(data)
            with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
                fixture.compare(left, right)
            manifest = json.loads((right / 'manifest.json').read_text())
            manifest['sha256'][path.name] = fixture.sha(path)
            (right / 'manifest.json').write_text(json.dumps(manifest))
            result = fixture.compare(left, right)
            self.assertEqual(result['status'], 'FAIL')
            self.assertEqual(result['differences'][0]['file'], 'beta-u32le.bin')
            self.assertEqual(result['differences'][0]['first_different_byte'], 0)
            manifest['contract'] = dict(manifest['contract'], protocol='IFR2')
            (right / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'schema/profile'):
                fixture.compare(left, right)

    def test_existing_derived_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory(prefix='im2p-portable-unit-') as folder:
            out = Path(folder)
            synthetic(out)
            before = fixture.sha(out / 'activation-i8.bin')
            with self.assertRaises(FileExistsError):
                fixture.derive(out)
            self.assertEqual(fixture.sha(out / 'activation-i8.bin'), before)


if __name__ == '__main__':
    unittest.main()
