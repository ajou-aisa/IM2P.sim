from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.gemmini_replay_contract import contract_digest
from scripts.gemmini_rtl_build_binding import (
    OBJECTS, RUN_AWARE_BINARY, BuildBindingError, build_inputs, mapping, seal_build, verify_build,
)


class RtlBuildBindingTests(unittest.TestCase):
    def prepare(self, root: Path) -> None:
        before = build_inputs('a8w8-d16-hp1')
        facts = mapping(mapping(before['hardware_contract'])['facts'])
        (root / 'resolved-profile.json').write_text(json.dumps(facts))
        (root / 'resolved-hardware.properties').write_text('test boundary fixture\n')
        (root / 'rtl').mkdir()
        (root / 'rtl/top.sv').write_text('module top; endmodule\n')
        (root / 'rtl-test-obj').mkdir()
        for name in OBJECTS:
            (root / 'rtl-test-obj' / name).write_bytes(b'unit-test-artifact-A')
        binary = root / RUN_AWARE_BINARY
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'unit-test-run-aware-binary-A')
        binding = seal_build(root, before)
        (root / 'rtl-build-binding.json').write_text(json.dumps(binding))

    def test_unchanged_inputs_and_artifacts_are_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            self.assertEqual(verify_build(root, 'a8w8-d16-hp1')['execution_kind'], 'FRESH_BUILD')

    def test_package_source_identity_is_bound_to_resolved_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            source = {'root': '/candidate/dependency/source/llama_cpp_gemmini',
                      'base_head': '1' * 40, 'source_manifest_sha256': '2' * 64,
                      'dependency_lock_sha256': '3' * 64}
            resolved = json.loads((root / 'resolved-profile.json').read_text())
            resolved['llama_source'] = source
            (root / 'resolved-profile.json').write_text(json.dumps(resolved))
            binding = seal_build(root, build_inputs('a8w8-d16-hp1', source))
            (root / 'rtl-build-binding.json').write_text(json.dumps(binding))
            self.assertEqual(verify_build(root, 'a8w8-d16-hp1')['llama_source'], source)
            resolved['llama_source']['source_manifest_sha256'] = '4' * 64
            (root / 'resolved-profile.json').write_text(json.dumps(resolved))
            with self.assertRaisesRegex(BuildBindingError, 'llama source differs'):
                verify_build(root, 'a8w8-d16-hp1')

    def test_stale_artifact_and_current_contract_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            path = root / 'rtl-build-binding.json'
            binding = json.loads(path.read_text())
            contract = binding['hardware_contract']
            contract['facts']['scale_mapping_revision'] = 'artifact-A-global-row-v0'
            contract['sha256'] = contract_digest(contract)
            binding['sha256'] = contract_digest(binding)
            path.write_text(json.dumps(binding))
            with self.assertRaisesRegex(BuildBindingError, 'artifact hardware contract mismatch'):
                verify_build(root, 'a8w8-d16-hp1')

    def test_changed_object_bytes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            (root / 'rtl-test-obj' / OBJECTS[0]).write_bytes(b'artifact-B')
            with self.assertRaisesRegex(BuildBindingError, 'artifact changed'):
                verify_build(root, 'a8w8-d16-hp1')

    def test_changed_run_aware_rtl_executable_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            binary = root / RUN_AWARE_BINARY
            binary.write_bytes(b'rtl-binary-A')
            binding = seal_build(root, build_inputs('a8w8-d16-hp1'))
            (root / 'rtl-build-binding.json').write_text(json.dumps(binding))
            binary.write_bytes(b'rtl-binary-B')
            with self.assertRaisesRegex(BuildBindingError, 'artifact changed'):
                verify_build(root, 'a8w8-d16-hp1')

    def test_source_change_during_build_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            before = copy.deepcopy(build_inputs('a8w8-d16-hp1'))
            mapping(before['fixture_source_sha256'])['fpga/gemmini_hp1/host/test_ws_rtl.cpp'] = '0' * 64
            with self.assertRaisesRegex(BuildBindingError, 'inputs changed'):
                seal_build(Path(directory), before)

    def test_missing_build_binding_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(BuildBindingError, 'build-time binding missing'):
                verify_build(Path(directory), 'a8w8-d16-hp1')


if __name__ == '__main__':
    unittest.main()
