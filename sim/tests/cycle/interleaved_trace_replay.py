# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# How to run: PYTHONPATH=IM2P.sim python3 -B -m sim.tests.cycle.interleaved_trace_replay LIBRARY TRACE STIMULUS OUT [REFERENCE]
"""Replay a complete actual trace in one native session under a CPU/NPU-interleaved issue sequence.

Identical to actual_trace_replay except the offer epoch: each work is offered at
max(request-available cycle, previous resource-ready cycle) from the sealed stimulus, and that
epoch must equal the scheduler's recorded port offer. Idle gaps are executed, never removed.
"""
from __future__ import annotations

import json
import resource
import sys
import time
from pathlib import Path

from sim.cycle.certificate_contract import array_value, read_document
from sim.cycle.npu_trace_schema import Record
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_binding import Code, Report, SequenceSession, Settings
from sim.cycle.sequence_trace_cli import descriptor, load_trace
from sim.cycle.stateful_sequence_evidence import require
from sim.tests.cycle.actual_trace_replay import MAX_SESSION_CYCLES, MAX_WORK_CYCLES, snapshot


def issue_sequence(stimulus: Record, work_count: int) -> tuple[list[int], list[int]]:
    available = [int(str(value)) for value in array_value(stimulus["request_available_cycles"], "availability")]
    ports = [int(str(value)) for value in array_value(stimulus["scheduler_port_offers"], "port offers")]
    require(len(available) == len(ports) == work_count and available == sorted(available),
            "issue sequence", "complete non-decreasing availability and port offers required")
    return available, ports


def replay(library: Path, trace: Path, stimulus_path: Path, output: Path, reference: Path | None) -> Record:
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    profile, works, trace_digest = load_trace(trace)
    stimulus = read_document(stimulus_path)
    require(stimulus["trace"] == {"path": str(trace), "sha256": trace_digest} and stimulus["profile"] == profile,
            "stimulus", "trace or profile differs from the sealed issue stimulus")
    available, ports = issue_sequence(stimulus, len(works))
    expected: list[str] = []
    if reference is not None:
        prior = json.loads(reference.read_text())
        records_ref = prior["records"]
        require(prior["status"] == "PASS" and prior["trace"]["sha256"] == trace_digest and
                sha256(Path(records_ref["path"])) == records_ref["sha256"], "fresh replay", "reference differs")
        expected = Path(records_ref["path"]).read_text().splitlines()
    records_path = output / "works.jsonl"
    settings = Settings(max_work_cycles=MAX_WORK_CYCLES, max_session_cycles=MAX_SESSION_CYCLES)
    idle = 0
    with SequenceSession(library, profile, settings=settings) as session, records_path.open("x") as stream:
        handle = session._handle.value
        require(session.identity is not None, "native identity", "missing")
        previous = snapshot(session)
        for ordinal, work in enumerate(works):
            before = snapshot(session)
            require(before == previous, "state carry", f"state changed before work{ordinal}")
            cursor = session.status().cursor
            offered = max(available[ordinal], cursor)
            require(offered == ports[ordinal], "issue sequence", f"work{ordinal} offer differs from scheduler")
            idle += offered - cursor
            require(session.offer(descriptor(work), offered) == Code.OK, "offer", f"work{ordinal} rejected")
            while (code := session.advance_to_boundary(MAX_SESSION_CYCLES, 1 << 16)) == Code.INCOMPLETE:
                pass
            require(code == Code.OK, "advance", f"work{ordinal}: {code.name}")
            report = session.pop_report()
            require(isinstance(report, Report), "report", f"work{ordinal} missing")
            if not isinstance(report, Report):
                raise TypeError("report")
            after = snapshot(session)
            require(session._handle.value == handle and after["generation"] == 1, "native lifetime",
                    f"handle or generation changed at work{ordinal}")
            record: Record = {
                "ordinal": ordinal, "work_id": work.identity, "admitted": True, "state_transition_valid": True,
                "request_available_cycle": available[ordinal], "idle_gap_cycles": offered - cursor,
                "window": {"offered_cycle": offered, "accepted_cycle": report.accepted_cycle,
                           "result_ready_cycle": report.result_ready_cycle,
                           "final_scale_release_cycle": report.final_scale_release_cycle,
                           "resource_ready_cycle": report.resource_ready_cycle,
                           "next_scratchpad_half": report.next_scratchpad_half,
                           "next_accumulator_half": report.next_accumulator_half,
                           "max_tag_occupancy": after["tag_peak"], "max_row_occupancy": after["row_peak"]},
                "before": before, "after": after}
            line = json.dumps(record, separators=(",", ":"), sort_keys=True)
            require(not expected or expected[ordinal] == line, "fresh replay",
                    f"first report/state/counter difference at work{ordinal}")
            stream.write(line + "\n")
            previous = after
            if (ordinal + 1) % 32 == 0:
                print(f"INTERLEAVED_REPLAY_PROGRESS works={ordinal + 1} cursor={after['cursor']}", flush=True)
    require(not expected or len(expected) == len(works), "fresh replay", "work count differs")
    return {
        "schema": "im2p-interleaved-trace-native-replay-v1", "status": "PASS", "profile": profile,
        "work_count": len(works), "work_ids": [work.identity for work in works],
        "trace": {"path": str(trace), "sha256": trace_digest},
        "stimulus": {"path": str(stimulus_path), "sha256": sha256(stimulus_path)},
        "library": {"path": str(library), "sha256": sha256(library)},
        "records": {"path": str(records_path), "sha256": sha256(records_path)},
        "harness": {"path": str(Path(__file__)), "sha256": sha256(Path(__file__))},
        "same_handle": True, "reset_count": 1, "midrun_resets": 0, "fallbacks": 0,
        "offer_policy": "cpu-availability-driven-independent-readiness", "idle_gap_cycles": idle,
        "fresh_reference": None if reference is None else {"path": str(reference), "sha256": sha256(reference)},
        "fresh_record_parity": bool(expected), "final_cursor": previous["cursor"],
        "wall_seconds": time.monotonic() - started,
        "max_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


if __name__ == "__main__":
    library_path, trace_path, stimulus_file, output_path = map(Path, sys.argv[1:5])
    result = replay(library_path, trace_path, stimulus_file, output_path,
                    Path(sys.argv[5]) if len(sys.argv) == 6 else None)
    with (output_path / "report.json").open("x") as target:
        target.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print("INTERLEAVED_REPLAY_PASS " + json.dumps({key: result[key] for key in (
        "work_count", "final_cursor", "idle_gap_cycles", "fresh_record_parity")}), flush=True)
