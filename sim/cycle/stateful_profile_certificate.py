"""Current-library DIM32 evidence for one complete, finite producer corpus."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Final

from sim.cycle.certificate_contract import TIMING, object_value, read_document
from sim.cycle.npu_trace_schema import Record, integer
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_binding import source_identity
from sim.cycle.stateful_domain import A8D32_REVISION, state_domain
from sim.cycle.stateful_sequence_evidence import (
    ROOT,
    EvidenceContext,
    reference,
    require,
)
from sim.cycle.stateful_sequence_tag6_pins import ARTIFACTS, NATIVE_SOURCES
from sim.tests.cycle.compositional_sequence_bounded import compare_bounded_sequences
from sim.tests.cycle.compositional_sequence_v2_payload import payload_geometry
from sim.tests.cycle.compositional_sequence_v2_stream import _queue_edge

SCHEMA: Final = "stateful-profile-domain-v1"
PROFILE: Final = "a8w8-d32-hp1"
CASE_ID: Final = "actual-a8w8-d32-producer14"
PROOF: Final = "profiles/a8w8-d32-hp1/back-to-back-v1/report.json"
PROOF_SHA256: Final = "73590ec69af40c58d0d5dccf954212a5b993a1232e818200a847fe5a9dacd9e6"
MEMORY_SHA256: Final = "e3dec90f975ea565e86b97cb086415d9babe17cc87b1e9b24b774d7b49d2d952"
INHERITED_SHA256: Final = "11b07b3b9f7d6f5da73cf83fdeb43346ece1508c8c76767b63e3210ff560c3fd"
PARENT_SHA256: Final = {
    "base": "44672d186182a224ee10e5e3723b66119a424a8752963e47ad8dac9b0cfe2ee8",
    "run_aware": "74f1d4253e6a01369fbe34ba987141f73dd3216df62d1dd63e25b047d3ad436f",
    "service": "d1f9b7e7932d05e272b0363bf69ee3565863aff8029d91bd32ddb58f049ed180",
}
SOURCE_NAMES: Final = (
    "sim/cycle/stateful_profile_certificate.py", "sim/cycle/stateful_domain.py",
    "sim/cycle/stateful_domain_admission.py", "sim/cycle/stateful_measurement_contract.py",
    "sim/cycle/stateful_measurement.py",
    "sim/cycle/execution_cli.py", "sim/cycle/scheduler.py", "sim/cycle/scheduler_sqlite.py",
    "sim/cycle/execution_sequence_admission.py", "sim/cycle/execution_sequence_provider.py",
    "sim/cycle/stateful_sequence_certificate.py", "sim/cycle/sequence_binding.py",
    "sim/cycle/sequence_binding_abi.py", "sim/cycle/certificate_contract.py",
    "sim/cycle/stateful_sequence_evidence.py",
    "sim/tests/cycle/compositional_sequence_bounded.py",
    "sim/tests/cycle/compositional_sequence_v2_payload.py",
    "sim/tests/cycle/compositional_sequence_v2_stream.py",
)


def _artifact(value: Record) -> Record:
    require(set(value) == {"path", "sha256"}, "profile artifact", "path/SHA256 reference required")
    return reference(Path(str(value["path"])), str(value["sha256"]))


def expected(context: EvidenceContext) -> Record:
    """Recheck fixed proof bytes and exact streams, not caller-supplied PASS labels."""
    require(context.tag6_evidence_input is context.current_evidence_input is context.domain_delta_input is None,
            "profile context", "historical and profile selectors are mutually exclusive")
    library = reference(context.library, ARTIFACTS["library"])
    shared = reference(context.shared_library, ARTIFACTS["shared_library"])
    parents: Record = {
        name: reference(path, PARENT_SHA256[name])
        for name, path in (("base", context.base_parent), ("run_aware", context.run_aware_parent),
                           ("service", context.service_parent))
    }
    inherited_path = context.evidence_root / "regression/inherited-current/report.json"
    inherited_ref = reference(inherited_path, INHERITED_SHA256)
    inherited = read_document(inherited_path)
    require(inherited["status"] == "PASS" and
            object_value(inherited["library"], "inherited library")["sha256"] == shared["sha256"],
            "profile parents", "current-library equivalence required")
    for name, count in (("base240", 240), ("run-aware42", 42), ("drained1056", 1056),
                        ("exact30", 30), ("guarded-stateful80", 80)):
        scope = object_value(object_value(inherited["scopes"], "scopes")[name], name)
        reference(Path(str(scope["path"])), str(scope["sha256"]))
        require(scope["status"] == "PASS" and scope["exact"] == scope["expected"] == count and
                scope["mismatch_count"] == 0, "profile parents", f"scope differs: {name}")
    identity = source_identity(context.shared_library, PROFILE)
    for name, digest in identity.source_sha256:
        wanted = MEMORY_SHA256 if name.endswith(f"/{PROFILE}.json") else NATIVE_SOURCES.get(name)
        require(digest == wanted, "profile native source", f"unreviewed source: {name}")
    proof_path = context.evidence_root / PROOF
    proof_ref = reference(proof_path, PROOF_SHA256)
    proof = read_document(proof_path)
    require(proof["schema"] == "im2p-stateful-profile-exact-v1" and proof["status"] == "EXACT" and
            proof["profile"] == PROFILE and proof["work_count"] == 14 and
            proof["work_ids"] == list(range(14)) and proof["initial_reset_count"] == 1 and
            proof["midrun_resets"] == proof["fallbacks"] == 0 and
            proof["offer_policy"] == "back-to-back-npu-only-independent-readiness" and
            proof["final_resource_cycle"] == 291379,
            "profile proof", "finite original corpus or session policy differs")
    artifacts = {name: _artifact(object_value(proof[name], name))
                 for name in ("input", "raw", "probe", "absolute_proof", "harness")}
    stimulus = read_document(Path(str(artifacts["input"]["path"])))
    absolute = read_document(Path(str(artifacts["absolute_proof"]["path"])))
    input_ref = _artifact(object_value(stimulus["absolute_input"], "absolute input"))
    original = read_document(Path(str(input_ref["path"])))
    artifacts["absolute_input"] = input_ref
    require(absolute["status"] == "PASS_ABSOLUTE_MODEL_RTL" and absolute["work_count"] == 14 and
            absolute["cycle_library_sha256"] == library["sha256"] and
            absolute["stimulus_file_sha256"] == input_ref["sha256"] and
            original["fixture_kind"] == "GENUINE_COMPLETE_PARENT",
            "profile proof", "independent genuine-parent proof differs")
    for name, digest in object_value(original["source_sha256"], "probe sources").items():
        require(sha256(ROOT / name) == digest, "profile proof source", f"changed: {name}")
    producer = object_value(stimulus["producer_artifacts"], "producer")
    for name, value in producer.items():
        artifacts[f"producer/{name}"] = _artifact(object_value(value, name))
    raw = Path(str(artifacts["raw"]["path"]))
    compared = compare_bounded_sequences(raw, raw, tuple(range(14)), payload_stimulus=stimulus)
    require(json.dumps(asdict(compared), sort_keys=True) ==
            json.dumps(proof["selected_events_and_queue"], sort_keys=True) and
            compared.rtl.events == compared.model.events == 280737 and
            compared.rtl.queue_edges == compared.model.queue_edges == 2286,
            "profile stream", "complete selected-event/queue coverage differs")
    geometry = payload_geometry(stimulus)
    peaks = {"RTL": [0, 0], "MODEL": [0, 0]}
    with raw.open("rb") as stream:
        for line in stream:
            for side, values in peaks.items():
                prefix = f"{side}_QUEUE_EDGE_V2".encode()
                if line.startswith(prefix + b" "):
                    generation, old, new = _queue_edge(line, prefix, geometry)
                    require(generation == 1, "profile state", "reset during sequence")
                    for index, field in enumerate(("tag_count", "row_count")):
                        values[index] = max(values[index], integer(old, field), integer(new, field))
    require(peaks == {"RTL": [4, 3], "MODEL": [4, 3]},
            "profile state", "observed queue domain changed")
    sources: Record = dict(identity.source_sha256)
    sources.update({name: sha256(ROOT / name) for name in SOURCE_NAMES})
    return {
        "schema": SCHEMA, "version": 1, "artifact_role": "MODEL_STATE_VALIDATION",
        "validation_scope": "SCOPED_EVIDENCE", "production_admitted": False,
        "profile": PROFILE, "case_id": CASE_ID, "state_domain_revision": A8D32_REVISION,
        "state_domain": asdict(state_domain(PROFILE, A8D32_REVISION)),
        "trace": artifacts["producer/trace"], "trace_work_count": 14, "work_ids": list(range(14)),
        "library": library, "shared_library": shared, "source_sha256": sources,
        "parents": parents, "parent_equivalence": inherited_ref,
        "proof": proof_ref, "artifacts": {name: value for name, value in artifacts.items()},
        "observed_tag_peak": 4, "observed_row_peak": 3,
        "reference_memory": {"timing": dict(TIMING), "initial_reset_count": 1,
                             "initial_scratchpad_half": 0, "initial_accumulator_half": 0},
        "offer_policy": proof["offer_policy"],
        "scope": "finite reviewed producer14 state domain", "formal_proof": False,
        "execution_scope": "NPU_ONLY_PROJECTION", "application_parent_completion": "NOT_CLAIMED",
        "production_eligibility": "CERTIFIED_PRODUCER14_ONLY",
        "E2E_RECONSTRUCTION_READY": "NOT_READY", "PAPER_CAMPAIGN_COMPLETE": "NOT_RUN",
    }


def validate_document(path: Path, context: EvidenceContext) -> Record:
    document = read_document(path)
    require(document.get("schema") == SCHEMA, "profile schema", "finite profile certificate required")
    require(json.dumps(document, sort_keys=True) == json.dumps(expected(context), sort_keys=True),
            "profile certificate", "domain, trace, source, library or proof binding differs")
    return document


def build(output: Path, context: EvidenceContext) -> Path:
    from sim.cycle.stateful_sequence_replay_certificate import _publish

    document = expected(context)
    _publish(output, document)
    validate_document(output, context)
    return output
