from __future__ import annotations

import copy
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from collections.abc import Callable

from sim.cycle.certificate_contract import array_value, object_value, read_document
from sim.cycle.cli import estimate_service
from sim.cycle.execution_cycle_provider import CycleServiceProvider, ReferenceMemoryScenario
from sim.cycle.execution_ir import ExecutionError, ServiceId
from sim.cycle.npu_trace import model_document
from sim.cycle.npu_trace_integrity import read_records, start_trace
from sim.cycle.npu_trace_schema import Record, integer, text
from sim.cycle.production_sequence_certificate import load_corpus
from sim.cycle.production_sequence_evidence import CertifiedCase, compare_raw_work, validate_case
from sim.cycle.service_certificate import ServiceAdmission
from sim.tests.cycle.production_sequence_work import parse_probe_log, validate_probe_log

REPORT = os.environ.get('IM2P_SEQUENCE_REPORT')
LIBRARY = os.environ.get('IM2P_CYCLE_LIBRARY')
TIMING: Record = {'backing_read_delay': 3, 'even_read_id_delay': 13,
                  'scale_read_extra_delay': 17, 'backing_write_delay': 11,
                  'backing_cycle_offset': 5, 'read_ready_period': 5}


@unittest.skipUnless(REPORT and LIBRARY, 'current A4D16 sequence report and library required')
class RealSequenceMutationTests(unittest.TestCase):
    def setUp(self) -> None:
        assert REPORT is not None and LIBRARY is not None
        self.report_path = Path(REPORT)
        self.library = Path(LIBRARY)
        self.report = read_document(self.report_path)
        self.profile = 'a4w4-d16-hp1'
        self.assertEqual(self.report['profile'], self.profile)
        self.projection_path = self.report_path.parent / 'projection.json'
        self.projection = read_document(self.projection_path)
        self.raw = (self.report_path.parent / 'rtl.log').read_text()
        _, self.rows, _, _ = parse_probe_log(self.raw)
        self.projected = [object_value(value, 'work') for value in
                          array_value(self.projection['works'], 'works')]
        producer = object_value(self.report['producer_artifacts'], 'producer')
        self.trace = Path(text(object_value(producer['trace'], 'trace'), 'path'))
        records = read_records(self.trace)
        state = start_trace(records)
        self.works = [work for record in records if (work := state.consume(record)) is not None]
        _ = state.summary()
        profiles = object_value(load_corpus()['profiles'], 'profiles')
        self.pinned = object_value(profiles[self.profile], 'profile')

    def admitted_case(self) -> CertifiedCase:
        return validate_case(self.report_path, self.library, self.profile,
                             (0, 0, 0, 0), self.pinned, TIMING)

    def expected(self) -> list[tuple[int, str]]:
        return [(integer(row, 'work_id'), text(row, 'work_binding')) for row in self.rows]

    def model_pair(self, ordinal: int) -> tuple[Record, Record]:
        original = model_document(self.profile, self.works[ordinal])
        row = self.rows[ordinal]
        halves = (0, 0) if ordinal == 0 else (
            integer(self.rows[ordinal - 1], 'next_scratchpad_half'),
            integer(self.rows[ordinal - 1], 'next_accumulator_half'))
        request = {**object_value(original['request'], 'request'),
                   'accepted_cycle': integer(row, 'accepted'),
                   'initial_scratchpad_half': halves[0],
                   'initial_accumulator_half': halves[1], 'record_events': 1}
        document: Record = {**original, 'request': request, 'timing': TIMING}
        return document, estimate_service(self.library, document)

    def rejects_report_change(self, change: Callable[[Record], None], reason: str) -> None:
        altered = copy.deepcopy(self.report)
        change(altered)
        original_read = read_document
        with patch('sim.cycle.production_sequence_evidence.read_document',
                   side_effect=lambda path: altered if path == self.report_path else original_read(path)):
            with self.assertRaisesRegex(ValueError, reason):
                self.admitted_case()

    def test_current_case_passes(self) -> None:
        self.assertEqual(len(self.admitted_case().works), 4)

    def test_work_reorder_rejected(self) -> None:
        lines = self.raw.splitlines(keepends=True)
        indices = [index for index, line in enumerate(lines) if line.startswith('SEQUENCE_WORK ')]
        lines[indices[0]], lines[indices[1]] = lines[indices[1]], lines[indices[0]]
        with self.assertRaisesRegex(ValueError, 'order'):
            validate_probe_log(''.join(lines), self.expected())

    def test_residual_run_block_mutation_rejected(self) -> None:
        altered = copy.deepcopy(self.projection)
        work = object_value(array_value(altered['works'], 'works')[1], 'residual')
        run = object_value(array_value(work['runs'], 'runs')[0], 'run')
        run['original_k_mask'] = 0
        original_read = read_document
        with patch('sim.cycle.production_sequence_evidence.read_document',
                   side_effect=lambda path: altered if path == self.projection_path else original_read(path)):
            with self.assertRaisesRegex(ValueError, 'projection'):
                self.admitted_case()

    def test_slot_mutation_rejected(self) -> None:
        row = {**self.rows[0], 'slot': 1}
        document, answer = self.model_pair(0)
        with self.assertRaisesRegex(ValueError, 'identity'):
            compare_raw_work(row, self.projected[0], answer, document, 0, 5, (0, 0), True)

    def test_initial_half_mutation_rejected(self) -> None:
        row = {**self.rows[0], 'initial_scratchpad_half': 1}
        document, answer = self.model_pair(0)
        with self.assertRaisesRegex(ValueError, 'initial halves'):
            compare_raw_work(row, self.projected[0], answer, document, 0, 5, (0, 0), True)

    def test_next_half_mutation_rejected(self) -> None:
        row = {**self.rows[0], 'next_accumulator_half': 1 - integer(self.rows[0], 'next_accumulator_half')}
        document, answer = self.model_pair(0)
        with self.assertRaisesRegex(ValueError, 'next half'):
            compare_raw_work(row, self.projected[0], answer, document, 0, 5, (0, 0), True)

    def test_accepted_epoch_mutation_rejected(self) -> None:
        row = {**self.rows[0], 'accepted': integer(self.rows[0], 'accepted') + 5}
        document, answer = self.model_pair(0)
        with self.assertRaisesRegex(ValueError, 'model request'):
            compare_raw_work(row, self.projected[0], answer, document, 0, 5, (0, 0), True)

    def test_mid_sequence_reset_rejected(self) -> None:
        altered = self.raw.replace('"reset_count":1', '"reset_count":2', 1)
        with self.assertRaisesRegex(ValueError, 'reset'):
            validate_probe_log(altered, self.expected())

    def test_early_acceptance_rejected(self) -> None:
        case = self.admitted_case()
        admission = ServiceAdmission('service', 'base', 'run', 'trace', 'p5/00', self.profile, 4)
        with patch('sim.cycle.execution_cycle_provider.validate_service_certificate',
                   return_value=(admission, (case,))):
            provider = CycleServiceProvider(self.library, self.trace, ReferenceMemoryScenario(TIMING, 0, 0),
                                            service_certificate=self.report_path,
                                            base_certificate=self.report_path,
                                            run_certificate=self.report_path)
        first = provider.requests[ServiceId('npu:0')].work
        provider.estimate(first, integer(self.rows[0], 'accepted'))
        second = provider.requests[ServiceId('npu:1')].work
        with self.assertRaisesRegex(ExecutionError, 'before resource ready'):
            provider.estimate(second, provider.previous_resource_cycle - 1)

    def test_plus_one_event_rejected(self) -> None:
        lines = self.raw.splitlines(keepends=True)
        index = next(i for i, line in enumerate(lines) if line.startswith('MODEL_EVENT '))
        parts = lines[index].split()
        parts[3] = str(int(parts[3]) + 1)
        lines[index] = ' '.join(parts) + '\n'
        with self.assertRaisesRegex(ValueError, 'event'):
            validate_probe_log(''.join(lines), self.expected())

    def test_source_hash_mutation_rejected(self) -> None:
        def change(row: Record) -> None:
            sources = object_value(row['source_sha256'], 'sources')
            sources['sim/tests/cycle/production_sequence_probe.cpp'] = '0' * 64
        self.rejects_report_change(change, 'runner source closure')

    def test_build_hash_mutation_rejected(self) -> None:
        def change(row: Record) -> None:
            object_value(row['rtl_build_binding'], 'build')['sha256'] = '0' * 64
        self.rejects_report_change(change, 'RTL build artifact changed')

    def test_trace_hash_mutation_rejected(self) -> None:
        def change(row: Record) -> None:
            producer = object_value(row['producer_artifacts'], 'producer')
            object_value(producer['trace'], 'trace')['sha256'] = '0' * 64
        self.rejects_report_change(change, 'trace artifact changed')


if __name__ == '__main__':
    unittest.main()
