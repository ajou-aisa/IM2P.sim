from __future__ import annotations

import importlib
import json
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest

from sim.cycle import cli as cli_module
from sim.cycle import execution_sequence_admission as admission_module
from sim.cycle import sequence_binding_abi as abi_module
from sim.cycle import stateful_sequence_certificate as certificate
from sim.cycle import stateful_sequence_evidence as evidence
from sim.cycle.execution_ir import ServiceId
from sim.cycle.execution_sequence_provider import (
    DiagnosticStatefulProvider,
    StatefulProviderError,
    StatefulSequenceProvider,
)
from sim.cycle.npu_trace_schema import NpuTraceError
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_binding import (
    DomainSnapshot,
    RowPressure,
    SequenceError,
    Settings,
)
from sim.cycle.stateful_sequence_certificate import NotReadyError, ScopedEvidence
from sim.cycle.stateful_sequence_evidence import (
    EvidenceContext,
    StatefulCertificateError,
)

E = Path("/Users/zerogod/aisa-lab/build/im2p-gemmini/stateful-sequence-v2-20260924T033303Z")
ARCHIVE = E.parent
CERTIFICATE = E / "integration/todo16-context-path-review-20260926T123941Z/scoped-certificate.json"
TRACE = E / "rtl/task-13-producer-20260924T125443Z/runs/a4w4-d16-hp1/npu-cycle-trace.jsonl"
LIBRARY = E / "build/cycle-a8d16/libim2p_cycle_model.dylib"
CONTEXT = EvidenceContext(
    E, E / "build/cycle-a8d16/libim2p_cycle_model.a", LIBRARY,
    ARCHIVE / "production-drained-sequence-20260923T064750Z/certificate/base-v4/current-certificate.json",
    ARCHIVE / "production-drained-sequence-20260923T064750Z/certificate/run-aware-current/official/production-run-aware-certificate.json",
    ARCHIVE / "production-drained-sequence-resume-20260923T100511Z/service-v1-package-current/service-certificate.json",
)


@pytest.fixture
def validator_source_mirror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    helper = tmp_path / evidence.HELPER_SOURCE
    helper.parent.mkdir(parents=True)
    shutil.copyfile(Path(evidence.__file__), helper)
    pinned_digest = sha256(helper)

    def scoped(path: Path, context: EvidenceContext) -> ScopedEvidence:
        if sha256(helper) != pinned_digest:
            raise StatefulCertificateError("source closure", "mirrored validator helper changed")
        return ScopedEvidence(sha256(path), sha256(context.library), ())

    monkeypatch.setattr(certificate, "validate", scoped)
    return helper


def test_validator_helper_drift_faults_final_publication(validator_source_mirror: Path) -> None:
    # Given: twelve real works completed while a copied validator helper still matches its open digest.
    with DiagnosticStatefulProvider(LIBRARY, TRACE, CERTIFICATE, CONTEXT) as provider:
        windows = tuple(provider.execute(work, provider.native_cursor) for work in provider.requests)
        before = bytes(provider._session.status()), provider.completed
        validator_source_mirror.write_bytes(validator_source_mirror.read_bytes() + b"\n# changed after open\n")
        # When: final publication rechecks the validator-bound source mirror.
        with pytest.raises(StatefulProviderError, match="source binding"):
            provider.verify_complete()
        # Then: native reports are not relabeled as a normal completed schedule.
        assert len(windows) == 12 and provider.faulted
        assert (bytes(provider._session.status()), provider.completed) == before


def test_admission_module_drift_faults_final_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, validator_source_mirror: Path,
) -> None:
    # Given: a completed real native session with an unchanged test-only scoped seam.
    with DiagnosticStatefulProvider(LIBRARY, TRACE, CERTIFICATE, CONTEXT) as provider:
        _ = tuple(provider.execute(work, provider.native_cursor) for work in provider.requests)
        changed = tmp_path / "changed-admission.py"
        changed.write_text("changed\n")
        monkeypatch.setattr(admission_module, "__file__", str(changed))
        # When/Then: the admission module's pinned bytes reject final publication.
        with pytest.raises(StatefulProviderError, match="source binding"):
            provider.verify_complete()
        assert provider.faulted


