from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from heapq import nsmallest
from pathlib import Path
from typing import Final

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.npu_trace_schema import (
    NpuTraceError,
    Record,
    integer,
    object_value,
    unique_pairs,
)
from sim.cycle.reconstruct_graph import array
from sim.tests.cycle.compositional_sequence_v2_base import AbsoluteOfferError
from sim.tests.cycle.compositional_sequence_v2_payload import (
    PayloadGeometry,
    compare_payloads,
    payload_gap,
    payload_geometry,
    validate_payloads,
)

# ponytail: 300k selected rows/work bounds RAM; use external sorting if source-bound work exceeds it.
MAX_SELECTED_EVENTS_PER_WORK: Final = 300_000
TAG_LAYOUT_SHA256: Final = 'c1431c91515b9e0ef892934778b7ecc1fe9f9a489ee24b4ff3a686245a175610'
BOUNDARY_PHASES: Final = (('PUBLIC_READY', 0), ('WORK1_OFFER', 1), ('WORK1_FIRE', 1))
QUEUE_EDGE_LINE_CAP: Final = 2048
QUEUE_EDGE_BYTE_CAP: Final = 128 * 1024 * 1024
RTL_QUEUE_FIELDS: Final = ('row_read', 'row_write', 'row_empty', 'row_maybe_full',
                           'row_enq_fire', 'row_deq_fire', 'tag_read', 'tag_write',
                           'tag_enq_fire', 'tag_deq_fire', 'request_valid',
                           'last_response_fire')


def _event(line: bytes, kind: bytes, ordinal: int, work_id: int) -> tuple[int, bytes]:
    fields = line.split()
    if (len(fields) != 5 or fields[0] != kind or
            int(fields[1]) != ordinal or int(fields[2]) != work_id):
        raise AbsoluteOfferError(f'{kind.decode()} work order or ID differs at {ordinal}')
    cycle = int(fields[3])
    if cycle < 0:
        raise AbsoluteOfferError(f'{kind.decode()} negative cycle at {ordinal}')
    return cycle, fields[4]


def _work(line: bytes, prefix: bytes, ordinal: int, work_id: int) -> Record:
    row = object_value(json.loads(line.removeprefix(prefix),
                                  object_pairs_hook=unique_pairs))
    if integer(row, 'ordinal') != ordinal or integer(row, 'work_id') != work_id:
        raise AbsoluteOfferError(f'{prefix.decode().strip()} work order or ID differs at {ordinal}')
    return row


def _counts(declared: dict[int, int], actual: list[int], kind: str,
            required: bool) -> None:
    if required and set(declared) != set(range(len(actual))):
        raise AbsoluteOfferError(f'{kind} selected event count coverage missing')
    for ordinal, count in declared.items():
        if count != actual[ordinal]:
            raise AbsoluteOfferError(f'{kind} selected event count differs at work {ordinal}: '
                                     f'declared={count} parsed={actual[ordinal]}')


def _reject_json_constant(value: str) -> None:
    raise AbsoluteOfferError(f'boundary malformed JSON constant {value}')


def _rtl_queue_shape(row: Record, location: str) -> None:
    for field in ('row_read', 'row_write', 'tag_read', 'tag_write'):
        value = row.get(field)
        if type(value) is not int or not 0 <= value < 6:
            raise AbsoluteOfferError(f'{location} {field} malformed')
    for field in ('row_empty', 'row_maybe_full', 'row_enq_fire', 'row_deq_fire',
                  'tag_enq_fire', 'tag_deq_fire', 'request_valid', 'last_response_fire'):
        value = row.get(field)
        if type(value) is not int or value not in (0, 1):
            raise AbsoluteOfferError(f'{location} {field} malformed')
    row_read, row_write = integer(row, 'row_read'), integer(row, 'row_write')
    row_count, tag_count = integer(row, 'row_count'), integer(row, 'tag_count')
    decoded_rows = (row_write - row_read) % 6
    if row_read == row_write and integer(row, 'row_maybe_full'):
        decoded_rows = 6
    if row_count != decoded_rows:
        raise AbsoluteOfferError(f'{location} row_read/row_write occupancy differs')
    if integer(row, 'row_empty') != int(row_count == 0):
        raise AbsoluteOfferError(f'{location} row_empty occupancy differs')
    if integer(row, 'tag_write') != (integer(row, 'tag_read') + tag_count) % 6:
        raise AbsoluteOfferError(f'{location} tag_read/tag_write occupancy differs')
    row_enq = integer(row, 'row_enq_fire')
    if row_enq != integer(row, 'tag_enq_fire'):
        raise AbsoluteOfferError(f'{location} tag_enq_fire differs')
    if row_enq and (row_count == 6 or tag_count == 6):
        raise AbsoluteOfferError(f'{location} old-full enqueue fire')
    for field, count in (('row_deq_fire', row_count), ('tag_deq_fire', tag_count)):
        if integer(row, field) and (count == 0 or not integer(row, 'last_response_fire')):
            raise AbsoluteOfferError(f'{location} {field} differs')


