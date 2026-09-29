"""Decode-trace cycle certificate: one frozen 256+128 decode workload on the RTL-calibrated native simulator.

Separate from the NPU-only actual-trace, cycle-trace and interleaved certificates (own schema, revision, pins
and harness; the stateful provider does not admit it). No new RTL runs: the cycle library is the one existing
RTL evidence calibrates, namely the actual-trace certificate (prefill), the milestone-1 RTL/model comparison
(prefill plus the first decode step, lm_head included) and the OLD->CURRENT transition. The complete trace is
replayed natively with boundary and per-edge stepping, which must agree exactly; per-work state transitions and
event order/ownership are checked; milestone-1 RTL windows must equal the replay; and the whole trace must stay
inside the RTL-observed state range and certified work classes. A disagreement is a simulator mismatch and a
range or class excursion is a boundary issue: either rejects, and only then is new RTL evidence needed. Works
beyond milestone 1 are simulator-certified, not RTL-exact, and the certificate records exactly which is which.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, assert_never

from scripts.gemmini_replay_contract import hardware_contract
from scripts.gemmini_resolve_profile import BuildFailure, JsonValue
from scripts.gemmini_rtl_build_binding import verify_build
from sim.cycle import actual_trace_certificate
from sim.cycle.actual_trace_certificate import inspect
from sim.cycle.certificate_contract import array_value, object_value, read_document
from sim.cycle.decode_trace_pins import DECODE_PINS, EVIDENCE_FILES, GENERATION
from sim.cycle.library_transition import TransitionError, validate_transition
from sim.cycle.npu_trace import work_binding
from sim.cycle.npu_trace_schema import Record, integer
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_binding import source_identity
from sim.cycle.sequence_trace_cli import load_trace
from sim.cycle.stateful_domain import StateDomainError, WorkClass, state_domain
from sim.cycle.stateful_sequence_evidence import ROOT, EvidenceContext, StatefulCertificateError, reference, require
from sim.cycle.stateful_sequence_tag6_pins import ARTIFACTS, NATIVE_SOURCES
from sim.tests.cycle.compositional_sequence_v2_base import SOURCE
from sim.tests.cycle.decode_trace_replay import OFFER_POLICY, parity_line
from sim.tests.cycle.decode_trace_replay import SCHEMA as REPLAY_SCHEMA

SCHEMA: Final = "im2p-decode-trace-certificate-v1"
REVISION: Final = "GUARDED_A8D32_DECODE_TRACE_SIMULATOR_V1"
BASIS: Final = "RTL_CALIBRATED_NATIVE_SIMULATOR"
CLOCK: Final = {"domain": "NPU_CYCLES", "operating_frequency_hz": None}
CAPTURE_FLAGS: Final = ("--availability-driven", "--tag-observer-v2", "--boundary-schema=2", "--queue-edge-schema=2")
WINDOW: Final = (("offered", "offered_cycle"), ("accepted", "accepted_cycle"), ("result_ready", "result_ready_cycle"),
                 ("final_scale_release", "final_scale_release_cycle"), ("resource_ready", "resource_ready_cycle"),
                 ("next_scratchpad_half", "next_scratchpad_half"), ("next_accumulator_half", "next_accumulator_half"))
HARNESS: Final = {"replay": "sim/tests/cycle/decode_trace_replay.py", "capture": "sim/tests/cycle/actual_trace_capture.py",
                  "comparison": "sim/tests/cycle/actual_trace_compare.py"}
CONSUMER_SOURCES: Final = (
    "sim/cycle/decode_trace_certificate.py", "sim/cycle/decode_trace_pins.py", "sim/cycle/actual_trace_certificate.py",
    "sim/cycle/actual_trace_pins.py", "sim/cycle/library_transition.py", "sim/cycle/stateful_domain.py",
    "sim/cycle/sequence_binding.py", "sim/cycle/sequence_binding_abi.py", "sim/cycle/sequence_trace_cli.py",
    "sim/cycle/npu_trace.py", "sim/cycle/npu_trace_integrity.py", "sim/cycle/npu_trace_schema.py",
    "sim/tests/cycle/actual_trace_replay.py", "sim/tests/cycle/compositional_sequence_bounded.py",
    "sim/tests/cycle/compositional_sequence_v2_payload.py", "sim/tests/cycle/compositional_sequence_v2_base.py",
    "sim/tests/cycle/compositional_sequence_probe.cpp", "sim/tests/cycle/production_sequence_probe.cpp",
)


@dataclass(frozen=True, slots=True)
class DecodeContext:
    evidence_root: Path
    library: Path
    shared_library: Path
    model: Path
    dataset: Path
    rtl_build: Path


def digest(value: JsonValue) -> str:
    """sha256 of canonical_json(value), for any JSON value."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                                     allow_nan=False).encode()).hexdigest()


