from __future__ import annotations

# allow: SIZE_OK — shared selection state machine and legacy/stateful gates retain one source-bound consumer boundary.
from dataclasses import asdict, dataclass
from fractions import Fraction
from math import ceil
from pathlib import Path
from typing import TypeAlias, assert_never

from sim.cycle.execution_ir import (
    ExecutionError,
    ExecutionIR,
    Kind,
    Milestone,
    Node,
    NodeId,
    ResourceId,
    ensure,
    service_id,
)
from sim.cycle.execution_sequence_admission import (
    ProductionStatefulAdmission,
    StatefulAdmission,
)
from sim.cycle.execution_sequence_provider import (
    DiagnosticStatefulProvider,
    StatefulSequenceProvider,
)
from sim.cycle.execution_services import NpuProvider, Services, WorkerService
from sim.cycle.npu_trace_schema import Record
from sim.cycle.reconstruct_graph import sha256

TimingProvider: TypeAlias = NpuProvider | DiagnosticStatefulProvider | StatefulSequenceProvider


@dataclass(frozen=True, slots=True)
class Scenario:
    npu_frequency_hz: int
    scope: str
    clock_artifact: Path | None = None
    profile: str = ''


@dataclass(frozen=True, slots=True)
class ScheduleInputs:
    ir: ExecutionIR
    services: Services
    npu_provider: TimingProvider


@dataclass(frozen=True, slots=True)
class ScheduledNode:
    identity: NodeId
    accepted_ns: Fraction
    result_ready_ns: Fraction
    resource_ready_ns: Fraction
    worker_intervals: tuple[WorkerService, ...]
    accepted_cycle: int | None = None
    evidence_id: str | None = None
    request_available_ns: Fraction | None = None
    port_offer_ns: Fraction | None = None
    final_scale_release_ns: Fraction | None = None

    def milestone(self, milestone: Milestone) -> Fraction:
        match milestone:
            case Milestone.ACCEPTED: return self.accepted_ns
            case Milestone.RESULT_READY: return self.result_ready_ns
            case Milestone.RESOURCE_READY: return self.resource_ready_ns
            case unreachable: assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class ScheduleResult:
    nodes: tuple[ScheduledNode, ...]
    scope: str
    service_validation_scope: str
    service_binding: Record
    source_binding: Record | None = None

    @property
    def by_id(self) -> dict[NodeId, ScheduledNode]:
        return {node.identity: node for node in self.nodes}

    def record(self) -> Record:
        stateful = self.source_binding is not None
        record: Record = {'schema': 'im2p-execution-schedule', 'version': 2 if stateful else 1, 'scope': self.scope,
                'service_validation_scope': self.service_validation_scope,
                'service_binding': self.service_binding,
                'time_unit': 'nanosecond-rational', 'queue_policy': 'READY_ORDER_THEN_ID',
                'clock_alignment': 'CEIL_TO_NPU_EDGE', 'worker_policy': 'EXPLICIT_GANG_VECTOR',
                'paper_latency_ready': False,
                'validated_service_reconstruction': self.scope == 'RECONSTRUCTED',
                'nodes': [scheduled_record(node) for node in self.nodes]}
        if stateful:
            ensure(self.source_binding == schedule_source_binding(), 'schedule source binding changed before publication')
            record.update(self.source_binding or {})
        return record


def rational(value: Fraction) -> Record:
    return {'numerator': value.numerator, 'denominator': value.denominator}


def scheduled_record(node: ScheduledNode) -> Record:
    record: Record = {'node_id': node.identity, 'accepted_ns': rational(node.accepted_ns),
            'result_ready_ns': rational(node.result_ready_ns), 'resource_ready_ns': rational(node.resource_ready_ns),
            'accepted_cycle': node.accepted_cycle, 'evidence_id': node.evidence_id,
            'worker_intervals': [{'resource': sample.resource, 'worker_id': sample.worker_id,
                'duration_ns': rational(sample.duration_ns), 'source': sample.source, 'unit': sample.unit,
                'quantity': sample.quantity, 'measurement': sample.measurement} for sample in node.worker_intervals]}
    if node.request_available_ns is not None:
        record.update(request_available_ns=rational(node.request_available_ns),
                      port_offer_ns=rational(node.port_offer_ns) if node.port_offer_ns is not None else None,
                      final_scale_release_ns=rational(node.final_scale_release_ns)
                      if node.final_scale_release_ns is not None else None)
    return record


