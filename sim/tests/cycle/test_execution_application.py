from __future__ import annotations

import unittest

from sim.cycle.execution_ir import ExecutionError, NodeId, ServiceId
from sim.cycle.npu_trace_schema import Record
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


class ApplicationTests(unittest.TestCase):
    def test_sample_cost_when_potal_application_source(self) -> None:
        from sim.cycle.execution_adapter import adapt_records
        from sim.cycle.execution_application import ApplicationSource, add_application
        ir, services = adapt_records(dataset(), contract(), [])
        result, costs = add_application(ir, services, ApplicationSource((application_record(),), application_contract()))
        self.assertIn(NodeId('application:token:0'), {node.identity for node in result.nodes})
        self.assertEqual(costs.cpu[ServiceId('application:sample:0')].source, 'APPLICATION_CPU')
        self.assertEqual(costs.cpu[ServiceId('application:sample:0')].workers[0].quantity, 7)

    def test_duplicate_sample_when_same_endpoint_repeated(self) -> None:
        from sim.cycle.execution_adapter import adapt_records
        from sim.cycle.execution_application import ApplicationSource, add_application
        ir, services = adapt_records(dataset(), contract(), [])
        with self.assertRaisesRegex(ExecutionError, 'application sample coverage'):
            add_application(ir, services, ApplicationSource((application_record(), application_record()), application_contract()))

    def test_fullcpu_sample_when_source_is_wrong(self) -> None:
        from sim.cycle.execution_adapter import adapt_records
        from sim.cycle.execution_application import ApplicationSource, add_application
        ir, services = adapt_records(dataset(), contract(), [])
        record = application_record()
        record['source_role'] = 'fullcpu'
        with self.assertRaisesRegex(ExecutionError, 'PoTal sampling'):
            add_application(ir, services, ApplicationSource((record,), application_contract()))


if __name__ == '__main__':
    unittest.main()
