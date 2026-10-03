from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sim.cycle.execution_ir import ensure
from sim.cycle.npu_trace_schema import Record, integer, object_value, text
from sim.cycle.reconstruct_graph import array, fields


PARENT_FIELDS = {'phase_id', 'operation_id', 'parent_id', 'required_work_ids', 'fence_call_id',
                 'fence_required_work_ids', 'production_geometry_version', 'scope', 'activation_bits',
                 'weight_bits', 'dim', 'parent_m', 'n', 'k', 'tile_i_count', 'tile_j_count',
                 'tile_k_count', 'residual_bindings'}
RESIDUAL_FIELDS = {'work_id', 'call_id', 'child_parent_id', 'dense_work_id', 'dense_parent_id',
                   'stripe_id', 'row_begin', 'row_end', 'source_row_begin', 'source_row_count'}
OWNER_FIELDS = {'producer_sequence', 'phase_id', 'operation_id', 'parent_id', 'work_id',
                'producer_run_id', 'stripe_id', 'workspace_slot', 'target_npu_slot',
                'row_begin', 'row_end', 'resource', 'transition', 'required_work_ids',
                'required_call_ids', 'observed_call_id', 'rmd_packet', 'direct_residual',
                'source_owner', 'source_location'}
REQUIRED_EVENTS = {('EXSIA_WORKSPACE', 'ACQUIRE'), ('EXSIA_WORKSPACE', 'RELEASE'),
                   ('ACTIVATION_ROWS', 'COMMIT'), ('RESIDUAL_PAYLOAD', 'SEAL'),
                   ('FRONTEND_OUTSTANDING', 'ACQUIRE'), ('FRONTEND_OUTSTANDING', 'RELEASE'),
                   ('FRONTEND_QUEUE', 'ENQUEUE'), ('FRONTEND_QUEUE', 'DEQUEUE'),
                   ('CPU_FUNCTIONAL_STREAM', 'ACCEPTED'), ('CPU_FUNCTIONAL_STREAM', 'COMPLETED')}
OPTIONAL_EVENTS = {('RESIDUAL_MERGE_CALL', 'COMPLETE'), ('RESIDUAL_CALLBACK', 'COMPLETE')}


@dataclass(frozen=True, slots=True)
class PipelineContract:
    parents: dict[int, Record]
    stripes: dict[int, list[Record]]
    residual_works: dict[int, dict[int, set[int]]]
    owners: dict[tuple[int, int], dict[tuple[str, str, int | None], Record]]


def required_ids(row: Record, key: str) -> tuple[int, ...]:
    values = tuple(integer({'id': value}, 'id') for value in array(row[key]))
    ensure(len(values) == len(set(values)), 'duplicate pipeline required identity')
    return values


