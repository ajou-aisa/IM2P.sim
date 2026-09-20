"""Strict target NPU trace schema; neither CPU telemetry nor production acceptance."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Final, TypeAlias

from scripts.gemmini_replay_contract import validate_contract
from scripts.gemmini_resolve_profile import JsonValue

Record: TypeAlias = dict[str, JsonValue]
SCHEMA: Final = 'im2p-npu-cycle-trace'
VERSION: Final = 1
COMMON: Final = {'schema', 'version', 'kind', 'sequence', 'run_id', 'collection_run_id', 'run_config_id', 'source_role'}
SEMANTIC_FIELDS: Final = {'semantic_phase_kind', 'semantic_decode_index', 'semantic_graph_occurrence', 'semantic_node_ordinal'}
INPUT_KEYS: Final = ('m', 'n', 'k', 'tile_i_count', 'tile_j_count', 'tile_k_count',
                    'activation_stride_bytes', 'weight_stride_bytes', 'output_stride_bytes', 'scale_stride_elements')
FIELDS: Final = {
    'RUN': {'model', 'profile', 'activation_bits', 'weight_bits', 'dim', 'prompt_tokens',
            'requested_generated_tokens', 'producer_execution_kind', 'actual_rtl_acceptance_in_collection',
            'hardware_contract', 'hardware_contract_sha256', 'collection_scope'},
    'PHASE': {'phase_id', 'phase_kind', 'decode_index', 'input_tokens'},
    'TARGET_OPERATION': {'phase_id', 'operation_id', 'node_id', 'layer', 'operation', 'actual_backend',
                         'activation_type', 'weight_type', 'm', 'n', 'k', 'target_eligible',
                         'selected_target', 'reason', 'status', 'npu_work_count'},
    'NPU_WORK': {'phase_id', 'operation_id', 'node_id', 'parent_id', 'call_id', 'work_id', 'layer', 'operation', 'provenance',
                 'scope', 'activation_bits', 'weight_bits', 'dim', *INPUT_KEYS, 'parent_m',
                 'production_geometry_version', 'row_begin', 'row_count', 'stripe_id', 'host_slot',
                 'original_block_id', 'block_size', 'vector_op', 'output_domain', 'work_context',
                 'source_row_begin', 'source_row_count', 'column_begin', 'group_index', 'rmd_raw',
                 'host_integer_block_multiply', 'hardware_contract_sha256', 'required_host_stage_ids'},
    'NPU_CALL': {'phase_id', 'operation_id', 'node_id', 'parent_id', 'call_id', 'call_kind', 'stage', 'required_work_ids'},
    'HOST_STAGE': {'phase_id', 'operation_id', 'node_id', 'parent_id', 'call_id', 'host_stage_id',
                   'execution_class', 'stage_name', 'source_owner', 'source_location', 'required_work_ids',
                   'required_host_stage_ids', 'event', 'status'},
    'RUN_END': {'status', 'reason', 'registered_operation_count', 'completed_operation_count',
                'target_npu_count', 'ordinary_cpu_count', 'unsupported_count', 'excluded_count',
                'npu_work_count', 'call_count', 'phase_count', 'host_stage_count', 'completed_host_stage_count',
                'potal_host_count', 'functional_emulation_count'},
}
CLASSES: Final = ('TARGET_NPU', 'ORDINARY_CPU', 'UNSUPPORTED', 'EXCLUDED')
SemanticKey: TypeAlias = tuple[str, int | None, int, int]


class NpuTraceError(ValueError):
    def __init__(self, detail: str) -> None:
        super().__init__('npu-trace: ' + detail)


def require(condition: bool, detail: str) -> None:
    if not condition:
        raise NpuTraceError(detail)


def integer(record: Record, key: str) -> int:
    value = record.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < 2**64:
        raise NpuTraceError(key + ': uint64 required')
    return value


def text(record: Record, key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise NpuTraceError(key + ': nonempty string required')
    return value


def object_value(value: JsonValue) -> Record:
    if not isinstance(value, dict):
        raise NpuTraceError('JSON object required')
    return value


def unique_pairs(pairs: list[tuple[str, JsonValue]]) -> Record:
    result: Record = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key: ' + key)
        result[key] = value
    return result


def semantic_key(record: Record) -> SemanticKey:
    phase = text(record, 'semantic_phase_kind')
    decode = None if record['semantic_decode_index'] is None else integer(record, 'semantic_decode_index')
    require((phase == 'prefill' and decode is None) or (phase == 'decode' and decode is not None), 'invalid semantic phase')
    return phase, decode, integer(record, 'semantic_graph_occurrence'), integer(record, 'semantic_node_ordinal')


def parse_record(line: str) -> Record:
    record = object_value(json.loads(line, object_pairs_hook=unique_pairs))
    require(record.get('schema') == SCHEMA and type(record.get('version')) is int and
            record['version'] == VERSION, 'unsupported schema/version; production optrace is a separate format')
    kind = text(record, 'kind')
    require(kind in FIELDS, 'unknown record kind')
    semantic = SEMANTIC_FIELDS if kind in ('TARGET_OPERATION', 'NPU_WORK', 'NPU_CALL', 'HOST_STAGE') else set()
    require(set(record) == COMMON | FIELDS[kind] | semantic, 'missing/unknown ' + kind + ' fields')
    integer(record, 'sequence'); integer(record, 'collection_run_id'); text(record, 'run_id')
    text(record, 'run_config_id')
    require(record['source_role'] == 'POTAL_COLLECTION', 'NPU trace requires PoTal collection ownership')
    if semantic:
        semantic_key(record)
    return record


@dataclass(frozen=True, slots=True)
class Run:
    identity: str
    collection_id: int
    run_config_id: str
    profile: str
    widths_dim: tuple[int, int, int]
    contract: Record
    contract_hash: str


def parse_run(record: Record) -> Run:
    require(record['kind'] == 'RUN', 'first record must be RUN')
    profile = text(record, 'profile')
    widths = tuple(integer(record, key) for key in ('activation_bits', 'weight_bits', 'dim'))
    a, w, dim = widths
    require(a == w and a in (4, 8) and dim in (16, 32, 64) and profile == f'a{a}w{w}-d{dim}-hp1',
            'unsupported profile/DIM')
    text(record, 'model'); integer(record, 'prompt_tokens'); integer(record, 'requested_generated_tokens')
    require(record['producer_execution_kind'] == 'CPU_FUNCTIONAL' and
            record['actual_rtl_acceptance_in_collection'] == 'NOT_APPLICABLE', 'collection is not CPU-functional')
    require(record['collection_scope'] == 'prompt_and_generation', 'unsupported collection scope')
    contract = object_value(record['hardware_contract'])
    validate_contract(contract, profile)
    digest = text(record, 'hardware_contract_sha256')
    require(contract['sha256'] == digest, 'run hardware contract hash mismatch')
    return Run(text(record, 'run_id'), integer(record, 'collection_run_id'), text(record, 'run_config_id'),
               profile, (a, w, dim), contract, digest)


@dataclass(frozen=True, slots=True)
class Work:
    phase: int
    operation_id: int
    parent_id: int
    node_id: int
    call_id: int
    identity: int
    layer: str
    operation: str
    provenance: str
    scope: str
    parent_rows: int
    row_begin: int
    stripe_id: int | None
    inputs: tuple[int, ...]


def parse_work(record: Record, run: Run) -> Work:
    for key in ('phase_id', 'operation_id', 'parent_id', 'node_id', 'call_id', 'work_id', 'parent_m', 'row_begin', 'row_count',
                'production_geometry_version', 'block_size', 'vector_op', 'output_domain',
                'work_context', 'source_row_begin', 'source_row_count', 'column_begin', 'group_index'):
        integer(record, key)
    for key in ('stripe_id', 'host_slot', 'original_block_id'):
        if record[key] is not None:
            integer(record, key)
    require(tuple(integer(record, key) for key in ('activation_bits', 'weight_bits', 'dim')) == run.widths_dim,
            'work/run profile mismatch')
    require(record['hardware_contract_sha256'] == run.contract_hash, 'work hardware contract mismatch')
    values = tuple(integer(record, key) for key in INPUT_KEYS)
    require(all(value > 0 for value in values), 'geometry/strides must be positive')
    m, n, k, ti, tj, tk, a, w, out, scale = values
    dim = run.widths_dim[2]
    require(ti <= 65535 // dim and tj <= 65535 // dim and tk <= (2**32-1) // dim, 'tile outside hardware domain')
    require(a >= k and w >= n and out >= 4*n and out % 4 == 0 and scale >= n, 'invalid descriptor strides')
    require((record['block_size'], record['vector_op'], record['output_domain'], record['production_geometry_version'])
            == (32, 5, 2, 1), 'unsupported HP1 descriptor contract')
    require(record['rmd_raw'] is False and record['host_integer_block_multiply'] is False, 'raw/host integer work forbidden')
    rows, begin = integer(record, 'parent_m'), integer(record, 'row_begin')
    require(record['row_count'] == m and begin+m <= rows, 'invalid parent row range')
    require(record['host_slot'] is None or record['host_slot'] in (0, 1), 'host slot outside [0,1]')
    provenance, scope = text(record, 'provenance'), text(record, 'scope')
    require((provenance == 'dense_main' and scope in ('full', 'stripe') and record['original_block_id'] is None) or
            (provenance == 'residual' and scope == 'residual_compact' and k <= 32 and record['original_block_id'] is not None),
            'invalid dense/residual scope')
    stripe = None
    if scope == 'stripe':
        stripe = integer(record, 'stripe_id'); integer(record, 'host_slot')
    else:
        require(begin == 0 and rows == m, 'FULL/compact must cover exactly its dispatch')
    return Work(integer(record, 'phase_id'), integer(record, 'operation_id'), integer(record, 'parent_id'),
                integer(record, 'node_id'), integer(record, 'call_id'),
                integer(record, 'work_id'), text(record, 'layer'), text(record, 'operation'), provenance, scope,
                rows, begin, stripe, values)
