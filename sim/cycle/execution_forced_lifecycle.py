from __future__ import annotations

from collections.abc import Iterable

from sim.cycle.execution_ir import ensure
from sim.cycle.execution_lifecycle import token_fingerprint
from sim.cycle.npu_trace_schema import Record, integer, object_value
from sim.cycle.reconstruct_graph import Manifest, fields, phase_key


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
