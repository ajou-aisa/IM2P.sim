# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy"]
# ///
# How to run: PYTHONPATH=IM2P.sim python3 -B -m sim.tests.cycle.decode_trace_replay LIBRARY IDENTITY OUT BUDGET {boundary,edge}
"""Replay the frozen decode trace in one native session with per-work state records and event invariants.

`boundary` advances with advance_to_boundary; `edge` advances one cycle per call, as actual_trace_milestone
does. Both write identical per-work before/after records and hash every report, state snapshot, counter set
and event, so their equality is the stepping-invariance and determinism evidence. Every drained event batch
must continue the global event id sequence, belong to the offered work and stay cycle-ordered inside that
work's window, and per-work event totals must equal the counter deltas. The session budget is an explicit
input (actual_trace_replay's fixed 1e9 cannot hold the 256+128 trace) and reaching it is a hard failure.
"""
from __future__ import annotations

import ctypes as C
import hashlib
import json
import resource
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Final

import numpy as np

from sim.cycle.certificate_contract import object_value, read_document
from sim.cycle.npu_trace_schema import Record, integer
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_binding import Code, Report, SequenceEvent, SequenceSession, Settings, Status
from sim.cycle.sequence_binding_abi import U64
from sim.cycle.sequence_trace_cli import descriptor, load_trace
from sim.cycle.stateful_sequence_evidence import require
from sim.tests.cycle.actual_trace_replay import MAX_WORK_CYCLES, snapshot

SCHEMA: Final = "im2p-decode-trace-native-replay-v1"
OFFER_POLICY: Final = "back-to-back-npu-only-independent-readiness"
BUFFER_EVENTS: Final = 1 << 20
EVENT_FIELDS: Final = np.dtype({"names": ["generation", "work_ordinal", "id", "cycle", "logical_work_id"],
                                "formats": ["<u8", "<u8", "<u8", "<u8", "<u8"],
                                "offsets": [8, 16, 32, 40, 48], "itemsize": C.sizeof(SequenceEvent)})
OK, INCOMPLETE, WOULD_BLOCK = int(Code.OK), int(Code.INCOMPLETE), int(Code.WOULD_BLOCK)
# Hex span of Status.stop_reason: why the last advance call returned, the only stepping-dependent field.
STOP_REASON: Final = slice(2 * Status.stop_reason.offset, 2 * (Status.stop_reason.offset + Status.stop_reason.size))


def parity_line(line: str) -> bytes:
    """A records line with stop_reason masked, so boundary and edge replays must match byte for byte."""
    record = object_value(json.loads(line), "record")
    for side in ("before", "after"):
        state = object_value(record[side], side)
        hexed = str(state["status"])
        state["status"] = hexed[:STOP_REASON.start] + "0" * (STOP_REASON.stop - STOP_REASON.start) + hexed[STOP_REASON.stop:]
    return json.dumps(record, separators=(",", ":"), sort_keys=True).encode() + b"\n"


