from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
from enum import IntEnum
from typing import Final

from sim.cycle.cli import CompactRuns, Event, Hardware, Result, Timing

U32: Final = C.c_uint32
U64: Final = C.c_uint64
WORK_U64: Final = (
    "logical_work_id", "m", "n", "k", "tile_i", "tile_j", "tile_k",
    "activation_stride_bytes", "weight_stride_bytes", "output_stride_bytes", "scale_stride_elements",
)
_STATUS_U64: Final = (
    "generation", "cursor", "session_cycles", "work_cycles", "accepted_work_id",
    "offered_cycle", "accepted_cycle", "last_discarded_generation",
)
_STATUS_U32: Final = (
    "initialized", "has_pending", "has_active", "has_report", "has_accepted",
    "faulted", "next_scratchpad_half", "next_accumulator_half", "stop_reason",
)


class Code(IntEnum):
    OK = 0
    INCOMPLETE = 1
    WOULD_BLOCK = 2
    INVALID = -1
    UNSUPPORTED = -2
    OVERFLOW = -3
    LIMIT = -4
    INTERNAL = -5
    FAULTED = -6


class StopReason(IntEnum):
    NONE = 0
    TARGET = 1
    WORK_BUDGET = 2
    SESSION_BUDGET = 3
    FAULT = 4
    REPORT_BUFFER = 5
    EVENT_BUFFER_AVAILABLE = 6
    EVENT_BURST = 7
    REPORT_AVAILABLE = 8
    SOFT_BUDGET = 9


@dataclass(frozen=True, slots=True)
class SequenceError(Exception):
    code: Code
    operation: str

    def __str__(self) -> str:
        return f"{self.operation}: {self.code.name}"


def _uint(value: int, bits: int, name: str) -> int:
    if type(value) is not int or value < 0:
        raise SequenceError(Code.INVALID, name)
    if value >= 1 << bits:
        raise SequenceError(Code.OVERFLOW, name)
    return value


def _checked(raw: int, operation: str) -> Code:
    try:
        code = Code(raw)
    except ValueError as error:
        raise SequenceError(Code.INTERNAL, operation) from error
    if code.value < 0:
        raise SequenceError(code, operation)
    return code


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    profile: str
    library_sha256: str
    source_sha256: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class Settings:
    read_ready_period: int = 5
    max_work_cycles: int | None = None
    max_session_cycles: int | None = None
    max_fragments: int | None = None
    max_trace_events: int | None = None
    max_work_ids: int | None = None


@dataclass(frozen=True, slots=True)
class Run:
    original_block_id: int
    original_k_mask: int
    compact_k_begin: int
    compact_k_count: int


@dataclass(frozen=True, slots=True)
class Work:
    logical_work_id: int
    m: int
    n: int
    k: int
    tile_i: int = 1
    tile_j: int = 1
    tile_k: int = 1
    activation_stride_bytes: int = 0
    weight_stride_bytes: int = 0
    output_stride_bytes: int = 0
    scale_stride_elements: int = 0
    original_k: int | None = None
    runs: tuple[Run, ...] = ()
    submission: int = 0
    record_events: bool = False


class Config(C.Structure):
    _fields_ = [("abi_version", U32), ("struct_size", U32), ("hardware", Hardware),
                ("timing", Timing), ("max_work_cycles", U64), ("max_session_cycles", U64),
                ("max_fragments", U64), ("max_trace_events", U64),
                ("pending_capacity", U32), ("report_capacity", U32), ("max_work_ids", U64)]


class Descriptor(C.Structure):
    _fields_ = [("abi_version", U32), ("struct_size", U32)] + [
        (name, U64) for name in WORK_U64] + [("compact_runs", C.POINTER(CompactRuns)),
                                         ("submission", U32), ("record_events", U32)]


class Status(C.Structure):
    _fields_ = [("abi_version", U32), ("struct_size", U32)] + [
        (name, U64) for name in _STATUS_U64] + [(name, U32) for name in _STATUS_U32]


class TagState(C.Structure):
    _fields_ = [("abi_version", U32), ("struct_size", U32)] + [
        (name, U64) for name in ("generation", "cursor", "enqueues", "dequeues",
                                 "full_backpressure_cycles")] + [
        (name, U32) for name in ("queue_len", "head_valid", "head_id", "backpressure_mapped")]