def sha_of(record: Record, key: str) -> str:
    return str(object_value(record[key], key)["sha256"])


def parity_digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open() as stream:
        for line in stream:
            value.update(parity_line(line))
    return value.hexdigest()


def anchored(record: Record, key: str) -> Record:
    item = object_value(record[key], key)
    return reference(Path(str(item["path"])), str(item["sha256"]))


def expected(context: DecodeContext, case_id: str) -> Record:
    require(case_id in DECODE_PINS, "decode trace", "no reviewed decode-trace evidence pins")
    pin = DECODE_PINS[case_id]
    require(set(pin.evidence_sha256) == set(EVIDENCE_FILES), "decode pins", "evidence set differs")
    evidence: Record = {key: reference(context.evidence_root / relative, pin.evidence_sha256[key])
                        for key, relative in EVIDENCE_FILES.items()}
    docs = {key: read_document(context.evidence_root / EVIDENCE_FILES[key])
            for key in ("identity", "anchors", "workload", "boundary", "edge")}
    model, dataset = reference(context.model, pin.model_sha256), reference(context.dataset, pin.dataset_sha256)
    library = reference(context.library, ARTIFACTS["library"])
    shared = reference(context.shared_library, ARTIFACTS["shared_library"])
    native = source_identity(context.shared_library, pin.profile)
    for name, value in native.source_sha256:
        wanted = pin.memory_sha256 if name.endswith(f"/{pin.profile}.json") else NATIVE_SOURCES.get(name)
        require(value == wanted, "decode native source", name)
    binding = verify_build(context.rtl_build, pin.profile)
    contract = hardware_contract(pin.profile)
    facts = object_value(contract["facts"], "facts")
    require(binding["hardware_contract"] == contract and binding["sha256"] == pin.rtl_build_binding_sha256 and
            facts["dim"] == 32, "decode hardware", "RTL build, Gemmini configuration or DIM differs")
    tool_lock = reference(context.rtl_build / "tool-lock.json", pin.tool_lock_sha256)
    tools = read_document(context.rtl_build / "tool-lock.json")
    upstream = json.loads(str(object_value(object_value(tools["build_configuration"], "build configuration")[
        "src/gemmini/UPSTREAM.lock.json"], "upstream lock")["text"]))

    # Workload identity: frozen before any certification run, re-derived from the producer's own outputs.
    identity, workload = docs["identity"], docs["workload"]
    application = object_value(json.loads(
        (context.evidence_root / EVIDENCE_FILES["application"]).read_text().splitlines()[0]), "application")
    chunk = object_value(array_value(workload["chunks"], "chunks")[0], "chunk")
    tokens = array_value(chunk["input_tokens"], "input tokens")
    generated = array_value(application["generated_tokens"], "generated tokens")
    profile, works, trace_digest = load_trace(context.evidence_root / EVIDENCE_FILES["trace"])
    work_ids: list[JsonValue] = [work.identity for work in works]
    bindings: list[JsonValue] = [work_binding(work) for work in works]
    steps = (len(works) - pin.prefill_works) // pin.works_per_step
    lm_head = [work.identity for work in works if max(work.inputs[:3]) > 8192]
    decode_lm_head = [pin.prefill_works + pin.works_per_step * step + offset
                      for step in range(steps) for offset in (pin.works_per_step - 2, pin.works_per_step - 1)]
    producer = reference(Path(str(object_value(identity["producer"], "producer")["path"])), pin.producer_sha256)
    require(Path(str(workload["model_path"])).resolve() == context.model.resolve() and
            Path(str(workload["dataset_path"])).resolve() == context.dataset.resolve() and
            {key: workload[key] for key in GENERATION} == GENERATION and
            len(tokens) == GENERATION["context_tokens"] and digest(tokens) == pin.input_tokens_sha256 and
            len(generated) == GENERATION["requested_generated_tokens"] and
            digest(generated) == pin.generated_tokens_sha256 and application["complete"] is True,
            "decode workload", "model, input, generation configuration or generated tokens differ")
    require(profile == pin.profile and trace_digest == sha_of(evidence, "trace") == pin.trace_sha256 and
            work_ids == list(range(len(works))) and digest(bindings) == pin.work_binding_fingerprint and
            pin.prefill_works + steps * pin.works_per_step == len(works) and
            steps == pin.decode_steps == GENERATION["requested_generated_tokens"] - 1 and
            lm_head == [pin.prefill_works - 2, pin.prefill_works - 1, *decode_lm_head],
            "decode workload", "trace, decode length or prefill/decode/lm_head structure differs")
    identity_trace = object_value(identity["trace"], "identity trace")
    require(object_value(identity["model"], "model")["sha256"] == model["sha256"] and
            object_value(identity["input"], "input")["input_tokens_sha256"] == pin.input_tokens_sha256 and
            object_value(identity["output"], "output")["generated_tokens_sha256"] == pin.generated_tokens_sha256 and
            object_value(identity_trace["npu_trace"], "trace")["sha256"] == pin.trace_sha256 and
            identity_trace["work_binding_fingerprint"] == pin.work_binding_fingerprint and
            object_value(identity["decode_schedule"], "schedule")["policy"] == OFFER_POLICY and
            {key: object_value(identity["clock"], "clock")[key] for key in CLOCK} == CLOCK and
            object_value(identity["hardware"], "hardware")["dim"] == facts["dim"],
            "decode identity", "frozen identity disagrees with the certified workload, schedule, clock or DIM")

    # RTL calibration anchors: existing evidence only, every file re-hashed and re-validated.
    anchors = docs["anchors"]
    actual = object_value(anchors["actual_trace"], "actual-trace anchor")
    actual_ref, scope = anchored(actual, "certificate"), object_value(actual["context"], "anchor context")
    require(actual_ref["sha256"] == pin.actual_trace_certificate_sha256 and
            Path(str(scope["library"])).resolve() == context.library.resolve() and
            Path(str(scope["shared_library"])).resolve() == context.shared_library.resolve(),
            "decode prefill anchor", "actual-trace certificate or its libraries differ")
    actual_path = Path(str(actual_ref["path"]))
    _ = actual_trace_certificate.validate(actual_path, EvidenceContext(*(Path(str(scope[key])) for key in (
        "evidence_root", "library", "shared_library", "base_parent", "run_aware_parent", "service_parent"))))
    actual_doc = read_document(actual_path)
    certified = object_value(actual_doc["state_domain"], "certified domain")
    tag_limit, row_limit = integer(certified, "allowed_tag_peak"), integer(certified, "allowed_row_peak")
    _, prefill_works, _ = load_trace(Path(str(object_value(actual_doc["trace"], "certified trace")["path"])))
    require([work_binding(work) for work in prefill_works] == bindings[:pin.prefill_works],
            "decode simulator mismatch", "decode prefill is not the RTL-certified actual trace")
    domain = state_domain(pin.profile, str(actual_doc["state_domain_revision"]))
    try:
        for work in works:
            domain.check_work_class(WorkClass(work.provenance, work.residual_work_revision or "dense"))
    except StateDomainError as error:
        raise StatefulCertificateError("decode boundary issue", f"work class outside the certified domain: {error}")
    milestone = object_value(anchors["milestone1"], "milestone anchor")
    m_refs = {key: anchored(milestone, key) for key in (
        "stimulus", "stimulus_text", "capture", "comparison", "variant", "stimulus_builder", "variant_builder")}
    m_docs = {key: read_document(Path(str(m_refs[key]["path"]))) for key in ("stimulus", "capture", "comparison", "variant")}
    m_capture, m_comparison, m_variant, m_stimulus = (m_docs[key] for key in ("capture", "comparison", "variant", "stimulus"))
    m_command = array_value(m_capture["command"], "milestone command")
    m_ids: list[JsonValue] = list(range(pin.milestone_work_count))
    require(m_capture["status"] == "PASS_CAPTURE" and m_capture["child_returncode"] == 0 and
            m_capture["child_reaped"] is True and m_command[1] == m_refs["stimulus_text"]["path"] and
            all(flag in m_command for flag in CAPTURE_FLAGS) and
            sha256(Path(str(m_command[0]))) == object_value(m_variant["binary"], "variant binary")["sha256"] and
            object_value(m_variant["source"], "variant source")["sha256"] == sha256(SOURCE) and
            object_value(m_capture["harness"], "harness")["sha256"] == sha256(ROOT / HARNESS["capture"]) and
            m_stimulus["selected_work_ids"] == m_ids and m_stimulus["native_only_work_ids"] == [] and
            m_stimulus["availability"] == [0] * pin.milestone_work_count,
            "decode milestone anchor", "milestone-1 capture, probe or stimulus differs")
    m_raw: Record = {}
    for side in ("rtl", "model"):
        item = object_value(object_value(object_value(m_capture["artifacts"], "artifacts")[side], side)["raw"], side)
        _ = reference(Path(str(item["path"])), str(item["sha256"]))
        m_raw[side] = {key: item[key] for key in ("sha256", "restored_sha256", "lines", "raw_bytes")}
    m_pairs = object_value(m_comparison["selected_events_and_queue"], "milestone streams")
    m_rtl, m_model = object_value(m_pairs["rtl"], "rtl"), object_value(m_pairs["model"], "model")
    endpoints = [object_value(row, "endpoint") for row in array_value(m_comparison["endpoints"], "endpoints")]
    require(m_comparison["status"] == "EXACT" and m_comparison["work_ids"] == m_ids and
            m_comparison["native_only_work_ids"] == [] and
            (m_comparison["tag_peak"], m_comparison["row_peak"]) == (tag_limit, row_limit) and
            object_value(m_comparison["stimulus"], "stimulus")["sha256"] == m_refs["stimulus"]["sha256"] and
            object_value(m_comparison["capture"], "capture")["sha256"] == m_refs["capture"]["sha256"] and
            object_value(m_comparison["harness"], "harness")["sha256"] == sha256(ROOT / HARNESS["comparison"]) and
            m_rtl["sha256"] == object_value(m_raw["rtl"], "rtl")["restored_sha256"] and
            m_model["sha256"] == object_value(m_raw["model"], "model")["restored_sha256"] and
            integer(m_rtl, "events") == integer(m_model, "events") > 0 and
            integer(m_rtl, "queue_edges") == integer(m_model, "queue_edges") > 0 and
            m_rtl["works"] == m_model["works"] == len(endpoints) == pin.milestone_work_count and
            [row["work_binding"] for row in endpoints] == bindings[:pin.milestone_work_count],
            "decode milestone anchor", "milestone-1 RTL/model comparison is not exact over this trace's works")
    transition_anchor = object_value(anchors["transition"], "transition anchor")
    t_ref, t_base = anchored(transition_anchor, "certificate"), anchored(transition_anchor, "base_certificate")
    try:
        admitted = validate_transition(Path(str(t_ref["path"])), Path(str(t_base["path"])), context.shared_library)
    except TransitionError as error:
        raise StatefulCertificateError("decode transition anchor", str(error))
    require(admitted.certificate_sha256 == pin.transition_certificate_sha256 and
            admitted.current_library_sha256 == shared["sha256"],
            "decode transition anchor", "CURRENT library is not the transition-admitted library")

    # Simulator output: two stepping modes, identical records and hashes, then state and event invariants.
    boundary, edge = docs["boundary"], docs["edge"]
    for report, mode in ((boundary, "boundary"), (edge, "edge")):
        require(report["schema"] == REPLAY_SCHEMA and report["status"] == "PASS" and report["mode"] == mode and
                report["work_ids"] == work_ids and
                object_value(report["identity"], "identity")["sha256"] == sha_of(evidence, "identity") and
                object_value(report["trace"], "trace")["sha256"] == pin.trace_sha256 and
                object_value(report["library"], "library")["sha256"] == shared["sha256"] and
                object_value(report["harness"], "harness")["sha256"] == sha256(ROOT / HARNESS["replay"]) and
                object_value(report["records"], "records")["sha256"] == sha_of(evidence, f"{mode}_records") and
                report["same_handle"] is True and report["midrun_resets"] == report["fallbacks"] == 0 and
                report["offer_policy"] == OFFER_POLICY and report["final_cursor"] == pin.final_cursor and
                report["event_count"] == pin.event_count and
                integer(report, "session_budget_cycles") > pin.final_cursor,
                "decode simulator replay", f"{mode} replay is not a complete one-session replay of this trace")
    hashes = object_value(boundary["hashes"], "hashes")
    records = {mode: context.evidence_root / EVIDENCE_FILES[f"{mode}_records"] for mode in ("boundary", "edge")}
    require(hashes == object_value(edge["hashes"], "edge hashes") and
            parity_digest(records["boundary"]) == parity_digest(records["edge"]) == hashes["records_parity"],
            "decode simulator mismatch", "boundary and per-edge stepping disagree")
    transition = inspect(records["boundary"], [work.identity for work in works], tag_limit, row_limit + 1)
    require(transition["final_cursor"] == pin.final_cursor, "decode boundary issue",
            "replay state leaves the RTL-observed range or the final cursor differs")
    rows = [object_value(json.loads(line), "record") for line in records["boundary"].read_text().splitlines()]
    windows = [object_value(row["window"], "window") for row in rows]
    events = [object_value(row["events"], "events") for row in rows]
    require(all(integer(item, "count") > 0 and item["first_id"] == (1 if index == 0 else integer(events[index - 1], "last_id") + 1)
                and integer(item, "last_id") - integer(item, "first_id") + 1 == item["count"] and
                integer(windows[index], "accepted_cycle") <= integer(item, "min_cycle") <=
                integer(item, "max_cycle") <= integer(windows[index], "resource_ready_cycle")
                for index, item in enumerate(events)) and integer(events[-1], "last_id") == pin.event_count,
            "decode simulator mismatch", "event ids, ownership or windows are inconsistent")
    require(all(row["request_available_cycle"] == 0 and row["port_offer_cycle"] == row["offered"] and
                all(row[rtl_key] == windows[index][native_key] for rtl_key, native_key in WINDOW)
                for index, row in enumerate(endpoints)),
            "decode simulator mismatch", "milestone-1 RTL windows differ from the simulator replay")
    ready = [integer(window, "resource_ready_cycle") for window in windows]
    ends = [pin.prefill_works - 1 + pin.works_per_step * step for step in range(steps + 1)]
    step_cycles: list[JsonValue] = [ready[end] - ready[start] for start, end in zip(ends, ends[1:])]
    sources: Record = dict(native.source_sha256)
    sources.update({name: sha256(ROOT / name) for name in (*CONSUMER_SOURCES, *HARNESS.values())})
    return {
        "schema": SCHEMA, "version": 1, "artifact_role": "DECODE_TRACE_CYCLE_CERTIFICATE",
        "validation_scope": "SCOPED_EVIDENCE", "production_admitted": False, "case_id": case_id,
        "decode_domain_revision": REVISION, "certification_basis": BASIS, "profile": pin.profile,
        "precision": "a8w8", "dim": facts["dim"],
        "workload": {"model": model, "dataset": dataset, "producer": producer, "trace": evidence["trace"],
                     "lifecycle": evidence["lifecycle"], "generation": dict(GENERATION),
                     "input_tokens_sha256": pin.input_tokens_sha256,
                     "prompt_fingerprint": object_value(identity["input"], "input")["prompt_fingerprint"],
                     "generated_tokens_sha256": pin.generated_tokens_sha256,
                     "logits_fingerprint": object_value(identity["output"], "output")["logits_fingerprint"],
                     "work_binding_fingerprint": pin.work_binding_fingerprint, "trace_work_count": len(works),
                     "prefill_works": pin.prefill_works, "decode_steps": steps,
                     "works_per_decode_step": pin.works_per_step, "lm_head_work_ids": [item for item in lm_head]},
        "decode_schedule": {"policy": OFFER_POLICY, "availability_cycles": 0,
                            "digest": object_value(identity["decode_schedule"], "schedule")["digest"]},
        "simulator": {"library": library, "shared_library": shared, "replays": {"boundary": evidence["boundary"],
                      "edge": evidence["edge"]}, "hashes": hashes, "event_count": pin.event_count,
                      "final_cursor": pin.final_cursor, "state_domain_revision": actual_doc["state_domain_revision"],
                      "tag_limit": tag_limit, "row_limit": row_limit},
        "rtl_calibration": {"actual_trace_certificate": actual_ref, "prefill_work_ids_rtl_exact":
                            actual_doc["rtl_work_ids"], "milestone1": {**m_refs, "raw_streams": m_raw,
                            "event_pairs": m_rtl["events"], "queue_edge_pairs": m_rtl["queue_edges"]},
                            "transition_certificate": t_ref, "base_certificate": t_base,
                            "rtl_build_binding_sha256": binding["sha256"], "tool_lock": tool_lock,
                            "chipyard_commit": upstream["chipyard"]["commit"],
                            "gemmini_commit": upstream["gemmini"]["commit"],
                            "rtl_simulator": str(object_value(object_value(tools["tools"], "tools")["verilator"],
                                                              "verilator")["version_output"]).strip()},
        "software": {"source_sha256": sources, "source_baseline": identity["sources"],
                     "producer_build_flags": object_value(identity["producer"], "producer")["build_flags"]},
        "hardware": {"hardware_contract_sha256": digest(contract), "clock": dict(CLOCK)},
        "evidence": evidence, "transition": transition,
        "coverage": {"rtl_exact_work_ids": m_ids, "simulator_certified_work_count": len(works),
                     "complete_trace": True, "extension_by_repetition": False},
        "cycles": {"final_cursor": pin.final_cursor, "prefill_cycles": ready[pin.prefill_works - 1],
                   "decode_step_cycles": step_cycles, "decode_steps_identical": len(set(map(str, step_cycles))) == 1,
                   "first_work_ending_past_2_32": next(index for index, value in enumerate(ready) if value >= 1 << 32)},
        "rtl_reexecution_triggers": {"simulator_mismatch": "NONE", "boundary_issue": "NONE"},
        "DECODE_FULL_TRACE_RTL_EXACT": "NOT_RUN",
        "formal_proof": False, "cycle_count_is_not_latency_ms": True, "E2E_RECONSTRUCTION_READY": "NOT_READY",
    }