def _boundary(line: bytes, kind: bytes, phase: str, ordinal: int, work_id: int,
              version: int) -> Record:
    if not line.startswith(kind + b' ') or len(line) > 1 << 20:
        raise AbsoluteOfferError(f'boundary malformed or oversized at work {ordinal}')
    try:
        row = object_value(json.loads(line.removeprefix(kind + b' '),
                                      object_pairs_hook=unique_pairs,
                                      parse_constant=_reject_json_constant))
    except (UnicodeDecodeError, json.JSONDecodeError, NpuTraceError) as error:
        raise AbsoluteOfferError(f'boundary malformed JSON at work {ordinal}: {error}') from error
    if version == 2:
        if type(row.get('boundary_schema')) is not int or row['boundary_schema'] != 2:
            raise AbsoluteOfferError(f'boundary work {ordinal} boundary_schema v2 missing or invalid')
    elif 'boundary_schema' in row:
        raise AbsoluteOfferError(f'boundary work {ordinal} v1 boundary_schema invalid')
    cycle = row.get('cycle')
    if not isinstance(cycle, int) or isinstance(cycle, bool) or not 0 <= cycle < 2**64:
        raise AbsoluteOfferError(f'boundary work {ordinal} cycle invalid')
    for field, expected in (('phase', phase), ('ordinal', ordinal), ('work_id', work_id)):
        if row.get(field) != expected or type(row[field]) is not type(expected):
            raise AbsoluteOfferError(f'boundary work {ordinal} cycle {cycle} {field} differs')
    for field, count_field, width in (('rows', 'row_count', 2), ('tags', 'tag_count', 4)):
        values, count = row.get(field), row.get(count_field)
        if not isinstance(count, int) or isinstance(count, bool) or not 0 <= count < 2**64 or \
                not isinstance(values, list) or len(values) != count:
            raise AbsoluteOfferError(f'boundary work {ordinal} cycle {cycle} {count_field} malformed')
        if any(not isinstance(item, list) or len(item) != width or
               any(type(value) is not int or not 0 <= value < 2**64 for value in item)
               for item in values):
            raise AbsoluteOfferError(f'boundary work {ordinal} cycle {cycle} {field} malformed')
        if version == 2 and count > 6:
            raise AbsoluteOfferError(f'boundary work {ordinal} cycle {cycle} {count_field} malformed')
    if version == 2 and any(array(tag)[2] not in (0, 1) for tag in array(row['tags'])):
        raise AbsoluteOfferError(f'boundary work {ordinal} cycle {cycle} tags rob_valid malformed')
    bank = row.get('bank_pipe')
    if not isinstance(bank, list) or len(bank) != 4:
        raise AbsoluteOfferError(f'boundary work {ordinal} cycle {cycle} bank_pipe malformed')
    for bank_index, bits in enumerate(bank):
        if not isinstance(bits, list) or len(bits) != 6:
            raise AbsoluteOfferError(f'boundary work {ordinal} cycle {cycle} bank_pipe[{bank_index}] malformed')
        for bit_index, bit in enumerate(bits):
            if type(bit) is not int or bit not in (0, 1):
                raise AbsoluteOfferError(f'boundary work {ordinal} cycle {cycle} '
                                         f'bank_pipe[{bank_index}][{bit_index}] malformed')
    if kind == b'RTL_BOUNDARY_V2':
        _rtl_queue_shape(row, f'boundary work {ordinal} cycle {cycle}')
    return row


def _compare_boundary(rtl: Record, model: Record, version: int) -> None:
    ordinal, cycle = integer(model, 'ordinal'), integer(model, 'cycle')
    fields = ('phase', 'ordinal', 'work_id', 'cycle', 'row_count', 'tag_count')
    if version == 1:
        fields += ('rows', 'tags')
    else:
        fields = ('boundary_schema',) + fields
    for field in fields:
        if rtl[field] != model[field]:
            raise AbsoluteOfferError(f'boundary first divergence at work {ordinal} cycle {cycle} '
                                     f'{field}: rtl={rtl[field]} model={model[field]}')
    if version == 2:
        for field, offsets in (('tags', ((0, 'id'), (2, 'rob_valid'),
                                         (3, 'rob_id'), (1, 'output_rows'))),
                               ('rows', ((0, 'id'), (1, 'mesh_total_rows')))):
            for index, (rtl_item, model_item) in enumerate(zip(array(rtl[field]),
                                                                array(model[field]), strict=True)):
                rtl_values, model_values = array(rtl_item), array(model_item)
                for offset, label in offsets:
                    if field == 'tags' and offset == 1 and \
                            rtl_values[2] == model_values[2] == 0:
                        continue
                    rtl_value, model_value = rtl_values[offset], model_values[offset]
                    if rtl_value != model_value:
                        raise AbsoluteOfferError(f'boundary first divergence at work {ordinal} '
                                                 f'cycle {cycle} {field}[{index}].{label}: '
                                                 f'rtl={rtl_value} model={model_value}')
    for bank_index, (rtl_bits, model_bits) in enumerate(zip(array(rtl['bank_pipe']),
                                                            array(model['bank_pipe']), strict=True)):
        for bit_index, (rtl_bit, model_bit) in enumerate(zip(array(rtl_bits), array(model_bits),
                                                               strict=True)):
                if rtl_bit != model_bit:
                    raise AbsoluteOfferError(f'boundary first divergence at work {ordinal} cycle {cycle} '
                                             f'bank_pipe[{bank_index}][{bit_index}]: '
                                             f'rtl={rtl_bit} model={model_bit}')


