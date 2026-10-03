from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
import json
from typing import Protocol

from sim.cycle.execution_ir import ResourceId, ServiceId, ensure
from sim.cycle.npu_trace_schema import Record, integer, object_value, text
from sim.cycle.reconstruct_graph import array, fields


@dataclass(frozen=True, slots=True)
class WorkerService:
    resource: ResourceId
    duration_ns: Fraction
    worker_id: int | None
    source: str
    unit: str
    quantity: int
    frequency_hz: int | None = None
    measurement: Record = field(default_factory=dict)
    resource_identity: str | None = None


@dataclass(frozen=True, slots=True)
class CpuService:
    source: str
    policy: str
    workers: tuple[WorkerService, ...]

    def __post_init__(self) -> None:
        ensure(self.source in ('FULL_CPU', 'POTAL_COLLECTION', 'APPLICATION_CPU'), 'unknown CPU source')
        ensure(bool(self.workers), 'empty worker vector')
        ensure(len({sample.resource for sample in self.workers}) == len(self.workers), 'duplicate worker resource')
        ensure(all(sample.duration_ns >= 0 for sample in self.workers), 'negative CPU service')


@dataclass(frozen=True, slots=True)
class NpuWork:
    identity: ServiceId
    request_sha256: str
    profile: str


@dataclass(frozen=True, slots=True)
class NpuService:
    result_ready_cycles: int
    resource_ready_cycles: int
    evidence_id: str

    def __post_init__(self) -> None:
        ensure(0 < self.result_ready_cycles <= self.resource_ready_cycles, 'invalid result/resource boundary')
        ensure(bool(self.evidence_id), 'missing NPU timing evidence identity')


class NpuProvider(Protocol):
    @property
    def validation_scope(self) -> str: ...

    def estimate(self, work: NpuWork, accepted_cycle: int) -> NpuService: ...


@dataclass(frozen=True, slots=True)
class PhaseTable:
    period: int
    samples: dict[tuple[str, str, int], NpuService]
    validation_scope: str = field(default='SYNTHETIC_ONLY', init=False)

    def estimate(self, work: NpuWork, accepted_cycle: int) -> NpuService:
        ensure(self.period > 0, 'invalid acceptance-phase period')
        key = (work.profile, work.request_sha256, accepted_cycle % self.period)
        ensure(key in self.samples, 'missing exact work/acceptance-phase service boundary')
        return self.samples[key]


@dataclass(frozen=True, slots=True)
class Services:
    cpu: dict[ServiceId, CpuService]
    npu: dict[ServiceId, NpuWork]


def cpu_resource_keys(sample: Record, source: str) -> tuple[str, ...]:
    ensure(source in ('FULL_CPU', 'POTAL_COLLECTION', 'APPLICATION_CPU'), 'unknown CPU source identity')
    if source == 'FULL_CPU':
        return ('FULL_CPU:' + str(integer(sample, 'worker_id')),)
    keys: list[str] = []
    if sample.get('worker_id') is not None:
        keys.append(source + ':' + str(integer(sample, 'worker_id')))
    if sample.get('host_execution_id') is not None or sample.get('thread_id') is not None:
        keys.append(json.dumps([source, 'host_thread', text(sample, 'host_execution_id'), integer(sample, 'thread_id')],
                               separators=(',', ':')))
    ensure(bool(keys), 'missing source-local CPU identity')
    return tuple(keys)


def bind_cpu_resource(sample: Record, source: str, mapping: Record) -> Record:
    keys = cpu_resource_keys(sample, source)
    selected = [key for key in keys if key in mapping]
    ensure(bool(selected), 'missing explicit CPU resource mapping: ' + ','.join(keys))
    ensure(len(selected) == 1, 'ambiguous explicit CPU resource mapping: ' + ','.join(selected))
    identity = selected[0]
    return {**sample, 'resource': text(mapping, identity), 'resource_identity': identity}


