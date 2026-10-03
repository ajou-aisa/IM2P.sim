from __future__ import annotations

from fractions import Fraction
import unittest

from sim.cycle.execution_ir import Dependency, ExecutionError, Kind, NodeId, ServiceId
from sim.cycle.npu_trace_schema import Record


def dataset() -> list[Record]:
    return [
        {'kind': 'OPERATION_CONTAINER', 'node_id': 'operation:a', 'dependencies': [], 'phase': {'kind': 'prefill'}},
        {'kind': 'OPERATION_CONTAINER', 'node_id': 'operation:b', 'dependencies': ['operation:a'], 'phase': {'kind': 'prefill'}},
        {'kind': 'SERVICE', 'node_id': 'ordinary:a', 'operation_node_id': 'operation:a',
         'node_class': 'ORDINARY_CPU', 'dependencies': [], 'duration_source': 'FULL_CPU',
         'duration': {'worker_intervals': [{'worker_id': 0, 'source': 'thread_cpu_clock',
                      'thread_cpu_valid': True, 'thread_cpu_ns': 10}]}},
        {'kind': 'SERVICE', 'node_id': 'ordinary:b', 'operation_node_id': 'operation:b',
         'node_class': 'ORDINARY_CPU', 'dependencies': ['operation:a'], 'duration_source': 'FULL_CPU',
         'duration': {'worker_intervals': [{'worker_id': 0, 'source': 'thread_cpu_clock',
                      'thread_cpu_valid': True, 'thread_cpu_ns': 3}]}},
    ]


def contract() -> Record:
    return {'schema': 'im2p-execution-lifecycle', 'version': 1, 'source_kind': 'SYNTHETIC',
            'dataset_sha256': 'dataset', 'npu_results_sha256': 'npu',
            'operation_exit_policy': 'ALL_MEMBER_COMPLETIONS', 'publish_policy': 'NONBLOCKING_SUBMISSION',
            'slot_release_policy': 'NPU_RESOURCE_READY', 'phase_graph_policy': 'EXPLICIT_EDGES',
            'entry_dependencies': {'operation:a': [], 'operation:b': []},
            'barriers': [], 'call_slots': {}, 'submission_order': [],
            'cpu_policy': 'THREAD_CPU_NS_GANG', 'worker_resources': {'FULL_CPU:0': 'cpu:0'}}


class AdapterTests(unittest.TestCase):
    def test_op_exit_when_successor_consumes_output(self) -> None:
        from sim.cycle.execution_adapter import adapt_records
        ir, services = adapt_records(dataset(), contract(), [])
        nodes = {node.identity: node for node in ir.nodes}
        self.assertIn(Dependency(NodeId('operation:a:exit')), nodes[NodeId('operation:b:enter')].dependencies)
        self.assertIn(Dependency(NodeId('ordinary:a')), nodes[NodeId('operation:a:exit')].dependencies)
        self.assertEqual(services.cpu[ServiceId('ordinary:a')].workers[0].duration_ns, Fraction(10))

    def test_missing_semantics_when_legacy_join_given(self) -> None:
        from sim.cycle.execution_adapter import adapt_records
        with self.assertRaises(ValueError):
            adapt_records(dataset(), {}, [])

    def test_unmeasured_host_when_interval_missing(self) -> None:
        from sim.cycle.execution_adapter import adapt_records
        rows = dataset()
        rows[2]['duration'] = None
        with self.assertRaises(ValueError):
            adapt_records(rows, contract(), [])

    def test_incomplete_phase_edges_when_operation_undeclared(self) -> None:
        from sim.cycle.execution_adapter import adapt_records
        declaration = contract()
        declaration['entry_dependencies'] = {'operation:a': []}
        with self.assertRaisesRegex(ExecutionError, 'phase/graph entry coverage'):
            adapt_records(dataset(), declaration, [])

    def test_publish_when_stripe_is_nonblocking(self) -> None:
        from sim.cycle.execution_adapter import adapt_records
        rows = dataset()[:1]
        previous = None
        for stage in ('PREPARE', 'INVOKE', 'PUBLISH', 'COMPLETE_REQUIRED', 'CONTINUATION'):
            identity = 'call:0:' + stage
            dependencies = [] if previous is None else [previous]
            if stage in ('PUBLISH', 'COMPLETE_REQUIRED'):
                dependencies.append('npu:0')
            rows.append({'kind': 'CALL_BOUNDARY', 'node_id': identity, 'operation_node_id': 'operation:a',
                         'call_id': 0, 'stage': stage, 'dependencies': [value for value in dependencies]})
            previous = identity
        rows.append({'kind': 'SERVICE', 'node_id': 'npu:0', 'operation_node_id': 'operation:a',
                     'node_class': 'TARGET_NPU', 'dependencies': ['call:0:INVOKE']})
        declaration = contract()
        declaration.update(entry_dependencies={'operation:a': []}, call_slots={'0': 0}, submission_order=['npu:0'])
        results: list[Record] = [{'work_id': 0, 'schema': 'im2p-npu-cycle-result', 'cycle_model_validation': 'CURRENT_CERTIFIED',
                                 'run_view_sha256': 'exact-request', 'profile': 'a8w8-d16-hp1'}]
        ir, _ = adapt_records(rows, declaration, results)
        nodes = {node.identity: node for node in ir.nodes}
        self.assertNotIn(Dependency(NodeId('npu:0')), nodes[NodeId('call:0:PUBLISH')].dependencies)
        self.assertIn(Dependency(NodeId('call:0:PUBLISH')), nodes[NodeId('npu:0')].dependencies)
        self.assertEqual(nodes[NodeId('call:0:PUBLISH')].kind, Kind.PUBLISH)


if __name__ == '__main__':
    unittest.main()
