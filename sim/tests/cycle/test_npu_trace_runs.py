from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from sim.cycle import cli, npu_trace
from sim.cycle.npu_trace_schema import Record, object_value, parse_run, parse_work
from sim.cycle.run_aware_certificate import validate_run_certificate
from sim.tests.cycle.test_npu_trace import records, write_trace


def production_run_records() -> list[Record]:
    rows = records()
    for row in rows:
        row['version'] = 2
    rows[0]['residual_work_revision'] = 'cross-block-run-aware-v1'
    work = rows[4]
    work.update(provenance='residual', scope='residual_compact', m=2, n=2,
                k=22, parent_m=2, row_count=2, activation_stride_bytes=22,
                weight_stride_bytes=2, output_stride_bytes=8,
                scale_stride_elements=2, source_row_count=2, original_block_id=None,
                original_k=128, residual_work_revision='cross-block-run-aware-v1',
                runs=[{'original_block_id': 0, 'original_k_mask': 0xfff,
                       'compact_k_begin': 0, 'compact_k_count': 12},
                      {'original_block_id': 3, 'original_k_mask': 0x3ff,
                       'compact_k_begin': 12, 'compact_k_count': 10}],
                row_map=[{'source_row': 0, 'lane_id': 0},
                         {'source_row': 1, 'lane_id': 1}])
    for row in rows:
        if row['kind'] == 'NPU_CALL':
            row['call_kind'] = 'RESIDUAL_COMPACT'
    return rows


