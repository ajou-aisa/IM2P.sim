from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import unittest

from sim.cycle.certificate_contract import validate_certificate
from sim.tests.cycle.certificate_document import complete_document
from scripts.gemmini_replay_contract import contract_digest


@unittest.skipUnless(os.environ.get('IM2P_CYCLE_CERTIFICATE'), 'real certificate required')
class CertificateAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = json.loads(Path(os.environ['IM2P_CYCLE_CERTIFICATE']).read_text())
        self.library = Path(os.environ['IM2P_CYCLE_LIBRARY'])

    def test_coordinated_corpus_shrink_is_rejected(self) -> None:
        validate_certificate(self.document, self.library)
        shortened = copy.deepcopy(self.document)
        for profile, identities in shortened['expected_cases'].items():
            count = shortened['captured_corpus_counts'][profile]
            identities.remove(f'captured-{count:03}')
            shortened['captured_corpus_counts'][profile] -= 1
        shortened['cases'] = [row for row in shortened['cases']
                              if row['case'] in shortened['expected_cases'][row['profile']]]
        for framing, summary in shortened['summaries'].items():
            count = sum(row['framing'] == framing for row in shortened['cases'])
            for key in ('cases_attempted', 'cases_rtl_admitted', 'cases_model_admitted', 'cases_exact'):
                summary[key] = count
        if shortened['execution_kind'] == 'REAGGREGATED_FROM_VERIFIED_EVIDENCE':
            shortened['reuse_proof']['raw_cases_reaggregated'] = len(shortened['cases'])
        with self.assertRaisesRegex(ValueError, 'independent corpus'):
            validate_certificate(shortened, self.library)

    def test_fresh_document_without_build_time_binding_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        document.pop('rtl_build_bindings', None)
        with self.assertRaisesRegex(ValueError, 'build.*binding'):
            complete_document(document, document['expected_cases'], self.library, 'FRESH_RUN')

    def test_independent_corpus_rejects_changed_inputs_and_digest(self) -> None:
        for field in ('shape', 'tile', 'timing', 'raw', 'corpus_authority'):
            with self.subTest(field=field):
                document = copy.deepcopy(self.document)
                if field == 'corpus_authority':
                    document[field]['sha256'] = '0' * 64
                elif field == 'raw':
                    document['cases'][0][field] = not document['cases'][0][field]
                else:
                    document['cases'][0][field][0] += 1
                with self.assertRaisesRegex(ValueError, 'independent corpus'):
                    validate_certificate(document, self.library)

    def test_stale_artifact_contract_cannot_be_restamped(self) -> None:
        document = copy.deepcopy(self.document)
        profile = document['profiles'][0]
        changed = document['hardware_contracts'][profile]
        changed['facts']['scale_mapping_revision'] = 'stale-global-physical-row-v0'
        changed['sha256'] = contract_digest(changed)
        with self.assertRaisesRegex(ValueError, 'build.*contract|build.*binding'):
            complete_document(document, document['expected_cases'], self.library, document['execution_kind'])


if __name__ == '__main__':
    unittest.main()
