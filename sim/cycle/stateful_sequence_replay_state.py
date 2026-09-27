"""Check every recorded native boundary of the finite original374 replay."""
from __future__ import annotations

import ctypes as C
import json
from pathlib import Path

from sim.cycle.certificate_contract import object_value
from sim.cycle.npu_trace_schema import Record, integer
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_binding import (
    DomainSnapshot,
    MeshState,
    RowPressure,
    Status,
    TagState,
)
from sim.cycle.stateful_sequence_evidence import require


def _snapshot(row: Record, completed: int) -> None:
    status = Status.from_buffer_copy(bytes.fromhex(str(row["status"])))
    domain = DomainSnapshot.from_buffer_copy(bytes.fromhex(str(row["domain"])))
    mesh = MeshState.from_buffer_copy(bytes.fromhex(str(row["mesh"])))
    pressure = RowPressure.from_buffer_copy(bytes.fromhex(str(row["rows"])))
    tags = TagState.from_buffer_copy(bytes.fromhex(str(row["tags"])))
    for key, value, version in (("status", status, 1), ("domain", domain, 2), ("mesh", mesh, 1),
                                ("rows", pressure, 1), ("tags", tags, 1)):
        require(len(bytes.fromhex(str(row[key]))) == C.sizeof(value) == value.struct_size and
                value.abi_version == version, "state transition", f"{key} ABI differs")
    counters = object_value(row["counters"], "counters")
    require(status.generation == domain.generation == mesh.generation == pressure.generation ==
            tags.generation == row["generation"] == 1 and
            status.cursor == domain.cursor == mesh.cursor == pressure.cursor == tags.cursor == row["cursor"] and
            status.has_active == status.has_pending == status.has_report == status.faulted == 0 and
            domain.resource_ready == row["ready"] == 1 and domain.ready_violation_mask == row["ready_mask"] == 0,
            "state transition", f"non-ready or mismatched epoch after {completed} works")
    require(domain.tag_count == tags.queue_len == integer(row, "tag_count") <= 6 and
            tags.enqueues - tags.dequeues == domain.tag_count and
            domain.max_tag_occupancy == integer(row, "tag_peak") <= 6 and
            domain.row_count == pressure.row_count == row["row_count"] == 0 and
            pressure.max_row_occupancy == integer(row, "row_peak") < 6 and
            all(tag.rob_valid == 0 for tag in domain.tags[:domain.tag_count]),
            "state transition", "capacity, resident tags or row domain differs")
    require(status.next_scratchpad_half == mesh.next_scratchpad_half == row["scratchpad_half"] and
            status.next_accumulator_half == mesh.next_accumulator_half == row["accumulator_half"] and
            status.next_scratchpad_half in (0, 1) and status.next_accumulator_half in (0, 1) and
            mesh.request_valid == mesh.control_valid == mesh.mesh_request_valid == mesh.mesh_request_fire == 0 and
            mesh.mesh_request_ready == 1 and counters["logical_work_count"] == completed,
            "state transition", "half carry, mesh drain or completed count differs")
    for kind in ("load", "store", "scale"):
        require(counters[f"{kind}_request_count"] == counters[f"{kind}_response_count"],
                "state transition", f"undrained {kind}")


def inspect_records(path: Path) -> Record:
    """Validate all374 native payloads and their unbroken before/after chain."""
    previous: Record | None = None
    tag_peak = row_peak = count = 0
    with path.open() as stream:
        for ordinal, line in enumerate(stream):
            row = object_value(json.loads(line), "work transition")
            require(row["ordinal"] == row["work_id"] == ordinal and
                    row["admitted"] is True and row["state_transition_valid"] is True,
                    "state transition", "work order or admission differs")
            before, after = (object_value(row[key], key) for key in ("before", "after"))
            window = object_value(row["window"], "window")
            _snapshot(before, ordinal)
            _snapshot(after, ordinal + 1)
            require(previous is None or before == previous, "state carry", f"gap before work{ordinal}")
            if ordinal == 0:
                require(before["cursor"] == before["tag_count"] == before["row_count"] ==
                        before["scratchpad_half"] == before["accumulator_half"] == 0,
                        "state carry", "cold origin differs")
            offered, accepted, result, scale, ready = (
                integer(window, key) for key in ("offered_cycle", "accepted_cycle", "result_ready_cycle",
                                                "final_scale_release_cycle", "resource_ready_cycle"))
            require(offered == before["cursor"] == accepted and accepted < result <= scale <= ready and
                    ready == after["cursor"] and window["next_scratchpad_half"] == after["scratchpad_half"] and
                    window["next_accumulator_half"] == after["accumulator_half"],
                    "state transition", f"timing or half mismatch at work{ordinal}")
            old_counts, counts = (object_value(item["counters"], "counters") for item in (before, after))
            require(integer(counts, "total_cycles") - integer(old_counts, "total_cycles") == result - accepted and
                    counts["done_cycle"] == result and integer(counts, "event_count") >=
                    integer(old_counts, "event_count"),
                    "state transition", f"counter/report mismatch at work{ordinal}")
            tag_peak = max(tag_peak, integer(after, "tag_peak"))
            row_peak = max(row_peak, integer(after, "row_peak"))
            previous = after
            count += 1
    require(count == 374 and previous is not None, "state transition", "complete original374 required")
    if previous is None:
        raise FileNotFoundError("empty state transition stream")
    return {"records": {"path": str(path), "sha256": sha256(path)}, "work_count": count,
            "work_ids": list(range(374)), "final_cursor": previous["cursor"], "tag_peak": tag_peak,
            "row_peak": row_peak, "ready_mask": 0, "counters": previous["counters"],
            "same_generation": True, "continuous_state_carry": True}
