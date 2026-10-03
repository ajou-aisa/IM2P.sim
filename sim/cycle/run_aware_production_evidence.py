from __future__ import annotations

import csv
import json
from collections import Counter
from hashlib import sha256
from pathlib import Path

from sim.tests.cycle.production_run_work import (
    Case,
    CertificateCase,
    CycleModel,
    EventComparison,
    EventDifference,
    WorkFixture,
)
from sim.tests.cycle.rtl_hardening import SELECTED_EVENTS, normalized_model_events

TIMING_FIELDS = ("backing_read_delay", "even_read_id_delay", "scale_read_extra_delay",
                 "backing_write_delay", "read_ready_period", "backing_cycle_offset")


def compare_events(model: list[tuple[int, str]], rtl: list[tuple[int, str]]) -> EventComparison:
    left, right = sorted(model), sorted(rtl)
    first: EventDifference | None = None
    for index in range(max(len(left), len(right))):
        model_event = left[index] if index < len(left) else None
        rtl_event = right[index] if index < len(right) else None
        if model_event != rtl_event:
            first = {"index": index, "model": model_event, "rtl": rtl_event}
            break
    return {"exact": bool(left and right) and first is None,
            "first_difference": first, "model_count": len(left), "rtl_count": len(right),
            "model_sha256": sha256(json.dumps(left, separators=(',', ':')).encode()).hexdigest(),
            "rtl_sha256": sha256(json.dumps(right, separators=(',', ':')).encode()).hexdigest()}


def parse_rtl_log(path: Path, case: Case, work: WorkFixture) -> dict[str, int]:
    summaries = [line for line in path.read_text().splitlines() if line.startswith("PRODUCTION_RUN ")]
    if len(summaries) != 1:
        raise ValueError("expected one production RTL summary")
    fields = dict(part.split("=", 1) for part in summaries[0].split()[1:])
    if (fields.get("case") != "production_" + case["case"] or
            fields.get("profile") != case["profile"] or
            any(fields.get(key) != "1" for key in ("attempted", "admitted", "exact", "logical_done")) or
            [int(fields[key]) for key in ("m", "n", "k")] != work["shape"] or
            int(fields["original_k"]) != work["original_k"] or
            int(fields["runs"]) != len(work["runs"]) or
            int(fields["first_block"]) != work["runs"][0][0] or
            int(fields["last_block"]) != work["runs"][-1][0] or
            int(fields["expected_values"]) != work["shape"][0] * work["shape"][1] or
            fields["expected_values"] != fields["actual_values"]):
        raise ValueError("RTL production case rejected or geometry changed")
    return {name: int(fields[name]) for name in (
        "start", "done", "cycles", "loops", "loads", "executes", "stores",
        "commits", "scale_reads", "scale_responses", "completions")}


def parse_rtl_events(path: Path, case: Case, work: WorkFixture) -> list[tuple[int, str]]:
    rows = list(csv.reader(path.read_text().splitlines()))
    metadata = [row for row in rows if row and row[0] == "CASE"]
    if len(metadata) != 1 or len(metadata[0]) != 16:
        raise ValueError("one production event CASE required")
    first = metadata[0]
    profile = case["profile"]
    if (first[2] != "production_" + case["case"] or
            [int(value) for value in first[3:10]] !=
            [*work["shape"], work["original_k"], *work["tile"]] or
            [int(value) for value in first[10:16]] != [case["timing"][key] for key in TIMING_FIELDS]):
        raise ValueError(f"RTL event metadata changed: {profile}/{case['case']}")
    index = first[1]
    selected: list[tuple[int, str]] = []
    loops: list[list[str]] = []
    for row in rows:
        if row[0] == "CASE":
            continue
        if row[0] == "LOOP":
            if len(row) != 13 or row[1] != index:
                raise ValueError("malformed RTL LOOP metadata")
            loops.append(row)
        elif len(row) == 6 and row[0] == index and row[2] in SELECTED_EVENTS | {"scale_release"}:
            if row[2] in SELECTED_EVENTS:
                selected.append((int(row[1]), row[2]))
        else:
            raise ValueError("unexpected production RTL event")
    work_cycles = [cycle for cycle, kind in selected if kind == "work"]
    owners = {(run[0], run[2], run[3]) for run in work["runs"]}
    observed_owners = {(int(row[10]), int(row[11]), int(row[12])) for row in loops}
    if not work_cycles or len(work_cycles) != len(loops) or observed_owners != owners:
        raise ValueError("RTL loop/run ownership incomplete")
    return [(cycle - min(work_cycles) + 1, kind) for cycle, kind in selected]


def compare_case(case: Case, work: WorkFixture, rtl: dict[str, int],
                 observed_events: list[tuple[int, str]], model: CycleModel) -> CertificateCase:
    result = model["result"]
    kinds = Counter(kind for _, kind in normalized_model_events(model["events"]))
    if rtl["start"] < 1 or rtl["done"] - rtl["start"] != rtl["cycles"]:
        raise ValueError("RTL endpoint convention changed")
    observed = {**rtl, "start": 1, "done": rtl["done"] - rtl["start"] + 1}
    expected = {"start": result["start_cycle"], "done": result["done_cycle"],
                "cycles": result["total_cycles"], "loops": result["loop_count"],
                "loads": kinds["load_issue"], "executes": kinds["execute_issue"],
                "stores": kinds["store_issue"], "commits": kinds["accumulator_commit"],
                "scale_reads": result["scale_request_count"],
                "scale_responses": result["scale_response_count"],
                "completions": (((work["shape"][0] + work["descriptor"][5] - 1) //
                                 work["descriptor"][5]) *
                                ((work["shape"][1] + work["descriptor"][5] - 1) //
                                 work["descriptor"][5]))}
    differences = {key: {"rtl": observed[key], "model": value} for key, value in expected.items()
                   if observed[key] != value}
    if result["planner_loop_count"] != rtl["loops"]:
        differences["planner_loops"] = {"rtl": rtl["loops"], "model": result["planner_loop_count"]}
    if result["logical_work_count"] != 1:
        differences["logical_work"] = {"rtl": 1, "model": result["logical_work_count"]}
    events = compare_events(normalized_model_events(model["events"]), observed_events)
    exact = not differences and events["exact"]
    return {"profile": case["profile"], "case": case["case"],
            "shape": work["shape"], "tile": work["tile"],
            "original_k": work["original_k"], "runs": work["runs"],
            "row_map": work["row_map"], "timing": case["timing"],
            "framing": case["framing"], "status": "PASS" if exact else "FAIL",
            "rtl_admitted": True, "model_admitted": True,
            "rtl_summary": observed, "model_summary": expected,
            "selected_event_multiset": events["exact"], "selected_event_comparison": events,
            "differences": differences, "delta_cycles": result["total_cycles"] - rtl["cycles"]}