@pytest.mark.parametrize("module", [abi_module, cli_module], ids=["binding-abi", "cli-layout"])
def test_transitive_binding_source_drift_faults_final_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, validator_source_mirror: Path, module: ModuleType,
) -> None:
    # Given: a task-owned copy of a binding import is pinned when the real native session opens.
    assert module.__file__ is not None
    copied = tmp_path / Path(module.__file__).name
    shutil.copyfile(module.__file__, copied)
    monkeypatch.setattr(module, "__file__", str(copied))
    with DiagnosticStatefulProvider(LIBRARY, TRACE, CERTIFICATE, CONTEXT) as provider:
        windows = tuple(provider.execute(work, provider.native_cursor) for work in provider.requests)
        before = bytes(provider._session.status()), provider.completed, provider.invocations
        # When: the copied dependency changes after native work but before final publication.
        copied.write_bytes(copied.read_bytes() + b"\n# changed after open\n")
        with pytest.raises(StatefulProviderError, match="source binding"):
            provider.verify_complete()
        # Then: prior native windows stay intact and the provider faults instead of publishing.
        assert provider.faulted and windows[0].work == provider.requests[0]
        assert (bytes(provider._session.status()), provider.completed, provider.invocations) == before


def test_current_source_mirror_allows_diagnostic_completion(validator_source_mirror: Path) -> None:
    # Given: the test-only scoped seam and an unchanged copied validator helper.
    with DiagnosticStatefulProvider(LIBRARY, TRACE, CERTIFICATE, CONTEXT) as provider:
        # When: all twelve real native works complete and the final source check runs.
        windows = tuple(provider.execute(work, provider.native_cursor) for work in provider.requests)
        provider.verify_complete()
        # Then: the positive native path remains a completed diagnostic result.
        assert len(windows) == 12 and not provider.faulted
        assert max(window.max_row_occupancy for window in windows) < 6


@pytest.mark.parametrize(("field", "value"), [
    ("max_row_occupancy", 6),
    ("generation", 2),
    ("cursor", 0),
    ("row_count", 1),
])
def test_row_domain_faults_without_losing_prior_window(
    validator_source_mirror: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: int,
) -> None:
    # Given: a real first work and a later drained edge whose row-queue peak is injected as six.
    with DiagnosticStatefulProvider(LIBRARY, TRACE, CERTIFICATE, CONTEXT) as provider:
        first = provider.execute(provider.requests[0], 0)
        before = provider.invocations, provider.completed, provider.previous_resource_cycle
        original = provider._session.row_pressure

        def full_peak() -> RowPressure:
            pressure = original()
            if pressure.cursor > first.resource_ready_cycle:
                assert pressure.row_count == 0
                setattr(pressure, field, value)
            return pressure

        monkeypatch.setattr(provider._session, "row_pressure", full_peak)
        # When: the second native report reaches the row-domain guard.
        with pytest.raises(StatefulProviderError, match="post-transition domain"):
            provider.execute(provider.requests[1], provider.native_cursor)
        # Then: the provider faults and leaves the first published window/count intact.
        assert provider.faulted and (provider.invocations, provider.completed,
                                     provider.previous_resource_cycle) == before
        assert first.work == provider.requests[0]


def test_provider_error_preserves_boundary_and_detail() -> None:
    # Given: a typed provider error created outside resource unwinding.
    error = StatefulProviderError("source binding", "changed")
    # When/Then: its public diagnostic remains stable.
    assert str(error) == "source binding: changed"


def test_provider_error_survives_contextmanager_unwind() -> None:
    # Given: a generator context manager that propagates the original provider error.
    @contextmanager
    def scope() -> Iterator[None]:
        yield

    # When/Then: unwinding keeps the typed error rather than raising FrozenInstanceError.
    with pytest.raises(StatefulProviderError, match="source binding: changed"), scope():
        raise StatefulProviderError("source binding", "changed")