class RowPressure(C.Structure):
    _fields_ = [("abi_version", U32), ("struct_size", U32), ("generation", U64),
                ("cursor", U64), ("row_count", U32), ("max_row_occupancy", U32)]


class DomainRow(C.Structure):
    _fields_ = [("id", U32), ("rows", U32)]


class DomainTag(C.Structure):
    _fields_ = [(name, U64) for name in ("origin_generation", "origin_ordinal", "origin_work_id")] + [
        (name, U32) for name in ("id", "rows", "rob_valid", "rob_id", "preload_src",
                                 "preload_dst", "preload_output_rows", "preload_output_cols",
                                 "preload_accumulate")]


class DomainBank(C.Structure):
    _fields_ = [(name, U32) for name in ("pending", "queued", "pipe_valid_mask")]


class DomainSnapshot(C.Structure):
    _fields_ = [("abi_version", U32), ("struct_size", U32), ("generation", U64),
                ("cursor", U64)] + [
        (name, U32) for name in ("resource_ready", "row_count", "tag_count",
                                 "max_tag_occupancy", "ready_violation_mask")] + [
        ("rows", DomainRow * 6), ("tags", DomainTag * 6), ("banks", DomainBank * 4)]


class Report(C.Structure):
    _fields_ = [("abi_version", U32), ("struct_size", U32)] + [
        (name, U64) for name in ("generation", "logical_work_id", "offered_cycle", "accepted_cycle",
                                 "result_ready_cycle", "final_scale_release_cycle", "resource_ready_cycle",
                                 "event_count")] + [("next_scratchpad_half", U32),
                                                    ("next_accumulator_half", U32), ("counters", Result)]


class SequenceEvent(C.Structure):
    _fields_ = [("abi_version", U32), ("struct_size", U32), ("generation", U64),
                ("work_ordinal", U64), ("submission", U32), ("reserved", U32), ("event", Event)]


class Diagnostic(C.Structure):
    _fields_ = [("abi_version", U32), ("struct_size", U32), ("code", C.c_int32),
                ("stop_reason", U32), ("last_good_cycle", U64), ("active_work_id", U64),
                ("work_cycles", U64), ("has_active", U32), ("reserved", U32),
                ("partial_counters", Result), ("message", C.c_char * 128)]


def bind(lib: C.CDLL) -> None:
    signatures = {
        "config_init": ([C.POINTER(Config)], None),
        "descriptor_init": ([C.POINTER(Descriptor)], None),
        "status_init": ([C.POINTER(Status)], None),
        "tag_state_init": ([C.POINTER(TagState)], None),
        "row_pressure_init": ([C.POINTER(RowPressure)], None),
        "domain_snapshot_init": ([C.POINTER(DomainSnapshot)], None),
        "report_init": ([C.POINTER(Report)], None),
        "error_init": ([C.POINTER(Diagnostic)], None),
        "create": ([C.POINTER(Config), C.POINTER(C.c_void_p)], C.c_int),
        "destroy": ([C.c_void_p], None),
        "reset": ([C.c_void_p], C.c_int),
        "offer": ([C.c_void_p, C.POINTER(Descriptor), U64], C.c_int),
        "advance_until": ([C.c_void_p, U64], C.c_int),
        "advance_to_boundary": ([C.c_void_p, U64, U64], C.c_int),
        "get_status": ([C.c_void_p, C.POINTER(Status)], C.c_int),
        "get_tag_state": ([C.c_void_p, C.POINTER(TagState)], C.c_int),
        "get_row_pressure": ([C.c_void_p, C.POINTER(RowPressure)], C.c_int),
        "get_domain_snapshot": ([C.c_void_p, C.POINTER(DomainSnapshot)], C.c_int),
        "get_error": ([C.c_void_p, C.POINTER(Diagnostic)], C.c_int),
        "pop_report": ([C.c_void_p, C.POINTER(Report)], C.c_int),
        "read_events": ([C.c_void_p, C.POINTER(SequenceEvent), U64, C.POINTER(U64)], C.c_int),
        "get_cumulative_counters": ([C.c_void_p, C.POINTER(Result)], C.c_int),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(lib, "im2p_cycle_sequence_" + name)
        function.argtypes, function.restype = arguments, result
