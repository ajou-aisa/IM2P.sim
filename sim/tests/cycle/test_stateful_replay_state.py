from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal, assert_never

import pytest

from sim.cycle.npu_trace_schema import Record, object_value
from sim.cycle.stateful_sequence_evidence import StatefulCertificateError
from sim.cycle.stateful_sequence_replay_state import inspect_records

Mutation = Literal["shrink", "reorder", "tag7", "row6", "half", "epoch", "counter", "carry"]


@pytest.fixture
def records() -> Path:
    root = os.environ.get("IM2P_TAG6_EVIDENCE_ROOT")
    if root is None:
        pytest.skip("actual complete374 evidence was not supplied")
    return Path(root) / "full374/second/works.jsonl"


def test_actual374_has_unbroken_native_state_chain(records: Path) -> None:
    # Given/When: all real native records from the complete original trace are inspected.
    result = inspect_records(records)
    # Then: every work, resource epoch and native counter belongs to one state chain.
    assert result["work_count"] == 374
    assert result["final_cursor"] == 501848295
    assert (result["tag_peak"], result["row_peak"], result["ready_mask"]) == (6, 5, 0)


@pytest.mark.parametrize("mutation", ["shrink", "reorder", "tag7", "row6", "half", "epoch", "counter", "carry"])
def test_actual_transition_mutations_reject(
        records: Path, tmp_path: Path, mutation: Mutation) -> None:
    # Given: a task-owned copy of all real native records, with one targeted mutation.
    rows: list[Record] = [object_value(json.loads(line)) for line in records.read_text().splitlines()]
    after = object_value(rows[239]["after"])
    window = object_value(rows[239]["window"])
    match mutation:
        case "shrink":
            rows.pop()
        case "reorder":
            rows[238], rows[239] = rows[239], rows[238]
        case "tag7":
            after["tag_peak"] = 7
        case "row6":
            after["row_peak"] = 6
        case "half":
            after["scratchpad_half"] = 1
        case "epoch":
            window["accepted_cycle"] = 312675328
        case "counter":
            object_value(after["counters"])["logical_work_count"] = 241
        case "carry":
            rows[240]["before"] = rows[238]["after"]
        case unreachable:
            assert_never(unreachable)
    path = tmp_path / "changed.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    # When/Then: altered records cannot acquire a state-transition certificate.
    with pytest.raises(StatefulCertificateError):
        inspect_records(path)
