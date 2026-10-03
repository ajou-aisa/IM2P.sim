from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.corpus_authority import (
    MANIFEST_V3_SHA256,
    MANIFEST_V4_SHA256,
    CorpusError,
    _hash_sources,
    _read_manifest,
)


def validate_v5(document: dict[str, JsonValue]) -> None:
    fields = {'schema', 'version', 'revision', 'scope', 'v4_manifest_sha256',
              'producer_base_head', 'dependency_lock_sha256', 'source_manifest_sha256',
              'embedded_package_authority_sha256'}
    if (set(document) != fields or document['schema'] != 'im2p-cycle-corpus-authority'
            or document['version'] != 5
            or document['revision'] != 'cycle-sim-cbe-source-equivalent-base-gemm-v5'
            or document['scope'] != 'isolated-base-gemm-source-equivalent-package'
            or document['v4_manifest_sha256'] != MANIFEST_V4_SHA256
            or document['embedded_package_authority_sha256'] != MANIFEST_V3_SHA256
            or document['producer_base_head'] != 'cbe3530468c1e3d50aca3b4b3bd817fc6ea72158'):
        raise CorpusError('independent corpus v5 schema/source identity differs')


def validate_package(document: dict[str, JsonValue], llama_root: Path, selector_root: Path) -> None:
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
    _hash_sources(document.get('selector_source_sha256'), selector_root, 'selector')
    if document['version'] == 5:
        _hash_sources(document.get('fixture_source_sha256'), package / 'source', 'candidate fixture')
    source_manifest = _read_manifest(package / 'source-manifest.json')
    if (document['version'] in (4, 5) and
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
