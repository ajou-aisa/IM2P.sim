from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from sim.cycle import stateful_sequence_certificate as certificate
from sim.cycle import stateful_sequence_replay_certificate as replay
from sim.cycle.certificate_contract import read_document
from sim.cycle.npu_trace_schema import Record
from sim.cycle.stateful_sequence_evidence import (
    EvidenceContext,
    StatefulCertificateError,
)

pytest_plugins = ("sim.tests.cycle.test_stateful_sequence_tag6",)


@pytest.fixture(scope="module")
def verified(context: EvidenceContext) -> tuple[Record, Record, Record]:
    return replay._verified(context)


def test_actual_complete_three_certificate_chain_admits(context: EvidenceContext) -> None:
    # Given: the actual independently completed374 replay and all three certificates.
    path = context.evidence_root / "full374/stateful-full374-replay-v1.json"
    # When: the public production gate validates every link.
    certificate.admit(path, context)
    # Then: the resulting scope identifies the complete original trace, not prefix240 alone.
    assert certificate.validate(path, context).case_ids == ("actual-gpt2-full374",)


def test_domain_without_replay_remains_not_ready(context: EvidenceContext) -> None:
    # Given/When/Then: complete replay elsewhere never implicitly promotes a domain-only input.
    with pytest.raises(certificate.NotReadyError):
        certificate.admit(context.evidence_root / replay.PINS["domain"][0], context)


@pytest.mark.parametrize("missing", ["domain_certificate", "state_transition_certificate", "replay_proofs"])
def test_missing_link_cannot_admit(
        context: EvidenceContext, verified: tuple[Record, Record, Record],
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    # Given: cached genuine proof bytes, and a primary certificate with one link omitted.
    monkeypatch.setattr(replay, "_verified", lambda supplied: verified)
    original = read_document(context.evidence_root / "full374/stateful-full374-replay-v1.json")
    changed = deepcopy(original)
    changed.pop(missing)
    path = tmp_path / "incomplete.json"
    path.write_text(json.dumps(changed))
    # When/Then: even unchanged data at the other links cannot grant production admission.
    with pytest.raises(StatefulCertificateError):
        certificate.admit(path, context)


def test_mutated_state_certificate_is_not_a_status_label(
        context: EvidenceContext, verified: tuple[Record, Record, Record],
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a forged state certificate whose status is still PASS.
    monkeypatch.setattr(replay, "_verified", lambda supplied: verified)
    state = read_document(context.evidence_root / replay.STATE_PATH)
    state["transition"] = {"work_count": 374, "status": "PASS"}
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state))
    monkeypatch.setattr(replay, "STATE_PATH", str(path))
    # When/Then: exact374 state payload validation rejects the label-only replacement.
    with pytest.raises(StatefulCertificateError, match="state-transition certificate"):
        replay.expected(context)


def test_consumer_source_change_rejects_before_admission(
        context: EvidenceContext, verified: tuple[Record, Record, Record],
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the genuine certificate with a changed current consumer implementation.
    monkeypatch.setattr(replay, "_verified", lambda supplied: verified)
    changed = tmp_path / "consumer.py"
    changed.write_text("changed\n")
    monkeypatch.setattr(replay, "CONSUMER_SOURCES", (str(changed),))
    # When/Then: proof artifacts do not override consumer source drift.
    with pytest.raises(StatefulCertificateError, match="consumer source"):
        certificate.admit(context.evidence_root / "full374/stateful-full374-replay-v1.json", context)