def test_diagnostic_provider_executes_real_ordered_trace_over_one_session() -> None:
    # Given: a reviewed producer trace containing two parents and twelve works.
    with DiagnosticStatefulProvider(LIBRARY, TRACE, CERTIFICATE, CONTEXT) as provider:
        requests = provider.requests
        pointer = provider._session._handle.value
        # When: every declared work is offered after the preceding resource release.
        windows = tuple(provider.execute(work, provider.native_cursor) for work in requests)
        provider.verify_complete()
        # Then: native acceptance and results are absolute, ordered, and source-bound.
        assert len(windows) > 4
        assert provider._session._handle.value == pointer
        assert provider.completed == frozenset(work.identity for work in requests)
        assert all(w.offered_cycle <= w.accepted_cycle < w.result_ready_cycle <=
                   w.final_scale_release_cycle <= w.resource_ready_cycle for w in windows)
        assert all(w.max_tag_occupancy <= 4 for w in windows)
        assert provider.admission.production_admitted is False


def test_production_constructor_refuses_before_native_open() -> None:
    # Given: the same scoped certificate with unresolved State.array.
    # When/Then: production admission reports its typed NOT_READY boundary.
    with pytest.raises(NotReadyError, match="NOT_READY"):
        StatefulSequenceProvider(LIBRARY, TRACE, CERTIFICATE, CONTEXT)


def test_production_scope_stays_not_ready_with_current_native(
    validator_source_mirror: Path,
) -> None:
    # Given: a test-only scoped seam with current native bytes.
    # When/Then: production still calls typed admit and returns NOT_READY.
    with pytest.raises(NotReadyError, match="NOT_READY"):
        StatefulSequenceProvider(LIBRARY, TRACE, CERTIFICATE, CONTEXT)


def test_invalid_request_does_not_advance_native_or_python_state() -> None:
    # Given: a cold diagnostic provider and a copied ID with a false binding.
    with DiagnosticStatefulProvider(LIBRARY, TRACE, CERTIFICATE, CONTEXT) as provider:
        original = provider.requests[0]
        before = (provider.native_cursor, provider.invocations, provider.completed)
        # When: the caller offers a forged request.
        with pytest.raises(ValueError, match="request binding"):
            provider.execute(replace(original, request_sha256="0" * 64), 0)
        with pytest.raises(ValueError, match="request binding"):
            provider.execute(replace(original, identity=ServiceId("npu:999")), 0)
        # Then: the real next request is still executable from the same cursor.
        assert (provider.native_cursor, provider.invocations, provider.completed) == before
        assert provider.execute(original, 0).work == original


def test_native_hard_limit_faults_provider_without_fallback() -> None:
    # Given: a budget shorter than the first real work.
    with DiagnosticStatefulProvider(
        LIBRARY, TRACE, CERTIFICATE, CONTEXT, settings=Settings(max_work_cycles=1),
    ) as provider:
        # When: native stepping reaches its hard limit.
        with pytest.raises(SequenceError, match="LIMIT"):
            provider.execute(provider.requests[0], 0)
        # Then: the provider cannot silently serve the next work.
        assert provider.faulted
        assert provider.invocations == 0
        with pytest.raises(ValueError, match="FAULTED"):
            provider.execute(provider.requests[0], provider.native_cursor)


@pytest.mark.parametrize(("field", "value", "work_index"), [
    ("tile_i_count", 0, 0),
    ("activation_stride_bytes", 1, 0),
    ("profile", "a8w8-d16-hp1", -1),
])
def test_invalid_trace_rejects_before_native_session(
    tmp_path: Path, field: str, value: int | str, work_index: int,
) -> None:
    # Given: a complete producer trace with one invalid boundary field.
    rows = [json.loads(line) for line in TRACE.read_text().splitlines()]
    selected = rows[0] if work_index == -1 else next(row for row in rows if row["kind"] == "NPU_WORK")
    selected[field] = value
    mutant = tmp_path / "mutant.jsonl"
    mutant.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    # When/Then: strict parsing refuses the trace before opening native state.
    with pytest.raises(NpuTraceError):
        DiagnosticStatefulProvider(LIBRARY, mutant, CERTIFICATE, CONTEXT)


def test_compact_run_mask_rejects_before_native_session(tmp_path: Path) -> None:
    # Given: an impossible compact mask on the real residual work.
    rows = [json.loads(line) for line in TRACE.read_text().splitlines()]
    residual = next(row for row in rows if row["kind"] == "NPU_WORK" and row["provenance"] == "residual")
    residual["runs"][0]["original_k_mask"] = 0
    mutant = tmp_path / "invalid-run.jsonl"
    mutant.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    # When/Then: the existing run validator rejects it.
    with pytest.raises(NpuTraceError, match="run coverage"):
        DiagnosticStatefulProvider(LIBRARY, mutant, CERTIFICATE, CONTEXT)


