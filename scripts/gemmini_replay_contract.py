from __future__ import annotations

import hashlib
from fnmatch import fnmatchcase
import json
from pathlib import Path
import re
from typing import Final, Mapping

from scripts.gemmini_resolve_profile import JsonValue, ProfileSelection, Scu, resolve_profile
from scripts.real_lib_manifest import sha256, verify_manifest

ROOT: Final = Path(__file__).resolve().parents[1]
SCHEMA: Final = 'im2p-hardware-lowering-contract'
VERSION: Final = 1
CONTRACT_KEY: Final = 'hardware_lowering_contract'
HARDWARE_GLOBS: Final = (
    'src/gemmini/src/main/scala/**/*.scala',
    'src/gemmini/control/src/main/scala/**/*.scala',
    'src/gemmini/upstream/src/main/scala/**/*.scala',
    'src/gemmini/patches/*.patch',
)
HARDWARE_PATHS: Final = (
    'config/gemmini_hp1_profiles.json',
    'sim/include/im2p_geometry.h',
    'sim/include/im2p_sim.h',
    'sim/common/gemmini_schedule.hpp',
    'sim/common/gemmini_schedule.cpp',
    'sim/backends/gemmini_hp1/geometry.cpp',
    'sim/backends/gemmini_hp1/runtime.cpp',
    'src/gemmini/vendor-manifest.json',
)
FACT_KEYS: Final = {
    'activation_bits', 'weight_bits', 'dim', 'packing', 'block_size',
    'accumulator_bits', 'array_partial_bits', 'numerical_revision', 'scu',
    'fragment_limit', 'memory', 'fixed_latencies', 'scale_entries',
    'external_stride_units', 'tile_units', 'lowering_revision',
    'scale_mapping_revision', 'saturation_order',
}


class ContractError(ValueError):
    pass


def canonical_json(document: Mapping[str, JsonValue]) -> str:
    return json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def contract_digest(document: Mapping[str, JsonValue]) -> str:
    return hashlib.sha256(canonical_json({k: v for k, v in document.items() if k != 'sha256'}).encode()).hexdigest()