def _queue_uint(row: Record, field: str, location: str) -> int:
    value = row.get(field)
    if type(value) is not int or not 0 <= value < 2**64:
        raise AbsoluteOfferError(f'{location} {field} malformed')
    return value


def _queue_snapshot(value: JsonValue, kind: bytes, generation: int,
                    stage: str, geometry: PayloadGeometry | None = None) -> Record:
    if not isinstance(value, dict):
        raise AbsoluteOfferError(f'queue edge generation {generation} {stage} malformed')
    row = object_value(value)
    cycle = _queue_uint(row, 'cycle', f'queue edge generation {generation} {stage}')
    location = f'queue edge generation {generation} cycle {cycle}'
    common = {'cycle', 'row_count', 'rows', 'tag_count', 'tags'}
    rtl = kind.startswith(b'RTL_')
    expected = common | (set(RTL_QUEUE_FIELDS) if rtl else
                         {'tag_enqueues', 'tag_dequeues'})
    if geometry is not None:
        expected.add('tag_payloads')
    if set(row) != expected:
        field = min(set(row) ^ expected)
        raise AbsoluteOfferError(f'{location} {field} missing or unexpected')
    for field, width in (('rows', 2), ('tags', 4)):
        count = _queue_uint(row, field[:-1] + '_count', location)
        values = row[field]
        if count > 6 or not isinstance(values, list) or len(values) != count or \
                any(not isinstance(item, list) or len(item) != width or
                    any(type(part) is not int or not 0 <= part < 2**64 for part in item)
                    for item in values):
            raise AbsoluteOfferError(f'{location} {field[:-1]}_count/{field} malformed')
    if any(array(tag)[2] not in (0, 1) for tag in array(row['tags'])):
        raise AbsoluteOfferError(f'{location} tags rob_valid malformed')
    if rtl:
        _rtl_queue_shape(row, location)
    else:
        enqueues = _queue_uint(row, 'tag_enqueues', location)
        dequeues = _queue_uint(row, 'tag_dequeues', location)
        if enqueues < dequeues or enqueues - dequeues != integer(row, 'tag_count'):
            raise AbsoluteOfferError(f'{location} tag_enqueues/tag_dequeues occupancy differs')
    if geometry is not None:
        validate_payloads(row, rtl, location, geometry)
    return row


def _queue_edge(line: bytes, kind: bytes,
                geometry: PayloadGeometry | None = None) -> tuple[int, Record, Record]:
    if not line.startswith(kind + b' ') or len(line) > QUEUE_EDGE_LINE_CAP or \
            not line.endswith(b'\n'):
        raise AbsoluteOfferError(f'{kind.decode()} queue edge malformed or line cap exceeded')
    try:
        row = object_value(json.loads(line.removeprefix(kind + b' '),
                                      object_pairs_hook=unique_pairs,
                                      parse_constant=_reject_json_constant))
    except (UnicodeDecodeError, json.JSONDecodeError, NpuTraceError) as error:
        raise AbsoluteOfferError(f'{kind.decode()} queue edge malformed JSON: {error}') from error
    if (set(row) != {'queue_schema', 'generation', 'old', 'next'} or
            type(row['queue_schema']) is not int or
            row['queue_schema'] != (2 if geometry is not None else 1)):
        raise AbsoluteOfferError(f'{kind.decode()} queue edge schema malformed')
    generation = _queue_uint(row, 'generation', 'queue edge')
    if generation == 0:
        raise AbsoluteOfferError('queue edge generation malformed')
    old = _queue_snapshot(row['old'], kind, generation, 'old', geometry)
    nxt = _queue_snapshot(row['next'], kind, generation, 'next', geometry)
    cycle = integer(old, 'cycle')
    if cycle == 2**64 - 1 or integer(nxt, 'cycle') != cycle + 1:
        raise AbsoluteOfferError(f'queue edge generation {generation} cycle {cycle} '
                                 'next.cycle differs from old.cycle+1')
    return generation, old, nxt


def _queue_fifo(old: Record, nxt: Record, row_deq: int, tag_deq: int,
                location: str) -> None:
    fields = [('rows', row_deq), ('tags', tag_deq)]
    if 'tag_payloads' in old:
        fields.append(('tag_payloads', tag_deq))
    for field, dequeued in fields:
        retained = array(old[field])[dequeued:]
        if array(nxt[field])[:len(retained)] != retained:
            raise AbsoluteOfferError(f'{location} {field} ordered FIFO differs')


