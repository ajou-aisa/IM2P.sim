from __future__ import annotations

import re
from dataclasses import dataclass

from scripts.gemmini_resolve_profile import (
    BuildFailure,
    JsonValue,
    load_memory_contract,
)
from sim.cycle.npu_trace_schema import NpuTraceError, Record, integer, object_value
from sim.cycle.reconstruct_graph import array, sha256
from sim.tests.cycle.compositional_sequence_v2_base import ROOT, AbsoluteOfferError

UINT64_LIMIT = 1 << 64
RTL_NAMES = ('id', 'rob_valid', 'rob_id', 'output_rows', 'output_cols',
             'addr_data', 'is_acc_addr', 'accumulate', 'read_full_acc_row', 'garbage_bit')
MODEL_NAMES = ('id', 'rob_valid', 'rob_id', 'mesh_total_rows', 'preload_src',
               'preload_dst', 'output_rows', 'output_cols', 'accumulate',
               'origin_generation', 'origin_ordinal', 'origin_work_id')


@dataclass(frozen=True, slots=True)
class PayloadGeometry:
    stimulus_sha256: str
    dim: int
    accumulator_rows: int
    address_mask: int


def payload_geometry(stimulus: Record) -> PayloadGeometry:
    profile = stimulus.get('profile')
    if not isinstance(profile, str) or re.fullmatch(r'a([48])w\1-d(?:16|32|64)-hp1', profile) is None:
        raise AbsoluteOfferError('queue payload v2 profile geometry unprovable')
    path = ROOT / 'config/gemmini_host_memory_contracts' / f'{profile}.json'
    try:
        hardware = object_value(stimulus['hardware_contract'])
        memory = object_value(object_value(hardware['facts'])['memory'])
        sources = object_value(hardware['source_sha256'])
        source_key = str(path.relative_to(ROOT))
        contract = load_memory_contract(path)
        values = (integer(memory, 'bank_count'), integer(memory, 'bank_rows'),
                  integer(memory, 'accumulator_rows'))
    except (OSError, KeyError, NpuTraceError, BuildFailure, ValueError) as error:
        raise AbsoluteOfferError(f'queue payload v2 profile geometry unprovable: {error}') from error
    if (hardware.get('profile') != profile or contract.profile != profile or
            sources.get(source_key) != sha256(path) or
            values != (contract.bank_count, contract.bank_rows, contract.accumulator_rows) or
            contract.bank_count <= 0 or contract.bank_rows <= 0 or
            contract.accumulator_rows <= 0):
        raise AbsoluteOfferError('queue payload v2 profile geometry source differs')
    # LocalAddr.data spans the total scratchpad row index space in the reviewed RTL.
    address_bits = (contract.bank_count * contract.bank_rows - 1).bit_length()
    if address_bits == 0 or address_bits > 64 or contract.accumulator_rows > 1 << address_bits:
        raise AbsoluteOfferError('queue payload v2 LocalAddr geometry unprovable')
    digest = stimulus.get('stimulus_sha256')
    if not isinstance(digest, str) or re.fullmatch(r'[0-9a-f]{64}', digest) is None:
        raise AbsoluteOfferError('queue payload v2 stimulus identity unprovable')
    return PayloadGeometry(digest, contract.dim, contract.accumulator_rows,
                           (1 << address_bits) - 1)


def validate_payloads(row: Record, rtl: bool, location: str,
                      geometry: PayloadGeometry) -> None:
    names = RTL_NAMES if rtl else MODEL_NAMES
    values = row.get('tag_payloads')
    if not isinstance(values, list) or len(values) != integer(row, 'tag_count'):
        raise AbsoluteOfferError(f'{location} tag_payloads occupancy malformed')
    for index, item in enumerate(values):
        if not isinstance(item, list) or len(item) != len(names):
            raise AbsoluteOfferError(f'{location} tag_payloads[{index}] width malformed')
        parts = _payload_values(item, names, f'{location} tag_payloads[{index}]')
        bit_indices = (1, 6, 7, 8, 9) if rtl else (1, 8)
        for bit_index in bit_indices:
            if parts[bit_index] not in (0, 1):
                raise AbsoluteOfferError(
                    f'{location} tag_payloads[{index}].{names[bit_index]} malformed')
        if rtl and parts[5] > geometry.address_mask:
            raise AbsoluteOfferError(f'{location} tag_payloads[{index}].addr_data out of range')


