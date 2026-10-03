#!/usr/bin/env python3
"""Normal-HP1 reference-memory timing for actually accepted production residuals.

No residual timing engine: reuse the existing dense-HP1 cycle API. Numerical
matrices/carriers are provided only to RTL, and not to the value-free model.
"""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from sim.tests.cycle import accepted_reference as reference
from sim.tests.cycle.rtl_hardening import sha256


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--residual-certificate', type=Path, required=True)
    parser.add_argument('--golden-root', type=Path, required=True)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.is_relative_to(ROOT):
        parser.error('evidence must be external')
    out.mkdir(parents=True, exist_ok=False)
    prior = json.loads(args.residual_certificate.read_text())
    if prior['status'] != 'PASS':
        raise ValueError('production numerical/accepted-work certificate must pass first')
    cases: list[dict[str, Any]] = []
    for profile in prior['profiles']:
        for line in Path(profile['invocations']).read_text().splitlines():
            item = json.loads(line)
            if item['status'] != 'PASS' or item['rmd_raw']:
                raise ValueError('uncertified/raw residual invocation')
            directory = Path(item['directory'])
            data = json.loads((directory / 'input.json').read_text())
            g = data['geometry']
            cases.append({
                'status': 'PASS', 'mode': 'full', 'profile': profile['profile'],
                'shape': [g['m'], g['n'], g['k']],
                'production_tile': [g['tile_i_count'], g['tile_j_count'], g['tile_k_count']],
                'expected_work_count': item['accepted_work'],
                'observations': str(directory / 'accepted.jsonl'),
                'numerical_input': str(directory / 'input.json'),
                'production_events': str(directory / 'events.csv'),
                'residual_case': item['case'], 'original_block_id': item['original_block_id']})
    manifest = out / 'accepted-production-inputs.json'
    manifest.write_text(json.dumps({'status': 'PASS', 'cases': cases,
        'input_certificate_sha256': sha256(args.residual_certificate)}, indent=2) + '\n')
    results = reference.compare(manifest, args.golden_root.resolve(),
                                args.library.resolve(), out / 'normal-hp1-reference')
    if len(results['cases']) != len(cases):
        raise ValueError('timing corpus lost accepted residual work')
    ordered = [case for profile in sorted({c['profile'] for c in cases})
               for case in cases if case['profile'] == profile]
    for original, result in zip(ordered, results['cases']):
        n = original['shape'][1]
        with Path(original['production_events']).open() as stream:
            events = list(csv.reader(stream))
        actual_scu = [list(map(int, row[2:2+n])) for row in events
                      if int(row[1]) & (1 << 11)]
        with (Path(result['case_directory']) / 'events.csv').open() as stream:
            ref_events = list(csv.reader(stream))
        reference_scu = [list(map(int, row[1:1+n])) for row in ref_events if row[0] == 'SCU']
        equal = actual_scu == reference_scu and bool(actual_scu)
        result.update(residual_case=original['residual_case'],
                      original_block_id=original['original_block_id'],
                      source_numerical_input=original['numerical_input'],
                      actual_vs_reference_scu_exact=equal,
                      scu_rows=len(actual_scu), scu_outputs=actual_scu,
                      numerical_output_exact=True, rmd_raw=False,
                      host_integer_block_multiply=False)
        if not equal:
            result['status'] = 'FAIL'
            result['scu_difference'] = {'production': actual_scu, 'reference': reference_scu}
    failed = [r for r in results['cases'] if r['status'] != 'PASS']
    results.update(status='FAIL' if failed else 'PASS',
                   cases_exact=sum(r['status'] == 'PASS' for r in results['cases']),
                   first_mismatch=failed[0] if failed else None,
                   certificate='production compact residual, normal HP1 SCU, accepted FULL work',
                   old_rmd_raw_certificate_used=False,
                   production_provider_elapsed_prediction=False,
                   physical_latency='NOT_CLAIMED')
    (out / 'residual-cycle-comparison.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps({k: results[k] for k in ('status', 'cases_total', 'cases_exact',
                                            'max_abs_delta_cycles')}))
    raise SystemExit(1 if failed else 0)


if __name__ == '__main__':
    main()