def _queue_rtl_transition(old: Record, nxt: Record, generation: int) -> None:
    cycle = integer(nxt, 'cycle')
    location = f'queue edge generation {generation} cycle {cycle}'
    row_enq, row_deq = integer(old, 'row_enq_fire'), integer(old, 'row_deq_fire')
    tag_enq, tag_deq = integer(old, 'tag_enq_fire'), integer(old, 'tag_deq_fire')
    expected = {
        'row_read': (integer(old, 'row_read') + row_deq) % 6,
        'row_write': (integer(old, 'row_write') + row_enq) % 6,
        'row_count': integer(old, 'row_count') + row_enq - row_deq,
        'row_maybe_full': row_enq if row_enq != row_deq else integer(old, 'row_maybe_full'),
        'tag_read': (integer(old, 'tag_read') + tag_deq) % 6,
        'tag_write': (integer(old, 'tag_write') + tag_enq) % 6,
        'tag_count': integer(old, 'tag_count') + tag_enq - tag_deq,
    }
    for field, value in expected.items():
        if integer(nxt, field) != value:
            raise AbsoluteOfferError(f'{location} {field} transition differs: '
                                     f'expected={value} actual={nxt[field]}')
    _queue_fifo(old, nxt, row_deq, tag_deq, location)


def _queue_compare_snapshot(rtl: Record, model: Record, generation: int) -> None:
    cycle = integer(rtl, 'cycle')
    location = f'queue edge generation {generation} cycle {cycle}'
    for field in ('cycle', 'row_count', 'tag_count'):
        if rtl[field] != model[field]:
            raise AbsoluteOfferError(f'{location} {field} differs: '
                                     f'rtl={rtl[field]} model={model[field]}')
    for field, names in (('rows', ('id', 'mesh_total_rows')),
                         ('tags', ('id', 'output_rows', 'rob_valid', 'rob_id'))):
        for index, (rtl_item, model_item) in enumerate(zip(array(rtl[field]),
                                                            array(model[field]), strict=True)):
            for offset, name in enumerate(names):
                rtl_value, model_value = array(rtl_item)[offset], array(model_item)[offset]
                if field == 'tags' and name == 'output_rows' and \
                        array(rtl_item)[2] == array(model_item)[2] == 0:
                    continue
                if rtl_value != model_value:
                    raise AbsoluteOfferError(f'{location} {field}[{index}].{name} differs: '
                                             f'rtl={rtl_value} model={model_value}')


def _queue_compare_edge(rtl_old: Record, rtl_next: Record,
                        model_old: Record, model_next: Record,
                        generation: int,
                        geometry: PayloadGeometry | None = None) -> tuple[int, int]:
    _queue_compare_snapshot(rtl_old, model_old, generation)
    _queue_compare_snapshot(rtl_next, model_next, generation)
    if geometry is not None:
        compare_payloads(rtl_old, model_old, generation, geometry)
        compare_payloads(rtl_next, model_next, generation, geometry)
    _queue_rtl_transition(rtl_old, rtl_next, generation)
    cycle = integer(model_next, 'cycle')
    location = f'queue edge generation {generation} cycle {cycle}'
    enq = integer(model_next, 'tag_enqueues') - integer(model_old, 'tag_enqueues')
    deq = integer(model_next, 'tag_dequeues') - integer(model_old, 'tag_dequeues')
    for field, delta, fire in (('tag_enqueues', enq, integer(rtl_old, 'tag_enq_fire')),
                               ('tag_dequeues', deq, integer(rtl_old, 'tag_deq_fire'))):
        if delta != fire:
            raise AbsoluteOfferError(f'{location} {field} delta differs: '
                                     f'rtl_fire={fire} model_delta={delta}')
    row_deq = integer(model_old, 'row_count') + enq - integer(model_next, 'row_count')
    if row_deq != integer(rtl_old, 'row_deq_fire'):
        raise AbsoluteOfferError(f'{location} row_count inferred dequeue differs')
    _queue_fifo(model_old, model_next, row_deq, deq, location)
    return enq, deq


def _queue_order(previous: tuple[int, int, Record] | None, generation: int,
                 old: Record, nxt: Record, kind: bytes) -> tuple[int, int, Record]:
    cycle = integer(old, 'cycle')
    if previous is not None:
        prior_generation, prior_cycle, prior_next = previous
        if (generation, cycle) <= (prior_generation, prior_cycle):
            raise AbsoluteOfferError(f'{kind.decode()} queue edge generation {generation} '
                                     f'cycle {cycle} duplicate or non-increasing origin')
        if generation != prior_generation:
            raise AbsoluteOfferError(f'{kind.decode()} queue edge generation changed')
        stable = ('row_count', 'rows', 'tag_count', 'tags') + (
            ('row_read', 'row_write', 'row_empty', 'row_maybe_full',
             'tag_read', 'tag_write') if kind.startswith(b'RTL_') else
            ('tag_enqueues', 'tag_dequeues'))
        for field in stable:
            if old[field] != prior_next[field]:
                raise AbsoluteOfferError(f'{kind.decode()} queue edge generation {generation} '
                                         f'cycle {cycle} {field} gap continuity differs')
    return generation, cycle, nxt


