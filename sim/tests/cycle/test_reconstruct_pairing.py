from __future__ import annotations

import copy
import hashlib
import json
import unittest

from sim.cycle.npu_trace_schema import Record, object_value
from sim.cycle.reconstruct_pairing import validate_source_pair


def pair() -> tuple[Record, Record]:
    common: Record = {'version': 2, 'model_sha256': 'a'*64, 'chunk_id': 0, 'decode_calls': 127,
        'input_tokens_sha256': 'b'*64, 'output_tokens_sha256': 'c'*64,
        'recipe_id': 'wikitext2-test-256x128-greedy-seed1234-v1'}
    potal: Record = {**common, 'source_role': 'POTAL_COLLECTION', 'actual_sampler_calls': 128,
        'execution_kind': 'FREE_GENERATION', 'command_arguments': ['--seed','1234','--chunk-index','0'],
        'artifacts': {'application_endpoints': {'sha256': 'd'*64}}}
    full: Record = {**common, 'source_role': 'FULL_CPU', 'actual_sampler_calls': 0,
        'execution_kind': 'FORCED_CPU_COST_ONLY', 'trajectory_source': 'POTAL',
        'command_arguments': ['--seed','1234','--chunk-index','0','--forced-token-ids','sha256:'+'e'*64],
        'paired_trajectory': {'schema': 'potal-paired-trajectory', 'version': 1,
            'execution_kind': 'FORCED_CPU_COST_ONLY', 'trajectory_source': 'POTAL',
            'source_potal_provenance_sha256': 'f'*64, 'source_potal_application_sha256': 'd'*64,
            'token_vector_sha256': 'c'*64, 'input_tokens_sha256': 'b'*64, 'forced_file_sha256': 'e'*64}}
    return full, potal


class ForcedPairingTests(unittest.TestCase):
    def test_current_free_generation_requires_complete_matching_identity(self):
        _, potal = pair()
        full: Record = {**potal, 'source_role': 'FULL_CPU'}
        validate_source_pair(full, potal, 'f'*64)
        for field in ('model_sha256', 'chunk_id', 'input_tokens_sha256', 'output_tokens_sha256', 'recipe_id'):
            missing_full, missing_potal = dict(full), dict(potal)
            del missing_full[field]
            del missing_potal[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_source_pair(missing_full, missing_potal, 'f'*64)

    def test_current_free_generation_rejects_changed_final_output_token(self):
        _, potal = pair()
        full: Record = {**potal, 'source_role': 'FULL_CPU'}
        tokens = list(range(128))
        changed = tokens[:-1] + [999]
        self.assertEqual(tokens[:127], changed[:127])
        full['output_tokens_sha256'] = hashlib.sha256(json.dumps(tokens, separators=(',', ':')).encode()).hexdigest()
        potal['output_tokens_sha256'] = hashlib.sha256(json.dumps(changed, separators=(',', ':')).encode()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'forced.*cost-only recollection'):
            validate_source_pair(full, potal, 'f'*64)

    def test_legacy_pair_keeps_legacy_scope_without_invented_token_digests(self):
        full: Record = {'version': 1, 'command_arguments': ['--seed', '1']}
        potal: Record = {'version': 1, 'command_arguments': ['--seed', '1']}
        validate_source_pair(full, potal, 'f'*64)
        potal['command_arguments'] = ['--seed', '2']
        with self.assertRaises(ValueError):
            validate_source_pair(full, potal, 'f'*64)

    def test_only_bound_forced_trajectory_argument_can_differ(self):
        full,potal = pair()
        validate_source_pair(full,potal,'f'*64)
        for field,value in (('actual_sampler_calls',128),('chunk_id',1),('model_sha256','0'*64),
                            ('output_tokens_sha256','1'*64),('trajectory_source','USER_GUESSED')):
            wrong = copy.deepcopy(full)
            wrong[field] = value
            with self.subTest(field=field),self.assertRaises(ValueError):
                validate_source_pair(wrong,potal,'f'*64)

    def test_changed_potal_binding_or_sampler_arguments_rejected(self):
        full,potal = pair()
        with self.assertRaises(ValueError):
            validate_source_pair(full,potal,'0'*64)
        changed = copy.deepcopy(full)
        object_value(changed['paired_trajectory'])['source_potal_application_sha256'] = '0'*64
        with self.assertRaises(ValueError):
            validate_source_pair(changed,potal,'f'*64)
        full['command_arguments'] = ['--seed','1','--chunk-index','0','--forced-token-ids','sha256:'+'e'*64]
        with self.assertRaises(ValueError):
            validate_source_pair(full,potal,'f'*64)

    def test_legacy_free_generation_keeps_strict_argument_equality(self):
        full,potal = pair()
        full['execution_kind'] = 'FREE_GENERATION'
        with self.assertRaises(ValueError):
            validate_source_pair(full,potal,'f'*64)


if __name__ == '__main__':
    unittest.main()
