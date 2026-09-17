#!/usr/bin/env python3
"""Fail closed on missing or mismatched captured production geometry."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from sim.tests.cycle.check_schedule_authority import captured_request


class ScheduleAuthorityTest(unittest.TestCase):
    def capture(self) -> dict:
        return {'kind': 'prepare_capture', 'mode': 'full', 'bits': 8, 'dim': 32,
                'shape': [129, 129, 96], 'tile': [2, 3, 2], 'stripe_rows': 64,
                'companion_exact': True, 'lowering_exact': True}

    def test_uses_captured_factors_without_retiling(self) -> None:
        row = self.capture()
        before = copy.deepcopy(row)
        result = captured_request(row, 'a8w8-d32-hp1', 19, 5)
        self.assertEqual([result['request'][k] for k in ('tile_i', 'tile_j', 'tile_k')], [2, 3, 2])
        self.assertEqual(result['request']['accepted_cycle'], 19)
        self.assertEqual(result['request']['submission'], 'planner-blocks')
        self.assertEqual(row, before)

    def test_mnk_alone_cannot_create_a_certificate(self) -> None:
        row = self.capture()
        del row['tile']
        with self.assertRaisesRegex(ValueError, 'factors are required'):
            captured_request(row, 'a8w8-d32-hp1', 0, 5)

    def test_missing_stripe_metadata_rejected(self) -> None:
        row = self.capture()
        del row['stripe_rows']
        with self.assertRaisesRegex(ValueError, 'stripe geometry'):
            captured_request(row, 'a8w8-d32-hp1', 0, 5)

    def test_profile_mismatch_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, 'profile differ'):
            captured_request(self.capture(), 'a4w4-d32-hp1', 0, 5)

    def test_pipeline_intent_is_not_a_full_rtl_certificate(self) -> None:
        row = self.capture()
        row['mode'] = 'pipeline'
        with self.assertRaisesRegex(ValueError, 'FULL prepare capture'):
            captured_request(row, 'a8w8-d32-hp1', 0, 5)

    def test_unproven_companion_or_lowerer_rejected(self) -> None:
        for key in ('companion_exact', 'lowering_exact'):
            for value in (False, None, 1):
                row = self.capture()
                row[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    captured_request(row, 'a8w8-d32-hp1', 0, 5)

    def test_zero_negative_boolean_or_missing_factor_rejected(self) -> None:
        for value in (0, -1, True, None, '2'):
            row = self.capture()
            row['tile'][2] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                captured_request(row, 'a8w8-d32-hp1', 0, 5)

    def test_changed_stripe_geometry_rejected(self) -> None:
        row = self.capture()
        row['stripe_rows'] += 1
        with self.assertRaisesRegex(ValueError, 'stripe geometry'):
            captured_request(row, 'a8w8-d32-hp1', 0, 5)

    def test_geometry_shape_must_be_complete(self) -> None:
        for value in (None, [], [1, 2], [1, 2, 3, 4], [1, 0, 32]):
            row = self.capture()
            row['shape'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                captured_request(row, 'a8w8-d32-hp1', 0, 5)

    def test_model_request_contains_no_numerical_or_rtl_answers(self) -> None:
        row = self.capture()
        row['expected_cycles'] = 123
        row['tensor'] = [1, 2, 3]
        request = captured_request(row, 'a8w8-d32-hp1', 0, 5)
        self.assertEqual(set(request), {'profile', 'timing_profile', 'request', 'timing'})
        self.assertNotIn('expected_cycles', request['request'])
        self.assertNotIn('tensor', request['request'])
        self.assertNotIn('done_cycle', request['request'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