def compare_selected_events(path: Path, work_ids: tuple[int, ...],
                            *, require_counts: bool,
                            require_boundary_v2: bool = False,
                            require_queue_edges: bool = False,
                            require_queue_payload_v2: bool = False,
                            payload_stimulus: Record | None = None) -> tuple[int, ...]:
    if not work_ids or len(work_ids) > 4096:
        raise AbsoluteOfferError('selected event work count unsupported')
    if require_queue_payload_v2 and not (require_queue_edges and require_boundary_v2):
        raise AbsoluteOfferError('queue payload v2 requires queue edges and boundary v2')
    geometry: PayloadGeometry | None = None
    if require_queue_payload_v2:
        if payload_stimulus is None:
            authority = path.parent / 'fresh-stimulus.json'
            try:
                payload_stimulus = object_value(json.loads(authority.read_bytes(),
                    object_pairs_hook=unique_pairs, parse_constant=_reject_json_constant))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, NpuTraceError) as error:
                raise AbsoluteOfferError(f'queue payload v2 geometry authority missing: {error}') from error
        geometry = payload_geometry(payload_stimulus)
    rtl_ranges: list[tuple[int, int] | None] = [None] * len(work_ids)
    rtl_counts = [0] * len(work_ids)
    model_counts = [0] * len(work_ids)
    declared_rtl: dict[int, int] = {}
    declared_model: dict[int, int] = {}
    rtl_boundaries: list[Record] = []
    model_boundary_count = 0
    boundary_version: int | None = None
    rtl_ordinal = 0
    model_ordinal = 0
    rtl_start: int | None = None
    rtl_end = 0
    model_events: Counter[tuple[int, bytes]] = Counter()
    model_side = False
    rtl_works: list[Record] = []
    model_works: list[Record] = []
    rtl_run: bytes | None = None
    model_run: bytes | None = None
    rtl_run_count = model_run_count = 0
    queue_rtl: dict[tuple[int, int], tuple[Record, Record]] = {}
    rtl_queue_previous: tuple[int, int, Record] | None = None
    model_queue_previous: tuple[int, int, Record] | None = None
    rtl_queue_count = model_queue_count = queue_bytes = 0
    rtl_tag_enqueues = rtl_tag_dequeues = 0
    model_tag_enqueues = model_tag_dequeues = 0
    rtl_payload_gap: str | None = None
    model_payload_gap: str | None = None
    with path.open('rb') as stream, path.open('rb') as replay:
        while line := stream.readline(QUEUE_EDGE_LINE_CAP + 1):
            if len(line) > QUEUE_EDGE_LINE_CAP and not line.endswith(b'\n'):
                if line.startswith((b'RTL_QUEUE_EDGE', b'MODEL_QUEUE_EDGE')):
                    raise AbsoluteOfferError('queue edge line cap exceeded')
                tail = stream.readline((1 << 20) if require_queue_edges else -1)
                if require_queue_edges and not tail.endswith(b'\n'):
                    raise AbsoluteOfferError('queue edge required raw line scan cap exceeded')
                line += tail
            start = stream.tell() - len(line)
            if line.startswith(b'COMPOSITION_RUN '):
                rtl_run, rtl_run_count = line, rtl_run_count + 1
            elif line.startswith(b'RTL_EVENT '):
                if model_side or rtl_ordinal >= len(work_ids):
                    raise AbsoluteOfferError('RTL_EVENT late-origin work order differs')
                _event(line, b'RTL_EVENT', rtl_ordinal, work_ids[rtl_ordinal])
                if rtl_start is None:
                    rtl_start = start
                rtl_end = stream.tell()
                rtl_counts[rtl_ordinal] += 1
                if rtl_counts[rtl_ordinal] > MAX_SELECTED_EVENTS_PER_WORK:
                    raise AbsoluteOfferError('UNSUPPORTED selected events per work cap')
            elif line.startswith((b'RTL_BOUNDARY ', b'RTL_BOUNDARY_V2 ')):
                version = 2 if line.startswith(b'RTL_BOUNDARY_V2 ') else 1
                if require_boundary_v2 and version != 2:
                    raise AbsoluteOfferError('required boundary v2 rejects legacy v1 row')
                if boundary_version is not None and version != boundary_version:
                    raise AbsoluteOfferError('RTL boundary mixed schema versions')
                if model_side or len(rtl_boundaries) >= len(BOUNDARY_PHASES):
                    raise AbsoluteOfferError('RTL boundary extra or late')
                phase, ordinal = BOUNDARY_PHASES[len(rtl_boundaries)]
                if rtl_ordinal != ordinal or ordinal >= len(work_ids):
                    raise AbsoluteOfferError(f'RTL boundary work {ordinal} order differs')
                kind = b'RTL_BOUNDARY_V2' if version == 2 else b'RTL_BOUNDARY'
                rtl_boundaries.append(_boundary(line, kind, phase, ordinal,
                                                work_ids[ordinal], version))
                boundary_version = version
            elif line.startswith(b'RTL_QUEUE_EDGE'):
                if model_side:
                    raise AbsoluteOfferError('RTL queue edge late after MODEL_RUN')
                queue_bytes += len(line)
                if queue_bytes > QUEUE_EDGE_BYTE_CAP:
                    raise AbsoluteOfferError('queue edge byte cap exceeded')
                kind = b'RTL_QUEUE_EDGE_V2' if geometry is not None else b'RTL_QUEUE_EDGE_V1'
                generation, old, nxt = _queue_edge(line, kind, geometry)
                previous = rtl_queue_previous
                rtl_queue_previous = _queue_order(rtl_queue_previous, generation,
                                                  old, nxt, kind)
                if geometry is not None and previous is not None and rtl_payload_gap is None:
                    rtl_payload_gap = payload_gap(previous[2], old, generation, True)
                queue_rtl[generation, integer(old, 'cycle')] = old, nxt
                rtl_queue_count += 1
                rtl_tag_enqueues += integer(old, 'tag_enq_fire')
                rtl_tag_dequeues += integer(old, 'tag_deq_fire')
            elif line.startswith(b'COMPOSITION_WORK '):
                if model_side or rtl_ordinal >= len(work_ids) or rtl_start is None:
                    raise AbsoluteOfferError('COMPOSITION_WORK order or selected events missing')
                rtl_works.append(_work(line, b'COMPOSITION_WORK ', rtl_ordinal,
                                       work_ids[rtl_ordinal]))
                rtl_ranges[rtl_ordinal] = rtl_start, rtl_end
                rtl_ordinal += 1
                rtl_start = None
            elif line.startswith(b'RTL_SELECTED_EVENT_COUNT '):
                fields = line.split()
                if model_side or len(fields) != 3:
                    raise AbsoluteOfferError('RTL selected event count malformed or late')
                ordinal, count = int(fields[1]), int(fields[2])
                if ordinal in declared_rtl or not 0 <= ordinal < rtl_ordinal or count < 0:
                    raise AbsoluteOfferError('RTL selected event count duplicate or out of order')
                declared_rtl[ordinal] = count
            elif line.startswith(b'MODEL_RUN '):
                if model_side or rtl_ordinal != len(work_ids):
                    raise AbsoluteOfferError('MODEL_RUN before complete RTL work order')
                _counts(declared_rtl, rtl_counts, 'RTL', require_counts)
                model_run, model_run_count = line, model_run_count + 1
                model_side = True
            elif line.startswith(b'MODEL_EVENT '):
                if not model_side or model_ordinal >= len(work_ids):
                    raise AbsoluteOfferError('MODEL_EVENT late-origin work order differs')
                key = _event(line, b'MODEL_EVENT', model_ordinal, work_ids[model_ordinal])
                model_events[key] += 1
                model_counts[model_ordinal] += 1
                if model_counts[model_ordinal] > MAX_SELECTED_EVENTS_PER_WORK:
                    raise AbsoluteOfferError('UNSUPPORTED selected events per work cap')
            elif line.startswith(b'MODEL_QUEUE_EDGE'):
                if not model_side:
                    raise AbsoluteOfferError('MODEL queue edge before MODEL_RUN')
                queue_bytes += len(line)
                if queue_bytes > QUEUE_EDGE_BYTE_CAP:
                    raise AbsoluteOfferError('queue edge byte cap exceeded')
                kind = b'MODEL_QUEUE_EDGE_V2' if geometry is not None else b'MODEL_QUEUE_EDGE_V1'
                generation, old, nxt = _queue_edge(line, kind, geometry)
                if model_queue_previous is None and \
                        (integer(old, 'tag_enqueues') or integer(old, 'tag_dequeues') or
                         integer(old, 'row_count') or integer(old, 'tag_count')):
                    raise AbsoluteOfferError(f'queue edge generation {generation} '
                                             f'cycle {old["cycle"]} reset origin differs')
                previous = model_queue_previous
                model_queue_previous = _queue_order(model_queue_previous, generation,
                                                    old, nxt, kind)
                if geometry is not None and previous is not None and model_payload_gap is None:
                    model_payload_gap = payload_gap(previous[2], old, generation, False)
                key = generation, integer(old, 'cycle')
                counterpart = queue_rtl.pop(key, None)
                if counterpart is None:
                    raise AbsoluteOfferError(f'queue edge generation {generation} cycle {key[1]} '
                                             'RTL counterpart missing')
                enq, deq = _queue_compare_edge(*counterpart, old, nxt, generation, geometry)
                model_tag_enqueues += enq
                model_tag_dequeues += deq
                model_queue_count += 1
            elif line.startswith((b'MODEL_BOUNDARY ', b'MODEL_BOUNDARY_V2 ')):
                version = 2 if line.startswith(b'MODEL_BOUNDARY_V2 ') else 1
                if require_boundary_v2 and version != 2:
                    raise AbsoluteOfferError('required boundary v2 rejects legacy v1 row')
                if version != boundary_version:
                    raise AbsoluteOfferError('MODEL boundary schema differs from RTL')
                if not model_side or model_boundary_count >= len(rtl_boundaries) or \
                        model_boundary_count >= len(BOUNDARY_PHASES):
                    raise AbsoluteOfferError('MODEL boundary missing RTL counterpart or extra')
                phase, ordinal = BOUNDARY_PHASES[model_boundary_count]
                if model_ordinal != ordinal:
                    raise AbsoluteOfferError(f'MODEL boundary work {ordinal} order differs')
                kind = b'MODEL_BOUNDARY_V2' if version == 2 else b'MODEL_BOUNDARY'
                model = _boundary(line, kind, phase, ordinal, work_ids[ordinal], version)
                _compare_boundary(rtl_boundaries[model_boundary_count], model, version)
                model_boundary_count += 1
            elif line.startswith(b'MODEL_WORK '):
                if not model_side or model_ordinal >= len(work_ids) or not model_events:
                    raise AbsoluteOfferError('MODEL_WORK order or selected events missing')
                model_works.append(_work(line, b'MODEL_WORK ', model_ordinal,
                                         work_ids[model_ordinal]))
                span = rtl_ranges[model_ordinal]
                if span is None:
                    raise AbsoluteOfferError(f'RTL_EVENT range missing at work {model_ordinal}')
                replay.seek(span[0])
                rtl_events: Counter[tuple[int, bytes]] = Counter()
                while replay.tell() < span[1]:
                    candidate = replay.readline()
                    if candidate.startswith(b'RTL_EVENT '):
                        key = _event(candidate, b'RTL_EVENT', model_ordinal,
                                     work_ids[model_ordinal])
                        rtl_events[key] += 1
                if rtl_events != model_events:
                    first = min(key for key in rtl_events.keys() | model_events.keys()
                                if rtl_events[key] != model_events[key])
                    window = {kind: nsmallest(12, ((cycle, event.decode(), count)
                                              for (cycle, event), count in rows.items()
                                              if abs(cycle - first[0]) <= 2))
                              for kind, rows in (('RTL_EVENT', rtl_events),
                                                 ('MODEL_EVENT', model_events))}
                    raise AbsoluteOfferError(f'selected event divergence at work {model_ordinal} '
                                             f'cycle {first[0]} event {first[1].decode()}: '
                                             f'rtl={rtl_events[first]} model={model_events[first]} '
                                             f'window={window}')
                model_events.clear()
                model_ordinal += 1
            elif line.startswith(b'MODEL_SELECTED_EVENT_COUNT '):
                fields = line.split()
                if not model_side or len(fields) != 3:
                    raise AbsoluteOfferError('MODEL selected event count malformed or early')
                ordinal, count = int(fields[1]), int(fields[2])
                if ordinal in declared_model or not 0 <= ordinal < model_ordinal or count < 0:
                    raise AbsoluteOfferError('MODEL selected event count duplicate or out of order')
                declared_model[ordinal] = count
    if not model_side or model_ordinal != len(work_ids):
        raise AbsoluteOfferError('selected RTL/model work coverage incomplete')
    if (rtl_boundaries or model_boundary_count) and \
            (len(rtl_boundaries) != len(BOUNDARY_PHASES) or model_boundary_count != len(BOUNDARY_PHASES)):
        raise AbsoluteOfferError('boundary paired phase coverage incomplete')
    if require_boundary_v2 and boundary_version != 2:
        raise AbsoluteOfferError('required boundary v2 coverage missing')
    _counts(declared_model, model_counts, 'MODEL', require_counts)
    if queue_bytes or require_queue_edges:
        if not rtl_queue_count and not model_queue_count:
            raise AbsoluteOfferError('required queue edge coverage missing')
        if queue_rtl or rtl_queue_count != model_queue_count:
            first = min(queue_rtl) if queue_rtl else None
            raise AbsoluteOfferError(f'queue edge MODEL counterpart coverage missing at {first}')
        if boundary_version != 2:
            raise AbsoluteOfferError('queue edge requires paired boundary v2 coverage')
        if rtl_run_count != 1 or model_run_count != 1 or rtl_run is None or model_run is None:
            raise AbsoluteOfferError('queue edge run identity coverage missing')
        try:
            rtl_identity = object_value(json.loads(rtl_run.removeprefix(b'COMPOSITION_RUN '),
                                                   object_pairs_hook=unique_pairs))
            model_identity = object_value(json.loads(model_run.removeprefix(b'MODEL_RUN '),
                                                     object_pairs_hook=unique_pairs))
        except (UnicodeDecodeError, json.JSONDecodeError, NpuTraceError) as error:
            raise AbsoluteOfferError(f'queue edge run identity malformed: {error}') from error
        digest = rtl_identity.get('stimulus_sha256')
        if not isinstance(digest, str) or len(digest) != 64 or \
                any(char not in '0123456789abcdef' for char in digest) or \
                model_identity.get('stimulus_sha256') != digest:
            raise AbsoluteOfferError('queue edge stimulus identity differs')
        if geometry is not None and digest != geometry.stimulus_sha256:
            raise AbsoluteOfferError('queue payload v2 geometry stimulus identity differs')
        for field, expected in (('work_count', len(work_ids)), ('instance_count', 1),
                                ('reset_count', 1)):
            if any(type(run.get(field)) is not int or run[field] != expected
                   for run in (rtl_identity, model_identity)):
                raise AbsoluteOfferError(f'queue edge run {field} differs')
        generation = _queue_uint(model_identity, 'generation', 'queue edge MODEL_RUN')
        if rtl_queue_previous is None or model_queue_previous is None or \
                generation != rtl_queue_previous[0] or generation != model_queue_previous[0]:
            raise AbsoluteOfferError('queue edge generation differs from MODEL_RUN')
        if rtl_payload_gap is not None or model_payload_gap is not None:
            raise AbsoluteOfferError(rtl_payload_gap or model_payload_gap or 'queue payload gap')
        for kind, works, observed in (('RTL', rtl_works,
                                       (rtl_tag_enqueues, rtl_tag_dequeues)),
                                      ('MODEL', model_works,
                                       (model_tag_enqueues, model_tag_dequeues))):
            for field, count in (('mesh_tag_enqueues', observed[0]),
                                 ('mesh_tag_dequeues', observed[1])):
                try:
                    declared = sum(integer(work, field) for work in works)
                except NpuTraceError as error:
                    raise AbsoluteOfferError(f'queue edge {kind} {field} work accounting missing') from error
                if declared != count:
                    raise AbsoluteOfferError(f'queue edge {kind} {field} coverage differs: '
                                             f'work={declared} edges={count}')
    return tuple(rtl_counts)


