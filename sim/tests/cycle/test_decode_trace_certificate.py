from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from scripts.gemmini_replay_contract import canonical_json
from sim.cycle import decode_trace_certificate as certificate
from sim.cycle import stateful_sequence_certificate as stateful
from sim.cycle.sequence_binding import Status
from sim.cycle.stateful_domain import ACTUAL_TRACE_REVISIONS, INTERLEAVED_TRACE_REVISIONS, PROFILE_EXTENSION_REVISIONS
from sim.cycle.stateful_sequence_evidence import EvidenceContext, StatefulCertificateError
from sim.tests.cycle.decode_trace_replay import parity_line


def test_decode_schema_and_revision_are_separate() -> None:
    assert certificate.REVISION not in {*ACTUAL_TRACE_REVISIONS, *INTERLEAVED_TRACE_REVISIONS,
                                        *PROFILE_EXTENSION_REVISIONS}
    assert certificate.SCHEMA not in {"im2p-actual-trace-certificate-v1", "im2p-cycle-trace-certificate-v1",
                                      "im2p-interleaved-schedule-certificate-v1"}


def test_stateful_provider_dispatch_does_not_admit_decode_documents(tmp_path: Path) -> None:
    path = tmp_path / "decode.json"
    path.write_text(json.dumps({"schema": certificate.SCHEMA, "case_id": "decode"}))
    with pytest.raises(StatefulCertificateError):
        stateful.validate(path, EvidenceContext(*(tmp_path / name for name in "elsbrv")))


def test_unpinned_case_is_rejected(tmp_path: Path) -> None:
    context = certificate.DecodeContext(*(tmp_path / name for name in "elsmdr"))
    with pytest.raises(StatefulCertificateError, match="no reviewed decode-trace evidence pins"):
        certificate.expected(context, "decode-unreviewed")


def test_digest_is_canonical_json_sha256() -> None:
    document = {"b": [1, 2], "a": "x"}
    assert certificate.digest(document) == hashlib.sha256(canonical_json(document).encode()).hexdigest()


def status_hex(stop_reason: int, cursor: int) -> str:
    status = Status()
    status.stop_reason, status.cursor = stop_reason, cursor
    return bytes(status).hex()


def test_stepping_parity_masks_only_the_stop_reason() -> None:
    def line(stop: int, cursor: int) -> str:
        return json.dumps({"before": {"status": status_hex(stop, cursor)}, "after": {"status": status_hex(stop, cursor)}})

    assert parity_line(line(8, 100)) == parity_line(line(1, 100))
    assert parity_line(line(8, 100)) != parity_line(line(8, 101))


def test_genuine_certificate_validates_and_rejects_a_claimed_clock(tmp_path: Path) -> None:
    names = ("IM2P_DECODE_CERTIFICATE", "IM2P_DECODE_EVIDENCE_ROOT", "IM2P_DECODE_LIBRARY", "IM2P_CYCLE_LIBRARY",
             "IM2P_DECODE_MODEL", "IM2P_DECODE_DATASET", "IM2P_DECODE_RTL_BUILD")
    values = [os.environ.get(name) for name in names]
    if any(value is None for value in values):
        pytest.skip("genuine decode certificate evidence not supplied")
    path, *rest = (Path(str(value)) for value in values)
    context = certificate.DecodeContext(*rest)
    document = certificate.validate(path, context)
    assert document["certification_basis"] == certificate.BASIS
    forged = json.loads(path.read_text())
    forged["hardware"]["clock"]["operating_frequency_hz"] = 1_000_000_000
    (tmp_path / "clock.json").write_text(json.dumps(forged))
    with pytest.raises(StatefulCertificateError, match="binding differs"):
        certificate.validate(tmp_path / "clock.json", context)
