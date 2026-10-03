from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from sim.cycle import stateful_profile_certificate as profile_certificate
from sim.cycle import stateful_sequence_certificate as certificates
from sim.cycle.certificate_contract import read_document
from sim.cycle.execution_sequence_admission import (
    AdmissionInputs,
    StatefulProviderError,
)
from sim.cycle.execution_sequence_provider import StatefulSequenceProvider
from sim.cycle.npu_trace_schema import Record
from sim.cycle.stateful_domain import InitialState, StateDomainError, WorkClass
from sim.cycle.stateful_domain_admission import admit
from sim.cycle.stateful_measurement import prepare_measurement
from sim.cycle.stateful_sequence_evidence import (
    EvidenceContext,
    StatefulCertificateError,
)


@pytest.fixture(scope="module")
def inputs() -> AdmissionInputs:
    root = os.environ.get("IM2P_PROVIDER_EXPANSION_EVIDENCE_ROOT")
    if root is None:
        pytest.skip("genuine profile-expansion evidence was not supplied")
    evidence = Path(root)
    value = read_document(evidence / "provider/a8d32-context.json")
    context = EvidenceContext(*(Path(str(value[key])) for key in (
        "evidence_root", "library", "shared_library", "base_parent", "run_aware_parent", "service_parent",
    )))
    certificate = Path(os.environ.get(
        "IM2P_PROFILE_CERTIFICATE", str(evidence / "profiles/a8w8-d32-hp1/certificate-r1.json")))
    document = read_document(certificate)
    trace = certificates.object_value(document["trace"], "trace")
    return AdmissionInputs(context.shared_library, Path(str(trace["path"])), certificate, context)


@pytest.fixture(scope="module")
def genuine(inputs: AdmissionInputs) -> Record:
    return profile_certificate.validate_document(inputs.certificate, inputs.context)


def test_genuine_profile_domain_admits_and_prepares_only_model_evidence(inputs: AdmissionInputs) -> None:
    # Given: the genuine independently compared DIM32 producer corpus.
    work = WorkClass("dense_main", "dense")
    # When: its model evidence is prepared for a reconstruction consumer.
    measurement = prepare_measurement(inputs, "a8w8-d32-hp1", work)
    # Then: trace/certificate identity is bound, but real clock and host evidence remain required.
    assert measurement.provider_certificate.sha256 == certificates.sha256(inputs.certificate)
    assert measurement.trace_identity.sha256 == certificates.sha256(inputs.trace)
    assert measurement.profile == measurement.clock_requirement.profile == "a8w8-d32-hp1"
    assert measurement.e2e_reconstruction_ready == "NOT_READY"
    assert measurement.host_requirement.status == "NOT_PROVIDED"


@pytest.mark.parametrize("profile", ["a8w8-d16-hp1", "a8w8-d64-hp1", "a4w4-d32-hp1"])
def test_certificate_does_not_authorize_another_profile(inputs: AdmissionInputs, profile: str) -> None:
    # Given/When/Then: known profiles still cannot borrow this finite certificate.
    with pytest.raises(StatefulProviderError):
        admit(profile, WorkClass("dense_main", "dense"), InitialState(), inputs)


@pytest.mark.parametrize("field", [
    "generation", "absolute_cycle", "scratchpad_half", "accumulator_half",
    "tag_count", "row_count", "ready_mask",
])
def test_genuine_certificate_rejects_changed_initial_state(inputs: AdmissionInputs, field: str) -> None:
    # Given: a real proof, but a different caller-supplied native origin.
    state = replace(InitialState(), **{field: 2})
    # When/Then: source/proof validity cannot override state mismatch.
    with pytest.raises(StateDomainError):
        admit("a8w8-d32-hp1", WorkClass("dense_main", "dense"), state, inputs)


@pytest.mark.parametrize("work", [
    WorkClass("raw", "dense"), WorkClass("residual", "legacy-block-local"),
    WorkClass("dense_main", "cross-block-run-aware-v1"),
])
def test_genuine_certificate_rejects_unsupported_work_class(inputs: AdmissionInputs, work: WorkClass) -> None:
    # Given/When/Then: genuine evidence does not authorize absent work/run classes.
    with pytest.raises(StateDomainError):
        admit("a8w8-d32-hp1", work, InitialState(), inputs)


def test_changed_trace_is_not_admitted(inputs: AdmissionInputs, tmp_path: Path) -> None:
    # Given: valid JSON from the same producer, but different immutable trace bytes.
    trace = tmp_path / "trace.jsonl"
    trace.write_bytes(inputs.trace.read_bytes() + b"\n")
    # When/Then: matching profile and certificate alone do not authorize the replacement.
    with pytest.raises(StatefulProviderError):
        admit("a8w8-d32-hp1", WorkClass("dense_main", "dense"), InitialState(),
              replace(inputs, trace=trace))


@pytest.mark.parametrize("field", ["profile", "state_domain", "trace_work_count", "source_sha256", "proof"])
def test_certificate_claim_edits_do_not_change_authority(
        inputs: AdmissionInputs, genuine: Record, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    # Given: cache the genuine assembler result, keeping actual candidate validation live.
    monkeypatch.setattr(profile_certificate, "expected", lambda context: genuine)
    candidate = deepcopy(genuine)
    candidate[field] = "changed"
    path = tmp_path / "certificate.json"
    path.write_text(json.dumps(candidate))
    # When/Then: relabeling or shrinking the certificate fails the public boundary.
    with pytest.raises(StatefulCertificateError):
        certificates.validate(path, inputs.context)


def test_source_change_rejects_existing_certificate(
        inputs: AdmissionInputs, genuine: Record, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: actual proof bytes, with one newly interpreted consumer source.
    changed = tmp_path / "consumer.py"
    changed.write_text("changed\n")
    monkeypatch.setattr(profile_certificate, "SOURCE_NAMES", (str(changed),))
    # When/Then: current-source revalidation rejects the earlier certificate.
    with pytest.raises(StatefulCertificateError):
        certificates.validate(inputs.certificate, inputs.context)


def test_certificate_change_rejects_before_native_advancement(
        inputs: AdmissionInputs, tmp_path: Path) -> None:
    # Given: a genuine production provider bound to a task-owned copy of the certificate.
    path = tmp_path / "certificate.json"
    path.write_bytes(inputs.certificate.read_bytes())
    with StatefulSequenceProvider(inputs.library, inputs.trace, path, inputs.context) as provider:
        before = bytes(provider._session.status()), provider.invocations
        path.write_text("{}\n")
        # When: changed certificate bytes are detected at the next work boundary.
        with pytest.raises(StatefulProviderError):
            provider.execute(provider.requests[0], 0)
        # Then: rejection precedes native stepping or successful invocation accounting.
        assert (bytes(provider._session.status()), provider.invocations) == before
