from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from sim.tests.cycle import current_rtl_certificate as certificate
from sim.tests.cycle.production_block_certificate import RESULT_MAP


class CurrentRtlInputAuthorityTest(unittest.TestCase):
    def test_reference_request_ignores_observed_start_and_offset(self) -> None:
        # Given a fixed independent model answer and valid ownership evidence.
        model_summary = {field: 0 for field in RESULT_MAP.values()}
        model_summary.update(start_cycle=1, done_cycle=40, total_cycles=39,
                             logical_work_count=1, loop_count=1)
        model_events = [(1, 'work'), (20, 'loop_done'), (40, 'logical_done')]
        model = {'result': model_summary,
                 'events': [{'cycle': cycle, 'type': kind} for cycle, kind in model_events]}
        case = {'case': 'isolated', 'shape': [1, 1, 1], 'tile': [1, 1, 1],
                'timing': [3, 13, 17, 11, 5], 'raw': False}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            events = root / 'events.csv'
            events.write_text('\n'.join([
                'OWNERSHIP,1,1,0,0,1,1,1', '1,20,loop_done',
                *(f'RELEASE,1,{21 + lane},0,{lane},1' for lane in range(16)),
            ]) + '\n')
            requests = {}
            for name, start, offset in (('control', 1, 5), ('shifted-start', 2, 5),
                                        ('shifted-offset', 1, 6)):
                # When only an observed RTL start or CASE offset changes.
                directory = root / name
                directory.mkdir()
                summary = {key: model_summary[field] for key, field in RESULT_MAP.items()}
                summary['start'] = start
                observed = {
                    'returncode': 0, 'summary': summary,
                    'selected_events': [(start, 'work'), *model_events[1:]],
                    'work_events': [['1', str(start), 'work']],
                    'cases': [['CASE', '1', '1', '1', '1', '1', '1', '1', '1',
                               '3', '13', '17', '11', '5', str(offset), '0', 'dense']],
                    'log': str(directory / 'run.log'), 'events_path': str(events),
                }
                with patch.object(certificate.rtl, 'run_probe', return_value=observed), \
                     patch.object(certificate.cli, 'estimate', return_value=model):
                    result = certificate.compare_case(root / 'probe', 'a8w8-d16-hp1',
                                                      'regression-tiles', case, root / 'model')
                requests[name] = json.loads((directory / 'model-request.json').read_text())
                # Then the independent comparison accepts only the control.
                self.assertEqual(result['status'], 'PASS' if name == 'control' else 'FAIL')
                if name == 'shifted-offset':
                    self.assertIn('backing_cycle_offset', result['differences'])
            self.assertEqual(requests['control'], requests['shifted-start'])
            self.assertEqual(requests['control'], requests['shifted-offset'])
            self.assertEqual(requests['control']['request']['accepted_cycle'], 1)
            self.assertEqual(requests['control']['timing']['backing_cycle_offset'], 5)


