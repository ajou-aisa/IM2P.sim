from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from sim.cycle import actual_trace_certificate as certificate
from sim.cycle import stateful_sequence_certificate as certificates
from sim.cycle.certificate_contract import object_value, read_document
from sim.cycle.npu_trace_schema import Record
from sim.cycle.sequence_domain import DomainRevisionError
from sim.cycle.stateful_domain import ACTUAL_TRACE_REVISIONS, state_domain
from sim.cycle.stateful_sequence_evidence import (
    EvidenceContext,
    StatefulCertificateError,
)

REVISION = "GUARDED_A8D32_ACTUAL_EVAL_TAG5_ROW_LT5_V1"


def context(root: Path, **selectors: Path) -> EvidenceContext:
    return EvidenceContext(*(root / name for name in ("e", "l", "s", "b", "r", "v")), **selectors)


def test_actual_revision_is_bound_to_one_profile() -> None:
    # Given: the actual-trace revision certified for A8W8/DIM32 only.
    # When/Then: it cannot describe any other hardware profile.
    for profile in ("a8w8-d16-hp1", "a8w8-d64-hp1", "a4w4-d32-hp1"):
        with pytest.raises(DomainRevisionError):
            state_domain(profile, REVISION)
    domain = state_domain("a8w8-d32-hp1", REVISION)
    assert (domain.allowed_tag_peak, domain.allowed_row_peak) == (5, 4)
    assert ACTUAL_TRACE_REVISIONS[REVISION][0] == "a8w8-d32-hp1"


def test_unpinned_case_is_rejected(tmp_path: Path) -> None:
    # Given: a case without reviewed evidence pins.
    # When/Then: no certificate can be expected for it.
    with pytest.raises(StatefulCertificateError, match="no reviewed actual-trace evidence pins"):
        certificate.expected(context(tmp_path), "actual-unreviewed")


def test_forged_document_without_pins_is_rejected(tmp_path: Path) -> None:
    # Given: a self-consistent looking document for an unreviewed case.
    path = tmp_path / "forged.json"
    path.write_text(json.dumps({"schema": certificate.SCHEMA, "case_id": "forged", "state_domain_revision": REVISION}))
    # When/Then: the admission dispatcher rejects it through the reviewed-pin check.
    with pytest.raises(StatefulCertificateError):
        certificates.validate(path, context(tmp_path))


@pytest.fixture(scope="module")
def genuine() -> tuple[Path, EvidenceContext, Record]:
    path = os.environ.get("IM2P_ACTUAL_TRACE_CERTIFICATE")
    if path is None:
        pytest.skip("genuine actual-trace certificate not supplied")
    document = read_document(Path(path))
    parents = object_value(document["parents"], "parents")
    evidence = object_value(object_value(document["evidence"], "evidence")["stimulus"], "stimulus")
    supplied = EvidenceContext(
        Path(str(evidence["path"])).parent, Path(str(object_value(document["library"], "library")["path"])),
        Path(str(object_value(document["shared_library"], "shared")["path"])),
        *(Path(str(object_value(parents[key], key)["path"])) for key in ("base", "run_aware", "service")))
    return Path(path), supplied, certificate.validate_document(Path(path), supplied)


def test_genuine_certificate_admits_through_existing_dispatch(genuine: tuple[Path, EvidenceContext, Record]) -> None:
    # Given: the certificate built from an actual inference trace and its evidence.
    path, supplied, document = genuine
    # When: the unchanged admission entry points read it.
    scoped = certificates.validate(path, supplied)
    certificates.admit(path, supplied)
    # Then: its own finite domain, not a synthetic producer domain, is selected.
    assert scoped.state_domain_revision == REVISION == document["state_domain_revision"]
    assert document["synthetic_corpus"] is False and document["native_only_work_ids"] == [372, 373]


def test_genuine_states_outside_narrower_domain_are_rejected(genuine: tuple[Path, EvidenceContext, Record]) -> None:
    # Given: the complete native state records of the certified trace.
    _, _, document = genuine
    records = Path(str(object_value(object_value(document["transition"], "transition")["records"], "r")["path"]))
    # When/Then: the synthetic tag4/row3 domain cannot contain the actual trace.
    with pytest.raises(StatefulCertificateError, match="outside the certified range"):
        certificate.inspect(records, list(range(374)), 4, 4)


@pytest.mark.parametrize("field", ["trace", "work_ids", "state_domain_revision", "state_domain", "evidence",
                                   "observed_tag_peak", "final_cursor", "source_sha256", "probe", "synthetic_corpus"])
def test_mutated_genuine_certificate_is_rejected(genuine: tuple[Path, EvidenceContext, Record], tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    # Given: the recomputed genuine document; the candidate stays untrusted.
    _, supplied, document = genuine
    monkeypatch.setattr(certificate, "expected", lambda context, case: deepcopy(document))
    candidate = deepcopy(document)
    candidate[field] = "changed"
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate))
    # When/Then: any relabelled binding is rejected.
    with pytest.raises(StatefulCertificateError):
        certificate.validate_document(path, supplied)


def test_historical_selectors_cannot_grant_actual_authority(tmp_path: Path) -> None:
    # Given: a Tag6 selector supplied alongside an actual-trace case.
    # When/Then: historical evidence selectors never open an actual-trace domain.
    if not certificate.ACTUAL_TRACE_PINS:
        pytest.skip("no reviewed actual-trace pins in this source revision")
    case = next(iter(certificate.ACTUAL_TRACE_PINS))
    with pytest.raises(StatefulCertificateError, match="historical selectors"):
        certificate.expected(context(tmp_path, tag6_evidence_input=tmp_path / "t"), case)
