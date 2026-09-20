from __future__ import annotations

import csv
import json
from pathlib import Path
import re

from scripts.gemmini_replay_contract import contract_digest, hardware_contract, reference_memory_contract
from sim.cycle.corpus_authority import authority
from sim.tests.cycle.certificate_document import JsonObject, JsonValue, complete_document
from sim.tests.cycle import current_rtl_certificate as current
from sim.tests.cycle import rtl_hardening as rtl
from sim.tests.cycle.production_block_certificate import RESULT_MAP, TIMING_KEYS, first_event_difference


class EvidenceError(ValueError):
    pass


def mapping(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        raise EvidenceError('expected a JSON object')
    return value


def sequence(value: JsonValue) -> list[JsonValue]:
    if not isinstance(value, list):
        raise EvidenceError('expected a JSON array')
    return value


def text(value: JsonValue) -> str:
    if not isinstance(value, str):
        raise EvidenceError('expected a JSON string')
    return value


def integer(value: JsonValue) -> int:
    if type(value) is not int:
        raise EvidenceError('expected a JSON integer')
    return value


def checked_file(path: Path, evidence: Path, hashes: dict[str, str]) -> str:
    relative = path.resolve().relative_to(evidence.resolve()).as_posix()
    expected = hashes.get(relative)
    if expected is None or rtl.sha256(path) != expected:
        raise EvidenceError(f'evidence checksum missing or different: {relative}')
    return path.read_text()


def source_proof(evidence: Path, hashes: dict[str, str]) -> JsonObject:
    inventory = mapping(json.loads(checked_file(evidence / 'final-source-sha256.json', evidence, hashes)))
    wanted: JsonObject = {}
    for profile in rtl.PROFILES:
        wanted.update(mapping(hardware_contract(profile)['source_sha256']))
    wanted.update(mapping(reference_memory_contract()['source_sha256']))
    wanted.update(mapping(authority()['fixture_source_sha256']))
    for pattern in ('sim/cycle/*.cpp', 'sim/cycle/*.hpp', 'sim/common/*.cpp', 'sim/common/*.hpp'):
        wanted.update({p.relative_to(current.ROOT).as_posix(): rtl.sha256(p)
                       for p in current.ROOT.glob(pattern)})
    for name in ('sim/cycle/CMakeLists.txt', 'sim/include/im2p_cycle_model.h'):
        wanted[name] = rtl.sha256(current.ROOT / name)
    vendor_path = 'src/gemmini/vendor-manifest.json'
    vendor = mapping(json.loads((current.ROOT / vendor_path).read_text()))
    if inventory.get('IM2P.sim/' + vendor_path) != rtl.sha256(current.ROOT / vendor_path):
        raise EvidenceError('historical vendor manifest differs')
    patches = {'src/gemmini/' + text(mapping(p)['path']): mapping(p)['sha256']
               for p in sequence(vendor['patches'])}
    mismatches = [name for name, digest in wanted.items()
                  if inventory.get('IM2P.sim/' + name, patches.get(name)) != digest]
    if mismatches:
        raise EvidenceError(f'historical source proof missing or different: {mismatches}')
    return {'checked_sources': len(wanted), 'source_sha256': wanted,
            'inventory_sha256': rtl.sha256(evidence / 'final-source-sha256.json'),
            'patch_hash_authority': vendor_path}


def probe_proof(directory: Path, evidence: Path, hashes: dict[str, str]) -> int:
    provenance = mapping(json.loads(checked_file(directory / 'provenance.json', evidence, hashes)))
    if (provenance['current_fixture_sha256'] != rtl.sha256(current.SOURCE)
            or provenance['schedule_sha256'] != rtl.sha256(current.ROOT / 'sim/common/gemmini_schedule.cpp')):
        raise EvidenceError('retained probe fixture/lowering differs')
    commands = checked_file(directory / 'commands.jsonl', evidence, hashes).splitlines()
    arguments = [text(arg) for arg in sequence(mapping(json.loads(commands[0]))['argv'])]
    objects = mapping(provenance['retained_objects_sha256'])
    for name, digest in objects.items():
        paths = [Path(arg) for arg in arguments if Path(arg).name == name]
        if len(paths) != 1 or rtl.sha256(paths[0]) != digest:
            raise EvidenceError(f'retained RTL object changed or missing: {name}')
    archive = next(Path(arg) for arg in arguments if arg.endswith('VIM2PGemminiWSHP1RtlTest__ALL.a'))
    sv = {path.name: rtl.sha256(path) for path in (archive.parent.parent / 'rtl').glob('*.sv')}
    if sv != provenance['rtl_sha256']:
        raise EvidenceError('retained RTL source artifact differs')
    for name in ('probe.cpp', 'probe'):
        path = directory / name
        if hashes.get(path.relative_to(evidence).as_posix()) != rtl.sha256(path):
            raise EvidenceError(f'retained probe artifact changed: {path}')
    return len(objects) + len(sv) + 2


def reaggregate_case(case: JsonObject, profile: str, framing: str, evidence: Path,
                     hashes: dict[str, str]) -> JsonObject:
    directory = evidence / 'cycle-release-certificate' / profile / framing / text(case['case'])
    log = directory / 'run.log'
    events_path = directory / 'events.csv'
    lines = checked_file(log, evidence, hashes).splitlines()
    summary_line = next((line for line in lines if line.startswith('WS RTL ')), '')
    observed: JsonObject = {key: int(value) for key, value in re.findall(r'\b(\w+)=(\d+)\b', summary_line)}
    rows = list(csv.reader(checked_file(events_path, evidence, hashes).splitlines()))
    captured = [row for row in rows if row and row[0] == 'CASE']
    accepted = [row for row in rows if row and row[0].isdigit() and row[2] == 'work']
    if len(captured) != 1 or not accepted:
        raise EvidenceError(f'raw RTL accepted-work identity incomplete: {directory}')
    raw = captured[0]
    shape, tile, timing = (sequence(case[key]) for key in ('shape', 'tile', 'timing'))
    if (list(map(int, raw[2:5])) != shape or list(map(int, raw[6:9])) != tile
            or list(map(int, raw[9:14])) != timing or bool(int(raw[15])) != case['raw']):
        raise EvidenceError(f'raw RTL work differs from declared corpus: {directory}')
    request = mapping(json.loads(checked_file(directory / 'model-request.json', evidence, hashes)))
    expected_request: JsonObject = {
        'profile': profile, 'timing_profile': 'rtl-regression',
        'request': dict(zip(('m', 'n', 'k', 'tile_i', 'tile_j', 'tile_k'), [*shape, *tile])) | {
            'accepted_cycle': int(accepted[0][1]), 'submission': framing, 'record_events': 1},
        'timing': dict(zip(TIMING_KEYS, [*timing, int(raw[14])])),
    }
    if request != expected_request:
        raise EvidenceError(f'model request includes different work or unsupported inputs: {directory}')
    model = mapping(json.loads(checked_file(directory / 'model-result.json', evidence, hashes)))
    summary = mapping(model['result'])
    if model['profile'] != profile or model['status'] != 'PASS' or model['value_free'] is not True:
        raise EvidenceError(f'model output admission/profile differs: {directory}')
    pairs = {name: field for name, field in RESULT_MAP.items() if name in observed}
    if not set(pairs) >= set(RESULT_MAP) - {'planner_loop_count', 'fragment_count'}:
        raise EvidenceError(f'raw endpoint/counter evidence missing: {directory}')
    differences: JsonObject = {name: {'rtl': observed[name], 'model': summary[field]}
                               for name, field in pairs.items() if observed[name] != summary[field]}
    model_events = rtl.normalized_model_events([mapping(event) for event in sequence(model['events'])])
    selected = sorted((int(row[1]), row[2]) for row in rows
                      if row and row[0].isdigit() and row[2] in rtl.SELECTED_EVENTS)
    scale = current.ownership(events_path, rtl.profile_bits_dim(profile)[1])
    exact = current.exact_result(differences, model_events, selected) and scale['status'] == 'PASS'
    cycles = summary['total_cycles']
    if not isinstance(cycles, int):
        raise EvidenceError('model cycles must be an integer')
    return {**case, 'profile': profile, 'framing': framing, 'rtl_admitted': True,
            'model_attempted': True, 'model_admitted': True, 'status': 'PASS' if exact else 'FAIL',
            'rtl_log': str(log), 'rtl_events': str(events_path),
            'reason': None if exact else 'RETAINED_RTL_MODEL_MISMATCH', 'differences': differences,
            'first_event_difference': first_event_difference(model_events, selected),
            'rtl_summary': observed, 'model_summary': summary, 'delta_cycles': cycles - integer(observed['cycles']),
            'selected_event_multiset_exact': bool(selected) and model_events == selected,
            'event_comparison': dict(current.event_comparison(model_events, selected)),
            'scale_ownership': {key: value for key, value in scale.items() if key != 'details'}}


def reaggregate(evidence: Path, library: Path, out: Path) -> JsonObject:
    if out.is_relative_to(current.ROOT) or out.exists():
        raise EvidenceError('output must be new and outside IM2P.sim')
    out.mkdir(parents=True)
    hashes: dict[str, str] = {}
    for line in (evidence / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        if name in hashes:
            raise EvidenceError(f'duplicate historical checksum: {name}')
        hashes[name] = digest
    previous = mapping(json.loads(checked_file(evidence / 'cycle-release-certificate/current-certificate.json', evidence, hashes)))
    if previous['model_library_sha256'] != rtl.sha256(library):
        raise EvidenceError('reaggregation requires the identical previously certified model library')
    proof = source_proof(evidence, hashes)
    corpora: dict[str, list[JsonObject]] = {}
    bindings: JsonObject = {}
    artifact_count = 0
    for profile in rtl.PROFILES:
        root = evidence / 'cycle-release-certificate' / profile
        captured = sequence(json.loads(checked_file(root / 'captured-corpus.json', evidence, hashes)))
        corpora[profile] = [mapping(case) for case in captured]
        corpora[profile].extend({'case': f'large-k-{k}', 'shape': [1, 1, k], 'tile': [1, 1, 3],
                                'timing': [3, 13, 17, 11, 5], 'raw': False,
                                'provenance': 'synthetic_large_k'} for k in (32, 64, 96, 3072, 8256))
        for framing in current.FRAMINGS:
            artifact_count += probe_proof(root / framing, evidence, hashes)
        provenance_path = root / 'planner-blocks/provenance.json'
        provenance = mapping(json.loads(checked_file(provenance_path, evidence, hashes)))
        command = mapping(json.loads(checked_file(root / 'planner-blocks/commands.jsonl', evidence, hashes).splitlines()[0]))
        archive = next(Path(text(arg)) for arg in sequence(command['argv'])
                       if text(arg).endswith('VIM2PGemminiWSHP1RtlTest__ALL.a'))
        build_manifest = archive.parent.parent.parent / 'result.json'
        if rtl.sha256(build_manifest) != previous['build_manifest_sha256']:
            raise EvidenceError('original RTL build manifest hash differs')
        artifacts = {'rtl-test-obj/' + name: digest for name, digest in mapping(provenance['retained_objects_sha256']).items()}
        artifacts.update({'rtl/' + name: digest for name, digest in mapping(provenance['rtl_sha256']).items()})
        binding: JsonObject = {'schema': 'im2p-rtl-build-binding', 'version': 1, 'execution_kind': 'VERIFIED_REUSE',
            'hardware_contract': hardware_contract(profile), 'artifact_sha256': artifacts,
            'fixture_source_sha256': authority()['fixture_source_sha256'],
            'verified_source_inventory_sha256': proof['inventory_sha256'],
            'verified_probe_provenance_sha256': rtl.sha256(provenance_path),
            'verified_build_manifest_sha256': rtl.sha256(build_manifest),
            'verified_evidence_checksums_sha256': rtl.sha256(evidence / 'SHA256SUMS')}
        binding['sha256'] = contract_digest(binding)
        bindings[profile] = binding
    expected = {name: [text(case['case']) for case in cases] for name, cases in corpora.items()}
    current.write_json(out / 'expected-corpus.json', expected)
    results = [reaggregate_case(case, profile, framing, evidence, hashes)
               for profile, cases in corpora.items() for framing in current.FRAMINGS for case in cases]
    mutation = current.mutation_test(results[0])
    summaries: JsonObject = {}
    for framing in current.FRAMINGS:
        subset = [case for case in results if case['framing'] == framing]
        summaries[framing] = {'cases_attempted': len(subset),
            'cases_rtl_admitted': sum(case['rtl_admitted'] is True for case in subset),
            'cases_model_admitted': sum(case['model_admitted'] is True for case in subset),
            'cases_exact': sum(case['status'] == 'PASS' for case in subset),
            'max_abs_delta_cycles': max(abs(integer(case['delta_cycles'])) for case in subset)}
    mismatch = next((case for case in results if case['status'] != 'PASS'), None)
    document: JsonObject = {
        'status': 'PASS' if mismatch is None and mutation['status'] == 'PASS' else 'FAIL',
        'cases': list(results), 'summaries': summaries, 'first_mismatch': mismatch,
        'selected_events': [event for event in sorted(rtl.SELECTED_EVENTS)], 'event_mutation': mutation,
        'model_library_sha256': rtl.sha256(library), 'build_manifest_sha256': previous['build_manifest_sha256'],
        'rtl_build_bindings': bindings,
        'hardware_contracts': {name: mapping(binding)['hardware_contract'] for name, binding in bindings.items()},
        'captured_corpus_counts': {name: len(cases) - 5 for name, cases in corpora.items()},
        'historical_goldens_used': False, 'historical_exclusions_used': False,
        'reuse_proof': {'evidence_root': str(evidence), 'checksums_sha256': rtl.sha256(evidence / 'SHA256SUMS'),
                       'source_proof': proof, 'raw_cases_reaggregated': len(results),
                       'retained_artifact_checks': artifact_count,
                       'same_model_library': True, 'fresh_rtl_execution': False},
    }
    result = complete_document(document, expected, library, 'REAGGREGATED_FROM_VERIFIED_EVIDENCE')
    current.write_json(out / 'current-certificate.json', result)
    current.write_json(out / 'event-hard-gate-mutation.json', mutation)
    return result
