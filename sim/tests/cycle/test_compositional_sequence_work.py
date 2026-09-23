#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# uv run -m sim.tests.cycle.test_compositional_sequence_work
# ──────────────────
from __future__ import annotations

import json

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.npu_trace_schema import Record
from sim.tests.cycle.compositional_sequence_work import validate_log


def test_rejects_one_cycle_event_mutation() -> None:
    requested: list[Record] = [{'work_id': index, 'work_binding': f'b{index}', 'slot': 0,
                                'arrival_delay': 0} for index in range(5)]
    lines = ['COMPOSITION_RUN {"instance_count":1,"reset_count":1,"work_count":5}']
    counters = ('submissions', 'scale_read_requests', 'scale_read_responses',
                'scale_release_count', 'load_requests', 'load_responses',
                'store_requests', 'store_responses')
    for index, row in enumerate(requested):
        fields = {'ordinal': index, **row, 'offered': index + 1, 'accepted': index + 1,
                  'predicted_offered': index + 1, 'predicted_accepted': index + 1,
                  'resource_ready': index + 2, 'predicted_resource_ready': index + 2,
                  'result_ready': index + 1, 'final_scale_release': index + 1,
                  'next_scratchpad_half': 0, 'next_accumulator_half': 0,
                  'numeric_pass': True, **{key: 0 for key in counters}}
        lines.extend((f'RTL_EVENT {index} {index} {index + 1} work',
                      f'MODEL_EVENT {index} {index} {index + 1} work',
                      f'MODEL_COUNTS {index} {index} ' + ' '.join(map(str,
                          (0,) * 8 + (index + 1, index + 1, index + 2, 0, 0))),
                      'COMPOSITION_WORK ' + json.dumps(fields)))
    raw = '\n'.join(lines)
    projection: Record = {'works': list[JsonValue](requested)}
    assert len(validate_log(raw, projection)) == 5
    changed = raw.replace('MODEL_EVENT 2 2 3 work', 'MODEL_EVENT 2 2 4 work', 1)
    try:
        validate_log(changed, projection)
    except ValueError as error:
        assert 'event stream differs' in str(error)
    else:
        raise AssertionError('event +1 mutation was accepted')


if __name__ == '__main__':
    test_rejects_one_cycle_event_mutation()