def _mapping(value: JsonValue, label: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ContractError(f'{label}: JSON object required')
    return value


def _hashes(paths: list[Path], root: Path) -> dict[str, JsonValue]:
    return {p.relative_to(root).as_posix(): sha256(p) for p in sorted(set(paths))}


def hardware_contract(profile: str, root: Path = ROOT) -> dict[str, JsonValue]:
    match = re.fullmatch(r'a(4|8)w(4|8)-d(16|32|64)-hp1', profile)
    if match is None:
        raise ContractError(f'unsupported hardware profile: {profile}')
    selection = ProfileSelection(int(match[1]), int(match[2]), int(match[3]), Scu.HP1_LEFT_SHIFT)
    memory = root / 'config/gemmini_host_memory_contracts' / f'{profile}.json'
    resolved = resolve_profile(selection, root / HARDWARE_PATHS[0], memory).to_document()
    facts = {key: resolved[key] for key in FACT_KEYS if key in resolved}
    facts.update({
        'scale_entries': 256,
        'external_stride_units': {'activation': 'bytes-unpacked-int8', 'weight': 'bytes-unpacked-int8',
                                  'output': 'bytes-signed32', 'scale': 'uint32-carrier-elements'},
        'tile_units': 'DIM-counts',
        'lowering_revision': 'explicit-production-geometry-v1-planner-blocks-v1',
        'scale_mapping_revision': 'loop-local-physical-row-generation-release-v1',
        'saturation_order': 'fragment-local-Sat32-then-signed32-saturating-accumulator',
    })
    paths = [root / p for p in HARDWARE_PATHS] + [memory]
    for pattern in HARDWARE_GLOBS:
        selected = list(root.glob(pattern))
        if not selected:
            raise ContractError(f'hardware contract source closure missing: {pattern}')
        paths.extend(selected)
    top = root / 'src/gemmini/control/src/main/scala/im2p/gemmini/UpstreamWsHp1Top.scala'
    if not re.search(r'scaleEntries:\s*Int\s*=\s*256\b', top.read_text()):
        raise ContractError('scale capacity changed; update explicit hardware revision')
    document: dict[str, JsonValue] = {'schema': SCHEMA, 'version': VERSION, 'profile': profile,
                                    'facts': facts, 'source_sha256': _hashes(paths, root)}
    document['sha256'] = contract_digest(document)
    return document


def validate_contract(document: Mapping[str, JsonValue], profile: str) -> None:
    if set(document) != {'schema', 'version', 'profile', 'facts', 'source_sha256', 'sha256'}:
        raise ContractError('hardware contract fields missing or unknown')
    if document['schema'] != SCHEMA or type(document['version']) is not int or document['version'] != VERSION:
        raise ContractError('unsupported hardware contract schema/version')
    if document['profile'] != profile:
        raise ContractError('hardware contract profile mismatch')
    facts = _mapping(document['facts'], 'hardware facts')
    if set(facts) != FACT_KEYS:
        raise ContractError('hardware contract facts missing or unknown')
    if profile != f'a{facts["activation_bits"]}w{facts["weight_bits"]}-d{facts["dim"]}-hp1':
        raise ContractError('hardware contract profile/DIM mismatch')
    memory = _mapping(facts['memory'], 'hardware memory')
    latency = _mapping(facts['fixed_latencies'], 'hardware latency')
    if not {'bank_count', 'bank_rows', 'accumulator_rows', 'scratchpad_row_bytes',
            'accumulator_row_bytes', 'scratchpad_total_bytes', 'accumulator_total_bytes'} <= memory.keys():
        raise ContractError('resolved memory contract incomplete')
    if set(latency) != {'scratchpad_read_delay', 'accumulator_latency'}:
        raise ContractError('fixed hardware latency contract incomplete')
    hashes = _mapping(document['source_sha256'], 'hardware source hashes')
    required = {*HARDWARE_PATHS, f'config/gemmini_host_memory_contracts/{profile}.json'}
    if not required <= hashes.keys() or not all(
        isinstance(v, str) and re.fullmatch('[0-9a-f]{64}', v) for v in hashes.values()
    ):
        raise ContractError('hardware source hash closure incomplete')
    if not all(any(fnmatchcase(p, pattern) for p in hashes) for pattern in HARDWARE_GLOBS):
        raise ContractError('hardware RTL source closure incomplete')
    if document['sha256'] != contract_digest(document):
        raise ContractError('hardware contract canonical SHA256 mismatch')


def compatible(producer: Mapping[str, JsonValue], model: Mapping[str, JsonValue]) -> None:
    profile = producer.get('profile')
    if not isinstance(profile, str):
        raise ContractError('producer hardware profile missing')
    validate_contract(producer, profile)
    validate_contract(model, profile)
    if producer['sha256'] != model['sha256']:
        raise ContractError('producer/model hardware lowering contract mismatch')


def reference_memory_contract(root: Path = ROOT) -> dict[str, JsonValue]:
    source = root / 'sim/cycle/c_api.cpp'
    match = re.search(r'c->timing\s*=\s*\{([^}]+)\}', source.read_text())
    if match is None:
        raise ContractError('reference memory defaults unavailable')
    fields = ('revision', 'backing_read_delay', 'even_read_id_delay', 'scale_read_extra_delay',
              'backing_write_delay', 'read_ready_period', 'backing_cycle_offset', 'reserved')
    values = [int(v.strip()) for v in match.group(1).split(',')]
    document: dict[str, JsonValue] = {
        'id': 'rtl-regression-reference-memory-v1',
        'scope': 'isolated-work-accounting', 'accepted_cycle': 1,
        'timing': dict(zip(fields, values, strict=True)),
        'source_sha256': _hashes([source, root / 'sim/cycle/timing_profile.hpp'], root),
    }
    document['sha256'] = contract_digest(document)
    return document


def runtime_binding(manifest_path: Path, profile: str) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    valid, reason = verify_manifest(manifest_path, expected_implementation='GEMMINI_HP1',
                                    expected_artifacts=('libim2p_gemmini_frontend.a', 'libim2p_sim.a'))
    if not valid:
        raise ContractError(f'selected runtime manifest invalid: {reason}')
    manifest = _mapping(json.loads(manifest_path.read_text()), 'runtime manifest')
    build = _mapping(manifest['build_config'], 'runtime build configuration')
    encoded = build.get(CONTRACT_KEY)
    if not isinstance(encoded, str):
        raise ContractError('selected runtime lacks build-bound hardware contract; rebuild or verify original inputs')
    contract = _mapping(json.loads(encoded), 'runtime hardware contract')
    validate_contract(contract, profile)
    identity = _mapping(manifest['identity'], 'runtime identity')
    if profile != f'a{identity["activation_bits"]}w{identity["weight_bits"]}-d{identity["dim"]}-hp1':
        raise ContractError('selected runtime/profile mismatch')
    artifact: dict[str, JsonValue] = {
        'manifest_sha256': sha256(manifest_path), 'fingerprint': manifest['fingerprint'],
        'artifacts': manifest['artifacts'], 'hardware_contract_sha256': contract['sha256'],
        'execution_kind': build.get('runtime_execution_kind', 'FRESH_BUILD'),
    }
    return contract, artifact


def verify_runtime_source_proof(path: Path, profile: str, contract: Mapping[str, JsonValue]) -> str:
    proof = _mapping(json.loads(path.read_text()), 'runtime source proof')
    if (proof.get('schema'), proof.get('version'), proof.get('profile')) != (
        'im2p-runtime-source-proof', 1, profile
    ):
        raise ContractError('unsupported runtime source proof schema/version/profile')
    digest = proof.get('archive_sha256')
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ContractError('runtime source proof archive SHA256 missing')
    recorded = _mapping(proof.get('source_sha256'), 'runtime original source hashes')
    paths = [ROOT / p for p in ('sim/Cargo.toml', 'sim/Cargo.lock', 'sim/build.rs')]
    for pattern in ('sim/backends/**/*.cpp', 'sim/backends/**/*.hpp', 'sim/ffi/*.cpp',
                    'sim/ffi/*.h', 'sim/ffi/*.hpp', 'sim/src/**/*.rs', 'sim/include/*.h'):
        paths.extend(ROOT.glob(pattern))
    expected = {**_mapping(contract.get('source_sha256'), 'hardware hashes'), **_hashes(paths, ROOT)}
    missing = [name for name, value in expected.items() if recorded.get(name) != value]
    if missing:
        raise ContractError(f'runtime original source proof differs or omits: {missing}')
    return digest
