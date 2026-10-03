from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import json

from sim.cycle.execution_ir import Dependency, Kind, Milestone, Node, NodeId, ensure
from sim.cycle.execution_pipeline_contract import OPTIONAL_EVENTS, REQUIRED_EVENTS, parse_pipeline, required_ids
from sim.cycle.npu_trace_schema import Record, integer, text
from sim.cycle.reconstruct_graph import array


@dataclass(frozen=True, slots=True)
class PipelineProjection:
    nodes: tuple[Node, ...]
    additions: dict[str, tuple[Dependency, ...]]
    removals: dict[str, frozenset[NodeId]]

    def edges(self, identity: str, original: tuple[Dependency, ...]) -> tuple[Dependency, ...]:
        removed = self.removals.get(identity, frozenset())
        return tuple(dict.fromkeys((*[edge for edge in original if edge.node not in removed],
                                    *self.additions.get(identity, ()))))


def project_pipeline(contract: Record, indexed: dict[str, Record], results: dict[str, Record]) -> PipelineProjection:
    proof = parse_pipeline(contract, results)
    additions: dict[str, list[Dependency]] = defaultdict(list)
    removals: dict[str, frozenset[NodeId]] = {}
    nodes: list[Node] = []

    def add(target: str, source: str, milestone: Milestone = Milestone.RESULT_READY) -> None:
        additions[target].append(Dependency(NodeId(source), milestone))

    def owner_id(parent_id: int, stripe_id: int, resource: str, transition: str,
                 call: int | None = None) -> str:
        suffix = '' if call is None else ':' + str(call)
        return f'owner:{parent_id}:{stripe_id}:{resource}:{transition}{suffix}'

    for parent_id, parent in proof.parents.items():
        ordered = proof.stripes[parent_id]
        prior_slot: dict[int, str] = {}
        prior_dequeue: str | None = None
        released_works: list[int] = []
        fence = f"call:{integer(parent, 'fence_call_id')}:COMPLETE_REQUIRED"
        ensure(fence in indexed and indexed[fence].get('call_kind') == 'FENCE', 'missing exact pipeline fence')
        fence_works = {value for value in array(indexed[fence]['dependencies'])
                       if isinstance(value, str) and value.startswith('npu:')}
        ensure(fence_works == {f'npu:{work}' for work in required_ids(parent, 'fence_required_work_ids')},
               'dataset/producer fence work mismatch')
        for stripe_id, result in enumerate(ordered):
            work_id, call_id = integer(result, 'work_id'), integer(result, 'call_id')
            npu = f'npu:{work_id}'
            ensure(npu in indexed, 'missing selected stripe service')
            operation = text(indexed[npu], 'operation_node_id')
            ensure(operation in indexed, 'owner lacks operation container')
            ensure(indexed[fence].get('operation_node_id') == operation,
                   'pipeline fence/operation mismatch')
            phase = json.dumps(indexed[operation]['phase'], sort_keys=True, separators=(',', ':'))
            group = proof.owners.get((parent_id, stripe_id), {})
            present = {(resource, transition) for resource, transition, _ in group}
            ensure(REQUIRED_EVENTS <= present and present <= REQUIRED_EVENTS | OPTIONAL_EVENTS,
                   'missing/unknown pipeline stripe transition')
            callback = ('RESIDUAL_CALLBACK', 'COMPLETE') in present
            merges = [key for key in group if key[0] == 'RESIDUAL_MERGE_CALL']
            ensure(callback or not merges, 'residual merge lacks callback')
            first = next(iter(group.values()))
            ensure(all(owner['producer_run_id'] == first['producer_run_id'] for owner in group.values()),
                   'mixed stripe producer identity')

            def oid(resource: str, transition: str) -> str:
                return owner_id(parent_id, stripe_id, resource, transition)

            acquire = oid('EXSIA_WORKSPACE', 'ACQUIRE')
            commit = oid('ACTIVATION_ROWS', 'COMMIT')
            seal = oid('RESIDUAL_PAYLOAD', 'SEAL')
            capacity = oid('FRONTEND_OUTSTANDING', 'ACQUIRE')
            enqueue = oid('FRONTEND_QUEUE', 'ENQUEUE')
            dequeue = oid('FRONTEND_QUEUE', 'DEQUEUE')
            accepted = oid('CPU_FUNCTIONAL_STREAM', 'ACCEPTED')
            completed = oid('CPU_FUNCTIONAL_STREAM', 'COMPLETED')
            workspace_release = oid('EXSIA_WORKSPACE', 'RELEASE')
            release = oid('FRONTEND_OUTSTANDING', 'RELEASE')
            slot = integer(result, 'host_slot')
            if slot in prior_slot:
                add(acquire, prior_slot[slot])
            add(commit, acquire)
            add(seal, commit)
            add(capacity, seal)
            add(enqueue, capacity)
            add(workspace_release, enqueue)
            add(dequeue, enqueue)
            if stripe_id >= 2:
                add(capacity, owner_id(parent_id, stripe_id - 2, 'FRONTEND_OUTSTANDING', 'RELEASE'))
            if prior_dequeue is not None:
                add(dequeue, prior_dequeue)
            prior_slot[slot], prior_dequeue = workspace_release, dequeue

            prepare, publish = f'call:{call_id}:PREPARE', f'call:{call_id}:PUBLISH'
            ensure(prepare in indexed and publish in indexed and
                   indexed[publish].get('call_kind') == 'STRIPE' and
                   indexed[prepare].get('operation_node_id') == operation and
                   indexed[publish].get('operation_node_id') == operation,
                   'stripe lacks exact publication boundary')
            ensure(indexed[npu].get('producer_operation_id', parent['operation_id']) == parent['operation_id'],
                   'stripe work/operation mismatch')
            add(prepare, dequeue)
            removals[publish] = frozenset((NodeId(npu),))
            removals[npu] = frozenset((NodeId(f'call:{call_id}:INVOKE'),))
            add(npu, publish)
            add(accepted, publish)
            add(npu, accepted)
            add(completed, accepted)

            completion_reqs = required_ids(group['CPU_FUNCTIONAL_STREAM', 'COMPLETED', None], 'required_work_ids')
            release_reqs = required_ids(group['FRONTEND_OUTSTANDING', 'RELEASE', None], 'required_work_ids')
            released_works.extend(release_reqs)
            callback_reqs = (required_ids(group['RESIDUAL_CALLBACK', 'COMPLETE', None], 'required_work_ids')
                             if callback else completion_reqs)
            ensure(set(completion_reqs) <= set(callback_reqs) and release_reqs == callback_reqs,
                   'capacity release required-work coverage mismatch')
            ensure(set(release_reqs) == {work_id} | proof.residual_works[parent_id].get(stripe_id, set()),
                   'capacity release/residual stripe binding mismatch')
            merge_calls = {merge[2] for merge in merges}
            release_calls = required_ids(group['FRONTEND_OUTSTANDING', 'RELEASE', None], 'required_call_ids')
            callback_calls = (required_ids(group['RESIDUAL_CALLBACK', 'COMPLETE', None], 'required_call_ids')
                              if callback else ())
            ensure(set(release_calls) == set(callback_calls) == merge_calls,
                   'capacity release required-call coverage mismatch')
            for raw_owner in group.values():
                resource, transition = text(raw_owner, 'resource'), text(raw_owner, 'transition')
                observed = None if raw_owner['observed_call_id'] is None else integer(raw_owner, 'observed_call_id')
                identity = owner_id(parent_id, stripe_id, resource, transition, observed)
                work_reqs, call_reqs = (required_ids(raw_owner, key) for key in
                                        ('required_work_ids', 'required_call_ids'))
                ensure(all('npu:' + str(work) in results and
                           results['npu:' + str(work)].get('operation_id') == parent['operation_id']
                           for work in work_reqs), 'owner required work lacks exact result')
                ensure(all(f'call:{call}:CONTINUATION' in indexed and
                           indexed[f'call:{call}:CONTINUATION'].get('call_kind') == 'RESIDUAL_MERGE' and
                           indexed[f'call:{call}:CONTINUATION'].get('operation_node_id') == operation
                           for call in call_reqs), 'owner required merge call missing')
                if (resource, transition) in {('CPU_FUNCTIONAL_STREAM', 'COMPLETED'),
                                              ('RESIDUAL_CALLBACK', 'COMPLETE'),
                                              ('FRONTEND_OUTSTANDING', 'RELEASE')}:
                    ensure(work_id in work_reqs, 'owner completion omits dense work')
                    for work in work_reqs:
                        add(identity, f'npu:{work}', Milestone.RESULT_READY)
                else:
                    ensure(not work_reqs, 'early owner event requires NPU completion')
                for call in call_reqs:
                    add(identity, f'call:{call}:CONTINUATION')
                if resource == 'RESIDUAL_MERGE_CALL':
                    ensure(call_reqs == (observed,), 'merge owner call dependency mismatch')
                    add(identity, completed)
                elif resource == 'RESIDUAL_CALLBACK':
                    add(identity, completed)
                    for merge_resource, merge_transition, merge_call in merges:
                        add(identity, owner_id(parent_id, stripe_id, merge_resource, merge_transition, merge_call))
                elif (resource, transition) == ('FRONTEND_OUTSTANDING', 'RELEASE'):
                    add(identity, oid('RESIDUAL_CALLBACK', 'COMPLETE') if callback else completed)
                nodes.append(Node(NodeId(identity), Kind.BARRIER, operation, phase, 0, ()))
            add(fence, release)
        for work in required_ids(parent, 'required_work_ids'):
            add(fence, f'npu:{work}', Milestone.RESULT_READY)
        ensure(set(released_works) == set(required_ids(parent, 'required_work_ids')) and
               len(released_works) == len(set(released_works)),
               'pipeline parent release/work coverage mismatch')
    completed_nodes: list[Node] = []
    for node in nodes:
        dependencies = (Dependency(NodeId(node.operation + ':enter')), *additions.pop(node.identity, []))
        completed_nodes.append(Node(node.identity, node.kind, node.operation, node.phase, node.order,
                                    tuple(dict.fromkeys(dependencies))))
    return PipelineProjection(tuple(completed_nodes),
                              {key: tuple(dict.fromkeys(value)) for key, value in additions.items()}, removals)
