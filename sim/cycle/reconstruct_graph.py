from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import graphlib
import gzip
import hashlib
import json
from pathlib import Path
import re
from typing import TextIO

from scripts.gemmini_replay_contract import canonical_json
from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.npu_trace_schema import Record, SEMANTIC_FIELDS, SemanticKey, integer, object_value, require, semantic_key, text, unique_pairs

COMMON = {'schema', 'version', 'kind', 'sequence'}
FIELDS = {
    'RUN': {'source_role', 'run_config_id', 'workload', 'producer', 'cpu_only_build'},
    'PHASE': {'phase_kind', 'decode_index', 'input_tokens', 'token_fingerprint'},
    'GRAPH': {'phase_kind', 'decode_index', 'graph_occurrence', 'nodes', 'leaves'},
    'NODE_EXECUTION': SEMANTIC_FIELDS | {'source_role', 'run_config_id', 'actual_backend', 'execution_class', 'success'},
    'RUN_END': {'success', 'expected_node_count', 'executed_node_count', 'cpu_only_proven', 'graph_count'},
}
WORKLOAD = {'model', 'prompt_tokens', 'generated_tokens', 'context_tokens', 'batch_tokens', 'microbatch_tokens',
            'cpu', 'cpu_batch', 'flash_attention', 'kv_type_k', 'kv_type_v', 'seed', 'numa', 'build_target'}


