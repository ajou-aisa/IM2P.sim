from __future__ import annotations

import ctypes as C
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Final, Self

from sim.cycle import sequence_binding
from sim.cycle.execution_ir import ServiceId
from sim.cycle.execution_sequence_admission import (
    AdmissionInputs,
    BoundWork,
    StatefulAdmission,
    StatefulProviderError,
    admit_trace,
    verify_sources,
)
from sim.cycle.execution_services import NpuWork
from sim.cycle.sequence_binding import (
    Code,
    SequenceEvent,
    SequenceSession,
    Settings,
    StopReason,
)
from sim.cycle.sequence_binding_abi import U64
from sim.cycle.sequence_domain import TAG6_REVISION
from sim.cycle.stateful_domain import (
    A8D32_REVISION,
    ACTUAL_TRACE_REVISIONS,
    PROFILE_EXTENSION_REVISIONS,
    state_domain,
)
from sim.cycle.stateful_sequence_evidence import EvidenceContext

MAX_WORK_CYCLES: Final = 10_000_000
MAX_SESSION_CYCLES: Final = 1_000_000_000
EVENT_BUFFER_EVENTS: Final = 1 << 20
_DEFAULT_SETTINGS: Final = Settings()
# Receives raw native SequenceEvent records (bytes, count); observation cannot alter timing.
EventSink = Callable[[memoryview, int], None]


@dataclass(frozen=True, slots=True)
class StatefulWindow:
    work: NpuWork
    offered_cycle: int
    accepted_cycle: int
    result_ready_cycle: int
    final_scale_release_cycle: int
    resource_ready_cycle: int
    next_scratchpad_half: int
    next_accumulator_half: int
    max_tag_occupancy: int
    max_row_occupancy: int
    admission: StatefulAdmission


