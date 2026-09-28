from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sim.cycle import (
    cli,
    sequence_binding,
    sequence_binding_abi,
    stateful_sequence_certificate,
)
from sim.cycle.certificate_contract import object_value, read_document
from sim.cycle.execution_ir import ServiceId
from sim.cycle.execution_services import NpuWork
from sim.cycle.npu_trace import _identity, work_binding
from sim.cycle.npu_trace_integrity import read_records, start_trace
from sim.cycle.npu_trace_schema import VERSION
from sim.cycle.npu_trace_schema import Work as TraceWork
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_binding import SequenceError, SequenceSession, SourceIdentity
from sim.cycle.sequence_domain import TAG5_PROFILE, TAG6_REVISION
from sim.cycle.stateful_domain import (
    A8D32_REVISION,
    ACTUAL_TRACE_REVISIONS,
    PROFILE_EXTENSION_REVISIONS,
    InitialState,
    WorkClass,
)
from sim.cycle.stateful_sequence_certificate import ScopedEvidence
from sim.cycle.stateful_sequence_evidence import (
    EvidenceContext,
    StatefulCertificateError,
)


class StatefulProviderError(ValueError):
    def __init__(self, boundary: str, detail: str) -> None:
        self.boundary, self.detail = boundary, detail
        super().__init__(f"{boundary}: {detail}")


@dataclass(frozen=True, slots=True)
class AdmissionInputs:
    library: Path
    trace: Path
    certificate: Path
    context: EvidenceContext


@dataclass(frozen=True, slots=True)
class BoundWork:
    request: NpuWork
    trace: TraceWork


@dataclass(frozen=True, slots=True)
class StatefulAdmission:
    scoped: ScopedEvidence
    trace_sha256: str
    source_identity: SourceIdentity
    binding_sha256: str
    binding_abi_sha256: str
    cli_sha256: str
    provider_sha256: str
    admission_sha256: str
    validator_sha256: str
    profile: str
    work_bindings: tuple[tuple[int, int, str], ...]
    read_ready_period: int = 5
    initial_scratchpad_half: int = 0
    initial_accumulator_half: int = 0

    validation_scope: str = field(default="DIAGNOSTIC_SCOPED_EVIDENCE", init=False)
    production_admitted: bool = field(default=False, init=False)
    trace_identity: tuple[int, ...] = ()
    extension_source_sha256: tuple[tuple[str, str], ...] = ()

    @property
    def certificate_sha256(self) -> str:
        return self.scoped.certificate_sha256


@dataclass(frozen=True, slots=True)
class ProductionStatefulAdmission(StatefulAdmission):
    validation_scope: Literal["STATEFUL_SEQUENCE_PRODUCTION"] = field(
        default="STATEFUL_SEQUENCE_PRODUCTION", init=False)
    production_admitted: Literal[True] = field(default=True, init=False)


