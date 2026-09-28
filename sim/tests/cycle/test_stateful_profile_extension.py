from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from sim.cycle import stateful_profile_extension as extension
from sim.cycle import stateful_sequence_certificate as certificates
from sim.cycle.certificate_contract import array_value, object_value, read_document
from sim.cycle.execution_sequence_admission import (
    AdmissionInputs,
    StatefulProviderError,
)
from sim.cycle.execution_sequence_provider import StatefulSequenceProvider
from sim.cycle.npu_trace_schema import Record
from sim.cycle.sequence_domain import DomainRevisionError
from sim.cycle.stateful_domain import (
    PROFILE_EXTENSION_REVISIONS,
    InitialState,
    StateDomainError,
    WorkClass,
    state_domain,
)
from sim.cycle.stateful_domain_admission import admit
from sim.cycle.stateful_profile_extension_pins import PROFILE_PINS
from sim.cycle.stateful_sequence_evidence import (
    EvidenceContext,
    StatefulCertificateError,
)


@pytest.fixture(scope="module", params=tuple(PROFILE_PINS))
def inputs(request: pytest.FixtureRequest) -> AdmissionInputs:
    root = os.environ.get("IM2P_PROFILE_EXTENSION_EVIDENCE_ROOT")
    if root is None:
        pytest.skip("genuine profile extension evidence not supplied")
    directory = Path(root) / "cycle-provider/profiles" / str(request.param)
    certificate = directory / "certificate-v1.json"
    document = read_document(certificate)
    parents = object_value(document["parents"], "parents")
    context = EvidenceContext(
        Path(root), Path(str(object_value(document["library"], "library")["path"])),
        Path(str(object_value(document["shared_library"], "shared")["path"])),
        *(Path(str(object_value(parents[key], key)["path"])) for key in ("base", "run_aware", "service")),
    )
    return AdmissionInputs(context.shared_library,
                           Path(str(object_value(document["trace"], "trace")["path"])),
                           certificate, context)


@pytest.fixture(scope="module")
def genuine(inputs: AdmissionInputs) -> Record:
    return extension.validate_document(inputs.certificate, inputs.context)


@pytest.mark.parametrize("revision,profile", tuple(PROFILE_EXTENSION_REVISIONS.items()))
def test_revision_cannot_authorize_another_profile(revision: str, profile: str) -> None:
    # Given: an independently reviewed revision identifies exactly one hardware profile.
    other = "a8w8-d16-hp1" if profile != "a8w8-d16-hp1" else "a4w4-d16-hp1"
    # When/Then: selecting the revision alone cannot transfer its finite domain.
    with pytest.raises(DomainRevisionError):
        state_domain(other, revision)


@pytest.mark.parametrize("field", [
    "profile", "precision", "dim", "trace", "trace_work_count", "work_ids", "state_domain",
    "source_sha256", "library", "shared_library", "proof", "milestone_parity", "domain_schema",
])
def test_mutation_cannot_relabel_certificate(inputs: AdmissionInputs, genuine: Record,
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    # Given: actual validated evidence; candidate document remains fully untrusted.
    monkeypatch.setattr(extension, "expected", lambda context, profile: genuine)
    candidate = deepcopy(genuine)
    candidate[field] = "changed"
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(candidate))
    # When/Then: editing any binding cannot authorize a replacement profile or corpus.
    with pytest.raises(StatefulCertificateError):
        certificates.validate(path, inputs.context)


@pytest.mark.parametrize("field", [
    "generation", "absolute_cycle", "scratchpad_half", "accumulator_half", "tag_count", "row_count", "ready_mask",
])
def test_initial_state_requires_certified_origin(inputs: AdmissionInputs, genuine: Record,
        monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    # Given: use the already-validated artifact to isolate the state boundary.
    scoped = certificates.ScopedEvidence(certificates.sha256(inputs.certificate),
        certificates.sha256(inputs.context.library), (str(genuine["case_id"]),),
        state_domain_revision=str(genuine["state_domain_revision"]))
    monkeypatch.setattr(certificates, "validate", lambda path, context: scoped)
    # When/Then: no non-cold state borrows a cold-origin certificate.
    with pytest.raises(StateDomainError):
        admit(str(genuine["profile"]), WorkClass("dense_main", "dense"),
              replace(InitialState(), **{field: 2}), inputs)


def test_trace_byte_change_is_rejected(inputs: AdmissionInputs, genuine: Record,
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: identical semantics with different trace identity.
    trace = tmp_path / "changed.jsonl"
    trace.write_bytes(inputs.trace.read_bytes() + b"\n")
    scoped = certificates.ScopedEvidence(certificates.sha256(inputs.certificate),
        certificates.sha256(inputs.context.library), (str(genuine["case_id"]),),
        state_domain_revision=str(genuine["state_domain_revision"]))
    monkeypatch.setattr(certificates, "validate", lambda path, context: scoped)
    # When/Then: byte identity remains mandatory at public admission.
    with pytest.raises(StatefulProviderError):
        admit(str(genuine["profile"]), WorkClass("dense_main", "dense"), InitialState(),
              replace(inputs, trace=trace))


def test_provider_replays_all_certified_boundaries(inputs: AdmissionInputs, genuine: Record) -> None:
    # Given: the complete independently captured native/RTL sequence.
    proof = read_document(Path(str(object_value(genuine["proof"], "proof")["path"])))
    rows = array_value(object_value(proof["works"], "works")["model"], "model")
    # When: the public provider consumes every work without reset or fallback.
    with StatefulSequenceProvider(inputs.library, inputs.trace, inputs.certificate, inputs.context) as provider:
        handle = provider._session._handle.value
        for work, value in zip(provider.requests, rows, strict=True):
            row = object_value(value, "model work")
            window = provider.execute(work, provider.native_cursor)
            # Then: each boundary and resident session matches the independent capture.
            assert (window.accepted_cycle, window.result_ready_cycle, window.final_scale_release_cycle,
                    window.resource_ready_cycle, window.next_scratchpad_half,
                    window.next_accumulator_half) == tuple(row[key] for key in (
                        "accepted", "result_ready", "final_scale_release", "resource_ready",
                        "next_scratchpad_half", "next_accumulator_half"))
            assert provider._session._handle.value == handle
            assert provider._session.status().generation == 1
            assert window.max_tag_occupancy <= 4 and window.max_row_occupancy <= 3
        provider.verify_complete()
        assert provider.invocations == genuine["trace_work_count"]
        assert provider.native_cursor == proof["final_resource_cycle"]


def test_changed_extension_source_fails_before_advance(inputs: AdmissionInputs, genuine: Record,
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: immutable raw evidence and a current certificate with a changed consumer source.
    source = tmp_path / "consumer.py"
    source.write_text("changed\n")
    monkeypatch.setattr(extension, "EXTENSION_SOURCES", (str(source),))
    # When/Then: old source authority cannot open a provider using changed code.
    with pytest.raises(StatefulCertificateError):
        extension.validate_document(inputs.certificate, inputs.context)
