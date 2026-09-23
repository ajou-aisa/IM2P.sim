from __future__ import annotations

from fractions import Fraction
import unittest

from sim.cycle.execution_ir import Dependency, ExecutionError, ExecutionIR, Kind, Milestone, Node, NodeId, ResourceId, ServiceId
from sim.cycle.execution_services import CpuService, NpuService, NpuWork, PhaseTable, Services, WorkerService, cpu_service


def cpu(identity: str, duration: int, worker: int = 0) -> CpuService:
    return CpuService('FULL_CPU', 'THREAD_CPU_NS_GANG', (
        WorkerService(ResourceId('cpu:' + str(worker)), Fraction(duration), worker,
                      'thread_cpu_clock', 'nanosecond', duration),))


def node(identity: str, kind: Kind, dependencies: tuple[Dependency, ...] = ()) -> Node:
    return Node(NodeId(identity), kind, identity, 'prefill', 0, dependencies, ServiceId(identity),
                (ResourceId('npu:0' if kind == Kind.NPU else 'cpu:0'),))


class SchedulerTests(unittest.TestCase):
    def run_graph(self, nodes: tuple[Node, ...], services: Services):
        from sim.cycle.scheduler import ScheduleInputs, Scenario, schedule
        provider = PhaseTable(1, {(work.profile, work.request_sha256, 0): NpuService(5, 8, 'synthetic')
                                 for work in services.npu.values()})
        return schedule(ScheduleInputs(ExecutionIR(nodes, 'SYNTHETIC', 'test'), services, provider),
                        Scenario(1_000_000_000, 'SYNTHETIC'))

    def test_cpu_chain_when_dependent(self) -> None:
        services = Services({ServiceId('a'): cpu('a', 10), ServiceId('b'): cpu('b', 7)}, {})
        result = self.run_graph((node('a', Kind.CPU), node('b', Kind.CPU, (Dependency(NodeId('a')),))), services)
        self.assertEqual(result.by_id[NodeId('b')].result_ready_ns, 17)

    def test_independent_ready_cpu_when_future_cpu_waits_for_npu(self) -> None:
        services = Services({ServiceId('later'): cpu('later', 3), ServiceId('ready'): cpu('ready', 2)},
                            {ServiceId('npu'): NpuWork(ServiceId('npu'), 'work', 'a8w8-d16-hp1')})
        result = self.run_graph((node('npu', Kind.NPU), node('later', Kind.CPU, (Dependency(NodeId('npu')),)),
                                 node('ready', Kind.CPU)), services)
        self.assertEqual(result.by_id[NodeId('ready')].accepted_ns, 0)
        self.assertEqual(result.by_id[NodeId('later')].accepted_ns, 5)

    def test_resource_tail_when_result_already_available(self) -> None:
        services = Services({ServiceId('compose'): cpu('compose', 1)},
                            {ServiceId(name): NpuWork(ServiceId(name), name, 'a8w8-d16-hp1') for name in ('a', 'b')})
        result = self.run_graph((node('a', Kind.NPU), node('b', Kind.NPU),
                                 node('compose', Kind.CPU, (Dependency(NodeId('a')),))), services)
        self.assertEqual(result.by_id[NodeId('compose')].accepted_ns, 5)
        self.assertEqual(result.by_id[NodeId('b')].accepted_ns, 8)

    def test_worker_vector_when_two_workers_overlap(self) -> None:
        services = Services({ServiceId('parallel'): CpuService('FULL_CPU', 'THREAD_CPU_NS_GANG',
                            cpu('a', 100).workers + cpu('b', 120, 1).workers)}, {})
        parallel = Node(NodeId('parallel'), Kind.CPU, 'op', 'prefill', 0, (), ServiceId('parallel'),
                        (ResourceId('cpu:0'), ResourceId('cpu:1')))
        result = self.run_graph((parallel,), services)
        self.assertEqual(result.by_id[NodeId('parallel')].result_ready_ns, 120)
        self.assertEqual(len(result.by_id[NodeId('parallel')].worker_intervals), 2)

    def test_functional_exclusion_when_causality_required(self) -> None:
        services = Services({ServiceId('a'): cpu('a', 10), ServiceId('b'): cpu('b', 2)}, {})
        functional = Node(NodeId('functional'), Kind.FUNCTIONAL_EMULATION, 'op', 'prefill', 0,
                          (Dependency(NodeId('a')),))
        result = self.run_graph((node('a', Kind.CPU), functional,
                                node('b', Kind.CPU, (Dependency(NodeId('functional')),))), services)
        self.assertEqual(result.by_id[NodeId('b')].accepted_ns, 10)
        self.assertEqual(result.by_id[NodeId('functional')].result_ready_ns, 10)

    def test_real_reconstruction_when_proof_missing(self) -> None:
        from sim.cycle.scheduler import ScheduleInputs, Scenario, schedule
        services = Services({}, {ServiceId('a'): NpuWork(ServiceId('a'), 'a', 'a8w8-d16-hp1')})
        inputs = ScheduleInputs(ExecutionIR((node('a', Kind.NPU),), 'BOUND_DATASET', 'digest'), services,
                                PhaseTable(1, {('a8w8-d16-hp1', 'a', 0): NpuService(5, 8, 'synthetic')}))
        with self.assertRaisesRegex(ExecutionError, 'validated clock'):
            schedule(inputs, Scenario(1_000_000_000, 'RECONSTRUCTED'))

    def test_cpu_units_when_selected_metric_invalid(self) -> None:
        with self.assertRaisesRegex(ExecutionError, 'substitution forbidden'):
            cpu_service({'source': 'FULL_CPU', 'policy': 'THREAD_CPU_NS_GANG', 'workers': [
                {'thread_cpu_valid': False, 'thread_cpu_ns': None, 'host_elapsed_valid': True, 'host_elapsed_ns': 99}]})

    def test_phase_table_when_acceptance_phase_missing(self) -> None:
        table = PhaseTable(3, {('a8w8-d16-hp1', 'a', 1): NpuService(4, 7, 'synthetic')})
        with self.assertRaisesRegex(ExecutionError, 'acceptance-phase'):
            table.estimate(NpuWork(ServiceId('a'), 'a', 'a8w8-d16-hp1'), 2)

    def test_clock_alignment_when_cpu_ready_between_npu_edges(self) -> None:
        from sim.cycle.scheduler import ScheduleInputs, Scenario, schedule
        services = Services({ServiceId('cpu'): cpu('cpu', 1)},
                            {ServiceId('npu'): NpuWork(ServiceId('npu'), 'exact', 'a8w8-d16-hp1')})
        ir = ExecutionIR((node('cpu', Kind.CPU), node('npu', Kind.NPU, (Dependency(NodeId('cpu')),))), 'SYNTHETIC', 'x')
        provider = PhaseTable(3, {('a8w8-d16-hp1', 'exact', 1): NpuService(4, 6, 'phase-one')})
        result = schedule(ScheduleInputs(ir, services, provider), Scenario(300_000_000, 'SYNTHETIC'))
        self.assertEqual(result.by_id[NodeId('npu')].accepted_ns, Fraction(10, 3))
        self.assertEqual(result.by_id[NodeId('npu')].accepted_cycle, 1)
        self.assertEqual(result.by_id[NodeId('npu')].result_ready_ns, Fraction(50, 3))

    def test_resource_dependency_when_slot_tail_exceeds_result(self) -> None:
        services = Services({ServiceId('reuse'): cpu('reuse', 1)},
                            {ServiceId('npu'): NpuWork(ServiceId('npu'), 'exact', 'a8w8-d16-hp1')})
        nodes = (node('npu', Kind.NPU), node('reuse', Kind.CPU, (Dependency(NodeId('npu'), Milestone.RESOURCE_READY),)))
        result = self.run_graph(nodes, services)
        self.assertEqual(result.by_id[NodeId('reuse')].accepted_ns, 8)

    def test_application_chain_when_token_ready_precedes_next_decode(self) -> None:
        services = Services({ServiceId(name): cpu(name, duration) for name, duration in [('prefill', 5), ('sample', 2), ('decode', 3)]}, {})
        services.cpu[ServiceId('sample')] = CpuService('APPLICATION_CPU', 'THREAD_CPU_NS_GANG', cpu('sample', 2).workers)
        token = Node(NodeId('token-ready'), Kind.BARRIER, 'application', 'prefill', 0, (Dependency(NodeId('sample')),))
        nodes = (node('prefill', Kind.CPU), node('sample', Kind.APPLICATION_CPU, (Dependency(NodeId('prefill')),)),
                 token, node('decode', Kind.CPU, (Dependency(NodeId('token-ready')),)))
        result = self.run_graph(nodes, services)
        self.assertEqual(result.by_id[NodeId('decode')].accepted_ns, 7)

    def test_profile_when_same_shape_has_other_hardware(self) -> None:
        table = PhaseTable(1, {('a8w8-d16-hp1', 'same-shape', 0): NpuService(4, 7, 'synthetic')})
        with self.assertRaisesRegex(ExecutionError, 'service boundary'):
            table.estimate(NpuWork(ServiceId('a'), 'same-shape', 'a4w4-d64-hp1'), 0)

    def test_application_when_full_cpu_sampling_supplied(self) -> None:
        services = Services({ServiceId('sample'): cpu('sample', 2)}, {})
        with self.assertRaisesRegex(ExecutionError, 'PoTal application source'):
            self.run_graph((node('sample', Kind.APPLICATION_CPU),), services)

    def test_valid_zero_worker_when_cpu_service_is_empty(self) -> None:
        result = self.run_graph((node('zero', Kind.CPU),), Services({ServiceId('zero'): cpu('zero', 0)}, {}))
        self.assertEqual(result.by_id[NodeId('zero')].result_ready_ns, 0)


if __name__ == '__main__':
    unittest.main()
