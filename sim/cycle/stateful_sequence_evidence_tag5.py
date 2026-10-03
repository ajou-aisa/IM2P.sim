"""Additive finite tag5 evidence; never substitutes for current parent evidence."""
from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from dataclasses import asdict
from hashlib import sha256 as digest_bytes
from itertools import accumulate
from pathlib import Path
from typing import Final

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.certificate_contract import array_value, object_value, read_document
from sim.cycle.npu_trace_schema import INPUT_KEYS, Record, integer, unique_pairs
from sim.cycle.npu_trace_schema import Work as TraceWork
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_binding import (
    Code,
    Report,
    SequenceSession,
    Settings,
    StopReason,
)
from sim.cycle.sequence_domain import TAG5_PROFILE, TAG5_REVISION, profile_domain
from sim.cycle.sequence_trace_cli import descriptor, load_trace
from sim.cycle.stateful_sequence_evidence import (
    EvidenceContext,
    StatefulCertificateError,
    reference,
    require,
)
from sim.cycle.stateful_sequence_evidence_v2 import ROOT
from sim.tests.cycle.compositional_sequence_bounded import compare_bounded_sequences

INPUT_SCHEMA: Final = "im2p-stateful-tag5-delta-input"
CASES: Final = (
    ("prefix", (0, 6, 7, 8)),
    ("reusable-edge", (0, 1277467, 3832407, 5109877)),
    ("idle-mixed-phase", (3, 1277473, 3832420, 5109900)),
    ("next-parent", (0, 6, 7, 8, 9, 10, 11, 12, 13)),
)
ENDPOINTS: Final = (
    ("offered", "offered_cycle"), ("accepted", "accepted_cycle"),
    ("result_ready", "result_ready_cycle"), ("final_scale_release", "final_scale_release_cycle"),
    ("resource_ready", "resource_ready_cycle"),
    ("next_scratchpad_half", "next_scratchpad_half"),
    ("next_accumulator_half", "next_accumulator_half"),
)
COUNTERS: Final = (
    ("submissions", "planner_loop_count"),
    ("load_requests", "load_request_count"), ("load_responses", "load_response_count"),
    ("store_requests", "store_request_count"), ("store_responses", "store_response_count"),
    ("scale_read_requests", "scale_request_count"), ("scale_read_responses", "scale_response_count"),
)
RAW_FIELDS: Final = (
    "ordinal", "work_id", "request_available_cycle", "port_offer_cycle", "scale_release_count",
    "initial_scratchpad_half", "initial_accumulator_half", "mesh_tag_queue_len",
    "mesh_tag_head_id", "mesh_tag_enqueues", "mesh_tag_dequeues", "mesh_tag_max_occupancy",
    *(key for key, _ in (*ENDPOINTS, *COUNTERS)),
)


def _bound(value: Record) -> Path:
    require(set(value) == {"path", "sha256"}, "domain delta", "artifact reference required")
    path = Path(str(value["path"]))
    reference(path, str(value["sha256"]))
    return path