def replay(library: Path, identity_path: Path, output: Path, budget: int, mode: str) -> Record:
    require(mode in ("boundary", "edge") and 0 < budget < 1 << 63, "decode replay", "mode or budget invalid")
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    trace_ref = object_value(object_value(read_document(identity_path)["trace"], "identity trace")["npu_trace"], "trace")
    trace = Path(str(trace_ref["path"]))
    profile, works, trace_digest = load_trace(trace)
    require(trace_digest == trace_ref["sha256"], "decode replay", "trace differs from the frozen identity")
    hashes = {name: hashlib.sha256() for name in ("reports", "state", "counters", "events", "records_parity")}
    buffer = (SequenceEvent * BUFFER_EVENTS)()
    view, rows = memoryview(buffer).cast("B"), np.frombuffer(buffer, dtype=EVENT_FIELDS)
    count, width = U64(), C.sizeof(SequenceEvent)
    records_path = output / "works.jsonl"
    settings = Settings(max_work_cycles=MAX_WORK_CYCLES, max_session_cycles=budget, max_trace_events=BUFFER_EVENTS)
    calls, next_id, last_cycle = 0, 1, 0
    with SequenceSession(library, profile, settings=settings) as session, records_path.open("x") as stream:
        lib, handle = session._native()
        original = handle.value
        previous = snapshot(session)
        cursor = session.status().cursor
        for ordinal, work in enumerate(works):
            seen: dict[str, int | None] = {"count": 0, "first_id": next_id, "min_cycle": None, "logical_work_id": None}

            def drain() -> None:
                nonlocal next_id, last_cycle
                while True:
                    code = lib.im2p_cycle_sequence_read_events(handle, buffer, BUFFER_EVENTS, C.byref(count))
                    require(code == OK, "decode events", f"read failed at work{ordinal}: {code}")
                    if count.value == 0:
                        return
                    batch = rows[:count.value]
                    ids, cycles = batch["id"], batch["cycle"]
                    logical = int(batch["logical_work_id"][0])
                    require(bool((batch["generation"] == 1).all() and (batch["work_ordinal"] == ordinal + 1).all() and
                                 (batch["logical_work_id"] == logical).all()) and
                            seen["logical_work_id"] in (None, logical) and int(ids[0]) == next_id and
                            bool((ids[1:] == ids[:-1] + 1).all()) and int(cycles[0]) >= last_cycle and
                            bool((cycles[1:] >= cycles[:-1]).all()),
                            "decode events", f"event identity, order or ownership broken at work{ordinal}")
                    hashes["events"].update(view[:count.value * width])
                    seen.update(count=int(seen["count"] or 0) + count.value, logical_work_id=logical,
                                min_cycle=int(cycles[0]) if seen["min_cycle"] is None else seen["min_cycle"])
                    next_id, last_cycle = int(ids[-1]) + 1, int(cycles[-1])

            before = snapshot(session)
            require(before == previous and before["cursor"] == cursor, "state carry",
                    f"state changed before work{ordinal}")
            require(session.offer(replace(descriptor(work), record_events=True), cursor) == Code.OK,
                    "offer", f"work{ordinal} rejected")
            offered = cursor
            while True:
                calls += 1
                code = (lib.im2p_cycle_sequence_advance_to_boundary(handle, budget, 1 << 16) if mode == "boundary"
                        else lib.im2p_cycle_sequence_advance_until(handle, cursor + 1))
                if code == WOULD_BLOCK:
                    drain()
                    continue
                require(code in (OK, INCOMPLETE), "advance", f"work{ordinal}: code {code}")
                if mode == "edge":
                    cursor += 1
                elif code == INCOMPLETE:
                    require(session.status().cursor < budget, "advance", f"work{ordinal}: session budget exhausted")
                if code == OK:
                    break
            drain()
            report = session.pop_report()
            if not isinstance(report, Report):
                raise TypeError(f"report: work{ordinal} missing")
            after = snapshot(session)
            cursor = session.status().cursor
            events = int(seen["count"] or 0)
            require(report.resource_ready_cycle == cursor == after["cursor"] and report.generation == 1 and
                    handle.value == original and after["generation"] == 1, "native lifetime",
                    f"reset, replacement or overshoot at work{ordinal}")
            require(events > 0 and events == integer(object_value(after["counters"], "counters"), "event_count") -
                    integer(object_value(before["counters"], "counters"), "event_count") and
                    seen["min_cycle"] is not None and report.accepted_cycle <= int(seen["min_cycle"]) and
                    last_cycle <= report.resource_ready_cycle, "decode events",
                    f"event total or window differs from counters at work{ordinal}")
            hashes["reports"].update(bytes(report))
            for state in (session.domain_snapshot(), session.row_pressure(), session.mesh_state()):
                hashes["state"].update(bytes(state))
            hashes["counters"].update(bytes(session.counters()))
            record: Record = {
                "ordinal": ordinal, "work_id": work.identity, "admitted": True, "state_transition_valid": True,
                "window": {"offered_cycle": offered, "accepted_cycle": report.accepted_cycle,
                           "result_ready_cycle": report.result_ready_cycle,
                           "final_scale_release_cycle": report.final_scale_release_cycle,
                           "resource_ready_cycle": report.resource_ready_cycle,
                           "next_scratchpad_half": report.next_scratchpad_half,
                           "next_accumulator_half": report.next_accumulator_half,
                           "max_tag_occupancy": after["tag_peak"], "max_row_occupancy": after["row_peak"]},
                "events": {"count": events, "first_id": seen["first_id"], "last_id": next_id - 1,
                           "min_cycle": seen["min_cycle"], "max_cycle": last_cycle,
                           "logical_work_id": seen["logical_work_id"]},
                "before": before, "after": after}
            line = json.dumps(record, separators=(",", ":"), sort_keys=True)
            stream.write(line + "\n")
            hashes["records_parity"].update(parity_line(line))
            previous = after
            if (ordinal + 1) % 98 == 0:
                print(f"DECODE_REPLAY_PROGRESS mode={mode} works={ordinal + 1} cursor={cursor}", flush=True)
    return {
        "schema": SCHEMA, "status": "PASS", "mode": mode, "profile": profile, "work_count": len(works),
        "work_ids": [work.identity for work in works],
        "identity": {"path": str(identity_path), "sha256": sha256(identity_path)},
        "trace": {"path": str(trace), "sha256": trace_digest},
        "library": {"path": str(library), "sha256": sha256(library)},
        "records": {"path": str(records_path), "sha256": sha256(records_path)},
        "harness": {"path": str(Path(__file__)), "sha256": sha256(Path(__file__))},
        "hashes": {name: value.hexdigest() for name, value in hashes.items()}, "event_count": next_id - 1,
        "session_budget_cycles": budget, "same_handle": True, "reset_count": 1, "midrun_resets": 0, "fallbacks": 0,
        "offer_policy": OFFER_POLICY, "final_cursor": cursor, "calls": calls,
        "wall_seconds": time.monotonic() - started,
        "max_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


if __name__ == "__main__":
    library_path, identity_file, output_path = map(Path, sys.argv[1:4])
    result = replay(library_path, identity_file, output_path, int(sys.argv[4]), sys.argv[5])
    with (output_path / "report.json").open("x") as target:
        target.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print("DECODE_REPLAY_PASS " + json.dumps({key: result[key] for key in (
        "mode", "work_count", "final_cursor", "event_count")}), flush=True)
