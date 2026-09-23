from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import graphlib
from typing import NewType

from sim.cycle.npu_trace_schema import Record, integer, object_value, text
from sim.cycle.reconstruct_graph import array, fields

NodeId = NewType('NodeId', str)
ServiceId = NewType('ServiceId', str)
ResourceId = NewType('ResourceId', str)


class ExecutionError(ValueError):
    def __init__(self, detail: str) -> None:
        self.detail: str = detail
        super().__init__('execution: ' + detail)


def ensure(condition: bool, detail: str) -> None:
    if not condition:
        raise ExecutionError(detail)


class Kind(str, Enum):
    CPU = 'CPU'
    NPU = 'NPU'
    APPLICATION_CPU = 'APPLICATION_CPU'
    OP_ENTER = 'OP_ENTER'
    OP_EXIT = 'OP_EXIT'
    BARRIER = 'BARRIER'
    PUBLISH = 'PUBLISH'
    FUNCTIONAL_EMULATION = 'FUNCTIONAL_EMULATION'
    WAIT = 'WAIT'
    EXCLUDED = 'EXCLUDED'


class Milestone(str, Enum):
    ACCEPTED = 'ACCEPTED'
    RESULT_READY = 'RESULT_READY'
    RESOURCE_READY = 'RESOURCE_READY'


@dataclass(frozen=True, slots=True)
class Dependency:
    node: NodeId
    milestone: Milestone = Milestone.RESULT_READY


@dataclass(frozen=True, slots=True)
class Node:
    identity: NodeId
    kind: Kind
    operation: str
    phase: str
    order: int
    dependencies: tuple[Dependency, ...]
    service: ServiceId | None = None
    resources: tuple[ResourceId, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutionIR:
    nodes: tuple[Node, ...]
    scope: str
    source_sha256: str

    def __post_init__(self) -> None:
        ensure(self.scope in ('SYNTHETIC', 'BOUND_DATASET'), 'unknown IR scope')
        ensure(bool(self.nodes), 'empty execution graph')
        identities = {node.identity for node in self.nodes}
        ensure(len(identities) == len(self.nodes), 'duplicate node identity')
        for node in self.nodes:
            ensure(node.order >= 0, 'negative submission order')
            ensure(len(set(node.resources)) == len(node.resources), 'duplicate resource')
            ensure(len(set(node.dependencies)) == len(node.dependencies), 'duplicate dependency')
            ensure(all(edge.node in identities for edge in node.dependencies), 'unresolved dependency')
            service_node = node.kind in (Kind.CPU, Kind.APPLICATION_CPU, Kind.NPU)
            ensure(service_node == (node.service is not None), 'service ownership mismatch')
            ensure(service_node == bool(node.resources), 'service/resource ownership mismatch')
            ensure(node.kind != Kind.NPU or 'npu:0' in node.resources, 'all NPU work must share npu:0')
        try:
            _ = tuple(graphlib.TopologicalSorter({node.identity: {edge.node for edge in node.dependencies}
                                             for node in self.nodes}).static_order())
        except graphlib.CycleError as error:
            raise ExecutionError('cyclic execution graph') from error


def service_id(node: Node) -> ServiceId:
    if node.service is None:
        raise ExecutionError('service identity required')
    return node.service


def parse_ir(document: Record) -> ExecutionIR:
    fields(document, {'schema', 'version', 'scope', 'source_sha256', 'nodes'})
    ensure(document['schema'] == 'im2p-execution-ir' and integer(document, 'version') == 1,
           'unsupported execution IR schema/version')
    nodes: list[Node] = []
    for value in array(document['nodes']):
        record = object_value(value)
        fields(record, {'node_id', 'kind', 'operation_id', 'phase', 'order', 'dependencies', 'service_id', 'resources'})
        edges: list[Dependency] = []
        for raw in array(record['dependencies']):
            edge = object_value(raw)
            fields(edge, {'node_id', 'milestone'})
            edges.append(Dependency(NodeId(text(edge, 'node_id')), Milestone(text(edge, 'milestone'))))
        resources = tuple(ResourceId(text({'resource': raw}, 'resource')) for raw in array(record['resources']))
        service = None if record['service_id'] is None else ServiceId(text(record, 'service_id'))
        nodes.append(Node(NodeId(text(record, 'node_id')), Kind(text(record, 'kind')),
                          text(record, 'operation_id'), text(record, 'phase'), integer(record, 'order'),
                          tuple(edges), service, resources))
    return ExecutionIR(tuple(nodes), text(document, 'scope'), text(document, 'source_sha256'))


def ir_record(ir: ExecutionIR) -> Record:
    return {'schema': 'im2p-execution-ir', 'version': 1, 'scope': ir.scope, 'source_sha256': ir.source_sha256,
            'nodes': [node_record(node) for node in ir.nodes]}


def node_record(node: Node) -> Record:
    return {'node_id': node.identity, 'kind': node.kind.value, 'operation_id': node.operation,
            'phase': node.phase, 'order': node.order, 'service_id': node.service, 'resources': list(node.resources),
            'dependencies': [{'node_id': edge.node, 'milestone': edge.milestone.value} for edge in node.dependencies]}
