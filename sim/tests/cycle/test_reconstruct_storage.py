from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
import tempfile
import unittest

from sim.cycle.npu_trace import ReplayArtifacts, ReplayOutputs
from sim.cycle.reconstruct import reconstruct
from sim.cycle.reconstruct_graph import json_records
from sim.tests.cycle.test_reconstruct import write_rows
from sim.tests.cycle.test_reconstruct_join import fixture, refresh_proofs


@unittest.skipUnless(os.getenv('IM2P_CYCLE_LIBRARY') and os.getenv('IM2P_CYCLE_CERTIFICATE'), 'actual certified C model required')
class ReconstructionStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.inputs = fixture(self.root, ReplayArtifacts(Path(os.environ['IM2P_CYCLE_LIBRARY']),
                                                       Path(os.environ['IM2P_CYCLE_CERTIFICATE'])))

    def test_gzip_storage_preserves_jsonl_and_has_deterministic_header(self):
        plain = ReplayOutputs(self.root/'plain.jsonl', self.root/'plain-summary.json')
        first = ReplayOutputs(self.root/'first.jsonl.gz', self.root/'first-summary.json')
        second = ReplayOutputs(self.root/'second.jsonl.gz', self.root/'second-summary.json')
        reconstruct(self.inputs, plain)
        reconstruct(self.inputs, first)
        reconstruct(self.inputs, second)
        encoded = first.results.read_bytes()
        self.assertEqual(gzip.decompress(encoded), plain.results.read_bytes())
        self.assertEqual(encoded, second.results.read_bytes())
        self.assertEqual(encoded[4:8], b'\x00\x00\x00\x00')
        self.assertEqual(encoded[3] & 8, 0)
        plain_summary = json.loads(plain.summary.read_text())
        gzip_summary = json.loads(first.summary.read_text())
        self.assertEqual(plain_summary.pop('storage_encoding'), 'plain')
        self.assertEqual(gzip_summary.pop('storage_encoding'), 'gzip')
        self.assertEqual(plain_summary, gzip_summary)
        self.assertEqual(first.summary.read_bytes(), second.summary.read_bytes())

    def test_invalid_final_record_never_publishes_compressed_output(self):
        rows = list(json_records(self.inputs.npu.trace))
        rows[-1]['npu_work_count'] = 99
        write_rows(self.inputs.npu.trace, rows)
        refresh_proofs(self.inputs)
        output = ReplayOutputs(self.root/'invalid.jsonl.gz', self.root/'invalid-summary.json')
        with self.assertRaisesRegex(ValueError, 'run count mismatch'):
            reconstruct(self.inputs, output)
        self.assertFalse(output.results.exists())
        self.assertFalse(output.summary.exists())


if __name__ == '__main__':
    unittest.main()
