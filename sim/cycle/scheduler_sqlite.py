from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from fractions import Fraction
import heapq
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

from sim.cycle.execution_ir import Kind, Milestone, Node, ensure, parse_ir
from sim.cycle.execution_services import NpuProvider, Services, parse_services
from sim.cycle.npu_trace import snapshot_inputs, verify_input_snapshots
from sim.cycle.npu_trace_schema import Record, object_value, unique_pairs
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.scheduler import Scenario, ServiceExecutor, rational, scheduled_record, validate_environment


@dataclass(frozen=True, slots=True)
class SqliteScheduleInputs:
    provider: NpuProvider
    scenario: Scenario


def _document(body: str) -> Record:
    return object_value(json.loads(body, object_pairs_hook=unique_pairs))


def _node(body: str) -> Node:
    record = _document(body)
    ensure(record.get('dependencies') == [], 'SQLite edges must be the sole dependency authority')
    return parse_ir({'schema': 'im2p-execution-ir', 'version': 1, 'scope': 'SYNTHETIC',
                     'source_sha256': 'node-shape-validation-only', 'nodes': [record]}).nodes[0]


def _prepare(database: sqlite3.Connection) -> tuple[int, tuple[str, ...]]:
    database.executescript('''PRAGMA cache_size=-16384;
      CREATE TABLE state(identity TEXT PRIMARY KEY, remaining INTEGER, started INTEGER, resource_group TEXT, priority INTEGER);
      CREATE INDEX ready_group ON state(resource_group,started,remaining,priority,identity);
      CREATE TABLE results(identity TEXT PRIMARY KEY, ordinal INTEGER UNIQUE, body TEXT);
      CREATE TABLE metadata(key TEXT PRIMARY KEY,body TEXT);''')
    groups: set[str] = set()
    count = 0
    for identity, body in database.execute('SELECT identity,body FROM ir.nodes'):
        node = _node(str(body))
        ensure(node.identity == identity, 'SQLite node identity/body mismatch')
        group = json.dumps([node.kind == Kind.NPU, sorted(node.resources)], separators=(',', ':'))
        groups.add(group)
        ensure(len(groups) <= 4096, 'explicit resource scenario exceeds bounded scheduler group limit')
        database.execute('INSERT INTO state VALUES(?,0,0,?,?)', (node.identity, group, node.order))
        count += 1
    ensure(count > 0, 'empty SQLite execution graph')
    missing = database.execute('SELECT e.node FROM ir.edges e LEFT JOIN state p ON p.identity=e.parent '
                               'LEFT JOIN state n ON n.identity=e.node WHERE p.identity IS NULL OR n.identity IS NULL LIMIT 1').fetchone()
    ensure(missing is None, 'unresolved SQLite schedule dependency')
    ensure(database.execute('SELECT COUNT(*) FROM ir.edges WHERE milestone NOT IN (?,?,?)',
                            tuple(value.value for value in Milestone)).fetchone()[0] == 0, 'unknown dependency milestone')
    database.execute('UPDATE state SET remaining=(SELECT COUNT(*) FROM ir.edges WHERE node=state.identity)')
    for identity, body in database.execute('SELECT identity,body FROM ir.nodes'):
        node = _node(str(body))
        record = database.execute('SELECT body FROM ir.services WHERE identity=?', (identity,)).fetchone()
        ensure((node.service is not None) == (record is not None), 'missing/extra SQLite service')
    ensure(database.execute('SELECT COUNT(*) FROM ir.services s LEFT JOIN state n ON n.identity=s.identity '
                            'WHERE n.identity IS NULL').fetchone()[0] == 0, 'extra SQLite service')
    return count, tuple(sorted(groups))


def _fire(database: sqlite3.Connection, identity: str, milestone: str) -> None:
    database.execute('UPDATE state SET remaining=remaining-1 WHERE identity IN '
                      '(SELECT node FROM ir.edges WHERE parent=? AND milestone=?)', (identity, milestone))