def admit_trace(inputs: AdmissionInputs, *, production: bool = False,
                ) -> tuple[StatefulAdmission, tuple[BoundWork, ...]]:
    if not isinstance(inputs.context, EvidenceContext):
        raise StatefulProviderError("certificate context", "EvidenceContext required")
    if inputs.library.resolve(strict=True) != inputs.context.shared_library.resolve(strict=True):
        raise StatefulProviderError("library", "caller library differs from certificate context")
    if production:
        stateful_sequence_certificate.admit(inputs.certificate, inputs.context)
    scoped = stateful_sequence_certificate.validate(inputs.certificate, inputs.context)
    trace_identity = _identity(inputs.trace)
    trace_digest = sha256(inputs.trace)
    records = read_records(inputs.trace)
    state = start_trace(records)
    if state.run.trace_version != VERSION:
        raise StatefulProviderError("trace", "version 2 required")
    bound = tuple(BoundWork(NpuWork(ServiceId(f"npu:{work.identity}"),
                                    work_binding(work), state.run.profile), work)
                  for record in records if (work := state.consume(record)) is not None)
    _ = state.summary()
    if not bound or _identity(inputs.trace) != trace_identity or sha256(inputs.trace) != trace_digest:
        raise StatefulProviderError("trace", "empty or changed during parse")
    if scoped.state_domain_revision == TAG6_REVISION:
        document = read_document(inputs.certificate)
        if (state.run.profile != TAG5_PROFILE or
                object_value(document["trace"], "Tag6 trace")["sha256"] != trace_digest or
                tuple(item.trace.identity for item in bound) != tuple(range(374))):
            raise StatefulProviderError("Tag6 corpus", "only the certified original374 trace is supported")
    if scoped.state_domain_revision in (TAG6_REVISION, A8D32_REVISION, *PROFILE_EXTENSION_REVISIONS,
                                        *ACTUAL_TRACE_REVISIONS):
        from sim.cycle.stateful_domain_admission import check_profile_scope

        domain = check_profile_scope(state.run.profile, inputs, scoped)
        domain.check_initial_state(InitialState())
        for item in bound:
            domain.check_work_class(WorkClass(item.trace.provenance,
                                             item.trace.residual_work_revision or "dense"))
        if scoped.state_domain_revision in (A8D32_REVISION, *PROFILE_EXTENSION_REVISIONS, *ACTUAL_TRACE_REVISIONS):
            document = read_document(inputs.certificate)
            if [item.trace.identity for item in bound] != document["work_ids"]:
                raise StatefulProviderError("profile corpus", "complete certified work order required")
    admission_type = ProductionStatefulAdmission if production else StatefulAdmission
    admission = admission_type(
        scoped, trace_digest, sequence_binding.source_identity(inputs.library, state.run.profile),
        sha256(Path(sequence_binding.__file__)),
        sha256(Path(sequence_binding_abi.__file__)), sha256(Path(cli.__file__)),
        sha256(Path(__file__).with_name("execution_sequence_provider.py")),
        sha256(Path(__file__)), sha256(Path(stateful_sequence_certificate.__file__)),
        state.run.profile,
        tuple((item.trace.identity, item.trace.parent_id, item.request.request_sha256) for item in bound),
        trace_identity=trace_identity,
        extension_source_sha256=tuple((name, sha256(Path(__file__).with_name(name))) for name in (
            "stateful_domain.py", "stateful_domain_admission.py", "stateful_profile_certificate.py",
            "stateful_profile_extension.py", "stateful_profile_extension_evidence.py",
            "stateful_profile_extension_pins.py")),
    )
    return admission, bound


def verify_sources(admission: StatefulAdmission, inputs: AdmissionInputs,
                   session: SequenceSession, *, publication: bool = True) -> None:
    try:
        if (sha256(inputs.certificate) != admission.certificate_sha256 or
                 _identity(inputs.trace) != admission.trace_identity or
                 (publication and sha256(inputs.trace) != admission.trace_sha256) or
                 sha256(Path(sequence_binding.__file__)) != admission.binding_sha256 or
                 sha256(Path(sequence_binding_abi.__file__)) != admission.binding_abi_sha256 or
                 sha256(Path(cli.__file__)) != admission.cli_sha256 or
                 sha256(Path(__file__).with_name("execution_sequence_provider.py")) != admission.provider_sha256 or
                sha256(Path(__file__)) != admission.admission_sha256 or
                sha256(Path(stateful_sequence_certificate.__file__)) != admission.validator_sha256 or
                any(sha256(Path(__file__).with_name(name)) != digest
                    for name, digest in admission.extension_source_sha256)):
            raise StatefulProviderError("source binding", "certificate, trace, or Python source changed")
        session.verify_identity()
        if not publication:
            return
        if stateful_sequence_certificate.validate(inputs.certificate, inputs.context) != admission.scoped:
            raise StatefulProviderError("source binding", "scoped certificate identity changed")
        if isinstance(admission, ProductionStatefulAdmission):
            stateful_sequence_certificate.admit(inputs.certificate, inputs.context)
    except (OSError, SequenceError, StatefulCertificateError) as error:
        raise StatefulProviderError("source binding", str(error)) from error
