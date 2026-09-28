"""Production certificate for one actual-inference trace from independent RTL/model and native evidence.

A trace is certified only through reviewed pins: RTL and the native model agree exactly on every
probe-admissible work, the complete trace replays deterministically in one native session, per-edge
and boundary stepping agree, and the complete trace never leaves the tag/row range RTL exercised.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Final, assert_never

from sim.cycle.actual_trace_pins import ACTUAL_TRACE_PINS, EVIDENCE_FILES
from sim.cycle.certificate_contract import (
    TIMING,
    array_value,
    object_value,
    read_document,
)
from sim.cycle.cycle_trace_certificate import summarize
from sim.cycle.npu_trace_schema import Record, integer
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_binding import (
    DomainSnapshot,
    MeshState,
    RowPressure,
    Status,
    TagState,
    source_identity,
)
from sim.cycle.stateful_domain import ACTUAL_TRACE_REVISIONS, InitialState, state_domain
from sim.cycle.stateful_profile_certificate import PARENT_SHA256
from sim.cycle.stateful_sequence_certificate import ScopedEvidence
from sim.cycle.stateful_sequence_evidence import (
    ROOT,
    EvidenceContext,
    StatefulCertificateError,
    reference,
    require,
)
from sim.cycle.stateful_sequence_tag6_pins import ARTIFACTS, NATIVE_SOURCES

SCHEMA: Final = "im2p-actual-trace-certificate-v1"
HARNESS: Final = {
    "stimulus": "sim/tests/cycle/actual_trace_stimulus.py", "capture": "sim/tests/cycle/actual_trace_capture.py",
    "comparison": "sim/tests/cycle/actual_trace_compare.py", "replay": "sim/tests/cycle/actual_trace_replay.py",
    "milestone": "sim/tests/cycle/actual_trace_milestone.py",
}
CONSUMER_SOURCES: Final = (
    "sim/cycle/actual_trace_certificate.py", "sim/cycle/actual_trace_pins.py", "sim/cycle/cycle_trace_certificate.py",
    "sim/cycle/stateful_sequence_certificate.py", "sim/cycle/execution_sequence_admission.py",
    "sim/cycle/execution_sequence_provider.py", "sim/cycle/stateful_domain.py",
    "sim/cycle/stateful_domain_admission.py", "sim/cycle/sequence_domain.py", "sim/cycle/sequence_binding.py",
    "sim/cycle/sequence_binding_abi.py", "sim/cycle/npu_trace.py", "sim/cycle/npu_trace_integrity.py",
    "sim/cycle/npu_trace_schema.py", "sim/tests/cycle/compositional_sequence_bounded.py",
    "sim/tests/cycle/compositional_sequence_v2_payload.py", "sim/tests/cycle/compositional_sequence_v2_base.py",
)


def _snapshot(row: Record, completed: int, tag_limit: int, row_exclusive: int) -> None:
    status = Status.from_buffer_copy(bytes.fromhex(str(row["status"])))
    domain = DomainSnapshot.from_buffer_copy(bytes.fromhex(str(row["domain"])))
    mesh = MeshState.from_buffer_copy(bytes.fromhex(str(row["mesh"])))
    pressure = RowPressure.from_buffer_copy(bytes.fromhex(str(row["rows"])))
    tags = TagState.from_buffer_copy(bytes.fromhex(str(row["tags"])))
    counters = object_value(row["counters"], "counters")
    require(status.generation == domain.generation == mesh.generation == pressure.generation ==
            tags.generation == row["generation"] == 1 and
            status.cursor == domain.cursor == mesh.cursor == pressure.cursor == tags.cursor == row["cursor"] and
            status.has_active == status.has_pending == status.has_report == status.faulted == 0 and
            domain.resource_ready == row["ready"] == 1 and domain.ready_violation_mask == row["ready_mask"] == 0,
            "actual state", f"non-ready or mismatched epoch after {completed} works")
    require(domain.tag_count == tags.queue_len == integer(row, "tag_count") <= tag_limit and
            tags.enqueues - tags.dequeues == domain.tag_count and
            domain.max_tag_occupancy == integer(row, "tag_peak") <= tag_limit and
            domain.row_count == pressure.row_count == row["row_count"] == 0 and
            pressure.max_row_occupancy == integer(row, "row_peak") < row_exclusive and
            all(tag.rob_valid == 0 for tag in domain.tags[:domain.tag_count]),
            "actual state", "tag/row occupancy outside the certified range")
    require(status.next_scratchpad_half == mesh.next_scratchpad_half == row["scratchpad_half"] in (0, 1) and
            status.next_accumulator_half == mesh.next_accumulator_half == row["accumulator_half"] in (0, 1) and
            mesh.request_valid == mesh.control_valid == mesh.mesh_request_valid == mesh.mesh_request_fire == 0 and
            mesh.mesh_request_ready == 1 and counters["logical_work_count"] == completed,
            "actual state", "half carry, mesh drain or completed count differs")
    for kind in ("load", "store", "scale"):
        require(counters[f"{kind}_request_count"] == counters[f"{kind}_response_count"],
                "actual state", f"undrained {kind}")


def inspect(path: Path, work_ids: list[int], tag_limit: int, row_exclusive: int) -> Record:
    previous: Record | None = None
    tag_peak = row_peak = 0
    with path.open() as stream:
        for ordinal, line in enumerate(stream):
            row = object_value(json.loads(line), "work transition")
            require(ordinal < len(work_ids) and row["ordinal"] == ordinal and row["work_id"] == work_ids[ordinal] and
                    row["admitted"] is True and row["state_transition_valid"] is True,
                    "actual state", "work order or admission differs")
            before, after = object_value(row["before"], "before"), object_value(row["after"], "after")
            window = object_value(row["window"], "window")
            _snapshot(before, ordinal, tag_limit, row_exclusive)
            _snapshot(after, ordinal + 1, tag_limit, row_exclusive)
            require(previous is None or before == previous, "state carry", f"gap before work{ordinal}")
            require(ordinal > 0 or before["cursor"] == before["tag_count"] == before["scratchpad_half"] ==
                    before["accumulator_half"] == 0, "state carry", "cold origin differs")
            offered, accepted, result, scale, ready = (integer(window, key) for key in (
                "offered_cycle", "accepted_cycle", "result_ready_cycle", "final_scale_release_cycle",
                "resource_ready_cycle"))
            require(offered == before["cursor"] == accepted and accepted < result <= scale <= ready == after["cursor"]
                    and window["next_scratchpad_half"] == after["scratchpad_half"] and
                    window["next_accumulator_half"] == after["accumulator_half"],
                    "actual state", f"timing or half mismatch at work{ordinal}")
            old, new = (object_value(item["counters"], "counters") for item in (before, after))
            require(integer(new, "total_cycles") - integer(old, "total_cycles") == result - accepted and
                    new["done_cycle"] == result, "actual state", f"counter/report mismatch at work{ordinal}")
            tag_peak, row_peak = max(tag_peak, integer(after, "tag_peak")), max(row_peak, integer(after, "row_peak"))
            previous = after
    if previous is None or previous["counters"] is None:
        raise StatefulCertificateError("actual state", "empty transition stream")
    require(integer(object_value(previous["counters"], "counters"), "logical_work_count") == len(work_ids),
            "actual state", "complete trace required")
    return {"records": {"path": str(path), "sha256": sha256(path)}, "work_count": len(work_ids),
            "final_cursor": previous["cursor"], "tag_peak": tag_peak, "row_peak": row_peak, "ready_mask": 0,
            "counters": previous["counters"], "same_generation": True, "continuous_state_carry": True}


def expected(context: EvidenceContext, case_id: str) -> Record:
    require(case_id in ACTUAL_TRACE_PINS, "actual trace", "no reviewed actual-trace evidence pins")
    pin = ACTUAL_TRACE_PINS[case_id]
    require(context.tag6_evidence_input is context.current_evidence_input is context.domain_delta_input is None,
            "actual trace context", "historical selectors cannot grant actual-trace authority")
    require(ACTUAL_TRACE_REVISIONS.get(pin.revision, ("", None))[0] == pin.profile and
            set(pin.evidence_sha256) == set(EVIDENCE_FILES), "actual trace pins", "revision or evidence set differs")
    domain = state_domain(pin.profile, pin.revision)
    require((domain.allowed_tag_peak, domain.allowed_row_peak) == (pin.tag_peak, pin.row_peak),
            "actual trace domain", "domain limits differ from the RTL-observed range")
    library = reference(context.library, ARTIFACTS["library"])
    shared = reference(context.shared_library, ARTIFACTS["shared_library"])
    parents: Record = {name: reference(path, PARENT_SHA256[name]) for name, path in (
        ("base", context.base_parent), ("run_aware", context.run_aware_parent), ("service", context.service_parent))}
    identity = source_identity(context.shared_library, pin.profile)
    for name, digest in identity.source_sha256:
        wanted = pin.memory_sha256 if name.endswith(f"/{pin.profile}.json") else NATIVE_SOURCES.get(name)
        require(digest == wanted, "actual native source", name)
    evidence: Record = {key: reference(context.evidence_root / relative, pin.evidence_sha256[key])
                        for key, relative in EVIDENCE_FILES.items()}
    docs = {key: read_document(Path(str(object_value(evidence[key], key)["path"])))
            for key in EVIDENCE_FILES if key != "stimulus_text"}
    stimulus, capture, comparison = docs["stimulus"], docs["capture"], docs["comparison"]
    trace = reference(Path(str(object_value(stimulus["trace"], "trace")["path"])), pin.trace_sha256)
    all_ids, rtl_ids = list(range(pin.work_count)), list(range(pin.rtl_work_count))
    require(stimulus["profile"] == pin.profile and stimulus["selected_work_ids"] == rtl_ids and
            stimulus["native_only_work_ids"] == all_ids[pin.rtl_work_count:] and
            stimulus["trace_work_count"] == pin.work_count, "actual stimulus", "profile or work partition differs")
    command = array_value(capture["command"], "probe command")
    require(capture["status"] == "PASS_CAPTURE" and capture["child_returncode"] == 0 and
            capture["child_reaped"] is True and
            command[1] == object_value(evidence["stimulus_text"], "stimulus text")["path"],
            "actual RTL capture", "complete independent capture of the sealed stimulus required")
    probe = reference(Path(str(command[0])), pin.probe_sha256)
    streams = object_value(capture["artifacts"], "capture artifacts")
    raw = {side: object_value(object_value(streams[side], side)["raw"], side) for side in ("rtl", "model")}
    for side, item in raw.items():
        _ = reference(Path(str(item["path"])), str(item["sha256"]))
        require(item["lines"] != 0, "actual RTL capture", f"{side} stream empty")
    pairs = object_value(comparison["selected_events_and_queue"], "comparison streams")
    rtl, model = object_value(pairs["rtl"], "rtl"), object_value(pairs["model"], "model")
    require(comparison["status"] == "EXACT" and comparison["work_ids"] == rtl_ids and
            comparison["tag_peak"] == pin.tag_peak and comparison["row_peak"] == pin.row_peak and
            comparison["stimulus"] == evidence["stimulus"] and comparison["capture"] == evidence["capture"] and
            comparison["trace"] == trace and rtl["sha256"] == raw["rtl"]["restored_sha256"] and
            model["sha256"] == raw["model"]["restored_sha256"] and
            integer(rtl, "events") == integer(model, "events") > 0 and
            integer(rtl, "queue_edges") == integer(model, "queue_edges") > 0 and
            rtl["works"] == model["works"] == pin.rtl_work_count,
            "actual RTL comparison", "exact RTL/model selected events, queues and endpoints required")
    first, fresh = docs["replay_first"], docs["replay_fresh"]
    for report in (first, fresh):
        require(report["status"] == "PASS" and report["work_ids"] == all_ids and report["trace"] == trace and
                object_value(report["library"], "library")["sha256"] == shared["sha256"] and
                report["same_handle"] is True and report["midrun_resets"] == report["fallbacks"] == 0 and
                report["final_cursor"] == pin.final_cursor and
                report["offer_policy"] == "back-to-back-npu-only-independent-readiness",
                "actual native replay", "complete one-session back-to-back replay required")
    first_records = object_value(first["records"], "records")
    require(fresh["fresh_record_parity"] is True and fresh["fresh_reference"] == evidence["replay_first"] and
            object_value(fresh["records"], "records")["sha256"] == first_records["sha256"],
            "actual native replay", "independent fresh replay parity required")
    records = reference(Path(str(first_records["path"])), str(first_records["sha256"]))
    transition = inspect(Path(str(records["path"])), all_ids, domain.allowed_tag_peak, domain.allowed_row_peak + 1)
    require((transition["final_cursor"], transition["tag_peak"], transition["row_peak"]) ==
            (pin.final_cursor, pin.tag_peak, pin.row_peak),
            "actual domain closure", "complete trace leaves the RTL-observed state range")
    milestone = docs["milestone"]
    edge = object_value(milestone["cycle_stepping"], "edge")
    boundary = object_value(milestone["advance_to_boundary"], "boundary")
    require(milestone["status"] == "EXACT" and milestone["work_ids"] == all_ids and milestone["trace"] == trace and
            object_value(milestone["library"], "library")["sha256"] == shared["sha256"] and
            {key: value for key, value in edge.items() if key != "calls"} ==
            {key: value for key, value in boundary.items() if key != "calls"} and
            edge["final_cycle"] == pin.final_cursor and integer(edge, "event_count") > 0,
            "actual milestone parity", "edge and boundary stepping must agree on the complete trace")
    for key, harness in (("capture", "capture"), ("comparison", "comparison"), ("replay_first", "replay"),
                         ("replay_fresh", "replay"), ("milestone", "milestone")):
        require(object_value(docs[key]["harness"], "harness")["sha256"] == sha256(ROOT / HARNESS[harness]),
                "actual harness", f"{key} was produced by different harness bytes")
    summary = summarize(Path(str(trace["path"])))
    require(summary.profile == pin.profile and list(summary.work_ids) == all_ids,
            "actual trace", "trace profile or work order differs")
    sources: Record = dict(identity.source_sha256)
    sources.update({name: sha256(ROOT / name) for name in (*CONSUMER_SOURCES, *HARNESS.values())})
    return {
        "schema": SCHEMA, "version": 1, "artifact_role": "MODEL_STATE_VALIDATION",
        "validation_scope": "SCOPED_EVIDENCE", "production_admitted": False, "case_id": case_id,
        "profile": pin.profile, "precision": domain.precision, "dim": domain.dim,
        "state_domain_revision": pin.revision, "state_domain": asdict(domain), "initial_state": asdict(InitialState()),
        "trace": trace, "trace_work_count": pin.work_count, "work_ids": [item for item in all_ids],
        "rtl_work_ids": [item for item in rtl_ids],
        "native_only_work_ids": [item for item in all_ids[pin.rtl_work_count:]],
        "geometry": summary.geometry, "tile_shape": summary.tile_shape, "ordered_runs": summary.ordered_runs,
        "row_mapping": summary.row_mapping, "work_binding_sha256": summary.work_binding_sha256,
        "library": library, "shared_library": shared, "parents": parents, "source_sha256": sources,
        "evidence": evidence, "probe": probe,
        "rtl_comparison": {"selected_event_pairs": rtl["events"], "queue_transition_pairs": rtl["queue_edges"],
                           "works": rtl["works"], "first_peak": comparison["first_peak"]},
        "transition": transition,
        "milestone": {"final_cycle": edge["final_cycle"], "event_count": edge["event_count"],
                      "events_sha256": edge["events"]},
        "observed_tag_peak": pin.tag_peak, "observed_row_peak": pin.row_peak, "final_cursor": pin.final_cursor,
        "reference_memory": {"timing": dict(TIMING), "initial_reset_count": 1,
                             "initial_scratchpad_half": 0, "initial_accumulator_half": 0},
        "offer_policy": "back-to-back-npu-only-independent-readiness",
        "scope": "finite actual-inference trace; RTL-exact probe-admissible prefix; complete native replay "
                 "inside the RTL-observed state range",
        "formal_proof": False, "synthetic_corpus": False, "execution_scope": "NPU_ONLY_PROJECTION",
        "production_eligibility": "CERTIFIED_ACTUAL_TRACE_ONLY",
        "E2E_RECONSTRUCTION_READY": "NOT_READY", "PAPER_CAMPAIGN_COMPLETE": "NOT_RUN",
    }


def validate_document(path: Path, context: EvidenceContext) -> Record:
    document = read_document(path)
    require(document.get("schema") == SCHEMA, "actual trace schema", "actual trace certificate required")
    require(json.dumps(document, sort_keys=True) ==
            json.dumps(expected(context, str(document.get("case_id"))), sort_keys=True),
            "actual trace certificate", "trace, evidence, domain, source or library binding differs")
    return document


def validate(path: Path, context: EvidenceContext) -> ScopedEvidence:
    document = validate_document(path, context)
    return ScopedEvidence(sha256(path), sha256(context.library), (str(document["case_id"]),),
                          state_domain_revision=str(document["state_domain_revision"]))


def build(output: Path, context: EvidenceContext, case_id: str) -> Path:
    from sim.cycle.stateful_sequence_replay_certificate import _publish

    _publish(output, expected(context, case_id))
    validate_document(output, context)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Actual-inference trace production certificate")
    for name in ("evidence_root", "library", "shared_library", "base_parent", "run_aware_parent", "service_parent"):
        parser.add_argument(f"--{name.replace('_', '-')}", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("build")
    make.add_argument("--case", required=True)
    make.add_argument("output", type=Path)
    commands.add_parser("validate").add_argument("path", type=Path)
    args = parser.parse_args()
    context = EvidenceContext(args.evidence_root, args.library, args.shared_library, args.base_parent,
                              args.run_aware_parent, args.service_parent)
    try:
        match args.command:
            case "build":
                path = build(args.output, context, args.case)
                print(json.dumps({"certificate": str(path), "sha256": sha256(path), "status": "VERIFIED"}))
            case "validate":
                result = validate(args.path, context)
                print(json.dumps({"certificate": str(args.path), "status": "VERIFIED",
                                  "state_domain_revision": result.state_domain_revision}))
            case unreachable:
                assert_never(unreachable)
    except (StatefulCertificateError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "REJECTED", "error": str(error)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
