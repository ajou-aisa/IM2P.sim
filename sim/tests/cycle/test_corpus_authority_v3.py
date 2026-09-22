from __future__ import annotations

import copy
import hashlib
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from sim.cycle import corpus_authority as corpus_source
from sim.cycle.certificate_contract import CertificateError, validate_certificate

PACKAGE = Path(os.environ['IM2P_CURRENT_CANDIDATE_PACKAGE']) if 'IM2P_CURRENT_CANDIDATE_PACKAGE' in os.environ \
    else ROOT / '.absent-candidate-package'


class CorpusAuthorityV3ManifestTest(unittest.TestCase):
    def test_freezes_selected_current_cases_and_moved_historical_cases(self) -> None:
        manifest = corpus_source.authority('v3', fixture_root=ROOT)
        rows = corpus_source.corpus('v3')
        old = corpus_source.corpus('v1')
        self.assertEqual(manifest['observed_total'], 15)
        moved = manifest['moved_historical_residual_case_ids']
        self.assertIsInstance(moved, list)
        if not isinstance(moved, list):
            self.fail('moved historical identities are not a list')
        self.assertEqual(len(moved), 27)
        for profile in corpus_source.PROFILES:
            with self.subTest(profile=profile):
                self.assertEqual(len(rows[profile]), 20)
                retained = [row for row in old[profile][:42] if row['provenance'] != 'residual_hp1']
                for index, (actual, historical) in enumerate(zip(rows[profile][:15], retained), start=1):
                    self.assertEqual(actual['case'], f'captured-{index:03}')
                    self.assertEqual(actual['captured_case'], index)
                    self.assertEqual({key: actual[key] for key in ('shape', 'tile', 'timing', 'raw', 'provenance')},
                                     {key: historical[key] for key in ('shape', 'tile', 'timing', 'raw', 'provenance')})


@unittest.skipUnless(PACKAGE.is_dir(), 'current candidate source package required')
class CorpusAuthorityV3Test(unittest.TestCase):
    def source(self) -> dict[str, str]:
        lock_digest = corpus_source.authority('v3')['dependency_lock_sha256']
        self.assertIsInstance(lock_digest, str)
        if not isinstance(lock_digest, str):
            self.fail('candidate lock digest is not a string')
        return {
            'root': str(PACKAGE / 'dependency/source/llama_cpp_gemmini'),
            'base_head': 'c61702970df8b287b1ed75b3e51abab559021b51',
            'source_manifest_sha256': hashlib.sha256((PACKAGE / 'source-manifest.json').read_bytes()).hexdigest(),
            'dependency_lock_sha256': lock_digest,
        }

    def document(self) -> dict:
        rows = corpus_source.corpus('v3')
        return {
            'corpus_authority': corpus_source.authority_reference('v3'),
            'llama_source': self.source(),
            'expected_cases': {name: [row['case'] for row in cases] for name, cases in rows.items()},
            'captured_corpus_counts': {name: 15 for name in rows},
            'observed_corpus_counts': {name: 15 for name in rows},
            'moved_historical_residual_case_ids': corpus_source.authority('v3')['moved_historical_residual_case_ids'],
            'cases': [{'profile': name, 'framing': framing, **copy.deepcopy(row)}
                      for name, cases in rows.items()
                      for framing in ('regression-tiles', 'planner-blocks') for row in cases],
        }

    def test_accepts_current_package_and_complete_fixed_corpus(self) -> None:
        document = self.document()
        corpus_source.authority('v3', fixture_root=ROOT,
                                llama_root=PACKAGE / 'dependency/source/llama_cpp_gemmini')
        corpus_source.validate_corpus(document)
        self.assertEqual(len(document['cases']), 6 * 2 * 20)

    def test_rejects_coordinated_shrink_and_changed_case_geometry(self) -> None:
        document = self.document()
        for mutation in ('shrink', 'id', 'shape', 'tile'):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(document)
                if mutation == 'shrink':
                    for name in changed['expected_cases']:
                        changed['expected_cases'][name].remove('captured-015')
                        changed['captured_corpus_counts'][name] = 14
                        changed['observed_corpus_counts'][name] = 14
                    changed['cases'] = [row for row in changed['cases'] if row['case'] != 'captured-015']
                else:
                    row = changed['cases'][0]
                    if mutation == 'id':
                        row['case'] = 'captured-999'
                    else:
                        row[mutation][0] += 1
                with self.assertRaises(corpus_source.CorpusError):
                    corpus_source.validate_corpus(changed)

    def test_rejects_fixture_only_or_wrong_package_claim(self) -> None:
        document = self.document()
        for mutation in ('missing', 'v2', 'manifest', 'head'):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(document)
                if mutation == 'missing':
                    del changed['llama_source']
                elif mutation == 'v2':
                    changed['corpus_authority'] = corpus_source.authority_reference('v2')
                elif mutation == 'manifest':
                    changed['llama_source']['source_manifest_sha256'] = '0' * 64
                else:
                    changed['llama_source']['base_head'] = '0' * 40
                with self.assertRaises(corpus_source.CorpusError):
                    corpus_source.validate_corpus(changed)

    def test_current_certificate_rejects_historical_authority(self) -> None:
        library = PACKAGE / 'source-manifest.json'
        document = {
            'schema': 'im2p-single-gemm-cycle-certificate', 'version': 2,
            'status': 'PASS', 'first_mismatch': None,
            'marker': 'IM2P_SINGLE_GEMM_CYCLE_MODEL_CURRENT',
            'scope': 'isolated-work-accounting', 'timing_profile': 'rtl-regression',
            'execution_kind': 'FRESH_RUN',
            'profiles': list(corpus_source.PROFILES),
            'framings': ['regression-tiles', 'planner-blocks'],
            'model_library_sha256': hashlib.sha256(library.read_bytes()).hexdigest(),
            'corpus_authority': corpus_source.authority_reference('v2'),
        }
        with self.assertRaisesRegex(CertificateError, 'requires v3'):
            validate_certificate(document, library)


if __name__ == '__main__':
    unittest.main()
