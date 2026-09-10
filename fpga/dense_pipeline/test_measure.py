#!/usr/bin/env python3
"""Measurement parser gates, independent of numerical execution."""
import unittest
from measure import summarize, verify


class Measurement(unittest.TestCase):
    def log(self):
        rows = [f'SAMPLE backend=uart fixture=a iteration={i} warmup={int(i == 0)} '
                f'service_ns={100 + i} sustained_ns={110 + i} cycles=361 request_bytes=4 '
                'response_bytes=8 transactions=2 exact=1 commit=1' for i in range(6)]
        return '\n'.join(rows + ['PERSISTENT_FULL_PASS jobs=6 raw=1536 logical=1536 padding=288 '
                                 'simulator_creates=0 simulator_executes=0 simulator_stream_begins=0'])

    def test_warmup_exclusion(self):
        summary = summarize(self.log(), 'uart', 1)
        self.assertEqual(summary['results'][0]['service_ns'], {'median': 103, 'min': 101, 'max': 105})
        self.assertEqual(summary['results'][0]['counter_derived_ns']['median'], 14440)

    def test_no_success_from_exit_only(self):
        for bad in [self.log().split('PERSISTENT_')[0], self.log().replace('simulator_creates=0', 'simulator_creates=1'),
                    self.log().replace('iteration=2', 'iteration=1'), self.log().replace('exact=1', 'exact=0'),
                    self.log().replace('jobs=6', 'jobs=5'), self.log().replace('fixture=a', 'fixture=b', 1)]:
            with self.assertRaises(ValueError):
                summarize(bad, 'uart', 1)

    def test_counts_and_mode(self):
        for bad in [self.log().replace('FULL_PASS', 'PIPELINE_PASS'), self.log().replace('cycles=361', 'cycles=0'),
                    self.log().replace('sustained_ns=110', 'sustained_ns=1'),
                    self.log().replace('raw=1536', 'raw=1535')]:
            with self.assertRaises(ValueError):
                summarize(bad, 'uart', 1, {'a': {'raw': 256, 'logical': 256, 'padding': 48}})

    def test_unsealed_host(self):
        with self.assertRaises(ValueError):
            verify({'persistent_host': '/unsealed-host', 'bitstream': '/bit', 'required_manifests': [], 'files': {},
                    'conditions': [{'suffix': suffix, 'fixtures': ['/fixture']} for suffix in
                                   ['', '-pipeline', '-live', '-live-pipeline']]})


if __name__ == '__main__':
    unittest.main()