class _StatefulProvider:
    def __init__(self, inputs: AdmissionInputs, settings: Settings, *, production: bool,
                 event_sink: EventSink | None = None) -> None:
        self._inputs = inputs
        self._event_sink = event_sink
        self._admission, bound = admit_trace(inputs, production=production)
        self._domain = state_domain(self._admission.profile,
                                    self._admission.scoped.state_domain_revision).limits
        # The certified original trace's output projection exceeds the legacy
        # per-work watchdog. Keep the existing total-session watchdog unchanged.
        work_limit = (MAX_SESSION_CYCLES if self._admission.scoped.state_domain_revision == TAG6_REVISION
                      else MAX_WORK_CYCLES)
        if (settings.read_ready_period != 5 or
                (settings.max_work_cycles is not None and
                 not 0 < settings.max_work_cycles <= work_limit) or
                (settings.max_session_cycles is not None and
                 not 0 < settings.max_session_cycles <= MAX_SESSION_CYCLES) or
                (event_sink is not None and settings.max_trace_events is not None and
                 not 0 < settings.max_trace_events <= EVENT_BUFFER_EVENTS)):
            raise StatefulProviderError("reference memory", "unsupported period, cycle or event budget")
        event_capacity = (settings.max_trace_events or EVENT_BUFFER_EVENTS) if event_sink is not None else 0
        self._event_buffer = (SequenceEvent * event_capacity)() if event_capacity else None
        self._bound = bound
        self.requests = tuple(item.request for item in bound)
        self.completed: frozenset[ServiceId] = frozenset()
        self.invocations = 0
        self.previous_resource_cycle = 0
        self.scratchpad_half, self.accumulator_half = 0, 0
        self.faulted, self._closed = False, False
        limits = Settings(read_ready_period=5,
                          max_work_cycles=settings.max_work_cycles or work_limit,
                          max_session_cycles=settings.max_session_cycles or MAX_SESSION_CYCLES,
                          max_fragments=settings.max_fragments,
                          max_trace_events=event_capacity or settings.max_trace_events,
                          max_work_ids=settings.max_work_ids)
        self._session = SequenceSession(inputs.library, self.admission.profile, settings=limits,
                                        expected_identity=self.admission.source_identity)
        self._session.__enter__()
        cold_valid = False
        try:
            cold = self._session.domain_snapshot()
            if (cold.generation, cold.cursor, cold.resource_ready, cold.row_count,
                    cold.tag_count, cold.max_tag_occupancy,
                    cold.ready_violation_mask) != (1, 0, 1, 0, 0, 0, 0):
                raise StatefulProviderError("initial state", "native cold reset differs")
            cold_valid = True
        finally:
            if not cold_valid:
                self._session.close()

    @property
    def admission(self) -> StatefulAdmission:
        return self._admission

    @property
    def native_cursor(self) -> int:
        return self._session.status().cursor

    def __enter__(self) -> Self:
        if self._closed:
            raise StatefulProviderError("closed", "provider cannot reopen")
        return self

    def __exit__(self, _type: type[BaseException] | None, _value: BaseException | None,
                 _traceback: TracebackType | None) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._session.close()
            self._closed = True

    def _check_report(self, item: BoundWork, offered: int) -> StatefulWindow:
        status = self._session.status()
        domain = self._session.domain_snapshot()
        rows = self._session.row_pressure()
        report = self._session.pop_report()
        if not isinstance(report, sequence_binding.Report):
            raise StatefulProviderError("native report", "missing completed work")
        counts = report.counters
        if not (
            report.generation == status.generation == domain.generation == 1 and
            status.cursor == domain.cursor == report.resource_ready_cycle and
            status.has_report == domain.resource_ready == 1 and
            status.has_pending == status.has_active == status.faulted == 0 and
            report.logical_work_id == item.trace.identity and report.offered_cycle == offered and
            offered <= report.accepted_cycle < report.result_ready_cycle <=
            report.final_scale_release_cycle <= report.resource_ready_cycle and
            counts.logical_work_count == 1 and counts.start_cycle == report.accepted_cycle and
            counts.done_cycle == report.result_ready_cycle and
            counts.total_cycles == report.result_ready_cycle - report.accepted_cycle and
            counts.load_request_count == counts.load_response_count and
            counts.store_request_count == counts.store_response_count and
            counts.scale_request_count == counts.scale_response_count and
            domain.max_tag_occupancy <= self._domain.max_tag_occupancy and
            domain.ready_violation_mask == self._domain.ready_violation_mask and
            domain.tag_count <= 6 and all(tag.rob_valid == 0 for tag in domain.tags[:domain.tag_count]) and
            rows.generation == domain.generation and rows.cursor == domain.cursor and
            rows.row_count == domain.row_count and
            rows.row_count <= rows.max_row_occupancy < self._domain.max_row_occupancy_exclusive and
            (report.next_scratchpad_half, report.next_accumulator_half) ==
            (status.next_scratchpad_half, status.next_accumulator_half) and
            report.next_scratchpad_half in (0, 1) and report.next_accumulator_half in (0, 1)
        ):
            raise StatefulProviderError("post-transition domain", "native report or drained state outside W/S")
        return StatefulWindow(item.request, offered, report.accepted_cycle,
                              report.result_ready_cycle, report.final_scale_release_cycle,
                              report.resource_ready_cycle, report.next_scratchpad_half,
                              report.next_accumulator_half, domain.max_tag_occupancy,
                              rows.max_row_occupancy, self.admission)

    def execute(self, work: NpuWork, offered_cycle: int) -> StatefulWindow:
        if self.faulted:
            raise StatefulProviderError("FAULTED", "native transition already failed")
        if self._closed:
            raise StatefulProviderError("closed", "provider cannot execute")
        if self.invocations >= len(self._bound):
            raise StatefulProviderError("work order", "all declared trace works completed")
        item = self._bound[self.invocations]
        if work != item.request:
            raise StatefulProviderError("request binding", "duplicate, reordered, or changed work")
        status = self._session.status()
        if (type(offered_cycle) is not int or offered_cycle < status.cursor or
                offered_cycle < self.previous_resource_cycle or offered_cycle >= 1 << 64 or
                status.has_pending or status.has_active or status.has_report or status.faulted):
            raise StatefulProviderError("offer epoch", "offer precedes native cursor/resource or state is busy")
        if (self.admission.scoped.state_domain_revision in
                (TAG6_REVISION, A8D32_REVISION, *PROFILE_EXTENSION_REVISIONS, *ACTUAL_TRACE_REVISIONS) and
                offered_cycle != self.previous_resource_cycle):
            raise StatefulProviderError("Tag6 offer policy", "only certified back-to-back NPU offers are supported")
        verify_sources(self.admission, self._inputs, self._session, publication=False)
        domain, rows = self._session.domain_snapshot(), self._session.row_pressure()
        if not (
            status.generation == domain.generation == rows.generation == 1 and
            status.cursor == domain.cursor == rows.cursor == self.previous_resource_cycle and
            domain.resource_ready == 1 and domain.ready_violation_mask == self._domain.ready_violation_mask and
            domain.max_tag_occupancy <= self._domain.max_tag_occupancy and domain.tag_count <= 6 and
            all(tag.rob_valid == 0 for tag in domain.tags[:domain.tag_count]) and
            domain.row_count == rows.row_count <= rows.max_row_occupancy < self._domain.max_row_occupancy_exclusive and
            (status.next_scratchpad_half, status.next_accumulator_half) ==
            (self.scratchpad_half, self.accumulator_half) and
            self._session.counters().logical_work_count == self.invocations
        ):
            raise StatefulProviderError("pre-transition domain", "native state outside admitted W/S")
        trace = item.trace
        descriptor = sequence_binding.Work(trace.identity, *trace.inputs,
            original_k=trace.original_k,
            runs=tuple(sequence_binding.Run(run.original_block_id, run.original_k_mask,
                                            run.compact_k_begin, run.compact_k_count)
                       for run in trace.runs), submission=0, record_events=self._event_sink is not None)
        if self._session.offer(descriptor, offered_cycle) != Code.OK:
            raise StatefulProviderError("native offer", "work was not accepted into pending state")
        committed = False
        try:
            while True:
                code = self._session.advance_to_boundary(MAX_SESSION_CYCLES, 65_536)
                stop = self._session.status().stop_reason
                if code == Code.OK:
                    if stop != StopReason.REPORT_AVAILABLE:
                        raise StatefulProviderError("native advance", "report boundary missing")
                    break
                if (self._event_sink is not None and code == Code.WOULD_BLOCK and
                        stop == StopReason.EVENT_BUFFER_AVAILABLE):
                    self._drain_events()
                    continue
                if code != Code.INCOMPLETE or stop != StopReason.SOFT_BUDGET:
                    raise StatefulProviderError("native advance", f"unexpected {code.name}")
            window = self._check_report(item, offered_cycle)
            self._drain_events()
            self.completed = self.completed | {work.identity}
            self.invocations += 1
            self.previous_resource_cycle = window.resource_ready_cycle
            self.scratchpad_half = window.next_scratchpad_half
            self.accumulator_half = window.next_accumulator_half
            committed = True
            return window
        finally:
            if not committed:
                self.faulted = True

    def _drain_events(self) -> None:
        if self._event_sink is None or self._event_buffer is None:
            return
        lib, handle = self._session._native()
        capacity, count = len(self._event_buffer), U64()
        while True:
            code = Code(lib.im2p_cycle_sequence_read_events(handle, self._event_buffer, capacity, C.byref(count)))
            if code != Code.OK or count.value > capacity:
                raise StatefulProviderError("native events", f"event drain failed: {code.name}")
            if count.value == 0:
                return
            self._event_sink(memoryview(self._event_buffer).cast("B")[:count.value * C.sizeof(SequenceEvent)],
                             count.value)

    def verify_complete(self) -> None:
        if (self.faulted or self._closed or self.invocations != len(self._bound) or
                self.completed != frozenset(work.identity for work in self.requests)):
            raise StatefulProviderError("completion", "session faulted, closed, or trace work omitted")
        verified = False
        try:
            verify_sources(self.admission, self._inputs, self._session)
            status = self._session.status()
            if (status.has_pending or status.has_active or status.has_report or status.faulted or
                    self._session.counters().logical_work_count != self.invocations or
                    self._session.event_count() != 0):
                raise StatefulProviderError("completion", "native session has unfinished work")
            verified = True
        finally:
            if not verified:
                self.faulted = True


class DiagnosticStatefulProvider(_StatefulProvider):
    def __init__(self, library: Path, trace: Path, certificate: Path,
                 context: EvidenceContext, *, settings: Settings = _DEFAULT_SETTINGS) -> None:
        super().__init__(AdmissionInputs(library, trace, certificate, context), settings, production=False)


class StatefulSequenceProvider(_StatefulProvider):
    def __init__(self, library: Path, trace: Path, certificate: Path,
                 context: EvidenceContext, *, settings: Settings = _DEFAULT_SETTINGS,
                 event_sink: EventSink | None = None) -> None:
        super().__init__(AdmissionInputs(library, trace, certificate, context), settings, production=True,
                         event_sink=event_sink)
