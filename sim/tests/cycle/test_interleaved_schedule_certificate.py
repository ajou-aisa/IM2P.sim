from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from sim.cycle import interleaved_schedule_certificate as certificate
from sim.cycle import stateful_sequence_certificate as certificates
from sim.cycle.certificate_contract import object_value, read_document
from sim.cycle.cycle_trace_certificate import context_from
from sim.cycle.execution_sequence_provider import DiagnosticStatefulProvider, StatefulProviderError
from sim.cycle.sequence_domain import DomainRevisionError
from sim.cycle.stateful_domain import INTERLEAVED_TRACE_REVISIONS, state_domain
from sim.cycle.stateful_sequence_evidence import EvidenceContext, StatefulCertificateError

REVISION = "GUARDED_A8D32_INTERLEAVED_ISSUE_TAG5_ROW_LT5_V1"


def context(root: Path) -> EvidenceContext:
    return EvidenceContext(*(root / name for name in ("e", "l", "s", "b", "r", "v")))


def test_interleaved_revision_is_separate_and_profile_bound() -> None:
    # Given: the interleaved revision certified for A8W8/DIM32 only.
    assert INTERLEAVED_TRACE_REVISIONS[REVISION][0] == "a8w8-d32-hp1"
    for profile in ("a8w8-d16-hp1", "a4w4-d32-hp1"):
        with pytest.raises(DomainRevisionError):
            state_domain(profile, REVISION)
    domain = state_domain("a8w8-d32-hp1", REVISION)
    assert (domain.allowed_tag_peak, domain.allowed_row_peak) == (5, 4)


def test_unpinned_case_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(StatefulCertificateError, match="no reviewed interleaved-schedule evidence pins"):
        certificate.expected(context(tmp_path), "interleaved-unreviewed")


def test_forged_document_is_rejected_through_dispatch(tmp_path: Path) -> None:
    path = tmp_path / "forged.json"
    path.write_text(json.dumps({"schema": certificate.SCHEMA, "case_id": "forged", "state_domain_revision": REVISION}))
    with pytest.raises(StatefulCertificateError):
        certificates.validate(path, context(tmp_path))


def test_issue_digest_binds_both_availability_and_ports() -> None:
    base = certificate.issue_digest([0, 5], [0, 9])
    assert base != certificate.issue_digest([0, 6], [0, 9])
    assert base != certificate.issue_digest([0, 5], [0, 10])


def provider(certificate_path: Path, trace: Path, library: Path) -> DiagnosticStatefulProvider:
    ctx = context_from(object_value(read_document(certificate_path)["evidence_context"], "context")) \
        if read_document(certificate_path)["schema"] == "im2p-cycle-trace-certificate-v1" else None
    if ctx is None:
        document = read_document(certificate_path)
        parents = object_value(document["parents"], "parents")
        ctx = EvidenceContext(Path(os.environ["IM2P_INTERLEAVED_EVIDENCE_ROOT"]),
                              *(Path(str(object_value(document[key], key)["path"])) for key in ("library", "shared_library")),
                              *(Path(str(object_value(parents[key], key)["path"])) for key in ("base", "run_aware", "service")))
    return DiagnosticStatefulProvider(library, trace, certificate_path, ctx)


def genuine(name: str) -> Path:
    value = os.environ.get(name)
    if value is None:
        pytest.skip(f"{name} not supplied")
    return Path(value)


def test_npu_only_certificate_still_rejects_cpu_gapped_offer() -> None:
    # Pinned reproduction of the three-source schedule boundary: the NPU-only certificate is not widened.
    cycle_trace, trace, library = (genuine(name) for name in (
        "IM2P_CYCLE_TRACE_CERTIFICATE", "IM2P_SMOKE_TRACE", "IM2P_CYCLE_LIBRARY"))
    with provider(cycle_trace, trace, library) as session:
        with pytest.raises(StatefulProviderError, match="only certified back-to-back NPU offers"):
            session.execute(session.requests[0], 1_819_084)


def test_interleaved_certificate_admits_only_its_issue_sequence() -> None:
    interleaved, trace, library = (genuine(name) for name in (
        "IM2P_INTERLEAVED_CERTIFICATE", "IM2P_SMOKE_TRACE", "IM2P_CYCLE_LIBRARY"))
    offers = certificate.certified_offers(interleaved)
    with provider(interleaved, trace, library) as session:
        with pytest.raises(StatefulProviderError, match="certified CPU/NPU issue sequence"):
            session.execute(session.requests[0], 0)
    with provider(interleaved, trace, library) as session:
        window = session.execute(session.requests[0], offers[0])
        assert window.offered_cycle == offers[0] and window.accepted_cycle == offers[0]