def cpu_service(record: Record) -> CpuService:
    fields(record, {'source', 'policy', 'workers'})
    source, policy = text(record, 'source'), text(record, 'policy')
    ensure(policy in ('THREAD_CPU_NS_GANG', 'HOST_ELAPSED_NS_GANG', 'CPU_WORK_CYCLES_GANG'), 'unknown CPU service policy')
    metric, validity, unit = {
        'THREAD_CPU_NS_GANG': ('thread_cpu_ns', 'thread_cpu_valid', 'nanosecond'),
        'HOST_ELAPSED_NS_GANG': ('host_elapsed_ns', 'host_elapsed_valid', 'nanosecond'),
        'CPU_WORK_CYCLES_GANG': ('cpu_work_cycles', 'cpu_work_cycles_valid', 'cycle'),
    }[policy]
    samples: list[WorkerService] = []
    for raw in array(record['workers']):
        sample = object_value(raw)
        ensure(sample.get(validity) is True, 'selected CPU metric invalid; substitution forbidden')
        quantity = integer(sample, metric)
        frequency = 1_000_000_000
        if unit == 'cycle':
            ensure(sample.get('cpu_work_cycles_unit') == 'cycle', 'invalid CPU cycle unit')
            frequency = integer(sample, 'cpu_frequency_hz')
            ensure(frequency > 0, 'CPU cycle conversion requires explicit validated frequency')
        clock_source = {'THREAD_CPU_NS_GANG': 'thread_cpu_clock', 'HOST_ELAPSED_NS_GANG': 'steady_clock',
                        'CPU_WORK_CYCLES_GANG': sample.get('cpu_work_cycles_source')}[policy]
        ensure(clock_source in ('thread_cpu_clock', 'steady_clock', 'riscv_cycle', 'linux_perf_cpu_cycles'),
               'unknown selected CPU timing source')
        worker_id = integer(sample, 'worker_id') if sample.get('worker_id') is not None else None
        identity = text(sample, 'resource_identity') if sample.get('resource_identity') is not None else None
        if identity is not None:
            ensure(identity in cpu_resource_keys(sample, source), 'CPU source identity binding mismatch')
        ensure(worker_id is not None or (source != 'FULL_CPU' and identity is not None),
               'host-thread service requires explicit source identity; ggml worker index unavailable')
        samples.append(WorkerService(ResourceId(text(sample, 'resource')), Fraction(quantity * 1_000_000_000, frequency),
                                     worker_id, str(clock_source), unit, quantity,
                                     frequency if unit == 'cycle' else None, dict(sample), identity))
    return CpuService(source, policy, tuple(samples))


def parse_services(document: Record) -> Services:
    fields(document, {'schema', 'version', 'cpu', 'npu'})
    ensure(document['schema'] == 'im2p-execution-services' and integer(document, 'version') == 1,
           'unsupported service schema/version')
    cpu = {ServiceId(key): cpu_service(object_value(value)) for key, value in object_value(document['cpu']).items()}
    npu: dict[ServiceId, NpuWork] = {}
    for key, value in object_value(document['npu']).items():
        row = object_value(value)
        fields(row, {'request_sha256', 'profile'})
        npu[ServiceId(key)] = NpuWork(ServiceId(key), text(row, 'request_sha256'), text(row, 'profile'))
    ensure(not set(cpu) & set(npu), 'ambiguous service identity')
    return Services(cpu, npu)


def services_record(services: Services) -> Record:
    cpu: Record = {}
    for identity, service in services.cpu.items():
        metric, validity = {'THREAD_CPU_NS_GANG': ('thread_cpu_ns', 'thread_cpu_valid'),
                            'HOST_ELAPSED_NS_GANG': ('host_elapsed_ns', 'host_elapsed_valid'),
                            'CPU_WORK_CYCLES_GANG': ('cpu_work_cycles', 'cpu_work_cycles_valid')}[service.policy]
        workers: list[Record] = []
        for sample in service.workers:
            row: Record = {**sample.measurement, 'resource': sample.resource,
                           metric: sample.quantity, validity: True}
            if sample.worker_id is not None or 'worker_id' in sample.measurement:
                row['worker_id'] = sample.worker_id
            if sample.resource_identity is not None:
                row['resource_identity'] = sample.resource_identity
            row.setdefault('source', sample.source)
            if sample.unit == 'cycle':
                ensure(sample.frequency_hz is not None, 'CPU clock provenance required')
                row.update(cpu_work_cycles_unit='cycle', cpu_work_cycles_source=sample.source,
                           cpu_frequency_hz=sample.frequency_hz)
            workers.append(row)
        cpu[identity] = {'source': service.source, 'policy': service.policy, 'workers': [row for row in workers]}
    return {'schema': 'im2p-execution-services', 'version': 1, 'cpu': cpu,
            'npu': {identity: {'profile': work.profile, 'request_sha256': work.request_sha256}
                    for identity, work in services.npu.items()}}
