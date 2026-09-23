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
from sim.cycle.reconstruct_graph import sha256


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
    validation_scope = 'DIAGNOSTIC_SERVICE_API'

    def __init__(self, library: Path, trace: Path, scenario: ReferenceMemoryScenario) -> None:
        self.library = library
        self.library_sha256 = sha256(library)
        self.requests: dict[ServiceId, BoundCycleRequest] = {}
        self.timing = dict(scenario.timing)
        self.scratchpad_half = scenario.initial_scratchpad_half
        self.accumulator_half = scenario.initial_accumulator_half
        ensure(self.scratchpad_half in (0, 1) and self.accumulator_half in (0, 1), 'invalid initial buffer halves')
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

    def estimate(self, work: NpuWork, accepted_cycle: int) -> NpuService:
        ensure(work.identity in self.requests and self.requests[work.identity].work == work,
               'service/trace profile or exact request binding mismatch')
        ensure(work.identity not in self.completed, 'duplicate scheduled NPU work')
        ensure(accepted_cycle >= self.previous_resource_cycle, 'next work accepted before resource ready')
        ensure(sha256(self.library) == self.library_sha256, 'cycle library changed during scheduling')
        original = self.requests[work.identity].document
        request = {**object_value(original['request']), 'accepted_cycle': accepted_cycle,
                   'initial_scratchpad_half': self.scratchpad_half,
                   'initial_accumulator_half': self.accumulator_half}
        document: Record = {**original, 'request': request, 'timing': self.timing}
        digest = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        result = estimate_service(self.library, document)
        service = object_value(result['service'])
        ready, resource = integer(service, 'result_ready_cycle'), integer(service, 'resource_ready_cycle')
        boundary = NpuService(ready - accepted_cycle, resource - accepted_cycle,
                              self.validation_scope + ':' + self.library_sha256 + ':' + digest)
        self.scratchpad_half = integer(service, 'next_scratchpad_half')
        self.accumulator_half = integer(service, 'next_accumulator_half')
        self.previous_resource_cycle = resource
        self.last_request_sha256 = digest
        self.invocations += 1
        self.completed.add(work.identity)
        return boundary
