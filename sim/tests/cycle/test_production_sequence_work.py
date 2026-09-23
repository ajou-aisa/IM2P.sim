#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# 1. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run: uv run -m pytest sim/tests/cycle/test_production_sequence_work.py
# ──────────────────
from __future__ import annotations

import unittest

from sim.tests.cycle.production_sequence_work import validate_probe_log
from sim.tests.cycle.production_tag_carry import proof_document


class ProductionSequenceLogTests(unittest.TestCase):
    def test_single_instance_exact_events_and_plus_one_mutation(self) -> None:
        run = 'SEQUENCE_RUN {"instance_count":1,"reset_count":1}\n'
        work = 'SEQUENCE_WORK {"ordinal":0,"work_id":0,"work_binding":"binding"}\n'
        events = 'RTL_EVENT 0 0 42 work\nMODEL_EVENT 0 0 42 work\n'
        validate_probe_log(run + work + events, [(0, "binding")])
        with self.assertRaisesRegex(ValueError, "event"):
            validate_probe_log(run + work + events.replace('MODEL_EVENT 0 0 42',
                                                            'MODEL_EVENT 0 0 43'),
                               [(0, "binding")])

    def test_mid_sequence_reset_is_rejected(self) -> None:
        raw = ('SEQUENCE_RUN {"instance_count":1,"reset_count":2}\n'
               'SEQUENCE_WORK {"ordinal":0,"work_id":0,"work_binding":"binding"}\n'
               'RTL_EVENT 0 0 42 work\nMODEL_EVENT 0 0 42 work\n')
        with self.assertRaisesRegex(ValueError, "reset"):
            validate_probe_log(raw, [(0, "binding")])

    def test_inert_tag_carry_requires_garbage_head_and_matching_next_dequeue(self) -> None:
        rows = []
        for ordinal in range(4):
            accepted = 5 + ordinal * 100
            rows.append({'ordinal': ordinal, 'work_id': ordinal,
                         'accepted': accepted, 'first_output_cycle': accepted + 5,
                         'first_tag_dequeue_seen': True, 'first_tag_dequeue_cycle': accepted + 2,
                         'first_tag_dequeue_out_id': 1, 'first_tag_dequeue_resp_valid': 1,
                         'first_tag_dequeue_resp_last': 1, 'first_tag_dequeue_match_ready': 1,
                         'carry_in_tag_len': 0 if ordinal == 0 else 1,
                         'carry_in_tag_head_id': 0 if ordinal == 0 else 1,
                         'mesh_tag_queue_len': 1, 'mesh_tag_head_id': 1,
                         'mesh_expected_carry_id': 1, 'mesh_tag_head_rob_valid': 0,
                         'mesh_tag_head_garbage': 1, 'mesh_tag_head_is_acc': 1,
                         'mesh_tag_head_accumulate': 1, 'mesh_tag_head_full_row': 1,
                         'mesh_tag_max_occupancy': 2, 'mesh_tag_never_full': True,
                         'mesh_tag_enqueues': 6, 'mesh_tag_dequeues': 5 if ordinal == 0 else 6,
                         'first_output_tag_head_id': 2, 'first_output_matmul_id': 2,
                         'first_output_tag_head_rob_valid': 1,
                         'mesh_tag_full_backpressure_cycles': 0,
                         'passive_mesh_tag_queue_len': 1, 'passive_mesh_tag_head_id': 1,
                         'numeric_pass': True, 'result_ready': accepted + 10,
                         'model_result_ready': accepted + 10,
                         'final_scale_release': accepted + 11,
                         'model_final_scale_release': accepted + 11,
                         'resource_ready': accepted + 12,
                         'model_resource_ready': accepted + 12,
                         'next_scratchpad_half': 0, 'model_next_scratchpad_half': 0,
                         'next_accumulator_half': 0, 'model_next_accumulator_half': 0,
                         'submissions': 6, 'model_submissions': 6,
                         'scale_read_requests': 1, 'model_scale_read_requests': 1,
                         'scale_read_responses': 1, 'model_scale_read_responses': 1,
                         'scale_release_count': 16, 'model_scale_release_count': 16,
                         'load_requests': 1, 'model_load_requests': 1,
                         'load_responses': 1, 'model_load_responses': 1,
                         'store_requests': 1, 'model_store_requests': 1,
                         'store_responses': 1, 'model_store_responses': 1})
        self.assertEqual(proof_document(rows, 'raw-sha')['status'], 'PASS')
        rows[2]['mesh_tag_dequeues'] = 5
        self.assertEqual(proof_document(rows, 'raw-sha')['status'], 'NOT_READY')
        rows[2]['mesh_tag_dequeues'] = 6
        rows[2]['first_output_tag_head_id'] = 3
        self.assertEqual(proof_document(rows, 'raw-sha')['status'], 'NOT_READY')
        rows[2]['first_output_tag_head_id'] = 2
        rows[2]['mesh_tag_head_rob_valid'] = 1
        self.assertEqual(proof_document(rows, 'raw-sha')['status'], 'NOT_READY')


if __name__ == '__main__':
    unittest.main()
