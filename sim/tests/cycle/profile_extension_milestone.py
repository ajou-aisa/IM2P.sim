# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# How to run: uv run --no-project python -m sim.tests.cycle.profile_extension_milestone LIBRARY TRACE OUTPUT
"""Exact edge/boundary parity for an entire genuine producer sequence."""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import assert_never

from sim.cycle.npu_trace_schema import Record
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_binding import Code, Report, SequenceSession, Settings
from sim.cycle.sequence_trace_cli import descriptor, load_trace
from sim.cycle.stateful_sequence_evidence import StatefulCertificateError, require


def compare(library: Path, trace: Path, output: Path) -> Record:
    profile, works, digest = load_trace(trace)
    results: list[Record] = []
    for batched in (False, True):
        hashes = {name: hashlib.sha256() for name in
                  ("reports", "state", "counters", "events")}
        calls = events = 0
        with SequenceSession(library, profile, settings=Settings(max_trace_events=1024)) as session:
            handle = session._handle.value
            for item in works:
                work = replace(descriptor(item), record_events=True)
                require(session.offer(work, session.status().cursor) == Code.OK,
                        "milestone offer", str(item.identity))
                while not session.status().has_report:
                    calls += 1
                    code = (session.advance_to_boundary(1_000_000_000, 1024) if batched
                            else session.advance_until(session.status().cursor + 1))
                    require(code in (Code.OK, Code.INCOMPLETE, Code.WOULD_BLOCK),
                            "milestone advancement", str(code))
                    batch = session.read_events(1024)
                    events += len(batch)
                    for event in batch:
                        hashes["events"].update(bytes(event))
                report = session.pop_report()
                match report:
                    case Code():
                        raise StatefulCertificateError("milestone report", str(item.identity))
                    case Report():
                        pass
                    case unreachable:
                        assert_never(unreachable)
                require(report.resource_ready_cycle == session.status().cursor and
                        report.generation == 1 and session._handle.value == handle,
                        "milestone session", "reset, replacement or cycle overshoot")
                hashes["reports"].update(bytes(report))
                for snapshot in (session.domain_snapshot(), session.row_pressure(),
                                 session.mesh_state()):
                    hashes["state"].update(bytes(snapshot))
                hashes["counters"].update(bytes(session.counters()))
            result: Record = {name: value.hexdigest() for name, value in hashes.items()}
            result.update(final_cycle=session.status().cursor, event_count=events, calls=calls)
            results.append(result)
    require({key: value for key, value in results[0].items() if key != "calls"} ==
            {key: value for key, value in results[1].items() if key != "calls"},
            "milestone parity", "cycles, state, events, reports or counters differ")
    document: Record = {
        "schema": "stateful-profile-milestone-parity-v1", "status": "EXACT",
        "profile": profile, "work_count": len(works), "initial_resets_per_session": 1,
        "midrun_resets": 0, "fallbacks": 0, "cycle_stepping": results[0],
        "advance_to_boundary": results[1],
        "trace": {"path": str(trace), "sha256": digest},
        "library": {"path": str(library), "sha256": sha256(library)},
        "harness": {"path": str(Path(__file__)), "sha256": sha256(Path(__file__))},
    }
    with output.open("x") as stream:
        stream.write(json.dumps(document, indent=2, sort_keys=True) + "\n")
    return document


if __name__ == "__main__":
    library_path, trace_path, output_path = map(Path, sys.argv[1:])
    receipt = compare(library_path, trace_path, output_path)
    print(json.dumps({key: receipt[key] for key in ("status", "profile", "work_count")}))
