from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from sim.cycle import corpus_authority as corpus_source
from sim.tests.cycle.certificate_document import complete_document


PINNED = Path(__file__).resolve().parents[4] / 'evidence/rmd-run-aware-20260921T062828Z/llama-develop'
ARCHIVED = Path(__file__).resolve().parents[4] / 'evidence/rmd-run-aware-20260921T062828Z/task-06-legacy-baseline/source'
PROFILES = tuple(f'a{bits}w{bits}-d{dim}-hp1' for bits in (4, 8) for dim in (16, 32, 64))


class CorpusAuthorityV2Test(unittest.TestCase):
    def test_v1_accepts_archived_source_but_rejects_current_fixture(self) -> None:
        self.assertEqual(corpus_source.authority('v1', fixture_root=ARCHIVED)['version'], 1)
        with self.assertRaisesRegex(corpus_source.CorpusError, 'fixture changed'):
            corpus_source.authority('v1', fixture_root=corpus_source.ROOT)

    def test_v2_freezes_fixed_identities_and_source_arithmetic(self) -> None:
        document = corpus_source.authority('v2', fixture_root=corpus_source.ROOT, llama_root=PINNED)
        rows = corpus_source.corpus('v2')
        historical = corpus_source.corpus('v1')
        self.assertEqual(document['version'], 2)
        self.assertEqual(set(rows), set(PROFILES))
        for profile in PROFILES:
            with self.subTest(profile=profile):
                self.assertEqual(len(rows[profile]), 47)
                self.assertEqual([row['case'] for row in rows[profile]],
                                 [f'captured-{i:03}' for i in range(1, 43)] +
                                 [f'large-k-{k}' for k in (32, 64, 96, 3072, 8256)])
                changes = [case for case, old in zip(rows[profile][:42], historical[profile][:42])
                           if case != old]
                self.assertEqual(len(changes), 27)
                self.assertEqual(len(rows[profile][:42]) - len(changes), 15)
                dim = int(profile.split('-d')[1].split('-')[0])
                block0 = 60 if profile.startswith('a4') else 33
                stripe0 = 20 if profile.startswith('a4') else 11
                for identity, m in (('captured-008', block0), ('captured-010', stripe0),
                                    ('captured-009', 15), ('captured-011', 5)):
                    case = next(row for row in rows[profile] if row['case'] == identity)
                    shape, tile = case['shape'], case['tile']
                    if not isinstance(shape, list) or not isinstance(tile, list):
                        self.fail('corpus shape/tile missing')
                    self.assertEqual(shape[0], m)
                    self.assertEqual(tile[0], (m + dim - 1) // dim)

    def test_v2_rejects_coordinated_deletion_and_changed_geometry(self) -> None:
        rows = corpus_source.corpus('v2')
        document = {'corpus_authority': corpus_source.authority_reference('v2'),
                    'expected_cases': {name: [row['case'] for row in cases] for name, cases in rows.items()},
                    'captured_corpus_counts': {name: 42 for name in rows},
                    'cases': [{'profile': name, 'framing': framing, **copy.deepcopy(row)}
                              for name, cases in rows.items()
                              for framing in ('regression-tiles', 'planner-blocks') for row in cases],
                    'llama_source': {'root': str(PINNED),
                                     'head': '71a8c0328cd436226b8ec5fad03adafac93940ed'}}
        corpus_source.validate_corpus(document)
        for bad in ('shrink', 'captured-008', 'captured-010', 'duplicate-case', 'measured-answer'):
            with self.subTest(bad=bad):
                changed = copy.deepcopy(document)
                if bad == 'shrink':
                    for name in PROFILES:
                        changed['expected_cases'][name].remove('captured-042')
                        changed['captured_corpus_counts'][name] = 41
                    changed['cases'] = [row for row in changed['cases'] if row['case'] != 'captured-042']
                elif bad == 'duplicate-case':
                    changed['cases'].append(copy.deepcopy(changed['cases'][0]))
                elif bad == 'measured-answer':
                    changed['cases'][0]['expected_cycles'] = 123
                else:
                    case = next(row for row in changed['cases'] if row['case'] == bad)
                    case['shape'][0] += 1
                    case['tile'][0] += 1
                with self.assertRaises(corpus_source.CorpusError):
                    corpus_source.validate_corpus(changed)

    def test_complete_document_preserves_only_valid_explicit_v2_reference(self) -> None:
        rows = corpus_source.corpus('v2')
        expected = {name: [str(row['case']) for row in cases] for name, cases in rows.items()}
        document = {'status': 'PASS', 'corpus_authority': corpus_source.authority_reference('v2'),
                    'llama_source': {'root': str(PINNED),
                                     'head': '71a8c0328cd436226b8ec5fad03adafac93940ed'},
                    'captured_corpus_counts': {name: 42 for name in rows},
                    'cases': [{'profile': name, 'framing': framing, **copy.deepcopy(row)}
                              for name, cases in rows.items()
                              for framing in ('regression-tiles', 'planner-blocks') for row in cases]}

        def checked(result: dict, _library: Path) -> dict:
            corpus_source.validate_corpus(result)
            return result

        with patch('sim.cycle.certificate_contract.finalize_certificate', side_effect=checked):
            complete = complete_document(document, expected, PINNED, 'FRESH_RUN')
            self.assertEqual(complete['corpus_authority'], document['corpus_authority'])
            invalid = copy.deepcopy(document)
            invalid['corpus_authority']['sha256'] = '0' * 64
            with self.assertRaises(corpus_source.CorpusError):
                complete_document(invalid, expected, PINNED, 'FRESH_RUN')

    def test_v2_rejects_duplicate_profile_or_case_and_answer_injection(self) -> None:
        original = json.loads(corpus_source.MANIFEST_V2.read_text())
        for bad in ('duplicate-profile', 'duplicate-case', 'swapped-residual-groups',
                    'measured-duration', 'measured-events'):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as directory:
                changed = copy.deepcopy(original)
                if bad == 'duplicate-profile':
                    changed['profiles'].append(copy.deepcopy(changed['profiles'][0]))
                elif bad == 'duplicate-case':
                    changed['captured_case_ids'].append(changed['captured_case_ids'][0])
                elif bad == 'swapped-residual-groups':
                    changed['identity_groups']['full_block0'][0] = 'captured-010'
                    changed['identity_groups']['stripe_block0'][0] = 'captured-008'
                elif bad == 'measured-duration':
                    changed['expected_cycles'] = 123
                else:
                    changed['expected_events'] = [{'cycle': 1, 'type': 'work'}]
                path = Path(directory) / 'v2.json'
                path.write_text(json.dumps(changed))
                with patch.object(corpus_source, 'MANIFEST_V2', path), \
                     self.assertRaises(corpus_source.CorpusError):
                    corpus_source.authority('v2', fixture_root=corpus_source.ROOT, llama_root=PINNED)

    def test_v2_rejects_wrong_producer_fixture_or_source_head(self) -> None:
        original = json.loads(corpus_source.MANIFEST_V2.read_text())
        for field, path in (
            ('producer_source_sha256', 'ggml/src/ggml-gemmini/residual/rmd/rmd-builder.cpp'),
            ('fixture_source_sha256', 'fpga/gemmini_hp1/host/rmd_rtl_fixture.hpp'),
        ):
            with self.subTest(missing=path):
                changed = copy.deepcopy(original)
                del changed[field][path]
                with self.assertRaises(corpus_source.CorpusError):
                    corpus_source._validate_v2(changed)
        for bad in ('producer', 'fixture'):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as directory:
                changed = copy.deepcopy(original)
                field = 'producer_source_sha256' if bad == 'producer' else 'fixture_source_sha256'
                first = next(iter(changed[field]))
                changed[field][first] = '0' * 64
                path = Path(directory) / 'v2.json'
                path.write_text(json.dumps(changed))
                with patch.object(corpus_source, 'MANIFEST_V2', path), \
                     self.assertRaises(corpus_source.CorpusError):
                    corpus_source.authority('v2', fixture_root=corpus_source.ROOT, llama_root=PINNED)
        with self.assertRaises(corpus_source.CorpusError):
            corpus_source.authority('v2', fixture_root=corpus_source.ROOT,
                                    llama_root=corpus_source.ROOT.parent / 'llama.cpp-gemmini')

    def test_v2_manifest_bytes_are_frozen_even_when_json_meaning_is_same(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'v2.json'
            path.write_bytes(corpus_source.MANIFEST_V2.read_bytes() + b'\n')
            with patch.object(corpus_source, 'MANIFEST_V2', path), \
                 self.assertRaises(corpus_source.CorpusError):
                corpus_source.authority('v2')


if __name__ == '__main__':
    unittest.main()
