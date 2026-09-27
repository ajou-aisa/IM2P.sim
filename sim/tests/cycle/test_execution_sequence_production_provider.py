from __future__ import annotations

import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from sim.cycle import stateful_sequence_certificate as certificate
from sim.cycle.execution_sequence_admission import ProductionStatefulAdmission
from sim.cycle.execution_sequence_provider import (
    DiagnosticStatefulProvider,
    StatefulProviderError,
    StatefulSequenceProvider,
)
from sim.cycle.npu_trace_schema import NpuTraceError
from sim.cycle.sequence_binding import Code, DomainSnapshot, SequenceSession
from sim.cycle.stateful_sequence_evidence import EvidenceContext
from sim.tests.cycle.test_execution_sequence_provider import (
    CERTIFICATE,
    CONTEXT,
    LIBRARY,
    TRACE,
)

pytest_plugins = ("sim.tests.cycle.test_execution_sequence_provider",)


@pytest.fixture
def test_only_production_gate(monkeypatch: pytest.MonkeyPatch,
                              validator_source_mirror: Path) -> None:
    """TEST_ONLY authorization seam; never evidence of current production parents."""
    def admitted(path: Path, context: EvidenceContext) -> None:
        certificate.validate(path, context)

    monkeypatch.setattr(certificate, "admit", admitted)


def test_authorized_production_path_uses_one_real_native_session(
    test_only_production_gate: None,
) -> None:
    # Given: TEST_ONLY admission with the actual library and twelve producer works.
    with StatefulSequenceProvider(LIBRARY, TRACE, CERTIFICATE, CONTEXT) as provider:
        handle = provider._session._handle.value
        # When: the complete ordered trace is executed through the production class.
        windows = tuple(provider.execute(work, provider.native_cursor) for work in provider.requests)
        provider.verify_complete()
        # Then: the gated path reuses native state and carries distinct typed admission.
        assert len(windows) == 12 and provider._session._handle.value == handle
        assert isinstance(provider.admission, ProductionStatefulAdmission)
        assert provider.admission.production_admitted is True
        assert all(window.admission is provider.admission for window in windows)
        assert provider._session.counters().logical_work_count == 12
        assert max(window.max_row_occupancy for window in windows) < 6
        assert asdict(provider.admission)["production_admitted"] is True


@pytest.mark.parametrize("constructor", [DiagnosticStatefulProvider, StatefulSequenceProvider])
@pytest.mark.parametrize(("field", "value"), [
    ("max_tag_occupancy", 5), ("row_count", 6), ("cursor", 1),
    ("generation", 2), ("ready_violation_mask", 1), ("resource_ready", 0),
])
def test_invalid_initial_domain_rejects_without_native_or_python_mutation(
    test_only_production_gate: None, monkeypatch: pytest.MonkeyPatch,
    constructor: type[DiagnosticStatefulProvider | StatefulSequenceProvider],
    field: str, value: int,
) -> None:
    # Given: a live provider whose current domain is outside the admitted tag bound.
    with constructor(LIBRARY, TRACE, CERTIFICATE, CONTEXT) as provider:
        snapshot = provider._session.domain_snapshot()
        setattr(snapshot, field, value)

        def outside_domain() -> DomainSnapshot:
            return snapshot

        monkeypatch.setattr(provider._session, "domain_snapshot", outside_domain)
        before = bytes(provider._session.status()), provider.completed, provider.invocations
        # When: execution attempts a transition from this invalid state.
        with pytest.raises(StatefulProviderError, match="pre-transition domain"):
            provider.execute(provider.requests[0], 0)
        # Then: even the native cursor and previously published bookkeeping are intact.
        assert (bytes(provider._session.status()), provider.completed, provider.invocations) == before
        assert provider.previous_resource_cycle == 0 and not provider.faulted


@pytest.mark.parametrize("source", ["trace", "certificate", "library"])
def test_changed_source_rejects_before_native_execution(
    validator_source_mirror: Path, tmp_path: Path, source: str,
) -> None:
    # Given: admission is bound to the real trace, then a caller swaps the input path.
    with DiagnosticStatefulProvider(LIBRARY, TRACE, CERTIFICATE, CONTEXT) as provider:
        changed = tmp_path / "changed.jsonl"
        changed.write_text("changed\n")
        if source == "library":
            provider._session.library = changed
        else:
            provider._inputs = replace(provider._inputs, **{source: changed})
        before = bytes(provider._session.status()), provider.completed, provider.invocations
        # When: a source-bound request is offered after source drift.
        with pytest.raises(StatefulProviderError, match="source binding"):
            provider.execute(provider.requests[0], 0)
        # Then: no native work or Python completion was committed.
        assert (bytes(provider._session.status()), provider.completed, provider.invocations) == before
        assert not provider.faulted


