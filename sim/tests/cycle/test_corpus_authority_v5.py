from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle import corpus_authority as source
from sim.cycle.certificate_contract import (
    MARKER,
    SCHEMA,
    VERSION,
    CertificateError,
    validate_certificate,
)

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = os.environ.get('IM2P_V5_CANDIDATE_PACKAGE')


def inputs(package: Path) -> dict[str, JsonValue]:
    rows = source.corpus('v4')
    reviewed = source.authority('v5')
    return {
        'corpus_authority': source.authority_reference('v5'),
        'llama_source': {
            'root': str(package / 'dependency/source/llama_cpp_gemmini'),
            'base_head': reviewed['producer_base_head'],
            'dependency_lock_sha256': reviewed['dependency_lock_sha256'],
            'source_manifest_sha256': reviewed['source_manifest_sha256'],
        },
        'expected_cases': {name: [row['case'] for row in cases] for name, cases in rows.items()},
        'captured_corpus_counts': {name: 15 for name in rows},
        'observed_corpus_counts': {name: 15 for name in rows},
        'moved_historical_residual_case_ids': reviewed['moved_historical_residual_case_ids'],
        'cases': [{'profile': name, 'framing': framing, **row}
                  for name, cases in rows.items()
                  for framing in ('regression-tiles', 'planner-blocks') for row in cases],
    }


class ReviewedV5Test(unittest.TestCase):
    def test_inherits_all_fixed_v4_inputs(self) -> None:
        # Given immutable v4 input authority, when v5 loads, then all 240 stay fixed.
        self.assertEqual(source.corpus('v5'), source.corpus('v4'))
        self.assertEqual(sum(len(rows) * 2 for rows in source.corpus('v5').values()), 240)

    def test_official_contract_admits_v5_authority_without_claiming_pass(self) -> None:
        # Given v5 but incomplete evidence, when admitted, then coverage still fails.
        document = {
            'schema': SCHEMA, 'version': VERSION, 'status': 'PASS', 'first_mismatch': None,
            'marker': MARKER, 'scope': 'isolated-work-accounting', 'timing_profile': 'rtl-regression',
            'execution_kind': 'FRESH_RUN', 'corpus_authority': source.authority_reference('v5'),
        }
        with self.assertRaisesRegex(CertificateError, 'profiles: array required'):
            validate_certificate(document, Path('/unused'))

    def test_certificate_rejects_wrong_profiles_framings_and_library(self) -> None:
        # Given a claimed PASS, when its coverage or library binding is stale,
        # then official admission rejects it before any result can be trusted.
        with tempfile.TemporaryDirectory() as directory:
            library = Path(directory) / 'library'
            library.write_bytes(b'current-library')
            original: dict[str, JsonValue] = {
                'schema': SCHEMA, 'version': VERSION, 'status': 'PASS', 'first_mismatch': None,
                'marker': MARKER, 'scope': 'isolated-work-accounting', 'timing_profile': 'rtl-regression',
                'execution_kind': 'FRESH_RUN', 'corpus_authority': source.authority_reference('v5'),
                'profiles': list(source.PROFILES), 'framings': ['regression-tiles', 'planner-blocks'],
                'model_library_sha256': hashlib.sha256(b'current-library').hexdigest(),
            }
            for field in ('profiles', 'framings', 'model_library_sha256'):
                changed = copy.deepcopy(original)
                changed[field] = '0' * 64 if field == 'model_library_sha256' else []
                with self.subTest(field=field), self.assertRaises(CertificateError):
                    validate_certificate(changed, library)

    def test_new_manifest_is_immutable(self) -> None:
        # Given a copied authority, when altered or assigned another role, then reject.
        for mutation in ('bytes', 'role', 'parent', 'extra'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                original = source.MANIFEST_V5.read_bytes()
                changed = json.loads(original)
                if mutation == 'role':
                    changed['scope'] = 'whole-package-current'
                if mutation == 'parent':
                    changed['v4_manifest_sha256'] = '0' * 64
                if mutation == 'extra':
                    changed['observed_total'] = 1
                candidate = Path(directory) / 'authority.json'
                candidate.write_bytes(original + b'\n' if mutation == 'bytes'
                                      else json.dumps(changed).encode())
                with patch.object(source, 'MANIFEST_V5', candidate), self.assertRaises(source.CorpusError):
                    source.authority('v5')


@unittest.skipUnless(PACKAGE, 'set IM2P_V5_CANDIDATE_PACKAGE for immutable package checks')
class ReviewedV5PackageTest(unittest.TestCase):
    def test_current_package_and_complete_fixed_corpus_are_admitted(self) -> None:
        # Given the real immutable package, when admitted, then all reviewed inputs pass.
        package = Path(PACKAGE or '')
        source.authority('v5', fixture_root=ROOT,
                         llama_root=package / 'dependency/source/llama_cpp_gemmini')
        source.validate_corpus(inputs(package))

    def test_historical_v4_still_rejects_current_package(self) -> None:
        # Given the current package, when historical v4 is selected, then its lock fails.
        with self.assertRaisesRegex(source.CorpusError, 'dependency lock differs'):
            source.authority('v4', llama_root=Path(PACKAGE or '') / 'dependency/source/llama_cpp_gemmini')

    @unittest.skipUnless(os.environ.get('IM2P_V4_CANDIDATE_PACKAGE'), 'historical v4 package required')
    def test_v5_rejects_historical_package(self) -> None:
        # Given the old immutable package, when v5 is selected, then its lock fails.
        old = Path(os.environ['IM2P_V4_CANDIDATE_PACKAGE'])
        with self.assertRaisesRegex(source.CorpusError, 'dependency lock differs'):
            source.authority('v5', llama_root=old / 'dependency/source/llama_cpp_gemmini')

    def test_source_or_package_mutation_is_rejected(self) -> None:
        # Given scoped copies without any Git repository, when a pinned input changes,
        # then the current authority rejects it before results are evaluated.
        reviewed = source.authority('v4')
        producer = reviewed['producer_source_sha256']
        fixtures = reviewed['fixture_source_sha256']
        selectors = reviewed['selector_source_sha256']
        assert isinstance(producer, dict) and isinstance(fixtures, dict) and isinstance(selectors, dict)
        package = Path(PACKAGE or '')
        package_files = ['dependency-lock.json', 'source-manifest.json',
                         'source/sim/cycle/corpus-authority-v3.json',
                         *(f'dependency/source/llama_cpp_gemmini/{name}' for name in producer),
                         *(f'source/{name}' for name in fixtures)]
        targets = [*package_files[:3], package_files[3], f'source/{next(iter(fixtures))}',
                   next(iter(selectors))]
        for name in targets:
            with self.subTest(path=name), tempfile.TemporaryDirectory() as directory:
                candidate = Path(directory)
                for relative in package_files:
                    target = candidate / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(package / relative, target)
                for relative in selectors:
                    target = candidate / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ROOT.parent / relative, target)
                target = candidate / name
                target.write_bytes(target.read_bytes() + b'\n')
                with patch.object(source, 'ROOT', candidate / 'source'), self.assertRaises(source.CorpusError):
                    source.authority('v5', fixture_root=candidate / 'source',
                                     llama_root=candidate / 'dependency/source/llama_cpp_gemmini')

    def test_coordinated_shrink_and_geometry_or_coverage_mutations_are_rejected(self) -> None:
        # Given the independently inherited requests, when results/counts agree on
        # a smaller or changed corpus, then the input authority still rejects it.
        for mutation in ('shrink', 'shape', 'tile', 'timing', 'profile', 'framing', 'duplicate'):
            with self.subTest(mutation=mutation):
                document = copy.deepcopy(inputs(Path(PACKAGE or '')))
                cases = document['cases']
                assert isinstance(cases, list) and isinstance(cases[0], dict)
                first = cases[0]
                if mutation == 'shrink':
                    expected = document['expected_cases']
                    assert isinstance(expected, dict)
                    for identities in expected.values():
                        assert isinstance(identities, list)
                        identities.remove('captured-015')
                    for field in ('captured_corpus_counts', 'observed_corpus_counts'):
                        document[field] = {name: 14 for name in source.PROFILES}
                    document['cases'] = [row for row in cases
                                         if isinstance(row, dict) and row['case'] != 'captured-015']
                if mutation in ('shape', 'tile', 'timing'):
                    values = first[mutation]
                    assert isinstance(values, list) and isinstance(values[0], int)
                    values[0] += 1
                if mutation in ('profile', 'framing'):
                    first[mutation] = 'unreviewed'
                if mutation == 'duplicate':
                    cases.append(copy.deepcopy(first))
                with self.assertRaises(source.CorpusError):
                    source.validate_corpus(document)


