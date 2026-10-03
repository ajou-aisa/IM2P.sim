"""Consume the reviewed milestone replay, without rebasing historical artifacts."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Final

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.certificate_contract import (
    PROFILES,
    array_value,
    object_value,
    read_document,
)
from sim.cycle.npu_trace_schema import Record, unique_pairs
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_binding import source_identity
from sim.cycle.stateful_sequence_evidence import (
    EvidenceContext,
    StatefulCertificateError,
    reference,
    require,
)
from sim.cycle.stateful_sequence_evidence_v2 import (
    ROOT,
    CurrentEvidence,
    _python_closure,
)

# These are independent reviewed artifacts, not hashes substituted into old certificates.
REPORT_SHA256: Final = "6229c7a0f2480e0493e4e9a9d3e47010005e1b76a96d56eba1b78884d22d1b32"
ANCESTOR_SHA256: Final = "80e275c64522d731fdf32de26dee1d683a19f9497f86a4f018688343bac954be"
NATIVE_DELTA: Final = {
    "sim/cycle/sequence_c_api.cpp": "3f5aeb89cc6ba21cf08c6abdfaee31affaad1d30f6f0971b70cddf5cfca8a6b5",
    "sim/cycle/CMakeLists.txt": "41dcbf7954ad9b163a0ec6394c67a84ab95c552dab3e0c0d86c0567cce6f0661",
    "sim/include/im2p_cycle_sequence.h": "d872634ec9adff6e9ccc5ea37546a71fdc1c2dfca8486dcdeded30beeb4befaf",
}
SCOPES: Final = {"base240": 240, "run-aware42": 42, "drained1056": 1056,
                 "exact30": 30, "guarded-stateful80": 80}
REPLAY_FIELDS: Final = (
    "schema", "scope", "classification", "status", "expected", "attempted", "exact",
    "mismatch_count", "first_mismatch", "current_library", "legacy_source",
    "retained_artifact_count", "retained_artifacts_sha256",
)


def _artifact(value: Record) -> Path:
    path = Path(str(value["path"]))
    reference(path, str(value["sha256"]))
    return path


def _closure(document: Record) -> Record:
    """Recheck absolute artifact references, including referenced JSON documents."""
    pending: list[JsonValue] = [document]
    references: Record = {}
    while pending:
        value = pending.pop()
        if isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, dict):
            path, digest = value.get("path"), value.get("sha256")
            if isinstance(path, str) and Path(path).is_absolute() and isinstance(digest, str):
                if path in references:
                    require(references[path] == {"path": path, "sha256": digest},
                            "equivalence artifact", f"conflicting reference: {path}")
                    continue
                references[path] = reference(Path(path), digest)
                if Path(path).suffix == ".json":
                    pending.append(json.loads(Path(path).read_text(), object_pairs_hook=unique_pairs))
            pending.extend(value.values())
    return references


def _check_replay(scope: str, retained: Record, fresh: Record) -> None:
    expected = SCOPES[scope]
    require(retained.get("schema") == "im2p-current-model-reused-rtl-validation-v1" and
            retained.get("scope") == scope and retained.get("classification") == "FRESH_MODEL_REUSED_RTL" and
            retained.get("status") == "PASS" and retained.get("expected") ==
            retained.get("attempted") == retained.get("exact") == expected and
            retained.get("mismatch_count") == 0 and retained.get("first_mismatch") is None and
            all(key in retained and fresh.get(key) == retained[key] for key in REPLAY_FIELDS),
            "equivalence replay", f"{scope} counts, identities, outputs or retained artifacts differ")
    if scope == "exact30":
        require(retained.get("expected_work_count") == retained.get("exact_work_count") ==
                fresh.get("expected_work_count") == fresh.get("exact_work_count") == 120,
                "equivalence replay", "exact30 work denominator differs")
    if scope == "guarded-stateful80":
        expected_state: Record = {"selected_event_pairs": 2538768, "max_tag_occupancy": 4,
                                  "max_row_occupancy": 3, "ready_violation_mask": 0,
                                  "full6_supported": False, "formal_proof": "NOT_RUN",
                                  "production_admitted": False}
        require(all(retained.get(key) == value and fresh.get(key) == value
                    for key, value in expected_state.items()),
                "equivalence replay", "stateful80 event or guarded-state scope differs")


def _check_native(native: Record, historical: Record) -> None:
    for name, digest in native.items():
        require(digest == NATIVE_DELTA.get(name, historical.get(name)),
                "equivalence source", f"unreviewed native change: {name}")


def reviewed_equivalence(context: EvidenceContext, manifest: Record,
                         expected_ids: tuple[str, ...]) -> CurrentEvidence:
    """Version-2 current input is explicit and usable only with the v3 domain delta."""
    require(context.domain_delta_input is not None and context.current_evidence_input is not None and
            set(manifest) == {"schema", "version", "historical_certificate", "equivalence_report"} and
            manifest.get("schema") == "im2p-stateful-current-evidence-input" and
            type(manifest.get("version")) is int and manifest["version"] == 2,
            "equivalence input", "explicit v3 historical equivalence input required")
    input_path = context.current_evidence_input
    if input_path is None:
        raise StatefulCertificateError("equivalence input", "current input missing")
    input_digest = sha256(input_path)
    require(read_document(input_path) == manifest, "equivalence input", "input changed before validation")
    ancestor_ref = object_value(manifest["historical_certificate"], "historical certificate")
    report_ref = object_value(manifest["equivalence_report"], "equivalence report")
    require(ancestor_ref.get("sha256") == ANCESTOR_SHA256 and report_ref.get("sha256") == REPORT_SHA256,
            "equivalence authority", "unreviewed or relabeled historical/replay artifact")
    ancestor, report = read_document(_artifact(ancestor_ref)), read_document(_artifact(report_ref))
    references = _closure({"ancestor": ancestor_ref, "report": report_ref})
    archive = object_value(report["current_archive"], "archive")
    shared = object_value(report["current_library"], "shared library")
    require(_artifact(archive) == context.library and _artifact(shared) == context.shared_library,
            "equivalence library", "caller library differs from reviewed replay")
    cases = tuple(object_value(row, "case") for row in array_value(ancestor["cases"], "cases"))
    require(ancestor.get("version") == 2 and [row.get("case_id") for row in cases] == list(expected_ids) and
            [row.get("profile") for row in cases] == list(PROFILES),
            "equivalence cases", "historical stateful profile/case identities differ")
    old_sources = object_value(ancestor["source_sha256"], "historical sources")
    native: Record = {}
    for profile in PROFILES:
        native.update(source_identity(context.library, profile).source_sha256)
    _check_native(native, old_sources)
    impact = object_value(report["source_impact"], "source impact")
    require(all(native.get(name) == old_sources.get(name) == digest for name, digest in
                object_value(impact["unchanged_exact_sha256"], "unchanged core").items()) and
            object_value(impact["additive_sequence_wrapper"], "wrapper")["current_sha256"] ==
                native["sim/cycle/sequence_c_api.cpp"],
            "equivalence source", "reviewed Engine/execute scope changed")
    sources: Record = {name: sha256(ROOT / name) for name in set(old_sources) | _python_closure()}
    sources.update(native)
    require(all(sources[name] == digest for name, digest in old_sources.items()
                if Path(name).suffix in (".scala", ".sv", ".v")),
            "equivalence source", "retained RTL source changed")
    scope_rows = object_value(report["scopes"], "scopes")
    require(set(scope_rows) == set(SCOPES), "equivalence scopes", "preserved scope set differs")
    harness_ref = object_value(report["harness"], "replay verifier")
    harness = _artifact(harness_ref)
    receipts: dict[str, Record] = {}
    for scope in SCOPES:
        receipt_ref = object_value(object_value(scope_rows[scope], scope)["receipt"], "receipt")
        retained = read_document(_artifact(receipt_ref))
        try:
            run = subprocess.run([sys.executable, "-B", str(harness), "--scope", scope],
                                 cwd=ROOT, capture_output=True, text=True, timeout=300, check=False)
        except subprocess.TimeoutExpired as error:
            raise StatefulCertificateError("equivalence replay", f"{scope} timed out") from error
        require(run.returncode == 0, "equivalence replay", f"{scope}: {run.stderr or run.stdout}")
        fresh = object_value(json.loads(run.stdout, object_pairs_hook=unique_pairs), "fresh replay")
        _check_replay(scope, retained, fresh)
        require(fresh.get("current_archive") == archive and fresh.get("engine_source_sha256") ==
                {name: native[name] for name in (*object_value(impact["unchanged_exact_sha256"], "core"),
                                                "sim/cycle/sequence_c_api.cpp")},
                "equivalence replay", f"{scope} current source/archive binding differs")
        receipts[scope] = retained
    historical_input = object_value(ancestor["evidence_input"], "historical input")
    require(receipts["guarded-stateful80"]["legacy_source"] == historical_input,
            "equivalence cases", "stateful replay used another historical input")
    equivalent: Record = {}
    for name, scope, path in (("base", "base240", context.base_parent),
                              ("run_aware", "run-aware42", context.run_aware_parent),
                              ("service", "exact30", context.service_parent)):
        old = object_value(object_value(ancestor["parents"], "parents")[name], name)
        original = object_value(receipts[scope]["legacy_source"], "parent source")
        require(_artifact(original) == path and original["sha256"] == old["sha256"],
                "equivalence parent", f"{name} historical certificate differs")
        equivalent[name] = {"historical_certificate": original,
            "current_library_sha256": shared["sha256"],
            "receipt": object_value(scope_rows[scope], scope)["receipt"]}
    # Preserve old cases and their old library bindings; only the top-level current identity is new.
    require(_closure({"ancestor": ancestor_ref, "report": report_ref}) == references and
            all(sha256(ROOT / name) == digest for name, digest in sources.items()),
            "equivalence closure", "source or artifact changed during replay")
    return CurrentEvidence(reference(input_path, input_digest), archive, shared, sources,
        references, cases, object_value(ancestor["completeness"], "completeness"),
        object_value(ancestor["state_refinement"], "state refinement"), equivalent)
