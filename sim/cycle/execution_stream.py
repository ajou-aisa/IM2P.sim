from __future__ import annotations

from collections import Counter
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import shutil
import tempfile

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.certificate_contract import read_document
from sim.cycle.execution_adapter import AdapterFiles, validate_lifecycle_contract
from sim.cycle.execution_application import ApplicationSource, project_application
from sim.cycle.execution_ir import Dependency, Kind, Milestone, Node, NodeId, ResourceId, ServiceId, ensure, node_record
from sim.cycle.execution_services import NpuWork, Services, bind_cpu_resource, cpu_service, services_record
from sim.cycle.npu_trace_schema import Record, integer, object_value, text
from sim.cycle.reconstruct_graph import array, json_records, sha256


class ExecutionStore:
    def __init__(self, database: sqlite3.Connection) -> None:
        self.db = database
        database.executescript('''PRAGMA cache_size=-16384;
          CREATE TABLE nodes(identity TEXT PRIMARY KEY, kind TEXT, operation TEXT, body TEXT, pending INTEGER);
          CREATE TABLE edges(node TEXT, parent TEXT, milestone TEXT, PRIMARY KEY(node,parent,milestone));
          CREATE INDEX parent_edges ON edges(parent);
          CREATE INDEX ready_nodes ON nodes(pending);
          CREATE TABLE services(identity TEXT PRIMARY KEY, body TEXT);
          CREATE TABLE metadata(key TEXT PRIMARY KEY, body TEXT);''')
        self.count = 0
        self.service_count = 0

    def add(self, node: Node, service: Record | None = None) -> None:
        body = node_record(node)
        body['dependencies'] = []
        self.db.execute('INSERT INTO nodes VALUES(?,?,?,?,0)',
                        (node.identity, node.kind.value, node.operation, json.dumps(body, separators=(',', ':'))))
        self.edges(node.identity, node.dependencies)
        self.count += 1
        if service is not None:
            self.db.execute('INSERT INTO services VALUES(?,?)', (node.identity, json.dumps(service, separators=(',', ':'))))
            self.service_count += 1

    def edges(self, identity: NodeId, dependencies: tuple[Dependency, ...]) -> None:
        self.db.executemany('INSERT INTO edges VALUES(?,?,?)',
                            ((identity, edge.node, edge.milestone.value) for edge in dependencies))

    def validate(self) -> None:
        missing = self.db.execute('SELECT e.node FROM edges e LEFT JOIN nodes p ON p.identity=e.parent '
                                  'LEFT JOIN nodes n ON n.identity=e.node WHERE p.identity IS NULL OR n.identity IS NULL LIMIT 1').fetchone()
        ensure(missing is None, 'unresolved SQLite execution dependency')
        self.db.execute('UPDATE nodes SET pending=(SELECT COUNT(DISTINCT parent) FROM edges WHERE node=nodes.identity)')
        visited = 0
        while True:
            ready = self.db.execute('SELECT identity FROM nodes WHERE pending=0 LIMIT 1024').fetchall()
            if not ready:
                break
            for row in ready:
                identity = str(row[0])
                self.db.execute('UPDATE nodes SET pending=-1 WHERE identity=?', (identity,))
                self.db.execute('UPDATE nodes SET pending=pending-1 WHERE identity IN '
                                '(SELECT node FROM edges WHERE parent=?)', (identity,))
                visited += 1
        ensure(visited == self.count, 'cyclic SQLite execution graph')