def parse_pipeline(contract: Record, results: dict[str, Record]) -> PipelineContract:
    parents: dict[int, Record] = {}
    for raw in array(contract['pipeline_parents']):
        parent = object_value(raw)
        fields(parent, PARENT_FIELDS)
        identity = integer(parent, 'parent_id')
        ensure(identity not in parents and parent['scope'] == 'STREAM', 'duplicate/unsupported pipeline parent')
        ensure(all(integer(parent, key) > 0 for key in ('parent_m', 'n', 'k', 'dim', 'tile_i_count',
                                                        'tile_j_count', 'tile_k_count')),
               'invalid producer parent geometry')
        ensure(integer(parent, 'production_geometry_version') == 1 and
               integer(parent, 'activation_bits') in (4, 8) and
               parent['activation_bits'] == parent['weight_bits'], 'unsupported producer parent geometry')
        ensure(bool(required_ids(parent, 'required_work_ids')), 'pipeline parent has no selected work')
        integer(parent, 'phase_id'); integer(parent, 'operation_id'); integer(parent, 'fence_call_id')
        parents[identity] = parent
    ensure(bool(parents), 'missing pipeline parent declarations')

    stripes: dict[int, list[Record]] = defaultdict(list)
    for identity, result in results.items():
        if result.get('scope') != 'stripe':
            continue
        parent_id = integer(result, 'parent_id')
        ensure(parent_id in parents, 'stripe result lacks producer parent')
        parent = parents[parent_id]
        ensure(identity == 'npu:' + str(integer(result, 'work_id')) and
               all(result.get(key) == parent[key] for key in ('phase_id', 'operation_id', 'parent_m',
                   'n', 'k', 'dim', 'activation_bits', 'weight_bits', 'production_geometry_version',
                   'tile_j_count', 'tile_k_count')) and
               integer(result, 'm') == integer(result, 'row_count') and
               integer(result, 'row_count') > 0 and integer(result, 'tile_i_count') > 0,
               'stripe result/parent geometry mismatch')
        integer(result, 'call_id'); integer(result, 'stripe_id'); integer(result, 'row_begin')
        ensure(result.get('host_slot') in (0, 1), 'invalid ExSIA workspace slot in result')
        stripes[parent_id].append(result)
    ensure(set(stripes) == set(parents), 'pipeline parent/result coverage mismatch')

    residual_works: dict[int, dict[int, set[int]]] = {}
    for parent_id, parent in parents.items():
        ordered = sorted(stripes[parent_id], key=lambda row: integer(row, 'stripe_id'))
        parent_works = required_ids(parent, 'required_work_ids')
        ensure(set(required_ids(parent, 'fence_required_work_ids')) == set(parent_works),
               'pipeline fence does not cover parent work')
        dense = tuple(integer(row, 'work_id') for row in ordered)
        ensure(tuple(work for work in parent_works if work in dense) == dense and
               all('npu:' + str(work) in results and
                   results['npu:' + str(work)].get('operation_id') == parent['operation_id']
                   for work in parent_works), 'pipeline parent required-work coverage mismatch')
        residual: dict[int, set[int]] = defaultdict(set)
        bound: set[int] = set()
        for raw in array(parent['residual_bindings']):
            binding = object_value(raw)
            fields(binding, RESIDUAL_FIELDS)
            work, dense_work, stripe_id = (integer(binding, key) for key in
                                            ('work_id', 'dense_work_id', 'stripe_id'))
            ensure(work not in bound and work in parent_works and work not in dense and
                   stripe_id < len(ordered) and dense_work == integer(ordered[stripe_id], 'work_id') and
                   integer(binding, 'dense_parent_id') == parent_id and
                   (integer(binding, 'row_begin'), integer(binding, 'row_end')) ==
                   (integer(ordered[stripe_id], 'row_begin'),
                    integer(ordered[stripe_id], 'row_begin') + integer(ordered[stripe_id], 'row_count')),
                   'residual binding lacks exact dense stripe')
            child = results['npu:' + str(work)]
            ensure(child.get('scope') == 'residual_compact' and
                   all(integer(binding, owner_key) == integer(child, result_key) for owner_key, result_key in
                       (('call_id', 'call_id'), ('child_parent_id', 'parent_id'),
                        ('source_row_begin', 'source_row_begin'),
                        ('source_row_count', 'source_row_count'))),
                   'residual binding differs from selected child work')
            bound.add(work)
            residual[stripe_id].add(work)
        ensure(set(parent_works) == set(dense) | bound, 'unbound pipeline residual work')
        residual_works[parent_id] = residual
        cursor = 0
        for index, result in enumerate(ordered):
            begin, count = integer(result, 'row_begin'), integer(result, 'row_count')
            ensure(integer(result, 'stripe_id') == index and begin == cursor,
                   'pipeline stripe gap, overlap, or duplicate')
            cursor += count
        ensure(cursor == integer(parent, 'parent_m'), 'incomplete producer parent rows')
        stripes[parent_id] = ordered

    owners: dict[tuple[int, int], dict[tuple[str, str, int | None], Record]] = defaultdict(dict)
    sequences: set[int] = set()
    for raw in array(contract['pipeline_owners']):
        owner = object_value(raw)
        fields(owner, OWNER_FIELDS)
        sequence = integer(owner, 'producer_sequence')
        ensure(sequence not in sequences, 'duplicate producer owner sequence')
        sequences.add(sequence)
        parent_id, stripe_id, work_id = (integer(owner, key) for key in ('parent_id', 'stripe_id', 'work_id'))
        ensure(parent_id in stripes and stripe_id < len(stripes[parent_id]), 'owner lacks exact stripe')
        result = stripes[parent_id][stripe_id]
        ensure(work_id == integer(result, 'work_id') and
               all(integer(owner, key) == integer(result, key) for key in
                   ('phase_id', 'operation_id', 'parent_id', 'stripe_id', 'row_begin')) and
               integer(owner, 'row_end') == integer(result, 'row_begin') + integer(result, 'row_count') and
               integer(owner, 'workspace_slot') == result['host_slot'] and owner['target_npu_slot'] is None,
               'owner/result identity, range, or slot mismatch')
        integer(owner, 'producer_run_id')
        ensure(type(owner['rmd_packet']) is bool and type(owner['direct_residual']) is bool and
               text(owner, 'source_location').find(':') >= 0 and
               owner['source_owner'] in ('IM2P.sim', 'llama.cpp-gemmini'), 'invalid producer owner provenance')
        resource, transition = text(owner, 'resource'), text(owner, 'transition')
        ensure((resource, transition) in REQUIRED_EVENTS | OPTIONAL_EVENTS, 'unknown producer owner transition')
        call = None if owner['observed_call_id'] is None else integer(owner, 'observed_call_id')
        ensure((resource == 'RESIDUAL_MERGE_CALL') == (call is not None), 'merge call owner identity mismatch')
        key = (resource, transition, call)
        group = owners[parent_id, stripe_id]
        ensure(key not in group, 'duplicate producer owner transition')
        group[key] = owner
    ensure(sequences == set(range(len(sequences))), 'producer owner sequence gap')
    return PipelineContract(parents, stripes, residual_works, owners)
