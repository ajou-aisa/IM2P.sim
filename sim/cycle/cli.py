#!/usr/bin/env python3
"""Estimate one value-free GEMM using the separate C API and existing profile resolver.

This command never compiles or invokes RTL. It accepts no tensor data and never
reads a cycle golden, frequency, model layer, or system-level operation trace.
"""
from __future__ import annotations

import argparse
import ctypes as C
import json
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.gemmini_resolve_profile import (
    BuildFailure, DEFAULT_CATALOG, ProfileSelection, Scu, resolve_profile,
)

U32, U64 = C.c_uint32, C.c_uint64
HARDWARE_FIELDS = ('activation_bits', 'weight_bits', 'dim', 'block_k', 'accumulator_bits',
    'bank_count', 'bank_rows', 'accumulator_rows', 'scratchpad_row_bytes',
    'accumulator_row_bytes', 'scratchpad_read_delay', 'accumulator_latency')
TIMING_FIELDS = ('revision', 'backing_read_delay', 'even_read_id_delay', 'scale_read_extra_delay',
    'backing_write_delay', 'read_ready_period', 'backing_cycle_offset', 'reserved')
REQUEST_U64 = ('m', 'n', 'k', 'tile_i', 'tile_j', 'tile_k', 'activation_stride_bytes',
    'weight_stride_bytes', 'output_stride_bytes', 'scale_stride_elements', 'accepted_cycle', 'logical_work_id')
REQUEST_U32 = ('submission', 'record_events', 'initial_scratchpad_half', 'initial_accumulator_half')
RESULT_FIELDS = ('start_cycle', 'done_cycle', 'total_cycles', 'logical_work_count', 'loop_count',
    'planner_loop_count', 'fragment_count', 'load_request_count', 'load_response_count',
    'store_request_count', 'store_response_count', 'scale_request_count', 'scale_response_count', 'event_count')
EVENT_U64 = ('id', 'cycle', 'logical_work_id', 'loop', 'fragment', 'dependency', 'detail')
EVENT_U32 = ('type', 'resource', 'tile_i', 'tile_j', 'tile_k', 'reserved')


class Hardware(C.Structure):
    _fields_ = [(name, U32) for name in HARDWARE_FIELDS]


class Timing(C.Structure):
    _fields_ = [(name, U32) for name in TIMING_FIELDS]


class Config(C.Structure):
    _fields_ = [('abi_version', U32), ('struct_size', U32), ('hardware', Hardware), ('timing', Timing),
                ('max_cycles', U64), ('max_fragments', U64), ('max_trace_events', U64)]


class Request(C.Structure):
    _fields_ = [('abi_version', U32), ('struct_size', U32)] + [(name, U64) for name in REQUEST_U64] + [
        (name, U32) for name in REQUEST_U32]


class Result(C.Structure):
    _fields_ = [('abi_version', U32), ('struct_size', U32)] + [(name, U64) for name in RESULT_FIELDS]


class Event(C.Structure):
    _fields_ = [(name, U64) for name in EVENT_U64] + [(name, U32) for name in EVENT_U32]


def object_fields(value: Any, allowed: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ValueError(f'{label} must be a JSON object')
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f'unknown {label} fields: {sorted(unknown)}')
    return value


def integer(value: Any, bits: int, label: str) -> int:
    if type(value) is not int or value < 0 or value >= 1 << bits:
        raise ValueError(f'{label} must be an unsigned {bits}-bit integer')
    return value