def _scan(path: Path, stimulus: Record, observed: list[Record]) -> Iterator[bytes]:
    """Inspect every queue state while the existing bounded comparator consumes it."""
    limits = profile_domain(TAG5_PROFILE, TAG5_REVISION)
    headers = 0
    with gzip.open(path, "rb") as stream:
        for line in stream:
            if line.startswith((b"COMPOSITION_RUN ", b"MODEL_RUN ")):
                row = object_value(json.loads(line.split(b" ", 1)[1], object_pairs_hook=unique_pairs), "run")
                headers += 1
                require(headers == 1 and row.get("instance_count") == row.get("reset_count") == 1 and
                        row.get("period") == 5 and row.get("stimulus_sha256") == stimulus["stimulus_sha256"] and
                        row.get("work_count") == len(array_value(stimulus["works"], "works")) and
                        row.get("offer_mode") == "availability-driven",
                        "domain delta", "cold run, period, stimulus or work coverage differs")
            elif line.startswith((b"RTL_QUEUE_EDGE_V2 ", b"MODEL_QUEUE_EDGE_V2 ")):
                edge = object_value(json.loads(line.split(b" ", 1)[1], object_pairs_hook=unique_pairs), "queue")
                for key in ("old", "next"):
                    state = object_value(edge[key], "queue state")
                    require(integer(state, "tag_count") <= limits.max_tag_occupancy and
                            integer(state, "row_count") < limits.max_row_occupancy_exclusive,
                            "domain delta", "tag6 or row6 outside reviewed domain")
            elif line.startswith((b"RTL_QUEUE_EDGE", b"MODEL_QUEUE_EDGE")):
                require(False, "domain delta", "queue payload v2 required")
            elif line.startswith((b"COMPOSITION_WORK ", b"MODEL_WORK ")):
                row = object_value(json.loads(line.split(b" ", 1)[1], object_pairs_hook=unique_pairs), "work")
                require(not line.startswith(b"COMPOSITION_WORK ") or row.get("numeric_pass") is True,
                        "domain delta", "RTL numerical fixture failed")
                observed.append(row)
            yield line
    require(headers == 1, "domain delta", "run header missing")


def _replay(library: Path, stimulus: Record, observed: list[Record], *,
            parsed_trace: tuple[str, tuple[TraceWork, ...], str] | None = None) -> None:
    """Recompute endpoints, halves, counters and sticky guard using the current shared library."""
    trace = _bound(object_value(stimulus["trace"], "trace"))
    profile, works, digest = load_trace(trace) if parsed_trace is None else parsed_trace
    require(digest == object_value(stimulus["trace"], "trace")["sha256"] and
            profile == TAG5_PROFILE and len(works) == stimulus.get("trace_work_count") == 374,
            "domain delta", "original trace denominator/profile differs")
    selected = array_value(stimulus["works"], "works")
    available = array_value(stimulus["availability"], "availability")
    require(len(observed) == len(selected) == len(available), "domain delta", "work coverage differs")
    limits = profile_domain(profile, TAG5_REVISION)
    sticky_peaks = tuple(accumulate((integer(row, "mesh_tag_max_occupancy") for row in observed), max))
    with SequenceSession(library, profile, settings=Settings(
            max_work_cycles=10_000_000, max_session_cycles=1_000_000_000)) as session:
        require(session.status().generation == 1 and session.status().cursor == 0,
                "domain delta", "current native reset differs")
        for ordinal, (work, raw) in enumerate(zip(works[:len(selected)], observed, strict=True)):
            selected_work = object_value(selected[ordinal], "selected work")
            encoded_work = json.dumps(asdict(work), sort_keys=True, separators=(",", ":"))
            require(selected_work.get("work_id") == work.identity and
                    selected_work.get("input") == dict(zip(INPUT_KEYS, work.inputs, strict=True)) and
                    selected_work.get("trace_record") == json.loads(encoded_work) and
                    raw.get("ordinal") == ordinal and raw.get("work_id") == work.identity and
                    raw.get("work_binding") == digest_bytes(encoded_work.encode()).hexdigest(),
                    "domain delta", "original work/run binding differs")
            status = session.status()
            offered = max(integer({"value": available[ordinal]}, "value"), status.cursor)
            require(raw.get("request_available_cycle") == available[ordinal] and
                    raw.get("port_offer_cycle") == offered and
                    raw.get("initial_scratchpad_half") == status.next_scratchpad_half and
                    raw.get("initial_accumulator_half") == status.next_accumulator_half,
                    "domain delta", "availability or carried halves differ")
            require(session.offer(descriptor(work), offered) == Code.OK,
                    "domain delta", "current native offer failed")
            while True:
                code = session.advance_to_boundary(1_000_000_000, 65536)
                stop = session.status().stop_reason
                if code == Code.OK:
                    require(stop == StopReason.REPORT_AVAILABLE, "domain delta", "report boundary missing")
                    break
                require(code == Code.INCOMPLETE and stop == StopReason.SOFT_BUDGET,
                        "domain delta", "current native replay failed")
            report = session.pop_report()
            if not isinstance(report, Report):
                raise StatefulCertificateError("domain delta", "native report missing")
            require(report.logical_work_id == work.identity and report.counters.logical_work_count == 1 and
                    all(raw.get(key) == getattr(report, field) for key, field in ENDPOINTS) and
                    all(raw.get(key) == getattr(report.counters, field) for key, field in COUNTERS),
                    "domain delta", "fresh current native endpoints/counters differ")
            domain, rows = session.domain_snapshot(), session.row_pressure()
            require(domain.generation == rows.generation == report.generation == 1 and
                    domain.cursor == rows.cursor == report.resource_ready_cycle and
                    domain.resource_ready == 1 and domain.tag_count <= limits.tag_capacity and
                    all(tag.rob_valid == 0 for tag in domain.tags[:domain.tag_count]) and
                    domain.ready_violation_mask == limits.ready_violation_mask and
                    domain.max_tag_occupancy == sticky_peaks[ordinal] and
                    domain.max_tag_occupancy <= limits.max_tag_occupancy and
                    rows.max_row_occupancy < limits.max_row_occupancy_exclusive,
                    "domain delta", "fresh native state outside reviewed domain")
        session.verify_identity()