def _payload_values(value: JsonValue, names: tuple[str, ...], location: str) -> tuple[int, ...]:
    parts = array(value)
    if len(parts) != len(names):
        raise AbsoluteOfferError(f'{location} width malformed')
    result: list[int] = []
    for field, part in zip(names, parts, strict=True):
        if type(part) is not int or not 0 <= part < UINT64_LIMIT:
            raise AbsoluteOfferError(f'{location}.{field} malformed')
        result.append(part)
    return tuple(result)


def compare_payloads(rtl: Record, model: Record, generation: int,
                     geometry: PayloadGeometry) -> None:
    location = f'queue edge generation {generation} cycle {integer(rtl, "cycle")}'
    for index, (left_value, right_value) in enumerate(zip(
            array(rtl['tag_payloads']), array(model['tag_payloads']), strict=True)):
        where = f'{location} tag_payloads[{index}]'
        left = _payload_values(left_value, RTL_NAMES, where)
        right = _payload_values(right_value, MODEL_NAMES, where)

        def equal(field: str, actual: int, expected: int, label: str = where) -> None:
            if actual != expected:
                raise AbsoluteOfferError(f'{label}.{field} differs: rtl={actual} model={expected}')

        for side, payload, tag in (('RTL', left, array(array(rtl['tags'])[index])),
                                   ('MODEL', right, array(array(model['tags'])[index]))):
            for offset, name in ((0, 'id'), (1, 'rob_valid'), (2, 'rob_id')):
                if payload[offset] != tag[(0, 2, 3)[offset]]:
                    raise AbsoluteOfferError(f'{where}.{side}.{name} common tag differs')
        equal('id', left[0], right[0])
        equal('rob_valid', left[1], right[1])
        equal('rob_id', left[2], right[2])
        if left[1] == 0:
            equal('rtl_invalid_rob_zero', left[2], 0)
            equal('native_invalid_rob_zero', right[2], 0)
            for offset, expected in enumerate((geometry.address_mask, 1, 1, 1, 1), 5):
                equal(RTL_NAMES[offset], left[offset], expected)
            equal('native_dst_sentinel', right[5], (1 << 32) - 1)
            continue
        for offset, name in ((3, 'output_rows'), (4, 'output_cols')):
            if not 1 <= left[offset] <= geometry.dim:
                raise AbsoluteOfferError(f'{where}.{name} out of range')
        if not 0 <= right[5] < geometry.accumulator_rows:
            raise AbsoluteOfferError(f'{where}.preload_dst out of accumulator range')
        for name, rtl_offset, model_offset in (
                ('rob_id', 2, 2), ('output_rows', 3, 6), ('output_cols', 4, 7),
                ('addr_data', 5, 5), ('accumulate', 7, 8)):
            equal(name, left[rtl_offset], right[model_offset])
        for offset, name, expected in ((6, 'is_acc_addr', 1),
                                       (8, 'read_full_acc_row', 0),
                                       (9, 'garbage_bit', 0)):
            equal(name, left[offset], expected)
        if left[3] != array(array(rtl['tags'])[index])[1]:
            raise AbsoluteOfferError(f'{where}.RTL.output_rows common tag differs')
        if right[6] != array(array(model['tags'])[index])[1]:
            raise AbsoluteOfferError(f'{where}.MODEL.output_rows common tag differs')


def payload_gap(previous: Record, current: Record, generation: int,
                rtl: bool) -> str | None:
    names = RTL_NAMES if rtl else MODEL_NAMES
    cycle = integer(current, 'cycle')
    for index, (old_value, new_value) in enumerate(zip(
            array(previous['tag_payloads']), array(current['tag_payloads']), strict=True)):
        old = _payload_values(old_value, names, 'previous tag_payload')
        new = _payload_values(new_value, names, 'current tag_payload')
        for field, before, after in zip(names, old, new, strict=True):
            if before != after:
                return (f'queue edge generation {generation} cycle {cycle} '
                        f'tag_payloads[{index}].{field} gap continuity differs')
    return None
