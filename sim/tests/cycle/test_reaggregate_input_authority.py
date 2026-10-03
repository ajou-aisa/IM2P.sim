from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from sim.tests.cycle.production_block_certificate import RESULT_MAP, TIMING_KEYS
from sim.tests.cycle.reaggregate_certificate import EvidenceError, mapping, reaggregate_case


class ReaggregateInputAuthorityTest(unittest.TestCase):
    def test_reference_request_ignores_observed_start_and_offset(self) -> None:
        # Given one reference request and model answer for unchanged geometry.
        profile = 'a8w8-d16-hp1'
        framing = 'regression-tiles'
        case = {'case': 'isolated', 'shape': [1, 1, 1], 'tile': [1, 1, 1],
                'timing': [3, 13, 17, 11, 5], 'raw': False}
        model_summary = {field: 0 for field in RESULT_MAP.values()}
        model_summary.update(start_cycle=1, done_cycle=40, total_cycles=39,
                             logical_work_count=1, loop_count=1)
        model_events = [(1, 'work'), (20, 'loop_done'), (40, 'logical_done')]
        request = {'profile': profile, 'timing_profile': 'rtl-regression',
                   'request': {'m': 1, 'n': 1, 'k': 1, 'tile_i': 1, 'tile_j': 1, 'tile_k': 1,
                               'accepted_cycle': 1, 'submission': framing, 'record_events': 1},
                   'timing': dict(zip(TIMING_KEYS, [*case['timing'], 5]))}
        model = {'profile': profile, 'status': 'PASS', 'value_free': True,
                 'result': model_summary,
                 'events': [{'cycle': cycle, 'type': kind} for cycle, kind in model_events]}
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            directory = evidence / 'cycle-release-certificate' / profile / framing / 'isolated'
            directory.mkdir(parents=True)
            hashes: dict[str, str] = {}

            def write(name: str, content: str) -> None:
                path = directory / name
                path.write_text(content)
                hashes[path.relative_to(evidence).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()

            write('model-request.json', json.dumps(request))
            write('model-result.json', json.dumps(model))
            request_bytes = (directory / 'model-request.json').read_bytes()
            for name, start, offset in (('control', 1, 5), ('shifted-start', 2, 5),
                                        ('shifted-offset', 1, 6)):
                with self.subTest(name=name):
                    # When only an observed work timestamp or CASE offset changes.
                    observed = {key: model_summary[field] for key, field in RESULT_MAP.items()}
                    observed['start'] = start
                    write('run.log', 'WS RTL ' + ' '.join(f'{key}={value}' for key, value in observed.items()) + '\n')
                    write('events.csv', '\n'.join([
                        f'CASE,1,1,1,1,1,1,1,1,3,13,17,11,5,{offset},0,dense',
                        f'1,{start},work', '1,20,loop_done', '1,40,logical_done',
                        'OWNERSHIP,1,1,0,0,1,1,1',
                        *(f'RELEASE,1,{21 + lane},0,{lane},1' for lane in range(16)),
                    ]) + '\n')
                    result = reaggregate_case(case, profile, framing, evidence, hashes)
                    # Then request bytes stay independent and observed answers still fail comparison.
                    self.assertEqual((directory / 'model-request.json').read_bytes(), request_bytes)
                    self.assertEqual(result['status'], 'PASS' if name == 'control' else 'FAIL')
                    differences = mapping(result['differences'])
                    if name == 'shifted-start':
                        self.assertEqual(differences['accepted_cycle'], {'rtl': 2, 'model': 1})
                    if name == 'shifted-offset':
                        self.assertEqual(differences['backing_cycle_offset'], {'rtl': 6, 'model': 5})
            write('events.csv', 'CASE,1,1,1,1,1,1,1,1,3,13,17,11,5,5,0,dense\n')
            with self.assertRaisesRegex(EvidenceError, 'accepted-work identity incomplete'):
                reaggregate_case(case, profile, framing, evidence, hashes)


if __name__ == '__main__':
    unittest.main()
