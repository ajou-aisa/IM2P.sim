from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from sim.cycle import stateful_sequence_certificate as certificate
from sim.cycle import stateful_sequence_evidence_equivalence as equivalence
from sim.cycle.npu_trace_schema import Record
from sim.cycle.stateful_sequence_evidence import (
    EvidenceContext,
    StatefulCertificateError,
)


def _context(root: Path) -> EvidenceContext:
    return EvidenceContext(root, root / "archive", root / "shared", root / "base",
                           root / "run", root / "service", root / "input", root / "delta")


def _receipt(scope: str) -> Record:
    count = equivalence.SCOPES[scope]
    result: Record = {
        "schema": "im2p-current-model-reused-rtl-validation-v1", "scope": scope,
        "classification": "FRESH_MODEL_REUSED_RTL", "status": "PASS",
        "expected": count, "attempted": count, "exact": count, "mismatch_count": 0,
        "first_mismatch": None, "current_library": {"sha256": "a" * 64},
        "legacy_source": {"sha256": "b" * 64}, "retained_artifact_count": 18,
        "retained_artifacts_sha256": "c" * 64,
    }
    if scope == "exact30":
        result.update(expected_work_count=120, exact_work_count=120)
    if scope == "guarded-stateful80":
        result.update(selected_event_pairs=2538768, max_tag_occupancy=4, max_row_occupancy=3,
                      ready_violation_mask=0, full6_supported=False, formal_proof="NOT_RUN",
                      production_admitted=False)
    return result


@pytest.mark.parametrize("scope", tuple(equivalence.SCOPES))
def test_replayed_scope_must_match_retained_receipt(scope: str) -> None:
    # Given: matching executable replay results at the transport boundary.
    retained = _receipt(scope)
    # When/Then: every required scope accepts its original full denominator.
    equivalence._check_replay(scope, retained, deepcopy(retained))


@pytest.mark.parametrize("field,value", [
    ("exact", 239), ("scope", "base239"), ("classification", "REUSED_REPORT"),
    ("current_library", {"sha256": "d" * 64}),
    ("retained_artifacts_sha256", "e" * 64), ("legacy_source", {"sha256": "f" * 64}),
])
def test_replay_rejects_shrink_relabel_and_changed_bindings(field: str, value: str | int | Record) -> None:
    # Given: fresh output differs from the reviewed receipt at one boundary.
    retained = _receipt("base240")
    fresh = {**retained, field: value}
    # When/Then: a PASS string cannot override that difference.
    with pytest.raises(StatefulCertificateError):
        equivalence._check_replay("base240", retained, fresh)


@pytest.mark.parametrize("field,value", [
    ("selected_event_pairs", 2538767), ("max_tag_occupancy", 5),
    ("max_row_occupancy", 6), ("ready_violation_mask", 1), ("full6_supported", True),
])
def test_equivalence_does_not_relabel_guard4_replays(field: str, value: int | bool) -> None:
    # Given: both summaries agree on a value outside the original reviewed scope.
    changed = {**_receipt("guarded-stateful80"), field: value}
    # When/Then: equality alone cannot expand the inherited state domain.
    with pytest.raises(StatefulCertificateError):
        equivalence._check_replay("guarded-stateful80", changed, deepcopy(changed))


def test_exact30_requires_all_original_120_works() -> None:
    # Given: all 30 cases remain but one work has disappeared.
    changed = {**_receipt("exact30"), "exact_work_count": 119}
    # When/Then: matching shrunk receipts fail the original work denominator.
    with pytest.raises(StatefulCertificateError):
        equivalence._check_replay("exact30", changed, deepcopy(changed))


def test_native_source_changes_are_limited_to_reviewed_additions() -> None:
    # Given: unchanged core plus the exact reviewed interface additions.
    historical: Record = {"sim/cycle/control_engine.cpp": "a" * 64}
    native = {**historical, **equivalence.NATIVE_DELTA}
    # When/Then: wrapper additions do not authorize changing Engine.
    equivalence._check_native(native, historical)
    with pytest.raises(StatefulCertificateError):
        equivalence._check_native({**native, "sim/cycle/control_engine.cpp": "b" * 64}, historical)


def test_recursive_artifact_check_handles_arrays_and_rejects_changed_leaf(tmp_path: Path) -> None:
    # Given: JSON command arrays alongside a recursively referenced raw artifact.
    leaf = tmp_path / "raw.log"
    leaf.write_bytes(b"retained raw\n")
    document = tmp_path / "command.json"
    document.write_text(json.dumps([{"path": str(leaf), "sha256": certificate.sha256(leaf)}]))
    reference: Record = {"path": str(document), "sha256": certificate.sha256(document)}
    assert len(equivalence._closure({"root": reference})) == 2
    leaf.write_bytes(b"changed raw\n")
    # When/Then: unchanged parent JSON cannot hide changed retained evidence.
    with pytest.raises(StatefulCertificateError):
        equivalence._closure({"root": reference})


def test_equivalence_input_cannot_activate_without_v3_delta(tmp_path: Path) -> None:
    # Given: the additive input presented to a legacy context.
    context = replace(_context(tmp_path), domain_delta_input=None)
    manifest: Record = {"schema": "im2p-stateful-current-evidence-input", "version": 2,
                        "historical_certificate": {}, "equivalence_report": {}}
    # When/Then: legacy v2 does not gain this interpretation.
    with pytest.raises(StatefulCertificateError):
        equivalence.reviewed_equivalence(context, manifest, certificate.EXPECTED_IDS)