@pytest.mark.parametrize("field", ["max_tag_occupancy", "ready_violation_mask"])
def test_post_transition_domain_fault_suppresses_window(
    monkeypatch: pytest.MonkeyPatch, field: str,
) -> None:
    # Given: one real native session with a corrupted diagnostic snapshot at resource release.
    with DiagnosticStatefulProvider(LIBRARY, TRACE, CERTIFICATE, CONTEXT) as provider:
        original = provider._session.domain_snapshot

        def faulty_snapshot() -> DomainSnapshot:
            snapshot = original()
            if snapshot.cursor and snapshot.resource_ready:
                setattr(snapshot, field, 5 if field == "max_tag_occupancy" else 1)
            return snapshot

        monkeypatch.setattr(provider._session, "domain_snapshot", faulty_snapshot)
        # When: native completes a real work, but S admission sees the fault.
        with pytest.raises(StatefulProviderError, match="post-transition domain"):
            provider.execute(provider.requests[0], 0)
        # Then: no Python completion commits or normal window can follow.
        assert provider.faulted and provider.invocations == 0
        assert provider.completed == frozenset()
        assert provider.native_cursor > 0
        with pytest.raises(StatefulProviderError, match="FAULTED"):
            provider.execute(provider.requests[0], provider.native_cursor)


def test_reordered_duplicate_and_early_offer_preserve_native_status() -> None:
    # Given: a live session with its first ordered work complete.
    with DiagnosticStatefulProvider(LIBRARY, TRACE, CERTIFICATE, CONTEXT) as provider:
        first = provider.requests[0]
        window = provider.execute(first, 0)
        before = bytes(provider._session.status()), provider.invocations, provider.completed
        # When/Then: stale, duplicate, and reordered requests are rejected without mutation.
        for request, cycle in ((first, provider.native_cursor),
                               (provider.requests[2], provider.native_cursor),
                               (provider.requests[1], window.resource_ready_cycle - 1)):
            with pytest.raises(StatefulProviderError):
                provider.execute(request, cycle)
            assert (bytes(provider._session.status()), provider.invocations, provider.completed) == before


@pytest.mark.parametrize(("owner", "attribute"), [
    ("inputs", "trace"),
    ("inputs", "certificate"),
    ("session", "library"),
])
def test_stale_source_binding_rejects_final_publication(
    tmp_path: Path, validator_source_mirror: Path, owner: str, attribute: str,
) -> None:
    # Given: a completed diagnostic session whose source trace path no longer matches admission.
    with DiagnosticStatefulProvider(LIBRARY, TRACE, CERTIFICATE, CONTEXT) as provider:
        _ = tuple(provider.execute(work, provider.native_cursor) for work in provider.requests)
        mutant = tmp_path / "stale-source"
        mutant.write_text("changed\n")
        if owner == "session":
            provider._session.library = mutant
        else:
            provider._inputs = replace(provider._inputs, **{attribute: mutant})
        before = bytes(provider._session.status())
        # When/Then: final publication binding fails without changing native state.
        with pytest.raises(StatefulProviderError, match="source binding"):
            provider.verify_complete()
        assert provider.faulted
        assert bytes(provider._session.status()) == before


def test_non_hp1_timing_rejects_before_native_open() -> None:
    # Given: a read-ready period outside the reviewed HP1 scenario.
    # When/Then: the provider refuses it before a native handle exists.
    with pytest.raises(StatefulProviderError, match="reference memory"):
        DiagnosticStatefulProvider(LIBRARY, TRACE, CERTIFICATE, CONTEXT,
                                   settings=Settings(read_ready_period=3))


def test_forged_scoped_result_cannot_replace_evidence_context() -> None:
    # Given: a caller-created value shaped like the validator output.
    forged = ScopedEvidence("0" * 64, "0" * 64, ())
    # When/Then: neither constructor trusts it as a validator input.
    provider_module = importlib.import_module("sim.cycle.execution_sequence_provider")
    for constructor in ("DiagnosticStatefulProvider", "StatefulSequenceProvider"):
        with pytest.raises(StatefulProviderError, match="certificate context"):
            getattr(provider_module, constructor)(LIBRARY, TRACE, CERTIFICATE, forged)
