from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.gemmini_replay_contract import contract_digest
from scripts.gemmini_rtl_build_binding import (
    OBJECTS, BuildBindingError, build_inputs, mapping, seal_build, verify_build,
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
        binding = seal_build(root, before)
        (root / 'rtl-build-binding.json').write_text(json.dumps(binding))

    def test_unchanged_inputs_and_artifacts_are_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            self.assertEqual(verify_build(root, 'a8w8-d16-hp1')['execution_kind'], 'FRESH_BUILD')

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
