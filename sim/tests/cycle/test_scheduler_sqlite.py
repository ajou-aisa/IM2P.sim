from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from sim.cycle.execution_ir import Dependency, ExecutionError, ExecutionIR, Kind, Milestone, Node, NodeId, ResourceId, ServiceId
from sim.cycle.execution_services import CpuService, NpuService, NpuWork, PhaseTable, Services, parse_services, services_record
from sim.cycle.execution_stream import ExecutionStore
from sim.cycle.npu_trace_schema import Record
from sim.cycle.scheduler import Scenario, ScheduleInputs, schedule
from sim.tests.cycle.test_execution_scheduler import cpu, node


def write_store(path: Path, inputs: ScheduleInputs) -> None:
    with closing(sqlite3.connect(path)) as db:
        store = ExecutionStore(db)
        for item in inputs.ir.nodes:
            cpu_services = {key: value for key, value in inputs.services.cpu.items() if key == item.service}
            npu_services = {key: value for key, value in inputs.services.npu.items() if key == item.service}
            service = services_record(Services(cpu_services, npu_services)) if item.service is not None else None
            store.add(item, service)
        store.validate()
        manifest: Record = {'schema': 'im2p-execution-sqlite', 'version': 1, 'status': 'PASS',
                            'scope': 'SYNTHETIC', 'node_count': store.count}
        db.execute('INSERT INTO metadata VALUES(?,?)', ('manifest', json.dumps(manifest)))
        db.commit()


