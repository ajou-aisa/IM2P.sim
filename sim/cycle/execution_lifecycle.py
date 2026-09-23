from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sim.cycle.execution_ir import ensure
from sim.cycle.npu_trace_schema import Record, integer, object_value
from sim.cycle.reconstruct_cpu import encoded_key
from sim.cycle.reconstruct_graph import Manifest, array, fields, phase_key


@dataclass(frozen=True, slots=True)
class LifecycleProjection:
    entry_dependencies: Record
    barriers: list[Record]
    application_steps: list[Record]
    expected_samples: int


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
    run: Record | None = None
    active: Record | None = None
    cursor, dispatches, ended = 0, 0, False
    graph_operations = [['operation:' + encoded_key((*phase_key(row), integer(row, 'graph_occurrence'), ordinal))
                         for ordinal in range(len(array(row['nodes'])))] for row in graph.graphs]
    for sequence, record in enumerate(events):
        ensure(not ended and record.get('schema') == 'potal-execution-lifecycle' and
               integer(record, 'version') == 1 and integer(record, 'sequence') == sequence, 'invalid lifecycle framing')
        common = {'schema', 'version', 'kind', 'sequence'}
        match record['kind']:
            case 'RUN':
                fields(record, common | {'source_role', 'source_commit', 'expected_samples', 'execution_policy',
                    'graph_policy', 'operation_exit_policy', 'target_mode_scope', 'requires_binary_manifest_binding'})
                ensure(sequence == 0 and record['source_role'] == 'potal_collection' and
                       record['execution_policy'] == 'blocking-llama-decode-synchronize-v1' and
                       record['graph_policy'] == 'semantic-session-completes-before-next-graph-v1' and
                       record['operation_exit_policy'] == 'ALL_MEMBER_COMPLETIONS' and
                       record['target_mode_scope'] == 'FULL_ONLY' and record['requires_binary_manifest_binding'] is True,
                       'unsupported producer lifecycle policy')
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
            case 'DISPATCH_BEGIN':
                fields(record, common | {'dispatch_id', 'graph_begin', 'phase_kind', 'decode_index'})
                ensure(bool(phases) and active is None and integer(record, 'dispatch_id') == dispatches and
                       integer(record, 'graph_begin') == cursor and phase_key(record) == phase_key(phases[-1]),
                       'invalid dispatch begin')
                active = record
            case 'DISPATCH_END':
                fields(record, common | {'dispatch_id', 'graph_end', 'status', 'synchronized', 'phase_kind', 'decode_index'})
                ensure(active is not None and integer(record, 'dispatch_id') == dispatches and record['status'] == 0 and
                       record['synchronized'] is True and phase_key(record) == phase_key(active), 'unsynchronized/failed dispatch')
                end = integer(record, 'graph_end')
                ensure(cursor < end <= len(graph.graphs), 'missing/extra dispatched semantic graph')
                begin_id = f'dispatch:{dispatches}:begin'
                barriers.append({'node_id': begin_id, 'phase': str(len(phases) - 1),
                                 'dependencies': [] if not dispatches else [f'dispatch:{dispatches - 1}:end']})
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
    return LifecycleProjection(entries, barriers, steps, len(samples))


