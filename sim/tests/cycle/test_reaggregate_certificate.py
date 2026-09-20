from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from sim.tests.cycle.reaggregate_certificate import EvidenceError, checked_file, reaggregate, source_proof


class ReaggregateEvidenceTest(unittest.TestCase):
    def test_missing_checksum_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'raw.json'
            path.write_text('{}')
            with self.assertRaises(EvidenceError):
                checked_file(path, root, {})

    def test_mutated_raw_artifact_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'raw.json'
            hashes = {'raw.json': hashlib.sha256(b'{}').hexdigest()}
            path.write_text('{"status":"PASS"}')
            with self.assertRaises(EvidenceError):
                checked_file(path, root, hashes)

    def test_rehashed_inventory_with_missing_sources_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'final-source-sha256.json'
            path.write_text('{}')
            hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()}
            with self.assertRaises(EvidenceError):
                source_proof(root, hashes)

    def test_library_sha_cannot_be_replaced_during_reaggregation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cert = root / 'cycle-release-certificate/current-certificate.json'
            cert.parent.mkdir()
            cert.write_text(json.dumps({'model_library_sha256': hashlib.sha256(b'old').hexdigest()}))
            (root / 'SHA256SUMS').write_text(
                f'{hashlib.sha256(cert.read_bytes()).hexdigest()}  cycle-release-certificate/current-certificate.json\n')
            library = root / 'library'
            library.write_bytes(b'new')
            with self.assertRaisesRegex(EvidenceError, 'identical previously certified model library'):
                reaggregate(root, library, root / 'new')


if __name__ == '__main__':
    unittest.main()
