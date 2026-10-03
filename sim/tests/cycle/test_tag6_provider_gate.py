from __future__ import annotations

from pathlib import Path

import pytest

from sim.cycle import (
    cli,
    execution_sequence_admission,
    sequence_binding,
    sequence_binding_abi,
)
from sim.cycle import execution_sequence_provider as providers
from sim.cycle import stateful_sequence_certificate as certificates
from sim.cycle.execution_ir import ServiceId
from sim.cycle.execution_sequence_admission import (
    AdmissionInputs,
    BoundWork,
    StatefulAdmission,
)
from sim.cycle.execution_services import NpuWork
from sim.cycle.npu_trace import _identity, work_binding
from sim.cycle.npu_trace_schema import Work
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_binding import DomainSnapshot
from sim.cycle.sequence_domain import (
    TAG5_PROFILE,
    TAG6_REVISION,
    DomainRevisionError,
    profile_domain,
)
from sim.cycle.stateful_sequence_evidence import EvidenceContext

pytest_plugins = ("sim.tests.cycle.test_sequence_binding",)


@pytest.fixture
def inputs(library: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AdmissionInputs:
    """TEST_ONLY admission seam; real native state and real per-work source checks."""
    trace, certificate = tmp_path / "trace", tmp_path / "certificate"
    trace.write_text("TEST_ONLY two one-element works\n")
    certificate.write_text("TEST_ONLY scoped admission, not production evidence\n")
    context = EvidenceContext(tmp_path, library, library, certificate, certificate, certificate)
    selected = AdmissionInputs(library, trace, certificate, context)
    scoped = certificates.ScopedEvidence(sha256(certificate), sha256(library), (),
                                         state_domain_revision=TAG6_REVISION)
    works = tuple(Work(0, i, i, i, i, i, "fixture", "MUL_MAT", "dense_main", "full",
                       1, 0, None, (1, 1, 1, 1, 1, 1, 1, 1, 4, 1), None, (), (), None)
                  for i in range(2))
    bound = tuple(BoundWork(NpuWork(ServiceId(f"npu:{work.identity}"),
                                    work_binding(work), TAG5_PROFILE), work) for work in works)
    admission = StatefulAdmission(
        scoped, sha256(trace), sequence_binding.source_identity(library, TAG5_PROFILE),
        sha256(Path(sequence_binding.__file__)), sha256(Path(sequence_binding_abi.__file__)),
        sha256(Path(cli.__file__)), sha256(Path(providers.__file__)),
        sha256(Path(execution_sequence_admission.__file__)), sha256(Path(certificates.__file__)),
        TAG5_PROFILE, tuple((work.identity, work.parent_id, work_binding(work)) for work in works),
        trace_identity=_identity(trace),
    )

    def test_only_admission(arguments: AdmissionInputs, *, production: bool = False
                            ) -> tuple[StatefulAdmission, tuple[BoundWork, ...]]:
        assert arguments == selected and not production
        return admission, bound

    monkeypatch.setattr(providers, "admit_trace", test_only_admission)
    return selected


def test_tag6_gap_rejects_before_native_state_changes(inputs: AdmissionInputs) -> None:
    # Given: two real native works under the test-only Tag6 scope.
    with providers.DiagnosticStatefulProvider(
            inputs.library, inputs.trace, inputs.certificate, inputs.context) as provider:
        first = provider.execute(provider.requests[0], 0)
        before = bytes(provider._session.status()), provider.invocations
        # When: an unreviewed idle gap is requested.
        with pytest.raises(providers.StatefulProviderError, match="Tag6 offer policy"):
            provider.execute(provider.requests[1], first.resource_ready_cycle + 1)
        # Then: rejected policy does not advance native or Python state.
        assert (bytes(provider._session.status()), provider.invocations) == before
        second = provider.execute(provider.requests[1], first.resource_ready_cycle)
        assert second.offered_cycle == first.resource_ready_cycle


@pytest.mark.parametrize(("peak", "accepted"), [(6, True), (7, False)])
def test_tag6_guard_keeps_physical_capacity(
        inputs: AdmissionInputs, monkeypatch: pytest.MonkeyPatch, peak: int, accepted: bool) -> None:
    # Given: the real native report with only its observed sticky peak injected.
    with providers.DiagnosticStatefulProvider(
            inputs.library, inputs.trace, inputs.certificate, inputs.context) as provider:
        original = provider._session.domain_snapshot

        def observed_peak() -> DomainSnapshot:
            domain = original()
            if domain.cursor and domain.resource_ready:
                domain.max_tag_occupancy = peak
            return domain

        monkeypatch.setattr(provider._session, "domain_snapshot", observed_peak)
        # When/Then: six is within the reviewed revision, seven still faults publication.
        if accepted:
            assert provider.execute(provider.requests[0], 0).max_tag_occupancy == peak
        else:
            with pytest.raises(providers.StatefulProviderError, match="post-transition domain"):
                provider.execute(provider.requests[0], 0)
            assert provider.faulted and provider.invocations == 0


@pytest.mark.parametrize("profile", ["a4w4-d16-hp1", "a8w8-d32-hp1", "a8w8-d64-hp1"])
def test_tag6_revision_rejects_unreviewed_profiles(profile: str) -> None:
    # Given/When/Then: selecting the new revision never expands other profiles.
    with pytest.raises(DomainRevisionError):
        profile_domain(profile, TAG6_REVISION)


def test_tag6_caller_work_watchdog_still_faults_native(inputs: AdmissionInputs) -> None:
    # Given: an explicit one-cycle watchdog within the admitted Tag6 domain.
    with providers.DiagnosticStatefulProvider(
            inputs.library, inputs.trace, inputs.certificate, inputs.context,
            settings=sequence_binding.Settings(max_work_cycles=1)) as provider:
        # When: a real work cannot finish within the caller's smaller budget.
        with pytest.raises(sequence_binding.SequenceError) as caught:
            provider.execute(provider.requests[0], 0)
        # Then: the native hard limit still faults without publishing or falling back.
        assert caught.value.code == sequence_binding.Code.LIMIT
        assert provider.faulted and provider.invocations == 0
        assert provider._session.error().stop_reason == sequence_binding.StopReason.WORK_BUDGET