@unittest.skipUnless(os.environ.get('IM2P_V5_BUILD_ROOT') and os.environ.get('IM2P_CYCLE_LIBRARY'),
                     'set current host build and library for official CLI checks')
class ReviewedV5CliTest(unittest.TestCase):
    def test_current_build_binding_must_name_the_admitted_package(self) -> None:
        # Given verified build bytes, when their package identity differs from
        # the host result's claim, then v5 rejects that substituted provenance.
        from scripts.gemmini_rtl_build_binding import verify_build
        from sim.tests.cycle.current_rtl_certificate import certify

        def changed_binding(output: Path, profile: str) -> dict[str, JsonValue]:
            binding = verify_build(output, profile)
            binding['llama_source'] = {}
            return binding

        with tempfile.TemporaryDirectory() as directory, \
             patch('scripts.gemmini_rtl_build_binding.verify_build', side_effect=changed_binding), \
             self.assertRaisesRegex(source.CorpusError, 'build package identity differs'):
            certify(Path(os.environ['IM2P_V5_BUILD_ROOT']), Path(os.environ['IM2P_CYCLE_LIBRARY']),
                    Path(directory) / 'output', corpus_revision='v5', preflight=True)

    def test_official_preflight_admits_inputs_without_publishing_certificate(self) -> None:
        # Given the real host package, when explicitly preflighted as v5, then
        # all 240 inputs are recorded without RTL execution or a final certificate.
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'preflight'
            command = [sys.executable, '-B', str(ROOT / 'sim/tests/cycle/current_rtl_certificate.py'),
                       '--build-root', os.environ['IM2P_V5_BUILD_ROOT'],
                       '--library', os.environ['IM2P_CYCLE_LIBRARY'], '--out', str(output),
                       '--corpus-revision', 'v5', '--preflight']
            process = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
            self.assertEqual(process.returncode, 0, process.stderr)
            report = json.loads((output / 'corpus-preflight.json').read_text())
            self.assertEqual((report['status'], report['cases_planned'], report['cases_attempted']),
                             ('PREFLIGHT_ONLY', 240, 0))
            self.assertFalse((output / 'current-certificate.json').exists())


if __name__ == '__main__':
    unittest.main()