def validate_forced_lifecycle(events: Iterable[Record], graph: Manifest,
                              expected_tokens: tuple[int, ...]) -> tuple[int, ...]:
    producer = object_value(graph.run['producer'])
    ensure(graph.run['source_role'] == 'FULL_CPU' and producer.get('execution_kind') == 'FORCED_CPU_COST_ONLY' and
           producer.get('trajectory_source') == 'POTAL' and len(expected_tokens) == 128 and
           integer(object_value(graph.run['workload']), 'generated_tokens') == 128,
           'forced lifecycle requires paired FullCPU cost-only scope and 128 PoTal tokens')
    tokens: list[int] = []
    phases: list[Record] = []
    active: Record | None = None
    cursor, dispatches, ended, started = 0, 0, False, False
    for sequence, record in enumerate(events):
        ensure(not ended and record.get('schema') == 'potal-execution-lifecycle' and
               integer(record, 'version') == 2 and integer(record, 'sequence') == sequence, 'invalid forced lifecycle framing')
        common = {'schema', 'version', 'kind', 'sequence'}
        match record['kind']:
            case 'RUN':
                fields(record, common | {'source_role', 'source_commit', 'expected_samples', 'execution_policy',
                    'graph_policy', 'operation_exit_policy', 'target_mode_scope', 'requires_binary_manifest_binding',
                    'execution_kind', 'trajectory_source', 'expected_tokens', 'performance_scope'})
                ensure(sequence == 0 and record['source_role'] == 'full_cpu' and record['source_commit'] == producer['git_commit'] and
                       record['execution_kind'] == 'FORCED_CPU_COST_ONLY' and record['trajectory_source'] == 'POTAL' and
                       integer(record, 'expected_tokens') == 128 and integer(record, 'expected_samples') == 0 and
                       record['performance_scope'] == 'ORDINARY_CPU_COST_ONLY' and record['target_mode_scope'] == 'CPU_ONLY' and
                       record['execution_policy'] == 'blocking-llama-decode-synchronize-v1' and
                       record['graph_policy'] == 'semantic-session-completes-before-next-graph-v1' and
                       record['operation_exit_policy'] == 'ALL_MEMBER_COMPLETIONS' and record['requires_binary_manifest_binding'] is True,
                       'invalid forced cost-only producer scope')
                started = True
            case 'PHASE':
                fields(record, common | {'phase_kind', 'decode_index', 'graph_begin', 'phase_ordinal'})
                ensure(started and active is None and integer(record, 'phase_ordinal') == len(phases) and
                       integer(record, 'graph_begin') == cursor and len(tokens) == len(phases) and
                       len(phases) < len(graph.phases) and phase_key(record) == phase_key(graph.phases[len(phases)]),
                       'forced semantic phase coverage mismatch')
                phases.append(record)
            case 'DISPATCH_BEGIN':
                fields(record, common | {'dispatch_id', 'graph_begin', 'phase_kind', 'decode_index'})
                ensure(bool(phases) and active is None and integer(record, 'dispatch_id') == dispatches and
                       integer(record, 'graph_begin') == cursor and phase_key(record) == phase_key(phases[-1]),
                       'invalid forced dispatch begin')
                active = record
            case 'DISPATCH_END':
                fields(record, common | {'dispatch_id', 'graph_end', 'status', 'synchronized', 'phase_kind', 'decode_index'})
                ensure(active is not None and integer(record, 'dispatch_id') == dispatches and integer(record, 'status') == 0 and
                       record['synchronized'] is True and phase_key(record) == phase_key(active), 'failed forced dispatch')
                end = integer(record, 'graph_end')
                ensure(cursor < end <= len(graph.graphs) and
                       all(phase_key(row) == phase_key(record) for row in graph.graphs[cursor:end]), 'forced graph window mismatch')
                cursor, dispatches, active = end, dispatches + 1, None
            case 'FORCED_TOKEN':
                fields(record, common | {'token_index', 'token_id', 'graph_end', 'dispatch_id', 'token_ready',
                                         'actual_sampling', 'phase_kind', 'decode_index'})
                ensure(active is None and bool(phases) and integer(record, 'token_index') == len(tokens) < 128 and
                       len(phases) == len(tokens) + 1 and integer(record, 'graph_end') == cursor and
                       integer(record, 'dispatch_id') + 1 == dispatches and record['token_ready'] is True and
                       record['actual_sampling'] is False and phase_key(record) == phase_key(phases[-1]),
                       'forced token must not claim sampling')
                tokens.append(integer(record, 'token_id'))
            case 'RUN_END':
                fields(record, common | {'success', 'samples', 'phases', 'dispatches', 'graphs', 'forced_tokens', 'completed_tokens'})
                ensure(started and active is None and record['success'] is True and integer(record, 'samples') == 0 and
                       integer(record, 'forced_tokens') == integer(record, 'completed_tokens') == len(tokens) == 128 and
                       integer(record, 'phases') == len(phases) == len(graph.phases) == 128 and
                       integer(record, 'dispatches') == dispatches and integer(record, 'graphs') == cursor == len(graph.graphs),
                       'forced completion requires 128 tokens and zero sampling')
                ended = True
            case _:
                ensure(False, 'forced cost-only lifecycle cannot contain sampling or unknown events')
    ensure(ended and tuple(tokens) == expected_tokens, 'forced token trajectory differs from all 128 PoTal IDs')
    for index, token in enumerate(tokens[:-1]):
        ensure(graph.phases[index + 1]['token_fingerprint'] == token_fingerprint(token) and
               graph.phases[index + 1]['input_tokens'] == 1, 'forced decode input fingerprint differs')
    return tuple(tokens)
