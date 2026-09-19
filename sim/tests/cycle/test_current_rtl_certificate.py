#!/usr/bin/env python3
"""Fail-closed tests for current-source certificate construction and hard gates."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from sim.tests.cycle.current_rtl_certificate import exact_result, observed_source, ownership


class CurrentCertificateTest(unittest.TestCase):
    def test_selected_events_are_a_hard_gate_even_when_endpoints_match(self) -> None:
        expected = [(1, 'work'), (15, 'load_issue'), (40, 'logical_done')]
        self.assertTrue(exact_result({}, expected, expected))
        shifted = [(1, 'work'), (16, 'load_issue'), (40, 'logical_done')]
        self.assertFalse(exact_result({}, shifted, expected))
        self.assertFalse(exact_result({}, expected[:-1], expected))
        self.assertFalse(exact_result({}, sorted(expected + [expected[1]]), expected))
        self.assertFalse(exact_result({'cycles': {'rtl': 40, 'model': 41}}, expected, expected))
        self.assertFalse(exact_result({}, [], []))

    def test_current_probe_preserves_geometry_and_uses_existing_lowerer(self) -> None:
        planner = observed_source('planner-blocks', True)
        self.assertIn('im2p::gemmini::plan_loop(schedule, work.rows, cursor)', planner)
        self.assertIn('planned.k + planned.ks, 0, planned.first, planned.last, generation', planner)
        self.assertIn('const auto first_address = scale_base;', planner)
        self.assertIn('OWNERSHIP,', planner)
        self.assertIn('RELEASE,', planner)
        self.assertNotIn('gemmini_set_tile_ws(', planner)
        regression = observed_source('regression-tiles', True)
        self.assertNotIn('im2p::gemmini::plan_loop(schedule', regression)
        self.assertIn('k += work.plan.tile_k * dim', regression)

    def test_capture_observer_keeps_real_fixture_main_and_numerical_checks(self) -> None:
        capture = observed_source('regression-tiles', False)
        self.assertIn('run_dual_loop_overlap(state);', capture)
        self.assertIn('run_frontend_ws_rtl_fixture(capability, &state, execute);', capture)
        self.assertIn('run_rmd_ws_rtl_fixture(capability, &state, execute_scu);', capture)
        self.assertIn('hardening_provenance = "residual_hp1";', capture)
        self.assertIn('hardening_provenance = "raw_diagnostic";', capture)
        self.assertIn('smoke_output == smoke_expected', capture)
        with self.assertRaises(ValueError):
            observed_source('planner-blocks', False)

    def check_ownership(self, changes: str = '') -> dict:
        # A large logical fragment does not consume large physical addresses.
        lines = ['OWNERSHIP,1,1,512,0,255,1,2', '1,20,loop_done,0,0,0']
        lines += [f'RELEASE,1,{21 + lane},0,{lane},255' for lane in range(16)]
        lines += ['OWNERSHIP,1,40,514,0,1,1,2', '1,60,loop_done,0,0,0']
        lines += [f'RELEASE,1,{61 + lane},0,{lane},1' for lane in range(16)]
        text = '\n'.join(lines) + '\n'
        if changes == 'global_row':
            text = text.replace('RELEASE,1,21,0,0,255', 'RELEASE,1,21,256,0,255')
        elif changes == 'wrong_generation':
            text = text.replace('RELEASE,1,61,0,0,1', 'RELEASE,1,61,0,0,255')
        elif changes == 'missing_lane':
            text = text.replace('RELEASE,1,21,0,0,255\n', '')
        elif changes == 'early_release':
            text = text.replace('RELEASE,1,21,0,0,255', 'RELEASE,1,19,0,0,255')
        elif changes == 'duplicate':
            text += 'RELEASE,1,80,0,0,1\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ownership.csv'
            path.write_text(text)
            return ownership(path, 16)

    def test_global_identity_local_rows_generation_wrap_and_release(self) -> None:
        result = self.check_ownership()
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['max_fragment_base'], 514)
        self.assertEqual(result['max_physical_end_row'], 1)
        self.assertEqual(result['generation_wraps'], 1)
        self.assertEqual(result['submissions'], 2)

    def test_physical_rows_generation_and_release_are_independently_checked(self) -> None:
        for defect in ('global_row', 'wrong_generation', 'missing_lane', 'early_release', 'duplicate'):
            with self.subTest(defect=defect):
                self.assertEqual(self.check_ownership(defect)['status'], 'FAIL')


if __name__ == '__main__':
    unittest.main(verbosity=2)