def non_event_lines(path: Path) -> Iterator[str]:
    with path.open() as stream:
        for line in stream:
            if not line.startswith(('RTL_EVENT ', 'MODEL_EVENT ')):
                yield line


def tag_pressure_rows(path: Path, work_ids: tuple[int, ...]) -> list[Record]:
    summaries: list[Record] = []
    pressure: list[Record] = []
    fields = {'ordinal', 'work_id', 'status', 'control_queue_layout_sha256',
              'full_queue_cycles', 'full_stall_cycles', 'legacy_heuristic_cycles',
              'full_dequeue_cycles', 'first_full_cycle', 'first_stall_cycle',
              'first_full_head_id', 'first_full_read_pointer', 'first_full_write_pointer',
              'read_pointer_wraps', 'write_pointer_wraps', 'matmul_id_wraps'}
    with path.open() as stream:
        for line in stream:
            if line.startswith('COMPOSITION_WORK '):
                row = object_value(json.loads(line.removeprefix('COMPOSITION_WORK '),
                                              object_pairs_hook=unique_pairs))
                ordinal = len(summaries)
                if ordinal >= len(work_ids) or (integer(row, 'ordinal'), integer(row, 'work_id')) != \
                        (ordinal, work_ids[ordinal]):
                    raise AbsoluteOfferError('COMPOSITION_WORK pressure identity/order differs')
                summaries.append(row)
            elif line.startswith('TAG_PRESSURE_V2 '):
                row = object_value(json.loads(line.removeprefix('TAG_PRESSURE_V2 '),
                                              object_pairs_hook=unique_pairs))
                ordinal = len(pressure)
                if ordinal >= len(summaries) or ordinal >= len(work_ids) or set(row) != fields or \
                        (integer(row, 'ordinal'), integer(row, 'work_id')) != \
                        (ordinal, work_ids[ordinal]):
                    raise AbsoluteOfferError('TAG_PRESSURE_V2 coverage or order differs')
                for key in fields - {'status', 'control_queue_layout_sha256'}:
                    integer(row, key)
                stalls = integer(row, 'full_stall_cycles')
                if (row['control_queue_layout_sha256'] != TAG_LAYOUT_SHA256 or
                        row['status'] != ('FULL_STALL_OBSERVED' if stalls else
                                          'FULL_STALL_NOT_OBSERVED') or
                        integer(summaries[ordinal], 'mesh_tag_full_backpressure_cycles') != stalls):
                    raise AbsoluteOfferError(f'TAG_PRESSURE_V2 stall/layout differs at work {ordinal}')
                pressure.append(row)
    if len(summaries) != len(work_ids) or len(pressure) != len(work_ids):
        raise AbsoluteOfferError('TAG_PRESSURE_V2 coverage missing')
    return pressure


