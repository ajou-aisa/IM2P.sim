from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from sim.cycle.npu_trace import ReplayArtifacts, ReplayOutputs
from sim.cycle.npu_trace_schema import Record, object_value
from sim.cycle.reconstruct import reconstruct
from sim.cycle.reconstruct_graph import array, json_records
from sim.tests.cycle.test_reconstruct import write_rows
from sim.tests.cycle.test_reconstruct_join import fixture, refresh_proofs


def named_metrics(record: Record, *, cycles: int, host_start: int, host_end: int,
                  thread_id: int, interval_class: str) -> Record:
    return dict(record, cpu_work_cycles=cycles, cpu_work_cycles_source='fixture_cpu_cycles',
                cpu_work_cycles_unit='cycle', cpu_work_cycles_valid=True,
                cpu_work_cycles_reason=None, thread_cpu_ns=cycles * 2,
                thread_cpu_valid=True, thread_cpu_reason=None,
                host_elapsed_ns=host_end - host_start, host_elapsed_valid=True,
                host_elapsed_reason=None, host_start_ns=host_start, host_end_ns=host_end,
                host_execution_id='fixture-execution', thread_id=thread_id,
                interval_class=interval_class)


@unittest.skipUnless(os.getenv('IM2P_CYCLE_LIBRARY') and os.getenv('IM2P_CYCLE_CERTIFICATE'),
                     'actual certified C model required')
class TimingContractTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        artifacts = ReplayArtifacts(Path(os.environ['IM2P_CYCLE_LIBRARY']),
                                    Path(os.environ['IM2P_CYCLE_CERTIFICATE']))
        self.inputs = fixture(self.root, artifacts)
        self.outputs = ReplayOutputs(self.root/'dataset.jsonl', self.root/'summary.json')

    def add_host_stage(self, host_stage_id: int, *, start: int, end: int,
                       thread_id: int, stage_name: str) -> None:
        trace = list(json_records(self.inputs.npu.trace))
        original = next(row for row in trace if row['kind'] == 'HOST_STAGE' and row['event'] == 'BEGIN')
        begin = dict(original, host_stage_id=host_stage_id, stage_name=stage_name,
                     required_host_stage_ids=[])
        target = next(index for index, row in enumerate(trace) if row['kind'] == 'TARGET_OPERATION')
        trace[target:target] = [begin, dict(begin, event='END', status='success')]
        trace[-1].update(host_stage_count=host_stage_id + 1,
                         completed_host_stage_count=host_stage_id + 1,
                         potal_host_count=host_stage_id + 1)
        for sequence, row in enumerate(trace):
            row['sequence'] = sequence
        write_rows(self.inputs.npu.trace, trace)
        rows = list(json_records(self.inputs.potal.log))
        original_measurement = next(row for row in rows if row.get('host_stage_id') == 0)
        rows.append(named_metrics(dict(original_measurement, host_stage_id=host_stage_id,
                                       op=stage_name), cycles=end-start,
                                  host_start=start, host_end=end, thread_id=thread_id,
                                  interval_class='CANONICAL_ADDITIVE'))
        rows[1] = named_metrics(rows[1], cycles=200, host_start=100, host_end=300,
                                thread_id=1, interval_class='CANONICAL_ADDITIVE')
        write_rows(self.inputs.potal.log, rows)
        refresh_proofs(self.inputs)

    def test_worker_cpu_work_and_host_elapsed_remain_distinct(self) -> None:
        for files in (self.inputs.full_cpu, self.inputs.potal):
            graph = list(json_records(files.graph))
            workload = object_value(graph[0]['workload'])
            object_value(workload['cpu'])['threads'] = 2
            object_value(workload['cpu_batch'])['threads'] = 2
            write_rows(files.graph, graph)
        rows = list(json_records(self.inputs.full_cpu.log))
        first = named_metrics(dict(rows[0], worker_count=2), cycles=100,
                              host_start=0, host_end=100, thread_id=11,
                              interval_class='PER_WORKER_CPU_WORK')
        second = named_metrics(dict(first, worker_id=1), cycles=120,
                               host_start=20, host_end=140, thread_id=12,
                               interval_class='PER_WORKER_CPU_WORK')
        rows = [first, second, named_metrics(rows[1], cycles=90000,
                host_start=200, host_end=300, thread_id=11,
                interval_class='PER_WORKER_CPU_WORK')]
        write_rows(self.inputs.full_cpu.log, rows)
        potal = list(json_records(self.inputs.potal.log))
        potal[1] = named_metrics(potal[1], cycles=20, host_start=400, host_end=420,
                                 thread_id=11, interval_class='CANONICAL_ADDITIVE')
        write_rows(self.inputs.potal.log, potal)
        refresh_proofs(self.inputs)
        reconstruct(self.inputs, self.outputs)
        ordinary = next(row for row in json_records(self.outputs.results)
                        if row['kind'] == 'SERVICE' and row['node_class'] == 'ORDINARY_CPU')
        duration = object_value(ordinary['duration'])
        self.assertEqual(duration['aggregation'], 'PER_WORKER_NOT_NODE_LATENCY')
        vector = duration['worker_intervals']
        self.assertIsInstance(vector, list)
        if not isinstance(vector, list):
            raise AssertionError('worker vector missing')
        self.assertEqual([object_value(row)['cpu_work_cycles'] for row in vector], [100, 120])
        self.assertEqual([object_value(row)['host_elapsed_ns'] for row in vector], [100, 120])
        self.assertNotIn('node_latency_cycles', duration)

    def test_same_resource_nested_host_interval_is_rejected(self) -> None:
        self.add_host_stage(1, start=120, end=180, thread_id=1, stage_name='nested')
        with self.assertRaisesRegex(ValueError, 'overlap'):
            reconstruct(self.inputs, self.outputs)

    def test_same_resource_partial_host_overlap_is_rejected(self) -> None:
        self.add_host_stage(1, start=250, end=350, thread_id=1, stage_name='partial')
        with self.assertRaisesRegex(ValueError, 'overlap'):
            reconstruct(self.inputs, self.outputs)

    def test_different_worker_overlap_is_accepted(self) -> None:
        self.add_host_stage(1, start=120, end=180, thread_id=2, stage_name='other-worker')
        self.assertEqual(reconstruct(self.inputs, self.outputs)['host_stage_measurement_coverage'], 'PASS')

    def test_valid_zero_host_interval_is_accepted(self) -> None:
        self.add_host_stage(1, start=300, end=300, thread_id=1, stage_name='zero')
        self.assertEqual(reconstruct(self.inputs, self.outputs)['host_stage_measurement_coverage'], 'PASS')

    def test_incomplete_named_host_interval_is_rejected(self) -> None:
        rows = list(json_records(self.inputs.potal.log))
        del rows[1]['host_execution_id']
        write_rows(self.inputs.potal.log, rows)
        refresh_proofs(self.inputs)
        with self.assertRaisesRegex(ValueError, 'incomplete named timing'):
            reconstruct(self.inputs, self.outputs)

    def test_unavailable_native_cycles_preserve_valid_thread_cpu_metric(self) -> None:
        rows = list(json_records(self.inputs.full_cpu.log))
        rows[0].update(cpu_work_cycles=None, cpu_work_cycles_source=None,
                       cpu_work_cycles_valid=False, cpu_work_cycles_reason='not_thread_cpu_counter')
        write_rows(self.inputs.full_cpu.log, rows)
        refresh_proofs(self.inputs)
        reconstruct(self.inputs, self.outputs)
        ordinary = next(row for row in json_records(self.outputs.results)
                        if row['kind'] == 'SERVICE' and row['node_class'] == 'ORDINARY_CPU')
        sample = object_value(array(object_value(ordinary['duration'])['worker_intervals'])[0])
        self.assertIs(sample['cpu_work_cycles_valid'], False)
        self.assertEqual(sample['thread_cpu_ns'], 20)

    def test_online_npu_diagnostic_cannot_override_offline_result(self) -> None:
        rows = list(json_records(self.inputs.potal.log))
        diagnostic = dict(rows[0], record_type='NPU_OPERATOR_SEGMENT',
                          duration_role='OBSERVATION_ONLY', cpu_service=False,
                          interval_class='DIAGNOSTIC', online_npu_cycles=999_999_999)
        rows.append(diagnostic)
        rows[1] = named_metrics(rows[1], cycles=20, host_start=400, host_end=420,
                                thread_id=1, interval_class='CANONICAL_ADDITIVE')
        write_rows(self.inputs.potal.log, rows)
        refresh_proofs(self.inputs)
        summary = reconstruct(self.inputs, self.outputs)
        self.assertEqual(summary['npu_work_count'], 1)
        npu = next(row for row in json_records(self.outputs.results)
                   if row['kind'] == 'SERVICE' and row['node_class'] == 'TARGET_NPU')
        self.assertNotEqual(object_value(npu['duration'])['cycles'], 999_999_999)

    def test_online_npu_duration_authority_claim_is_rejected(self) -> None:
        rows = list(json_records(self.inputs.potal.log))
        rows.append(dict(rows[0], record_type='NPU_OPERATOR_SEGMENT',
                         duration_role='POTAL_HOST', host_stage_id=0,
                         interval_class='CANONICAL_ADDITIVE', online_npu_cycles=1))
        write_rows(self.inputs.potal.log, rows)
        refresh_proofs(self.inputs)
        with self.assertRaisesRegex(ValueError, 'non-interval telemetry'):
            reconstruct(self.inputs, self.outputs)

    def test_global_diagnostic_telemetry_needs_no_semantic_join_identity(self) -> None:
        rows = list(json_records(self.inputs.potal.log))
        rows.insert(0, {'schema': 'gemmini.cycle', 'version': 2,
                        'record_type': 'INFERENCE_CONFIGURATION', 'execution_id': 'fixture'})
        write_rows(self.inputs.potal.log, rows)
        refresh_proofs(self.inputs)
        self.assertEqual(reconstruct(self.inputs, self.outputs)['ordinary_cpu_bijection'], 'PASS')

    def test_compact_structural_segment_is_non_authoritative(self) -> None:
        rows = list(json_records(self.inputs.full_cpu.log))
        rows.insert(0, {'op': 'operator.host_dispatch', 'kind': 'segment',
                        'interval_class': 'STRUCTURAL', 'duration_role': 'OBSERVATION_ONLY',
                        'exclusion_reason': 'outside_collection'})
        write_rows(self.inputs.full_cpu.log, rows)
        refresh_proofs(self.inputs)
        self.assertEqual(reconstruct(self.inputs, self.outputs)['ordinary_cpu_bijection'], 'PASS')

    def test_host_correlated_diagnostic_does_not_replace_canonical_cost(self) -> None:
        rows = list(json_records(self.inputs.potal.log))
        diagnostic = dict(rows[1], op='im2p.output_reconstruction',
                          duration_role='OBSERVATION_ONLY', interval_class='DIAGNOSTIC')
        rows.insert(1, diagnostic)
        write_rows(self.inputs.potal.log, rows)
        refresh_proofs(self.inputs)
        self.assertEqual(reconstruct(self.inputs, self.outputs)['host_stage_measurement_coverage'], 'PASS')


if __name__ == '__main__':
    unittest.main()
