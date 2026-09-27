from __future__ import annotations

import copy
import hashlib
import os
import unittest
from pathlib import Path
from typing import assert_never

from scripts.gemmini_replay_contract import contract_digest
from scripts.gemmini_resolve_profile import JsonValue
from scripts.gemmini_rtl_build_binding import verify_build
from sim.cycle.certificate_contract import (
    CertificateError,
    array_value,
    object_value,
    read_document,
    validate_certificate,
)
from sim.cycle.corpus_authority import ROOT, authority_reference

ENVIRONMENT = ('IM2P_V4_BASE_CERTIFICATE', 'IM2P_V5_BUILD_ROOT', 'IM2P_CYCLE_LIBRARY')


def structural_fixture() -> tuple[dict[str, JsonValue], dict[str, JsonValue], Path]:
    document = read_document(Path(os.environ['IM2P_V4_BASE_CERTIFICATE']))
    historical = copy.deepcopy(object_value(document['rtl_build_bindings'], 'historical bindings'))
    host = read_document(Path(os.environ['IM2P_V5_BUILD_ROOT']) / 'result.json')
    profiles = [object_value(row, 'host profile') for row in array_value(host['profiles'], 'profiles')]
    bindings: dict[str, JsonValue] = {}
    for row in profiles:
        name, resolved = row['profile'], row['resolved_profile']
        assert isinstance(name, str) and isinstance(resolved, str)
        bindings[name] = verify_build(Path(resolved).parent, name)
    library = Path(os.environ['IM2P_CYCLE_LIBRARY'])
    document.update(corpus_authority=authority_reference('v5'), llama_source=profiles[0]['llama_source'],
                    model_library_sha256=hashlib.sha256(library.read_bytes()).hexdigest(),
                    rtl_build_bindings=bindings)
    return document, historical, library


@unittest.skipUnless(all(os.environ.get(name) for name in ENVIRONMENT),
                     'historical certificate and current host/library required for consumer mutation fixture')
class CorpusPackageBindingTest(unittest.TestCase):
    def test_matching_source_structural_control_reaches_consumer(self) -> None:
        # Given old case values used only as an in-memory structural fixture,
        # when its six source bindings agree, then this relation is admissible.
        document, _, library = structural_fixture()
        validate_certificate(document, library)

    def test_consumer_rejects_each_profile_with_rehashed_wrong_or_missing_package(self) -> None:
        # Given a structurally admissible in-memory fixture, when any profile's
        # package source is replaced and rehashed, then the real consumer rejects.
        document, historical, library = structural_fixture()
        bindings = object_value(document['rtl_build_bindings'], 'bindings')
        for profile in bindings:
            for mutation in ('historical_binding', 'missing_source', 'wrong_lock', 'dirty_worktree_root'):
                with self.subTest(profile=profile, mutation=mutation):
                    changed = copy.deepcopy(document)
                    changed_bindings = object_value(changed['rtl_build_bindings'], 'bindings')
                    binding = object_value(changed_bindings[profile], 'binding')
                    match mutation:
                        case 'historical_binding':
                            binding = copy.deepcopy(object_value(historical[profile], 'historical binding'))
                        case 'missing_source':
                            del binding['llama_source']
                        case 'wrong_lock':
                            object_value(binding['llama_source'], 'source')['dependency_lock_sha256'] = '0' * 64
                        case 'dirty_worktree_root':
                            object_value(binding['llama_source'], 'source')['root'] = str(ROOT.parent / 'llama.cpp-gemmini')
                        case unreachable:
                            assert_never(unreachable)
                    binding['sha256'] = contract_digest(binding)
                    changed_bindings[profile] = binding
                    with self.assertRaisesRegex(CertificateError, 'RTL build package identity differs'):
                        validate_certificate(changed, library)


if __name__ == '__main__':
    unittest.main()