def validate_tag_windows(
        tag_edges: dict[str, list[tuple[int, str, int, int, int, int, int]]],
        expected: list[Record],
        work_streams: tuple[list[Record], list[Record]]) -> None:
    rtl_works, model_works = work_streams
    for kind, stream in tag_edges.items():
        if any(row[0] not in range(len(expected)) or min(row[2:]) < 0 for row in stream):
            raise AbsoluteOfferError(f'{kind} ordinal or value differs')
        expected_keys: list[tuple[int, str, int]] = []
        for ordinal, work in enumerate(rtl_works if kind == 'RTL_TAG_EDGE' else model_works):
            offer = integer(expected[ordinal], 'port_offer_cycle')
            resource = integer(work, 'resource_ready')
            count = min(5, resource - offer + 1)
            for phase in ('OFFER', 'RESOURCE'):
                window = [row for row in stream if row[0] == ordinal and row[1] == phase]
                first = offer if phase == 'OFFER' else resource - count + 1
                cycles = list(range(first, first + count))
                if (count < 1 or [row[2] for row in window] != cycles or
                        any(row[3] >= 6 for row in window)):
                    raise AbsoluteOfferError(f'required observation {kind} {phase} window differs '
                                             f'at work {ordinal}: expected={cycles} window={window}')
                expected_keys.extend((ordinal, phase, cycle) for cycle in cycles)
        if [row[:3] for row in stream] != expected_keys:
            raise AbsoluteOfferError(f'required observation {kind} TAG_EDGE order differs')
    rtl_tag, model_tag = tag_edges['RTL_TAG_EDGE'], tag_edges['MODEL_TAG_EDGE']
    for position, (rtl, model) in enumerate(zip(rtl_tag, model_tag, strict=True)):
        if rtl[:4] != model[:4] or rtl[5:] != model[5:] or (rtl[3] and rtl[4] != model[4]):
            raise AbsoluteOfferError(f'TAG_EDGE first divergence at work {rtl[0]} cycle {rtl[2]}: '
                                     f'rtl_window={rtl_tag[max(0, position-2):position+3]} '
                                     f'model_window={model_tag[max(0, position-2):position+3]}')