def test_production_rechecks_authority_before_final_publication(
    test_only_production_gate: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: TEST_ONLY authorization is revoked after all real native works finish.
    with StatefulSequenceProvider(LIBRARY, TRACE, CERTIFICATE, CONTEXT) as provider:
        _ = tuple(provider.execute(work, provider.native_cursor) for work in provider.requests)

        def stale(path: Path, context: EvidenceContext) -> None:
            raise certificate.NotReadyError("NOT_READY", "TEST_ONLY parent revocation")

        monkeypatch.setattr(certificate, "admit", stale)
        before = bytes(provider._session.status()), provider.completed, provider.invocations
        # When: publication checks the actual production admission gate again.
        with pytest.raises(StatefulProviderError, match="source binding"):
            provider.verify_complete()
        # Then: the session is poisoned without rewriting completed native reports.
        assert provider.faulted
        assert (bytes(provider._session.status()), provider.completed, provider.invocations) == before


def test_production_invalid_work_order_and_offer_preserve_completed_native_state(
    test_only_production_gate: None,
) -> None:
    # Given: the first work has completed through the TEST_ONLY admitted path.
    with StatefulSequenceProvider(LIBRARY, TRACE, CERTIFICATE, CONTEXT) as provider:
        first = provider.execute(provider.requests[0], 0)
        before = bytes(provider._session.status()), provider.completed, provider.invocations
        # When: duplicate, reordered, mutated, and early-electrical-offer requests arrive.
        for work, offered in (
            (provider.requests[0], provider.native_cursor),
            (provider.requests[2], provider.native_cursor),
            (replace(provider.requests[1], request_sha256="0" * 64), provider.native_cursor),
            (provider.requests[1], first.resource_ready_cycle - 1),
        ):
            with pytest.raises(StatefulProviderError):
                provider.execute(work, offered)
            # Then: each rejected request leaves prior native and Python state intact.
            assert (bytes(provider._session.status()), provider.completed, provider.invocations) == before


def test_interrupt_after_offer_faults_and_context_closes_native_handle(
    test_only_production_gate: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: real native admission followed by an interrupt at the first transition.
    provider = StatefulSequenceProvider(LIBRARY, TRACE, CERTIFICATE, CONTEXT)

    def interrupted(until: int) -> Code:
        raise KeyboardInterrupt

    monkeypatch.setattr(provider._session, "advance_until", interrupted)
    # When: an interrupt unwinds the provider's context manager.
    with pytest.raises(KeyboardInterrupt), provider:
        provider.execute(provider.requests[0], 0)
    # Then: pending native state is closed and cannot produce a normal completion.
    assert provider.faulted and provider._session._handle.value is None
    assert provider.invocations == 0 and not provider.completed


def test_work_execution_does_not_reread_trace_or_certificate_corpus(
    validator_source_mirror: Path,
) -> None:
    # Given: the trace is fully admitted before audit events record actual file opens.
    observed: list[str] = []
    recording = False

    def file_opened(event: str, arguments: tuple[str | bytes | int | None, ...]) -> None:
        if recording and event == "open" and isinstance(arguments[0], str):
            observed.append(arguments[0])

    sys.addaudithook(file_opened)
    with DiagnosticStatefulProvider(LIBRARY, TRACE, CERTIFICATE, CONTEXT) as provider:
        # When: twelve real native works execute with file-access auditing enabled.
        try:
            recording = True
            _ = tuple(provider.execute(work, provider.native_cursor) for work in provider.requests)
        finally:
            recording = False
        # Then: execution uses bound works; trace/corpus content is deferred to publication.
        assert str(TRACE) not in observed
        assert str(validator_source_mirror) not in observed
        observed.clear()
        try:
            recording = True
            provider.verify_complete()
        finally:
            recording = False
        assert str(TRACE) in observed and str(validator_source_mirror) in observed


@pytest.mark.parametrize(("field", "value"), [
    ("tile_i_count", 0), ("activation_stride_bytes", 1), ("work_id", 999),
])
def test_malformed_production_trace_rejects_before_native_open(
    test_only_production_gate: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    field: str, value: int,
) -> None:
    # Given: one malformed W in a complete producer trace under TEST_ONLY authorization.
    rows = [json.loads(line) for line in TRACE.read_text().splitlines()]
    work = next(row for row in rows if row["kind"] == "NPU_WORK")
    work[field] = value
    mutant = tmp_path / "invalid-work.jsonl"
    mutant.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    def forbidden_open(session: SequenceSession) -> SequenceSession:
        raise AssertionError("invalid work reached native open")

    monkeypatch.setattr(SequenceSession, "__enter__", forbidden_open)
    # When/Then: strict trace validation fails before any native session can open.
    with pytest.raises(NpuTraceError):
        StatefulSequenceProvider(LIBRARY, mutant, CERTIFICATE, CONTEXT)
