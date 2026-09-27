from __future__ import annotations

import ctypes as C
from pathlib import Path

import pytest

from sim.cycle import sequence_binding as binding
from sim.tests.cycle import test_sequence_binding

library = test_sequence_binding.library


@pytest.mark.parametrize("record_events", [False, True])
@pytest.mark.parametrize("profile", [
    "a4w4-d16-hp1", "a4w4-d32-hp1", "a4w4-d64-hp1",
    "a8w8-d16-hp1", "a8w8-d32-hp1", "a8w8-d64-hp1",
])
def test_milestone_matches_single_edges_without_ready_overshoot(
    library: Path, record_events: bool, profile: str,
) -> None:
    # Given: identical descriptors in separate cold native sessions.
    work = binding.Work(9, 7, 17, 33, submission=1, record_events=record_events)
    settings = binding.Settings(max_trace_events=16)
    results: list[tuple[bytes, bytes, bytes, bytes, tuple[bytes, ...], int]] = []
    for batched in (False, True):
        with binding.SequenceSession(library, profile, settings=settings) as session:
            assert session.offer(work, 0) == binding.Code.OK
            events: list[bytes] = []
            calls = 0
            # When: one driver calls each edge and the other stops at native boundaries.
            while not session.status().has_report:
                calls += 1
                if batched:
                    code = session.advance_to_boundary(100_000, 97)
                else:
                    code = session.advance_until(session.status().cursor + 1)
                assert code in (binding.Code.OK, binding.Code.INCOMPLETE, binding.Code.WOULD_BLOCK)
                events.extend(bytes(event) for event in session.read_events(16))
            cursor = session.status().cursor
            report = session.pop_report()
            assert isinstance(report, binding.Report)
            results.append((bytes(report), bytes(session.counters()), bytes(session.domain_snapshot()),
                            bytes(session.row_pressure()), tuple(events), calls))
            # Then: edge q remains unprocessed and can accept the next work without a bubble.
            assert cursor == report.resource_ready_cycle
            assert session.offer(binding.Work(10, 1, 1, 1), cursor) == binding.Code.OK
            assert session.advance_to_boundary(cursor + 1, 1) == binding.Code.INCOMPLETE
            assert session.status().accepted_cycle == cursor
    assert results[0][:-1] == results[1][:-1]
    assert results[1][-1] < results[0][-1]


def test_milestone_zero_budget_preserves_native_state(library: Path) -> None:
    # Given: a cold session with a pending descriptor.
    with binding.SequenceSession(library, "a8w8-d16-hp1") as session:
        assert session.offer(binding.Work(0, 1, 1, 1), 0) == binding.Code.OK
        before = bytes(session.status()), bytes(session.domain_snapshot())
        # When/Then: invalid soft budget raises without advancing or changing output.
        with pytest.raises(binding.SequenceError) as caught:
            session.advance_to_boundary(1000, 0)
        assert caught.value.code == binding.Code.INVALID
        assert (bytes(session.status()), bytes(session.domain_snapshot())) == before


def test_milestone_stop_reasons_remain_distinct(library: Path) -> None:
    # Given: a work longer than one native step.
    with binding.SequenceSession(library, "a8w8-d16-hp1") as session:
        assert session.offer(binding.Work(0, 1, 1, 1), 0) == binding.Code.OK
        # When: soft budget, target, and report boundaries are reached independently.
        assert session.advance_to_boundary(1000, 1) == binding.Code.INCOMPLETE
        assert session.status().stop_reason == binding.StopReason.SOFT_BUDGET
        assert session.advance_to_boundary(2, 1000) == binding.Code.INCOMPLETE
        assert session.status().stop_reason == binding.StopReason.TARGET
        assert session.advance_to_boundary(1000, 1000) == binding.Code.OK
        status = session.status()
        # Then: report availability is not conflated with target or soft-budget completion.
        assert status.stop_reason == binding.StopReason.REPORT_AVAILABLE
        assert session.advance_to_boundary(2000, 1000) == binding.Code.OK
        assert session.status().cursor == status.cursor
        assert C.sizeof(binding.Status) == 112
