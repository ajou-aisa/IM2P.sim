# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# How to run: PYTHONPATH=IM2P.sim python3 -B -m sim.tests.cycle.actual_trace_milestone LIBRARY TRACE OUTPUT.json
"""Exact per-edge stepping versus advance_to_boundary parity over a complete actual trace."""
from __future__ import annotations

import ctypes as C
import hashlib
import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Final

from sim.cycle.npu_trace_schema import Record
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_binding import (
    Code,
    Report,
    SequenceEvent,
    SequenceSession,
    Settings,
)
from sim.cycle.sequence_binding_abi import U64
from sim.cycle.sequence_trace_cli import descriptor, load_trace
from sim.cycle.stateful_sequence_evidence import require

BUFFER_EVENTS: Final = 1 << 20
MAX_SESSION_CYCLES: Final = 1_000_000_000
OK, INCOMPLETE, WOULD_BLOCK = int(Code.OK), int(Code.INCOMPLETE), int(Code.WOULD_BLOCK)


def run(library: Path, trace: Path, batched: bool) -> Record:
    profile, works, _ = load_trace(trace)
    hashes = {name: hashlib.sha256() for name in ("reports", "state", "counters", "events")}
    buffer = (SequenceEvent * BUFFER_EVENTS)()
    view = memoryview(buffer).cast("B")
    width, count = C.sizeof(SequenceEvent), U64()
    calls = events = 0
    settings = Settings(max_work_cycles=10_000_000, max_session_cycles=MAX_SESSION_CYCLES,
                        max_trace_events=BUFFER_EVENTS)
    with SequenceSession(library, profile, settings=settings) as session:
        lib, handle = session._native()
        original = handle.value
        cursor = session.status().cursor

        def drain() -> int:
            total = 0
            while True:
                code = lib.im2p_cycle_sequence_read_events(handle, buffer, BUFFER_EVENTS, C.byref(count))
                require(code == OK, "milestone events", f"read failed: {code}")
                if count.value == 0:
                    return total
                hashes["events"].update(view[:count.value * width])
                total += count.value

        for item in works:
            require(session.offer(replace(descriptor(item), record_events=True), cursor) == Code.OK,
                    "milestone offer", str(item.identity))
            while True:
                calls += 1
                code = (lib.im2p_cycle_sequence_advance_to_boundary(handle, MAX_SESSION_CYCLES, 1 << 16)
                        if batched else lib.im2p_cycle_sequence_advance_until(handle, cursor + 1))
                if code == WOULD_BLOCK:
                    events += drain()
                    continue
                require(code in (OK, INCOMPLETE), "milestone advance", f"work {item.identity}: {code}")
                if not batched:
                    cursor += 1
                if code == OK:
                    break
            events += drain()
            report = session.pop_report()
            require(isinstance(report, Report), "milestone report", str(item.identity))
            if not isinstance(report, Report):
                raise TypeError("report")
            cursor = session.status().cursor
            require(report.resource_ready_cycle == cursor and report.generation == 1 and handle.value == original,
                    "milestone session", f"reset, replacement or overshoot at work {item.identity}")
            hashes["reports"].update(bytes(report))
            for state in (session.domain_snapshot(), session.row_pressure(), session.mesh_state()):
                hashes["state"].update(bytes(state))
            hashes["counters"].update(bytes(session.counters()))
    result: Record = {name: digest.hexdigest() for name, digest in hashes.items()}
    result.update(final_cycle=cursor, event_count=events, calls=calls, work_count=len(works))
    return result


def compare(library: Path, trace: Path, output: Path) -> Record:
    started = time.monotonic()
    edge, boundary = run(library, trace, False), run(library, trace, True)
    exact = ({key: value for key, value in edge.items() if key != "calls"} ==
             {key: value for key, value in boundary.items() if key != "calls"})
    profile, works, digest = load_trace(trace)
    document: Record = {
        "schema": "im2p-actual-trace-milestone-parity-v1", "status": "EXACT" if exact else "MISMATCH",
        "profile": profile, "work_count": len(works), "work_ids": [work.identity for work in works],
        "initial_resets_per_session": 1, "midrun_resets": 0, "fallbacks": 0,
        "cycle_stepping": edge, "advance_to_boundary": boundary,
        "trace": {"path": str(trace), "sha256": digest},
        "library": {"path": str(library), "sha256": sha256(library)},
        "harness": {"path": str(Path(__file__)), "sha256": sha256(Path(__file__))},
        "wall_seconds": time.monotonic() - started}
    with output.open("x") as stream:
        stream.write(json.dumps(document, indent=2, sort_keys=True) + "\n")
    return document


if __name__ == "__main__":
    receipt = compare(*map(Path, sys.argv[1:4]))
    print("ACTUAL_MILESTONE " + json.dumps({key: receipt[key] for key in ("status", "work_count")}), flush=True)
    raise SystemExit(0 if receipt["status"] == "EXACT" else 1)
