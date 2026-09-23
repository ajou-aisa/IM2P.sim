from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from sim.cycle.cli import estimate_service
from sim.cycle.execution_ir import ServiceId, ensure
from sim.cycle.execution_services import NpuService, NpuWork
from sim.cycle.npu_trace import model_document, work_binding
from sim.cycle.npu_trace_integrity import read_records, start_trace
from sim.cycle.npu_trace_schema import Record, integer, object_value
from sim.cycle.production_sequence_certificate import CertifiedCase
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.service_certificate import ServiceAdmission, validate_service_certificate


@dataclass(frozen=True, slots=True)
class ReferenceMemoryScenario:
    timing: Record
    initial_scratchpad_half: int
    initial_accumulator_half: int


@dataclass(frozen=True, slots=True)
class BoundCycleRequest:
    work: NpuWork
    document: Record


class CycleServiceProvider:
    def __init__(self, library: Path, trace: Path, scenario: ReferenceMemoryScenario, *,
                 service_certificate: Path | None = None, base_certificate: Path | None = None,
                 run_certificate: Path | None = None) -> None:
        self.library = library
        self.library_sha256 = sha256(library)
        self.requests: dict[ServiceId, BoundCycleRequest] = {}
        self.timing = dict(scenario.timing)
        self.scratchpad_half = scenario.initial_scratchpad_half
        self.accumulator_half = scenario.initial_accumulator_half
        ensure(type(self.scratchpad_half) is int and type(self.accumulator_half) is int and
               self.scratchpad_half in (0, 1) and self.accumulator_half in (0, 1),
               'invalid initial buffer halves')
        self.previous_resource_cycle = 0
        self.last_request_sha256: str | None = None
        self.invocations = 0
        self.completed: set[ServiceId] = set()
        records = read_records(trace)
        state = start_trace(records)
        for record in records:
            work = state.consume(record)
            if work is not None:
                identity = ServiceId('npu:' + str(work.identity))
                ensure(identity not in self.requests, 'duplicate trace work')
                bound = NpuWork(identity, work_binding(work), state.run.profile)
                self.requests[identity] = BoundCycleRequest(bound, model_document(state.run.profile, work))
        _ = state.summary()
        certificates = (service_certificate, base_certificate, run_certificate)
        ensure(all(path is None for path in certificates) or all(path is not None for path in certificates),
               'service, base and run certificates must be supplied together')
        validated = (
            validate_service_certificate(service_certificate, library, trace,
                base_certificate=base_certificate, run_certificate=run_certificate,
                timing=self.timing, initial_scratchpad_half=self.scratchpad_half,
                initial_accumulator_half=self.accumulator_half)
            if service_certificate is not None and base_certificate is not None and run_certificate is not None
            else None)
        self._certified_cases: tuple[CertifiedCase, ...] | None = None
        self.fixture_admission: ServiceAdmission | None
        self.admission: ServiceAdmission | None = None
        if isinstance(validated, tuple):
            admission, self._certified_cases = validated
            ensure(bool(self._certified_cases) and all(
                case.profile == admission.profile and
                case.period == self.timing['read_ready_period'] and
                len(case.works) == len(self.requests) for case in self._certified_cases),
                'certified sequence profile, timing or work coverage mismatch')
            self.fixture_admission = admission
            self.admission = admission
        else:
            self.fixture_admission = validated

    @property
    def validation_scope(self) -> str:
        if self.admission is not None:
            return 'CURRENT_CERTIFIED_SEQUENCE'
        return 'DRAINED_FIXTURE_PARITY' if self.fixture_admission is not None else 'DIAGNOSTIC_SERVICE_API'

    def estimate(self, work: NpuWork, accepted_cycle: int) -> NpuService:
        ensure(work.identity in self.requests and self.requests[work.identity].work == work,
               'service/trace profile or exact request binding mismatch')
        ensure(work.identity not in self.completed, 'duplicate scheduled NPU work')
        ensure(accepted_cycle >= self.previous_resource_cycle, 'next work accepted before resource ready')
        ensure(sha256(self.library) == self.library_sha256, 'cycle library changed during scheduling')
        candidates = self._certified_cases
        if candidates is not None:
            candidates = tuple(case for case in candidates if
                               (row := case.works[self.invocations]).work_id ==
                               int(str(work.identity).removeprefix('npu:')) and
                               row.work_binding == work.request_sha256 and
                               row.accepted_phase == accepted_cycle % case.period and
                               row.initial_scratchpad_half == self.scratchpad_half and
                               row.initial_accumulator_half == self.accumulator_half)
            ensure(bool(candidates), 'certified sequence work, phase or initial halves mismatch')
        original = self.requests[work.identity].document
        request = {**object_value(original['request']), 'accepted_cycle': accepted_cycle,
                   'initial_scratchpad_half': self.scratchpad_half,
                   'initial_accumulator_half': self.accumulator_half}
        document: Record = {**original, 'request': request, 'timing': self.timing}
        digest = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        result = estimate_service(self.library, document)
        service = object_value(result['service'])
        ready, resource = integer(service, 'result_ready_cycle'), integer(service, 'resource_ready_cycle')
        if candidates is not None:
            candidates = tuple(case for case in candidates if
                               (row := case.works[self.invocations]).result_ready_cycles == ready - accepted_cycle and
                               row.final_scale_release_cycles == integer(service, 'final_scale_release_cycle') - accepted_cycle and
                               row.resource_ready_cycles == resource - accepted_cycle and
                               row.next_scratchpad_half == integer(service, 'next_scratchpad_half') and
                               row.next_accumulator_half == integer(service, 'next_accumulator_half'))
            ensure(bool(candidates), 'certified sequence service result mismatch')
        certificate = ':' + self.fixture_admission.certificate_sha256 if self.fixture_admission is not None else ''
        boundary = NpuService(ready - accepted_cycle, resource - accepted_cycle,
                              self.validation_scope + ':' + self.library_sha256 + certificate + ':' + digest)
        self.scratchpad_half = integer(service, 'next_scratchpad_half')
        self.accumulator_half = integer(service, 'next_accumulator_half')
        self.previous_resource_cycle = resource
        self.last_request_sha256 = digest
        self.invocations += 1
        self.completed.add(work.identity)
        self._certified_cases = candidates
        return boundary
