# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# PYTHONPATH=. uv run pytest -q sim/tests/cycle/test_sequence_repeat_stream.py
# ──────────────────
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from sim.tests.cycle import compositional_sequence_v2_runtime as runtime
from sim.tests.cycle import compositional_sequence_v2_stream as event_stream


def _log(path: Path, *, paired_omission: bool = False,
         swapped: bool = False, count: int = 2, pressure: bool = False) -> None:
    lines = ['COMPOSITION_RUN {}']
    ordinals = (1, 0) if swapped else (0, 1)
    for ordinal in ordinals:
        cycles = range(count - 1) if paired_omission and ordinal == 1 else range(count)
        lines.extend(f'RTL_EVENT {ordinal} {ordinal} {cycle} work' for cycle in cycles)
        lines.append('COMPOSITION_WORK ' + json.dumps({
            'ordinal': ordinal, 'work_id': ordinal,
            'mesh_tag_full_backpressure_cycles': 0,
        }))
        if pressure:
            lines.append('TAG_PRESSURE_V2 ' + json.dumps({
                'ordinal': ordinal, 'work_id': ordinal,
                'status': 'FULL_STALL_NOT_OBSERVED',
                'control_queue_layout_sha256':
                    'c1431c91515b9e0ef892934778b7ecc1fe9f9a489ee24b4ff3a686245a175610',
                'full_queue_cycles': 0, 'full_stall_cycles': 0,
                'legacy_heuristic_cycles': 0, 'full_dequeue_cycles': 0,
                'first_full_cycle': 0, 'first_stall_cycle': 0,
                'first_full_head_id': 0, 'first_full_read_pointer': 0,
                'first_full_write_pointer': 0, 'read_pointer_wraps': 0,
                'write_pointer_wraps': 0, 'matmul_id_wraps': 0,
            }))
    lines.extend(f'RTL_SELECTED_EVENT_COUNT {ordinal} {count}' for ordinal in range(2))
    lines.append('MODEL_RUN {}')
    for ordinal in range(2):
        cycles = range(count - 1) if paired_omission and ordinal == 1 else range(count)
        lines.extend(f'MODEL_EVENT {ordinal} {ordinal} {cycle} work' for cycle in cycles)
        lines.append('MODEL_WORK ' + json.dumps({'ordinal': ordinal, 'work_id': ordinal}))
    lines.extend(f'MODEL_SELECTED_EVENT_COUNT {ordinal} {count}' for ordinal in range(2))
    path.write_text('\n'.join(lines) + '\n')


def test_exact_stream_accepts_two_grouped_works(tmp_path: Path) -> None:
    # Given: complete producer and model event groups.
    path = tmp_path / 'raw.log'
    _log(path)

    # When: the selected-event stream is compared.
    counts = event_stream.compare_selected_events(path, (0, 1), require_counts=True)

    # Then: both work groups have exact selected-event coverage.
    assert counts == (2, 2)


def test_exact_stream_rejects_paired_omission(tmp_path: Path) -> None:
    # Given: the same RTL and model event removed while independent counts stay sealed.
    path = tmp_path / 'raw.log'
    _log(path, paired_omission=True)

    # When/Then: the count boundary rejects the incomplete event stream.
    with pytest.raises(ValueError, match='selected event count'):
        event_stream.compare_selected_events(path, (0, 1), require_counts=True)


def test_exact_stream_rejects_reordered_work(tmp_path: Path) -> None:
    # Given: both RTL work groups are present but their order is reversed.
    path = tmp_path / 'raw.log'
    _log(path, swapped=True)

    # When/Then: the source-declared ordinal order is enforced.
    with pytest.raises(ValueError, match='order'):
        event_stream.compare_selected_events(path, (0, 1), require_counts=True)


def test_exact_stream_rejects_one_event_mutation(tmp_path: Path) -> None:
    # Given: equal counts but one RTL cycle differs.
    path = tmp_path / 'raw.log'
    _log(path)
    path.write_text(path.read_text().replace('RTL_EVENT 1 1 1 work',
                                             'RTL_EVENT 1 1 2 work'))

    # When/Then: exact event keys expose the first changed cycle.
    with pytest.raises(ValueError, match='event divergence'):
        event_stream.compare_selected_events(path, (0, 1), require_counts=True)


def test_exact_stream_handles_input_larger_than_json_cap(tmp_path: Path) -> None:
    # Given: a log larger than ten MiB, with one repeated event key per side.
    path = tmp_path / 'large.log'
    count = 300_000
    with path.open('w') as stream:
        stream.write('COMPOSITION_RUN {}\n')
        stream.write('RTL_EVENT 0 0 1 work\n' * count)
        stream.write('COMPOSITION_WORK ' + json.dumps({'ordinal': 0, 'work_id': 0}) +
                     f'\nRTL_SELECTED_EVENT_COUNT 0 {count}\nMODEL_RUN {{}}\n')
        stream.write('MODEL_EVENT 0 0 1 work\n' * count)
        stream.write('MODEL_WORK ' + json.dumps({'ordinal': 0, 'work_id': 0}) +
                     f'\nMODEL_SELECTED_EVENT_COUNT 0 {count}\n')
    assert path.stat().st_size > 10 * 1024 * 1024

    # When: the file is validated through two seekable readers.
    counts = event_stream.compare_selected_events(path, (0,), require_counts=True)

    # Then: the full event count is retained without loading the file at once.
    assert counts == (count,)


def test_repeat_pressure_requires_one_record_per_work(tmp_path: Path) -> None:
    # Given: two complete pressure records bound to the work summaries.
    path = tmp_path / 'raw.log'
    _log(path, pressure=True)

    # When: the opt-in observer records are checked.
    rows = event_stream.tag_pressure_rows(path, (0, 1))

    # Then: all diagnostic work occurrences have measured status.
    assert [row['status'] for row in rows] == ['FULL_STALL_NOT_OBSERVED'] * 2


def test_repeat_pressure_rejects_missing_record(tmp_path: Path) -> None:
    # Given: one required pressure record is omitted.
    path = tmp_path / 'raw.log'
    _log(path, pressure=True)
    lines = path.read_text().splitlines()
    path.write_text('\n'.join(line for line in lines
                              if not line.startswith('TAG_PRESSURE_V2 {"ordinal": 1')) + '\n')

    # When/Then: opt-in record coverage is mandatory.
    with pytest.raises(ValueError, match='TAG_PRESSURE_V2 coverage'):
        event_stream.tag_pressure_rows(path, (0, 1))


def test_public_absolute_validator_has_no_event_bypass() -> None:
    # Given: the public validator is used by default absolute-offer modes.
    parameters = inspect.signature(runtime.validate_absolute_log).parameters

    # When/Then: callers cannot attest to event comparison on their own.
    assert 'events_verified' not in parameters
