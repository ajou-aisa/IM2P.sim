from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sim.cycle.execution_adapter import AdapterFiles, adapt, adapt_records
from sim.cycle.execution_application import ApplicationSource, add_application
from sim.cycle.execution_ir import Dependency, ExecutionError, NodeId, ServiceId
from sim.cycle.execution_stream import adapt_stream
from sim.cycle.npu_trace_schema import Record, object_value
from sim.cycle.reconstruct_graph import sha256
from sim.tests.cycle.test_execution_adapter import contract, dataset


def application_record() -> Record:
    return {'schema': 'potal-application-cpu', 'version': 1, 'sample_index': 0, 'token_id': 42,
            'chunk_id': 0, 'phase': 'prefill', 'decode_index': None, 'stage': 'sample_accept',
            'source_role': 'potal_collection', 'thread_id': 123, 'host_thread_id': 123,
            'thread_cpu_ns': 7, 'thread_cpu_valid': True, 'thread_cpu_reason': None}


def application_contract() -> Record:
    return {'sha256': 'test', 'sampler_policy': 'SINGLE_CALLING_THREAD', 'resource': 'cpu:0',
            'metric_policy': 'THREAD_CPU_NS_GANG', 'expected_samples': 1,
            'steps': [{'sample_index': 0, 'logits_ready': ['operation:b'], 'next_decode_entries': []}]}


def prefill_case() -> tuple[list[Record], Record, list[Record]]:
    rows = dataset()
    declaration = contract()
    declaration['entry_dependencies'] = {'operation:a': ['dispatch:0:begin'],
                                         'operation:b': ['dispatch:1:begin']}
    declaration['barriers'] = [
        {'node_id': 'application:request:begin', 'phase': 'prefill', 'dependencies': []},
        {'node_id': 'dispatch:0:begin', 'phase': 'prefill', 'dependencies': []},
        {'node_id': 'dispatch:0:end', 'phase': 'prefill', 'dependencies': ['operation:a']},
        {'node_id': 'dispatch:1:begin', 'phase': 'prefill', 'dependencies': ['dispatch:0:end']},
        {'node_id': 'dispatch:1:end', 'phase': 'prefill', 'dependencies': ['operation:b']}]
    app = application_contract()
    app['prefill_steps'] = [{'batch_index': 0, 'dispatch_id': 0, 'graph_begin': 0},
                            {'batch_index': 1, 'dispatch_id': 1, 'graph_begin': 1}]
    app['steps'] = [{'sample_index': 0, 'logits_ready': ['dispatch:1:end'],
                     'next_decode_entries': []}]
    declaration['application'] = app
    records: list[Record] = []
    for batch in range(2):
        records.append({**application_record(), 'version': 2, 'stage': 'prefill_batch_prepare',
                        'batch_index': batch, 'dispatch_id': batch, 'sample_index': None,
                        'token_id': None})
    records.append({**application_record(), 'version': 2})
    return rows, declaration, records


class ApplicationTests(unittest.TestCase):
    def test_prefill_prep_edges_match_json_and_sqlite(self) -> None:
        rows, declaration, records = prefill_case()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'dataset.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
            (root / 'npu.jsonl').write_text('')
            (root / 'application.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in records))
            declaration['dataset_sha256'] = sha256(root / 'dataset.jsonl')
            declaration['npu_results_sha256'] = sha256(root / 'npu.jsonl')
            application = declaration['application']
            assert isinstance(application, dict)
            application['sha256'] = sha256(root / 'application.jsonl')
            (root / 'lifecycle.json').write_text(json.dumps(declaration))
            files = AdapterFiles(root / 'dataset.jsonl', root / 'lifecycle.json',
                                 root / 'npu.jsonl', root / 'application.jsonl')
            ir, services = adapt(files)
            nodes = {node.identity: node for node in ir.nodes}
            first = NodeId('application:prefill-prep:0')
            second = NodeId('application:prefill-prep:1')
            self.assertIn(Dependency(NodeId('application:request:begin')), nodes[first].dependencies)
            self.assertIn(Dependency(NodeId('dispatch:0:end')), nodes[second].dependencies)
            self.assertIn(Dependency(first), nodes[NodeId('dispatch:0:begin')].dependencies)
            self.assertIn(Dependency(second), nodes[NodeId('dispatch:1:begin')].dependencies)
            self.assertEqual(services.cpu[ServiceId(first)].workers[0].quantity, 7)
            adapt_stream(files, root / 'execution.sqlite')
            expected = {(str(node.identity), str(edge.node), edge.milestone.value)
                        for node in ir.nodes for edge in node.dependencies}
            with closing(sqlite3.connect(root / 'execution.sqlite')) as database:
                self.assertEqual(set(database.execute('SELECT node,parent,milestone FROM edges')), expected)

    def test_missing_prefill_prep_is_rejected(self) -> None:
        rows, declaration, records = prefill_case()
        ir, services = adapt_records(rows, declaration, [])
        records.pop(0)
        with self.assertRaisesRegex(ExecutionError, 'prefill'):
            add_application(ir, services, ApplicationSource(tuple(records), object_value(declaration['application'])))

    def test_sample_cost_when_potal_application_source(self) -> None:
        ir, services = adapt_records(dataset(), contract(), [])
        result, costs = add_application(ir, services, ApplicationSource((application_record(),), application_contract()))
        self.assertIn(NodeId('application:token:0'), {node.identity for node in result.nodes})
        self.assertEqual(costs.cpu[ServiceId('application:sample:0')].source, 'APPLICATION_CPU')
        self.assertEqual(costs.cpu[ServiceId('application:sample:0')].workers[0].quantity, 7)

    def test_duplicate_sample_when_same_endpoint_repeated(self) -> None:
        ir, services = adapt_records(dataset(), contract(), [])
        with self.assertRaisesRegex(ExecutionError, 'application sample coverage'):
            add_application(ir, services, ApplicationSource((application_record(), application_record()), application_contract()))

    def test_fullcpu_sample_when_source_is_wrong(self) -> None:
        ir, services = adapt_records(dataset(), contract(), [])
        record = application_record()
        record['source_role'] = 'fullcpu'
        with self.assertRaisesRegex(ExecutionError, 'PoTal sampling'):
            add_application(ir, services, ApplicationSource((record,), application_contract()))


if __name__ == '__main__':
    unittest.main()
