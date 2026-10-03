from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from sim.cycle.npu_trace_schema import Record
from sim.tests.cycle.compositional_sequence_mesh import compare_mesh_states
from sim.tests.cycle.compositional_sequence_v2_base import AbsoluteOfferError


def _records(side: str) -> list[Record]:
    return [{
        "mesh_schema": 1, "generation": 1, "cycle": cycle, "ordinal": 239, "work_id": 239,
        "request_valid": 0, "request_rows": 0, "request_counter": 0, "written_mask": 0,
        "matmul_id": 4, "control_valid": 1, "control_first": 1, "control_fire_mask": 7,
        "side_valid_mask": 7, "side_ready_mask": 7, "attempt_valid": 1, "request_ready": 0,
        "request_fire": 0, "stall_reason_mask": 2, "tag_count": 6, "row_count": 5,
        "tag_read": 1, "tag_write": 1, "row_read": 2, "row_write": 1,
        "tag_valid": 1, "row_valid": 1, "owner_generation": 0, "owner_ordinal": 0, "owner_work": 0,
        "owner_source": "active-manifest-attribution" if side == "RTL" else "native-origin",
        "pointer_source": "physical-register" if side == "RTL" else "logical-fifo-accounting",
    } for cycle in range(10, 13)]


def _write(path: Path, rows: list[Record]) -> str:
    payload = b"".join((json.dumps(row, sort_keys=True) + "\n").encode() for row in rows)
    with gzip.open(path, "wb") as stream:
        stream.write(payload)
    return hashlib.sha256(payload).hexdigest()


def test_full_queue_stalls_preserve_every_cycle_and_hash(tmp_path: Path) -> None:
    rtl, model = tmp_path / "rtl.gz", tmp_path / "model.gz"
    left = _write(rtl, _records("RTL"))
    right = _write(model, _records("MODEL"))
    result = compare_mesh_states(rtl, model, ordinal=239, work_id=239, first_cycle=10, last_cycle=12)
    assert (result.cycles, result.tag_peak, result.row_peak) == (3, 6, 5)
    assert (result.full_cycles, result.full_stall_cycles, result.first_full_cycle) == (3, 3, 10)
    assert (result.rtl_sha256, result.model_sha256) == (left, right)


def test_tag_seven_rejected_even_when_both_sides_claim_it(tmp_path: Path) -> None:
    rtl, model = tmp_path / "rtl.gz", tmp_path / "model.gz"
    for side, path in (("RTL", rtl), ("MODEL", model)):
        rows = _records(side)
        rows[1]["tag_count"] = 7
        rows[1]["tag_write"] = 2
        _write(path, rows)
    with pytest.raises(AbsoluteOfferError):
        compare_mesh_states(rtl, model, ordinal=239, work_id=239, first_cycle=10, last_cycle=12)


@pytest.mark.parametrize(("field", "value"), [
    ("matmul_id", 3), ("row_count", 4), ("owner_work", 238),
    ("cycle", 12), ("tag_write", 2), ("request_ready", 1),
    ("request_fire", 1), ("side_ready_mask", 0), ("control_first", 0),
    ("tag_count", True),
])
def test_state_mutations_rejected(tmp_path: Path, field: str, value: int | bool) -> None:
    rtl, model = tmp_path / "rtl.gz", tmp_path / "model.gz"
    _write(rtl, _records("RTL"))
    rows = _records("MODEL")
    rows[1][field] = value
    _write(model, rows)
    with pytest.raises(AbsoluteOfferError):
        compare_mesh_states(rtl, model, ordinal=239, work_id=239, first_cycle=10, last_cycle=12)


@pytest.mark.parametrize("mutation", ["omit", "duplicate", "extra", "epoch"])
def test_edge_coverage_and_declared_epoch_rejected(tmp_path: Path, mutation: str) -> None:
    rtl, model = tmp_path / "rtl.gz", tmp_path / "model.gz"
    left, right = _records("RTL"), _records("MODEL")
    if mutation == "omit":
        del right[1]
    elif mutation == "duplicate":
        right.insert(1, dict(right[0]))
    elif mutation == "extra":
        right.append(dict(right[-1]) | {"cycle": 13})
    _write(rtl, left)
    _write(model, right)
    with pytest.raises(AbsoluteOfferError):
        compare_mesh_states(rtl, model, ordinal=239, work_id=239,
                            first_cycle=11 if mutation == "epoch" else 10, last_cycle=12)


def test_ready_low_without_an_attempt_is_not_a_stall(tmp_path: Path) -> None:
    rtl, model = tmp_path / "rtl.gz", tmp_path / "model.gz"
    for side, path in (("RTL", rtl), ("MODEL", model)):
        rows = _records(side)
        for row in rows:
            row.update(control_valid=0, control_first=0, control_fire_mask=0,
                       side_valid_mask=0, attempt_valid=0)
        _write(path, rows)
    result = compare_mesh_states(rtl, model, ordinal=239, work_id=239, first_cycle=10, last_cycle=12)
    assert result.full_cycles == 3
    assert result.full_stall_cycles == 0
