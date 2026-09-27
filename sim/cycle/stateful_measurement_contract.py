"""Stateful consumer inputs; these schemas never authorize E2E publication."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ClockRequirement:
    profile: str
    hardware_contract_sha256: str
    required_timing_status: Literal["POST_ROUTE_PASS"] = "POST_ROUTE_PASS"


@dataclass(frozen=True, slots=True)
class HostRequirement:
    evidence_kind: Literal["VALIDATED_TARGET_HOST_APPLICATION"] = "VALIDATED_TARGET_HOST_APPLICATION"
    status: Literal["NOT_PROVIDED"] = "NOT_PROVIDED"


@dataclass(frozen=True, slots=True)
class ValidatedStatefulMeasurement:
    provider_certificate: ArtifactReference
    state_domain_revision: str
    trace_identity: ArtifactReference
    profile: str
    clock_requirement: ClockRequirement
    host_requirement: HostRequirement
    schema: Literal["im2p-validated-stateful-measurement-v1"] = field(
        default="im2p-validated-stateful-measurement-v1", init=False)
    e2e_reconstruction_ready: Literal["NOT_READY"] = field(default="NOT_READY", init=False)
    paper_campaign_complete: Literal["NOT_RUN"] = field(default="NOT_RUN", init=False)


@dataclass(frozen=True, slots=True)
class OperatingClockArtifact:
    profile: str
    frequency_hz: int
    source: ArtifactReference
    rtl_hash: str
    constraint_hash: str
    implementation_hash: str
    timing_status: Literal["POST_ROUTE_PASS"]
    hardware_contract_sha256: str
