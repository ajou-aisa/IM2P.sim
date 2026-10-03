from __future__ import annotations

import gzip
import json
from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path

import pytest

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle import stateful_sequence_certificate as certificate
from sim.cycle import stateful_sequence_evidence_tag5 as delta
from sim.cycle.certificate_contract import PROFILES
from sim.cycle.npu_trace_schema import Record
from sim.cycle.sequence_domain import (
    LEGACY_REVISION,
    TAG5_PROFILE,
    TAG5_REVISION,
    DomainRevisionError,
    profile_domain,
)
from sim.cycle.stateful_sequence_evidence import (
    EvidenceContext,
    StatefulCertificateError,
)


@pytest.mark.parametrize("profile", PROFILES)
def test_tag5_revision_expands_only_a8d16(profile: str) -> None:
    # Given/When: selecting either supported revision for every supported profile.
    old, new = profile_domain(profile), profile_domain(profile, TAG5_REVISION)
    # Then: the old contract remains tag4; only the new A8D16 limit changes.
    assert old.max_tag_occupancy == 4
    assert new.max_tag_occupancy == (5 if profile == TAG5_PROFILE else 4)
    assert (new.max_row_occupancy_exclusive, new.ready_violation_mask, new.tag_capacity) == (6, 0, 6)


@pytest.mark.parametrize("field_name", [
    "max_tag_occupancy", "max_row_occupancy_exclusive", "ready_violation_mask", "tag_capacity",
])
def test_shared_domain_is_immutable(field_name: str) -> None:
    # Given/When/Then: callers cannot widen the shared object in place.
    with pytest.raises(FrozenInstanceError):
        setattr(profile_domain(TAG5_PROFILE), field_name, 6)


@pytest.mark.parametrize(("profile", "revision"), [
    (TAG5_PROFILE, "READY"), (TAG5_PROFILE, "TAG6"), ("a8w8-d128-hp1", TAG5_REVISION),
])
def test_unknown_profile_or_report_label_is_not_authority(profile: str, revision: str) -> None:
    # Given/When/Then: labels and unsupported profiles cannot select expanded limits.
    with pytest.raises(DomainRevisionError):
        profile_domain(profile, revision)


def _context(tmp_path: Path) -> EvidenceContext:
    return EvidenceContext(tmp_path, tmp_path / "archive", tmp_path / "shared",
        tmp_path / "base", tmp_path / "run", tmp_path / "service",
        tmp_path / "current.json", tmp_path / "delta.json")


def _write(path: Path, document: Record) -> Record:
    path.write_text(json.dumps(document))
    return {"path": str(path), "sha256": certificate.sha256(path)}


def _input(tmp_path: Path) -> tuple[EvidenceContext, Record, Record]:
    current: Record = {
        "schema": certificate.SCHEMA, "version": 2, "artifact_role": certificate.ROLE,
        "validation_scope": certificate.SCOPE, "production_admitted": False,
        "work_domain_revision": "PRODUCER_TWO_PARENT_ABI2_V2",
        "state_domain_revision": LEGACY_REVISION,
        "completeness": {"works_expected": 80, "works_observed": 80},
        "reference_memory": {"timing": dict(certificate.TIMING)},
        "source_sha256": {"sim/cycle/control_engine.cpp": "a" * 64},
        "library": {"path": str(tmp_path / "archive"), "sha256": "b" * 64},
        "shared_library": {"path": str(tmp_path / "shared"), "sha256": "c" * 64},
        "cases": [{"case_id": name} for name in certificate.EXPECTED_IDS],
    }
    prior = _write(tmp_path / "prior.json", current)
    impact: Record = {"schema": "im2p-stateful-tag5-source-impact-v1",
        "prior_certificate_sha256": prior["sha256"],
        **{key: current[key] for key in ("source_sha256", "library", "shared_library")}}
    impact_ref = _write(tmp_path / "impact.json", impact)
    cases: list[JsonValue] = [{"case_id": name} for name, _ in delta.CASES]
    review: Record = {"schema": "im2p-stateful-tag5-review-v1", "verdict": "confirmed",
        "source_impact_sha256": impact_ref["sha256"], "state_domain_revision": TAG5_REVISION,
        "cases": cases, "domain": asdict(profile_domain(TAG5_PROFILE, TAG5_REVISION))}
    manifest: Record = {"schema": delta.INPUT_SCHEMA, "version": 1,
        "state_domain_revision": TAG5_REVISION, "prior_certificate": prior,
        "source_impact": impact_ref, "state_review": _write(tmp_path / "review.json", review),
        "cases": cases}
    context = _context(tmp_path)
    _write(tmp_path / "delta.json", manifest)
    return context, current, manifest


