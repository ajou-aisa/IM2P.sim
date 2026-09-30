"""Observed host CPU core fields: validated when present, carried to the join sample, absent in older logs."""
from __future__ import annotations

import pytest

from sim.cycle.reconstruct_timing import HOST_CORE_FIELDS, duration_sample, validate_host_cores


def record(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {'op': 'cpu.mul_mat', 'thread_id': 7, 'cpu_work_cycles_valid': True,
                              'host_cpu_core_start': 2, 'host_cpu_core_end': 3, 'cpu_migrated': True,
                              'cpu_work_cycles_scope': 'user+kernel', 'cpu_work_cycles_sample_reason': None}
    row.update(changes)
    return row


def test_valid_core_fields_are_carried_to_the_duration_sample() -> None:
    validate_host_cores(record())
    validate_host_cores(record(host_cpu_core_end=2, cpu_migrated=False))
    sample = duration_sample(record())
    assert {name: sample[name] for name in HOST_CORE_FIELDS} == {name: record()[name] for name in HOST_CORE_FIELDS}


def test_logs_without_core_fields_stay_admissible() -> None:
    validate_host_cores({'op': 'cpu.mul_mat', 'thread_id': 7})


def test_unknown_core_or_cross_thread_interval_has_no_migration_claim() -> None:
    validate_host_cores(record(host_cpu_core_start=None, cpu_migrated=None))
    validate_host_cores(record(thread_id=None, cpu_migrated=None))


def test_invalid_cycles_carry_no_scope() -> None:
    validate_host_cores(record(cpu_work_cycles_valid=False, cpu_work_cycles_scope=None,
                               cpu_work_cycles_sample_reason='unavailable_event'))


@pytest.mark.parametrize('changes, reason', [
    ({'host_cpu_core_start': -1}, 'malformed host CPU core id'),
    ({'host_cpu_core_end': '3'}, 'malformed host CPU core id'),
    ({'host_cpu_core_end': 3.0}, 'malformed host CPU core id'),
    ({'host_cpu_core_end': True}, 'malformed host CPU core id'),
    ({'cpu_migrated': False}, 'migration flag differs'),
    ({'host_cpu_core_start': None}, 'migration flag differs'),
    ({'cpu_work_cycles_scope': 'user'}, 'user\\+kernel'),
    ({'cpu_work_cycles_valid': False}, 'user\\+kernel'),
])
def test_malformed_core_timing_is_rejected(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        validate_host_cores(record(**changes))


def test_partial_core_fields_are_rejected() -> None:
    row = record()
    del row['cpu_migrated']
    with pytest.raises(ValueError, match='incomplete host core timing fields'):
        validate_host_cores(row)
