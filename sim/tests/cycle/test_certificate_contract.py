from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from sim.tests.cycle.test_optrace import records
from sim.cycle import optrace
from sim.cycle.certificate_contract import read_document, validate_certificate
from scripts.gemmini_replay_contract import contract_digest


class CertificateTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('IM2P_CYCLE_LIBRARY'), 'actual C library required')
    def test_cli_rejects_marker_only_certificate(self) -> None:
        library = Path(os.environ['IM2P_CYCLE_LIBRARY'])
        rows = records()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'trace.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
            (root/'sources.json').write_text(json.dumps({key:rows[0][key] for key in
                                                       ('source_commits','source_worktree_sha256','profile')}))
            (root/'certificate.json').write_text(json.dumps({
                'status':'PASS', 'marker':'IM2P_SINGLE_GEMM_CYCLE_MODEL_CURRENT',
                'model_library_sha256':hashlib.sha256(library.read_bytes()).hexdigest()}))
            result = subprocess.run([sys.executable, '-B', str(ROOT/'sim/cycle/optrace.py'),
                                     str(root/'trace.jsonl'), '--library', str(library),
                                     '--sources', str(root/'sources.json'), '--cycle-certificate',
                                     str(root/'certificate.json'), '--output', str(root/'result.json')],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn('certificate', result.stderr.lower())


@unittest.skipUnless(all(os.environ.get(key) for key in (
    'IM2P_CYCLE_LIBRARY', 'IM2P_CYCLE_CERTIFICATE', 'IM2P_OPTRACE_TRACE',
    'IM2P_OPTRACE_PRODUCER')), 'official generated certificate and host trace required')
class OfficialReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.library = Path(os.environ['IM2P_CYCLE_LIBRARY'])
        cls.certificate = Path(os.environ['IM2P_CYCLE_CERTIFICATE'])
        cls.trace = Path(os.environ['IM2P_OPTRACE_TRACE'])
        cls.sources = Path(os.environ['IM2P_OPTRACE_PRODUCER'])
        cls.document = json.loads(cls.certificate.read_text())
        cls.producer = json.loads(cls.sources.read_text())

    def cli(self, artifacts: optrace.ReplayArtifacts, output: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, '-B', str(ROOT/'sim/cycle/optrace.py'), str(self.trace),
                               '--library', str(artifacts.library), '--sources', str(artifacts.sources),
                               '--cycle-certificate', str(artifacts.certificate), '--output', str(output)],
                              capture_output=True, text=True)

    def test_untouched_generator_output_roundtrips_both_entries(self) -> None:
        before = hashlib.sha256(self.certificate.read_bytes()).hexdigest()
        artifacts = optrace.ReplayArtifacts(self.library, self.sources, self.certificate)
        direct = optrace.replay(self.trace, artifacts)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'result.json'
            process = self.cli(artifacts, output)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(output.read_text()), direct)
        self.assertEqual(direct['status'], 'PASS')
        self.assertEqual(direct.get('validation_scope'), 'CURRENT_CERTIFIED')
        self.assertEqual(hashlib.sha256(self.certificate.read_bytes()).hexdigest(), before)

    def test_incomplete_certificates_fail_cli_and_callable(self) -> None:
        changes = {}
        for name in ('marker_only','empty','profile_missing','profile_duplicate','framing_missing',
                     'framing_duplicate','case_missing','case_duplicate','expected_duplicate',
                     'count','event_mismatch','event_digest','empty_events','library_hash',
                     'contracts_missing','reference_missing','mutation_missing','first_mismatch','version'):
            changes[name] = copy.deepcopy(self.document)
        changes['marker_only'] = {key:self.document[key] for key in
                                  ('status','marker','model_library_sha256')}
        changes['empty']['cases'] = []
        changes['profile_missing']['profiles'].pop()
        changes['profile_duplicate']['profiles'].append(changes['profile_duplicate']['profiles'][0])
        changes['framing_missing']['framings'].pop()
        changes['framing_duplicate']['framings'].append('planner-blocks')
        changes['case_missing']['cases'].pop()
        changes['case_duplicate']['cases'].append(copy.deepcopy(changes['case_duplicate']['cases'][0]))
        first_profile = changes['expected_duplicate']['profiles'][0]
        changes['expected_duplicate']['expected_cases'][first_profile].append(
            changes['expected_duplicate']['expected_cases'][first_profile][0])
        changes['count']['summaries']['planner-blocks']['cases_exact'] -= 1
        changes['event_mismatch']['cases'][0]['selected_event_multiset_exact'] = False
        changes['event_digest']['cases'][0]['event_comparison']['model_multiset_sha256'] = '0'*64
        changes['empty_events']['cases'][0]['event_comparison'].update(model_event_count=0,rtl_event_count=0)
        changes['library_hash']['model_library_sha256'] = '0'*64
        del changes['contracts_missing']['hardware_contracts']
        del changes['reference_missing']['reference_memory']
        del changes['mutation_missing']['event_mutation']
        changes['first_mismatch']['first_mismatch'] = {'case':'failure'}
        changes['version']['version'] = True
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, document in changes.items():
                with self.subTest(case=name):
                    certificate = root/(name+'.json')
                    certificate.write_text(json.dumps(document))
                    artifacts = optrace.ReplayArtifacts(self.library, self.sources, certificate)
                    with self.assertRaises(ValueError):
                        optrace.replay(self.trace, artifacts)
                    process = self.cli(artifacts, root/(name+'-result.json'))
                    self.assertNotEqual(process.returncode, 0, process.stdout)
                    self.assertIn('certificate', process.stderr.lower())

    def test_valid_pairs_with_different_hardware_contract_fail(self) -> None:
        for field in ('scale_mapping_revision','memory'):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                model = copy.deepcopy(self.document)
                profile = self.producer['profile']
                contract = model['hardware_contracts'][profile]
                if field == 'memory':
                    for key in ('bank_rows', 'scratchpad_total_bytes', 'ws_scratchpad_rows_per_buffer'):
                        contract['facts']['memory'][key] *= 2
                else:
                    contract['facts'][field] = 'global-fragment-address-v0'
                contract['sha256'] = contract_digest(contract)
                binding = model['rtl_build_bindings'][profile]
                binding['hardware_contract'] = copy.deepcopy(contract)
                binding['sha256'] = contract_digest(binding)
                validate_certificate(model, self.library)
                root = Path(directory)
                certificate = root/'model-B.json'
                certificate.write_text(json.dumps(model))
                artifacts = optrace.ReplayArtifacts(self.library, self.sources, certificate)
                with self.assertRaisesRegex(ValueError, 'producer/model hardware lowering contract mismatch'):
                    optrace.replay(self.trace, artifacts)
                process = self.cli(artifacts, root/'result.json')
                self.assertNotEqual(process.returncode, 0)
                self.assertIn('producer/model hardware lowering contract mismatch', process.stderr)

    def test_smaller_denominator_cannot_redefine_complete_corpus(self) -> None:
        shortened = copy.deepcopy(self.document)
        shortened['expected_cases'] = {p: ids[:1] for p, ids in shortened['expected_cases'].items()}
        shortened['cases'] = [row for row in shortened['cases']
                              if row['case'] in shortened['expected_cases'][row['profile']]]
        for framing, summary in shortened['summaries'].items():
            count = sum(row['framing'] == framing for row in shortened['cases'])
            for key in ('cases_attempted', 'cases_rtl_admitted', 'cases_model_admitted', 'cases_exact'):
                summary[key] = count
        bad_reuse = copy.deepcopy(self.document)
        bad_reuse['reuse_proof']['raw_cases_reaggregated'] -= 1
        for name, document in (('shortened', shortened), ('reuse-count', bad_reuse)):
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                certificate = root/'certificate.json'
                certificate.write_text(json.dumps(document))
                artifacts = optrace.ReplayArtifacts(self.library, self.sources, certificate)
                with self.assertRaisesRegex(ValueError, 'corpus|raw case'):
                    optrace.replay(self.trace, artifacts)
                self.assertNotEqual(self.cli(artifacts, root/'result.json').returncode, 0)

    def test_source_profile_and_runtime_bindings_fail_closed(self) -> None:
        changes = {}
        for name in ('source','profile','dim','runtime_manifest','runtime_contract','runtime_missing'):
            changes[name] = copy.deepcopy(self.producer)
        changes['source']['source_commits']['IM2P.sim'] = '0'*40
        changes['profile']['profile'] = 'a4w4-d16-hp1'
        changes['dim']['hardware_contract']['facts']['dim'] = 32
        changes['dim']['hardware_contract']['sha256'] = contract_digest(changes['dim']['hardware_contract'])
        changes['runtime_manifest']['runtime_artifact']['manifest_sha256'] = '0'*64
        changes['runtime_contract']['runtime_artifact']['hardware_contract_sha256'] = '0'*64
        del changes['runtime_missing']['runtime_artifact']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, source in changes.items():
                with self.subTest(case=name):
                    path = root/(name+'.json')
                    path.write_text(json.dumps(source))
                    artifacts = optrace.ReplayArtifacts(self.library, path, self.certificate)
                    with self.assertRaises(ValueError):
                        optrace.replay(self.trace, artifacts)
                    process = self.cli(artifacts, root/(name+'-result.json'))
                    self.assertNotEqual(process.returncode, 0, process.stdout)

    def test_legacy_trace_requires_recollection(self) -> None:
        rows = [json.loads(line) for line in self.trace.read_text().splitlines()]
        rows[0]['version'] = 1
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory)/'legacy.jsonl'
            trace.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            artifacts = optrace.ReplayArtifacts(self.library, self.sources, self.certificate)
            with self.assertRaisesRegex(ValueError, 'recollect.*trace v2'):
                optrace.replay(trace, artifacts)


if __name__ == '__main__':
    unittest.main()
