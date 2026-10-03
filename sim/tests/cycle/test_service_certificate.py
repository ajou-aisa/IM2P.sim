from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sim.cycle.execution_cycle_provider import CycleServiceProvider, ReferenceMemoryScenario
from sim.cycle.execution_ir import ExecutionError, ServiceId
from sim.cycle.npu_trace_schema import Record
from sim.cycle.production_sequence_certificate import CertifiedCase, CertifiedWork
from sim.cycle.service_certificate import ServiceAdmission
from sim.tests.cycle.test_npu_trace_runs import production_run_records
from sim.tests.cycle.test_npu_trace import records, write_trace

CERT_ENV = ('IM2P_CYCLE_LIBRARY', 'IM2P_CYCLE_CERTIFICATE',
            'IM2P_RUN_CERTIFICATE', 'IM2P_SERVICE_CERTIFICATE')
TIMING: Record = {'backing_read_delay': 3, 'even_read_id_delay': 13,
                  'scale_read_extra_delay': 17, 'backing_write_delay': 11,
                  'backing_cycle_offset': 5, 'read_ready_period': 5}


class ServiceCertificateAdmissionTests(unittest.TestCase):
    def test_validated_fixture_result_stays_diagnostic(self) -> None:
        # Given: the existing validator returns only the fixture admission.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library, trace = root / 'library', root / 'trace.jsonl'
            library.write_bytes(b'diagnostic fixture')
            write_trace(trace, production_run_records())
            fixture = ServiceAdmission('fixture', 'base', 'run', 'trace',
                                       'drained-fixture', 'a8w8-d16-hp1', 1)
            with patch('sim.cycle.execution_cycle_provider.validate_service_certificate',
                       return_value=fixture):
                # When: the provider receives a complete v1 proof.
                provider = CycleServiceProvider(library, trace, ReferenceMemoryScenario(TIMING, 0, 0),
                                                service_certificate=root / 'fixture.json',
                                                base_certificate=root / 'base.json',
                                                run_certificate=root / 'run.json')

            # Then: it remains outside reconstructed scheduling.
            self.assertEqual(provider.fixture_admission, fixture)
            self.assertIsNone(provider.admission)
            self.assertEqual(provider.validation_scope, 'DRAINED_FIXTURE_PARITY')

    def test_validated_production_sequence_enables_admission(self) -> None:
        # Given: the certificate validator has admitted a production sequence.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library, trace = root / 'library', root / 'trace.jsonl'
            library.write_bytes(b'diagnostic fixture')
            write_trace(trace, production_run_records())
            admitted = ServiceAdmission('service', 'base', 'run', 'trace',
                                        'production-sequence', 'a8w8-d16-hp1', 1)
            scenario = ReferenceMemoryScenario(TIMING, 0, 0)
            diagnostic = CycleServiceProvider(library, trace, scenario)
            work = diagnostic.requests[ServiceId('npu:0')].work
            case = CertifiedCase('phase-one', 'a8w8-d16-hp1', 5,
                                 (CertifiedWork(0, work.request_sha256, 1, 0, 0,
                                                10, 12, 14, 1, 0),))
            with patch('sim.cycle.execution_cycle_provider.validate_service_certificate',
                       return_value=(admitted, (case,))):
                # When: the provider receives the validated production result.
                provider = CycleServiceProvider(library, trace, scenario,
                                                service_certificate=root / 'service.json',
                                                base_certificate=root / 'base.json',
                                                run_certificate=root / 'run.json')

            # Then: the scheduler-visible admission is established by validator output.
            self.assertEqual(provider.admission, admitted)
            self.assertEqual(provider.validation_scope, 'CURRENT_CERTIFIED_SEQUENCE')

    def test_certified_sequence_rejects_other_acceptance_phase(self) -> None:
        # Given: one validated production case covers phase one only.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library, trace = root / 'library', root / 'trace.jsonl'
            library.write_bytes(b'diagnostic fixture')
            write_trace(trace, production_run_records())
            scenario = ReferenceMemoryScenario(TIMING, 0, 0)
            diagnostic = CycleServiceProvider(library, trace, scenario)
            work = diagnostic.requests[ServiceId('npu:0')].work
            admitted = ServiceAdmission('service', 'base', 'run', 'trace',
                                        'production-sequence', 'a8w8-d16-hp1', 1)
            case = CertifiedCase('phase-one', 'a8w8-d16-hp1', 5,
                                 (CertifiedWork(0, work.request_sha256, 1, 0, 0,
                                                10, 12, 14, 1, 0),))
            with patch('sim.cycle.execution_cycle_provider.validate_service_certificate',
                       return_value=(admitted, (case,))):
                provider = CycleServiceProvider(library, trace, scenario,
                                                service_certificate=root / 'service.json',
                                                base_certificate=root / 'base.json',
                                                run_certificate=root / 'run.json')

            # When: the scheduler chooses phase two for the exact work.
            # Then: admission rejects it before calling the cycle model.
            with patch('sim.cycle.execution_cycle_provider.estimate_service',
                       side_effect=AssertionError('cycle model called before phase admission')):
                with self.assertRaisesRegex(ExecutionError, 'certified.*phase'):
                    provider.estimate(work, 2)

    def test_diagnostic_provider_has_no_certificate_admission(self) -> None:
        # Given: a structurally valid trace and a library path without a service certificate.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library, trace = root / 'library', root / 'trace.jsonl'
            library.write_bytes(b'diagnostic fixture')
            write_trace(trace, records())

            # When: the existing diagnostic provider is constructed.
            provider = CycleServiceProvider(library, trace, ReferenceMemoryScenario({}, 0, 0))

            # Then: production admission is absent regardless of its diagnostic label.
            self.assertIsNone(provider.admission)
            self.assertEqual(provider.validation_scope, 'DIAGNOSTIC_SERVICE_API')

    def test_partial_certificate_bundle_is_rejected(self) -> None:
        # Given: a valid trace and a service path without the required base/run proof.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library, trace = root / 'library', root / 'trace.jsonl'
            library.write_bytes(b'diagnostic fixture')
            write_trace(trace, records())

            # When: production admission is requested with incomplete evidence.
            with self.assertRaisesRegex(ExecutionError, 'supplied together'):
                CycleServiceProvider(library, trace, ReferenceMemoryScenario(TIMING, 0, 0),
                                     service_certificate=root / 'missing.json')

    @unittest.skipUnless(all(os.environ.get(name) for name in CERT_ENV), 'current RTL service evidence required')
    def test_current_fixture_certificate_estimates_without_production_admission(self) -> None:
        # Given: current source-bound two-work fixture, base and run certificates.
        library, base, run, service = (Path(os.environ[name]) for name in CERT_ENV)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace = root / 'trace.jsonl'
            write_trace(trace, production_run_records())
            scenario = ReferenceMemoryScenario(TIMING, 0, 0)

            # When: the callable validates the fixture bundle and estimates one work.
            provider = CycleServiceProvider(library, trace, scenario, service_certificate=service,
                                            base_certificate=base, run_certificate=run)
            boundary = provider.estimate(provider.requests[ServiceId('npu:0')].work, 1)

            # Then: fixture evidence binds the work, but cannot publish production latency.
            self.assertEqual(provider.validation_scope, 'DRAINED_FIXTURE_PARITY')
            self.assertIsNone(provider.admission)
            fixture = provider.fixture_admission
            self.assertIsNotNone(fixture)
            assert fixture is not None
            self.assertEqual(fixture.profile, 'a8w8-d16-hp1')
            self.assertIn(fixture.certificate_sha256, boundary.evidence_id)
            self.assertGreater(boundary.resource_ready_cycles, boundary.result_ready_cycles)

    @unittest.skipUnless(all(os.environ.get(name) for name in CERT_ENV), 'current RTL service evidence required')
    def test_fixture_parity_cannot_enter_reconstructed_scheduler(self) -> None:
        from sim.cycle.scheduler import Scenario, validate_environment

        library, base, run, service = (Path(os.environ[name]) for name in CERT_ENV)
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / 'trace.jsonl'
            write_trace(trace, production_run_records())
            provider = CycleServiceProvider(library, trace, ReferenceMemoryScenario(TIMING, 0, 0),
                                            service_certificate=service, base_certificate=base,
                                            run_certificate=run)
            with self.assertRaisesRegex(ExecutionError, 'drained-sequence service certificate'):
                validate_environment('BOUND_DATASET', provider,
                                     Scenario(1_000_000_000, 'RECONSTRUCTED', Path(__file__), 'a8w8-d16-hp1'))

    @unittest.skipUnless(all(os.environ.get(name) for name in CERT_ENV), 'current RTL service evidence required')
    def test_forged_certificate_status_is_rejected(self) -> None:
        library, base, run, service = (Path(os.environ[name]) for name in CERT_ENV)
        # Given: a source-bound certificate with its status changed to an unsupported label.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace = root / 'trace.jsonl'
            write_trace(trace, production_run_records())
            altered = json.loads(service.read_text())
            altered['status'] = 'PASS_BY_LABEL'
            forged = root / 'forged.json'
            forged.write_text(json.dumps(altered))

            # When: the forged certificate is offered.
            with self.assertRaisesRegex(ValueError, 'schema, corpus or library'):
                CycleServiceProvider(library, trace, ReferenceMemoryScenario(TIMING, 0, 0),
                                     service_certificate=forged,
                                     base_certificate=base, run_certificate=run)

    @unittest.skipUnless(all(os.environ.get(name) for name in CERT_ENV), 'current RTL service evidence required')
    def test_unsupported_reference_timing_is_rejected(self) -> None:
        library, base, run, service = (Path(os.environ[name]) for name in CERT_ENV)
        # Given: an intact certificate and a timing period outside its RTL phase sweep.
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / 'trace.jsonl'
            write_trace(trace, production_run_records())

            # When: the provider is constructed for the unsupported period.
            with self.assertRaisesRegex(ValueError, 'unsupported reference-memory timing'):
                CycleServiceProvider(library, trace,
                                     ReferenceMemoryScenario({**TIMING, 'read_ready_period': 7}, 0, 0),
                                     service_certificate=service, base_certificate=base, run_certificate=run)

    @unittest.skipUnless(all(os.environ.get(name) for name in CERT_ENV), 'current RTL service evidence required')
    def test_guard_log_digest_mutation_is_rejected(self) -> None:
        library, base, run, service = (Path(os.environ[name]) for name in CERT_ENV)
        # Given: the current certificate with one guarded RTL log digest altered.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace = root / 'trace.jsonl'
            write_trace(trace, production_run_records())
            altered = json.loads(service.read_text())
            altered['profiles']['a4w4-d16-hp1']['guarded_log']['sha256'] = '0' * 64
            forged = root / 'forged.json'
            forged.write_text(json.dumps(altered))

            # When: admission validates the referenced raw log.
            with self.assertRaisesRegex(ValueError, 'guarded_log artifact changed'):
                CycleServiceProvider(library, trace, ReferenceMemoryScenario(TIMING, 0, 0),
                                     service_certificate=forged, base_certificate=base,
                                     run_certificate=run)

    @unittest.skipUnless(all(os.environ.get(name) for name in CERT_ENV), 'current RTL service evidence required')
    def test_residual_period_three_without_run_proof_is_rejected(self) -> None:
        library, base, run, service = (Path(os.environ[name]) for name in CERT_ENV)
        # Given: a current cross-block residual trace and period-three service timing.
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / 'trace.jsonl'
            write_trace(trace, production_run_records())

            # When: admission combines the period-three sweep with the period-five run certificate.
            with self.assertRaisesRegex(ValueError, 'only read-ready period 5'):
                CycleServiceProvider(library, trace,
                                     ReferenceMemoryScenario({**TIMING, 'read_ready_period': 3}, 0, 0),
                                     service_certificate=service, base_certificate=base, run_certificate=run)


if __name__ == '__main__':
    unittest.main()
