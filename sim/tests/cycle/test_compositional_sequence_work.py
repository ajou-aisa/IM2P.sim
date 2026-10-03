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
import subprocess
import sys
from pathlib import Path

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


def test_cli_v2_requires_case_before_manifest_read(tmp_path: Path) -> None:
    # Given: a v2 invocation with no case and an absent manifest.
    out = tmp_path / 'v2-out'

    # When: the public runner parses its arguments.
    result = subprocess.run(
        [sys.executable, '-B', str(Path(__file__).with_name('compositional_sequence_work.py')),
         '--stimulus', str(tmp_path / 'missing.json'), '--out', str(out)],
        capture_output=True, text=True, check=False, timeout=30,
    )

    # Then: the exact v2 argument error is reported without publishing output.
    assert result.returncode == 1
    assert result.stdout == ''
    assert result.stderr == 'compositional sequence: v2 requires --case and excludes v1 trace/delays arguments\n'
    assert not out.exists()


def test_cli_v1_requires_inputs_before_output(tmp_path: Path) -> None:
    # Given: a v1 invocation with only its output path.
    out = tmp_path / 'v1-out'

    # When: the public runner parses its arguments.
    result = subprocess.run(
        [sys.executable, '-B', str(Path(__file__).with_name('compositional_sequence_work.py')),
         '--out', str(out)],
        capture_output=True, text=True, check=False, timeout=30,
    )

    # Then: the exact v1 argument error is reported without publishing output.
    assert result.returncode == 1
    assert result.stdout == ''
    assert result.stderr == 'compositional sequence: v1 requires trace, lifecycle, semantic graph, RTL build, library and delays\n'
    assert not out.exists()


if __name__ == '__main__':
    test_rejects_one_cycle_event_mutation()
