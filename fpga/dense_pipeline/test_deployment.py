#!/usr/bin/env python3
"""Native deployment guards; mock archive headers, no compiler or device execution."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build


class Deployment(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def snapshot(self, archive=False):
        out = self.root / 'snapshot'
        out.mkdir()
        manifest = out / 'integration-sha256.json'
        manifest.write_text('{}')
        identity = {'source_sha256': build.full.digest(manifest), 'sim_archive': None}
        if archive:
            library = out / 'synthetic.a'
            library.write_bytes(b'synthetic unit-test archive')
            identity.update(sim_archive=str(library), sim_archive_sha256=build.full.digest(library))
        (out / 'identity.json').write_text(json.dumps(identity))
        return out

    def test_archive_architecture(self):
        for host, machine, accepted in [('x86_64', 62, True), ('aarch64', 183, True),
                                        ('aarch64', 62, False), ('x86_64', 183, False)]:
            with self.subTest(host=host, machine=machine):
                elf = bytearray(64)
                elf[:6] = b'\x7fELF\x02\x01'
                elf[18:20] = machine.to_bytes(2, 'little')
                with patch.object(build.platform, 'machine', return_value=host), \
                     patch.object(build.subprocess, 'check_output', side_effect=['object.o\n', bytes(elf)]):
                    if accepted:
                        self.assertEqual(build.archive_machine(self.root / 'unused.a'), host)
                    else:
                        with self.assertRaisesRegex(ValueError, 'architecture mismatch'):
                            build.archive_machine(self.root / 'unused.a')

    def test_missing_archive_stops_before_host_commands(self):
        out = self.snapshot()
        with patch.object(build.full, 'command') as command, \
             patch.object(build, 'archive_machine') as architecture:
            with self.assertRaisesRegex(ValueError, 'run native-sim before host'):
                build.host(out)
            command.assert_not_called()
            architecture.assert_not_called()

    def test_architecture_failure_stops_before_host_commands(self):
        out = self.snapshot(archive=True)
        with patch.object(build.full, 'command') as command, \
             patch.object(build, 'archive_machine', side_effect=ValueError('architecture mismatch')):
            with self.assertRaisesRegex(ValueError, 'architecture mismatch'):
                build.host(out)
            command.assert_not_called()

    def test_baseline_mismatch_stops_before_freeze(self):
        with patch.object(build, 'BASE_CORE_COMMIT', '0' * 40), \
             patch.object(build.full, 'freeze') as freeze:
            with self.assertRaisesRegex(ValueError, 'locked core commit'):
                build.freeze(self.root / 'candidate')
            freeze.assert_not_called()

    def test_locked_source_mismatch_is_not_sealed(self):
        repository = self.root / 'repo'
        tools = repository / 'fpga/dense_pipeline'
        tools.mkdir(parents=True)
        baseline = repository / 'fpga/full_replay/baseline.json'
        baseline.parent.mkdir()
        baseline.write_text(json.dumps({'head': build.BASE_CORE_COMMIT}))
        (repository / 'synth').mkdir()
        (repository / 'synth/DensePipeline.bsv').write_text('synthetic provider, never compiled')
        out = self.root / 'candidate'

        def frozen(destination):
            (destination / 'source/synth').mkdir(parents=True)
            (destination / 'source/locked.txt').write_bytes(b'changed')
            (destination / 'selection.json').write_text('{"explicit_replacements": []}')

        expected = hashlib.sha256(b'verified input').hexdigest()
        with patch.object(build, 'ROOT', repository), patch.object(build, 'HERE', tools), \
             patch.object(build, 'LOCK', {'verified_source_sha256': {'locked.txt': expected}}), \
             patch.object(build.full, 'freeze', side_effect=frozen), \
             patch.object(build, 'archive', side_effect=lambda repo, pin, target: target.mkdir()), \
             patch.object(build.full, 'command'):
            with self.assertRaisesRegex(ValueError, 'verified numerical/host input changed: locked.txt'):
                build.freeze(out)
        self.assertFalse((out / 'identity.json').exists())
        self.assertFalse((out / 'integration-sha256.json').exists())


if __name__ == '__main__':
    unittest.main()
