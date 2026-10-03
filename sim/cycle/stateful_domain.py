"""Finite state descriptions, not authority to admit an arbitrary workload."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal, assert_never

from sim.cycle.npu_trace_schema import RUN_REVISION
from sim.cycle.sequence_domain import (
    LEGACY_REVISION,
    TAG5_REVISION,
    TAG6_REVISION,
    DomainLimits,
    DomainRevisionError,
    profile_domain,
)

A8D32_REVISION: Final = "GUARDED_A8D32_PRODUCER_TAG4_ROW_LT4_V1"
PROFILE_EXTENSION_REVISIONS: Final = {
    "stateful-a8w8-d64-v1": "a8w8-d64-hp1",
    "stateful-a4w4-d16-v1": "a4w4-d16-hp1",
    "stateful-a4w4-d32-v1": "a4w4-d32-hp1",
    "stateful-a4w4-d64-v1": "a4w4-d64-hp1",
}
ACTUAL_TRACE_REVISIONS: Final = {
    "GUARDED_A8D32_ACTUAL_EVAL_TAG5_ROW_LT5_V1": ("a8w8-d32-hp1", DomainLimits(5, max_row_occupancy_exclusive=5)),
}
# One certified CPU/NPU-interleaved issue sequence each; never implies back-to-back or arbitrary gaps.
INTERLEAVED_TRACE_REVISIONS: Final = {
    "GUARDED_A8D32_INTERLEAVED_ISSUE_TAG5_ROW_LT5_V1": ("a8w8-d32-hp1", DomainLimits(5, max_row_occupancy_exclusive=5)),
}


class Revision(StrEnum):
    LEGACY = LEGACY_REVISION
    TAG5 = TAG5_REVISION
    TAG6 = TAG6_REVISION
    A8D32 = A8D32_REVISION
    A8D64 = "stateful-a8w8-d64-v1"
    A4D16 = "stateful-a4w4-d16-v1"
    A4D32 = "stateful-a4w4-d32-v1"
    A4D64 = "stateful-a4w4-d64-v1"
    A8D32_ACTUAL = "GUARDED_A8D32_ACTUAL_EVAL_TAG5_ROW_LT5_V1"
    A8D32_INTERLEAVED = "GUARDED_A8D32_INTERLEAVED_ISSUE_TAG5_ROW_LT5_V1"


class StateDomainError(ValueError):
    def __init__(self, boundary: str, detail: str) -> None:
        self.boundary, self.detail = boundary, detail
        super().__init__(f"{boundary}: {detail}")


@dataclass(frozen=True, slots=True)
class InitialState:
    generation: int = 1
    absolute_cycle: int = 0
    scratchpad_half: int = 0
    accumulator_half: int = 0
    tag_count: int = 0
    row_count: int = 0
    ready_mask: int = 0


@dataclass(frozen=True, slots=True)
class WorkClass:
    work_type: str
    run_type: str


@dataclass(frozen=True, slots=True)
class StateDomain:
    profile: str
    precision: str
    dim: int
    allowed_tag_peak: int
    allowed_row_peak: int
    ready_mask_rule: Literal["ZERO_AT_RESOURCE_READY"]
    initial_state_domain: Literal["COLD_RESET_ONCE"]
    supported_work_types: tuple[str, ...]
    supported_run_types: tuple[str, ...]
    revision: str
    workload_scope: Literal["CERTIFICATE_BOUND_CORPUS"] = "CERTIFICATE_BOUND_CORPUS"

    @property
    def limits(self) -> DomainLimits:
        return DomainLimits(self.allowed_tag_peak, self.allowed_row_peak + 1)

    def check_initial_state(self, state: InitialState) -> None:
        values = (
            state.generation, state.absolute_cycle, state.scratchpad_half,
            state.accumulator_half, state.tag_count, state.row_count, state.ready_mask,
        )
        if any(type(value) is not int for value in values) or state != InitialState():
            raise StateDomainError("initial state", "only the certified cold origin is supported")

    def check_work_class(self, work: WorkClass) -> None:
        if (work.work_type not in self.supported_work_types or
                work.run_type not in self.supported_run_types or
                (work.work_type, work.run_type) not in
                (("dense_main", "dense"), ("residual", RUN_REVISION))):
            raise StateDomainError("work class", "work/run pair is outside the certified domain")


def state_domain(profile: str, revision: str) -> StateDomain:
    """Describe a revision; certificate and trace validation remain mandatory."""
    shape = re.fullmatch(r"a([48])w\1-d(16|32|64)-hp1", profile)
    if shape is None:
        raise DomainRevisionError(profile, revision)
    try:
        selected = Revision(revision)
    except ValueError as error:
        raise DomainRevisionError(profile, revision) from error
    match selected:
        case Revision.A8D64 | Revision.A4D16 | Revision.A4D32 | Revision.A4D64:
            if PROFILE_EXTENSION_REVISIONS[revision] != profile:
                raise DomainRevisionError(profile, revision)
            limits = DomainLimits(4, max_row_occupancy_exclusive=4)
        case Revision.A8D32:
            if profile != "a8w8-d32-hp1":
                raise DomainRevisionError(profile, revision)
            limits = DomainLimits(4, max_row_occupancy_exclusive=4)
        case Revision.A8D32_ACTUAL:
            certified_profile, limits = ACTUAL_TRACE_REVISIONS[revision]
            if certified_profile != profile:
                raise DomainRevisionError(profile, revision)
        case Revision.A8D32_INTERLEAVED:
            certified_profile, limits = INTERLEAVED_TRACE_REVISIONS[revision]
            if certified_profile != profile:
                raise DomainRevisionError(profile, revision)
        case Revision.LEGACY | Revision.TAG5 | Revision.TAG6:
            limits = profile_domain(profile, revision)
        case unreachable:
            assert_never(unreachable)
    bits, dim = map(int, shape.groups())
    return StateDomain(
        profile, f"A{bits}W{bits}", dim, limits.max_tag_occupancy,
        limits.max_row_occupancy_exclusive - 1, "ZERO_AT_RESOURCE_READY",
        "COLD_RESET_ONCE", ("dense_main", "residual"), ("dense", RUN_REVISION), revision,
    )
