from __future__ import annotations

import heapq
import json
import os
import sqlite3
import tempfile
from array import array
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Final, TypeAlias, assert_never, cast

from sim.cycle.execution_ir import (
    Kind,
    Milestone,
    Node,
    NodeId,
    ResourceId,
    ServiceId,
    ensure,
    parse_ir,
)
from sim.cycle.execution_sequence_provider import (
    DiagnosticStatefulProvider,
    StatefulSequenceProvider,
)
from sim.cycle.execution_services import Services, parse_services
from sim.cycle.input_snapshot import (
    COPY_RESERVE_BYTES,
    SQLITE_WORKING_SET_FACTOR,
    require_storage_budget,
)
from sim.cycle.npu_trace import snapshot_inputs, verify_input_snapshots
from sim.cycle.npu_trace_schema import Record, integer, object_value
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.scheduler import (
    Scenario,
    ScheduledNode,
    ServiceExecutor,
    TimingProvider,
    complete_provider,
    rational,
    schedule_source_binding,
    scheduled_record,
    service_binding,
    validate_environment,
    validation_scope,
)
from sim.cycle.strict_json import strict_loads

Queue: TypeAlias = list[tuple[int, str, int, Node]]
# Receives every scheduled node once, in schedule order, after its row is stored: (ordinal, node, endpoints, row).
Observer: TypeAlias = Callable[[int, Node, ScheduledNode, Record], None]
FIRE_ORDER: Final = tuple(Milestone)
SLOT: Final = {milestone.value: slot for slot, milestone in enumerate(FIRE_ORDER)}
KINDS: Final = tuple(Kind)
KIND_CODE: Final = {kind: code for code, kind in enumerate(KINDS)}
RESULT_BATCH: Final = 2048
SQLITE_INTEGER_LIMIT: Final = 1 << 63
_COMPACT = json.JSONEncoder(separators=(',', ':'))


@dataclass(frozen=True, slots=True)
class SqliteScheduleInputs:
    provider: TimingProvider
    scenario: Scenario


def _document(body: str) -> Record:
    return object_value(strict_loads(body))


def _same_record(actual: Record, expected: Record) -> bool:
    return (json.dumps(actual, sort_keys=True, separators=(',', ':'), allow_nan=False) ==
            json.dumps(expected, sort_keys=True, separators=(',', ':'), allow_nan=False))


def _node(body: str) -> Node:
    record = _document(body)
    ensure(record.get('dependencies') == [], 'SQLite edges must be the sole dependency authority')
    return parse_ir({'schema': 'im2p-execution-ir', 'version': 1, 'scope': 'SYNTHETIC',
                     'source_sha256': 'node-shape-validation-only', 'nodes': [record]}).nodes[0]


