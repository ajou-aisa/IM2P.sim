from __future__ import annotations

import json
from collections import Counter

from sim.cycle.npu_trace_schema import Record, object_value
from sim.cycle.reconstruct_graph import array
from sim.tests.cycle.compositional_sequence_v2_base import AbsoluteOfferError


def validate_log(raw: str, projection: Record) -> list[Record]:
    works = []
    runs = []
    rtl: Counter[tuple[int, int, int, str]] = Counter()
    model: Counter[tuple[int, int, int, str]] = Counter()
    counts: dict[int, tuple[int, ...]] = {}
    for line in raw.splitlines():
        if line.startswith('COMPOSITION_RUN '):
            runs.append(object_value(json.loads(line.removeprefix('COMPOSITION_RUN '))))
        elif line.startswith('COMPOSITION_WORK '):
            works.append(object_value(json.loads(line.removeprefix('COMPOSITION_WORK '))))
        elif line.startswith(('RTL_EVENT ', 'MODEL_EVENT ')):
            kind, ordinal, work_id, cycle, name = line.split()
            (rtl if kind == 'RTL_EVENT' else model)[int(ordinal), int(work_id), int(cycle), name] += 1
        elif line.startswith('MODEL_COUNTS '):
            _, ordinal, _, *values = line.split()
            counts[int(ordinal)] = tuple(map(int, values))
    expected = [object_value(row) for row in array(projection['works'])]
    if len(runs) != 1 or runs[0].get('instance_count') != 1 or runs[0].get('reset_count') != 1 or \
            runs[0].get('work_count') != len(expected) or len(works) != len(expected):
        raise AbsoluteOfferError('one-instance >4-work run evidence missing')
    if not rtl or rtl != model or {row[0] for row in rtl} != set(range(len(expected))):
        raise AbsoluteOfferError('selected RTL/model event stream differs')
    for index, (observed, requested) in enumerate(zip(works, expected, strict=True)):
        if (observed['ordinal'], observed['work_id'], observed['work_binding'], observed['slot']) != \
                (index, requested['work_id'], requested['work_binding'], requested['slot']):
            raise AbsoluteOfferError('producer identity/order/slot differs')
        if observed['offered'] != observed['predicted_offered'] or \
                observed['accepted'] != observed['predicted_accepted']:
            raise AbsoluteOfferError('independent acceptance epoch differs')
        if index and observed['offered'] != works[index-1]['resource_ready'] + requested['arrival_delay']:
            raise AbsoluteOfferError('successor offer policy differs')
        if observed['resource_ready'] != observed['predicted_resource_ready'] or \
                observed['numeric_pass'] is not True:
            raise AbsoluteOfferError('resource prediction or numeric result differs')
        modeled = counts.get(index)
        actual = tuple(observed[key] for key in ('submissions', 'scale_read_requests',
                        'scale_read_responses', 'scale_release_count', 'load_requests',
                        'load_responses', 'store_requests', 'store_responses',
                        'result_ready', 'final_scale_release', 'resource_ready',
                        'next_scratchpad_half', 'next_accumulator_half'))
        if modeled != actual:
            raise AbsoluteOfferError(f'composition counters/endpoints differ at work {index}')
    return works