def json_records(path: Path) -> Iterator[Record]:
    with (gzip.open(path, 'rt', encoding='utf-8') if path.suffix == '.gz'
          else path.open(encoding='utf-8')) as stream:
        for line in stream:
            require(bool(line.strip()), 'blank JSONL record')
            yield object_value(json.loads(line, object_pairs_hook=unique_pairs))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def emit(stream: TextIO, record: Record) -> None:
    stream.write(json.dumps({'schema': 'im2p-reconstruction', 'version': 1, **record},
                           sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n')


def service_identity(key: SemanticKey, node: Record) -> Record:
    payload = object_value(node['payload'])
    return {'operation_id': json.dumps(key, separators=(',', ':')),
            'phase': {'kind': key[0], 'decode_index': key[1]}, 'layer': payload['name'], 'op': payload['op'],
            'semantic_phase_kind': key[0], 'semantic_decode_index': key[1],
            'semantic_graph_occurrence': key[2], 'semantic_node_ordinal': key[3]}


def array(value: JsonValue) -> list[JsonValue]:
    if not isinstance(value, list):
        raise ValueError('JSON array required')
    return value


def fields(record: Record, expected: set[str]) -> None:
    require(set(record) == expected, 'missing/unknown fields: ' + ','.join(sorted(set(record) ^ expected)))


def phase_key(record: Record) -> tuple[str, int | None]:
    phase = text(record, 'phase_kind')
    decode = None if record['decode_index'] is None else integer(record, 'decode_index')
    require((phase == 'prefill' and decode is None) or (phase == 'decode' and decode is not None), 'invalid phase')
    return phase, decode


def tensor_payload(payload: Record) -> None:
    require(isinstance(payload.get('name'), str), 'tensor name must be string')
    text(payload, 'type')
    shape = array(payload['shape'])
    require(len(shape) == 4, 'tensor shape must have four dimensions')
    for value in shape:
        integer({'dimension': value}, 'dimension')


@dataclass(frozen=True, slots=True)
class Manifest:
    run: Record
    phases: list[Record]
    graphs: list[Record]
    nodes: dict[SemanticKey, Record]
    executions: dict[SemanticKey, Record]
    edges: dict[SemanticKey, tuple[SemanticKey, ...]]

    def inputs(self, key: SemanticKey) -> list[Record | None]:
        graph = next(row for row in self.graphs if (*phase_key(row), row['graph_occurrence']) == key[:3])
        result: list[Record | None] = []
        for raw in array(object_value(self.nodes[key]['payload'])['inputs']):
            if raw is None:
                result.append(None)
                continue
            edge = object_value(raw); ordinal = integer(edge, 'ordinal')
            payload = object_value(self.nodes[(*key[:3], ordinal)]['payload']) if edge['kind'] == 'node' else \
                object_value(array(graph['leaves'])[ordinal])
            result.append(payload)
        return result


def read_manifest(path: Path) -> Manifest:
    run: Record = {}
    phases: list[Record] = []
    graphs: list[Record] = []
    nodes: dict[SemanticKey, Record] = {}
    executions: dict[SemanticKey, Record] = {}
    edges: dict[SemanticKey, tuple[SemanticKey, ...]] = {}
    ended, next_graph = False, 0
    for sequence, record in enumerate(json_records(path)):
        require(not ended, 'record after semantic run end')
        require(record.get('schema') == 'im2p-semantic-graph' and type(record.get('version')) is int and record['version'] == 1,
                'unsupported semantic graph schema/version')
        kind = text(record, 'kind')
        require(kind in FIELDS, 'unknown semantic record')
        fields(record, COMMON | FIELDS[kind])
        require(integer(record, 'sequence') == sequence, 'semantic sequence duplicate/gap')
        if kind == 'RUN':
            require(sequence == 0, 'duplicate semantic run')
            run = record
            require(run['source_role'] in ('FULL_CPU', 'POTAL_COLLECTION'), 'unproven collection role')
            text(run, 'run_config_id')
            workload, producer = object_value(run['workload']), object_value(run['producer'])
            require(set(workload) == WORKLOAD, 'incomplete workload identity')
            require(bool(producer) and producer.get('warmup_excluded') is True and
                    producer.get('reserve_measure_graphs_excluded') is True, 'collection scope proof missing')
            require(type(run['cpu_only_build']) is bool, 'CPU-only build proof missing')
        elif kind == 'PHASE':
            require(bool(run) and len(executions) == len(nodes), 'phase before completed graph')
            expected = ('prefill', None) if not phases else ('decode', len(phases)-1)
            require(phase_key(record) == expected, 'nonmonotonic semantic phase')
            integer(record, 'input_tokens')
            require(re.fullmatch('fnv1a64-le-i32:[0-9a-f]{16}', text(record, 'token_fingerprint')) is not None,
                    'token input fingerprint missing')
            phases.append(record); next_graph = 0
        elif kind == 'GRAPH':
            require(bool(phases) and phase_key(record) == phase_key(phases[-1]) and len(executions) == len(nodes),
                    'graph phase or execution boundary mismatch')
            require(integer(record, 'graph_occurrence') == next_graph, 'duplicate/missing graph occurrence')
            next_graph += 1
            phase, decode = phase_key(record)
            leaves, entries = array(record['leaves']), array(record['nodes'])
            for leaf in leaves:
                payload = object_value(leaf); fields(payload, {'name', 'type', 'shape'}); tensor_payload(payload)
            for ordinal, value in enumerate(entries):
                node = object_value(value); fields(node, {'node_ordinal', 'payload', 'excluded'})
                require(integer(node, 'node_ordinal') == ordinal and type(node['excluded']) is bool, 'invalid original node ordinal')
                payload = object_value(node['payload'])
                fields(payload, {'op', 'name', 'type', 'shape', 'parameters_hex', 'parameters_status', 'inputs'})
                tensor_payload(payload); text(payload, 'op')
                require(payload['parameters_status'] == 'STATIC_BYTES' and isinstance(payload['parameters_hex'], str) and
                        re.fullmatch('(?:[0-9a-f]{2})*', payload['parameters_hex']) is not None, 'unsupported static operation parameters')
                key = (phase, decode, next_graph-1, ordinal)
                require(key not in nodes, 'duplicate semantic node')
                nodes[key] = node
                dependencies: list[SemanticKey] = []
                for edge in array(payload['inputs']):
                    if edge is None:
                        continue
                    source = object_value(edge); fields(source, {'kind', 'ordinal'})
                    index = integer(source, 'ordinal')
                    require(source['kind'] in ('node', 'leaf'), 'unknown edge kind')
                    require(index < (len(entries) if source['kind'] == 'node' else len(leaves)), 'dangling graph edge')
                    if source['kind'] == 'node': dependencies.append((phase, decode, next_graph-1, index))
                edges[key] = tuple(dependencies)
            graphs.append(record)
        elif kind == 'NODE_EXECUTION':
            key = semantic_key(record)
            require(key in nodes and key not in executions, 'unknown/duplicate original node execution')
            require(record['run_config_id'] == run['run_config_id'] and record['source_role'] == run['source_role'], 'execution source mismatch')
            require(record['success'] is True, 'node execution failed')
            text(record, 'actual_backend')
            kind = text(record, 'execution_class')
            require(kind in ('ORDINARY_CPU', 'TARGET_NPU', 'EXCLUDED'), 'unsupported node execution class')
            require((kind == 'EXCLUDED') == nodes[key]['excluded'], 'excluded-node coverage mismatch')
            require(run['source_role'] != 'FULL_CPU' or kind != 'TARGET_NPU', 'FullCPU execution used NPU')
            executions[key] = record
        else:
            require(kind == 'RUN_END' and bool(phases) and record['success'] is True, 'semantic run did not complete')
            require(integer(record, 'expected_node_count') == integer(record, 'executed_node_count') == len(nodes) == len(executions),
                    'semantic node coverage incomplete')
            require(integer(record, 'graph_count') == len(graphs), 'semantic graph count mismatch')
            require(run['source_role'] != 'FULL_CPU' or (run['cpu_only_build'] is True and record['cpu_only_proven'] is True),
                    'FullCPU build/execution proof missing')
            ended = True
    require(ended, 'missing semantic run end')
    try:
        tuple(graphlib.TopologicalSorter(edges).static_order())
    except graphlib.CycleError as error:
        raise ValueError('cyclic original graph') from error
    return Manifest(run, phases, graphs, nodes, executions, edges)


def compare_manifests(full: Manifest, potal: Manifest) -> None:
    require((full.run['source_role'], potal.run['source_role']) == ('FULL_CPU', 'POTAL_COLLECTION'), 'reconstruction source roles swapped')
    require(full.run['run_config_id'] == potal.run['run_config_id'] and
            canonical_json(object_value(full.run['workload'])) == canonical_json(object_value(potal.run['workload'])), 'workload/CPU configuration mismatch')
    strip = lambda record: {key: value for key, value in record.items() if key != 'sequence'}
    def phase_contract(record: Record) -> Record:
        return {key: value for key, value in record.items()
                if key != 'sequence' and (record['phase_kind'] == 'prefill' or key != 'token_fingerprint')}
    require([phase_contract(row) for row in full.phases] == [phase_contract(row) for row in potal.phases], 'phase/prompt input mismatch')
    require([strip(row) for row in full.graphs] == [strip(row) for row in potal.graphs], 'original graph payload/edge mismatch')
