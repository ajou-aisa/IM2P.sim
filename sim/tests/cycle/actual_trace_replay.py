# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# How to run: PYTHONPATH=IM2P.sim python3 -B -m sim.tests.cycle.actual_trace_replay LIBRARY TRACE OUT [REFERENCE_REPORT]
"""Replay a complete actual trace in one native session, recording every before/after state."""
from __future__ import annotations

import json
import resource
import sys
import time
from pathlib import Path
from typing import Final

from sim.cycle.cli import RESULT_FIELDS
from sim.cycle.npu_trace_schema import Record
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_binding import Code, Report, SequenceSession, Settings
from sim.cycle.sequence_trace_cli import descriptor, load_trace
from sim.cycle.stateful_sequence_evidence import require

MAX_WORK_CYCLES: Final = 10_000_000
MAX_SESSION_CYCLES: Final = 1_000_000_000


def snapshot(session: SequenceSession) -> Record:
    status, domain, rows = session.status(), session.domain_snapshot(), session.row_pressure()
    counters = session.counters()
    return {
        "status": bytes(status).hex(), "domain": bytes(domain).hex(), "rows": bytes(rows).hex(),
        "mesh": bytes(session.mesh_state()).hex(), "tags": bytes(session.tag_state()).hex(),
        "generation": status.generation, "cursor": status.cursor, "ready": domain.resource_ready,
        "ready_mask": domain.ready_violation_mask, "tag_count": domain.tag_count,
        "tag_peak": domain.max_tag_occupancy, "row_count": rows.row_count, "row_peak": rows.max_row_occupancy,
        "scratchpad_half": status.next_scratchpad_half, "accumulator_half": status.next_accumulator_half,
        "counters": {key: getattr(counters, key) for key in RESULT_FIELDS},
    }


def replay(library: Path, trace: Path, output: Path, reference: Path | None) -> Record:
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    profile, works, trace_digest = load_trace(trace)
    expected: list[str] = []
    if reference is not None:
        prior = json.loads(reference.read_text())
        records_ref = prior["records"]
        require(prior["status"] == "PASS" and prior["trace"]["sha256"] == trace_digest and
                sha256(Path(records_ref["path"])) == records_ref["sha256"], "fresh replay", "reference differs")
        expected = Path(records_ref["path"]).read_text().splitlines()
    records_path = output / "works.jsonl"
    settings = Settings(max_work_cycles=MAX_WORK_CYCLES, max_session_cycles=MAX_SESSION_CYCLES)
    with SequenceSession(library, profile, settings=settings) as session, records_path.open("x") as stream:
        handle = session._handle.value
        identity = session.identity
        require(identity is not None, "native identity", "missing")
        previous = snapshot(session)
        for ordinal, work in enumerate(works):
            before = snapshot(session)
            require(before == previous, "state carry", f"state changed before work{ordinal}")
            offered = session.status().cursor
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
                print(f"ACTUAL_REPLAY_PROGRESS works={ordinal + 1} cursor={after['cursor']}", flush=True)
    require(not expected or len(expected) == len(works), "fresh replay", "work count differs")
    return {
        "schema": "im2p-actual-trace-native-replay-v1", "status": "PASS", "profile": profile,
        "work_count": len(works), "work_ids": [work.identity for work in works],
        "trace": {"path": str(trace), "sha256": trace_digest},
        "library": {"path": str(library), "sha256": sha256(library)},
        "records": {"path": str(records_path), "sha256": sha256(records_path)},
        "harness": {"path": str(Path(__file__)), "sha256": sha256(Path(__file__))},
        "same_handle": True, "reset_count": 1, "midrun_resets": 0, "fallbacks": 0,
        "offer_policy": "back-to-back-npu-only-independent-readiness",
        "fresh_reference": None if reference is None else {"path": str(reference), "sha256": sha256(reference)},
        "fresh_record_parity": bool(expected), "final_cursor": previous["cursor"],
        "wall_seconds": time.monotonic() - started,
        "max_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


if __name__ == "__main__":
    library_path, trace_path, output_path = map(Path, sys.argv[1:4])
    result = replay(library_path, trace_path, output_path, Path(sys.argv[4]) if len(sys.argv) == 5 else None)
    with (output_path / "report.json").open("x") as target:
        target.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print("ACTUAL_REPLAY_PASS " + json.dumps({key: result[key] for key in (
        "work_count", "final_cursor", "fresh_record_parity")}), flush=True)