def _endpoints(rtl: list[Record], model: list[Record]) -> None:
    for left, right in zip(rtl, model, strict=True):
        require(isinstance(left.get("work_binding"), str) and
                left["work_binding"] == right.get("work_binding") and
                all(integer(left, key) == integer(right, key) for key in RAW_FIELDS),
            "domain delta", "RTL/model endpoints or per-work tag peak differ")


def recheck_delta(delta: Record) -> None:
    """Recheck all evidence bytes immediately before publication."""
    for value in object_value(delta["artifacts"], "delta artifacts").values():
        _bound(object_value(value, "delta artifact"))


def reviewed_delta(context: EvidenceContext, current: Record) -> Record:
    """Validate a strict additive input only after reviewed_current has succeeded.

    The input contains absolute path/SHA references: prior_certificate,
    source_impact, state_review, and four ordered cases (case_id, stimulus,
    rtl, model). source_impact binds the current source map and both libraries;
    state_review binds its hash and the complete cases array. Neither review
    strings nor saved comparison counts replace raw comparison or native replay.
    """
    path = context.domain_delta_input
    require(path is not None, "NOT_READY", "tag5 delta input missing")
    if path is None:
        raise FileNotFoundError("tag5 delta input")
    manifest = read_document(path)
    artifacts: Record = {"input": reference(path, sha256(path))}
    require(set(manifest) == {"schema", "version", "state_domain_revision", "prior_certificate",
                             "source_impact", "state_review", "cases"} and
            manifest.get("schema") == INPUT_SCHEMA and type(manifest.get("version")) is int and
            manifest["version"] == 1 and manifest.get("state_domain_revision") == TAG5_REVISION,
            "domain delta", "new tag5 revision required; guard4 relabeling is forbidden")
    documents: dict[str, Record] = {}
    for name in ("prior_certificate", "source_impact", "state_review"):
        artifacts[name] = object_value(manifest[name], name)
        documents[name] = read_document(_bound(object_value(artifacts[name], name)))
    prior, impact, review = (documents[name] for name in
                             ("prior_certificate", "source_impact", "state_review"))
    require(prior.get("version") == 2 and all(prior.get(key) == current.get(key) for key in
            ("schema", "artifact_role", "validation_scope", "production_admitted",
             "work_domain_revision", "state_domain_revision", "completeness", "reference_memory")) and
            [object_value(row, "case").get("case_id") for row in array_value(prior.get("cases"), "cases")] ==
            [object_value(row, "case").get("case_id") for row in array_value(current["cases"], "cases")],
            "domain delta", "immutable v2 ancestor scope differs")
    require(impact.get("schema") == "im2p-stateful-tag5-source-impact-v1" and
            impact.get("prior_certificate_sha256") ==
                object_value(artifacts["prior_certificate"], "prior certificate")["sha256"] and
            all(impact.get(key) == current.get(key) for key in ("source_sha256", "library", "shared_library")) and
            review.get("schema") == "im2p-stateful-tag5-review-v1" and review.get("verdict") == "confirmed" and
            review.get("source_impact_sha256") == object_value(artifacts["source_impact"], "impact")["sha256"] and
            review.get("state_domain_revision") == TAG5_REVISION and review.get("cases") == manifest["cases"] and
            review.get("domain") == asdict(profile_domain(TAG5_PROFILE, TAG5_REVISION)),
            "domain delta", "current source/library/domain review differs")
    cases = [object_value(row, "delta case") for row in array_value(manifest["cases"], "cases")]
    require([row.get("case_id") for row in cases] == [name for name, _ in CASES],
            "domain delta", "prefix and three ordered adjacent holdouts required")
    comparisons: list[JsonValue] = []
    traces: dict[Path, tuple[str, tuple[TraceWork, ...], str]] = {}
    for row, (name, availability) in zip(cases, CASES, strict=True):
        require(set(row) == {"case_id", "stimulus", "rtl", "model"},
                "domain delta", "case artifact closure differs")
        paths: dict[str, Path] = {}
        for key in ("stimulus", "rtl", "model"):
            item = object_value(row[key], key)
            paths[key] = _bound(item)
            artifacts[f"{name}/{key}"] = item
        stimulus = read_document(paths["stimulus"])
        ids = tuple(range(len(availability)))
        require(stimulus.get("profile") == TAG5_PROFILE and
                stimulus.get("availability") == list(availability) and
                stimulus.get("selected_work_ids") == list(ids),
                "domain delta", "profile, holdout or work denominator changed")
        trace = object_value(stimulus["trace"], "trace")
        artifacts[f"{name}/trace"] = trace
        trace_path = _bound(trace)
        if trace_path not in traces:
            traces[trace_path] = load_trace(trace_path)
        rtl: list[Record] = []
        model: list[Record] = []
        receipt = compare_bounded_sequences(_scan(paths["rtl"], stimulus, rtl),
            _scan(paths["model"], stimulus, model), ids, payload_stimulus=stimulus)
        require(len(rtl) == len(model) == len(ids), "domain delta", "endpoint coverage differs")
        _endpoints(rtl, model)
        if name == "prefix":
            require(receipt.rtl.events == receipt.model.events == 20141152 and
                    receipt.rtl.queue_edges == receipt.model.queue_edges == 336288 and
                    model[-1].get("mesh_tag_max_occupancy") == 5,
                    "domain delta", "actual tag5 prefix proof shrunk or relabeled")
        _replay(context.shared_library, stimulus, model, parsed_trace=traces[trace_path])
        comparisons.append({"case_id": name, "work_ids": list(ids),
                            "selected_event_pairs": receipt.rtl.events,
                            "queue_transition_pairs": receipt.rtl.queue_edges})
    result: Record = {"state_domain_revision": TAG5_REVISION, "profile": TAG5_PROFILE,
                      "artifacts": artifacts, "cases": comparisons,
                      "parent_scopes": {"base": 240, "run_aware": 42, "service_fixture": 1056,
                                        "service": 30, "stateful": 80},
                      "formal_proof": "NOT_RUN", "full6_supported": False}
    recheck_delta(result)
    for key in ("library", "shared_library"):
        _bound(object_value(current[key], key))
    require(all(sha256(ROOT / name) == digest for name, digest in
                object_value(current["source_sha256"], "sources").items()),
            "source closure", "current source changed during delta validation")
    return result
