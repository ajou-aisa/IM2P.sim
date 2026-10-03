"""Finite reviewed Tag6 evidence, separate from the preserved Tag5 certificate."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Final

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.certificate_contract import (
    TIMING,
    array_value,
    object_value,
    read_document,
)
from sim.cycle.npu_trace_schema import Record
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_domain import TAG5_PROFILE, TAG6_REVISION, profile_domain
from sim.cycle.stateful_sequence_evidence import (
    ROOT,
    EvidenceContext,
    reference,
    require,
)
from sim.cycle.stateful_sequence_tag6_pins import ARTIFACTS, NATIVE_SOURCES, PROOFS

SCHEMA: Final = "stateful-tag6-domain-v1"
INPUT_SCHEMA: Final = "im2p-stateful-tag6-input-v1"
MUTATIONS: Final = (
    "tag7-occupancy", "tag7-forced-admission", "tag-id", "row-count",
    "mesh-ownership", "half-carry", "accepted-epoch", "event-plus-one",
)
SOURCE_NAMES: Final = (
    "sim/cycle/sequence_binding.py", "sim/cycle/sequence_binding_abi.py", "sim/cycle/cli.py",
    "sim/cycle/sequence_domain.py", "sim/cycle/stateful_sequence_evidence.py",
    "sim/cycle/stateful_sequence_evidence_tag6.py", "sim/cycle/stateful_sequence_tag6_pins.py",
    "sim/cycle/npu_trace.py", "sim/cycle/npu_trace_schema.py", "sim/cycle/npu_trace_integrity.py",
    "sim/cycle/npu_trace_calls.py", "sim/cycle/npu_trace_hosts.py", "sim/cycle/npu_trace_runs.py",
    "sim/cycle/certificate_contract.py", "sim/cycle/reconstruct_graph.py",
    "scripts/gemmini_replay_contract.py", "scripts/gemmini_resolve_profile.py",
    "sim/tests/cycle/compositional_sequence_probe.cpp",
    "sim/tests/cycle/compositional_sequence_bounded.py", "sim/tests/cycle/compositional_sequence_mesh.py",
    "sim/tests/cycle/compositional_sequence_v2_base.py", "sim/tests/cycle/compositional_sequence_v2_stream.py",
    "sim/tests/cycle/compositional_sequence_v2_payload.py",
)


def _bound(value: Record) -> Record:
    require(set(value) == {"path", "sha256"}, "Tag6 artifact", "path/SHA256 reference required")
    return reference(Path(str(value["path"])), str(value["sha256"]))


def _proofs(context: EvidenceContext) -> tuple[dict[str, Record], Record]:
    documents: dict[str, Record] = {}
    bindings: Record = {}
    for name, (relative, digest) in PROOFS.items():
        path = context.evidence_root / relative
        bindings[name] = reference(path, digest)
        if path.suffix == ".json":
            documents[name] = read_document(path)
    return documents, bindings


def expected(context: EvidenceContext) -> Record:
    """Bind reviewed exact streams and current interpretation source, never status labels alone."""
    path = context.tag6_evidence_input
    require(path is not None and context.current_evidence_input is None and context.domain_delta_input is None,
            "NOT_READY", "separate Tag6 evidence input required")
    if path is None:
        raise FileNotFoundError("Tag6 input")
    manifest = read_document(path)
    require(set(manifest) == {"schema", "artifacts"} and manifest["schema"] == INPUT_SCHEMA,
            "Tag6 input", "strict reviewed input required")
    supplied = object_value(manifest["artifacts"], "Tag6 artifacts")
    require(set(supplied) == set(ARTIFACTS), "Tag6 input", "artifact closure differs")
    artifacts: Record = {}
    for name, digest in ARTIFACTS.items():
        item = object_value(supplied[name], name)
        require(item.get("sha256") == digest, "Tag6 reviewed pin", f"unreviewed artifact: {name}")
        artifacts[name] = _bound(item)
    require(artifacts["library"] == reference(context.library, ARTIFACTS["library"]) and
            artifacts["shared_library"] == reference(context.shared_library, ARTIFACTS["shared_library"]),
            "Tag6 library", "caller library differs")
    for name, digest in NATIVE_SOURCES.items():
        require(sha256(ROOT / name) == digest, "Tag6 native source", f"changed: {name}")
    documents, proof_refs = _proofs(context)
    comparison, capture, mutations = (documents[name] for name in ("comparison", "capture", "mutations"))
    require(comparison["status"] == "EXACT" and comparison["work_ids"] == list(range(240)) and
            comparison["trace_work_count"] == 374 and comparison["first_tag6_post_edge_cycle"] == 312676159,
            "Tag6 comparison", "actual prefix240 proof differs")
    streams = object_value(comparison["selected_events_and_queue"], "stream comparison")
    for side in ("rtl", "model"):
        compared = object_value(streams[side], side)
        require(compared["events"] == 844325918 and compared["queue_edges"] == 14167135 and
                compared["works"] == 240, "Tag6 comparison", "complete event/queue coverage differs")
        side_artifacts = object_value(object_value(capture["artifacts"], "raw")[side], side)
        for kind in ("raw", "mesh"):
            item = object_value(side_artifacts[kind], kind)
            artifacts[f"{side}/{kind}"] = reference(Path(str(item["path"])), str(item["sha256"]))
        require(compared["sha256"] == object_value(side_artifacts["raw"], "raw")["restored_sha256"],
                "Tag6 raw identity", "verified stream digest differs")
    require(capture["status"] == "PASS_CAPTURE" and capture["child_returncode"] == 0 and
            capture["child_reaped"] is True, "Tag6 capture", "complete independent capture required")
    cases = [object_value(item, "mutation") for item in array_value(mutations["cases"], "mutations")]
    require(mutations["status"] == "PASS" and [item["name"] for item in cases] == list(MUTATIONS) and
            all(item["status"] == "REJECTED" for item in cases),
            "Tag6 mutations", "actual state/event counterexamples required")
    for item in cases:
        artifacts[f"mutation/{item['name']}"] = _bound(object_value(item["artifact"], "mutation artifact"))
    artifacts["mutation_harness"] = _bound(object_value(mutations["harness"], "mutation harness"))
    parity = documents["parity"]
    require(parity["result"] == "EXACT_PARITY" and parity["work_count"] == 240 and
            parity["event_count"] == 950417687 and parity["final_cursor"] == 312771524 and
            parity["exact_event_record_comparison"] is True,
            "Tag6 milestone parity", "exact cycle/event/report/state proof required")
    execution = documents["parity_execution"]
    require(execution["status"] == "PASS" and execution["exit_code"] == 0,
            "Tag6 milestone parity", "successful execution required")
    for name, digest in object_value(execution["source_sha256"], "parity artifacts").items():
        artifacts[f"parity/{Path(name).name}"] = reference(Path(name), str(digest))
    for name, count in (("base240", 240), ("run42", 42), ("drained1056", 1056),
                        ("exact30", 30), ("stateful80", 80)):
        row = documents[name]
        require(row["status"] == "PASS" and row["exact"] == row["expected"] == count and
                row["mismatch_count"] == 0, "Tag6 inherited scope", f"scope differs: {name}")
    prior = read_document(Path(str(object_value(artifacts["tag5_certificate"], "Tag5")["path"])))
    old_parents = object_value(prior["parents"], "Tag5 parents")
    parents: Record = {}
    for name, parent in (("base", context.base_parent), ("run_aware", context.run_aware_parent),
                         ("service", context.service_parent)):
        parents[name] = reference(parent, str(object_value(old_parents[name], name)["sha256"]))
    source: Record = {name: digest for name, digest in NATIVE_SOURCES.items()}
    source.update({name: sha256(ROOT / name) for name in SOURCE_NAMES})
    mutation_results: list[JsonValue] = list(cases)
    return {
        "schema": SCHEMA, "version": 1, "artifact_role": "MODEL_STATE_VALIDATION",
        "validation_scope": "SCOPED_EVIDENCE", "production_admitted": False,
        "state_domain_revision": TAG6_REVISION, "profile": TAG5_PROFILE,
        "domain": asdict(profile_domain(TAG5_PROFILE, TAG6_REVISION)),
        "offer_policy": "back-to-back-npu-only-independent-readiness",
        "trace": artifacts["trace"], "trace_work_count": 374, "proof_work_ids": list(range(240)),
        "library": artifacts["library"], "shared_library": artifacts["shared_library"],
        "parents": parents, "evidence_input": reference(path, sha256(path)),
        "proofs": proof_refs, "artifacts": artifacts, "source_sha256": source,
        "reference_memory": {"timing": dict(TIMING), "initial_reset_count": 1,
                             "initial_scratchpad_half": 0, "initial_accumulator_half": 0},
        "tag_peak": 6, "row_peak": 5, "ready_mask": 0,
        "event_hash": parity["event_sha256"], "selected_event_pairs": 844325918,
        "queue_transition_pairs": 14167135, "first_tag6_cycle": 312676159,
        "mutation_results": mutation_results, "formal_proof": False,
        "scope": "finite reviewed state domain",
        "excluded": ["tag7", "row6", "nonzero-ready-boundary-mask", "other-profiles",
                     "other-traces", "idle-gap-arrivals", "full-E2E-latency", "hardware-clock"],
        "source_scope": "physical domain proof; runtime admission separately binds consumers",
    }


def validate_document(path: Path, context: EvidenceContext) -> Record:
    document = read_document(path)
    require(document.get("schema") == SCHEMA, "Tag6 schema", "distinct domain certificate required")
    require(json.dumps(document, sort_keys=True) == json.dumps(expected(context), sort_keys=True),
            "Tag6 certificate",
            "scope, source, library, corpus or reviewed evidence binding differs")
    return document
