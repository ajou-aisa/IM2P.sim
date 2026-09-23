from __future__ import annotations

import copy
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from sim.cycle.corpus_authority import (
    CorpusError, authority, authority_reference, corpus, validate_corpus,
)
from sim.cycle.certificate_contract import (
    CertificateError, MARKER, SCHEMA, VERSION, validate_certificate,
)


ROOT = Path(__file__).resolve().parents[3]
PACKAGE = os.environ.get('IM2P_V4_CANDIDATE_PACKAGE')


class ReviewedV4CasesTest(unittest.TestCase):
    def test_official_contract_admits_v4_authority(self) -> None:
        # Given v4 authority and no profiles, when official admission starts,
        # then its next failure is missing coverage rather than v3-only routing.
        document = {
            'schema': SCHEMA, 'version': VERSION, 'status': 'PASS',
            'first_mismatch': None, 'marker': MARKER,
            'scope': 'isolated-work-accounting', 'timing_profile': 'rtl-regression',
            'execution_kind': 'FRESH_RUN', 'corpus_authority': authority_reference('v4'),
        }
        with self.assertRaisesRegex(CertificateError, 'profiles: array required'):
            validate_certificate(document, Path('/unused'))

    def test_v4_preserves_reviewed_case_set(self) -> None:
        # Given the immutable v3 case authority, when v4 is loaded, then every
        # profile retains the same 15 captured and five synthetic inputs.
        self.assertEqual(corpus('v4'), corpus('v3'))
        self.assertEqual({name: len(rows) for name, rows in corpus('v4').items()},
                         {name: 20 for name in corpus('v3')})

    @unittest.skipUnless(PACKAGE, 'set IM2P_V4_CANDIDATE_PACKAGE for immutable package checks')
    def test_coordinated_case_shrink_is_rejected(self) -> None:
        # Given a complete v4 corpus document, when all reported counts and
        # results are shrunk together, then the independent authority rejects it.
        cases = corpus('v4')
        reviewed = authority('v4')
        document = {
            'corpus_authority': authority_reference('v4'),
            'llama_source': {
                'root': str(Path(PACKAGE or '') / 'dependency/source/llama_cpp_gemmini'),
                'base_head': reviewed['producer_base_head'],
                'source_manifest_sha256': reviewed['source_manifest_sha256'],
                'dependency_lock_sha256': reviewed['dependency_lock_sha256'],
            },
            'expected_cases': {name: [row['case'] for row in rows] for name, rows in cases.items()},
            'captured_corpus_counts': {name: 15 for name in cases},
            'observed_corpus_counts': {name: 15 for name in cases},
            'moved_historical_residual_case_ids': reviewed['moved_historical_residual_case_ids'],
            'cases': [{'profile': name, 'framing': framing, **row}
                      for name, rows in cases.items()
                      for framing in ('regression-tiles', 'planner-blocks') for row in rows],
        }
        validate_corpus(document)
        shortened = copy.deepcopy(document)
        for name in cases:
            shortened['expected_cases'][name].remove('captured-015')
            shortened['captured_corpus_counts'][name] -= 1
        shortened['cases'] = [row for row in shortened['cases'] if row['case'] != 'captured-015']
        with self.assertRaisesRegex(CorpusError, 'identities/counts differ'):
            validate_corpus(shortened)


@unittest.skipUnless(PACKAGE, 'set IM2P_V4_CANDIDATE_PACKAGE for immutable package checks')
class ReviewedV4PackageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.package = Path(PACKAGE or '')
        self.llama = self.package / 'dependency/source/llama_cpp_gemmini'

    def copy_inputs(self, destination: Path) -> Path:
        reviewed = authority('v4')
        producer_sources = reviewed['producer_source_sha256']
        if not isinstance(producer_sources, dict):
            self.fail('reviewed producer source map missing')
        names = [
            'dependency-lock.json', 'source-manifest.json',
            'source/sim/cycle/corpus-authority-v3.json',
            *(f'dependency/source/llama_cpp_gemmini/{name}'
              for name in producer_sources),
        ]
        for name in names:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.package / name, target)
        return destination / 'dependency/source/llama_cpp_gemmini'

    def test_current_package_is_admitted(self) -> None:
        # Given the immutable candidate, when v4 checks its package identity,
        # then the current source and exact lock are admitted.
        self.assertEqual(authority('v4', fixture_root=ROOT, llama_root=self.llama)['version'], 4)

    def test_lock_mutation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary)
            llama = self.copy_inputs(candidate)
            lock = candidate / 'dependency-lock.json'
            lock.write_bytes(lock.read_bytes() + b'\n')
            with self.assertRaisesRegex(CorpusError, 'dependency lock differs'):
                authority('v4', fixture_root=ROOT, llama_root=llama)

    def test_producer_mutation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary)
            llama = self.copy_inputs(candidate)
            source = llama / 'ggml/src/ggml-gemmini/residual/rmd/rmd-executor.cpp'
            source.write_bytes(source.read_bytes() + b'\n')
            with self.assertRaisesRegex(CorpusError, 'producer changed'):
                authority('v4', fixture_root=ROOT, llama_root=llama)


if __name__ == '__main__':
    unittest.main()
