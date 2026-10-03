from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sim.cycle.execution_adapter import AdapterFiles, adapt_records
from sim.cycle.execution_ir import ExecutionError, ServiceId
from sim.cycle.execution_services import parse_services, services_record
from sim.cycle.execution_stream import adapt_stream
from sim.cycle.npu_trace_schema import Record, object_value
from sim.cycle.reconstruct_graph import sha256
from sim.tests.cycle.test_execution_adapter import contract

HOST_KEY = '["POTAL_COLLECTION","host_thread","78363-1790098209975292000",114094390]'


def measured_host() -> Record:
    return {'source': 'host_tick', 'unit': 'tick', 'start': 74117994707579, 'end': 74117994707596,
            'delta': 17, 'valid': True, 'stage': 'im2p.output_reconstruction',
            'host_execution_id': '78363-1790098209975292000', 'thread_id': 114094390,
            'host_start_ns': 3298438168582000, 'host_end_ns': 3298438168582416,
            'host_elapsed_ns': 416, 'host_elapsed_valid': True, 'host_elapsed_reason': None,
            'thread_cpu_ns': None, 'thread_cpu_valid': False, 'thread_cpu_reason': 'unavailable_thread_cpu_time',
            'cpu_work_cycles': None, 'cpu_work_cycles_valid': False, 'cpu_work_cycles_source': None,
            'cpu_work_cycles_unit': 'cycle', 'cpu_work_cycles_reason': 'not_thread_cpu_counter'}


def records() -> list[Record]:
    return [{'kind': 'OPERATION_CONTAINER', 'node_id': 'operation:a', 'dependencies': [], 'phase': {'kind': 'prefill'}},
            {'kind': 'SERVICE', 'node_id': 'host:13', 'operation_node_id': 'operation:a', 'node_class': 'POTAL_HOST',
             'duration_source': 'POTAL_COLLECTION', 'dependencies': [], 'duration': measured_host()}]


def declaration() -> Record:
    value = contract()
    value.update(cpu_policy='HOST_ELAPSED_NS_GANG', entry_dependencies={'operation:a': []},
                 worker_resources={HOST_KEY: 'cpu:serial-reference'})
    return value


class HostIdentityTests(unittest.TestCase):
    def test_native_host_when_worker_id_missing_uses_thread_identity(self) -> None:
        _, services = adapt_records(records(), declaration(), [])
        worker = services.cpu[ServiceId('host:13')].workers[0]
        self.assertIsNone(worker.worker_id)
        self.assertEqual(worker.duration_ns, 416)
        self.assertEqual(worker.measurement['thread_id'], 114094390)
        self.assertEqual(worker.measurement['host_execution_id'], '78363-1790098209975292000')

    def test_metadata_when_service_roundtripped_remains_original(self) -> None:
        _, services = adapt_records(records(), declaration(), [])
        restored = parse_services(services_record(services)).cpu[ServiceId('host:13')].workers[0]
        for key, value in measured_host().items():
            self.assertEqual(restored.measurement[key], value)
        self.assertNotIn('worker_id', restored.measurement)

    def test_mapping_when_missing_fails(self) -> None:
        policy = declaration()
        policy['worker_resources'] = {'POTAL_COLLECTION:0': 'cpu:0'}
        with self.assertRaisesRegex(ExecutionError, 'resource mapping'):
            adapt_records(records(), policy, [])

    def test_mapping_when_ambiguous_fails(self) -> None:
        rows, policy = records(), declaration()
        object_value(rows[1]['duration'])['worker_id'] = 0
        object_value(policy['worker_resources'])['POTAL_COLLECTION:0'] = 'cpu:other'
        with self.assertRaisesRegex(ExecutionError, 'ambiguous'):
            adapt_records(rows, policy, [])

    def test_thread_cpu_policy_when_invalid_does_not_fall_back(self) -> None:
        policy = declaration()
        policy['cpu_policy'] = 'THREAD_CPU_NS_GANG'
        with self.assertRaisesRegex(ExecutionError, 'substitution forbidden'):
            adapt_records(records(), policy, [])

    def test_sqlite_when_native_host_has_no_worker_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'dataset.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in records()))
            (root / 'npu.jsonl').write_text('')
            policy = declaration()
            policy.update(dataset_sha256=sha256(root / 'dataset.jsonl'), npu_results_sha256=sha256(root / 'npu.jsonl'))
            (root / 'lifecycle.json').write_text(json.dumps(policy))
            summary = adapt_stream(AdapterFiles(root / 'dataset.jsonl', root / 'lifecycle.json', root / 'npu.jsonl'),
                                   root / 'execution.sqlite')
            self.assertEqual(summary['service_count'], 1)
            with closing(sqlite3.connect(root / 'execution.sqlite')) as database:
                raw = object_value(json.loads(database.execute('SELECT body FROM services').fetchone()[0]))
            worker = parse_services(raw).cpu[ServiceId('host:13')].workers[0]
            self.assertIsNone(worker.worker_id)
            self.assertEqual(worker.measurement['host_elapsed_ns'], 416)

    def test_ordinary_when_multiple_workers_preserves_vector_identity(self) -> None:
        rows = records()
        rows[1].update(node_class='ORDINARY_CPU', duration_source='FULL_CPU', duration={'worker_intervals': [
            {'worker_id': 0, 'thread_cpu_ns': 10, 'thread_cpu_valid': True},
            {'worker_id': 1, 'thread_cpu_ns': 12, 'thread_cpu_valid': True}]})
        policy = declaration()
        policy.update(cpu_policy='THREAD_CPU_NS_GANG', worker_resources={'FULL_CPU:0': 'cpu:0', 'FULL_CPU:1': 'cpu:1'})
        _, services = adapt_records(rows, policy, [])
        workers = services.cpu[ServiceId('host:13')].workers
        self.assertEqual(tuple(worker.worker_id for worker in workers), (0, 1))
        self.assertEqual(tuple(worker.quantity for worker in workers), (10, 12))

    def test_missing_ordinary_worker_when_host_thread_present_does_not_substitute(self) -> None:
        rows = records()
        rows[1].update(node_class='ORDINARY_CPU', duration_source='FULL_CPU', duration={'worker_intervals': [measured_host()]})
        policy = declaration()
        with self.assertRaisesRegex(ValueError, 'worker_id'):
            adapt_records(rows, policy, [])


if __name__ == '__main__':
    unittest.main()