def _run(database: sqlite3.Connection, inputs: SqliteScheduleInputs) -> Record:
    manifest_row = database.execute("SELECT body FROM ir.metadata WHERE key='manifest'").fetchone()
    ensure(manifest_row is not None, 'missing SQLite execution manifest')
    manifest = _document(str(manifest_row[0]))
    ensure(manifest.get('schema') == 'im2p-execution-sqlite' and manifest.get('version') == 1 and
           manifest.get('status') == 'PASS' and manifest.get('scope') in ('SYNTHETIC', 'PRODUCER_DECLARED'),
           'unsupported SQLite execution input')
    scope = 'BOUND_DATASET' if manifest['scope'] == 'PRODUCER_DECLARED' else 'SYNTHETIC'
    validate_environment(scope, inputs.provider, inputs.scenario)
    count, groups = _prepare(database)
    engine = ServiceExecutor(inputs.provider, inputs.scenario)
    events: list[tuple[Fraction, str, str]] = []
    now = result_end = resource_end = Fraction(0)
    finished = 0
    while finished < count:
        while events and events[0][0] <= now:
            _, identity, milestone = heapq.heappop(events)
            _fire(database, identity, milestone)
        candidates: list[Node] = []
        future: list[Fraction] = []
        for group in groups:
            row = database.execute('SELECT n.body FROM state s JOIN ir.nodes n ON n.identity=s.identity '
                'WHERE s.resource_group=? AND s.started=0 AND s.remaining=0 ORDER BY s.priority,s.identity LIMIT 1', (group,)).fetchone()
            if row is None:
                continue
            node = _node(str(row[0]))
            earliest = engine.earliest(node, now)
            if earliest == now:
                candidates.append(node)
            else:
                future.append(earliest)
        if not candidates:
            if events:
                future.append(events[0][0])
            ensure(bool(future), 'execution stalled: causal cycle or unresolved resource dependency')
            now = min(future)
            continue
        node = min(candidates, key=lambda candidate: (candidate.order, candidate.identity))
        row = database.execute('SELECT body FROM ir.services WHERE identity=?', (node.identity,)).fetchone()
        services = Services({}, {}) if row is None else parse_services(_document(str(row[0])))
        ensure(set(services.cpu) | set(services.npu) == ({node.service} if node.service is not None else set()),
               'SQLite service identity mismatch')
        completed = engine.execute(node, services, now)
        database.execute('UPDATE state SET started=1 WHERE identity=?', (node.identity,))
        database.execute('INSERT INTO results VALUES(?,?,?)',
                          (node.identity, finished, json.dumps(scheduled_record(completed), separators=(',', ':'))))
        for milestone in Milestone:
            epoch = completed.milestone(milestone)
            if epoch <= now:
                _fire(database, node.identity, milestone.value)
            else:
                heapq.heappush(events, (epoch, node.identity, milestone.value))
        result_end = max(result_end, completed.result_ready_ns)
        resource_end = max(resource_end, completed.resource_ready_ns)
        finished += 1
        if finished % 10_000 == 0:
            ensure(len(events) <= 16384, 'scenario exceeds bounded concurrent event capacity')
    return {'schema': 'im2p-execution-schedule-sqlite', 'version': 1, 'scope': inputs.scenario.scope,
            'node_count': count, 'completion_ns': rational(result_end), 'resource_completion_ns': rational(resource_end),
            'service_validation_scope': inputs.provider.validation_scope, 'paper_latency_ready': False,
            'time_unit': 'nanosecond-rational', 'queue_policy': 'READY_ORDER_THEN_ID',
            'clock_alignment': 'CEIL_TO_NPU_EDGE', 'worker_policy': 'EXPLICIT_GANG_VECTOR',
            'validated_service_reconstruction': inputs.scenario.scope == 'RECONSTRUCTED'}


def schedule_sqlite(source: Path, output: Path, inputs: SqliteScheduleInputs) -> Record:
    ensure(source.resolve() != output.resolve() and not output.exists(), 'schedule output must be new and distinct')
    ensure(shutil.disk_usage(output.parent).free > 256 * 1024 * 1024, 'scheduler output storage reserve exhausted')
    with tempfile.TemporaryDirectory(prefix='schedule-sqlite-', dir=output.parent) as temporary:
        root = Path(temporary)
        snapshots = snapshot_inputs((source,), root)
        staged = root / 'result.sqlite'
        with closing(sqlite3.connect(staged, uri=True)) as database:
            database.execute('ATTACH DATABASE ? AS ir', (snapshots[0].snapshot.resolve().as_uri() + '?mode=ro',))
            summary = _run(database, inputs)
            summary['input_sqlite_sha256'] = sha256(snapshots[0].snapshot)
            database.execute('INSERT INTO metadata VALUES(?,?)', ('manifest', json.dumps(summary, sort_keys=True)))
            database.commit()
        verify_input_snapshots(snapshots, 'SQLite scheduling')
        os.link(staged, output)
    return summary


def read_schedule_node(path: Path, identity: str) -> Record:
    with closing(sqlite3.connect(path.resolve(strict=True).as_uri() + '?mode=ro', uri=True)) as database:
        manifest = database.execute("SELECT body FROM metadata WHERE key='manifest'").fetchone()
        ensure(manifest is not None, 'missing SQLite schedule manifest')
        header = _document(str(manifest[0]))
        ensure(header.get('schema') == 'im2p-execution-schedule-sqlite' and header.get('version') == 1,
               'not a current SQLite schedule artifact')
        row = database.execute('SELECT body FROM results WHERE identity=?', (identity,)).fetchone()
        ensure(row is not None, 'missing schedule node: ' + identity)
        record = _document(str(row[0]))
        ensure(record.get('node_id') == identity, 'schedule node identity mismatch')
        return record
