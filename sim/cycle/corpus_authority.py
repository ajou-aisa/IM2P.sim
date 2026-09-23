"""Reviewed corpus inputs, independent of certificate outcomes and counts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Final, Mapping

from scripts.gemmini_resolve_profile import JsonValue

ROOT: Final = Path(__file__).resolve().parents[2]
MANIFEST: Final = Path(__file__).with_name('corpus-authority-v1.json')
MANIFEST_V2: Final = Path(__file__).with_name('corpus-authority-v2.json')
MANIFEST_V2_SHA256: Final = 'e6f4f482d51887b3d29260e5a8a5d3986ed8a5131881725810e34c5f14b45b4a'
MANIFEST_V3: Final = Path(__file__).with_name('corpus-authority-v3.json')
MANIFEST_V3_SHA256: Final = '7add3fd188167e11e047f36647e0a28ea567ef8a7dcc3f6c28971c58bac5bb18'
MANIFEST_V4: Final = Path(__file__).with_name('corpus-authority-v4.json')
MANIFEST_V4_SHA256: Final = '1d260ac7ff24ead5796fba52f9b47b7fe7f983dfed2650b706c67bb775b10dbf'
LARGE_K: Final = (32, 64, 96, 3072, 8256)
CASE_FIELDS: Final = ('case', 'shape', 'tile', 'timing', 'raw', 'provenance')
PROFILES: Final = tuple(f'a{bits}w{bits}-d{dim}-hp1' for bits in (4, 8) for dim in (16, 32, 64))
GROUPS: Final = ('full_block0', 'full_block1', 'stripe_block0', 'stripe_block1')
SOURCE_ROWS: Final = {'full_block0': (60, 33), 'full_block1': (15, 15),
                      'stripe_block0': (20, 11), 'stripe_block1': (5, 5)}
HISTORICAL_ROWS: Final = {'full_block0': (81, 45), 'full_block1': (27, 27),
                          'stripe_block0': (27, 15), 'stripe_block1': (9, 9)}


class CorpusError(ValueError):
    pass


def _read_manifest(path: Path) -> dict[str, JsonValue]:
    def unique(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {}
        for key, value in pairs:
            if key in result:
                raise CorpusError('independent corpus duplicate JSON key: ' + key)
            result[key] = value
        return result

    document = json.loads(path.read_text(), object_pairs_hook=unique)
    if not isinstance(document, dict):
        raise CorpusError('independent corpus manifest object missing')
    return document


def _hash_sources(hashes: JsonValue, root: Path, label: str) -> None:
    if not isinstance(hashes, dict) or not hashes:
        raise CorpusError('independent corpus ' + label + ' authority missing')
    for name, digest in hashes.items():
        if not isinstance(name, str) or not isinstance(digest, str) or Path(name).is_absolute() or '..' in Path(name).parts:
            raise CorpusError('independent corpus ' + label + ' source invalid')
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise CorpusError('independent corpus ' + label + ' changed; review corpus revision: ' + name)


def _validate_v2(document: dict[str, JsonValue]) -> None:
    fields = {'schema', 'version', 'revision', 'base_manifest_sha256', 'producer_head',
              'producer_source_sha256', 'selector_source_sha256', 'fixture_source_sha256',
              'captured_case_ids', 'large_k', 'identity_groups', 'profiles'}
    if (set(document) != fields or document['schema'] != 'im2p-cycle-corpus-authority'
            or document['version'] != 2 or document['revision'] != 'ws-production-pinned-develop-row-pruned-v2'
            or document['base_manifest_sha256'] != hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
            or document['producer_head'] != '71a8c0328cd436226b8ec5fad03adafac93940ed'):
        raise CorpusError('independent corpus v2 schema/source identity differs')
    ids = document['captured_case_ids']
    if ids != [f'captured-{i:03}' for i in range(1, 43)] or document['large_k'] != list(LARGE_K):
        raise CorpusError('independent corpus v2 fixed identities differ')
    base_fixture = _read_manifest(MANIFEST).get('fixture_source_sha256')
    if not isinstance(base_fixture, dict):
        raise CorpusError('independent corpus v1 fixture source map missing')
    required_sources = {
        'producer_source_sha256': {
            'ggml/src/ggml-gemmini/residual/rmd/rmd-builder.cpp',
            'ggml/src/ggml-gemmini/residual/rmd/rmd-executor.cpp',
            'ggml/src/ggml-gemmini/residual/rmd/rmd-im2p-executor.cpp'},
        'selector_source_sha256': {'RISC-V-DynDNN-gemmini-include/gemmini.h'},
        'fixture_source_sha256': set(base_fixture) | {
            'fpga/gemmini_hp1/host/frontend_rtl_fixture.hpp',
            'fpga/gemmini_hp1/host/rmd_rtl_fixture.hpp',
            'fpga/gemmini_hp1/host/bound_rmd_rtl_fixture.hpp',
            'fpga/gemmini_hp1/host/rmd.cpp',
            'fpga/gemmini_hp1/host/rmd.hpp'},
    }
    for field, names in required_sources.items():
        hashes = document[field]
        if not isinstance(hashes, dict) or set(hashes) != names:
            raise CorpusError('independent corpus v2 source closure differs: ' + field)
    _validate_reviewed_cases(document)


def _validate_v3(document: dict[str, JsonValue]) -> None:
    fields = {'schema', 'version', 'revision', 'base_manifest_sha256', 'producer_base_head',
              'dependency_lock_sha256', 'producer_source_sha256',
              'selector_source_sha256', 'fixture_source_sha256', 'captured_case_ids',
              'large_k', 'observed_total', 'retained_historical_case_ids',
              'moved_historical_residual_case_ids', 'profiles'}
    if (set(document) != fields or document['schema'] != 'im2p-cycle-corpus-authority'
            or document['version'] != 3 or document['revision'] != 'cycle-sim-c617-run-aware-base-gemm-v3'
            or document['base_manifest_sha256'] != hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
            or document['producer_base_head'] != 'c61702970df8b287b1ed75b3e51abab559021b51'):
        raise CorpusError('independent corpus v3 schema/source identity differs')
    expected_sources = _read_manifest(MANIFEST_V2)
    expected_fixture = expected_sources.get('fixture_source_sha256')
    fixture = document['fixture_source_sha256']
    producer = document['producer_source_sha256']
    if (not isinstance(expected_fixture, dict) or not isinstance(fixture, dict) or
            not isinstance(producer, dict)
            or set(fixture) != set(expected_fixture) | {
                'fpga/gemmini_hp1/host/run_aware_rtl_driver.inc'}
            or not {'ggml/src/ggml-gemmini/residual/rmd/rmd-executor.cpp',
                    'ggml/src/ggml-gemmini/residual/rmd/rmd-im2p-executor.cpp',
                    'ggml/src/ggml-gemmini/residual/rmd/rmd-run-aware.cpp',
                    'ggml/src/ggml-gemmini/residual/rmd/rmd-run-aware.hpp'} <= set(producer)):
        raise CorpusError('independent corpus v3 source closure differs')
    if (document['captured_case_ids'] != [f'captured-{i:03}' for i in range(1, 16)] or
            document['observed_total'] != 15 or document['large_k'] != list(LARGE_K) or
            document['profiles'] != list(PROFILES)):
        raise CorpusError('independent corpus v3 fixed current identities differ')
    retained = document['retained_historical_case_ids']
    moved = document['moved_historical_residual_case_ids']
    old = _read_manifest(MANIFEST)['profiles']
    if (not isinstance(old, dict) or set(old) != set(PROFILES) or
            not isinstance(retained, list) or not isinstance(moved, list)):
        raise CorpusError('independent corpus v3 historical partition missing')
    for name in PROFILES:
        rows = old[name]
        if not isinstance(rows, list) or len(rows) != 42 or not all(isinstance(row, dict) for row in rows):
            raise CorpusError('independent corpus v1 source profile missing')
        source_rows = [row for row in rows if isinstance(row, dict)]
        if (retained != [row['case'] for row in source_rows if row['provenance'] != 'residual_hp1'] or
                moved != [row['case'] for row in source_rows if row['provenance'] == 'residual_hp1'] or
                len(retained) != 15 or len(moved) != 27):
            raise CorpusError('independent corpus v3 moved historical identities differ')


def _validate_v4(document: dict[str, JsonValue], prior: dict[str, JsonValue]) -> None:
    fields = {'schema', 'version', 'revision', 'v3_manifest_sha256',
              'producer_base_head', 'dependency_lock_sha256', 'source_manifest_sha256',
              'embedded_package_authority_sha256', 'producer_source_delta'}
    if (set(document) != fields or document['schema'] != 'im2p-cycle-corpus-authority'
            or document['version'] != 4
            or document['revision'] != 'cycle-sim-0e1-metrics-guard-base-gemm-v4'
            or document['v3_manifest_sha256'] != MANIFEST_V3_SHA256
            or document['embedded_package_authority_sha256'] != MANIFEST_V3_SHA256
            or document['producer_base_head'] != '0e1c8976d97887ac9494323b99643145e7b5abae'):
        raise CorpusError('independent corpus v4 schema/source identity differs')
    delta = document['producer_source_delta']
    producer = prior['producer_source_sha256']
    name = 'ggml/src/ggml-gemmini/residual/rmd/rmd-executor.cpp'
    if (not isinstance(delta, dict) or not isinstance(producer, dict)
            or set(delta) != {'path', 'previous_sha256', 'sha256', 'excluded_when'}
            or delta['path'] != name or delta['previous_sha256'] != producer[name]
            or delta['excluded_when'] != 'GGML_GEMMINI_RESIDUAL_METRICS=0'):
        raise CorpusError('independent corpus v4 reviewed producer delta differs')


def _validate_reviewed_cases(document: dict[str, JsonValue]) -> None:
    ids = document['captured_case_ids']
    if ids != [f'captured-{i:03}' for i in range(1, 43)] or document['large_k'] != list(LARGE_K):
        raise CorpusError('independent corpus fixed identities differ')
    groups = document['identity_groups']
    if not isinstance(groups, dict) or set(groups) != set(GROUPS):
        raise CorpusError('independent corpus v2 residual groups differ')
    grouped: list[str] = []
    group_ids: dict[str, list[str]] = {}
    for name in GROUPS:
        group = groups[name]
        if not isinstance(group, list) or not all(isinstance(identity, str) for identity in group):
            raise CorpusError('independent corpus v2 residual case list missing')
        group_ids[name] = [identity for identity in group if isinstance(identity, str)]
        grouped.extend(group_ids[name])
    if (len(grouped) != 27 or len(set(grouped)) != 27 or
            not set(grouped).issubset({f'captured-{i:03}' for i in range(1, 43)})):
        raise CorpusError('independent corpus v2 residual identities differ')
    profiles = document['profiles']
    if not isinstance(profiles, list) or len(profiles) != 6 or not all(isinstance(row, dict) for row in profiles):
        raise CorpusError('independent corpus v2 profile map missing')
    names = [row.get('name') for row in profiles if isinstance(row, dict)]
    if not all(isinstance(name, str) for name in names) or len(names) != len(set(names)) or set(names) != set(PROFILES):
        raise CorpusError('independent corpus v2 duplicate/missing profile')
    old = _read_manifest(MANIFEST)['profiles']
    if not isinstance(old, dict) or set(old) != set(PROFILES):
        raise CorpusError('independent corpus v1 base profile map missing')
    for profile in profiles:
        if not isinstance(profile, dict) or set(profile) != {'name', *GROUPS}:
            raise CorpusError('independent corpus v2 profile fields differ')
        name = profile['name']
        if not isinstance(name, str):
            raise CorpusError('independent corpus v2 profile identity missing')
        bits = 4 if name.startswith('a4') else 8
        dim = int(name.split('-d')[1].split('-')[0])
        original = old[name]
        if not isinstance(original, list) or len(original) != 42 or [row['case'] for row in original if isinstance(row, dict)] != ids:
            raise CorpusError('independent corpus v1 fixed base identities differ')
        by_id = {row['case']: row for row in original if isinstance(row, dict)}
        for group in GROUPS:
            pair = profile[group]
            if not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(part, list) and len(part) == 3 for part in pair):
                raise CorpusError('independent corpus v2 shape/tile missing')
            shape, tile = pair
            m = SOURCE_ROWS[group][0 if bits == 4 else 1]
            k = 32 if group.endswith('block0') else 5
            if shape != [m, 3, k] or tile != [(m + dim - 1) // dim, 1, (k + dim - 1) // dim]:
                raise CorpusError('independent corpus v2 source-derived shape/tile differs')
            for identity in group_ids[group]:
                historical = by_id[identity]
                if (historical['provenance'] != 'residual_hp1' or
                        historical['shape'] != [HISTORICAL_ROWS[group][0 if bits == 4 else 1], 3, k]):
                    raise CorpusError('independent corpus v2 residual group identity differs')


def authority(revision: str = 'v1', *, fixture_root: Path | None = None,
              llama_root: Path | None = None) -> dict[str, JsonValue]:
    if revision not in ('v1', 'v2', 'v3', 'v4'):
        raise CorpusError('independent corpus revision unsupported')
    if revision == 'v2' and hashlib.sha256(MANIFEST_V2.read_bytes()).hexdigest() != MANIFEST_V2_SHA256:
        raise CorpusError('independent corpus v2 manifest bytes changed; review new revision')
    if revision == 'v3' and hashlib.sha256(MANIFEST_V3.read_bytes()).hexdigest() != MANIFEST_V3_SHA256:
        raise CorpusError('independent corpus v3 manifest bytes changed; review new revision')
    if revision == 'v4' and hashlib.sha256(MANIFEST_V4.read_bytes()).hexdigest() != MANIFEST_V4_SHA256:
        raise CorpusError('independent corpus v4 manifest bytes changed; review new revision')
    document = _read_manifest({'v1': MANIFEST, 'v2': MANIFEST_V2,
                               'v3': MANIFEST_V3, 'v4': MANIFEST_V4}[revision])
    if revision == 'v2':
        _validate_v2(document)
    if revision == 'v3':
        _validate_v3(document)
    if revision == 'v4':
        prior = authority('v3')
        _validate_v4(document, prior)
        producer = prior['producer_source_sha256']
        delta = document['producer_source_delta']
        if not isinstance(producer, dict) or not isinstance(delta, dict):
            raise CorpusError('independent corpus v4 producer source map missing')
        changed_path, changed_sha = delta.get('path'), delta.get('sha256')
        if not isinstance(changed_path, str) or not isinstance(changed_sha, str):
            raise CorpusError('independent corpus v4 producer source digest missing')
        document = {**prior, **document,
                    'producer_source_sha256': {**producer, changed_path: changed_sha}}
    if fixture_root is not None:
        _hash_sources(document.get('fixture_source_sha256'), fixture_root, 'fixture')
    if revision == 'v2' and llama_root is not None:
        head = subprocess.run(['git', '-C', str(llama_root), 'rev-parse', 'HEAD'],
                              capture_output=True, text=True, check=False)
        dirty = subprocess.run(['git', '-C', str(llama_root), 'status', '--porcelain', '--untracked-files=no'],
                               capture_output=True, text=True, check=False)
        if (head.returncode or dirty.returncode or head.stdout.strip() != document['producer_head']
                or dirty.stdout.strip()):
            raise CorpusError('independent corpus pinned producer HEAD/cleanliness differs')
        _hash_sources(document.get('producer_source_sha256'), llama_root, 'producer')
        _hash_sources(document.get('selector_source_sha256'), ROOT.parent, 'selector')
    if revision in ('v3', 'v4') and llama_root is not None:
        if (llama_root.name != 'llama_cpp_gemmini' or llama_root.parent.name != 'source' or
                llama_root.parent.parent.name != 'dependency'):
            raise CorpusError('independent corpus candidate llama package layout differs')
        package = llama_root.parents[2]
        _hash_sources({'sim/cycle/corpus-authority-v3.json': MANIFEST_V3_SHA256},
                      package / 'source', 'candidate authority')
        lock_path = package / 'dependency-lock.json'
        if (not lock_path.is_file() or
                hashlib.sha256(lock_path.read_bytes()).hexdigest() != document['dependency_lock_sha256']):
            raise CorpusError('independent corpus candidate dependency lock differs')
        lock = _read_manifest(lock_path)
        repositories = lock.get('repositories')
        candidate = repositories.get('llama_cpp_gemmini') if isinstance(repositories, dict) else None
        if (not isinstance(repositories, dict) or
                not isinstance(candidate, dict) or
                candidate.get('head') != document['producer_base_head']):
            raise CorpusError('independent corpus candidate base head differs')
        _hash_sources(document.get('producer_source_sha256'), llama_root, 'producer')
        _hash_sources(document.get('selector_source_sha256'), ROOT.parent, 'selector')
        source_manifest = _read_manifest(package / 'source-manifest.json')
        if (revision == 'v4' and
                hashlib.sha256((package / 'source-manifest.json').read_bytes()).hexdigest()
                != document['source_manifest_sha256']):
            raise CorpusError('independent corpus candidate source manifest differs')
        files = source_manifest.get('files')
        if not isinstance(files, dict):
            raise CorpusError('independent corpus candidate source manifest missing')
        sources = {'source/sim/cycle/corpus-authority-v3.json': MANIFEST_V3_SHA256}
        for field, prefix in (('producer_source_sha256', 'dependency/source/llama_cpp_gemmini/'),
                              ('fixture_source_sha256', 'source/')):
            hashes = document[field]
            if not isinstance(hashes, dict):
                raise CorpusError('independent corpus candidate source closure missing')
            for name, digest in hashes.items():
                if not isinstance(name, str) or not isinstance(digest, str):
                    raise CorpusError('independent corpus candidate source hash invalid')
                sources[prefix + name] = digest
        if any(not isinstance(entry := files.get(name), dict) or entry.get('sha256') != digest
               for name, digest in sources.items()):
            raise CorpusError('independent corpus candidate source manifest closure differs')
    return document


def authority_reference(revision: str = 'v1') -> dict[str, JsonValue]:
    document = authority(revision)
    manifest = {'v1': MANIFEST, 'v2': MANIFEST_V2,
                'v3': MANIFEST_V3, 'v4': MANIFEST_V4}[revision]
    return {'revision': document['revision'], 'sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}


def corpus(revision: str = 'v1') -> dict[str, list[dict[str, JsonValue]]]:
    profiles = _read_manifest(MANIFEST)['profiles']
    if not isinstance(profiles, dict):
        raise CorpusError('independent corpus profile map missing')
    result: dict[str, list[dict[str, JsonValue]]] = {}
    for profile, rows in profiles.items():
        if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
            raise CorpusError('independent corpus cases missing: ' + profile)
        result[profile] = [dict(row) for row in rows if isinstance(row, dict)]
    if revision in ('v3', 'v4'):
        document = authority(revision)
        retained = document['retained_historical_case_ids']
        if not isinstance(retained, list):
            raise CorpusError('independent corpus v3 retained cases missing')
        for profile, rows in result.items():
            by_id = {row['case']: row for row in rows}
            result[profile] = []
            for index, historical_id in enumerate(retained, start=1):
                if not isinstance(historical_id, str):
                    raise CorpusError('independent corpus v3 retained identity invalid')
                row = dict(by_id[historical_id])
                row['case'] = f'captured-{index:03}'
                row['captured_case'] = index
                result[profile].append(row)
    elif revision == 'v2':
        document = authority('v2')
        groups = document['identity_groups']
        revisions = document['profiles']
        if not isinstance(groups, dict) or not isinstance(revisions, list):
            raise CorpusError('independent corpus v2 groups/profiles missing')
        for revision_profile in revisions:
            if not isinstance(revision_profile, dict):
                raise CorpusError('independent corpus v2 profile missing')
            name = revision_profile['name']
            if not isinstance(name, str):
                raise CorpusError('independent corpus v2 profile identity missing')
            by_id = {row['case']: row for row in result[name]}
            for group in GROUPS:
                pair = revision_profile[group]
                group_cases = groups[group]
                if not isinstance(pair, list) or not isinstance(group_cases, list):
                    raise CorpusError('independent corpus v2 group missing')
                for identity in group_cases:
                    if not isinstance(identity, str):
                        raise CorpusError('independent corpus v2 group identity missing')
                    row = by_id[identity]
                    row['shape'], row['tile'] = pair
    elif revision != 'v1':
        raise CorpusError('independent corpus revision unsupported')
    for profile in result:
        result[profile].extend({'case': f'large-k-{k}', 'shape': [1, 1, k], 'tile': [1, 1, 3],
                                'timing': [3, 13, 17, 11, 5], 'raw': False,
                                'provenance': 'synthetic_large_k'} for k in LARGE_K)
    return result


def validate_corpus(document: Mapping[str, JsonValue]) -> None:
    reference = document.get('corpus_authority')
    if isinstance(reference, dict) and reference.get('revision') == 'cycle-sim-0e1-metrics-guard-base-gemm-v4':
        revision = 'v4'
    elif isinstance(reference, dict) and reference.get('revision') == 'cycle-sim-c617-run-aware-base-gemm-v3':
        revision = 'v3'
    elif isinstance(reference, dict) and reference.get('revision') == 'ws-production-pinned-develop-row-pruned-v2':
        revision = 'v2'
    else:
        revision = 'v1'
    if reference != authority_reference(revision):
        raise CorpusError('independent corpus manifest/digest missing or different')
    if revision == 'v2':
        source = document.get('llama_source')
        manifest = authority('v2')
        if not isinstance(source, dict) or set(source) != {'root', 'head'}:
            raise CorpusError('independent corpus pinned producer identity missing')
        source_root = source.get('root')
        if source.get('head') != manifest['producer_head'] or not isinstance(source_root, str):
            raise CorpusError('independent corpus pinned producer identity missing')
        authority('v2', fixture_root=ROOT, llama_root=Path(source_root))
    if revision in ('v3', 'v4'):
        source = document.get('llama_source')
        manifest = authority(revision)
        if not isinstance(source, dict) or set(source) != {'root', 'base_head', 'source_manifest_sha256',
                                                           'dependency_lock_sha256'}:
            raise CorpusError('independent corpus candidate package identity missing')
        source_root = source.get('root')
        if (not isinstance(source_root, str) or not source_root or
                source['base_head'] != manifest['producer_base_head'] or
                source['dependency_lock_sha256'] != manifest['dependency_lock_sha256']):
            raise CorpusError('independent corpus candidate package identity differs')
        authority(revision, fixture_root=ROOT, llama_root=Path(source_root))
        source_manifest = Path(source_root).parents[2] / 'source-manifest.json'
        if (not isinstance(source['source_manifest_sha256'], str) or
                not source_manifest.is_file() or
                hashlib.sha256(source_manifest.read_bytes()).hexdigest() != source['source_manifest_sha256']):
            raise CorpusError('independent corpus candidate source manifest differs')
    if revision in ('v2', 'v3', 'v4'):
        manifest = authority(revision)
        fixture_hashes = manifest['fixture_source_sha256']
        if not isinstance(fixture_hashes, dict):
            raise CorpusError('independent corpus fixture hash map missing')
        bindings = document.get('rtl_build_bindings')
        if isinstance(bindings, dict):
            for binding in bindings.values():
                if not isinstance(binding, dict):
                    raise CorpusError('independent corpus fixture build binding missing')
                recorded = binding.get('fixture_source_sha256')
                if not isinstance(recorded, dict):
                    raise CorpusError('independent corpus fixture build binding missing')
                for name, digest in fixture_hashes.items():
                    if (name.endswith('rtl_fixture.cpp') or name.endswith('test_ws_rtl.cpp')) and recorded.get(name) != digest:
                        raise CorpusError('independent corpus fixture build binding differs: ' + name)
    expected = corpus(revision)
    identities = {profile: [row['case'] for row in rows] for profile, rows in expected.items()}
    counts = {profile: len(rows) - len(LARGE_K) for profile, rows in expected.items()}
    if document.get('expected_cases') != identities or document.get('captured_corpus_counts') != counts:
        raise CorpusError('independent corpus identities/counts differ')
    if revision in ('v3', 'v4'):
        manifest = authority(revision)
        if (document.get('observed_corpus_counts') != {profile: manifest['observed_total'] for profile in PROFILES}
                or document.get('moved_historical_residual_case_ids') != manifest['moved_historical_residual_case_ids']):
            raise CorpusError('independent corpus v3 observed/moved denominator differs')
    cases = document.get('cases')
    if not isinstance(cases, list):
        raise CorpusError('independent corpus case results missing')
    by_key = {(profile, row['case']): row for profile, rows in expected.items() for row in rows}
    seen: set[tuple[str, str, str]] = set()
    for row in cases:
        if not isinstance(row, dict):
            raise CorpusError('independent corpus result identity missing')
        profile_name, framing, case_name = row.get('profile'), row.get('framing'), row.get('case')
        if not isinstance(profile_name, str) or not isinstance(framing, str) or not isinstance(case_name, str):
            raise CorpusError('independent corpus result identity/framing missing')
        key = (profile_name, framing, case_name)
        if key in seen or framing not in ('regression-tiles', 'planner-blocks'):
            raise CorpusError('independent corpus duplicate/invalid result identity')
        seen.add(key)
        if any(name.startswith(('expected_', 'oracle_', 'measured_')) for name in row):
            raise CorpusError('independent corpus measured answer inserted as input')
        original = by_key.get((str(row['profile']), str(row['case'])))
        if (original is None or any(row.get(field) != original[field] for field in CASE_FIELDS)
                or row.get('captured_case') != original.get('captured_case')):
            raise CorpusError('independent corpus shape/tile/timing/provenance differs')
    if seen != {(profile, framing, row['case']) for profile, rows in expected.items()
                for framing in ('regression-tiles', 'planner-blocks') for row in rows}:
        raise CorpusError('independent corpus result coverage differs')
