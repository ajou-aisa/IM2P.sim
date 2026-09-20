"""Bind official host-test outputs to inputs captured before the build."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Final, Mapping

from scripts.gemmini_replay_contract import ROOT, contract_digest, hardware_contract, validate_contract
from scripts.gemmini_resolve_profile import JsonValue
from scripts.real_lib_manifest import sha256

SCHEMA: Final = 'im2p-rtl-build-binding'
OBJECTS: Final = ('frontend_rtl_fixture.o', 'rmd_rtl_fixture.o', 'bound_rmd_rtl_fixture.o',
                 'rmd-reference.o', 'VIM2PGemminiWSHP1RtlTest__ALL.a', 'verilated.o', 'verilated_threads.o')


class BuildBindingError(ValueError):
    pass


def mapping(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise BuildBindingError('RTL build binding object missing')
    return value


def build_inputs(profile: str) -> dict[str, JsonValue]:
    return {'hardware_contract': hardware_contract(profile),
            'fixture_source_sha256': {p.relative_to(ROOT).as_posix(): sha256(p)
                                     for p in sorted((ROOT / 'fpga/gemmini_hp1/host').glob('*rtl*.cpp'))}}


def seal_build(output: Path, before: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    contract = mapping(before['hardware_contract'])
    profile = contract['profile']
    if not isinstance(profile, str) or before != build_inputs(profile):
        raise BuildBindingError('RTL build inputs changed while commands were running')
    resolved = mapping(json.loads((output / 'resolved-profile.json').read_text()))
    facts = mapping(contract['facts'])
    for key in ('activation_bits', 'weight_bits', 'dim', 'memory', 'fixed_latencies'):
        if resolved.get(key) != facts[key]:
            raise BuildBindingError('RTL build resolved hardware differs from contract: ' + key)
    paths = [*(output / 'rtl').glob('*.sv'),
             *(output / 'rtl-test-obj' / name for name in OBJECTS),
             *(output / 'host-build').glob('*.a'), output / 'resolved-profile.json',
             output / 'resolved-hardware.properties']
    if not any(path.suffix == '.sv' for path in paths):
        raise BuildBindingError('RTL build binding has no emitted RTL')
    result: dict[str, JsonValue] = {'schema': SCHEMA, 'version': 1, 'execution_kind': 'FRESH_BUILD',
        **before, 'artifact_sha256': {p.relative_to(output).as_posix(): sha256(p) for p in sorted(paths)}}
    result['sha256'] = contract_digest(result)
    return result


def validate_binding(binding: Mapping[str, JsonValue], contract: Mapping[str, JsonValue]) -> None:
    if (binding.get('schema') != SCHEMA or type(binding.get('version')) is not int or binding['version'] != 1
            or binding.get('execution_kind') not in ('FRESH_BUILD', 'VERIFIED_REUSE')):
        raise BuildBindingError('RTL build binding schema/execution missing')
    recorded = mapping(binding.get('hardware_contract'))
    profile = contract.get('profile')
    if not isinstance(profile, str):
        raise BuildBindingError('RTL build binding profile missing')
    validate_contract(recorded, profile)
    if recorded != contract:
        raise BuildBindingError('RTL build artifact hardware contract mismatch')
    fixture_hashes = mapping(binding.get('fixture_source_sha256'))
    if not fixture_hashes:
        raise BuildBindingError('RTL build fixture source binding missing')
    if binding['execution_kind'] == 'VERIFIED_REUSE':
        for name in ('verified_source_inventory_sha256', 'verified_probe_provenance_sha256',
                     'verified_build_manifest_sha256', 'verified_evidence_checksums_sha256'):
            value = binding.get(name)
            if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
                raise BuildBindingError('RTL build verified reuse proof missing: ' + name)
    artifacts = mapping(binding.get('artifact_sha256'))
    if not artifacts or not any(name.endswith('.sv') for name in artifacts):
        raise BuildBindingError('RTL build artifact closure missing')
    if not all('rtl-test-obj/' + name in artifacts for name in OBJECTS):
        raise BuildBindingError('RTL build object closure missing')
    for name, digest in artifacts.items():
        if Path(name).is_absolute() or '..' in Path(name).parts or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise BuildBindingError('RTL build artifact path/hash invalid')
    if binding.get('sha256') != contract_digest(binding):
        raise BuildBindingError('RTL build binding digest mismatch')


def verify_build(output: Path, profile: str) -> dict[str, JsonValue]:
    path = output / 'rtl-build-binding.json'
    if not path.is_file():
        raise BuildBindingError('RTL build-time binding missing; fresh official host-test build required')
    binding = mapping(json.loads(path.read_text()))
    validate_binding(binding, hardware_contract(profile))
    if binding.get('fixture_source_sha256') != build_inputs(profile)['fixture_source_sha256']:
        raise BuildBindingError('RTL build fixture source changed')
    for name, digest in mapping(binding['artifact_sha256']).items():
        if sha256(output / name) != digest:
            raise BuildBindingError('RTL build artifact changed: ' + name)
    return binding
