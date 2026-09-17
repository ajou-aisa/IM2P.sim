#!/usr/bin/env python3
"""Single-request binding, value-free structure and artifact-independence tests."""
from __future__ import annotations

import copy
import ctypes as C
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from sim.cycle import cli


class CycleCliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        value = os.environ.get('IM2P_CYCLE_LIBRARY')
        if not value:
            raise RuntimeError('IM2P_CYCLE_LIBRARY must name the built standalone library')
        cls.library = Path(value).resolve(strict=True)

    def request(self) -> dict:
        return {'profile': 'a8w8-d32-hp1', 'timing_profile': 'rtl-regression', 'request':
                {'m': 2, 'n': 3, 'k': 64, 'tile_i': 1, 'tile_j': 1, 'tile_k': 2,
                 'accepted_cycle': 589, 'submission': 'regression-tiles', 'record_events': 1}}

    def test_scalar_only_c_request(self) -> None:
        self.assertEqual(C.sizeof(cli.Request), 120)
        self.assertEqual(C.sizeof(cli.Event), 80)
        for field in cli.Request._fields_:
            self.assertIn(field[1], (C.c_uint32, C.c_uint64))
        for forbidden in ('activations', 'weights', 'tensor', 'numerical_output', 'main', 'rmd',
                          'GPT', 'Llama', 'attention', 'MLP', 'frequency', 'fmax'):
            doc = self.request()
            doc['request'][forbidden] = 0
            with self.subTest(field=forbidden), self.assertRaises(ValueError):
                cli.estimate(self.library, doc)

    def test_numerical_inputs_are_not_accepted_at_any_level(self) -> None:
        for key in ('activations', 'weights', 'op_trace', 'cpu_cycles', 'frequency'):
            doc = self.request(); doc[key] = []
            with self.subTest(field=key), self.assertRaises(ValueError):
                cli.estimate(self.library, doc)

    def test_real_resolver_and_c_api(self) -> None:
        result = cli.estimate(self.library, self.request())
        self.assertEqual(result['result']['total_cycles'], 503)
        self.assertEqual(result['result']['load_request_count'], 68)
        self.assertEqual(result['result']['scale_request_count'], 2)
        self.assertEqual(result['result']['store_response_count'], 2)
        self.assertEqual(result['resolved_hardware']['bank_rows'], 2048)
        self.assertEqual(result['classification'], 'cycle model result')
        self.assertTrue(result['value_free'])

    def test_deterministic_result_and_event_bytes(self) -> None:
        first = json.dumps(cli.estimate(self.library, self.request()), sort_keys=True).encode()
        for _ in range(3):
            other = json.dumps(cli.estimate(self.library, self.request()), sort_keys=True).encode()
            self.assertEqual(first, other)

    def test_hardware_profile_mismatch(self) -> None:
        wrong = ROOT / 'config/gemmini_host_memory_contracts/a4w4-d16-hp1.json'
        with self.assertRaises(cli.BuildFailure):
            cli.estimate(self.library, self.request(), memory_contract=wrong)
        for name in ('a4w8-d16-hp1', 'a16w16-d16-hp1', 'a8w8-d8-hp1', 'unknown', 32):
            doc = self.request(); doc['profile'] = name
            with self.subTest(profile=name), self.assertRaises(ValueError):
                cli.estimate(self.library, doc)

    def test_invalid_shape_and_integer_conversion(self) -> None:
        for value in (0, -1, True, False, 1.5, '32', 1 << 64):
            doc = self.request(); doc['request']['m'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                cli.estimate(self.library, doc)
        for field in ('tile_i', 'tile_j', 'tile_k'):
            doc = self.request(); doc['request'][field] = 0
            with self.subTest(field=field), self.assertRaises(ValueError):
                cli.estimate(self.library, doc)

    def test_timing_admission_and_slow_reference(self) -> None:
        doc = self.request()
        doc['timing'] = {'backing_read_delay': 11, 'even_read_id_delay': 31,
                         'scale_read_extra_delay': 29, 'backing_write_delay': 37, 'read_ready_period': 3}
        doc['request']['accepted_cycle'] = 1158
        self.assertEqual(cli.estimate(self.library, doc)['result']['total_cycles'], 730)
        for invalid in ({'read_ready_period': 1}, {'backing_read_delay': -1},
                        {'backing_read_delay': 1 << 32}, {'queue_depth': 8}):
            doc['timing'] = invalid
            with self.subTest(timing=invalid), self.assertRaises(ValueError):
                cli.estimate(self.library, doc)

    def test_planner_and_hardware_loop_counts_are_distinct(self) -> None:
        doc = self.request()
        tile = cli.estimate(self.library, doc)['result']
        doc['request']['submission'] = 'planner-blocks'
        block = cli.estimate(self.library, doc)['result']
        self.assertEqual(tile['planner_loop_count'], 2)
        self.assertEqual(tile['loop_count'], 1)
        self.assertEqual(block['planner_loop_count'], 2)
        self.assertEqual(block['loop_count'], 2)
        self.assertEqual(tile['fragment_count'], block['fragment_count'])

    def test_event_identity_and_trace_is_optional(self) -> None:
        doc = self.request()
        doc['request'].update(m=65, n=65, k=64, logical_work_id=91)
        traced = cli.estimate(self.library, doc)
        events = traced['events']
        self.assertTrue(any(e['loop'] > 0 for e in events))
        self.assertTrue(all(e['logical_work_id'] == 91 and e['dependency'] < e['id'] for e in events))
        doc['request']['record_events'] = 0
        quiet = cli.estimate(self.library, doc)
        self.assertEqual(quiet['events'], [])
        expected = copy.deepcopy(traced['result']); expected['event_count'] = 0
        self.assertEqual(expected, quiet['result'])

    def test_cli_output_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory(prefix='im2p-cycle-cli-') as directory:
            root = Path(directory)
            request = root / 'request.json'; request.write_text(json.dumps(self.request()))
            out = root / 'result.json'
            command = [sys.executable, '-B', str(ROOT / 'sim/cycle/cli.py'), '--library',
                       str(self.library), '--request', str(request), '--out', str(out)]
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            before = out.read_bytes()
            repeated = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertNotEqual(repeated.returncode, 0)
            self.assertEqual(before, out.read_bytes())

    def test_independent_library_has_no_numerical_or_verilator_symbols(self) -> None:
        symbols = subprocess.run(['nm', '-u', str(self.library)], text=True,
                                 capture_output=True, check=False)
        self.assertEqual(symbols.returncode, 0, symbols.stderr)
        for marker in ('Verilated', 'VIM2P', 'ggml_', 'im2p_execute_matmul', 'im2p_start_matmul'):
            self.assertNotIn(marker, symbols.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
