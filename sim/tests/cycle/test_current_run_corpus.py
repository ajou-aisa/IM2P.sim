"""Current authority boundary tests; synthetic documents are unit-only."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from sim.cycle import current_run_corpus as current
from sim.cycle.certificate_contract import read_document
from sim.cycle.current_run_capture import (
    EXECUTABLE,
    PRODUCER,
    PRODUCER_SHA256,
    check_hashes,
    reference,
    validate_producer,
)
from sim.cycle.npu_trace_schema import Record
from sim.tests.cycle import production_run_work as work


class CurrentRunCorpusTest(unittest.TestCase):
    def test_current_selection_unit_seam_retains_exact_historical_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / 'capture.json'
            capture.write_text('{}')
            path = Path(directory) / 'corpus.json'
            with patch.object(current, 'validate_capture', return_value={}):
                current.issue_current_corpus(capture, path)
                selected = current.load_selected_manifest(path)
            self.assertEqual(selected['case_count'], 42)
            self.assertEqual(selected['producer_source_sha256'], PRODUCER_SHA256)
            self.assertEqual(selected['cases'], read_document(work.PRODUCTION_MANIFEST)['cases'])

    def test_historical_default_rejects_changed_producer(self) -> None:
        # Given current producer bytes differ from the historical source pin.
        old = read_document(work.PRODUCTION_MANIFEST)
        self.assertEqual(sha256(work.PRODUCTION_MANIFEST.read_bytes()).hexdigest(),
                         work.PRODUCTION_MANIFEST_SHA256)
        self.assertNotEqual(old['producer_source_sha256'],
                            sha256((work.WORKSPACE / str(old['producer_source'])).read_bytes()).hexdigest())
        # When the historical default is selected, then it still fails closed.
        with self.assertRaisesRegex(ValueError, 'production corpus incomplete'):
            work.load_manifest()

    def test_current_selector_rejects_forged_or_incomplete_authority(self) -> None:
        # Given unit-only forged current documents with no native receipt.
        original = read_document(work.PRODUCTION_MANIFEST)
        for changed in ({}, {**original, 'schema_version': 2},
                        {**original, 'schema_version': 2, 'case_count': 41},
                        {**original, 'schema_version': 2, 'profiles': []}):
            with self.subTest(fields=sorted(changed)), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'current.json'
                path.write_text(json.dumps(changed))
                # When explicitly selected, then malformed provenance is rejected.
                with self.assertRaises(ValueError):
                    current.load_selected_manifest(path)

    def test_current_selector_does_not_promote_historical_manifest(self) -> None:
        # Given the unchanged historical authority, when used as current input,
        # then an explicit current selection cannot bypass its source gate.
        with self.assertRaises(ValueError):
            current.load_selected_manifest(work.PRODUCTION_MANIFEST)

    def test_rehashed_current_mutations_fail_before_native_admission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / 'capture.json'
            capture.write_text('{}')
            with patch.object(current, 'validate_capture', return_value={}):
                original = current.current_document(capture)
            mutations = [
                {**original, 'cases': []}, {**original, 'case_count': 41},
                {**original, 'profiles': ['a8w8-d16-hp1']},
                {**original, 'producer_source_sha256': '0' * 64},
                {**original, 'artifact_role': 'CPU_FUNCTIONAL'},
                {**original, 'capture_index': {'path': str(capture), 'sha256': '0' * 64}},
            ]
            for index, document in enumerate(mutations):
                with self.subTest(index=index):
                    path = Path(directory) / f'changed-{index}.json'
                    path.write_text(json.dumps(document))
                    with self.assertRaises(ValueError):
                        current.load_selected_manifest(path)

    def test_capture_fixed_domain_rejects_shrink_duplicates_and_fake_role(self) -> None:
        source: Record = {
            'schema': 'im2p-production-run-aware-capture', 'version': 1,
            'artifact_role': 'PRODUCTION_NATIVE_GEMMINI_HP1',
            'expected_case_names': list(work.PRODUCTION_CASE_NAMES),
            'profiles': list(current.PROFILES), 'case_count': 42,
            'producers': [{'profile': name} for name in current.PROFILES],
            'cases': [{'profile': profile, 'case': name} for profile in current.PROFILES
                      for name in work.PRODUCTION_CASE_NAMES]}
        cases = source['cases']
        assert isinstance(cases, list)
        mutations = [{**source, 'cases': cases[:-1]}, {**source, 'cases': cases[:-1] + [cases[0]]},
                     {**source, 'producers': []}, {**source, 'artifact_role': 'CPU_FUNCTIONAL'}]
        with tempfile.TemporaryDirectory() as directory:
            for index, document in enumerate(mutations):
                path = Path(directory) / f'capture-{index}.json'
                path.write_text(json.dumps(document))
                with self.subTest(index=index), self.assertRaises(ValueError):
                    current.validate_capture(path)

    def test_native_artifact_and_dependency_hashes_reject_stale_and_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'producer'
            path.write_bytes(b'unit-only executable identity')
            recorded: Record = {'path': str(path), 'sha256': sha256(path.read_bytes()).hexdigest()}
            path.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'artifact changed'):
                reference(recorded)
            with self.assertRaisesRegex(ValueError, 'closure incomplete'):
                check_hashes({}, {path}, 'unit-only dependency')
            with self.assertRaisesRegex(ValueError, 'changed'):
                check_hashes({str(path): '0' * 64}, {path}, 'unit-only source')

    def test_issuer_never_overwrites_or_publishes_failed_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'bad.json'
            source.write_text('{}')
            output = Path(directory) / 'current.json'
            with self.assertRaises(ValueError):
                current.issue_current_corpus(source, output)
            self.assertFalse(output.exists())
            output.write_bytes(b'preserve')
            with self.assertRaisesRegex(ValueError, 'already exists'):
                current.issue_current_corpus(source, output)
            self.assertEqual(output.read_bytes(), b'preserve')

    def test_native_role_rejects_cpu_functional_substitution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory).resolve()
            binary = build / 'bin' / EXECUTABLE
            binary.parent.mkdir()
            binary.write_bytes(b'unit-only identity, never executed')
            binary.chmod(0o700)
            cache = {'GGML_GEMMINI': 'ON', 'LLAMA_BUILD_TESTS': 'ON',
                     'GGML_GEMMINI_EXECUTION_BACKEND': 'IM2P_SIM',
                     'IM2P_SIM_IMPLEMENTATION': 'CPU_FUNCTIONAL',
                     'GGML_GEMMINI_ACTIVATION_BITS': '8', 'GGML_GEMMINI_WEIGHT_BITS': '8',
                     'GGML_GEMMINI_DIM': '16', 'GGML_GEMMINI_BLOCK_SIZE': '32',
                     'GGML_GEMMINI_OPTION': 'WS', 'GGML_GEMMINI_ENABLE_RMD': 'ON',
                     'CYCLE_SIM': '0', 'CMAKE_HOME_DIRECTORY': str(work.WORKSPACE / 'llama.cpp-gemmini')}
            row: Record = {'profile': 'a8w8-d16-hp1', 'build_root': str(build),
                           'executable': {'path': str(binary), 'sha256': sha256(binary.read_bytes()).hexdigest()}}
            (build / 'CMakeCache.txt').write_text('\n'.join(f'{key}:STRING={value}' for key, value in cache.items()))
            with self.assertRaisesRegex(ValueError, 'profile/role differs'):
                validate_producer(row)
            cache['IM2P_SIM_IMPLEMENTATION'] = 'GEMMINI_HP1'
            (build / 'CMakeCache.txt').write_text('\n'.join(f'{key}:STRING={value}' for key, value in cache.items()))
            (build / 'compile_commands.json').write_text(json.dumps([
                {'file': str(work.WORKSPACE / PRODUCER), 'command': 'c++ -DCYCLE_SIM=1'}]))
            with self.assertRaisesRegex(ValueError, 'compiled role differs'):
                validate_producer(row)

    def test_missing_capture_cli_fails_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'current.json'
            result = subprocess.run([sys.executable, '-B', '-m', 'sim.cycle.current_run_corpus',
                                     '--capture-index', str(Path(directory) / 'missing.json'),
                                     '--out', str(output)], cwd=work.ROOT,
                                    capture_output=True, text=True, timeout=10, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