class _Graph:
    """The validated IR as columns (no object per node) with CSR child lists per (parent, milestone).

    A Node object exists only while its node is ready and unstarted (queued); `remaining` counts every incoming edge
    and reaches zero when all of them fired, exactly when the former `state.remaining` did."""

    def __init__(self) -> None:
        self.index: dict[str, int] = {}
        self.identity: list[str] = []
        self.kind = bytearray()
        self.order = array('q')
        self.operation: list[str] = []
        self.phase: list[str] = []
        self.service: list[ServiceId | None] = []
        self.resources: list[tuple[ResourceId, ...]] = []
        self.group = array('H')
        self.remaining = array('l')
        self.offsets = array('Q')
        self.children = array('I')
        self._interned: dict[object, object] = {}

    def _intern(self, value: object) -> object:
        return self._interned.setdefault(value, value)

    def add(self, node: Node, group: int) -> None:
        if node.identity in self.index:
            raise sqlite3.IntegrityError('UNIQUE constraint failed: state.identity')
        if node.order >= SQLITE_INTEGER_LIMIT:
            raise OverflowError('Python int too large to convert to SQLite INTEGER')
        self.index[node.identity] = len(self.identity)
        self.identity.append(node.identity)
        self.kind.append(KIND_CODE[node.kind])
        self.order.append(node.order)
        self.operation.append(cast(str, self._intern(node.operation)))
        self.phase.append(cast(str, self._intern(node.phase)))
        self.service.append(node.identity if node.service == node.identity else node.service)  # type: ignore[arg-type]
        self.resources.append(cast(tuple[ResourceId, ...], self._intern(node.resources)))
        self.group.append(group)

    def link(self, database: sqlite3.Connection) -> None:
        """One validation pass over the authoritative edges (endpoint and milestone checks, incoming counts), then
        a second pass filling the child lists of each (parent, milestone)."""
        nodes = len(self.identity)
        counts = array('Q', bytes(8 * 3 * nodes))
        self.remaining = array('l', bytes(self.remaining.itemsize * nodes))
        missing = unknown = False
        index = self.index
        for parent, milestone, node in database.execute('SELECT parent,milestone,node FROM ir.edges'):
            source, child = index.get(parent), index.get(node)
            if source is None or child is None:
                missing = True
                continue
            self.remaining[child] += 1
            slot = SLOT.get(milestone) if isinstance(milestone, str) else None
            if slot is None:
                unknown = unknown or milestone is not None  # a NULL milestone was never an unknown one; it never fires
                continue
            counts[3 * source + slot] += 1
        ensure(not missing, 'unresolved SQLite schedule dependency')
        ensure(not unknown, 'unknown dependency milestone')
        self.offsets = array('Q', bytes(8 * (3 * nodes + 1)))
        total = 0
        for position in range(3 * nodes):
            self.offsets[position] = total
            total += counts[position]
        self.offsets[3 * nodes] = total
        self.children = array('I', bytes(4 * total))
        cursor = counts
        for position in range(3 * nodes):
            cursor[position] = self.offsets[position]
        for parent, milestone, node in database.execute('SELECT parent,milestone,node FROM ir.edges'):
            slot = SLOT.get(milestone) if isinstance(milestone, str) else None
            if slot is not None:
                position = 3 * index[parent] + slot
                self.children[cursor[position]] = index[node]
                cursor[position] += 1

    def node(self, index: int) -> Node:
        return Node(NodeId(self.identity[index]), KINDS[self.kind[index]], self.operation[index], self.phase[index],
                    self.order[index], (), self.service[index], self.resources[index])

    def ready(self, index: int, queues: list[Queue]) -> None:
        heapq.heappush(queues[self.group[index]], (self.order[index], self.identity[index], index, self.node(index)))

    def release(self, index: int, slot: int, queues: list[Queue]) -> None:
        position = 3 * index + slot
        for child in self.children[self.offsets[position]:self.offsets[position + 1]]:
            self.remaining[child] -= 1
            if self.remaining[child] == 0:
                self.ready(child, queues)


def _prepare(database: sqlite3.Connection) -> tuple[_Graph, list[Queue]]:
    """Validate the IR with one parse per node and two passes over the edges; the SQLite output holds only the
    schedule results and its manifest (readiness lives in the compact graph)."""
    database.executescript('''PRAGMA cache_size=-16384;
      CREATE TABLE results(identity TEXT PRIMARY KEY, ordinal INTEGER UNIQUE, body TEXT);
      CREATE TABLE metadata(key TEXT PRIMARY KEY,body TEXT);''')
    graph = _Graph()
    groups: dict[str, int] = {}
    for identity, body in database.execute('SELECT identity,body FROM ir.nodes'):
        node = _node(str(body))
        ensure(node.identity == identity, 'SQLite node identity/body mismatch')
        group = json.dumps([node.kind == Kind.NPU, sorted(node.resources)], separators=(',', ':'))
        code = groups.setdefault(group, len(groups))
        ensure(len(groups) <= 4096, 'explicit resource scenario exceeds bounded scheduler group limit')
        graph.add(node, code)
    ensure(bool(graph.identity), 'empty SQLite execution graph')
    graph.link(database)
    for identity, service in database.execute('SELECT n.identity,s.identity IS NOT NULL FROM ir.nodes n '
                                               'LEFT JOIN ir.services s ON s.identity=n.identity ORDER BY n.rowid'):
        ensure((graph.service[graph.index[str(identity)]] is not None) == bool(service), 'missing/extra SQLite service')
    ensure(database.execute('SELECT COUNT(*) FROM ir.services s LEFT JOIN ir.nodes n ON n.identity=s.identity '
                            'WHERE n.identity IS NULL').fetchone()[0] == 0, 'extra SQLite service')
    queues: list[Queue] = [[] for _ in groups]
    for index, remaining in enumerate(graph.remaining):
        if remaining == 0:
            graph.ready(index, queues)
    return graph, queues


def _request_available(database: sqlite3.Connection, node: Node) -> Fraction:
    ready = Fraction(0)
    for milestone, body in database.execute(
        'SELECT e.milestone,r.body FROM ir.edges e JOIN results r ON r.identity=e.parent WHERE e.node=?',
        (node.identity,),
    ):
        match Milestone(str(milestone)):
            case Milestone.ACCEPTED:
                key = 'accepted_ns'
            case Milestone.RESULT_READY:
                key = 'result_ready_ns'
            case Milestone.RESOURCE_READY:
                key = 'resource_ready_ns'
            case unreachable:
                assert_never(unreachable)
        epoch = object_value(_document(str(body))[key])
        ready = max(ready, Fraction(integer(epoch, 'numerator'), integer(epoch, 'denominator')))
    return ready