class RunAwareTraceTests(unittest.TestCase):
    @unittest.skipUnless(os.getenv('IM2P_CYCLE_LIBRARY') and os.getenv('IM2P_CYCLE_CERTIFICATE') and
                         os.getenv('IM2P_RUN_CERTIFICATE'), 'fixture certificate and cycle library required')
    def test_fixture_only_certificate_cannot_publish_current_residual_replay(self) -> None:
        # Given: a structurally valid current residual trace and a fixture-only run certificate.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace = root / 'npu-cycle-trace.jsonl'
            write_trace(trace, production_run_records())
            artifacts = npu_trace.ReplayArtifacts(
                Path(os.environ['IM2P_CYCLE_LIBRARY']), Path(os.environ['IM2P_CYCLE_CERTIFICATE']),
                Path(os.environ['IM2P_RUN_CERTIFICATE']))
            outputs = npu_trace.ReplayOutputs(root / 'result.jsonl', root / 'summary.json')
            # When: the independent base source mismatch is isolated from run-certificate scope.
            with patch.object(npu_trace, 'validate_certificate'):
                # Then: fixture-only evidence must not publish a production residual result.
                with self.assertRaisesRegex(ValueError, 'production-generated run-aware certificate required'):
                    npu_trace.replay(trace, artifacts, outputs)
            self.assertFalse(outputs.results.exists() or outputs.summary.exists())

    @unittest.skipUnless(os.getenv('IM2P_CYCLE_LIBRARY') and os.getenv('IM2P_RUN_CERTIFICATE'),
                         'run-aware RTL fixture evidence required')
    def test_fixture_certificate_cannot_self_shrink_or_fake_exactness(self) -> None:
        library = Path(os.environ['IM2P_CYCLE_LIBRARY'])
        source = Path(os.environ['IM2P_RUN_CERTIFICATE'])
        self.assertEqual(validate_run_certificate(source, library), 'FIXTURE_ONLY')
        certificate = json.loads(source.read_text())
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'mutated-certificate.json'
            for label, mutate in (
                ('library', lambda value: value.update(library_sha256='0' * 64)),
                ('missing case', lambda value: value['cases'].pop()),
                ('selected event', lambda value: value['cases'][0].update(selected_event_multiset=False)),
            ):
                with self.subTest(label=label):
                    altered = copy.deepcopy(certificate)
                    mutate(altered)
                    target.write_text(json.dumps(altered))
                    with self.assertRaises(ValueError):
                        validate_run_certificate(target, library)

    def test_run_and_row_map_corruption_is_rejected(self) -> None:
        rows = production_run_records()
        mutations = (
            ('missing runs', lambda work: work.update(runs=[])),
            ('changed mask', lambda work: work['runs'][1].update(original_k_mask=0x1ff)),
            ('K gap', lambda work: work['runs'][1].update(compact_k_begin=13)),
            ('duplicate block', lambda work: work['runs'][1].update(original_block_id=0)),
            ('out-of-range block', lambda work: work['runs'][1].update(original_block_id=4)),
            ('duplicate row', lambda work: work['row_map'][1].update(source_row=0, lane_id=0)),
            ('unsupported revision', lambda work: work.update(residual_work_revision='block-local-v1')),
        )
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / 'npu-cycle-trace.jsonl'
            for label, mutate in mutations:
                with self.subTest(label=label):
                    broken = copy.deepcopy(rows)
                    mutate(broken[4])
                    write_trace(trace, broken)
                    with self.assertRaises(ValueError):
                        npu_trace.validate_trace(trace)

    def test_valid_changed_mask_or_tile_never_reuses_stale_result_binding(self) -> None:
        rows = production_run_records()
        run = parse_run(rows[0])
        original = parse_work(rows[4], run)
        changed_mask = copy.deepcopy(rows[4])
        mask_runs = changed_mask['runs']
        assert isinstance(mask_runs, list)
        object_value(mask_runs[0])['original_k_mask'] = 0x1ffe
        changed_tile = copy.deepcopy(rows[4])
        changed_tile['tile_k_count'] = 4
        self.assertNotEqual(npu_trace.work_binding(original),
                            npu_trace.work_binding(parse_work(changed_mask, run)))
        self.assertNotEqual(npu_trace.work_binding(original),
                            npu_trace.work_binding(parse_work(changed_tile, run)))

    @unittest.skipUnless(os.getenv('IM2P_CYCLE_LIBRARY'), 'current C library required')
    def test_public_cycle_adapter_uses_explicit_runs(self) -> None:
        # Given: a compact K=22 request whose runs own original blocks 0 and 3.
        document = {
            'profile': 'a8w8-d16-hp1',
            'request': {'m': 2, 'n': 2, 'k': 22, 'tile_i': 1, 'tile_j': 1, 'tile_k': 2,
                        'activation_stride_bytes': 22, 'weight_stride_bytes': 2,
                        'output_stride_bytes': 8, 'scale_stride_elements': 2,
                        'accepted_cycle': 1, 'logical_work_id': 7,
                        'submission': 'planner-blocks', 'record_events': 0},
            'original_k': 128,
            'runs': [{'original_block_id': 0, 'original_k_mask': 0xfff,
                      'compact_k_begin': 0, 'compact_k_count': 12},
                     {'original_block_id': 3, 'original_k_mask': 0x3ff,
                      'compact_k_begin': 12, 'compact_k_count': 10}],
        }
        # When: the existing value-free C API estimates the declared run view.
        answer = cli.estimate(Path(os.environ['IM2P_CYCLE_LIBRARY']), document)
        # Then: the single logical work has two original-scale owners.
        self.assertEqual(answer['status'], 'PASS')
        self.assertEqual(answer['result']['logical_work_count'], 1)
        self.assertEqual(answer['result']['fragment_count'], 2)

    def test_cross_block_work_when_trace_is_v2(self) -> None:
        # Given: one production residual work spanning original blocks 0 and 3.
        rows = production_run_records()
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / 'npu-cycle-trace.jsonl'
            write_trace(trace, rows)
            # When: the dedicated NPU trace validator reads the exact run view.
            result = npu_trace.validate_trace(trace)
        # Then: it admits one logical residual work, not two block-local calls.
        self.assertEqual(result['residual_work_count'], 1)

    @unittest.skipUnless(os.getenv('IM2P_CYCLE_LIBRARY') and os.getenv('IM2P_CYCLE_CERTIFICATE'),
                         'current certified C library required')
    def test_current_residual_replay_requires_run_aware_certificate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace = root / 'npu-cycle-trace.jsonl'
            write_trace(trace, production_run_records())
            artifacts = npu_trace.ReplayArtifacts(
                Path(os.environ['IM2P_CYCLE_LIBRARY']), Path(os.environ['IM2P_CYCLE_CERTIFICATE']))
            outputs = npu_trace.ReplayOutputs(root / 'result.jsonl', root / 'summary.json')
            with self.assertRaisesRegex(ValueError, 'run-aware certificate required'):
                npu_trace.replay(trace, artifacts, outputs)
            self.assertFalse(outputs.results.exists() or outputs.summary.exists())

    @unittest.skipUnless(os.getenv('IM2P_CYCLE_LIBRARY') and os.getenv('IM2P_CYCLE_CERTIFICATE'),
                         'current certified C library required')
    def test_legacy_block_local_records_cannot_be_upgraded(self) -> None:
        old = records()
        old[4].update(scope='residual_compact', provenance='residual', k=31,
                      tile_k_count=2, original_block_id=96)
        for record in old:
            if record['kind'] == 'NPU_CALL':
                record['call_kind'] = 'RESIDUAL_COMPACT'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace = root / 'npu-cycle-trace.jsonl'
            write_trace(trace, old)
            artifacts = npu_trace.ReplayArtifacts(
                Path(os.environ['IM2P_CYCLE_LIBRARY']), Path(os.environ['IM2P_CYCLE_CERTIFICATE']))
            outputs = npu_trace.ReplayOutputs(root / 'result.jsonl', root / 'summary.json')
            with self.assertRaisesRegex(ValueError, 'legacy block-local residual trace'):
                npu_trace.replay(trace, artifacts, outputs)
            self.assertFalse(outputs.results.exists() or outputs.summary.exists())


if __name__ == '__main__':
    unittest.main()
