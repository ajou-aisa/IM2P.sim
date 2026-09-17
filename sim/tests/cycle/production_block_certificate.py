#!/usr/bin/env python3
"""Certify planner-block cycle estimates against external integrated RTL probes."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from sim.cycle import cli
from sim.tests.cycle.rtl_hardening import (
    PROFILES, build_probe, normalized_model_events, profile_bits_dim, run_probe,
)

RESULT_MAP = {
    'start': 'start_cycle', 'done': 'done_cycle', 'cycles': 'total_cycles',
    'work_count': 'logical_work_count', 'loop_count': 'loop_count',
    'planner_loop_count': 'planner_loop_count', 'fragment_count': 'fragment_count',
    'load_req': 'load_request_count', 'load_resp': 'load_response_count',
    'store_req': 'store_request_count', 'store_resp': 'store_response_count',
    'scale_req': 'scale_request_count', 'scale_resp': 'scale_response_count',
}
TIMING_KEYS = ('backing_read_delay', 'even_read_id_delay', 'scale_read_extra_delay',
               'backing_write_delay', 'read_ready_period', 'backing_cycle_offset')


def first_event_difference(model: list[tuple[int, str]], rtl: list[tuple[int, str]]) -> dict[str, Any] | None:
    for index in range(max(len(model), len(rtl))):
        left = model[index] if index < len(model) else None
        right = rtl[index] if index < len(rtl) else None
        if left != right:
            return {'index': index, 'model': left, 'rtl': right,
                    'classification': 'EVENT_EXPANSION_OR_RESOURCE_RULE'}
    return None


def scale_memory_representable(dim: int, n: int, k: int, tile_j: int) -> tuple[bool, dict[str, int]]:
    # Production planner-block descriptors retain the global fragmentBase.  With
    # full/slot0 scaleBase=0, ScaleBackingLoader maps block rows into 256 entries.
    js = min(tile_j * dim, n)
    max_j = (js + dim - 1) // dim
    scale_blocks = (k + 31) // 32
    end_row = scale_blocks * max_j
    return end_row <= 256, {'scale_blocks': scale_blocks, 'max_j': max_j,
                            'required_end_row': end_row, 'scale_entries': 256}


def compare(golden_root: Path, observations: Path, library: Path, out: Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=False)
    probes = out / 'probes'
    probes.mkdir()
    target = json.loads((golden_root / 'cycle-regression.json').read_text())
    if target.get('status') != 'PASS' or target.get('case_count') != 268:
        raise ValueError('expected preserved 268-case shape corpus')
    rows: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    profile_summary: dict[str, dict[str, int]] = {}
    coverage = {name: [] for name in (
        'K_le_DIM', 'K_gt_DIM', 'block32_crossing', 'multiple_J', 'multiple_I',
        'DIM64_K32_split', 'A4', 'A8', 'replace_accumulate', 'final_store',
        'read_delay', 'write_delay', 'backpressure', 'load_execute_overlap')}

    for profile in PROFILES:
        bits, dim = profile_bits_dim(profile)
        executable = build_probe(golden_root, probes, profile)
        observed_rows = list(csv.reader((observations / profile / 'events.csv').open()))
        inputs = {int(row[1]): row for row in observed_rows if row and row[0] == 'CASE'}
        cases = [row for row in target['cases'] if row['profile'] == profile]
        if set(inputs) != set(range(1, len(cases) + 1)):
            raise ValueError(f'{profile}: reference observations do not cover shape corpus')
        exact = 0
        represented = 0
        for case_index, golden in enumerate(cases, 1):
            data = inputs[case_index]
            m, n, k = map(int, data[2:5])
            tile_i, tile_j, tile_k = map(int, data[6:9])
            timing5 = (int(data[9]), int(data[10]), int(data[11]), int(data[12]), int(data[13]))
            representable, scale_detail = scale_memory_representable(dim, n, k, tile_j)
            if not representable:
                excluded_request = {
                    'profile': profile, 'timing_profile': 'rtl-regression',
                    'request': {'m': m, 'n': n, 'k': k, 'tile_i': tile_i, 'tile_j': tile_j,
                                'tile_k': tile_k, 'accepted_cycle': 0,
                                'submission': 'planner-blocks', 'record_events': 0},
                    'timing': dict(zip(TIMING_KEYS, (*timing5, int(data[14])))),
                }
                model_rejected = False
                rejection = None
                try:
                    cli.estimate(library, excluded_request)
                except ValueError as error:
                    model_rejected = True
                    rejection = str(error)
                exclusions.append({
                    'profile': profile, 'case': case_index, 'shape': {'m': m, 'n': n, 'k': k},
                    'tile': [tile_i, tile_j, tile_k],
                    'reason': 'planner-block global scale row exceeds 256-entry ScaleMemory',
                    'rtl_contract': 'ScaleBackingLoader endRowWide <= scaleEntries',
                    'cycle_model_rejects': model_rejected, 'cycle_model_rejection': rejection,
                    **scale_detail,
                })
                continue
            represented += 1
            probe = run_probe(
                probes, executable, f'case-{case_index:03}', m=m, n=n, k=k,
                tile_i=tile_i, tile_j=tile_j, tile_k=tile_k, timing=timing5, raw=False)
            summary = probe['summary']
            if probe['returncode'] != 0 or not summary or not probe['work_events'] or len(probe['cases']) != 1:
                rows.append({'profile': profile, 'case': case_index, 'status': 'FAIL',
                             'returncode': probe['returncode'], 'log': probe['log'],
                             'reason': 'RTL probe failed before comparable result'})
                continue
            accepted = int(probe['work_events'][0][1])
            offset = int(probe['cases'][0][14])
            request = {
                'profile': profile, 'timing_profile': 'rtl-regression',
                'request': {'m': m, 'n': n, 'k': k, 'tile_i': tile_i, 'tile_j': tile_j,
                            'tile_k': tile_k, 'accepted_cycle': accepted,
                            'submission': 'planner-blocks', 'record_events': 1},
                'timing': dict(zip(TIMING_KEYS, (*timing5, offset))),
            }
            try:
                model = cli.estimate(library, request)
            except Exception as error:  # certificate records exact admission failure
                rows.append({'profile': profile, 'case': case_index, 'status': 'FAIL',
                             'returncode': probe['returncode'], 'log': probe['log'],
                             'reason': f'cycle model rejected production-valid RTL case: {error}'})
                continue
            rtl = {key: int(summary[key]) for key in RESULT_MAP}
            model_result = model['result']
            differences = {
                key: {'rtl': rtl[key], 'model': int(model_result[field])}
                for key, field in RESULT_MAP.items() if rtl[key] != int(model_result[field])
            }
            rtl_events = [(int(cycle), str(kind)) for cycle, kind in probe['selected_events']]
            model_events = normalized_model_events(model['events'])
            event_exact = model_events == rtl_events
            event_difference = first_event_difference(model_events, rtl_events)
            endpoint_counter_exact = not differences
            case_exact = endpoint_counter_exact and event_exact
            if case_exact:
                exact += 1
            row = {
                'profile': profile, 'case': case_index,
                'shape': {'m': m, 'n': n, 'k': k}, 'tile': [tile_i, tile_j, tile_k],
                'accepted_cycle': accepted, 'submission': 'planner-blocks',
                'rtl': rtl, 'model': {key: int(model_result[field]) for key, field in RESULT_MAP.items()},
                'endpoint_counter_exact': endpoint_counter_exact,
                'selected_event_type_multiset_exact': event_exact,
                'first_event_divergence': event_difference,
                'differences': differences, 'status': 'PASS' if case_exact else 'FAIL',
                'rtl_log': probe['log'], 'rtl_events': probe['events_path'],
            }
            rows.append(row)
            array_cycles = [cycle for cycle, kind in rtl_events if kind == 'array_input']
            load_cycles = [cycle for cycle, kind in rtl_events if kind == 'load_dma']
            conditions = {
                'K_le_DIM': k <= dim, 'K_gt_DIM': k > dim, 'block32_crossing': k > 32,
                'multiple_J': n > dim, 'multiple_I': m > dim,
                'DIM64_K32_split': dim == 64 and k > 32,
                'A4': bits == 4, 'A8': bits == 8, 'replace_accumulate': k > 32,
                'final_store': rtl['store_resp'] > 0, 'read_delay': timing5[0] > 0,
                'write_delay': timing5[3] > 0, 'backpressure': timing5[4] > 1,
                'load_execute_overlap': bool(array_cycles and any(
                    min(array_cycles) <= cycle <= max(array_cycles) for cycle in load_cycles)),
            }
            for name, present in conditions.items():
                if present:
                    coverage[name].append(f'{profile}/{case_index}')
        profile_summary[profile] = {'corpus_total': len(cases), 'representable': represented,
                                    'exact': exact, 'excluded': len(cases) - represented}
        print(profile, exact, '/', represented, 'production planner-block cases exact;',
              len(cases) - represented, 'excluded', flush=True)

    comparable = [row for row in rows if 'endpoint_counter_exact' in row]
    failures = [row for row in rows if row.get('status') != 'PASS']
    deltas = [abs(row['model']['cycles'] - row['rtl']['cycles']) for row in comparable]
    event_exact_count = sum(bool(row['selected_event_type_multiset_exact']) for row in comparable)
    missing_coverage = [name for name, examples in coverage.items() if not examples]
    exclusions_exact = all(bool(row.get('cycle_model_rejects')) for row in exclusions)
    status = ('PASS' if not failures and len(comparable) == 268 - len(exclusions) and
              event_exact_count == len(comparable) and exclusions_exact and not missing_coverage else 'FAIL')
    result = {
        'status': status, 'certificate': 'production-planner-block isolated single-GEMM slot0/full',
        'corpus_total': 268, 'representable_total': len(comparable),
        'exact_total': sum(row['status'] == 'PASS' for row in comparable),
        'excluded_total': len(exclusions), 'excluded_model_rejections_exact': exclusions_exact,
        'max_abs_delta_cycles': max(deltas) if deltas else None,
        'selected_event_type_multisets_exact': event_exact_count,
        'first_mismatch': failures[0] if failures else None,
        'profiles': profile_summary, 'exclusions': exclusions, 'coverage': coverage,
        'missing_coverage': missing_coverage, 'cases': rows,
        'scope': ('Descriptors are emitted directly from sim/common/gemmini_schedule.cpp in '
                  'planner-block order. Fresh reset per logical GEMM; full-mode host slot 0; '
                  'selected events compare sorted (cycle, normalized event type) pairs only.'),
    }
    (out / 'production-blocks-vs-rtl.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--golden-root', type=Path, required=True)
    parser.add_argument('--observations', type=Path, required=True)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = compare(args.golden_root.resolve(), args.observations.resolve(),
                     args.library.resolve(), args.out.resolve())
    print(json.dumps({key: result[key] for key in (
        'status', 'corpus_total', 'representable_total', 'exact_total', 'excluded_total',
        'max_abs_delta_cycles', 'selected_event_type_multisets_exact', 'first_mismatch')}, indent=2))
    raise SystemExit(0 if result['status'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
