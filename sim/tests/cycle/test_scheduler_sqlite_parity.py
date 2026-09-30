"""SQLite scheduler equals the in-memory JSON scheduler on random causal/resource graphs and parses each node once."""
from __future__ import annotations

import json
import random
import sqlite3
import tempfile
import unittest
from contextlib import closing
from fractions import Fraction
from pathlib import Path
from unittest import mock

from sim.cycle import scheduler_sqlite
from sim.cycle.execution_ir import (
    Dependency,
    ExecutionIR,
    Kind,
    Milestone,
    Node,
    NodeId,
    ResourceId,
    ServiceId,
)
from sim.cycle.execution_services import (
    CpuService,
    NpuService,
    NpuWork,
    PhaseTable,
    Services,
    WorkerService,
    parse_services,
    services_record,
)
from sim.cycle.scheduler import Scenario, ScheduleInputs, schedule
from sim.cycle.scheduler_sqlite import (
    SqliteScheduleInputs,
    schedule_sqlite,
    verify_schedule_sqlite,
)
from sim.tests.cycle.test_scheduler_sqlite import write_store

STRUCTURAL = (Kind.OP_ENTER, Kind.OP_EXIT, Kind.BARRIER, Kind.PUBLISH, Kind.FUNCTIONAL_EMULATION, Kind.WAIT, Kind.EXCLUDED)


def random_inputs(rng: random.Random, size: int) -> ScheduleInputs:
    """CPU gangs over shared workers, NPU work in several resource groups, structural nodes, order ties."""
    workers = [f'cpu:{index}' for index in range(rng.randint(1, 4))]
    npu_groups = [('npu:0',), ('npu:0', 'dma:0'), ('npu:0', 'dma:1')][:rng.randint(1, 3)]
    period = rng.randint(1, 3)
    requests = [f'request-{index}' for index in range(rng.randint(1, 4))]
    samples = {}
    for request in requests:
        for phase in range(period):
            result = rng.randint(1, 30)
            samples[('a8w8-d16-hp1', request, phase)] = NpuService(result, result + rng.randint(0, 20), 'synthetic')
    nodes: list[Node] = []
    cpu: dict[ServiceId, CpuService] = {}
    npu: dict[ServiceId, NpuWork] = {}
    for index in range(size):
        identity = NodeId(f'n{rng.randint(0, 999):03d}-{index:04d}')
        parents = rng.sample(nodes, k=min(len(nodes), rng.choice((0, 1, 1, 2, 3))))
        dependencies = tuple(Dependency(parent.identity, rng.choice(tuple(Milestone))) for parent in parents)
        order = rng.randint(0, max(1, size // 4))
        roll = rng.random()
        if roll < 0.45:
            gang = rng.sample(workers, k=rng.randint(1, min(2, len(workers))))
            cpu[ServiceId(identity)] = CpuService('FULL_CPU', 'THREAD_CPU_NS_GANG', tuple(
                WorkerService(ResourceId(resource), Fraction(duration), slot, 'thread_cpu_clock', 'nanosecond', duration)
                for slot, (resource, duration) in enumerate((resource, rng.choice((0, 1, 3, 8))) for resource in gang)))
            nodes.append(Node(identity, Kind.CPU, 'op', 'prefill', order, dependencies, ServiceId(identity),
                              tuple(ResourceId(resource) for resource in gang)))
        elif roll < 0.75:
            npu[ServiceId(identity)] = NpuWork(ServiceId(identity), rng.choice(requests), 'a8w8-d16-hp1')
            nodes.append(Node(identity, Kind.NPU, 'op', 'decode', order, dependencies, ServiceId(identity),
                              tuple(ResourceId(resource) for resource in rng.choice(npu_groups))))
        else:
            nodes.append(Node(identity, rng.choice(STRUCTURAL), 'op', 'prefill', order, dependencies))
    return ScheduleInputs(ExecutionIR(tuple(nodes), 'SYNTHETIC', 'test'), parse_services(services_record(Services(cpu, npu))),
                          PhaseTable(period, samples))


class SqliteSchedulerParityTests(unittest.TestCase):
    def test_matches_json_scheduler_on_random_resource_groups_and_milestones(self) -> None:
        # Given: seeded graphs where resource groups, order ties, clock edges and every milestone interact.
        rng = random.Random(20260930)
        for index in range(60):
            inputs = random_inputs(rng, rng.choice((1, 2, 5, 12, 40, 90)))
            scenario = Scenario(rng.choice((1_000_000_000, 300_000_000)), 'SYNTHETIC')
            with self.subTest(graph=index), tempfile.TemporaryDirectory() as directory:
                source, output = Path(directory) / 'input.sqlite', Path(directory) / 'schedule.sqlite'
                write_store(source, inputs)
                # When: the disk-backed scheduler runs the same graph.
                schedule_sqlite(source, output, SqliteScheduleInputs(inputs.npu_provider, scenario))
                # Then: every node, order and endpoint equals the JSON scheduler; only results and manifest are stored.
                with closing(sqlite3.connect(output)) as db:
                    actual = [json.loads(row[0]) for row in db.execute('SELECT body FROM results ORDER BY ordinal')]
                    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertEqual(actual, schedule(inputs, scenario).record()['nodes'])
                self.assertEqual(tables, {'results', 'metadata'})
                verify_schedule_sqlite(source, output, SqliteScheduleInputs(inputs.npu_provider, scenario))

    def test_observer_sees_every_stored_row_once_in_schedule_order(self) -> None:
        # Given: a graph with several resource groups and future milestones.
        inputs = random_inputs(random.Random(11), 60)
        seen: list[tuple[int, str, str]] = []
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'input.sqlite', Path(directory) / 'schedule.sqlite'
            write_store(source, inputs)
            # When: the schedule is computed with an observer.
            schedule_sqlite(source, output, SqliteScheduleInputs(inputs.npu_provider, Scenario(1_000_000_000, 'SYNTHETIC')),
                            lambda ordinal, node, completed, record: seen.append(
                                (ordinal, node.identity, json.dumps(record, separators=(',', ':')))))
            with closing(sqlite3.connect(output)) as db:
                stored = [(ordinal, identity, body) for identity, ordinal, body in
                          db.execute('SELECT identity,ordinal,body FROM results ORDER BY ordinal')]
        # Then: the observer saw exactly the stored rows, once each, in schedule order.
        self.assertEqual(seen, stored)

    def test_parses_each_node_body_once(self) -> None:
        # Given: a graph whose group heads stay blocked across many scheduling iterations.
        inputs = random_inputs(random.Random(7), 120)
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'input.sqlite', Path(directory) / 'schedule.sqlite'
            write_store(source, inputs)
            # When: the graph is scheduled.
            with mock.patch.object(scheduler_sqlite, '_node', wraps=scheduler_sqlite._node) as parse:
                schedule_sqlite(source, output, SqliteScheduleInputs(inputs.npu_provider, Scenario(1_000_000_000, 'SYNTHETIC')))
            # Then: each IR node body was decoded exactly once.
            self.assertEqual(parse.call_count, len(inputs.ir.nodes))


if __name__ == '__main__':
    unittest.main()
