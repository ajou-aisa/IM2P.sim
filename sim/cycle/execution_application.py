from __future__ import annotations

from dataclasses import dataclass, replace

from sim.cycle.execution_ir import Dependency, ExecutionIR, Kind, Node, NodeId, ResourceId, ServiceId, ensure
from sim.cycle.execution_services import CpuService, Services, cpu_service
from sim.cycle.npu_trace_schema import Record, integer, object_value, text
from sim.cycle.reconstruct_graph import array, fields


@dataclass(frozen=True, slots=True)
class ApplicationSource:
    records: tuple[Record, ...]
    contract: Record


@dataclass(frozen=True, slots=True)
class ApplicationProjection:
    nodes: tuple[Node, ...]
    services: dict[ServiceId, CpuService]
    entry_edges: dict[NodeId, tuple[Dependency, ...]]


def project_application(available: dict[NodeId, Kind], source: ApplicationSource) -> ApplicationProjection:
    contract = source.contract
    prefill = 'prefill_steps' in contract
    fields(contract, {'sha256', 'sampler_policy', 'resource', 'metric_policy', 'expected_samples', 'steps'} |
           ({'prefill_steps'} if prefill else set()))
    ensure(contract['sampler_policy'] == 'SINGLE_CALLING_THREAD', 'unsupported sampler worker contract')
    count = integer(contract, 'expected_samples')
    samples = [record for record in source.records if record.get('stage') == 'sample_accept']
    preps = [record for record in source.records if record.get('stage') == 'prefill_batch_prepare']
    ensure(count > 0 and len(samples) == count and len(samples) + len(preps) == len(source.records),
           'application sample coverage mismatch')
    steps = [object_value(raw) for raw in array(contract['steps'])]
    ensure(len(steps) == count, 'application dependency coverage mismatch')
    nodes: dict[NodeId, Node] = {}
    cpu: dict[ServiceId, CpuService] = {}
    entry_edges: dict[NodeId, tuple[Dependency, ...]] = {}
    worker = ResourceId(text(contract, 'resource'))
    threads: set[int] = set()
    if prefill:
        prefill_steps = [object_value(raw) for raw in array(contract['prefill_steps'])]
        ensure(bool(prefill_steps) and len(prefill_steps) == len(preps) and
               tuple(preps) == source.records[:len(preps)], 'application prefill coverage mismatch')
        request = NodeId('application:request:begin')
        ensure(available.get(request) == Kind.BARRIER, 'application request-start barrier missing')
        previous: NodeId = request
        for index, (record, step) in enumerate(zip(preps, prefill_steps, strict=True)):
            fields(step, {'batch_index', 'dispatch_id', 'graph_begin'})
            dispatch = integer(step, 'dispatch_id')
            ensure(integer(step, 'batch_index') == index and
                   integer(record, 'batch_index') == index and integer(record, 'dispatch_id') == dispatch and
                   integer(step, 'graph_begin') >= 0 and
                   record.get('schema') == 'potal-application-cpu' and integer(record, 'version') == 2 and
                   integer(record, 'thread_id') >= 0 and
                   record.get('source_role') == 'potal_collection' and
                   record.get('sample_index') is None and record.get('token_id') is None and
                   record.get('phase') == 'prefill' and record.get('decode_index') is None,
                   'application prefill identity mismatch')
            begin, end = NodeId(f'dispatch:{dispatch}:begin'), NodeId(f'dispatch:{dispatch}:end')
            ensure(available.get(begin) == Kind.BARRIER and available.get(end) == Kind.BARRIER,
                   'application prefill dispatch barriers missing')
            prep = NodeId(f'application:prefill-prep:{dispatch}')
            ensure(prep not in nodes and prep not in available, 'duplicate application prefill service')
            cpu[ServiceId(prep)] = cpu_service({'source': 'APPLICATION_CPU', 'policy': contract['metric_policy'],
                                               'workers': [{**record, 'worker_id': 0, 'resource': worker}]})
            nodes[prep] = Node(prep, Kind.APPLICATION_CPU, prep, 'prefill:None', dispatch,
                               (Dependency(previous),), ServiceId(prep), (worker,))
            entry_edges[begin] = (Dependency(prep),)
            previous = end
    else:
        ensure(not preps and len(source.records) == count, 'application sample coverage mismatch')
    for index, (record, step) in enumerate(zip(samples, steps, strict=True)):
        ensure(record.get('schema') == 'potal-application-cpu' and integer(record, 'version') == (2 if prefill else 1) and
               record.get('source_role') == 'potal_collection' and record.get('stage') == 'sample_accept',
               'PoTal sampling source required')
        ensure(integer(record, 'sample_index') == index, 'duplicate/gapped application sample identity')
        ensure(record.get('phase') == ('prefill' if index == 0 else 'decode') and
               record.get('decode_index') == (None if index == 0 else index - 1), 'application phase mismatch')
        threads.add(integer(record, 'thread_id'))
        ensure(len(threads) == 1, 'single sampler thread contract violated')
        fields(step, {'sample_index', 'logits_ready', 'next_decode_entries'})
        ensure(integer(step, 'sample_index') == index, 'application dependency identity mismatch')
        required = [text({'id': value}, 'id') for value in array(step['logits_ready'])]
        ensure(bool(required) and len(set(required)) == len(required), 'missing/duplicate logits-ready predecessor')
        dependencies = tuple(Dependency(NodeId(value + ':exit' if NodeId(value + ':exit') in available else value))
                             for value in required)
        ensure(all(edge.node in available or edge.node in nodes for edge in dependencies), 'unknown logits-ready node')
        sample_id = NodeId('application:sample:' + str(index))
        token_id = NodeId('application:token:' + str(index))
        ensure(sample_id not in nodes and token_id not in nodes and sample_id not in available and token_id not in available,
               'duplicate application service')
        cpu[ServiceId(sample_id)] = cpu_service({'source': 'APPLICATION_CPU', 'policy': contract['metric_policy'],
                                                'workers': [{**record, 'worker_id': 0, 'resource': worker}]})
        phase = text(record, 'phase') + ':' + str(record['decode_index'])
        nodes[sample_id] = Node(sample_id, Kind.APPLICATION_CPU, sample_id, phase, index, dependencies,
                                ServiceId(sample_id), (worker,))
        nodes[token_id] = Node(token_id, Kind.BARRIER, sample_id, phase, index, (Dependency(sample_id),))
        successors = [text({'id': value}, 'id') for value in array(step['next_decode_entries'])]
        ensure(bool(successors) == (index + 1 < count), 'last sample must not trigger an extra decode')
        ensure(len(set(successors)) == len(successors), 'duplicate next-decode entry')
        for successor in successors:
            entry = NodeId(successor + ':enter')
            ensure(entry in available and available[entry] == Kind.OP_ENTER, 'unknown next-decode operation entry')
            entry_edges[entry] = entry_edges.get(entry, ()) + (Dependency(token_id),)
    return ApplicationProjection(tuple(nodes.values()), cpu, entry_edges)


def add_application(ir: ExecutionIR, services: Services, source: ApplicationSource) -> tuple[ExecutionIR, Services]:
    projected = project_application({node.identity: node.kind for node in ir.nodes}, source)
    ensure(not set(projected.services) & set(services.cpu), 'duplicate application service identity')
    nodes = [replace(node, dependencies=node.dependencies + projected.entry_edges.get(node.identity, ())) for node in ir.nodes]
    nodes.extend(projected.nodes)
    return ExecutionIR(tuple(nodes), ir.scope, ir.source_sha256), Services({**services.cpu, **projected.services}, services.npu)
