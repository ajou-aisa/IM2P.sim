from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from sim.cycle import cli
from sim.cycle.execution_cycle_provider import CycleServiceProvider, ReferenceMemoryScenario
from sim.cycle.execution_ir import ExecutionError, ExecutionIR, Kind, ServiceId, ir_record
from sim.cycle.execution_services import NpuWork, Services, services_record
from sim.cycle.npu_trace_schema import Record, integer, object_value
from sim.tests.cycle.test_execution_scheduler import node
from sim.tests.cycle.test_npu_trace import write_trace
from sim.tests.cycle.test_npu_trace_runs import production_run_records


class ServiceApiTests(unittest.TestCase):
    def test_additive_service_entry_when_requested(self) -> None:
        self.assertTrue(callable(getattr(cli, 'estimate_service', None)), 'additive service binding is missing')

    @unittest.skipUnless('IM2P_CYCLE_LIBRARY' in os.environ, 'current service library required')
    def test_old_logical_endpoints_when_service_extended(self) -> None:
        library = Path(os.environ['IM2P_CYCLE_LIBRARY'])
        document: Record = {'profile': 'a8w8-d32-hp1', 'request': {'m': 2, 'n': 3, 'k': 64, 'tile_i': 1,
                    'tile_j': 1, 'tile_k': 2, 'accepted_cycle': 589, 'submission': 'regression-tiles', 'record_events': 0}}
        original = cli.estimate(library, document)
        self.assertEqual(original['result']['total_cycles'], 503)
        extended = cli.estimate_service(library, document)
        result, service = object_value(extended['result']), object_value(extended['service'])
        self.assertEqual(result['done_cycle'], original['result']['done_cycle'])
        self.assertEqual(result['total_cycles'], original['result']['total_cycles'])
        self.assertGreater(integer(service, 'resource_ready_cycle'), integer(service, 'result_ready_cycle'))

    @unittest.skipUnless('IM2P_CYCLE_LIBRARY' in os.environ, 'current service library required')
    def test_real_service_api_when_explicit_run_trace_supplied(self) -> None:
        library = Path(os.environ['IM2P_CYCLE_LIBRARY'])
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / 'trace.jsonl'
            write_trace(trace, production_run_records())
            provider = CycleServiceProvider(library, trace, ReferenceMemoryScenario({'read_ready_period': 3}, 0, 0))
            bound = provider.requests[ServiceId('npu:0')]
            service = provider.estimate(bound.work, 1)
            self.assertGreater(service.resource_ready_cycles, service.result_ready_cycles)
            self.assertEqual(provider.invocations, 1)
            self.assertEqual(provider.validation_scope, 'DIAGNOSTIC_SERVICE_API')
            with self.assertRaisesRegex(ExecutionError, 'duplicate scheduled'):
                provider.estimate(bound.work, provider.previous_resource_cycle)

    @unittest.skipUnless('IM2P_CYCLE_LIBRARY' in os.environ, 'current service library required')
    def test_exact_request_when_digest_changed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / 'trace.jsonl'
            write_trace(trace, production_run_records())
            provider = CycleServiceProvider(Path(os.environ['IM2P_CYCLE_LIBRARY']), trace,
                                            ReferenceMemoryScenario({}, 0, 0))
            with self.assertRaisesRegex(ExecutionError, 'exact request binding'):
                provider.estimate(NpuWork(ServiceId('npu:0'), 'changed', 'a8w8-d16-hp1'), 1)

    @unittest.skipUnless('IM2P_CYCLE_LIBRARY' in os.environ, 'current service library required')
    def test_cli_when_real_model_used_only_offline(self) -> None:
        library = Path(os.environ['IM2P_CYCLE_LIBRARY'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_trace(root / 'trace.jsonl', production_run_records())
            provider = CycleServiceProvider(library, root / 'trace.jsonl', ReferenceMemoryScenario({}, 0, 0))
            ir = ExecutionIR((node('npu:0', Kind.NPU),), 'SYNTHETIC', 'fixture')
            bundle = {'schema': 'im2p-execution-bundle', 'version': 1, 'ir': ir_record(ir),
                      'services': services_record(Services({}, {key: bound.work for key, bound in provider.requests.items()}))}
            (root / 'bundle.json').write_text(json.dumps(bundle))
            (root / 'timing.json').write_text('{}')
            result = subprocess.run([sys.executable, '-m', 'sim.cycle.execution_cli', 'schedule',
                '--bundle', str(root / 'bundle.json'), '--cycle-library', str(library),
                '--npu-trace', str(root / 'trace.jsonl'), '--timing', str(root / 'timing.json'),
                '--initial-scratchpad-half', '0', '--initial-accumulator-half', '0',
                '--frequency-hz', '1000000000', '--synthetic', '--output', str(root / 'schedule.json')],
                capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads((root / 'schedule.json').read_text())
            self.assertEqual(output['service_validation_scope'], 'DIAGNOSTIC_SERVICE_API')
            self.assertFalse(output['paper_latency_ready'])


if __name__ == '__main__':
    unittest.main()