def _run(database: sqlite3.Connection, inputs: SqliteScheduleInputs,
         observed: sqlite3.Connection | None = None, observer: Observer | None = None) -> Record:
    stateful = isinstance(inputs.provider, (DiagnosticStatefulProvider, StatefulSequenceProvider))
    source_binding = schedule_source_binding(Path(__file__)) if stateful else {}
    manifest_row = database.execute("SELECT body FROM ir.metadata WHERE key='manifest'").fetchone()
    ensure(manifest_row is not None, 'missing SQLite execution manifest')
    manifest = _document(str(manifest_row[0]))
    ensure(manifest.get('schema') == 'im2p-execution-sqlite' and manifest.get('version') == 1 and
           manifest.get('status') == 'PASS' and manifest.get('scope') in ('SYNTHETIC', 'PRODUCER_DECLARED'),
           'unsupported SQLite execution input')
    scope = 'BOUND_DATASET' if manifest['scope'] == 'PRODUCER_DECLARED' else 'SYNTHETIC'
    validate_environment(scope, inputs.provider, inputs.scenario)
    graph, queues = _prepare(database)
    count = len(graph.identity)
    engine = ServiceExecutor(inputs.provider, inputs.scenario)
    events: list[tuple[Fraction, str, str, int]] = []
    rows: list[tuple[str, int, str]] = []

    def flush() -> None:
        database.executemany('INSERT INTO results VALUES(?,?,?)', rows)
        rows.clear()
    now = result_end = resource_end = Fraction(0)
    finished = 0
    while finished < count:
        while events and events[0][0] <= now:
            _, _, milestone, index = heapq.heappop(events)
            graph.release(index, SLOT[milestone], queues)
        # Group heads in (priority, identity) order: the first head that can start now is the minimum over every
        # startable head, and earliest() has no side effects, so the heads after it need no evaluation.
        future: list[Fraction] = []
        for queue in sorted(queue for queue in queues if queue):
            node = queue[0][3]
            earliest = engine.earliest(node, now)
            if earliest == now:
                break
            future.append(earliest)
        else:
            if events:
                future.append(events[0][0])
            ensure(bool(future), 'execution stalled: causal cycle or unresolved resource dependency')
            now = min(future)
            continue
        row = database.execute('SELECT body FROM ir.services WHERE identity=?', (node.identity,)).fetchone()
        services = Services({}, {}) if row is None else parse_services(_document(str(row[0])))
        ensure(set(services.cpu) | set(services.npu) == ({node.service} if node.service is not None else set()),
               'SQLite service identity mismatch')
        if stateful:
            flush()  # the parents' stored rows are read back by the stateful provider
        request_available = _request_available(database, node) if stateful else None
        completed = engine.execute(node, services, now, request_available)
        _, _, index, _ = heapq.heappop(queue)
        record = scheduled_record(completed)
        if observed is not None:
            actual = observed.execute('SELECT identity,body FROM results WHERE ordinal=?', (finished,)).fetchone()
            ensure(actual is not None and actual[0] == node.identity and _same_record(_document(str(actual[1])), record),
                   'SQLite schedule node/endpoint mismatch')
        rows.append((node.identity, finished, _COMPACT.encode(record)))
        if len(rows) == RESULT_BATCH:
            flush()
        if observer is not None:
            observer(finished, node, completed, record)
        for slot, milestone in enumerate(FIRE_ORDER):
            epoch = completed.milestone(milestone)
            if epoch <= now:
                graph.release(index, slot, queues)
            else:
                heapq.heappush(events, (epoch, node.identity, milestone.value, index))
        result_end = max(result_end, completed.result_ready_ns)
        resource_end = max(resource_end, completed.resource_ready_ns)
        finished += 1
        if finished % 10_000 == 0:
            ensure(len(events) <= 16384, 'scenario exceeds bounded concurrent event capacity')
    flush()
    complete_provider(inputs.provider, inputs.scenario)
    from sim.cycle.execution_cycle_provider import CycleServiceProvider
    if isinstance(inputs.provider, (DiagnosticStatefulProvider, StatefulSequenceProvider)):
        library_sha256 = inputs.provider.admission.source_identity.library_sha256
    else:
        library_sha256 = sha256(inputs.provider.library) if isinstance(inputs.provider, CycleServiceProvider) else None
    summary: Record = {'schema': 'im2p-execution-schedule-sqlite', 'version': 2 if stateful else 1,
            'scope': inputs.scenario.scope,
            'node_count': count, 'completion_ns': rational(result_end), 'resource_completion_ns': rational(resource_end),
            'service_validation_scope': validation_scope(inputs.provider),
            'service_binding': service_binding(inputs.provider, inputs.scenario),
            'cycle_library_sha256': library_sha256,
            'clock_selection_sha256': sha256(inputs.scenario.clock_artifact)
                                      if inputs.scenario.clock_artifact is not None else None,
            'paper_latency_ready': False,
            'time_unit': 'nanosecond-rational', 'queue_policy': 'READY_ORDER_THEN_ID',
            'clock_alignment': 'CEIL_TO_NPU_EDGE', 'worker_policy': 'EXPLICIT_GANG_VECTOR',
            'validated_service_reconstruction': inputs.scenario.scope == 'RECONSTRUCTED'}
    if stateful:
        ensure(source_binding == schedule_source_binding(Path(__file__)), 'SQLite schedule source binding changed during scheduling')
        summary.update(source_binding)
    return summary