def load_library(path: Path) -> C.CDLL:
    lib = C.CDLL(str(path.resolve(strict=True)))
    signatures = {
        'im2p_cycle_model_config_init': ([C.POINTER(Config)], None),
        'im2p_cycle_request_init': ([C.POINTER(Request)], None),
        'im2p_cycle_model_create': ([C.POINTER(Config)], C.c_void_p),
        'im2p_cycle_model_destroy': ([C.c_void_p], None),
        'im2p_cycle_estimate': ([C.c_void_p, C.POINTER(Request), C.POINTER(Result)], C.c_int),
        'im2p_cycle_model_error': ([C.c_void_p], C.c_char_p),
        'im2p_cycle_model_event_count': ([C.c_void_p], U64),
        'im2p_cycle_model_event': ([C.c_void_p, U64, C.POINTER(Event)], C.c_int),
        'im2p_cycle_event_name': ([U32], C.c_char_p),
        'im2p_cycle_resource_name': ([U32], C.c_char_p),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(lib, name)
        function.argtypes, function.restype = arguments, result
    return lib


def estimate(library: Path, document: Any, catalog: Path = DEFAULT_CATALOG,
             memory_contract: Path | None = None) -> dict[str, Any]:
    doc = object_fields(document, {'profile', 'timing_profile', 'timing', 'request', 'limits'}, 'input')
    name = doc.get('profile')
    match = re.fullmatch(r'a([48])w\1-d(16|32|64)-hp1', name) if isinstance(name, str) else None
    if match is None:
        raise ValueError('profile must name a supported matched Gemmini HP1 profile')
    if doc.get('timing_profile', 'rtl-regression') != 'rtl-regression':
        raise ValueError('unsupported timing profile')
    selection = ProfileSelection(int(match[1]), int(match[1]), int(match[2]), Scu.HP1_LEFT_SHIFT)
    contract = memory_contract or ROOT / 'config/gemmini_host_memory_contracts' / (selection.name + '.json')
    resolved = resolve_profile(selection, catalog, contract)
    memory, hardware = resolved.memory, resolved.catalog
    lib = load_library(library)
    cfg, request = Config(), Request()
    lib.im2p_cycle_model_config_init(C.byref(cfg))
    lib.im2p_cycle_request_init(C.byref(request))
    if cfg.struct_size != C.sizeof(cfg) or request.struct_size != C.sizeof(request):
        raise ValueError('C library and Python binding ABI differ')
    cfg.hardware = Hardware(selection.activation_bits, selection.weight_bits, selection.dim,
        hardware.block_size, hardware.accumulator_bits, memory.bank_count, memory.bank_rows,
        memory.accumulator_rows, memory.scratchpad_row_bytes, memory.accumulator_row_bytes,
        hardware.scratchpad_read_delay, hardware.accumulator_latency)
    for key, value in object_fields(doc.get('timing', {}), {key for key in TIMING_FIELDS if key not in ('reserved', 'revision')}, 'timing').items():
        setattr(cfg.timing, key, integer(value, 32, key))
    for key, value in object_fields(doc.get('limits', {}), {'max_cycles', 'max_fragments', 'max_trace_events'}, 'limits').items():
        setattr(cfg, key, integer(value, 64, key))
    data = object_fields(doc.get('request'), set(REQUEST_U64 + REQUEST_U32), 'request')
    if not {'m', 'n', 'k'}.issubset(data):
        raise ValueError('m, n, k are required')
    for key, value in data.items():
        if key == 'submission':
            names = {'planner-blocks': 0, 'regression-tiles': 1}
            if not isinstance(value, str) or value not in names:
                raise ValueError('submission must be planner-blocks or regression-tiles')
            value = names[value]
        setattr(request, key, integer(value, 64 if key in REQUEST_U64 else 32, key))
    handle = lib.im2p_cycle_model_create(C.byref(cfg))
    if not handle:
        raise ValueError('C API rejected the resolved timing/hardware configuration')
    try:
        result = Result()
        status = lib.im2p_cycle_estimate(handle, C.byref(request), C.byref(result))
        if status:
            message = lib.im2p_cycle_model_error(handle).decode('utf-8', errors='replace')
            raise ValueError(f'cycle model status {status}: {message}')
        trace = []
        for index in range(lib.im2p_cycle_model_event_count(handle)):
            event = Event()
            if lib.im2p_cycle_model_event(handle, index, C.byref(event)):
                raise RuntimeError('event retrieval failed')
            row = {key: getattr(event, key) for key in EVENT_U64 + EVENT_U32 if key != 'reserved'}
            # ABI v1 names this field `loop`, but the engine stores the serialized
            # hardware submission/frame index there. Keep the ABI field and expose
            # an unambiguous JSON alias for diagnostics.
            row['submission_index'] = row['loop']
            row['type'] = lib.im2p_cycle_event_name(event.type).decode('utf-8')
            row['resource'] = lib.im2p_cycle_resource_name(event.resource).decode('utf-8')
            trace.append(row)
        return {'status': 'PASS', 'classification': 'cycle model result', 'profile': name,
                'timing_profile': 'rtl-regression', 'value_free': True,
                'resolved_hardware': {key: getattr(cfg.hardware, key) for key in HARDWARE_FIELDS},
                'timing': {key: getattr(cfg.timing, key) for key in TIMING_FIELDS if key != 'reserved'},
                'result': {key: getattr(result, key) for key in RESULT_FIELDS}, 'events': trace}
    finally:
        lib.im2p_cycle_model_destroy(handle)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--request', type=Path, required=True, help='single-request JSON, never an op-trace')
    parser.add_argument('--catalog', type=Path, default=DEFAULT_CATALOG)
    parser.add_argument('--memory-contract', type=Path)
    parser.add_argument('--out', type=Path, help='new JSON file; default writes to stdout')
    args = parser.parse_args()
    try:
        if args.out is not None and args.out.exists():
            raise ValueError('output must be a new file')
        result = estimate(args.library, json.loads(args.request.read_text()), args.catalog, args.memory_contract)
        text = json.dumps(result, indent=2, sort_keys=True) + '\n'
        if args.out is None:
            print(text, end='')
        else:
            with args.out.open('x') as f:
                f.write(text)
        return 0
    except (ValueError, OSError, BuildFailure, RuntimeError) as error:
        print(f'CYCLE_MODEL_FAILED: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
