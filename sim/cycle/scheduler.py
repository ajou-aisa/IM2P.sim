from __future__ import annotations

from dataclasses import asdict, dataclass
from fractions import Fraction
from math import ceil
from pathlib import Path
from typing import assert_never

from sim.cycle.execution_ir import ExecutionError, ExecutionIR, Kind, Milestone, Node, NodeId, ResourceId, ensure, service_id
from sim.cycle.execution_services import NpuProvider, Services, WorkerService
from sim.cycle.npu_trace_schema import Record


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
    npu_provider: NpuProvider


@dataclass(frozen=True, slots=True)
class ScheduledNode:
    identity: NodeId
    accepted_ns: Fraction
    result_ready_ns: Fraction
    resource_ready_ns: Fraction
    worker_intervals: tuple[WorkerService, ...]
    accepted_cycle: int | None = None
    evidence_id: str | None = None

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

    @property
    def by_id(self) -> dict[NodeId, ScheduledNode]:
        return {node.identity: node for node in self.nodes}

    def record(self) -> Record:
        return {'schema': 'im2p-execution-schedule', 'version': 1, 'scope': self.scope,
                'service_validation_scope': self.service_validation_scope,
                'service_binding': self.service_binding,
                'time_unit': 'nanosecond-rational', 'queue_policy': 'READY_ORDER_THEN_ID',
                'clock_alignment': 'CEIL_TO_NPU_EDGE', 'worker_policy': 'EXPLICIT_GANG_VECTOR',
                'paper_latency_ready': False,
                'validated_service_reconstruction': self.scope == 'RECONSTRUCTED',
                'nodes': [scheduled_record(node) for node in self.nodes]}


def rational(value: Fraction) -> Record:
    return {'numerator': value.numerator, 'denominator': value.denominator}


def scheduled_record(node: ScheduledNode) -> Record:
    return {'node_id': node.identity, 'accepted_ns': rational(node.accepted_ns),
            'result_ready_ns': rational(node.result_ready_ns), 'resource_ready_ns': rational(node.resource_ready_ns),
            'accepted_cycle': node.accepted_cycle, 'evidence_id': node.evidence_id,
            'worker_intervals': [{'resource': sample.resource, 'worker_id': sample.worker_id,
                'duration_ns': rational(sample.duration_ns), 'source': sample.source, 'unit': sample.unit,
                'quantity': sample.quantity, 'measurement': sample.measurement} for sample in node.worker_intervals]}


def validate_environment(scope: str, provider: NpuProvider, scenario: Scenario) -> None:
    ensure(scenario.npu_frequency_hz > 0, 'positive NPU frequency required')
    ensure(scenario.scope in ('SYNTHETIC', 'RECONSTRUCTED'), 'unknown schedule scope')
    if scenario.scope == 'RECONSTRUCTED':
        if scenario.clock_artifact is None:
            raise ExecutionError('validated clock artifact required')
        from sim.cycle.execution_cycle_provider import CycleServiceProvider
        if not isinstance(provider, CycleServiceProvider) or provider.admission is None:
            raise ExecutionError('validated current-source drained-sequence service certificate required')
        ensure(scope == 'BOUND_DATASET', 'real reconstruction requires bound dataset')
        ensure(provider.admission.profile == scenario.profile and
               provider.admission.work_count == len(provider.requests),
               'service certificate profile/work coverage differs from schedule')
        from scripts.evaluation_clock import load_selection
        selection = load_selection(scenario.clock_artifact, scenario.profile)
        ensure(selection.frequency_hz == scenario.npu_frequency_hz, 'clock frequency mismatch')


def service_binding(provider: NpuProvider, scenario: Scenario) -> Record:
    if scenario.scope == 'SYNTHETIC':
        return {'scope': 'SYNTHETIC_ONLY'}
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
    for node in inputs.ir.nodes:
        if node.kind in (Kind.CPU, Kind.APPLICATION_CPU):
            ensure(node.kind != Kind.APPLICATION_CPU or
                   inputs.services.cpu[service_id(node)].source == 'APPLICATION_CPU',
                   'application sampling requires PoTal application source')
            ensure(set(node.resources) == {worker.resource for worker in inputs.services.cpu[service_id(node)].workers},
                   'CPU worker/resource mismatch')


class ServiceExecutor:
    def __init__(self, provider: NpuProvider, scenario: Scenario) -> None:
        self.provider = provider
        self.scenario = scenario
        self.resource_free: dict[ResourceId, Fraction] = {}
        self.cycle_ns = Fraction(1_000_000_000, scenario.npu_frequency_hz)

    def earliest(self, node: Node, now: Fraction) -> Fraction:
        available = max((self.resource_free.get(resource, Fraction(0)) for resource in node.resources), default=Fraction(0))
        ready = max(now, available)
        return ceil(ready / self.cycle_ns) * self.cycle_ns if node.kind == Kind.NPU else ready

    def execute(self, node: Node, services: Services, now: Fraction) -> ScheduledNode:
        ensure(now == self.earliest(node, now), 'cannot reserve a future resource for unready work')
        accepted_cycle: int | None = None
        evidence: str | None = None
        workers: tuple[WorkerService, ...] = ()
        result_at = released_at = now
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
                accepted_cycle = int(now / self.cycle_ns)
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
        return ScheduledNode(node.identity, now, result_at, released_at, workers, accepted_cycle, evidence)


def schedule(inputs: ScheduleInputs, scenario: Scenario) -> ScheduleResult:
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
            completed[node.identity] = engine.execute(node, inputs.services, now)
            del pending[node.identity]
            started = True
            break
        if not started:
            ensure(bool(future), 'execution stalled: unresolved causal/resource dependency')
            now = min(future)
    if scenario.scope == 'RECONSTRUCTED':
        from sim.cycle.execution_cycle_provider import CycleServiceProvider
        ensure(isinstance(inputs.npu_provider, CycleServiceProvider) and
               inputs.npu_provider.completed == set(inputs.npu_provider.requests),
               'scheduled NPU work does not equal bound trace work')
    return ScheduleResult(tuple(completed.values()), scenario.scope,
                          inputs.npu_provider.validation_scope, service_binding(inputs.npu_provider, scenario))
