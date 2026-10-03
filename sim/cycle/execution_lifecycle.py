from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from sim.cycle.execution_ir import ensure
from sim.cycle.npu_trace_schema import Record, integer, object_value
from sim.cycle.reconstruct_cpu import encoded_key
from sim.cycle.reconstruct_graph import Manifest, array, fields, phase_key
from scripts.gemmini_resolve_profile import JsonValue


@dataclass(frozen=True, slots=True)
class LifecycleProjection:
    entry_dependencies: Record
    barriers: list[Record]
    application_steps: list[Record]
    expected_samples: int
    pipeline_parents: list[Record] = field(default_factory=list)
    pipeline_owners: list[Record] = field(default_factory=list)
    prefill_steps: list[Record] = field(default_factory=list)


def token_fingerprint(token: int) -> str:
    value = 14695981039346656037
    for shift in (0, 8, 16, 24):
        value = ((value ^ ((token >> shift) & 255)) * 1099511628211) & ((1 << 64) - 1)
    return f'fnv1a64-le-i32:{value:016x}'


def project_lifecycle(events: Iterable[Record], graph: Manifest) -> LifecycleProjection:
    entries: Record = {}
    barriers: list[Record] = []
    samples: list[Record] = []
    phases: list[Record] = []
    parents: list[Record] = []
    owners: list[Record] = []
    prefill_steps: list[Record] = []
    request_started = False
    parent_by_operation: dict[int, Record] = {}
    run: Record | None = None
    active: Record | None = None
    cursor, dispatches, ended = 0, 0, False
    graph_operations = [['operation:' + encoded_key((*phase_key(row), integer(row, 'graph_occurrence'), ordinal))
                         for ordinal in range(len(array(row['nodes'])))] for row in graph.graphs]
    for sequence, record in enumerate(events):
        version = integer(record, 'version')
        ensure(not ended and record.get('schema') == 'potal-execution-lifecycle' and
               version in (1, 3) and integer(record, 'sequence') == sequence and
               (run is None or version == (3 if run['target_mode_scope'] == 'STRIPE_PIPELINE_ONLY' else 1)),
               'invalid lifecycle framing')
        common = {'schema', 'version', 'kind', 'sequence'}
        match record['kind']:
            case 'RUN':
                run_fields = common | {'source_role', 'source_commit', 'expected_samples', 'execution_policy',
                    'graph_policy', 'operation_exit_policy', 'target_mode_scope', 'requires_binary_manifest_binding'}
                if version == 3:
                    run_fields |= {'producer_ownership_source', 'actual_rtl_acceptance_in_collection',
                                   'workspace_slot_domain', 'target_npu_slot_domain'}
                fields(record, run_fields)
                ensure(sequence == 0 and record['source_role'] == 'potal_collection' and
                       record['execution_policy'] == 'blocking-llama-decode-synchronize-v1' and
                       record['graph_policy'] == 'semantic-session-completes-before-next-graph-v1' and
                       record['operation_exit_policy'] == 'ALL_MEMBER_COMPLETIONS' and
                       record['target_mode_scope'] == ('STRIPE_PIPELINE_ONLY' if version == 3 else 'FULL_ONLY') and
                       record['requires_binary_manifest_binding'] is True,
                       'unsupported producer lifecycle policy')
                if version == 3:
                    ensure(record['producer_ownership_source'] == 'CPU_FUNCTIONAL' and
                           record['actual_rtl_acceptance_in_collection'] == 'NOT_APPLICABLE' and
                           record['workspace_slot_domain'] == 'EXSIA_SCRATCH' and
                           record['target_npu_slot_domain'] == 'UNDECLARED',
                           'pipeline collection cannot claim RTL acceptance or NPU slot ownership')
                run = record
                ensure(record['source_commit'] == object_value(graph.run['producer'])['git_commit'] and
                       integer(record, 'expected_samples') == integer(object_value(graph.run['workload']), 'generated_tokens'),
                       'producer source/sample configuration mismatch')
            case 'PHASE':
                fields(record, common | {'phase_kind', 'decode_index', 'graph_begin', 'phase_ordinal'})
                ensure(run is not None and active is None and integer(record, 'phase_ordinal') == len(phases) and
                       integer(record, 'graph_begin') == cursor and len(samples) == len(phases), 'invalid phase boundary')
                ensure(len(phases) < len(graph.phases) and phase_key(record) == phase_key(graph.phases[len(phases)]),
                       'lifecycle/semantic phase mismatch')
                phases.append(record)
            case 'REQUEST_START':
                fields(record, common | {'request_id', 'phase_kind', 'decode_index', 'graph_begin'})
                ensure(version == 3 and not request_started and bool(phases) and len(phases) == 1 and
                       active is None and dispatches == 0 and integer(record, 'request_id') == 0 and
                       record['phase_kind'] == 'prefill' and record['decode_index'] is None and
                       integer(record, 'graph_begin') == 0, 'invalid pipeline request start')
                request_started = True
                barriers.append({'node_id': 'application:request:begin', 'phase': '0', 'dependencies': []})
            case 'PREFILL_BATCH_READY':
                fields(record, common | {'batch_index', 'dispatch_id', 'graph_begin', 'phase_kind', 'decode_index'})
                ensure(version == 3 and request_started and active is None and bool(phases) and
                       phases[-1]['phase_kind'] == 'prefill' and integer(record, 'batch_index') == len(prefill_steps) and
                       integer(record, 'dispatch_id') == dispatches and integer(record, 'graph_begin') == cursor and
                       record['phase_kind'] == 'prefill' and record['decode_index'] is None,
                       'invalid pipeline prefill preparation boundary')
                prefill_steps.append({'batch_index': record['batch_index'], 'dispatch_id': record['dispatch_id'],
                                      'graph_begin': record['graph_begin']})
            case 'DISPATCH_BEGIN':
                fields(record, common | {'dispatch_id', 'graph_begin', 'phase_kind', 'decode_index'})
                ensure(bool(phases) and active is None and integer(record, 'dispatch_id') == dispatches and
                       integer(record, 'graph_begin') == cursor and phase_key(record) == phase_key(phases[-1]),
                       'invalid dispatch begin')
                if version == 3 and record['phase_kind'] == 'prefill':
                    ensure(request_started and len(prefill_steps) == dispatches + 1,
                           'prefill dispatch lacks measured batch preparation')
                active = record
            case 'DISPATCH_END':
                fields(record, common | {'dispatch_id', 'graph_end', 'status', 'synchronized', 'phase_kind', 'decode_index'})
                ensure(active is not None and integer(record, 'dispatch_id') == dispatches and record['status'] == 0 and
                       record['synchronized'] is True and phase_key(record) == phase_key(active), 'unsynchronized/failed dispatch')
                end = integer(record, 'graph_end')
                ensure(cursor < end <= len(graph.graphs), 'missing/extra dispatched semantic graph')
                begin_id = f'dispatch:{dispatches}:begin'
                begins_after: list[JsonValue] = [] if not dispatches else [f'dispatch:{dispatches - 1}:end']
                barriers.append({'node_id': begin_id, 'phase': str(len(phases) - 1),
                                 'dependencies': begins_after})
                predecessor = begin_id
                for index in range(cursor, end):
                    ensure(phase_key(graph.graphs[index]) == phase_key(record), 'dispatch graph phase differs')
                    enter, leave = f'graph:{index}:begin', f'graph:{index}:end'
                    barriers.append({'node_id': enter, 'phase': str(len(phases) - 1), 'dependencies': [predecessor]})
                    for operation in graph_operations[index]:
                        ensure(operation not in entries, 'duplicate graph operation membership')
                        entries[operation] = [enter]
                    barriers.append({'node_id': leave, 'phase': str(len(phases) - 1),
                                     'dependencies': [value for value in graph_operations[index]]})
                    predecessor = leave
                barriers.append({'node_id': f'dispatch:{dispatches}:end', 'phase': str(len(phases) - 1),
                                 'dependencies': [predecessor]})
                cursor, active, dispatches = end, None, dispatches + 1
            case 'SAMPLE':
                fields(record, common | {'sample_index', 'token_id', 'graph_end', 'dispatch_id', 'token_ready',
                                         'phase_kind', 'decode_index'})
                ensure(active is None and bool(phases) and integer(record, 'sample_index') == len(samples) and
                       len(samples) + 1 == len(phases) and record['token_ready'] is True and
                       integer(record, 'dispatch_id') + 1 == dispatches and integer(record, 'graph_end') == cursor and
                       phase_key(record) == phase_key(phases[-1]), 'sample lacks completed logits dispatch')
                samples.append(record)
            case 'PIPELINE_PARENT':
                ensure(version == 3 and run is not None and active is None and
                       len(samples) == integer(run, 'expected_samples'), 'pipeline parent outside completed collection')
                fields(record, common | {'phase_id', 'operation_id', 'parent_id', 'required_work_ids',
                    'production_geometry_version', 'scope', 'activation_bits', 'weight_bits', 'dim',
                    'parent_m', 'n', 'k', 'tile_i_count', 'tile_j_count', 'tile_k_count',
                    'fence_call_id', 'fence_required_work_ids', 'residual_bindings'})
                operation = integer(record, 'operation_id')
                work_ids = array(record['required_work_ids'])
                ensure(operation not in parent_by_operation and 0 <= integer(record, 'phase_id') < len(phases) and
                       integer(record, 'production_geometry_version') == 1 and record['scope'] == 'STREAM' and
                       record['activation_bits'] in (4, 8) and record['weight_bits'] in (4, 8) and
                       record['dim'] in (16, 32, 64) and
                       all(integer(record, key) > 0 for key in ('parent_m', 'n', 'k', 'tile_i_count',
                                                                 'tile_j_count', 'tile_k_count')) and
                       bool(work_ids) and len(work_ids) == len(set(work_ids)) and
                       all(type(value) is int and value >= 0 for value in work_ids) and
                       integer(record, 'fence_call_id') >= 0 and
                       set(array(record['fence_required_work_ids'])) == set(work_ids) and
                       isinstance(record['residual_bindings'], list),
                       'invalid pipeline parent geometry or work set')
                parent_by_operation[operation] = record
                parents.append(record)
            case 'PIPELINE_OWNER':
                if run is None:
                    raise ValueError('pipeline owner has no run')
                ensure(version == 3 and bool(parents) and active is None and
                       len(samples) == integer(run, 'expected_samples'), 'pipeline owner outside completed collection')
                fields(record, common | {'producer_sequence', 'phase_id', 'operation_id', 'parent_id', 'work_id',
                    'producer_run_id', 'stripe_id', 'workspace_slot', 'target_npu_slot', 'row_begin', 'row_end',
                    'resource', 'transition', 'rmd_packet', 'direct_residual', 'required_work_ids',
                    'required_call_ids', 'observed_call_id', 'source_owner', 'source_location'})
                parent = parent_by_operation.get(integer(record, 'operation_id'))
                if parent is None:
                    raise ValueError('pipeline owner has no declared parent')
                member_ids = array(parent['required_work_ids'])
                ensure(integer(record, 'producer_sequence') == len(owners) and
                       integer(record, 'phase_id') == parent['phase_id'] and
                       integer(record, 'parent_id') == parent['parent_id'] and
                       integer(record, 'work_id') in member_ids and
                       integer(record, 'workspace_slot') in (0, 1) and record['target_npu_slot'] is None and
                       0 <= integer(record, 'row_begin') < integer(record, 'row_end') <= integer(parent, 'parent_m') and
                       record['source_owner'] in ('IM2P.sim', 'llama.cpp-gemmini') and
                       isinstance(record['source_location'], str) and bool(record['source_location']) and
                       all(type(value) is int and value in member_ids
                           for value in array(record['required_work_ids'])) and
                       all(type(value) is int and value >= 0 for value in array(record['required_call_ids'])) and
                       (record['observed_call_id'] is None or
                        (type(record['observed_call_id']) is int and record['observed_call_id'] >= 0)),
                       'pipeline owner identity or slot mismatch')
                owners.append(record)
            case 'RUN_END':
                fields(record, common | {'success', 'samples', 'phases', 'dispatches', 'graphs'})
                ensure(run is not None and active is None and record['success'] is True and
                       integer(record, 'samples') == len(samples) == integer(run, 'expected_samples') and
                       integer(record, 'phases') == len(phases) == len(graph.phases) and
                       integer(record, 'dispatches') == dispatches and integer(record, 'graphs') == cursor == len(graph.graphs),
                       'incomplete producer lifecycle coverage')
                ended = True
            case _:
                ensure(False, 'unknown producer lifecycle event')
    ensure(ended and run is not None, 'producer lifecycle did not finish')
    if run is None:
        raise ValueError('producer lifecycle has no run')
    if run['target_mode_scope'] == 'STRIPE_PIPELINE_ONLY':
        ensure(request_started and bool(prefill_steps), 'pipeline request/preparation coverage missing')
    steps: list[Record] = []
    for index, sample in enumerate(samples):
        successors: list[str] = []
        if index + 1 < len(samples):
            following = graph.phases[index + 1]
            ensure(following['input_tokens'] == 1 and
                   following['token_fingerprint'] == token_fingerprint(integer(sample, 'token_id')),
                   'sample token / next decode input trajectory mismatch')
            successors = graph_operations[integer(phases[index + 1], 'graph_begin')]
        steps.append({'sample_index': index, 'logits_ready': [f"dispatch:{sample['dispatch_id']}:end"],
                      'next_decode_entries': [value for value in successors]})
    return LifecycleProjection(entries, barriers, steps, len(samples), parents, owners, prefill_steps)


def validate_forced_lifecycle(events: Iterable[Record], graph: Manifest,
                              expected_tokens: tuple[int, ...]) -> tuple[int, ...]:
    from sim.cycle.execution_forced_lifecycle import validate_forced_lifecycle as validate
    return validate(events, graph, expected_tokens)