@pytest.mark.parametrize("mutation", ["guard4", "tag6", "shrunk", "stale_source", "stale_library"])
def test_delta_rejects_relabeled_or_stale_authority(tmp_path: Path, mutation: str) -> None:
    # Given: hash-consistent small review files at the input boundary, not actual evidence.
    context, current, manifest = _input(tmp_path)
    if mutation == "guard4":
        manifest["state_domain_revision"] = LEGACY_REVISION
    elif mutation == "tag6":
        review = certificate.read_document(tmp_path / "review.json")
        review["domain"] = {**asdict(profile_domain(TAG5_PROFILE, TAG5_REVISION)),
                            "max_tag_occupancy": 6}
        manifest["state_review"] = _write(tmp_path / "review.json", review)
    elif mutation == "shrunk":
        manifest["cases"] = []
        review = certificate.read_document(tmp_path / "review.json")
        review["cases"] = []
        manifest["state_review"] = _write(tmp_path / "review.json", review)
    elif mutation == "stale_source":
        current["source_sha256"] = {"sim/cycle/control_engine.cpp": "f" * 64}
    else:
        current["library"] = {"path": str(tmp_path / "archive"), "sha256": "f" * 64}
    _write(tmp_path / "delta.json", manifest)
    # When/Then: none can cross into raw evidence validation or admit a revision.
    with pytest.raises(StatefulCertificateError):
        delta.reviewed_delta(context, current)


def test_confirmed_review_without_raw_holdouts_is_rejected(tmp_path: Path) -> None:
    # Given: confirmed, hash-consistent review claims with no raw evidence.
    context, current, _ = _input(tmp_path)
    # When/Then: report labels alone cannot certify tag5.
    with pytest.raises(StatefulCertificateError):
        delta.reviewed_delta(context, current)


@pytest.mark.parametrize(("tags", "rows", "resets", "accepted"), [
    (5, 4, 1, True), (6, 4, 1, False), (5, 6, 1, False), (5, 4, 2, False),
])
def test_raw_domain_checks_queue_edges_not_summary_claims(
    tmp_path: Path, tags: int, rows: int, resets: int, accepted: bool,
) -> None:
    # Given: a complete raw scanner input with one concrete old/new queue edge.
    stimulus: Record = {"stimulus_sha256": "a" * 64, "works": [{}]}
    run = {"instance_count": 1, "reset_count": resets, "period": 5,
           "stimulus_sha256": "a" * 64, "work_count": 1, "offer_mode": "availability-driven"}
    edge = {"old": {"tag_count": 4, "row_count": 3},
            "next": {"tag_count": tags, "row_count": rows}}
    lines = [b"MODEL_RUN " + json.dumps(run).encode() + b"\n",
             b"MODEL_QUEUE_EDGE_V2 " + json.dumps(edge).encode() + b"\n"]
    path = tmp_path / "model.log.gz"
    with gzip.open(path, "wb") as stream:
        stream.write(b"".join(lines))
    # When/Then: every concrete state must lie inside the reviewed domain.
    if accepted:
        assert list(delta._scan(path, stimulus, [])) == lines
    else:
        with pytest.raises(StatefulCertificateError):
            list(delta._scan(path, stimulus, []))


@pytest.mark.parametrize("version", [1, 2])
def test_old_certificate_cannot_be_reinterpreted_as_tag5(tmp_path: Path, version: int) -> None:
    # Given: an old certificate passed with the new context switch.
    path = tmp_path / "certificate.json"
    _write(path, {"schema": certificate.SCHEMA, "version": version,
                  "artifact_role": certificate.ROLE, "validation_scope": certificate.SCOPE,
                  "production_admitted": False})
    # When/Then: dispatch rejects it before source/evidence evaluation.
    with pytest.raises(StatefulCertificateError):
        certificate.validate(path, _context(tmp_path))


def test_missing_current_evidence_never_publishes_delta(tmp_path: Path) -> None:
    # Given: delta requested without the inherited current-source authority.
    context = replace(_context(tmp_path), current_evidence_input=None)
    output = tmp_path / "certificate.json"
    # When/Then: no certificate or new domain is returned.
    with pytest.raises(StatefulCertificateError) as error:
        certificate.build(output, context)
    assert error.value.boundary == "NOT_READY"
    assert not output.exists()


def test_stale_current_evidence_stays_not_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: reviewed_current detects source drift; the delta cannot override that decision.
    context, _, _ = _input(tmp_path)

    def stale(_context: EvidenceContext, _ids: tuple[str, ...]) -> None:
        raise StatefulCertificateError("source closure", "changed native wrapper")

    monkeypatch.setattr(certificate.current_evidence, "reviewed_current", stale)
    # When/Then: the additive path preserves the failure and publishes nothing.
    with pytest.raises(certificate.NotReadyError):
        certificate.build(tmp_path / "output.json", context)
    assert not (tmp_path / "output.json").exists()


@pytest.mark.parametrize("model_peak", [4, 5])
def test_per_work_peak_is_not_rewritten_to_generation_maximum(model_peak: int) -> None:
    # Given: work four peaks at four after work three already reached five.
    endpoints: Record = {key: 1 for key in delta.RAW_FIELDS}
    endpoints["work_binding"] = "a" * 64
    rtl: list[Record] = [{**endpoints, "mesh_tag_max_occupancy": 5},
                         {**endpoints, "mesh_tag_max_occupancy": 4}]
    model: list[Record] = [{**endpoints, "mesh_tag_max_occupancy": 5},
                           {**endpoints, "mesh_tag_max_occupancy": model_peak}]
    # When/Then: normalize only the comparison envelope, never the raw evidence.
    if model_peak == 4:
        delta._endpoints(rtl, model)
        assert rtl[1]["mesh_tag_max_occupancy"] == 4
    else:
        with pytest.raises(StatefulCertificateError):
            delta._endpoints(rtl, model)