def schedule_source_binding(sqlite_source: Path | None = None) -> Record:
    from sim.cycle import execution_cli
    binding: Record = {'scheduler_sha256': sha256(Path(__file__)),
                       'execution_cli_sha256': sha256(Path(execution_cli.__file__))}
    if sqlite_source is not None:
        binding['scheduler_sqlite_sha256'] = sha256(sqlite_source)
    return binding


def validation_scope(provider: TimingProvider) -> str:
    if isinstance(provider, (DiagnosticStatefulProvider, StatefulSequenceProvider)):
        return provider.admission.validation_scope
    return provider.validation_scope


def validate_environment(scope: str, provider: TimingProvider, scenario: Scenario) -> None:
    ensure(scenario.npu_frequency_hz > 0, 'positive NPU frequency required')
    ensure(scenario.scope in ('SYNTHETIC', 'RECONSTRUCTED', 'STATEFUL_DIAGNOSTIC'), 'unknown schedule scope')
    if isinstance(provider, DiagnosticStatefulProvider):
        ensure(scenario.scope == 'STATEFUL_DIAGNOSTIC', 'stateful diagnostic provider requires diagnostic scenario')
    if isinstance(provider, StatefulSequenceProvider):
        ensure(scenario.scope == 'RECONSTRUCTED' and isinstance(provider.admission, ProductionStatefulAdmission),
               'typed production stateful admission and reconstructed scenario required')
    if scenario.scope == 'STATEFUL_DIAGNOSTIC':
        ensure(isinstance(provider, DiagnosticStatefulProvider) and
               type(provider.admission) is StatefulAdmission and
               provider.admission.production_admitted is False and
               provider.admission.profile == scenario.profile and
               len(provider.requests) == len(provider.admission.work_bindings) and
               scenario.clock_artifact is None and scope == 'BOUND_DATASET',
               'source-bound diagnostic provider, trace and test clock required')
    if scenario.scope == 'RECONSTRUCTED':
        if scenario.clock_artifact is None:
            raise ExecutionError('validated clock artifact required')
        from sim.cycle.execution_cycle_provider import CycleServiceProvider
        if isinstance(provider, StatefulSequenceProvider):
            ensure(isinstance(provider.admission, ProductionStatefulAdmission) and
                   provider.admission.profile == scenario.profile and
                   len(provider.admission.work_bindings) == len(provider.requests),
                   'stateful certificate profile/work coverage differs from schedule')
        elif isinstance(provider, CycleServiceProvider) and provider.admission is not None:
            ensure(provider.admission.profile == scenario.profile and
                   provider.admission.work_count == len(provider.requests),
                   'service certificate profile/work coverage differs from schedule')
        else:
            raise ExecutionError('validated current-source drained-sequence service certificate required')
        ensure(scope == 'BOUND_DATASET', 'real reconstruction requires bound dataset')
        from scripts.evaluation_clock import load_selection
        selection = load_selection(scenario.clock_artifact, scenario.profile)
        ensure(selection.frequency_hz == scenario.npu_frequency_hz, 'clock frequency mismatch')


