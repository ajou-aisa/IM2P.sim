"""Independent, finite producer-domain certificates for reviewed profile runs."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Final

from sim.cycle.certificate_contract import TIMING, object_value, read_document
from sim.cycle.npu_trace_schema import Record
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_binding import source_identity
from sim.cycle.stateful_domain import PROFILE_EXTENSION_REVISIONS, state_domain
from sim.cycle.stateful_profile_certificate import PARENT_SHA256, SOURCE_NAMES
from sim.cycle.stateful_profile_extension_evidence import (
    artifact,
    check_milestone,
    check_proof,
)
from sim.cycle.stateful_profile_extension_pins import INHERITED_SHA256, PROFILE_PINS
from sim.cycle.stateful_sequence_evidence import (
    ROOT,
    EvidenceContext,
    reference,
    require,
)
from sim.cycle.stateful_sequence_tag6_pins import ARTIFACTS, NATIVE_SOURCES

SCHEMA: Final = "stateful-profile-extension-v1"
EXTENSION_SOURCES: Final = (
    "sim/cycle/stateful_profile_extension.py",
    "sim/cycle/stateful_profile_extension_evidence.py",
    "sim/cycle/stateful_profile_extension_pins.py",
    "sim/tests/cycle/profile_extension_milestone.py",
)


def expected(context: EvidenceContext, profile: str) -> Record:
    require(profile in PROFILE_PINS, "extension profile", "no independent reviewed certificate")
    pin = PROFILE_PINS[profile]
    require(context.tag6_evidence_input is context.current_evidence_input is context.domain_delta_input is None,
            "extension context", "historical selectors cannot grant extension authority")
    library = reference(context.library, ARTIFACTS["library"])
    shared = reference(context.shared_library, ARTIFACTS["shared_library"])
    parents: Record = {name: reference(path, PARENT_SHA256[name]) for name, path in (
        ("base", context.base_parent), ("run_aware", context.run_aware_parent),
        ("service", context.service_parent))}
    regression_path = context.evidence_root / "cycle-provider/inherited-current/report.json"
    regression_ref = reference(regression_path, INHERITED_SHA256)
    regression = read_document(regression_path)
    require(regression["status"] == "PASS" and regression["library"] == shared,
            "extension regression", "fresh model/current library equivalence required")
    for name, count in (("base240", 240), ("run-aware42", 42), ("drained1056", 1056)):
        scope = object_value(object_value(regression["scopes"], "scopes")[name], name)
        reference(Path(str(scope["path"])), str(scope["sha256"]))
        require(scope["status"] == "PASS" and scope["exact"] == scope["expected"] == count and
                scope["mismatch_count"] == 0, "extension regression", name)
    directory = context.evidence_root / "cycle-provider/profiles" / profile
    proof_ref = reference(directory / "back-to-back-v1/report.json", pin.proof_sha256)
    proof = read_document(Path(str(proof_ref["path"])))
    checked = check_proof(proof, profile, pin.work_count)
    require(checked["library_sha256"] == library["sha256"] and
            checked["tag_peak"] == 4 and checked["row_peak"] == 3,
            "extension native/domain", "library or observed finite state differs")
    milestone_ref = reference(directory / "milestone-v2.json", pin.milestone_sha256)
    milestone = read_document(Path(str(milestone_ref["path"])))
    check_milestone(milestone, profile, proof)
    artifacts = object_value(checked["artifacts"], "artifacts")
    require(milestone["trace"] == artifacts["producer/trace"] and milestone["library"] == shared,
            "extension milestone identity", "profile trace or library changed")
    event_ref = reference(context.evidence_root / "cycle-provider/layout-audit" / f"events-{profile}.json",
                          pin.events_sha256)
    event = read_document(Path(str(event_ref["path"])))
    require(event["profile"] == profile and event["raw_file_sha256"] ==
            object_value(artifacts["raw"], "raw")["sha256"], "extension events", "raw identity differs")
    rtl, model = object_value(event["rtl"], "RTL events"), object_value(event["model"], "model events")
    for field in ("canonical_event_sha256", "selected_event_count", "event_multiplicity", "work_multiplicity"):
        require(rtl[field] == model[field], "extension events", field)
    layout_ref = reference(context.evidence_root / "cycle-provider/layout-audit/layout-audit.md",
                           "05767d466becbfc64c144e74ae9f51498bf21a0b970d3793a3455abfc4335d29")
    identity = source_identity(context.shared_library, profile)
    for name, digest in identity.source_sha256:
        wanted = pin.memory_sha256 if name.endswith(f"/{profile}.json") else NATIVE_SOURCES.get(name)
        require(digest == wanted, "extension native source", name)
    revision = next(name for name, value in PROFILE_EXTENSION_REVISIONS.items() if value == profile)
    domain = state_domain(profile, revision)
    sources: Record = dict(identity.source_sha256)
    sources.update({name: sha256(ROOT / name) for name in (*SOURCE_NAMES, *EXTENSION_SOURCES)})
    coverage_ref = reference(directory / "parent-coverage.json", pin.coverage_sha256)
    coverage = read_document(Path(str(coverage_ref["path"])))
    require(coverage["profile"] == profile and coverage["status"] == "PASS" and
            coverage["matrix_regression"] == regression_ref and
            coverage["profile_counts"] == {"base240": 40, "run-aware42": 7, "drained1056": 176},
            "extension profile coverage", "matrix denominators and profile subsets differ")
    for value in object_value(coverage["parents"], "coverage parents").values():
        artifact(object_value(value, "parent"))
    return {
        "schema": SCHEMA, "version": 1, "artifact_role": "MODEL_STATE_VALIDATION",
        "validation_scope": "SCOPED_EVIDENCE", "production_admitted": False,
        "profile": profile, "precision": int(profile[1]), "dim": domain.dim,
        "case_id": revision, "state_domain_revision": revision, "state_domain": asdict(domain),
        "domain_schema": {"profile": f"{domain.precision}-D{domain.dim}",
                          "precision": int(profile[1]), "dim": domain.dim,
                          "tag_peak_limit": 4, "row_peak_limit": 3,
                          "ready_boundary_rule": domain.ready_mask_rule,
                          "initial_state": {"scratchpad_half": 0, "accumulator_half": 0},
                          "supported_work_types": ["DENSE", "RESIDUAL"],
                          "supported_run_types": ["SINGLE_RUN", "MULTI_RUN"]},
        "trace": artifacts["producer/trace"], "trace_work_count": pin.work_count,
        "work_ids": list(range(pin.work_count)), "source_sha256": sources,
        "library": library, "shared_library": shared, "parents": parents,
        "parent_equivalence": regression_ref, "parent_profile_coverage": coverage_ref,
        "proof": proof_ref, "milestone_parity": milestone_ref, "selected_event_stream": event_ref,
        "layout_audit": layout_ref, "artifacts": artifacts,
        "hardware_contract": checked["hardware_contract"],
        "observed_tag_peak": 4, "observed_row_peak": 3,
        "reference_memory": {"timing": dict(TIMING), "initial_reset_count": 1,
                             "initial_scratchpad_half": 0, "initial_accumulator_half": 0},
        "offer_policy": proof["offer_policy"], "scope": "finite reviewed producer state domain",
        "formal_proof": False, "execution_scope": "NPU_ONLY_PROJECTION",
        "application_parent_completion": "NOT_CLAIMED",
        "production_eligibility": "CERTIFIED_TRACE_ONLY", "E2E_RECONSTRUCTION_READY": "NOT_READY",
        "PAPER_CAMPAIGN_COMPLETE": "NOT_RUN",
    }


def validate_document(path: Path, context: EvidenceContext) -> Record:
    document = read_document(path)
    require(document.get("schema") == SCHEMA, "extension schema", "profile certificate required")
    require(json.dumps(document, sort_keys=True) ==
            json.dumps(expected(context, str(document.get("profile"))), sort_keys=True),
            "extension certificate", "domain, precision, DIM, source, library or proof differs")
    return document


def build(output: Path, context: EvidenceContext, profile: str) -> Path:
    from sim.cycle.stateful_sequence_replay_certificate import _publish

    _publish(output, expected(context, profile))
    validate_document(output, context)
    return output
