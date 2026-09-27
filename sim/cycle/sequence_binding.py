from __future__ import annotations

import ctypes as C
import re
from pathlib import Path
from types import TracebackType
from typing import Final, Self

from scripts.gemmini_resolve_profile import (
    DEFAULT_CATALOG,
    ProfileSelection,
    Scu,
    resolve_profile,
)
from sim.cycle.cli import (
    HARDWARE_FIELDS,
    TIMING_FIELDS,
    CompactRun,
    CompactRuns,
    Hardware,
    Result,
)
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_binding_abi import (
    U64,
    WORK_U64,
    Code,
    Config,
    Descriptor,
    Diagnostic,
    DomainBank,
    DomainRow,
    DomainSnapshot,
    DomainTag,
    Report,
    RowPressure,
    Run,
    SequenceError,
    SequenceEvent,
    Settings,
    SourceIdentity,
    Status,
    TagState,
    Work,
    _uint,
    bind,
)
from sim.cycle.sequence_binding_abi import _checked as _checked_code

__all__ = ("Code", "Config", "Descriptor", "Diagnostic", "DomainBank", "DomainRow",
           "DomainSnapshot", "DomainTag", "Report", "RowPressure", "Run", "SequenceError", "SequenceEvent",
           "SequenceSession", "Settings", "SourceIdentity", "Status", "TagState", "Work",
           "source_identity")

ROOT: Final = Path(__file__).resolve().parents[2]
_SOURCE_PATHS: Final = (
    "sim/cycle/CMakeLists.txt", "sim/cycle/sequence_c_api.cpp", "sim/cycle/c_api.cpp",
    "sim/cycle/control_engine.cpp", "sim/cycle/control_engine.hpp", "sim/cycle/execute_engine.cpp",
    "sim/cycle/scheduled_work.cpp", "sim/cycle/scheduled_work.hpp", "sim/cycle/timing_profile.hpp",
    "sim/cycle/cycle_model.hpp", "sim/common/gemmini_schedule.cpp",
    "sim/include/im2p_cycle_sequence.h", "sim/include/im2p_cycle_model.h",
    "sim/include/im2p_compact_runs.h", "config/gemmini_hp1_profiles.json",
)
def source_identity(library: Path, profile: str) -> SourceIdentity:
    match = re.fullmatch(r"a([48])w\1-d(16|32|64)-hp1", profile)
    if match is None:
        raise SequenceError(Code.UNSUPPORTED, "profile")
    names = (*_SOURCE_PATHS, f"config/gemmini_host_memory_contracts/{profile}.json")
    return SourceIdentity(profile, sha256(library), tuple((name, sha256(ROOT / name)) for name in names))


