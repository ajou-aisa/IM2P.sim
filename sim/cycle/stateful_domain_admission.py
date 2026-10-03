"""Profile/class/state admission still requires concrete trace and certificate bytes."""
from __future__ import annotations

from dataclasses import dataclass

from sim.cycle import stateful_sequence_certificate
from sim.cycle.certificate_contract import object_value, read_document
from sim.cycle.execution_sequence_admission import (
    AdmissionInputs,
    StatefulProviderError,
)
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_binding import SourceIdentity, source_identity
from sim.cycle.stateful_domain import InitialState, StateDomain, WorkClass, state_domain
from sim.cycle.stateful_measurement_contract import ArtifactReference
from sim.cycle.stateful_sequence_certificate import ScopedEvidence


@dataclass(frozen=True, slots=True)
class DomainAdmission:
    domain: StateDomain
    work_class: WorkClass
    initial_state: InitialState
    provider_certificate: ArtifactReference
    trace_identity: ArtifactReference
    source_identity: SourceIdentity


def check_profile_scope(profile: str, inputs: AdmissionInputs, scoped: ScopedEvidence) -> StateDomain:
    """Check the profile and corpus after the caller has validated the certificate."""
    document = read_document(inputs.certificate)
    trace = object_value(document.get("trace"), "profile trace")
    if (document.get("profile") != profile or trace.get("sha256") != sha256(inputs.trace) or
            inputs.library.resolve(strict=True) != inputs.context.shared_library.resolve(strict=True)):
        raise StatefulProviderError("profile domain", "profile, trace or library differs from certificate")
    return state_domain(profile, scoped.state_domain_revision)


def admit(profile: str, work_class: WorkClass, initial_state: InitialState,
          certificate: AdmissionInputs) -> DomainAdmission:
    """Validate model-domain admission; this grants no clock or host authority."""
    scoped = stateful_sequence_certificate.validate(certificate.certificate, certificate.context)
    domain = check_profile_scope(profile, certificate, scoped)
    domain.check_work_class(work_class)
    domain.check_initial_state(initial_state)
    return DomainAdmission(
        domain, work_class, initial_state,
        ArtifactReference(str(certificate.certificate.resolve()), scoped.certificate_sha256),
        ArtifactReference(str(certificate.trace.resolve()), sha256(certificate.trace)),
        source_identity(certificate.library, profile),
    )
