from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from sim.cycle.npu_trace_schema import Record
from sim.cycle.reconstruct_graph import sha256
from sim.tests.cycle.test_execution_adapter import contract, dataset

ROOT = Path(__file__).resolve().parents[3]


def fixture_files(root: Path) -> None:
    (root / 'dataset.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in dataset()))
    (root / 'npu.jsonl').write_text('')
    declaration = contract()
    declaration.update(dataset_sha256=sha256(root / 'dataset.jsonl'), npu_results_sha256=sha256(root / 'npu.jsonl'))
    (root / 'lifecycle.json').write_text(json.dumps(declaration))
    summary: Record = {'status': 'PASS', 'scope': 'structural-three-source-reconstruction',
                       'decode_token_fingerprint_matches': {'0': True},
                       'source_artifacts': {'npu_results': {'sha256': sha256(root / 'npu.jsonl')}}}
    (root / 'summary.json').write_text(json.dumps(summary))
    (root / 'phase.json').write_text(json.dumps({'schema': 'im2p-service-phase-table', 'version': 1,
                                                'scope': 'SYNTHETIC_ONLY', 'period': 1, 'samples': []}))


class ExecutionCliTests(unittest.TestCase):
    def invoke(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, '-m', 'sim.cycle.execution_cli', *arguments], cwd=ROOT,
                              capture_output=True, text=True, timeout=15)

    def test_official_adapter_and_schedule_when_synthetic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_files(root)
            result = self.invoke(['adapt', '--dataset', str(root / 'dataset.jsonl'), '--npu-results', str(root / 'npu.jsonl'),
                                  '--lifecycle', str(root / 'lifecycle.json'), '--join-summary', str(root / 'summary.json'),
                                  '--output', str(root / 'bundle.json')])
            self.assertEqual(result.returncode, 0, result.stderr)
            command = ['schedule', '--bundle', str(root / 'bundle.json'), '--phase-table', str(root / 'phase.json'),
                       '--frequency-hz', '1000000000', '--synthetic']
            result = self.invoke([*command, '--output', str(root / 'schedule.json')])
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads((root / 'schedule.json').read_text())
            rows = {row['node_id']: row for row in output['nodes']}
            self.assertEqual(rows['operation:b:exit']['result_ready_ns'], {'numerator': 13, 'denominator': 1})
            self.assertFalse(output['paper_latency_ready'])
            repeat = self.invoke([*command, '--output', str(root / 'repeat.json')])
            self.assertEqual(repeat.returncode, 0, repeat.stderr)
            self.assertEqual((root / 'schedule.json').read_bytes(), (root / 'repeat.json').read_bytes())

    def test_failed_input_when_lifecycle_hash_changed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_files(root)
            (root / 'dataset.jsonl').write_text('{}\n')
            result = self.invoke(['adapt', '--dataset', str(root / 'dataset.jsonl'), '--npu-results', str(root / 'npu.jsonl'),
                                  '--lifecycle', str(root / 'lifecycle.json'), '--join-summary', str(root / 'summary.json'),
                                  '--output', str(root / 'bundle.json')])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('binding mismatch', result.stderr)
            self.assertFalse((root / 'bundle.json').exists())

    def test_help_when_requested(self) -> None:
        result = self.invoke(['--help'])
        self.assertEqual(result.returncode, 0)
        self.assertIn('adapt', result.stdout)
        self.assertIn('schedule', result.stdout)


if __name__ == '__main__':
    unittest.main()