def service_binding(provider: TimingProvider, scenario: Scenario) -> Record:
    if scenario.scope == 'SYNTHETIC':
        return {'scope': 'SYNTHETIC_ONLY'}
    if scenario.scope == 'STATEFUL_DIAGNOSTIC':
        if not isinstance(provider, DiagnosticStatefulProvider):
            raise ExecutionError('typed stateful diagnostic binding required')
        return {'scope': 'DIAGNOSTIC_STATEFUL_SEQUENCE', 'npu_frequency_hz': scenario.npu_frequency_hz,
                **asdict(provider.admission)}
    if isinstance(provider, StatefulSequenceProvider) and isinstance(provider.admission, ProductionStatefulAdmission):
        return {'scope': 'CURRENT_STATEFUL_SEQUENCE', 'npu_frequency_hz': scenario.npu_frequency_hz,
                **asdict(provider.admission)}
    from sim.cycle.execution_cycle_provider import CycleServiceProvider
    if not isinstance(provider, CycleServiceProvider) or provider.admission is None:
        raise ExecutionError('certified service binding required')
    return {'scope': 'CURRENT_CERTIFIED_SEQUENCE', **asdict(provider.admission)}


def _gate(inputs: ScheduleInputs, scenario: Scenario) -> None:
    validate_environment(inputs.ir.scope, inputs.npu_provider, scenario)
    cpu_ids = {node.service for node in inputs.ir.nodes if node.kind in (Kind.CPU, Kind.APPLICATION_CPU)}
    npu_ids = {node.service for node in inputs.ir.nodes if node.kind == Kind.NPU}
    ensure(cpu_ids == set(inputs.services.cpu), 'missing/extra CPU service')
    ensure(npu_ids == set(inputs.services.npu), 'missing/extra NPU service')
    if isinstance(inputs.npu_provider, (DiagnosticStatefulProvider, StatefulSequenceProvider)):
        ensure(npu_ids == {work.identity for work in inputs.npu_provider.requests},
               'scheduled NPU work does not equal bound trace work')
    for node in inputs.ir.nodes:
        if node.kind in (Kind.CPU, Kind.APPLICATION_CPU):
            ensure(node.kind != Kind.APPLICATION_CPU or
                   inputs.services.cpu[service_id(node)].source == 'APPLICATION_CPU',
                   'application sampling requires PoTal application source')
            ensure(set(node.resources) == {worker.resource for worker in inputs.services.cpu[service_id(node)].workers},
                   'CPU worker/resource mismatch')


