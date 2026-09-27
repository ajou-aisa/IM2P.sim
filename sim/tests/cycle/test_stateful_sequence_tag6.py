from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle import stateful_sequence_certificate as certificate
from sim.cycle import stateful_sequence_evidence_tag6 as tag6
from sim.cycle.certificate_contract import read_document
from sim.cycle.npu_trace_schema import Record
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.stateful_sequence_evidence import (
    EvidenceContext,
    StatefulCertificateError,
)
from sim.cycle.stateful_sequence_tag6_pins import PROOFS


@pytest.fixture(scope="module")
def context() -> EvidenceContext:
    root = os.environ.get("IM2P_TAG6_EVIDENCE_ROOT")
    if root is None:
        pytest.skip("reviewed Tag6 evidence was not supplied")
    value = read_document(Path(root) / "state-domain/context.json")
    return EvidenceContext(
        *(Path(str(value[key])) for key in
          ("evidence_root", "library", "shared_library", "base_parent", "run_aware_parent", "service_parent")),
        tag6_evidence_input=Path(str(value["tag6_evidence_input"])),
    )


@pytest.fixture(scope="module")
def reviewed(context: EvidenceContext) -> Record:
    return tag6.expected(context)


def test_real_reviewed_domain_stays_diagnostic(
        context: EvidenceContext, reviewed: Record, tmp_path: Path) -> None:
    # Given: the genuine hash-bound complete prefix240 proof and mutation corpus.
    path = tmp_path / "domain.json"
    path.write_text(json.dumps(reviewed))
    # When: the public gate validates the new certificate then attempts production admission.
    scoped = certificate.validate(path, context)
    with pytest.raises(certificate.NotReadyError):
        certificate.admit(path, context)
    # Then: Tag6 scope is available only for diagnostics until two more certificates exist.
    assert scoped.state_domain_revision == tag6.TAG6_REVISION
    assert scoped.production_admitted is False


@pytest.mark.parametrize(("field", "value"), [
    ("profile", "a4w4-d16-hp1"), ("tag_peak", 7), ("row_peak", 6), ("ready_mask", 1),
    ("proof_work_ids", [239]), ("trace_work_count", 240), ("production_admitted", True),
])
def test_certificate_claim_mutations_do_not_change_authority(
        context: EvidenceContext, reviewed: Record, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch, field: str, value: JsonValue) -> None:
    # Given: cache the already independently verified expected document, not candidate validation.
    original = deepcopy(reviewed)
    monkeypatch.setattr(tag6, "expected", lambda supplied: original if supplied == context else {})
    changed = deepcopy(reviewed)
    changed[field] = value
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(changed))
    # When/Then: a self-consistent JSON edit cannot widen or relabel the certificate.
    with pytest.raises(StatefulCertificateError):
        tag6.validate_document(path, context)


def test_changed_proof_cannot_be_rebased_with_a_new_digest(
        context: EvidenceContext, tmp_path: Path) -> None:
    # Given: an attacker supplies a plausible exact summary at the expected proof path.
    relative, digest = PROOFS["comparison"]
    forged = tmp_path / relative
    forged.parent.mkdir(parents=True)
    forged.write_text(json.dumps({"status": "EXACT", "work_ids": list(range(240))}))
    assert sha256(forged) != digest
    # When/Then: trust roots are reviewed bytes, not attacker-provided status or digest.
    with pytest.raises(StatefulCertificateError):
        tag6._proofs(replace(context, evidence_root=tmp_path))


def test_missing_raw_bytes_are_not_evidence(tmp_path: Path) -> None:
    # Given/When/Then: a missing raw file cannot be replaced by its declared hash.
    with pytest.raises(StatefulCertificateError):
        tag6._bound({"path": str(tmp_path / "missing.gz"), "sha256": "0" * 64})


@pytest.mark.parametrize("field", ["library", "shared_library"])
def test_stale_library_rejects_the_same_reviewed_scope(
        context: EvidenceContext, tmp_path: Path, field: str) -> None:
    # Given: all evidence remains real but the caller substitutes different native bytes.
    changed = tmp_path / "library"
    changed.write_bytes(b"different")
    # When/Then: reviewed domain cannot bind that replacement.
    with pytest.raises(StatefulCertificateError):
        tag6.expected(replace(context, **{field: changed}))


def test_current_native_source_cannot_drift_after_review(
        context: EvidenceContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the real evidence and a changed current native build definition.
    source = tmp_path / "sim/cycle/CMakeLists.txt"
    source.parent.mkdir(parents=True)
    source.write_text("changed\n")
    monkeypatch.setattr(tag6, "ROOT", tmp_path)
    # When/Then: fixed reviewed native source hashes reject silent reinterpretation.
    with pytest.raises(StatefulCertificateError, match="Tag6 native source"):
        tag6.expected(context)