class SequenceSession:
    def __init__(self, library: Path, profile: str, *, settings: Settings | None = None,
                 expected_identity: SourceIdentity | None = None) -> None:
        self.library = library
        self.profile = profile
        self._settings = settings if settings is not None else Settings()
        self.expected_identity = expected_identity
        self._identity: SourceIdentity | None = None
        self._hardware: tuple[int, ...] | None = None
        self._timing: tuple[int, ...] | None = None
        self._lib: C.CDLL | None = None
        self._handle = C.c_void_p()
        self._opened = False

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def identity(self) -> SourceIdentity | None:
        return self._identity

    @property
    def hardware(self) -> tuple[int, ...] | None:
        return self._hardware

    @property
    def timing(self) -> tuple[int, ...] | None:
        return self._timing

    def __enter__(self) -> Self:
        if self._opened:
            raise SequenceError(Code.INVALID, "session already open")
        identity = source_identity(self.library, self.profile)
        if self.expected_identity is not None and identity != self.expected_identity:
            raise SequenceError(Code.INVALID, "source/library identity changed")
        match = re.fullmatch(r"a([48])w\1-d(16|32|64)-hp1", self.profile)
        if match is None:
            raise SequenceError(Code.UNSUPPORTED, "profile")
        selection = ProfileSelection(int(match[1]), int(match[1]), int(match[2]), Scu.HP1_LEFT_SHIFT)
        resolved = resolve_profile(selection, DEFAULT_CATALOG,
            ROOT / "config/gemmini_host_memory_contracts" / f"{self.profile}.json")
        lib = C.CDLL(str(self.library.resolve(strict=True)))
        bind(lib)
        config = Config()
        lib.im2p_cycle_sequence_config_init(C.byref(config))
        if (config.abi_version, config.struct_size) != (1, C.sizeof(Config)):
            raise SequenceError(Code.UNSUPPORTED, "config ABI")
        memory, hardware = resolved.memory, resolved.catalog
        values = (selection.activation_bits, selection.weight_bits, selection.dim,
            hardware.block_size, hardware.accumulator_bits, memory.bank_count, memory.bank_rows,
            memory.accumulator_rows, memory.scratchpad_row_bytes, memory.accumulator_row_bytes,
            hardware.scratchpad_read_delay, hardware.accumulator_latency)
        config.hardware = Hardware(*(_uint(value, 32, name) for name, value in
                                     zip(HARDWARE_FIELDS, values, strict=True)))
        config.timing.read_ready_period = _uint(self.settings.read_ready_period, 32, "read_ready_period")
        for name in ("max_work_cycles", "max_session_cycles", "max_fragments",
                     "max_trace_events", "max_work_ids"):
            value = getattr(self.settings, name)
            if value is not None:
                setattr(config, name, _uint(value, 64, name))
        handle = C.c_void_p()
        self._checked(lib.im2p_cycle_sequence_create(C.byref(config), C.byref(handle)), "create")
        try:
            self._checked(lib.im2p_cycle_sequence_reset(handle), "reset")
        except SequenceError:
            lib.im2p_cycle_sequence_destroy(handle)
            raise
        self._lib, self._handle, self._identity = lib, handle, identity
        self._opened = True
        self._hardware = tuple(getattr(config.hardware, name) for name in HARDWARE_FIELDS)
        self._timing = tuple(getattr(config.timing, name) for name in TIMING_FIELDS)
        return self

    def __exit__(self, _type: type[BaseException] | None, _value: BaseException | None,
                 _traceback: TracebackType | None) -> None:
        self.close()

    def close(self) -> None:
        if self._lib is not None and self._handle.value:
            self._lib.im2p_cycle_sequence_destroy(self._handle)
        self._handle = C.c_void_p()
        self._lib = None

    def verify_identity(self) -> None:
        if self._identity is None or source_identity(self.library, self.profile) != self._identity:
            raise SequenceError(Code.INVALID, "publication identity changed")

    @staticmethod
    def _checked(raw: int, operation: str) -> Code:
        return _checked_code(raw, operation)

    def _native(self) -> tuple[C.CDLL, C.c_void_p]:
        if self._lib is None or not self._handle.value:
            raise SequenceError(Code.INVALID, "closed session")
        return self._lib, self._handle

    def reset(self) -> Code:
        lib, handle = self._native()
        return self._checked(lib.im2p_cycle_sequence_reset(handle), "reset")

    def offer(self, work: Work, offered_cycle: int) -> Code:
        lib, handle = self._native()
        descriptor = Descriptor()
        lib.im2p_cycle_sequence_descriptor_init(C.byref(descriptor))
        for name in WORK_U64:
            setattr(descriptor, name, _uint(getattr(work, name), 64, name))
        descriptor.submission = _uint(work.submission, 32, "submission")
        if type(work.record_events) is not bool:
            _uint(work.record_events, 32, "record_events")
            raise SequenceError(Code.INVALID, "record_events")
        descriptor.record_events = int(work.record_events)
        owned = None
        view = None
        if work.runs:
            if work.original_k is None:
                raise SequenceError(Code.INVALID, "original_k")
            fields = ("original_block_id", "original_k_mask", "compact_k_begin", "compact_k_count")
            spans = [CompactRun(*(_uint(getattr(run, field), 32, field) for field in fields))
                     for run in work.runs]
            owned = (CompactRun * len(spans))(*spans)
            view = CompactRuns(1, C.sizeof(CompactRuns), _uint(work.original_k, 32, "original_k"),
                               len(spans), owned)
            descriptor.compact_runs = C.pointer(view)
        elif work.original_k is not None:
            raise SequenceError(Code.INVALID, "runs")
        return self._checked(lib.im2p_cycle_sequence_offer(
            handle, C.byref(descriptor), _uint(offered_cycle, 64, "offered_cycle")), "offer")

    def advance_until(self, until_cycle: int) -> Code:
        lib, handle = self._native()
        return self._checked(lib.im2p_cycle_sequence_advance_until(
            handle, _uint(until_cycle, 64, "until_cycle")), "advance_until")

    def status(self) -> Status:
        lib, handle = self._native()
        status = Status()
        lib.im2p_cycle_sequence_status_init(C.byref(status))
        self._checked(lib.im2p_cycle_sequence_get_status(handle, C.byref(status)), "get_status")
        return status

    def tag_state(self) -> TagState:
        lib, handle = self._native()
        tags = TagState()
        lib.im2p_cycle_sequence_tag_state_init(C.byref(tags))
        self._checked(lib.im2p_cycle_sequence_get_tag_state(handle, C.byref(tags)), "get_tag_state")
        return tags

    def row_pressure(self) -> RowPressure:
        lib, handle = self._native()
        pressure = RowPressure()
        lib.im2p_cycle_sequence_row_pressure_init(C.byref(pressure))
        self._checked(lib.im2p_cycle_sequence_get_row_pressure(
            handle, C.byref(pressure)), "get_row_pressure")
        return pressure

    def domain_snapshot(self) -> DomainSnapshot:
        lib, handle = self._native()
        snapshot = DomainSnapshot()
        lib.im2p_cycle_sequence_domain_snapshot_init(C.byref(snapshot))
        self._checked(lib.im2p_cycle_sequence_get_domain_snapshot(
            handle, C.byref(snapshot)), "get_domain_snapshot")
        return snapshot

    def error(self) -> Diagnostic:
        lib, handle = self._native()
        error = Diagnostic()
        lib.im2p_cycle_sequence_error_init(C.byref(error))
        self._checked(lib.im2p_cycle_sequence_get_error(handle, C.byref(error)), "get_error")
        return error

    def counters(self) -> Result:
        lib, handle = self._native()
        counters = Result()
        counters.abi_version, counters.struct_size = 1, C.sizeof(Result)
        self._checked(lib.im2p_cycle_sequence_get_cumulative_counters(
            handle, C.byref(counters)), "get_counters")
        return counters

    def pop_report(self) -> Report | Code:
        lib, handle = self._native()
        report = Report()
        lib.im2p_cycle_sequence_report_init(C.byref(report))
        code = self._checked(lib.im2p_cycle_sequence_pop_report(handle, C.byref(report)), "pop_report")
        return report if code == Code.OK else code

    def event_count(self) -> int:
        lib, handle = self._native()
        count = U64()
        self._checked(lib.im2p_cycle_sequence_read_events(handle, None, 0, C.byref(count)), "event_count")
        return count.value

    def read_events(self, capacity: int) -> tuple[SequenceEvent, ...]:
        lib, handle = self._native()
        length = min(_uint(capacity, 64, "capacity"), self.event_count())
        if length == 0:
            return ()
        events = (SequenceEvent * length)()
        count = U64()
        self._checked(
            lib.im2p_cycle_sequence_read_events(handle, events, length, C.byref(count)),
            "read_events",
        )
        return tuple(events[: count.value])