class SqliteSchedulerTests(unittest.TestCase):
    def inputs(self, nodes: tuple[Node, ...], services: Services) -> ScheduleInputs:
        canonical = parse_services(services_record(services))
        provider = PhaseTable(1, {(work.profile, work.request_sha256, 0): NpuService(5, 8, 'synthetic')
                                 for work in canonical.npu.values()})
        return ScheduleInputs(ExecutionIR(nodes, 'SYNTHETIC', 'test'), canonical, provider)

    def test_matches_json_when_graph_uses_cpu_npu_and_causal_milestones(self) -> None:
        # Given: real phase tables and canonical shared services for both schedulers.
        ordinary = Services({ServiceId(name): cpu(name, duration) for name, duration in
                             (('a', 10), ('b', 7), ('ready', 2), ('later', 3))}, {})
        npu = {ServiceId(name): NpuWork(ServiceId(name), name, 'a8w8-d16-hp1') for name in ('npu', 'other')}
        gang = CpuService('FULL_CPU', 'THREAD_CPU_NS_GANG', cpu('a', 3).workers + cpu('b', 7, 1).workers)
        cases = (
            ('cpu-chain', (node('a', Kind.CPU), node('b', Kind.CPU, (Dependency(NodeId('a')),))),
             Services({key: ordinary.cpu[key] for key in (ServiceId('a'), ServiceId('b'))}, {})),
            ('newly-ready-priority', (node('b', Kind.CPU), node('a', Kind.CPU, (Dependency(NodeId('b')),)),
                                      node('c', Kind.CPU)),
             Services({ServiceId('a'): cpu('a', 5), ServiceId('b'): cpu('b', 0), ServiceId('c'): cpu('c', 3)}, {})),
            ('cpu-npu-cpu', (node('a', Kind.CPU), node('npu', Kind.NPU, (Dependency(NodeId('a')),)),
                             node('b', Kind.CPU, (Dependency(NodeId('npu')),))),
             Services({key: ordinary.cpu[key] for key in (ServiceId('a'), ServiceId('b'))}, {ServiceId('npu'): npu[ServiceId('npu')]})),
            ('independent', (node('npu', Kind.NPU), node('later', Kind.CPU, (Dependency(NodeId('npu')),)),
                             node('ready', Kind.CPU)),
             Services({key: ordinary.cpu[key] for key in (ServiceId('ready'), ServiceId('later'))}, {ServiceId('npu'): npu[ServiceId('npu')]})),
            ('worker-vector', (replace(node('a', Kind.CPU), resources=(ResourceId('cpu:0'), ResourceId('cpu:1'))),
                               replace(node('b', Kind.CPU), resources=(ResourceId('cpu:0'),))),
             Services({ServiceId('a'): gang, ServiceId('b'): cpu('b', 2)}, {})),
            ('resource-tail', (node('npu', Kind.NPU), node('other', Kind.NPU),
                               node('a', Kind.CPU, (Dependency(NodeId('npu')),)),
                               node('b', Kind.CPU, (Dependency(NodeId('npu'), Milestone.RESOURCE_READY),))),
             Services({ServiceId('a'): cpu('a', 1), ServiceId('b'): cpu('b', 1)}, npu)),
            ('accepted-edge', (node('npu', Kind.NPU),
                                node('a', Kind.CPU, (Dependency(NodeId('npu'), Milestone.ACCEPTED),))),
             Services({ServiceId('a'): cpu('a', 1)}, {ServiceId('npu'): npu[ServiceId('npu')]})),
            ('exclusions', (node('a', Kind.CPU),
                            Node(NodeId('functional'), Kind.FUNCTIONAL_EMULATION, 'op', 'prefill', 0, (Dependency(NodeId('a')),)),
                            Node(NodeId('wait'), Kind.WAIT, 'op', 'prefill', 0, (Dependency(NodeId('functional')),)),
                            Node(NodeId('excluded'), Kind.EXCLUDED, 'op', 'prefill', 0, (Dependency(NodeId('wait')),)),
                            node('b', Kind.CPU, (Dependency(NodeId('excluded')),))),
             Services({ServiceId('a'): cpu('a', 10), ServiceId('b'): cpu('b', 2)}, {})),
        )
        from sim.cycle.scheduler_sqlite import SqliteScheduleInputs, schedule_sqlite
        for name, nodes, services in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                source, output = Path(directory) / 'input.sqlite', Path(directory) / 'schedule.sqlite'
                inputs = self.inputs(nodes, services)
                write_store(source, inputs)
                scenario = Scenario(1_000_000_000, 'SYNTHETIC')
                expected = schedule(inputs, scenario).record()['nodes']
                # When: the same graph is scheduled from its SQLite representation.
                summary = schedule_sqlite(source, output, SqliteScheduleInputs(inputs.npu_provider, scenario))
                # Then: persisted per-node results preserve all timing and provenance fields.
                with closing(sqlite3.connect(output)) as db:
                    actual = [json.loads(row[0]) for row in db.execute('SELECT body FROM results ORDER BY ordinal')]
                    metadata = [json.loads(row[0]) for row in db.execute('SELECT body FROM metadata')]
                self.assertEqual(actual, expected)
                self.assertEqual(summary['schema'], 'im2p-execution-schedule-sqlite')
                self.assertEqual(summary['version'], 1)
                self.assertEqual(summary['scope'], 'SYNTHETIC')
                self.assertFalse(summary['paper_latency_ready'])
                self.assertIn(summary, metadata)

    def test_exact_rationals_when_npu_acceptance_waits_for_clock_edge(self) -> None:
        # Given: a 1ns CPU prerequisite and a 300MHz NPU with phase-specific evidence.
        from sim.cycle.scheduler_sqlite import SqliteScheduleInputs, schedule_sqlite
        inputs = self.inputs((node('a', Kind.CPU), node('npu', Kind.NPU, (Dependency(NodeId('a')),))),
                             Services({ServiceId('a'): cpu('a', 1)},
                                      {ServiceId('npu'): NpuWork(ServiceId('npu'), 'digest', 'a8w8-d16-hp1')}))
        inputs = replace(inputs, npu_provider=PhaseTable(3, {('a8w8-d16-hp1', 'digest', 1): NpuService(4, 6, 'phase-one')}))
        scenario = Scenario(300_000_000, 'SYNTHETIC')
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'input.sqlite', Path(directory) / 'schedule.sqlite'
            write_store(source, inputs)
            # When: fractional cycle times cross the SQLite storage boundary.
            schedule_sqlite(source, output, SqliteScheduleInputs(inputs.npu_provider, scenario))
            # Then: exact rational results match the existing scheduler.
            with closing(sqlite3.connect(output)) as db:
                actual = [json.loads(row[0]) for row in db.execute('SELECT body FROM results ORDER BY ordinal')]
            self.assertEqual(actual, schedule(inputs, scenario).record()['nodes'])

    def test_large_store_when_more_than_100000_services_share_one_resource(self) -> None:
        # Given: 100001 independent 1ns CPU services sharing one physical worker.
        from sim.cycle.scheduler_sqlite import SqliteScheduleInputs, schedule_sqlite
        count = 100_001
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'input.sqlite', Path(directory) / 'schedule.sqlite'
            with closing(sqlite3.connect(source)) as db:
                store = ExecutionStore(db)
                for index in range(count):
                    identity = f'cpu-{index:06d}'
                    store.add(node(identity, Kind.CPU), services_record(Services({ServiceId(identity): cpu(identity, 1)}, {})))
                store.validate()
                db.execute('INSERT INTO metadata VALUES(?,?)', ('manifest', json.dumps({
                    'schema': 'im2p-execution-sqlite', 'version': 1, 'status': 'PASS',
                    'scope': 'SYNTHETIC', 'node_count': count})))
                db.commit()
            # When: the disk-backed scheduler processes a graph beyond the legacy cap.
            summary = schedule_sqlite(source, output, SqliteScheduleInputs(PhaseTable(1, {}), Scenario(1_000_000_000, 'SYNTHETIC')))
            # Then: count and elapsed time have an independent oracle; never load all rows.
            self.assertEqual(summary['node_count'], count)
            self.assertEqual(summary['completion_ns'], {'numerator': count, 'denominator': 1})
            with closing(sqlite3.connect(output)) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM results').fetchone()[0], count)
                last = json.loads(db.execute('SELECT body FROM results WHERE identity=?', ('cpu-100000',)).fetchone()[0])
            self.assertEqual(last['result_ready_ns'], {'numerator': count, 'denominator': 1})

    def test_rejects_invalid_units_when_service_record_is_corrupted(self) -> None:
        # Given: a valid store whose selected CPU cycle metric has an invalid unit.
        from sim.cycle.scheduler_sqlite import SqliteScheduleInputs, schedule_sqlite
        inputs = self.inputs((node('a', Kind.CPU),), Services({ServiceId('a'): cpu('a', 1)}, {}))
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'input.sqlite', Path(directory) / 'schedule.sqlite'
            write_store(source, inputs)
            invalid = {'schema': 'im2p-execution-services', 'version': 1, 'npu': {}, 'cpu': {'a': {
                'source': 'FULL_CPU', 'policy': 'CPU_WORK_CYCLES_GANG', 'workers': [{'resource': 'cpu:0',
                'worker_id': 0, 'cpu_work_cycles_valid': True, 'cpu_work_cycles': 1,
                'cpu_work_cycles_unit': 'nanosecond', 'cpu_work_cycles_source': 'riscv_cycle', 'cpu_frequency_hz': 1_000_000_000}]}}}
            with closing(sqlite3.connect(source)) as db:
                db.execute('UPDATE services SET body=?', (json.dumps(invalid),))
                db.commit()
            # When: corrupted service metadata reaches scheduling.
            with self.assertRaisesRegex(ExecutionError, 'unit'):
                schedule_sqlite(source, output, SqliteScheduleInputs(inputs.npu_provider, Scenario(1_000_000_000, 'SYNTHETIC')))
            # Then: no partially valid schedule is published.
            self.assertFalse(output.exists())

    def test_error_is_atomic_when_provider_fails_after_cpu_work(self) -> None:
        # Given: valid CPU work followed by NPU work missing its phase-table entry.
        from sim.cycle.scheduler_sqlite import SqliteScheduleInputs, schedule_sqlite
        inputs = self.inputs((node('a', Kind.CPU), node('npu', Kind.NPU, (Dependency(NodeId('a')),))),
                             Services({ServiceId('a'): cpu('a', 1)},
                                      {ServiceId('npu'): NpuWork(ServiceId('npu'), 'digest', 'a8w8-d16-hp1')}))
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'input.sqlite', Path(directory) / 'schedule.sqlite'
            write_store(source, inputs)
            # When: service estimation fails after a predecessor has been scheduled.
            with self.assertRaisesRegex(ExecutionError, 'acceptance-phase'):
                schedule_sqlite(source, output, SqliteScheduleInputs(PhaseTable(1, {}), Scenario(1_000_000_000, 'SYNTHETIC')))
            # Then: the partially scheduled prefix remains unpublished.
            self.assertFalse(output.exists())

    def test_rejects_cycle_when_authoritative_edges_are_corrupted(self) -> None:
        # Given: a formerly valid graph with a causal cycle added after validation.
        from sim.cycle.scheduler_sqlite import SqliteScheduleInputs, schedule_sqlite
        inputs = self.inputs((node('a', Kind.CPU), node('b', Kind.CPU, (Dependency(NodeId('a')),))),
                             Services({ServiceId('a'): cpu('a', 1), ServiceId('b'): cpu('b', 1)}, {}))
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'input.sqlite', Path(directory) / 'schedule.sqlite'
            write_store(source, inputs)
            with closing(sqlite3.connect(source)) as db:
                db.execute('INSERT INTO edges VALUES(?,?,?)', ('a', 'b', 'RESULT_READY'))
                db.commit()
            # When: the authoritative edge table cannot make causal progress.
            with self.assertRaisesRegex(ExecutionError, 'cycl|causal|stall'):
                schedule_sqlite(source, output, SqliteScheduleInputs(inputs.npu_provider, Scenario(1_000_000_000, 'SYNTHETIC')))
            # Then: stale adapter validation cannot permit publication.
            self.assertFalse(output.exists())

    def test_cli_when_sqlite_bundle_is_supplied(self) -> None:
        # Given: a persisted synthetic CPU graph and an actual phase-table document.
        inputs = self.inputs((node('a', Kind.CPU),), Services({ServiceId('a'): cpu('a', 7)}, {}))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_store(root / 'input.sqlite', inputs)
            (root / 'phase.json').write_text(json.dumps({'schema': 'im2p-service-phase-table', 'version': 1,
                'scope': 'SYNTHETIC_ONLY', 'period': 1, 'samples': []}))
            # When: the public command is given a SQLite bundle and SQLite destination.
            result = subprocess.run([sys.executable, '-m', 'sim.cycle.execution_cli', 'schedule',
                '--bundle', str(root / 'input.sqlite'), '--phase-table', str(root / 'phase.json'),
                '--frequency-hz', '1000000000', '--synthetic', '--output', str(root / 'schedule.sqlite')],
                cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True, timeout=20)
            # Then: the command publishes a database containing the expected schedule.
            self.assertEqual(result.returncode, 0, result.stderr)
            with closing(sqlite3.connect(root / 'schedule.sqlite')) as db:
                record = json.loads(db.execute('SELECT body FROM results WHERE identity=?', ('a',)).fetchone()[0])
            self.assertEqual(record['result_ready_ns'], {'numerator': 7, 'denominator': 1})


if __name__ == '__main__':
    unittest.main()
