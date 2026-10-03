from __future__ import annotations

from sim.cycle.npu_trace_schema import INPUT_KEYS, Record, object_value
from sim.cycle.reconstruct_graph import array
from sim.tests.cycle.compositional_sequence_v2_stimulus import validate_stimulus


def numeric_text(projection: Record) -> str:
    lines = ['IM2P_COMPOSITIONAL_SEQUENCE_V1 ' + str(projection['profile']), 'PERIOD 5']
    works = array(projection['works'])
    lines.append(f'WORKS {len(works)}')
    for raw in works:
        row = object_value(raw)
        trace = object_value(row['trace_record'])
        inputs = object_value(row['input'])
        runs = array(row['runs'])
        fields = (row['ordinal'], row['work_id'], 'R' if row['scope'] == 'residual_compact' else 'D',
                  row['slot'], row['arrival_delay'], trace['parent_id'], trace['call_id'],
                  trace['stripe_id'] if trace['stripe_id'] is not None else 0,
                  trace['row_begin'], trace['parent_m'], *(inputs[key] for key in INPUT_KEYS),
                  row['original_k'] if row['original_k'] is not None else 0,
                  row['work_binding'], len(runs))
        lines.append('W ' + ' '.join(map(str, fields)))
        for run in runs:
            span = object_value(run)
            lines.append('R ' + ' '.join(str(span[key]) for key in
                        ('original_block_id', 'original_k_mask', 'compact_k_begin', 'compact_k_count')))
    return '\n'.join(lines) + '\n'


def absolute_numeric_text(stimulus: Record, *, expected_stimulus_sha256: str | None = None,
                          expected_repeats: int | None = None) -> str:
    validate_stimulus(stimulus, expected_stimulus_sha256=expected_stimulus_sha256,
                      expected_repeats=expected_repeats)
    lines = ['IM2P_COMPOSITIONAL_SEQUENCE_V2 ' + str(stimulus['profile']), 'PERIOD 5']
    works = [object_value(row) for row in array(stimulus['works'])]
    lines.extend((f'WORKS {len(works)}', f'DIGEST {stimulus["stimulus_sha256"]}'))
    for work in works:
        inputs = object_value(work['input'])
        if 'synthetic_identity' in work:
            trace = object_value(work['synthetic_identity'])
        elif 'trace_record' in work:
            trace = object_value(work['trace_record'])
        else:
            trace = {'parent_id': 0, 'call_id': 0, 'stripe_id': 0,
                     'row_begin': 0, 'parent_m': inputs['m']}
        runs = [object_value(row) for row in array(work['runs'])]
        fields = (work['ordinal'], work['work_id'],
                  'R' if work['scope'] == 'residual_compact' else 'D', work['slot'], 0,
                  trace['parent_id'], trace['call_id'],
                  trace['stripe_id'] if trace['stripe_id'] is not None else 0,
                  trace['row_begin'], trace['parent_m'], *(inputs[key] for key in INPUT_KEYS),
                  work['original_k'] if work['original_k'] is not None else 0,
                  work['work_binding'], len(runs))
        lines.append('W ' + ' '.join(map(str, fields)))
        for run in runs:
            lines.append('R ' + ' '.join(str(run[key]) for key in
                         ('original_block_id', 'original_k_mask', 'compact_k_begin', 'compact_k_count')))
        lines.append(f'A {work["ordinal"]} {work["request_available_cycle"]} {work["port_offer_cycle"]}')
    return '\n'.join(lines) + '\n'