def test_parent_equivalence_keeps_historical_library_hash(tmp_path: Path) -> None:
    # Given: unchanged old parent documents with separately verified current equivalence.
    context = _context(tmp_path)
    verified: Record = {}
    for name, path in (("base", context.base_parent), ("run_aware", context.run_aware_parent),
                       ("service", context.service_parent)):
        key = "model_library_sha256" if name == "base" else "library_sha256"
        path.write_text(json.dumps({key: "a" * 64}))
        verified[name] = {"historical_certificate": certificate.reference(path, certificate.sha256(path)),
                          "current_library_sha256": "b" * 64}
    # When: parent admission attaches equivalence without rewriting old bytes.
    parents = certificate.parents(context, "b" * 64, verified)
    # Then: historical and current identities remain distinct.
    for value in parents.values():
        parent = certificate.object_value(value, "parent")
        assert parent["library_sha256"] == "a" * 64
        assert parent["status"] == "CURRENT_EQUIVALENT"


@pytest.fixture
def cached_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch
                     ) -> tuple[EvidenceContext, list[int]]:
    # The proof assembler is the only fake; the actual byte rechecks remain live.
    context = _context(tmp_path)
    for name in ("source.py", "input", "archive", "shared", "delta", "raw"):
        (tmp_path / name).write_text(name)
    refs = {name: certificate.reference(tmp_path / name, certificate.sha256(tmp_path / name))
            for name in ("input", "archive", "shared", "delta", "raw")}
    document: Record = {"source_sha256": {"source.py": certificate.sha256(tmp_path / "source.py")},
        "evidence_input": refs["input"], "library": refs["archive"], "shared_library": refs["shared"],
        "reviewed_evidence": {}, "parents": {}, "cases": [],
        "domain_delta": {"artifacts": {"input": refs["delta"], "raw": refs["raw"]}}}
    calls: list[int] = []

    def assemble(_context: EvidenceContext) -> Record:
        calls.append(1)
        return deepcopy(document)

    monkeypatch.setattr(certificate, "ROOT", tmp_path)
    monkeypatch.setattr(certificate, "_current_cache", None)
    monkeypatch.setattr(certificate, "_expected_current", assemble)
    return context, calls


def test_successful_cache_rechecks_bytes_and_returns_independent_copy(
    cached_candidate: tuple[EvidenceContext, list[int]],
) -> None:
    # Given: one successful fresh validation followed by caller mutation of its result.
    context, calls = cached_candidate
    first = certificate.expected_current(context)
    certificate.object_value(first["domain_delta"], "delta")["artifacts"] = {}
    # When: the same immutable evidence is requested again.
    second = certificate.expected_current(context)
    # Then: expensive proof assembly ran once; caller mutation did not poison the cache.
    assert calls == [1]
    assert certificate.object_value(second["domain_delta"], "delta")["artifacts"] != {}


@pytest.mark.parametrize("changed", ["source.py", "input", "archive", "shared", "delta", "raw"])
def test_cached_validation_rejects_any_changed_binding(
    cached_candidate: tuple[EvidenceContext, list[int]], changed: str,
) -> None:
    # Given: a valid cached proof, then drift in a bound source, input or artifact.
    context, calls = cached_candidate
    certificate.expected_current(context)
    (context.evidence_root / changed).write_text("changed")
    # When/Then: reuse fails closed, evicts the cache, and does not silently reapprove.
    with pytest.raises(StatefulCertificateError):
        certificate.expected_current(context)
    assert certificate._current_cache is None
    assert calls == [1]


def test_real_legacy_equivalence_preserves_history(tmp_path: Path) -> None:
    # Given: the independently reviewed, immutable milestone artifacts.
    builds = Path(__file__).resolve().parents[6] / "build/im2p-gemmini"
    milestone = builds / "sequence-tag5-milestone-20260927T111614Z"
    historical = builds / "stateful-sequence-v2-20260924T033303Z"
    ancestor_path = historical / "resume-20260927T024214Z/stateful-certificate.json"
    report_path = milestone / "current-validation/legacy/report.json"
    ancestor = certificate.read_document(ancestor_path)
    report = certificate.read_document(report_path)
    manifest: Record = {"schema": "im2p-stateful-current-evidence-input", "version": 2,
        "historical_certificate": {"path": str(ancestor_path), "sha256": equivalence.ANCESTOR_SHA256},
        "equivalence_report": {"path": str(report_path), "sha256": equivalence.REPORT_SHA256}}
    current_input = tmp_path / "input.json"
    current_input.write_text(json.dumps(manifest))
    bound = certificate.object_value(ancestor["parents"], "parents")
    context = EvidenceContext(evidence_root=historical,
        library=Path(str(certificate.object_value(report["current_archive"], "archive")["path"])),
        shared_library=Path(str(certificate.object_value(report["current_library"], "shared")["path"])),
        base_parent=Path(str(certificate.object_value(bound["base"], "base")["path"])),
        run_aware_parent=Path(str(certificate.object_value(bound["run_aware"], "run")["path"])),
        service_parent=Path(str(certificate.object_value(bound["service"], "service")["path"])),
        current_evidence_input=current_input, domain_delta_input=tmp_path / "domain-delta.json")
    # When: the official current-evidence boundary reruns every model-only scope.
    current = certificate.current_evidence.reviewed_current(context, certificate.EXPECTED_IDS)
    # Then: all historical cases survive unchanged with separate current equivalence.
    assert list(current.cases) == ancestor["cases"]
    assert current.completeness == ancestor["completeness"]
    assert current.library == report["current_archive"]
    assert current.shared_library == report["current_library"]
    assert current.parent_equivalence is not None
    assert set(current.parent_equivalence) == {"base", "run_aware", "service"}
    assert certificate.sha256(ancestor_path) == equivalence.ANCESTOR_SHA256
