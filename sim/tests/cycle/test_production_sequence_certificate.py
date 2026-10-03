from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sim.cycle import production_sequence_certificate as certificate
from sim.cycle.npu_trace_schema import Record
from sim.cycle.production_sequence_evidence import compare_raw_work, validate_case
from sim.cycle.reconstruct_graph import Manifest, sha256

ROOT = Path(__file__).resolve().parents[3]
TIMING: Record = {'backing_read_delay': 3, 'even_read_id_delay': 13,
                  'scale_read_extra_delay': 17, 'backing_write_delay': 11,
                  'backing_cycle_offset': 5, 'read_ready_period': 5}


class ProductionSequenceCertificateTests(unittest.TestCase):
    def test_build_records_selected_artifact_without_relabeling_legacy_default(self) -> None:
        # Serialization seam only; parent and raw-case proofs are not native evidence here.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = root / 'selected.json'
            selected.write_bytes(certificate.CORPUS.read_bytes())
            library, base, run, fixture, trace = (root / name for name in ('library', 'base', 'run', 'fixture', 'trace'))
            for path in (library, base, run, trace):
                path.write_text('{}')
            fixture.write_text(json.dumps({'base_certificate_sha256': sha256(base),
                                           'run_certificate_sha256': sha256(run)}))
            report = root / 'report.json'
            report.write_text(json.dumps({'producer_artifacts': {
                'trace': {'path': str(trace), 'sha256': sha256(trace)}}}))
            inputs = root / 'inputs.json'
            inputs.write_text(json.dumps({'cases': [
                {'profile': profile, 'phases': [phase] * 4, 'report': str(report)}
                for profile in certificate.PROFILES for phase in range(5)]}))
            with patch('sim.cycle.certificate_contract.validate_certificate'), \
                 patch('sim.cycle.run_aware_certificate.validate_run_certificate', return_value='PRODUCTION_GENERATED'), \
                 patch('sim.cycle.service_certificate.validate_evidence'), \
                 patch.object(certificate, 'validate_production_sequence', return_value=()):
                output = root / 'selected-certificate.json'
                certificate.build(inputs, library, base, run, fixture, output, corpus=selected)
                current = json.loads(output.read_text())
                self.assertEqual(current['corpus_authority'], {'path': str(selected.resolve()), 'sha256': sha256(selected)})
                self.assertEqual(current['corpus_sha256'], sha256(selected))
                legacy = root / 'legacy-certificate.json'
                certificate.build(inputs, library, base, run, fixture, legacy)
                self.assertNotIn('corpus_authority', json.loads(legacy.read_text()))

    def test_explicit_approved_copy_is_selected_without_changing_default(self) -> None:
        original = certificate.load_corpus()
        with tempfile.TemporaryDirectory() as directory:
            selected = Path(directory) / 'selected.json'
            selected.write_bytes(certificate.CORPUS.read_bytes())
            self.assertEqual(certificate.load_corpus(selected), original)
            selected.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'unapproved selected corpus'):
                certificate.load_corpus(selected)
            self.assertEqual(certificate.load_corpus(), original)

    def test_current_native_corpus_requires_explicit_selection_and_exact_bytes(self) -> None:
        # Given: independently reviewed current captures and the unchanged legacy default.
        current = ROOT / 'sim/cycle/production_sequence_corpus_current.json'
        historical = certificate.load_corpus()
        # When: the current corpus is explicitly selected.
        selected = certificate.load_corpus(current)
        # Then: only its exact reviewed bytes are accepted, with the fixed domain preserved.
        self.assertNotEqual(selected['producer_commit'], historical['producer_commit'])
        for key in ('case_count', 'phase_vectors', 'work_ids', 'initial_halves', 'period'):
            self.assertEqual(selected[key], historical[key])
        self.assertEqual(certificate.load_corpus(), historical)
        with tempfile.TemporaryDirectory() as directory:
            altered = Path(directory) / 'current.json'
            document = dict(selected)
            document['producer_commit'] = historical['producer_commit']
            altered.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, 'unapproved selected corpus'):
                certificate.load_corpus(altered)

    def test_rehashed_unapproved_selection_cannot_fall_back_to_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = root / 'unapproved.json'
            altered = certificate.load_corpus()
            altered['producer_commit'] = 'unit-only-unapproved-source'
            selected.write_text(json.dumps(altered))
            library = root / 'library'
            library.write_bytes(b'unit-only library, never loaded')
            document: Record = {
                'schema': certificate.SCHEMA, 'version': 2,
                'artifact_role': certificate.ROLE, 'status': 'PASS',
                'corpus_sha256': sha256(selected), 'library_sha256': sha256(library),
                'corpus_authority': {'path': str(selected), 'sha256': sha256(selected)},
                'source_sha256': {name: sha256(ROOT / name) for name in certificate.SOURCE_PATHS},
                'cases': [],
            }
            with self.assertRaisesRegex(ValueError, 'unapproved selected corpus'):
                certificate.validate_production_sequence(document, library, root / 'trace', TIMING, (0, 0))
            output = root / 'certificate.json'
            with self.assertRaisesRegex(ValueError, 'unapproved selected corpus'):
                certificate.build(root / 'inputs', library, root / 'base', root / 'run',
                                  root / 'fixture', output, corpus=selected)
            self.assertFalse(output.exists())

    def test_receipt_binding_uses_selected_corpus_metadata_at_every_boundary(self) -> None:
        # Unit-only receipt context: no current corpus or native capture is approved.
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            sim, llama = workspace / 'sim', workspace / 'llama.cpp-gemmini'
            sim.mkdir()
            llama.mkdir()
            profile = 'a4w4-d16-hp1'
            captured = workspace / 'capture' / profile
            (captured / 'run').mkdir(parents=True)
            trace = captured / 'run/npu-cycle-trace.jsonl'
            trace.write_text('unit-only raw identity\n')
            diff = captured / 'producer.diff'
            diff.write_text('unit-only dirty source contents\n')
            source_lines = []
            for index in range(27):
                source = llama / f'unit-{index}.cpp'
                source.write_text(f'unit-only source {index}\n')
                source_lines.append(f'{sha256(source)}  {source.name}')
            source_manifest = captured / 'source-sha256.txt'
            source_manifest.write_text('\n'.join(source_lines) + '\n')
            source_digest = sha256(source_manifest)
            hardware = '1' * 64
            commit = 'unit-only-selected-producer-commit'
            receipt: Record = {
                'schema': 'im2p-native-producer-corpus-receipt', 'version': 1,
                'profile': profile, 'scope': 'native-production-dispatch-fixture',
                'source': {'llama_commit': commit, 'producer_dirty_diff_sha256': sha256(diff),
                           'producer_dirty_diff': diff.name, 'source_manifest_sha256': source_digest},
                'hardware_contract_sha256': hardware,
                'artifacts_sha256': {'run/npu-cycle-trace.jsonl': sha256(trace)},
                'validation': {'strict_manifest_lifecycle': 'PASS'},
            }
            receipt_path = captured / 'receipt.json'
            receipt_path.write_text(json.dumps(receipt))
            (captured / 'qualification.json').write_text('{"status":"PASS"}')
            pinned: Record = {'receipt_sha256': sha256(receipt_path),
                              'source_manifest_sha256': source_digest,
                              'hardware_contract_sha256': hardware}
            selected: Record = {'producer_commit': commit, 'producer_dirty_diff_sha256': sha256(diff),
                                'profiles': {profile: pinned}}
            graph = Manifest({'producer': {'git_commit': commit,
                              'source_manifest_sha256': source_digest,
                              'hardware_contract_sha256': hardware}}, [], [], {}, {}, {})
            with patch.object(certificate, 'ROOT', sim), patch.object(certificate, 'read_manifest', return_value=graph):
                certificate.validate_producer_receipt(profile, trace, selected)
                for changed in ({**selected, 'producer_commit': 'wrong'},
                                {**selected, 'producer_dirty_diff_sha256': '0' * 64}):
                    with self.subTest(changed=changed), self.assertRaises(ValueError):
                        certificate.validate_producer_receipt(profile, trace, changed)
                trace.write_text('changed raw bytes\n')
                with self.assertRaisesRegex(ValueError, 'raw artifact differs'):
                    certificate.validate_producer_receipt(profile, trace, selected)

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