class PassiveCaptureLogTest(unittest.TestCase):
    def check_capture(self, original: str, observed: str) -> list[str]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            official_log = root / 'official.log'
            official_log.write_text(original)
            out = root / 'out'
            capture_dir = out / 'capture'
            capture_dir.mkdir(parents=True)
            log = capture_dir / 'run.log'
            log.write_text(observed)
            (capture_dir / 'events.csv').write_text(
                'CASE,1,1,1,1,1,1,1,1,3,13,17,11,5,5,0,dense\n')
            profile = {'command_results': [{'log': str(official_log)}]}
            with patch.object(certificate, 'build_probe', return_value=capture_dir / 'probe'), \
                 patch.object(certificate.rtl, 'run_logged', return_value=0):
                try:
                    result = certificate.capture(profile, out)
                finally:
                    self.assertEqual(official_log.read_bytes(), original.encode())
                    self.assertEqual(log.read_bytes(), observed.encode())
            return [row['case'] for row in result]

    def test_capture_keeps_original_stdout_when_only_passive_columns_differ(self) -> None:
        # Given identical numerical stdout and only extra passive summary columns.
        original = 'WS RTL FULL rows=1 cycles=10 tile=1/1/1\nNUMERIC PASS\n'
        observed = ('WS RTL FULL rows=1 cycles=10 start=1 done=11 work_count=1 loop_count=1 '
                    'load_req=0 load_resp=0 store_req=0 store_resp=0 scale_req=0 scale_resp=0 '
                    'tile=1/1/1\nNUMERIC PASS\n')
        # When capture compares the official and observed logs.
        cases = self.check_capture(original, observed)
        # Then the original fixture output is admitted and the work is captured.
        self.assertEqual(cases, ['captured-001'])

    def test_capture_ignores_complete_device_diagnostics_even_mid_line(self) -> None:
        # Given complete device diagnostics with changing clock and thread values.
        record = {'op': 'rmd.device_host_call', 'kind': 'segment',
                  'layer': 'gemmini_hp1_rmd_rtl_fixture', 'start': 0, 'end': 0, 'delta': None,
                  'ns_start': 1, 'ns_end': 2, 'tid': 3, 'valid': False,
                  'reason': 'not_thread_cpu_counter', 'operation_success': True}
        original_trace = json.dumps(record, separators=(',', ':')) + '\n'
        observed_trace = json.dumps({**record, 'ns_start': 4, 'ns_end': 5, 'tid': 6},
                                    separators=(',', ':')) + '\n'
        original = original_trace + 'WS RTL FULL rows=1 cycles=10 tile=1/1/1\nNUMERIC PASS\n'
        observed = (observed_trace +
                    'WS RTL FULL rows=1 cycles=10 start=1 done=11 work_count=1 loop_count=1 '
                    'load_req=0 load_resp=0 store_req=0 store_resp=0 scale_req=0 scale_resp=0 '
                    'tile=1/' + observed_trace + '1/1\nNUMERIC PASS\n')
        # When capture compares logs with a diagnostic interrupting a WS RTL line.
        cases = self.check_capture(original, observed)
        # Then only complete diagnostic records are discarded for comparison.
        self.assertEqual(cases, ['captured-001'])

    def test_capture_ignores_current_device_diagnostic_even_mid_line(self) -> None:
        # Given the current producer's complete diagnostic shape within stdout.
        record = {
            'op': 'rmd.device_host_call', 'kind': 'segment', 'layer': 'gemmini_hp1_rmd_rtl_fixture',
            'start': 0, 'end': 0, 'delta': None, 'cpu_work_cycles': None,
            'cpu_work_cycles_valid': False, 'cpu_work_cycles_source': None,
            'cpu_work_cycles_unit': 'cycle', 'cpu_work_cycles_reason': 'not_thread_cpu_counter',
            'thread_cpu_ns': 1, 'thread_cpu_valid': True, 'thread_cpu_reason': None,
            'host_elapsed_ns': 1, 'host_elapsed_valid': True, 'host_elapsed_reason': None,
            'host_execution_id': '1-1', 'host_start_ns': 1, 'host_end_ns': 2,
            'host_start_tid': 1, 'host_end_tid': 1, 'host_thread_id': 1, 'thread_id': 1,
            'ns_start': 1, 'ns_end': 2, 'tid': 1, 'valid': False,
            'reason': 'not_thread_cpu_counter', 'operation_success': True,
            'interval_class': 'DIAGNOSTIC', 'duration_role': 'OBSERVATION_ONLY',
            'exclusion_reason': 'outside_collection',
        }
        diagnostic = json.dumps(record, separators=(',', ':')) + '\n'
        original = 'WS RTL FULL rows=1 cycles=10 tile=1/1/1\nNUMERIC PASS\n'
        observed = ('WS RTL FULL rows=1 cycles=10 start=1 done=11 work_count=1 loop_count=1 '
                    'load_req=0 load_resp=0 store_req=0 store_resp=0 scale_req=0 scale_resp=0 '
                    'tile=1/' + diagnostic + '1/1\nNUMERIC PASS\n')
        # When capture strips only the complete diagnostic record.
        self.assertEqual(self.check_capture(original, observed), ['captured-001'])
        # Then changed diagnostic closure still fails the byte guard.
        for changed in (diagnostic.replace(',"thread_id":1', ''),
                        diagnostic.replace('"host_elapsed_ns":1', '"host_elapsed_ns":1,"extra":1')):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, 'passive observer changed'):
                self.check_capture(original, observed.replace(diagnostic, changed))

    def test_capture_rejects_other_output_and_incomplete_diagnostics(self) -> None:
        original = 'WS RTL FULL rows=1 cycles=10 tile=1/1/1\nNUMERIC PASS\n'
        observed = ('WS RTL FULL rows=1 cycles=10 start=1 done=11 work_count=1 loop_count=1 '
                    'load_req=0 load_resp=0 store_req=0 store_resp=0 scale_req=0 scale_resp=0 '
                    'tile=1/1/1\nNUMERIC PASS\n')
        trace = ('{"op":"rmd.device_host_call","kind":"segment",'
                 '"layer":"gemmini_hp1_rmd_rtl_fixture","start":0,"end":0,"delta":null,'
                 '"ns_start":1,"ns_end":2,"tid":3,"valid":false,'
                 '"reason":"not_thread_cpu_counter","operation_success":true}\n')
        for changed in (observed.replace('NUMERIC PASS', 'NUMERIC FAIL'),
                        observed.replace('cycles=10', 'cycles=11'),
                        observed + '{"op":"other","kind":"segment"}\n',
                        observed + trace[:-2],
                        observed + trace.replace(',"tid":3', ''),
                        observed + trace.replace('"ns_start":1', '"ns_start":broken')):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, 'passive observer changed'):
                self.check_capture(original, changed)

    def test_capture_rejects_newline_byte_difference(self) -> None:
        original = 'WS RTL FULL rows=1 cycles=10 tile=1/1/1\r\nNUMERIC PASS\r\n'
        observed = ('WS RTL FULL rows=1 cycles=10 start=1 done=11 work_count=1 loop_count=1 '
                    'load_req=0 load_resp=0 store_req=0 store_resp=0 scale_req=0 scale_resp=0 '
                    'tile=1/1/1\nNUMERIC PASS\n')
        with self.assertRaisesRegex(ValueError, 'passive observer changed'):
            self.check_capture(original, observed)

    def test_capture_rejects_nan_and_duplicate_diagnostic_keys(self) -> None:
        original = 'WS RTL FULL rows=1 cycles=10 tile=1/1/1\nNUMERIC PASS\n'
        observed = ('WS RTL FULL rows=1 cycles=10 start=1 done=11 work_count=1 loop_count=1 '
                    'load_req=0 load_resp=0 store_req=0 store_resp=0 scale_req=0 scale_resp=0 '
                    'tile=1/1/1\nNUMERIC PASS\n')
        trace = ('{"op":"rmd.device_host_call","kind":"segment",'
                 '"layer":"gemmini_hp1_rmd_rtl_fixture","start":0,"end":0,"delta":null,'
                 '"ns_start":1,"ns_end":2,"tid":3,"valid":false,'
                 '"reason":"not_thread_cpu_counter","operation_success":true}\n')
        for changed in (trace.replace('"ns_start":1', '"ns_start":NaN'),
                        trace.replace('"tid":3', '"tid":3,"tid":4')):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, 'passive observer changed'):
                self.check_capture(original, observed + changed)


if __name__ == '__main__':
    unittest.main(verbosity=2)
