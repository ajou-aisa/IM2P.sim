from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from fractions import Fraction
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sim.cycle.execution_cli import Arguments, verify_schedule
from sim.cycle.execution_cycle_provider import BoundCycleRequest, CycleServiceProvider
from sim.cycle.execution_ir import Dependency, ExecutionError, ExecutionIR, Kind, Node, NodeId, ResourceId, ServiceId, ir_record
from sim.cycle.execution_services import CpuService, NpuWork, Services, WorkerService, parse_services, services_record
from sim.cycle.execution_stream import ExecutionStore
from sim.cycle.npu_trace_schema import Record, integer, object_value
from sim.cycle.reconstruct_graph import array, sha256
from sim.cycle.scheduler import Scenario, ScheduleInputs, schedule
from sim.cycle.scheduler_sqlite import SqliteScheduleInputs, schedule_sqlite
from sim.cycle.service_certificate import ServiceAdmission


class AdmittedCpuProvider(CycleServiceProvider):
    def __init__(self, library: Path) -> None:
        self.library = library
        self.requests = {}
        self.completed = set()
        self.admission = ServiceAdmission('service', 'base', 'run', 'trace', 'scenario', 'profile', 0)


class ScheduleVerifierTests(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Arguments, ScheduleInputs, Scenario]:
        library = root / 'library'
        library.write_bytes(b'fixture')
        clock = root / 'clock.json'
        clock.write_text('{}')
        nodes = (
            Node(NodeId('cpu'), Kind.CPU, 'op', 'prefill', 0, (), ServiceId('cpu'), (ResourceId('cpu:0'),)),
            Node(NodeId('application:sample:0'), Kind.APPLICATION_CPU, 'sample', 'decode', 1,
                 (Dependency(NodeId('cpu')),), ServiceId('application:sample:0'), (ResourceId('cpu:0'),)),
        )
        workers = (WorkerService(ResourceId('cpu:0'), Fraction(7), 0, 'thread_cpu_clock', 'nanosecond', 7),)
        services = Services({ServiceId('cpu'): CpuService('FULL_CPU', 'THREAD_CPU_NS_GANG', workers),
                             ServiceId('application:sample:0'): CpuService('APPLICATION_CPU', 'THREAD_CPU_NS_GANG', workers)}, {})
        services = parse_services(services_record(services))
        inputs = ScheduleInputs(ExecutionIR(nodes, 'BOUND_DATASET', 'dataset'), services, AdmittedCpuProvider(library))
        arguments = Arguments()
        arguments.bundle = root / 'bundle.json'
        arguments.schedule = root / 'schedule.json'
        arguments.cycle_library = library
        arguments.clock_selection = clock
        arguments.profile = 'profile'
        scenario = Scenario(1_000_000_000, 'RECONSTRUCTED', clock, 'profile')
        return arguments, inputs, scenario

    def write_bound_store(self, path: Path, inputs: ScheduleInputs) -> None:
        with closing(sqlite3.connect(path)) as database:
            store = ExecutionStore(database)
            for node in inputs.ir.nodes:
                service_id = node.service
                assert service_id is not None
                store.add(node, services_record(Services({service_id: inputs.services.cpu[service_id]}, {})))
            store.validate()
            database.execute('INSERT INTO metadata VALUES(?,?)', ('manifest', json.dumps({
                'schema': 'im2p-execution-sqlite', 'version': 1, 'status': 'PASS',
                'scope': 'PRODUCER_DECLARED', 'node_count': store.count})))
            database.commit()

    def test_rejects_forged_application_endpoint_when_json_schedule(self) -> None:
        # Given: a bound execution bundle and a certified-scope schedule with one altered sample endpoint.
        with tempfile.TemporaryDirectory() as directory:
            args, inputs, scenario = self.fixture(Path(directory))
            args.bundle.write_text(json.dumps({'schema': 'im2p-execution-bundle', 'version': 1,
                                               'ir': ir_record(inputs.ir), 'services': services_record(inputs.services),
                                               'dataset_sha256': 'dataset', 'join_summary_sha256': 'join',
                                               'lifecycle_sha256': 'lifecycle'}))
            with patch('scripts.evaluation_clock.load_selection', return_value=SimpleNamespace(frequency_hz=1_000_000_000)):
                record = schedule(inputs, scenario).record()
                library, clock = Path(directory) / 'library', Path(directory) / 'clock.json'
                record.update(input_bundle_sha256=sha256(args.bundle), phase_table_sha256=None,
                              cycle_library_sha256=sha256(library), clock_selection_sha256=sha256(clock))
                args.schedule.write_text(json.dumps(record))
                with patch('sim.cycle.execution_cli.provider', return_value=AdmittedCpuProvider(library)):
                    self.assertEqual(verify_schedule(args)['status'], 'PASS')
                    sample = object_value(array(record['nodes'])[1])
                    endpoint = object_value(sample['result_ready_ns'])
                    sample['result_ready_ns'] = {'numerator': integer(endpoint, 'numerator'),
                                                 'denominator': True}
                    args.schedule.write_text(json.dumps(record))
                    # When: the altered rational endpoint reaches the verifier.
                    with self.assertRaisesRegex(ValueError, 'node|endpoint'):
                        verify_schedule(args)
                    # Then: a valid service-scope label cannot bless a forged application endpoint.

    def test_rejects_synthetic_json_input_even_when_schedule_hash_is_updated(self) -> None:
        # Given: a reconstructed schedule whose source bundle is relabelled synthetic.
        with tempfile.TemporaryDirectory() as directory:
            args, inputs, scenario = self.fixture(Path(directory))
            bundle: Record = {'schema': 'im2p-execution-bundle', 'version': 1,
                              'ir': ir_record(inputs.ir), 'services': services_record(inputs.services),
                              'dataset_sha256': 'dataset'}
            args.bundle.write_text(json.dumps(bundle))
            with patch('scripts.evaluation_clock.load_selection', return_value=SimpleNamespace(frequency_hz=1_000_000_000)):
                record = schedule(inputs, scenario).record()
                record.update(input_bundle_sha256=sha256(args.bundle), phase_table_sha256=None,
                              cycle_library_sha256=sha256(Path(directory) / 'library'),
                              clock_selection_sha256=sha256(Path(directory) / 'clock.json'))
                object_value(bundle['ir'])['scope'] = 'SYNTHETIC'
                args.bundle.write_text(json.dumps(bundle))
                record['input_bundle_sha256'] = sha256(args.bundle)
                args.schedule.write_text(json.dumps(record))
                # When: the verifier reads the actual source scope.
                with patch('sim.cycle.execution_cli.provider', return_value=AdmittedCpuProvider(Path(directory) / 'library')):
                    with self.assertRaisesRegex(ValueError, 'bound dataset'):
                        verify_schedule(args)
                # Then: matching schedule hashes cannot admit a synthetic source.

    def test_rejects_trace_work_omitted_from_schedule(self) -> None:
        # Given: admitted trace work with no corresponding NPU node in the bound graph.
        with tempfile.TemporaryDirectory() as directory:
            _, inputs, scenario = self.fixture(Path(directory))
            provider = inputs.npu_provider
            assert isinstance(provider, AdmittedCpuProvider)
            provider.requests[ServiceId('npu:missing')] = BoundCycleRequest(
                NpuWork(ServiceId('npu:missing'), 'request', 'profile'), {})
            assert provider.admission is not None
            provider.admission = replace(provider.admission, work_count=1)
            # When: reconstruction reaches the trace coverage gate.
            with patch('scripts.evaluation_clock.load_selection', return_value=SimpleNamespace(frequency_hz=1_000_000_000)):
                with self.assertRaisesRegex(ExecutionError, 'bound trace work'):
                    schedule(inputs, scenario)
            # Then: an admitted trace cannot be partly omitted.

    def test_rejects_forged_cpu_endpoint_when_sqlite_schedule(self) -> None:
        # Given: an actual producer-declared SQLite input and a valid schedule.
        with tempfile.TemporaryDirectory() as directory:
            args, inputs, scenario = self.fixture(Path(directory))
            args.bundle = Path(directory) / 'bundle.sqlite'
            args.schedule = Path(directory) / 'schedule.sqlite'
            self.write_bound_store(args.bundle, inputs)
            with patch('scripts.evaluation_clock.load_selection', return_value=SimpleNamespace(frequency_hz=1_000_000_000)):
                schedule_sqlite(args.bundle, args.schedule, SqliteScheduleInputs(inputs.npu_provider, scenario))
                with patch('sim.cycle.execution_cli.provider', return_value=AdmittedCpuProvider(Path(directory) / 'library')):
                    self.assertEqual(verify_schedule(args)['status'], 'PASS')
                    with closing(sqlite3.connect(args.schedule)) as database:
                        body = json.loads(database.execute('SELECT body FROM results WHERE identity=?', ('cpu',)).fetchone()[0])
                        body['resource_ready_ns']['numerator'] += 1
                        database.execute('UPDATE results SET body=? WHERE identity=?', (json.dumps(body), 'cpu'))
                        database.commit()
                    # When: the altered CPU rational endpoint reaches the verifier.
                    with self.assertRaisesRegex(ValueError, 'node|endpoint'):
                        verify_schedule(args)
                    # Then: the verifier compares every disk-backed row.

    def test_rejects_synthetic_sqlite_input_even_when_schedule_hash_is_updated(self) -> None:
        # Given: a reconstructed SQLite schedule whose source manifest is relabelled synthetic.
        with tempfile.TemporaryDirectory() as directory:
            args, inputs, scenario = self.fixture(Path(directory))
            args.bundle = Path(directory) / 'bundle.sqlite'
            args.schedule = Path(directory) / 'schedule.sqlite'
            self.write_bound_store(args.bundle, inputs)
            with patch('scripts.evaluation_clock.load_selection', return_value=SimpleNamespace(frequency_hz=1_000_000_000)):
                schedule_sqlite(args.bundle, args.schedule, SqliteScheduleInputs(inputs.npu_provider, scenario))
                with closing(sqlite3.connect(args.bundle)) as database:
                    manifest = json.loads(database.execute("SELECT body FROM metadata WHERE key='manifest'").fetchone()[0])
                    manifest['scope'] = 'SYNTHETIC'
                    database.execute("UPDATE metadata SET body=? WHERE key='manifest'", (json.dumps(manifest),))
                    database.commit()
                with closing(sqlite3.connect(args.schedule)) as database:
                    manifest = json.loads(database.execute("SELECT body FROM metadata WHERE key='manifest'").fetchone()[0])
                    manifest['input_sqlite_sha256'] = sha256(args.bundle)
                    database.execute("UPDATE metadata SET body=? WHERE key='manifest'", (json.dumps(manifest),))
                    database.commit()
                # When: the verifier replays the source manifest.
                with patch('sim.cycle.execution_cli.provider', return_value=AdmittedCpuProvider(Path(directory) / 'library')):
                    with self.assertRaisesRegex(ValueError, 'bound dataset'):
                        verify_schedule(args)
                # Then: matching schedule hashes cannot admit a synthetic SQLite source.


if __name__ == '__main__':
    unittest.main()