def schedule_sqlite(source: Path, output: Path, inputs: SqliteScheduleInputs,
                    observer: Observer | None = None) -> Record:
    """Schedule the SQLite IR into a new SQLite schedule; `observer` sees each node as it is scheduled (one pass)."""
    ensure(source.resolve() != output.resolve() and not output.exists(), 'schedule output must be new and distinct')
    require_storage_budget(output, COPY_RESERVE_BYTES + (SQLITE_WORKING_SET_FACTOR + 1) * source.stat().st_size)
    with tempfile.TemporaryDirectory(prefix='schedule-sqlite-', dir=output.parent) as temporary:
        root = Path(temporary)
        snapshots = snapshot_inputs((source,), root)
        staged = root / 'result.sqlite'
        with closing(sqlite3.connect(staged, uri=True)) as database:
            database.execute('ATTACH DATABASE ? AS ir', (snapshots[0].snapshot.resolve().as_uri() + '?mode=ro',))
            summary = _run(database, inputs, observer=observer)
            summary['input_sqlite_sha256'] = sha256(snapshots[0].snapshot)
            database.execute('INSERT INTO metadata VALUES(?,?)', ('manifest', json.dumps(summary, sort_keys=True)))
            database.commit()
        verify_input_snapshots(snapshots, 'SQLite scheduling')
        if isinstance(inputs.provider, (DiagnosticStatefulProvider, StatefulSequenceProvider)):
            inputs.provider.verify_complete()
        if summary['version'] == 2:
            ensure(all(summary.get(key) == digest for key, digest in
                       schedule_source_binding(Path(__file__)).items()),
                   'SQLite schedule source binding changed before publication')
        os.link(staged, output)
    return summary


def verify_schedule_sqlite(source: Path, schedule_path: Path, inputs: SqliteScheduleInputs) -> str:
    source_digest, schedule_digest = sha256(source), sha256(schedule_path)
    temporary_root = Path(tempfile.gettempdir())
    require_storage_budget(temporary_root / 'state.sqlite',
                           COPY_RESERVE_BYTES + SQLITE_WORKING_SET_FACTOR * source.stat().st_size)
    with tempfile.TemporaryDirectory(prefix='verify-schedule-', dir=temporary_root) as temporary:
        staged = Path(temporary) / 'state.sqlite'
        with (closing(sqlite3.connect(staged)) as database,
              closing(sqlite3.connect(schedule_path.resolve(strict=True).as_uri() + '?mode=ro', uri=True)) as observed):
            database.execute('ATTACH DATABASE ? AS ir', (source.resolve(strict=True).as_uri() + '?mode=ro',))
            observed.execute('BEGIN')
            summary = _run(database, inputs, observed)
            summary['input_sqlite_sha256'] = source_digest
            row = observed.execute("SELECT body FROM metadata WHERE key='manifest'").fetchone()
            ensure(row is not None and _same_record(_document(str(row[0])), summary),
                   'SQLite schedule service/clock/source binding mismatch')
            count = observed.execute('SELECT COUNT(*) FROM results').fetchone()[0]
            ensure(count == summary['node_count'], 'SQLite schedule node coverage mismatch')
    ensure(sha256(source) == source_digest and sha256(schedule_path) == schedule_digest,
           'SQLite source or schedule changed during verification')
    return schedule_digest


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
