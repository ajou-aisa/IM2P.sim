#!/usr/bin/env python3
"""Compare a value-free model against external preserved integrated RTL evidence.

Only request shape, tile, timing inputs and the accepted-clock epoch enter the
model process. RTL elapsed cycles, counters and event traces are compared AFTER
that process completes, never supplied to it or used to adjust model parameters.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess

PROFILES = tuple(f'a{b}w{b}-d{d}-hp1' for b in (4, 8) for d in (16, 32, 64))
FIELDS = ('start', 'done', 'cycles', 'work_count', 'loop_count', 'load_req', 'load_resp',
          'store_req', 'store_resp', 'scale_req', 'scale_resp')
EVENTS = {'work', 'load_issue', 'execute_issue', 'store_issue', 'context', 'load_dma',
          'read_request', 'read_response', 'scratchpad_read', 'array_input', 'raw_completed',
          'array_output', 'accumulator_write', 'accumulator_commit', 'store_dma',
          'write_request', 'write_completion', 'loop_done', 'logical_done'}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare(model: Path, golden_root: Path, observed: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=False)
    target = json.loads((golden_root / 'cycle-regression.json').read_text())
    if target['status'] != 'PASS' or target['case_count'] != 268:
        raise ValueError('expected established 268-case RTL corpus')
    rows = []
    coverage = {name: [] for name in ('K_le_DIM', 'K_gt_DIM', 'block32_crossing', 'multiple_J',
        'multiple_I', 'DIM64_K32_split', 'A4', 'A8', 'replace_accumulate', 'final_store',
        'read_delay', 'write_delay', 'backpressure', 'load_execute_overlap')}
    sources = {'model_executable': {'path': str(model), 'sha256': sha(model)},
               'golden': {'path': str(golden_root / 'cycle-regression.json'),
                          'sha256': sha(golden_root / 'cycle-regression.json')}, 'profiles': {}}
    for profile in PROFILES:
        bits, dim = int(profile[1]), int(profile.split('-d')[1].split('-')[0])
        observations = observed / profile / 'events.csv'
        evidence = json.loads((observed / profile / 'provenance.json').read_text())
        if evidence.get('runtime_log_byte_identical') is not True:
            raise ValueError('passive observer identity missing')
        all_events = list(csv.reader(observations.open()))
        inputs = {int(r[1]): r for r in all_events if r[0] == 'CASE'}
        rtl_events: dict[int, list] = {}
        for r in all_events:
            if r[0].isdigit():
                rtl_events.setdefault(int(r[0]), []).append(r)
        cases = [r for r in target['cases'] if r['profile'] == profile]
        if set(inputs) != set(range(1, len(cases) + 1)):
            raise ValueError('observer and golden logical-work coverage differ')
        manifest_path = golden_root / 'after-matrix' / profile / 'resolved-profile.json'
        manifest = json.loads(manifest_path.read_text())
        mem = manifest['memory']
        sources['profiles'][profile] = {'observer_sha256': sha(observations),
                                       'resolved_profile_sha256': sha(manifest_path)}
        dest = out / profile
        dest.mkdir()
        for case, golden in enumerate(cases, 1):
            data = inputs[case]
            rtl = golden['current']
            if list(map(int, data[2:5])) != [golden['shape'][name] for name in ('m', 'n', 'k')]:
                raise ValueError('observer request shape differs from the preserved RTL case')
            # Work acceptance is an input epoch, not a predicted duration. Compare
            # at identical absolute ready/backpressure phase; no host time model.
            work_events = [r for r in rtl_events[case] if r[2] == 'work']
            accepted = int(work_events[0][1])
            if accepted != rtl['start']:
                raise ValueError('RTL accepted-clock convention differs from golden')
            trace = dest / f'{case:03}-model-events.csv'
            command = [str(model), str(bits), str(dim), str(mem['bank_rows']), str(mem['accumulator_rows']),
                       *data[2:5], *data[6:9], str(accepted), *data[9:15], '0', '0', str(trace)]
            with (out / 'commands.jsonl').open('a') as f:
                f.write(json.dumps({'profile': profile, 'case': case, 'argv': command}) + '\n')
            run = subprocess.run(command, text=True, capture_output=True, check=False, timeout=30)
            (dest / f'{case:03}-stdout.json').write_text(run.stdout)
            (dest / f'{case:03}-stderr.log').write_text(run.stderr)
            actual = json.loads(run.stdout) if run.returncode == 0 else None
            delta = actual['cycles'] - rtl['cycles'] if actual else None
            differences = {key: {'rtl': rtl[key], 'model': actual[key] if actual else None}
                           for key in FIELDS if actual is None or rtl[key] != actual[key]}
            first = None
            event_exact = False
            if actual:
                model_events = list(csv.reader(trace.open()))
                mt = sorted((int(r[0]), {'scale_request': 'read_request',
                     'scale_response': 'read_response'}.get(r[1], r[1]))
                    for r in model_events if r[1] in EVENTS | {'scale_request', 'scale_response'})
                rt = sorted((int(r[1]), r[2]) for r in rtl_events[case] if r[2] in EVENTS)
                event_exact = mt == rt
                for i in range(max(len(mt), len(rt))):
                    a, b = mt[i] if i < len(mt) else None, rt[i] if i < len(rt) else None
                    if a != b:
                        first = {'index': i, 'model': a, 'rtl': b,
                                 'classification': 'EVENT_EXPANSION_OR_RESOURCE_RULE',
                                 'model_trace': str(trace), 'rtl_trace': str(observations)}
                        break
                array_cycles = [int(r[0]) for r in model_events if r[1] == 'array_input']
                load_cycles = [int(r[0]) for r in model_events if r[1] == 'load_dma']
                if array_cycles and any(min(array_cycles) <= x <= max(array_cycles) for x in load_cycles):
                    coverage['load_execute_overlap'].append(f'{profile}/{case}')
            endpoint_counter_exact = run.returncode == 0 and not differences
            case_exact = endpoint_counter_exact and event_exact
            row = {'profile': profile, 'case': case, 'shape': golden['shape'],
                   'input': {'tile': list(map(int, data[6:9])), 'timing': list(map(int, data[9:15])),
                             'accepted_cycle': accepted, 'submission': 'tile-coalesced-planner'},
                   'rtl': rtl, 'model': actual, 'delta_cycles': delta,
                   'endpoint_counter_exact': endpoint_counter_exact,
                   'observed_event_cycles_exact': event_exact,
                   'status': 'PASS' if case_exact else 'FAIL',
                   'counter_and_endpoint_differences': differences,
                   'first_event_divergence': first, 'returncode': run.returncode, 'error': run.stderr}
            rows.append(row)
            m, n, k = map(int, data[2:5])
            conditions = {'K_le_DIM': k <= dim, 'K_gt_DIM': k > dim,
                'block32_crossing': k > 32, 'multiple_J': n > dim, 'multiple_I': m > dim,
                'DIM64_K32_split': dim == 64 and k > 32, 'A4': bits == 4, 'A8': bits == 8,
                'replace_accumulate': k > min(dim, 32), 'final_store': bool(actual and actual['store_resp']),
                'read_delay': int(data[9]) > 0, 'write_delay': int(data[12]) > 0,
                'backpressure': int(data[13]) > 1}
            for name, present in conditions.items():
                if present: coverage[name].append(f'{profile}/{case}')
        selected = [r for r in rows if r['profile'] == profile]
        print(profile, sum(r['status'] == 'PASS' for r in selected), '/', len(selected),
              'endpoint/counter + selected per-cycle event-type multiset exact;',
              sum(r['observed_event_cycles_exact'] for r in selected), '/', len(selected),
              'selected event-type multisets exact', flush=True)
    measured = [abs(r['delta_cycles']) for r in rows if r['delta_cycles'] is not None]
    cases_exact = sum(r['status'] == 'PASS' for r in rows)
    selected_event_exact = sum(r['observed_event_cycles_exact'] for r in rows)
    first_mismatch = next(({
        'profile': r['profile'], 'case': r['case'], 'returncode': r['returncode'],
        'endpoint_counter_exact': r['endpoint_counter_exact'],
        'observed_event_cycles_exact': r['observed_event_cycles_exact'],
        'counter_and_endpoint_differences': r['counter_and_endpoint_differences'],
        'first_event_divergence': r['first_event_divergence'], 'error': r['error']}
        for r in rows if r['status'] != 'PASS'), None)
    aggregate_pass = (cases_exact == len(rows) and selected_event_exact == len(rows) and
                      first_mismatch is None)
    result = {'status': 'PASS' if aggregate_pass else 'FAIL',
              'cases_total': len(rows), 'cases_exact': cases_exact,
              'max_abs_delta_cycles': max(measured) if measured else None,
              'first_mismatch': first_mismatch,
              'all_counter_fields_exact': all(r['endpoint_counter_exact'] for r in rows),
              'observed_event_streams_exact': selected_event_exact,
              'selected_event_type_multisets_exact': selected_event_exact,
              'coverage': coverage, 'sources': sources, 'cases': rows,
              'scope': 'serialized single-GEMM regression submissions; selected events compare sorted (cycle, normalized event type) pairs only; absolute acceptance epoch supplied; no timing lookup in model'}
    (out / 'cycle-model-vs-rtl.json').write_text(json.dumps(result, indent=2) + '\n')
    if any(not examples for examples in coverage.values()): raise ValueError('required coverage missing')
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model', type=Path, required=True)
    p.add_argument('--golden-root', type=Path, required=True)
    p.add_argument('--observations', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    result = compare(a.model.resolve(), a.golden_root.resolve(), a.observations.resolve(), a.out.resolve())
    print(json.dumps({k: result[k] for k in ('status', 'cases_total', 'cases_exact', 'max_abs_delta_cycles')}))
    raise SystemExit(0 if result['status'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
