from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.gemmini_replay_contract import (
    CONTRACT_KEY, ContractError, canonical_json, compatible, contract_digest,
    hardware_contract, reference_memory_contract, runtime_binding,
    validate_contract, verify_runtime_source_proof, _mapping,
)
from scripts.im2p_config import profile_config
from scripts.real_lib_manifest import SCHEMA, artifact_rows


class ReplayContractTest(unittest.TestCase):
    def test_six_profiles_have_deterministic_distinct_contracts(self):
        hashes = set()
        for bits in (4, 8):
            for dim in (16, 32, 64):
                profile = f'a{bits}w{bits}-d{dim}-hp1'
                contract = hardware_contract(profile)
                validate_contract(contract, profile)
                self.assertEqual(contract, hardware_contract(profile))
                hashes.add(contract['sha256'])
        self.assertEqual(len(hashes), 6)

    def test_individually_valid_contracts_reject_different_scale_or_memory(self):
        producer = hardware_contract('a8w8-d16-hp1')
        for key, value in (('scale_mapping_revision', 'global-row-v0'),
                           ('lowering_revision', 'different-lowering-v2')):
            model = deepcopy(producer)
            _mapping(model['facts'], 'facts')[key] = value
            model['sha256'] = contract_digest(model)
            validate_contract(model, 'a8w8-d16-hp1')
            with self.assertRaisesRegex(ContractError, 'producer/model'):
                compatible(producer, model)
        model = deepcopy(producer)
        _mapping(_mapping(model['facts'], 'facts')['memory'], 'memory')['bank_rows'] = 8192
        model['sha256'] = contract_digest(model)
        validate_contract(model, 'a8w8-d16-hp1')
        with self.assertRaisesRegex(ContractError, 'producer/model'):
            compatible(producer, model)

    def test_profile_dim_missing_fields_and_bad_hash_rejected(self):
        producer = hardware_contract('a8w8-d16-hp1')
        with self.assertRaisesRegex(ContractError, 'profile'):
            compatible(producer, hardware_contract('a8w8-d32-hp1'))
        for field in ('facts', 'source_sha256', 'sha256'):
            broken = deepcopy(producer)
            del broken[field]
            with self.assertRaises(ContractError):
                validate_contract(broken, 'a8w8-d16-hp1')
        broken = deepcopy(producer)
        _mapping(broken['facts'], 'facts')['dim'] = 32
        broken['sha256'] = contract_digest(broken)
        with self.assertRaisesRegex(ContractError, 'DIM'):
            validate_contract(broken, 'a8w8-d16-hp1')
        broken = deepcopy(producer)
        broken['sha256'] = '0' * 64
        with self.assertRaisesRegex(ContractError, 'SHA256'):
            validate_contract(broken, 'a8w8-d16-hp1')

    def test_reference_memory_separate_from_hardware(self):
        reference = reference_memory_contract()
        self.assertEqual(_mapping(reference['timing'], 'timing')['backing_read_delay'], 3)
        self.assertEqual(reference['accepted_cycle'], 1)
        self.assertNotIn('max_cycles', canonical_json(reference))
        self.assertNotIn('timing', _mapping(hardware_contract('a8w8-d16-hp1')['facts'], 'facts'))

    def test_runtime_manifest_binds_actual_archives_and_rejects_old_manifest(self):
        profile = 'a8w8-d16-hp1'
        contract = hardware_contract(profile)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = ('libim2p_gemmini_frontend.a', 'libim2p_sim.a')
            for name in names:
                (root / name).write_bytes(name.encode())
            manifest = {
                'schema': SCHEMA, 'fingerprint': '1' * 64,
                'identity': {**profile_config(8, 8, 16), 'id': 'a8-w8-d16',
                             'implementation': 'GEMMINI_HP1', 'block_size': 32,
                             'platform': platform.system(), 'platform_release': platform.release(),
                             'arch': platform.machine()},
                'toolchains': {}, 'artifact_root': '.',
                'artifacts': artifact_rows(root, tuple(Path(p) for p in names)),
                'build_config': {CONTRACT_KEY: canonical_json(contract)},
            }
            path = root / 'real-lib.json'
            path.write_text(json.dumps(manifest))
            actual, binding = runtime_binding(path, profile)
            self.assertEqual(actual, contract)
            self.assertEqual(binding['manifest_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            (root / names[1]).write_bytes(b'wrong-runtime')
            with self.assertRaisesRegex(ContractError, 'manifest invalid'):
                runtime_binding(path, profile)
            (root / names[1]).write_bytes(names[1].encode())
            manifest['build_config'] = {}
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ContractError, 'lacks build-bound'):
                runtime_binding(path, profile)

    def test_reuse_proof_without_original_inputs_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'proof.json'
            path.write_text(json.dumps({'schema': 'im2p-runtime-source-proof', 'version': 1,
                'profile': 'a8w8-d16-hp1', 'archive_sha256': '1' * 64, 'source_sha256': {}}))
            with self.assertRaisesRegex(ContractError, 'differs or omits'):
                verify_runtime_source_proof(path, 'a8w8-d16-hp1', hardware_contract('a8w8-d16-hp1'))


if __name__ == '__main__':
    unittest.main()