def validate(path: Path, context: DecodeContext) -> Record:
    document = read_document(path)
    require(document.get("schema") == SCHEMA, "decode trace schema", "decode trace certificate required")
    require(json.dumps(document, sort_keys=True) ==
            json.dumps(expected(context, str(document.get("case_id"))), sort_keys=True),
            "decode trace certificate", "workload, anchor, simulator, source or library binding differs")
    return document


def build(output: Path, context: DecodeContext, case_id: str) -> Path:
    from sim.cycle.stateful_sequence_replay_certificate import _publish

    _publish(output, expected(context, case_id))
    validate(output, context)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Decode-trace cycle certificate")
    for name in ("evidence_root", "library", "shared_library", "model", "dataset", "rtl_build"):
        parser.add_argument(f"--{name.replace('_', '-')}", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("build")
    make.add_argument("--case", required=True)
    make.add_argument("output", type=Path)
    commands.add_parser("validate").add_argument("path", type=Path)
    args = parser.parse_args()
    context = DecodeContext(args.evidence_root, args.library, args.shared_library, args.model, args.dataset,
                            args.rtl_build)
    try:
        match args.command:
            case "build":
                path = build(args.output, context, args.case)
                print(json.dumps({"certificate": str(path), "sha256": sha256(path), "status": "VERIFIED"}))
            case "validate":
                document = validate(args.path, context)
                print(json.dumps({"certificate": str(args.path), "status": "VERIFIED",
                                  "certification_basis": document["certification_basis"]}))
            case unreachable:
                assert_never(unreachable)
    except (StatefulCertificateError, BuildFailure, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "REJECTED", "error": str(error)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
