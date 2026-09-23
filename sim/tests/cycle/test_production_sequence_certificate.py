from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sim.cycle import production_sequence_certificate as certificate
from sim.cycle.npu_trace_schema import Record
from sim.cycle.production_sequence_evidence import compare_raw_work, validate_case
from sim.cycle.reconstruct_graph import sha256

ROOT = Path(__file__).resolve().parents[3]
TIMING: Record = {'backing_read_delay': 3, 'even_read_id_delay': 13,
                  'scale_read_extra_delay': 17, 'backing_write_delay': 11,
                  'backing_cycle_offset': 5, 'read_ready_period': 5}


class ProductionSequenceCertificateTests(unittest.TestCase):
    def test_observed_undrained_runner_report_is_rejected(self) -> None:
        # Given: a same-instance report with public parity but unresolved tag carry.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'report.json'
            path.write_text(json.dumps({
                'schema': 'im2p-producer-continuous-rtl-run', 'version': 1,
                'status': 'OBSERVED_UNDRAINED', 'profile': 'a4w4-d16-hp1',
                'period': 5, 'phases': [0, 0, 0, 0], 'work_count': 4,
                'instance_count': 1, 'reset_count': 1,
                'event_plus_one_mutation_rejected': True,
            }))

            # When: certificate aggregation considers the report.
            # Then: it refuses status promotion before trusting any artifact labels.
            with self.assertRaisesRegex(ValueError, 'scope or mutation proof incomplete'):
                validate_case(path, root / 'library', 'a4w4-d16-hp1',
                              (0, 0, 0, 0), {}, TIMING)

    def test_pinned_corpus_rejects_shrunk_case_set(self) -> None:
        # Given: a readable corpus that removes one expected phase.
        with tempfile.TemporaryDirectory() as directory:
            altered = json.loads(certificate.CORPUS.read_text())
            altered['phase_vectors'].pop()
            path = Path(directory) / 'short.json'
            path.write_text(json.dumps(altered))

            # When: the independent corpus pin is validated.
            # Then: changing the case denominator is rejected.
            with patch.object(certificate, 'CORPUS', path), self.assertRaisesRegex(ValueError, 'corpus changed'):
                certificate.load_corpus()

    def test_label_only_production_document_cannot_admit(self) -> None:
        # Given: a production label and current source hashes but no sequence cases.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / 'library'
            library.write_bytes(b'fixture')
            document = {
                'schema': certificate.SCHEMA, 'version': 2,
                'artifact_role': certificate.ROLE, 'status': 'PASS',
                'corpus_sha256': certificate.CORPUS_SHA256,
                'library_sha256': sha256(library),
                'source_sha256': {name: sha256(ROOT / name) for name in certificate.SOURCE_PATHS},
                'cases': [],
            }

            # When: the label is offered as production proof.
            # Then: the pinned 30-case denominator blocks admission.
            with self.assertRaisesRegex(ValueError, 'case set'):
                certificate.validate_production_sequence(document, library, root / 'trace', TIMING, (0, 0))

    def test_public_drain_without_internal_proof_is_rejected(self) -> None:
        # Given: a raw work whose public drain flags pass while the tag queue persists.
        trace = {'parent_id': 7, 'call_id': 3, 'stripe_id': 0, 'row_begin': 0, 'parent_m': 1}
        inputs = {'m': 1, 'n': 1, 'k': 32, 'tile_i_count': 1,
                  'tile_j_count': 1, 'tile_k_count': 1}
        projected = {'work_id': 0, 'work_binding': 'bound', 'slot': 0,
                     'accepted_phase': 1, 'trace_record': trace, 'input': inputs}
        row = {'ordinal': 0, 'work_id': 0, 'work_binding': 'bound', 'slot': 0,
               'accepted': 1, 'accepted_phase': 1, 'backing_cycle_offset': 5,
               'initial_scratchpad_half': 0, 'initial_accumulator_half': 0,
               'numeric_pass': True, 'mesh_tag_queue_len': 1,
               **{key: value for key, value in trace.items()},
               'm': 1, 'n': 1, 'k': 32, 'tile_i': 1, 'tile_j': 1, 'tile_k': 1}
        for flag in ('drain_work_ready', 'drain_busy_clear', 'drain_memory', 'drain_writeback',
                     'drain_controller', 'drain_backing_reads', 'drain_backing_writes',
                     'metadata_queue_empty', 'mesh_row_queue_empty', 'mesh_request_idle'):
            row[flag] = True
        row['internal_queue_drain_observed'] = False

        # When: the raw row reaches the independent service validator.
        # Then: public readiness cannot replace internal carry proof.
        with self.assertRaisesRegex(ValueError, 'internal drain incomplete'):
            compare_raw_work(row, projected, {'service': {}, 'result': {}},
                             {'request': {}}, 0, 5, (0, 0))


if __name__ == '__main__':
    unittest.main()
