# How to run: PYTHONPATH=IM2P.sim python3 -B -m sim.cycle.evaluation_stateful_replay LIBRARY TRACE OUT BUDGET
#   [--limit N] [--reference CERTIFIED_WORKS_JSONL]
"""FAST_EVALUATION stateful NPU replay: the certified decode stepping without event materialization or hashing.

Works stay strictly sequential in one native session (stateful dependencies forbid work parallelism). Relative
to sim/tests/cycle/decode_trace_replay.py in boundary mode the only differences are record_events=False and
no event drain/hash; per-work windows and before/after state snapshots keep the certified record shape, so
parity with a certification reference is checked record by record (compare). Full event validation stays in
the certification/audit harness.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from sim.cycle.certificate_contract import object_value
from sim.cycle.npu_trace_schema import Record
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_binding import Code, Report, SequenceSession, Settings
from sim.cycle.sequence_trace_cli import descriptor, load_trace
from sim.cycle.stateful_sequence_evidence import require
from sim.tests.cycle.actual_trace_replay import MAX_WORK_CYCLES, snapshot
from sim.tests.cycle.decode_trace_replay import BUFFER_EVENTS, parity_line

OK, INCOMPLETE = int(Code.OK), int(Code.INCOMPLETE)


def replay(library: Path, trace: Path, output: Path, budget: int, limit: int | None = None) -> Record:
    require(0 < budget < 1 << 63 and (limit is None or limit > 0), "evaluation stateful replay", "budget or limit invalid")
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    profile, works, trace_digest = load_trace(trace)
    works = works[:limit] if limit is not None else works
    records_path = output / "works.jsonl"
    settings = Settings(max_work_cycles=MAX_WORK_CYCLES, max_session_cycles=budget, max_trace_events=BUFFER_EVENTS)
    with SequenceSession(library, profile, settings=settings) as session, records_path.open("x") as stream:
        lib, handle = session._native()
        original = handle.value
        cursor = session.status().cursor
        previous = snapshot(session)
        for ordinal, work in enumerate(works):
            before = snapshot(session)
            require(before == previous and before["cursor"] == cursor, "state carry", f"state changed before work{ordinal}")
            require(session.offer(descriptor(work), cursor) == Code.OK, "offer", f"work{ordinal} rejected")
            offered = cursor
            while True:
                code = lib.im2p_cycle_sequence_advance_to_boundary(handle, budget, 1 << 16)
                require(code in (OK, INCOMPLETE), "advance", f"work{ordinal}: code {code}")
                if code == OK:
                    break
                require(session.status().cursor < budget, "advance", f"work{ordinal}: session budget exhausted")
            report = session.pop_report()
            if not isinstance(report, Report):
                raise TypeError(f"report: work{ordinal} missing")
            after = snapshot(session)
            cursor = session.status().cursor
            require(report.resource_ready_cycle == cursor == after["cursor"] and report.generation == 1 and
                    handle.value == original and after["generation"] == 1, "native lifetime",
                    f"reset, replacement or overshoot at work{ordinal}")
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
            stream.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")
            previous = after
    return {"schema": "im2p-evaluation-stateful-replay-v1", "status": "PASS", "mode": "FAST_EVALUATION_NO_EVENTS",
            "profile": profile, "work_count": len(works), "trace": {"path": str(trace), "sha256": trace_digest},
            "library": {"path": str(library), "sha256": sha256(library)},
            "records": {"path": str(records_path), "sha256": sha256(records_path)},
            "session_budget_cycles": budget, "final_cursor": cursor, "wall_seconds": time.monotonic() - started}


def _differences(left: object, right: object, path: str = "") -> list[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        return [item for key in sorted(set(left) | set(right))
                for item in _differences(left.get(key), right.get(key), f"{path}.{key}")]
    return [] if left == right else [path]


def compare(evaluation: Path, reference: Path) -> Record:
    """Record-by-record parity with a certification replay; the reference's event block is the only omission."""
    differing: dict[str, int] = {}
    first: Record | None = None
    count = 0
    with evaluation.open() as mine, reference.open() as theirs:
        for line, certified in zip(mine, theirs):
            expected = object_value(json.loads(certified), "reference record")
            expected.pop("events")
            left = json.loads(parity_line(line))
            right = json.loads(parity_line(json.dumps(expected)))
            for field in _differences(left, right):
                differing[field] = differing.get(field, 0) + 1
                if first is None:
                    first = {"ordinal": count, "field": field}
            count += 1
    fields: Record = {key: value for key, value in differing.items()}
    return {"compared_works": count, "identical": not differing, "differing_fields": fields, "first_difference": first,
            "masked": ["status.stop_reason (why the last advance returned; stepping dependent)",
                       "reference events block (not materialized in evaluation mode)"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("library", type=Path)
    parser.add_argument("trace", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("budget", type=int)
    parser.add_argument("--limit", type=int, help="bounded prefix of works")
    parser.add_argument("--reference", type=Path, help="certification works.jsonl for record-by-record parity")
    args = parser.parse_args()
    try:
        result = replay(args.library, args.trace, args.output, args.budget, args.limit)
        if args.reference is not None:
            result["parity"] = compare(args.output / "works.jsonl", args.reference)
        with (args.output / "report.json").open("x") as target:
            target.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps({key: result[key] for key in ("work_count", "final_cursor", "wall_seconds")} |
                         {"parity": result.get("parity")}, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError) as error:
        print(f"evaluation stateful replay failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
