from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict, replace

import pytest

from sim.cycle.npu_trace_schema import RUN_REVISION
from sim.cycle.sequence_domain import (
    LEGACY_REVISION,
    TAG5_REVISION,
    TAG6_REVISION,
    DomainRevisionError,
    profile_domain,
)
from sim.cycle.stateful_domain import (
    A8D32_REVISION,
    InitialState,
    StateDomainError,
    WorkClass,
    state_domain,
)


@pytest.mark.parametrize(("profile", "precision", "dim"), [
    ("a4w4-d16-hp1", "A4W4", 16),
    ("a4w4-d32-hp1", "A4W4", 32),
    ("a4w4-d64-hp1", "A4W4", 64),
    ("a8w8-d16-hp1", "A8W8", 16),
    ("a8w8-d32-hp1", "A8W8", 32),
    ("a8w8-d64-hp1", "A8W8", 64),
])
def test_domain_describes_profile_without_broadening_legacy_limits(
        profile: str, precision: str, dim: int) -> None:
    # Given/When: a supported profile under the existing reviewed revision.
    domain = state_domain(profile, LEGACY_REVISION)
    # Then: the serializable description retains finite authority and old limits.
    assert (domain.precision, domain.dim) == (precision, dim)
    assert domain.limits == profile_domain(profile, LEGACY_REVISION)
    assert asdict(domain)["workload_scope"] == "CERTIFICATE_BOUND_CORPUS"


@pytest.mark.parametrize(("revision", "tag_peak"), [(TAG5_REVISION, 5), (TAG6_REVISION, 6)])
def test_tag5_and_tag6_keep_their_distinct_boundaries(revision: str, tag_peak: int) -> None:
    # Given/When: the already certified A8W8/DIM16 revision.
    domain = state_domain("a8w8-d16-hp1", revision)
    # Then: neither historical scope nor physical queue capacity changes.
    assert (domain.allowed_tag_peak, domain.allowed_row_peak, domain.limits.tag_capacity) == (tag_peak, 5, 6)


@pytest.mark.parametrize("field", [
    "generation", "absolute_cycle", "scratchpad_half", "accumulator_half",
    "tag_count", "row_count", "ready_mask",
])
def test_changed_initial_state_is_not_admitted(field: str) -> None:
    # Given: a finite DIM32 domain and one changed cold-state field.
    domain = state_domain("a8w8-d32-hp1", A8D32_REVISION)
    changed = replace(InitialState(), **{field: 2})
    # When/Then: a warm state or different epoch cannot reuse the cold proof.
    with pytest.raises(StateDomainError):
        domain.check_initial_state(changed)


@pytest.mark.parametrize("work", [
    WorkClass("dense_main", "dense"), WorkClass("residual", RUN_REVISION),
])
def test_supported_work_run_pair_passes_class_gate(work: WorkClass) -> None:
    # Given: class admission is separate from source/certificate/corpus admission.
    domain = state_domain("a8w8-d32-hp1", A8D32_REVISION)
    # When/Then: the two explicitly modeled classes pass only this class gate.
    domain.check_work_class(work)
    domain.check_initial_state(InitialState())


@pytest.mark.parametrize("work", [
    WorkClass("raw", "dense"), WorkClass("dense_main", RUN_REVISION),
    WorkClass("residual", "dense"), WorkClass("residual", "legacy-block-local"),
])
def test_unsupported_work_run_pair_rejects(work: WorkClass) -> None:
    # Given/When/Then: individually familiar names do not authorize an unsupported pair.
    with pytest.raises(StateDomainError):
        state_domain("a8w8-d32-hp1", A8D32_REVISION).check_work_class(work)


@pytest.mark.parametrize(("profile", "revision"), [
    ("a4w4-d32-hp1", A8D32_REVISION), ("a8w8-d64-hp1", A8D32_REVISION),
    ("a8w8-d32-hp1", TAG6_REVISION), ("a8w8-d32-hp1", "READY"),
    ("a8w8-d128-hp1", LEGACY_REVISION),
])
def test_profile_or_revision_mismatch_rejects(profile: str, revision: str) -> None:
    # Given/When/Then: descriptive schemas cannot grant unreviewed profile authority.
    with pytest.raises(DomainRevisionError):
        state_domain(profile, revision)


@pytest.mark.parametrize("field", ["allowed_tag_peak", "allowed_row_peak"])
def test_domain_is_immutable(field: str) -> None:
    # Given: a shared finite domain value.
    domain = state_domain("a8w8-d32-hp1", A8D32_REVISION)
    # When/Then: callers cannot mutate its tag limit.
    with pytest.raises(FrozenInstanceError):
        setattr(domain, field, 7)
