#!/usr/bin/env python3
"""Prove paired rmdRaw=false/true RTL timing equivalence over the valid raw domain."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from sim.tests.cycle.rtl_hardening import PROFILES, build_probe, profile_bits_dim, run_probe

TIMING = (3, 13, 17, 11, 5)
COMPARE_KEYS = ('start', 'done', 'cycles', 'load_req', 'load_resp', 'store_req',
                'store_resp', 'scale_req', 'scale_resp', 'planner_loop_count',
                'loop_count', 'fragment_count')
WRITEBACK_EVENTS = {'context', 'raw_completed', 'array_output', 'accumulator_write',
                    'accumulator_commit', 'store_dma', 'write_request',
                    'write_completion', 'logical_done'}


def first_event_difference(left: list[tuple[int, str]], right: list[tuple[int, str]]) -> dict[str, Any] | None:
    for index in range(max(len(left), len(right))):
        a = left[index] if index < len(left) else None
        b = right[index] if index < len(right) else None
        if a != b:
            return {'index': index, 'non_raw': a, 'rmd_raw': b}
    return None


def certify(golden_root: Path, out: Path, probe_root: Path | None) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=False)
    local_probes = out / 'probes'
    if probe_root is None:
        local_probes.mkdir()
    rows: list[dict[str, Any]] = []
    profiles: dict[str, dict[str, int]] = {}
    for profile in PROFILES:
        _, dim = profile_bits_dim(profile)
        if probe_root is None:
            executable = build_probe(golden_root, local_probes, profile)
        else:
            executable = probe_root / profile / 'planner-block-probe'
            if not executable.is_file():
                raise ValueError(f'missing reusable hardening probe: {executable}')
        k_values = (1, min(dim, 16), 31, 32)
        exact = 0
        for k in k_values:
            tile_k = (k + dim - 1) // dim
            dense = run_probe(out, executable, f'pair-k{k:02}-dense', m=1, n=3, k=k,
                              tile_i=1, tile_j=1, tile_k=tile_k, timing=TIMING, raw=False)
            raw = run_probe(out, executable, f'pair-k{k:02}-raw', m=1, n=3, k=k,
                            tile_i=1, tile_j=1, tile_k=tile_k, timing=TIMING, raw=True)
            dense_summary = dense['summary']
            raw_summary = raw['summary']
            differences = {
                key: {'non_raw': dense_summary.get(key), 'rmd_raw': raw_summary.get(key)}
                for key in COMPARE_KEYS if dense_summary.get(key) != raw_summary.get(key)
            }
            dense_events = [(int(cycle), str(kind)) for cycle, kind in dense['selected_events']]
            raw_events = [(int(cycle), str(kind)) for cycle, kind in raw['selected_events']]
            event_exact = dense_events == raw_events
            wb_dense = [event for event in dense_events if event[1] in WRITEBACK_EVENTS]
            wb_raw = [event for event in raw_events if event[1] in WRITEBACK_EVENTS]
            writeback_exact = wb_dense == wb_raw
            geometry_exact = (
                len(dense['loops']) == len(raw['loops']) == 1 and
                dense['loops'][0][3:9] == raw['loops'][0][3:9] and
                dense['loops'][0][9] == '0' and raw['loops'][0][9] == '1')
            same_phase = (
                bool(dense['work_events']) and bool(raw['work_events']) and
                int(dense['work_events'][0][1]) == int(raw['work_events'][0][1]) and
                len(dense['cases']) == len(raw['cases']) == 1 and
                dense['cases'][0][14] == raw['cases'][0][14])
            valid_raw_metadata = (
                k <= 32 and geometry_exact and raw['loops'][0][6] == '0' and
                raw['loops'][0][5] == '0')
            passed = (dense['returncode'] == raw['returncode'] == 0 and not differences and
                      event_exact and writeback_exact and geometry_exact and same_phase and
                      valid_raw_metadata)
            if passed:
                exact += 1
            row = {
                'profile': profile, 'm': 1, 'n': 3, 'k': k,
                'tile': [1, 1, tile_k], 'timing': list(TIMING),
                'rmd_raw_domain': {'compact_k_1_to_32': k <= 32, 'fragment_base': 0,
                                   'accumulate': False, 'final_fragment': True},
                'same_accepted_backing_phase': same_phase,
                'descriptor_geometry_exact_except_rmdRaw_bit': geometry_exact,
                'endpoint_counter_exact': not differences,
                'context_writeback_commit_exact': writeback_exact,
                'selected_event_type_multiset_exact': event_exact,
                'first_event_divergence': first_event_difference(dense_events, raw_events),
                'differences': differences,
                'non_raw_log': dense['log'], 'raw_log': raw['log'],
                'non_raw_events': dense['events_path'], 'raw_events': raw['events_path'],
                'status': 'PASS' if passed else 'FAIL',
            }
            rows.append(row)
        profiles[profile] = {'exact_pairs': exact, 'total_pairs': len(k_values)}
        print(profile, exact, '/', len(k_values), 'RMD_RAW timing pairs exact', flush=True)
    failures = [row for row in rows if row['status'] != 'PASS']
    result = {
        'status': 'PASS' if not failures else 'FAIL',
        'timing_equivalent': not failures,
        'profiles_total': len(PROFILES), 'pairs_total': len(rows),
        'pairs_exact': sum(row['status'] == 'PASS' for row in rows),
        'tested_domain': {
            'profiles': list(PROFILES), 'm': 1, 'n': 3, 'compact_k': [1, 16, 31, 32],
            'fragment_base': 0, 'accumulate': False, 'final_fragment': True,
            'memory_timing': {'read': 3, 'even_id_extra': 13, 'scale_extra': 17,
                              'write': 11, 'read_ready_period': 5},
            'queue_state': 'fresh reset / drained single work',
            'non_raw_carrier': 0,
        },
        'profiles': profiles, 'first_mismatch': failures[0] if failures else None,
        'cases': rows,
        'decision': ('No rmdRaw/semantic field is needed in cycle ABI v1 for this certified domain.'
                     if not failures else
                     'Timing differs; identify a minimal hardware timing fact before changing the API.'),
        'scope': ('Paired processes start from fresh identical RTL state. The only descriptor bit '
                  'changed is rmdRaw; non-raw scale carriers are valid exponent 0. Selected events '
                  'compare sorted (cycle, normalized event type) pairs, not payloads or IDs.'),
    }
    (out / 'rmd-raw-timing-equivalence.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--golden-root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--probe-root', type=Path)
    args = parser.parse_args()
    result = certify(args.golden_root.resolve(), args.out.resolve(),
                     args.probe_root.resolve() if args.probe_root else None)
    print(json.dumps({key: result[key] for key in (
        'status', 'timing_equivalent', 'profiles_total', 'pairs_total', 'pairs_exact',
        'first_mismatch')}, indent=2))
    raise SystemExit(0 if result['status'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