def adapt_stream(files: AdapterFiles, output: Path) -> Record:
    contract = read_document(files.lifecycle)
    validate_lifecycle_contract(contract)
    ensure(not output.exists(), 'execution store output must be new')
    for path, expected in ((files.dataset, contract['dataset_sha256']), (files.npu_results, contract['npu_results_sha256'])):
        ensure(sha256(path) == expected, 'lifecycle input binding mismatch')
    operations: dict[str, Record] = {}
    for row in json_records(files.dataset):
        if row['kind'] == 'OPERATION_CONTAINER':
            identity = text(row, 'node_id')
            ensure(identity not in operations, 'duplicate operation container')
            operations[identity] = row
    entry = object_value(contract['entry_dependencies'])
    ensure(bool(operations) and set(entry) == set(operations), 'phase/graph entry coverage mismatch')
    resources = object_value(contract['worker_resources'])
    submission = [text({'id': value}, 'id') for value in array(contract['submission_order'])]
    ensure(len(set(submission)) == len(submission), 'duplicate submission order')
    order = {identity: index for index, identity in enumerate(submission)}
    slots = object_value(contract['call_slots'])
    ensure(all(value is None for value in slots.values()), 'streaming production adapter currently supports FULL only')
    results: dict[str, NpuWork] = {}
    calls: set[str] = set()
    for row in json_records(files.npu_results):
        ensure(row.get('scope') in ('full', 'residual_compact') and row.get('cycle_model_validation') == 'CURRENT_CERTIFIED',
               'certified FULL/run-aware NPU results required')
        identity, call = 'npu:' + str(integer(row, 'work_id')), str(integer(row, 'call_id'))
        ensure(identity not in results and call not in calls, 'duplicate work/call result')
        results[identity] = NpuWork(ServiceId(identity), text(row, 'run_view_sha256'), text(row, 'profile'))
        calls.add(call)
    ensure(set(results) == set(submission) and calls == set(slots), 'NPU result/submission/call coverage mismatch')

    def edge(value: JsonValue) -> Dependency:
        identity = text({'id': value}, 'id')
        return Dependency(NodeId(identity + ':exit' if identity in operations else identity))

    with tempfile.TemporaryDirectory(prefix='execution-store-', dir=output.parent) as temporary:
        staged = Path(temporary) / 'execution.sqlite'
        with closing(sqlite3.connect(staged)) as db:
            store = ExecutionStore(db)
            available: dict[NodeId, Kind] = {}
            members: Counter[str] = Counter()
            counts: Counter[str] = Counter()
            for identity, row in operations.items():
                phase = json.dumps(row['phase'], sort_keys=True)
                incoming = tuple(dict.fromkeys(edge(value) for value in array(row['dependencies']) + array(entry[identity])))
                begin, end = NodeId(identity + ':enter'), NodeId(identity + ':exit')
                store.add(Node(begin, Kind.OP_ENTER, identity, phase, 0, incoming))
                store.add(Node(end, Kind.OP_EXIT, identity, phase, 0, ()))
                available[begin], available[end] = Kind.OP_ENTER, Kind.OP_EXIT
            for row in json_records(files.dataset):
                counts['input_record_count'] += 1
                if row['kind'] == 'OPERATION_CONTAINER':
                    continue
                identity, operation = text(row, 'node_id'), text(row, 'operation_node_id')
                ensure(operation in operations, 'execution service has unknown operation')
                phase = json.dumps(operations[operation]['phase'], sort_keys=True)
                dependencies = tuple(dict.fromkeys([edge(value) for value in array(row['dependencies'])] +
                                                   [Dependency(NodeId(operation + ':enter'))]))
                service, claims, priority, service_record = None, (), 0, None
                match row['kind']:
                    case 'CALL_BOUNDARY':
                        ensure(row.get('call_kind') != 'STRIPE' and row.get('stage') != 'PUBLISH',
                               'PIPELINE requires verified target slot/fence lifecycle')
                        kind = Kind.BARRIER
                    case 'SERVICE':
                        match row['node_class']:
                            case 'ORDINARY_CPU' | 'POTAL_HOST':
                                kind, service = Kind.CPU, ServiceId(identity)
                                source = text(row, 'duration_source')
                                ensure(source == ('FULL_CPU' if row['node_class'] == 'ORDINARY_CPU' else 'POTAL_COLLECTION'),
                                       'CPU duration authority mismatch')
                                duration = object_value(row['duration'])
                                raw = array(duration['worker_intervals']) if row['node_class'] == 'ORDINARY_CPU' else [duration]
                                workers: list[JsonValue] = []
                                for value in raw:
                                    sample = object_value(value)
                                    workers.append(bind_cpu_resource(sample, source, resources))
                                cpu = cpu_service({'source': source, 'policy': contract['cpu_policy'], 'workers': workers})
                                claims = tuple(worker.resource for worker in cpu.workers)
                                service_record = services_record(Services({service: cpu}, {}))
                            case 'TARGET_NPU':
                                ensure(identity in results, 'missing NPU result')
                                kind, service, priority = Kind.NPU, ServiceId(identity), order[identity]
                                claims = (ResourceId('npu:0'),)
                                service_record = services_record(Services({}, {service: results[identity]}))
                                if priority:
                                    dependencies += (Dependency(NodeId(submission[priority - 1]), Milestone.ACCEPTED),)
                            case 'FUNCTIONAL_EMULATION' | 'WAIT' | 'EXCLUDED':
                                kind = Kind(str(row['node_class']))
                            case _:
                                ensure(False, 'unsupported streamed service class')
                                kind = Kind.EXCLUDED
                    case _:
                        ensure(False, 'unsupported structural record')
                        kind = Kind.EXCLUDED
                store.add(Node(NodeId(identity), kind, operation, phase, priority, dependencies, service, claims), service_record)
                store.edges(NodeId(operation + ':exit'), (Dependency(NodeId(identity)),))
                members[operation] += 1
                counts[str(row['node_class']) if row['kind'] == 'SERVICE' else 'STRUCTURAL'] += 1
                if store.count % 10_000 == 0:
                    ensure(shutil.disk_usage(output.parent).free >= 256 * 1024 * 1024, 'execution IR storage reserve exhausted')
            ensure(set(members) == set(operations), 'operation without final completion members')
            ensure(counts['TARGET_NPU'] == len(results), 'unused NPU service results')
            for value in array(contract['barriers']):
                row = object_value(value)
                identity = NodeId(text(row, 'node_id'))
                dependencies = tuple(edge(value) for value in array(row['dependencies']))
                store.add(Node(identity, Kind.BARRIER, identity, text(row, 'phase'), 0, dependencies))
                available[identity] = Kind.BARRIER
            if files.application is not None:
                declaration = object_value(contract['application'])
                ensure(declaration['sha256'] == sha256(files.application), 'application binding mismatch')
                application = project_application(available, ApplicationSource(tuple(json_records(files.application)), declaration))
                for node in application.nodes:
                    service = application.services.get(ServiceId(node.identity))
                    stored = None if service is None else services_record(Services({ServiceId(node.identity): service}, {}))
                    store.add(node, stored)
                for identity, dependencies in application.entry_edges.items():
                    store.edges(identity, dependencies)
            else:
                ensure(contract['source_kind'] == 'SYNTHETIC' and 'application' not in contract, 'bound application input required')
            store.validate()
            result: Record = {'schema': 'im2p-execution-sqlite', 'version': 1, 'status': 'PASS',
                'scope': contract['source_kind'], 'input_record_count': counts['input_record_count'],
                'node_count': store.count, 'service_count': store.service_count, 'structural_dag': 'PASS',
                'dataset_sha256': contract['dataset_sha256'], 'lifecycle_sha256': sha256(files.lifecycle),
                'class_counts': dict(counts), 'system_latency': 'NOT_MODELED', 'storage': 'SQLITE_BOUNDED_CACHE_16MIB',
                'dependency_storage': 'edges table is authoritative; node body omits dependencies'}
            db.execute('INSERT INTO metadata VALUES(?,?)', ('manifest', json.dumps(result)))
            db.execute('INSERT INTO metadata VALUES(?,?)', ('lifecycle', json.dumps(contract)))
            db.commit()
        ensure(sha256(files.dataset) == contract['dataset_sha256'], 'dataset changed during adaptation')
        os.link(staged, output)
    return result
