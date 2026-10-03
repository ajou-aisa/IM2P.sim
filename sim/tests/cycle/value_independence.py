#!/usr/bin/env python3
"""Compare the existing extremal/zero RTL fixtures at identical ready phase.

Only a pre-work idle phase alignment is added in an external test copy. The
existing numerical oracles are unchanged. Neither values nor RTL answers become
cycle-model inputs. This is a new paired timing test, NOT replacement golden data.
"""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from sim.cycle import cli
from sim.tests.cycle.compare_rtl import EVENTS, FIELDS, PROFILES
from sim.tests.cycle.rtl_observer import observe


def validate(golden: Path, output: Path, library: Path) -> None:
    observe(golden, output, list(PROFILES), value_pair_phase=True)
    results = []
    for profile in PROFILES:
        observed = list(csv.reader((output / profile / 'events.csv').open()))
        pairs = [r for r in observed if r[0] == 'VALUE_INPUT']
        if len(pairs) != 2 or list(map(int, pairs[0][2:])) != [32, 32] or list(map(int, pairs[1][2:])) != [0, 32]:
            raise ValueError('existing extremal/zero numerical inputs not distinguished')
        all_work = []
        for line in (output / (profile + '-run.log')).read_text().splitlines():
            if line.startswith('WS RTL '):
                all_work.append({key: int(value) for key, value in re.findall(r'\b(\w+)=(\d+)\b', line)})
        ids = [int(p[1]) for p in pairs]
        rtl = [all_work[i - 1] for i in ids]
        timing = next(r for r in observed if r[0] == 'CASE' and int(r[1]) == ids[0])
        if any((r['start'] + int(timing[14])) % int(timing[13]) != 0 for r in rtl):
            raise ValueError('paired fixture did not align actual acceptance phase')
        counter_keys = [key for key in FIELDS if key not in ('start', 'done')]
        if any(rtl[0][key] != rtl[1][key] for key in counter_keys):
            raise ValueError(f'{profile}: numerical values changed elapsed or counters at identical phase')
        signatures = []
        for case, row in zip(ids, rtl):
            signatures.append(sorted((int(r[1]) - row['start'], r[2]) for r in observed
                if r[0] == str(case) and r[2] in EVENTS and int(r[1]) <= row['done']))
        if signatures[0] != signatures[1]:
            raise ValueError(f'{profile}: relative event cycles changed with numerical values')
        metadata = next(r for r in observed if r[0] == 'CASE' and int(r[1]) == ids[0])
        request = {'profile': profile, 'timing_profile': 'rtl-regression',
            'request': {'m': 1, 'n': 1, 'k': 32, 'tile_i': int(metadata[6]), 'tile_j': int(metadata[7]),
                'tile_k': int(metadata[8]), 'accepted_cycle': 0, 'record_events': 1, 'submission': 'regression-tiles'},
            'timing': dict(zip(('backing_read_delay', 'even_read_id_delay', 'scale_read_extra_delay',
                               'backing_write_delay', 'read_ready_period', 'backing_cycle_offset'),
                              map(int, metadata[9:15])))}
        first = cli.estimate(library, request)
        second = cli.estimate(library, request)
        if json.dumps(first, sort_keys=True) != json.dumps(second, sort_keys=True):
            raise ValueError('same timing request produced nondeterministic model output')
        predicted = first['result']
        renamed = {'cycles': 'total_cycles', 'work_count': 'logical_work_count', 'loop_count': 'loop_count',
            'load_req': 'load_request_count', 'load_resp': 'load_response_count',
            'store_req': 'store_request_count', 'store_resp': 'store_response_count',
            'scale_req': 'scale_request_count', 'scale_resp': 'scale_response_count'}
        if any(rtl[0][key] != predicted[renamed[key]] for key in counter_keys):
            raise ValueError(f'{profile}: pair cycle model comparison failed')
        model_events = sorted((e['cycle'], {'scale_request':'read_request', 'scale_response':'read_response'}.get(e['type'], e['type']))
            for e in first['events'] if e['type'] in EVENTS | {'scale_request', 'scale_response'})
        if model_events != signatures[0]:
            raise ValueError(f'{profile}: paired RTL/model event comparison failed')
        results.append({'profile': profile, 'status': 'PASS', 'rtl_cases': ids,
                        'rtl_inputs': {'first_nonzero_a_w': [32, 32], 'second_nonzero_a_w': [0, 32]},
                        'rtl_original_numerical_assertions': 'PASS', 'elapsed_cycles': rtl[0]['cycles'],
                        'relative_event_cycles_exact': True, 'model_deterministic_bytes': True,
                        'model_counter_comparison': 'PASS', 'model_input': request})
        print(profile + ' value-independence pair PASS', flush=True)
    (output / 'value-independence.json').write_text(json.dumps({
        'status': 'PASS', 'profiles': results, 'rtl_value_pairs': len(results),
        'rtl_logical_works': len(results) * 2, 'production_rtl_changed': False,
        'numerical_oracle_changed': False, 'model_received_values': False,
        'test_control': 'idle before each of two original raw-boundary invocations until backing clock ready phase zero'}, indent=2) + '\n')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--golden-root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--library', type=Path, required=True)
    args = parser.parse_args()
    validate(args.golden_root.resolve(), args.out.resolve(), args.library.resolve())


if __name__ == '__main__':
    main()
