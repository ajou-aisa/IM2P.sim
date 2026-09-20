"""Reviewed corpus inputs, independent of certificate outcomes and counts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Final, Mapping

from scripts.gemmini_resolve_profile import JsonValue

ROOT: Final = Path(__file__).resolve().parents[2]
MANIFEST: Final = Path(__file__).with_name('corpus-authority-v1.json')
LARGE_K: Final = (32, 64, 96, 3072, 8256)
CASE_FIELDS: Final = ('case', 'shape', 'tile', 'timing', 'raw', 'provenance')


class CorpusError(ValueError):
    pass


def authority() -> dict[str, JsonValue]:
    document: dict[str, JsonValue] = json.loads(MANIFEST.read_text())
    hashes = document.get('fixture_source_sha256')
    if not isinstance(hashes, dict) or not hashes:
        raise CorpusError('independent corpus fixture authority missing')
    for name, digest in hashes.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise CorpusError('independent corpus fixture changed; review corpus revision: ' + name)
    return document


def authority_reference() -> dict[str, JsonValue]:
    document = authority()
    return {'revision': document['revision'], 'sha256': hashlib.sha256(MANIFEST.read_bytes()).hexdigest()}


def corpus() -> dict[str, list[dict[str, JsonValue]]]:
    profiles = authority()['profiles']
    if not isinstance(profiles, dict):
        raise CorpusError('independent corpus profile map missing')
    result: dict[str, list[dict[str, JsonValue]]] = {}
    for profile, rows in profiles.items():
        if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
            raise CorpusError('independent corpus cases missing: ' + profile)
        result[profile] = [row for row in rows if isinstance(row, dict)]
        result[profile].extend({'case': f'large-k-{k}', 'shape': [1, 1, k], 'tile': [1, 1, 3],
                                'timing': [3, 13, 17, 11, 5], 'raw': False,
                                'provenance': 'synthetic_large_k'} for k in LARGE_K)
    return result


def validate_corpus(document: Mapping[str, JsonValue]) -> None:
    expected = corpus()
    if document.get('corpus_authority') != authority_reference():
        raise CorpusError('independent corpus manifest/digest missing or different')
    identities = {profile: [row['case'] for row in rows] for profile, rows in expected.items()}
    counts = {profile: len(rows) - len(LARGE_K) for profile, rows in expected.items()}
    if document.get('expected_cases') != identities or document.get('captured_corpus_counts') != counts:
        raise CorpusError('independent corpus identities/counts differ')
    cases = document.get('cases')
    if not isinstance(cases, list):
        raise CorpusError('independent corpus case results missing')
    by_key = {(profile, row['case']): row for profile, rows in expected.items() for row in rows}
    for row in cases:
        if not isinstance(row, dict) or not isinstance(row.get('profile'), str) or not isinstance(row.get('case'), str):
            raise CorpusError('independent corpus result identity missing')
        original = by_key.get((str(row['profile']), str(row['case'])))
        if original is None or any(row.get(field) != original[field] for field in CASE_FIELDS):
            raise CorpusError('independent corpus shape/tile/timing/provenance differs')