class ServiceExecutor:
    def __init__(self, provider: TimingProvider, scenario: Scenario) -> None:
        self.provider = provider
        self.scenario = scenario
        self.resource_free: dict[ResourceId, Fraction] = {}
        self.cycle_ns = Fraction(1_000_000_000, scenario.npu_frequency_hz)

    def earliest(self, node: Node, now: Fraction) -> Fraction:
        available = max((self.resource_free.get(resource, Fraction(0)) for resource in node.resources), default=Fraction(0))
        ready = max(now, available)
        if node.kind == Kind.NPU and isinstance(self.provider, (DiagnosticStatefulProvider, StatefulSequenceProvider)):
            ready = max(ready, self.provider.previous_resource_cycle * self.cycle_ns)
        return ceil(ready / self.cycle_ns) * self.cycle_ns if node.kind == Kind.NPU else ready

    def execute(self, node: Node, services: Services, now: Fraction,
                request_available: Fraction | None = None) -> ScheduledNode:
        ensure(now == self.earliest(node, now), 'cannot reserve a future resource for unready work')
        accepted_cycle: int | None = None
        evidence: str | None = None
        workers: tuple[WorkerService, ...] = ()
        result_at = released_at = now
        port_offer: Fraction | None = None
        final_release: Fraction | None = None
        match node.kind:
            case Kind.CPU | Kind.APPLICATION_CPU:
                cpu = services.cpu[service_id(node)]
                ensure(node.kind != Kind.APPLICATION_CPU or cpu.source == 'APPLICATION_CPU',
                       'application sampling requires PoTal application source')
                ensure(set(node.resources) == {worker.resource for worker in cpu.workers}, 'CPU worker/resource mismatch')
                workers = cpu.workers
                for worker in workers:
                    self.resource_free[worker.resource] = now + worker.duration_ns
                result_at = released_at = max(self.resource_free[worker.resource] for worker in workers)
            case Kind.NPU:
                work = services.npu[service_id(node)]
                ensure(self.scenario.scope != 'RECONSTRUCTED' or work.profile == self.scenario.profile,
                       'NPU work/clock profile mismatch')
                offered_cycle = int(now / self.cycle_ns)
                if isinstance(self.provider, (DiagnosticStatefulProvider, StatefulSequenceProvider)):
                    window = self.provider.execute(work, offered_cycle)
                    accepted_cycle = window.accepted_cycle
                    port_offer = window.offered_cycle * self.cycle_ns
                    result_at = window.result_ready_cycle * self.cycle_ns
                    final_release = window.final_scale_release_cycle * self.cycle_ns
                    released_at = window.resource_ready_cycle * self.cycle_ns
                    evidence = window.admission.certificate_sha256
                else:
                    accepted_cycle = offered_cycle
                    service = self.provider.estimate(work, accepted_cycle)
                    result_at = now + service.result_ready_cycles * self.cycle_ns
                    released_at = now + service.resource_ready_cycles * self.cycle_ns
                    evidence = service.evidence_id
                for resource in node.resources:
                    self.resource_free[resource] = released_at
            case Kind.OP_ENTER | Kind.OP_EXIT | Kind.BARRIER | Kind.PUBLISH | Kind.FUNCTIONAL_EMULATION | Kind.WAIT | Kind.EXCLUDED:
                ensure(not node.resources and node.service is None, 'structural/excluded node has target service')
            case unreachable:
                assert_never(unreachable)
        accepted_at = accepted_cycle * self.cycle_ns if port_offer is not None and accepted_cycle is not None else now
        return ScheduledNode(node.identity, accepted_at, result_at, released_at, workers, accepted_cycle, evidence,
                             request_available if isinstance(self.provider, (DiagnosticStatefulProvider, StatefulSequenceProvider)) else None,
                             port_offer, final_release)


def complete_provider(provider: TimingProvider, scenario: Scenario) -> None:
    if isinstance(provider, (DiagnosticStatefulProvider, StatefulSequenceProvider)):
        provider.verify_complete()
        return
    if scenario.scope == 'RECONSTRUCTED':
        from sim.cycle.execution_cycle_provider import CycleServiceProvider
        ensure(isinstance(provider, CycleServiceProvider) and
               provider.completed == set(provider.requests),
               'scheduled NPU work does not equal bound trace work')


def schedule(inputs: ScheduleInputs, scenario: Scenario) -> ScheduleResult:
    source_binding = schedule_source_binding() if isinstance(inputs.npu_provider, (DiagnosticStatefulProvider, StatefulSequenceProvider)) else None
    _gate(inputs, scenario)
    pending = {node.identity: node for node in inputs.ir.nodes}
    completed: dict[NodeId, ScheduledNode] = {}
    engine = ServiceExecutor(inputs.npu_provider, scenario)
    now = Fraction(0)
    while pending:
        future: list[Fraction] = []
        started = False
        for node in sorted(pending.values(), key=lambda item: (item.order, item.identity)):
            if any(edge.node not in completed for edge in node.dependencies):
                continue
            ready = max((completed[edge.node].milestone(edge.milestone) for edge in node.dependencies), default=Fraction(0))
            earliest = engine.earliest(node, max(now, ready))
            if earliest > now:
                future.append(earliest)
                continue
            completed[node.identity] = engine.execute(node, inputs.services, now, ready)
            del pending[node.identity]
            started = True
            break
        if not started:
            ensure(bool(future), 'execution stalled: unresolved causal/resource dependency')
            now = min(future)
    complete_provider(inputs.npu_provider, scenario)
    return ScheduleResult(tuple(completed.values()), scenario.scope,
                          validation_scope(inputs.npu_provider), service_binding(inputs.npu_provider, scenario),
                          source_binding)
