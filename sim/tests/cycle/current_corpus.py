from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.corpus_authority import (
    ROOT,
    CorpusError,
    authority,
    authority_reference,
    corpus,
)


def prepare_corpus(profiles: Sequence[Mapping[str, JsonValue]], revision: str
                   ) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    sources = [profile.get('llama_source') for profile in profiles]
    if not sources or not all(source == sources[0] for source in sources) or not isinstance(sources[0], dict):
        raise CorpusError('current candidate package identity missing or inconsistent')
    llama_source = sources[0]
    if set(llama_source) != {'root', 'base_head', 'source_manifest_sha256', 'dependency_lock_sha256'}:
        raise CorpusError('current candidate package identity differs')
    source_root = llama_source['root']
    if not isinstance(source_root, str) or not source_root:
        raise CorpusError('current candidate llama source root missing')
    reviewed = authority(revision, fixture_root=ROOT, llama_root=Path(source_root))
    source_manifest = Path(source_root).parents[2] / 'source-manifest.json'
    if (not source_manifest.is_file() or
            hashlib.sha256(source_manifest.read_bytes()).hexdigest() != llama_source['source_manifest_sha256']):
        raise CorpusError('current candidate source manifest differs')
    if (llama_source['base_head'] != reviewed['producer_base_head'] or
            llama_source['dependency_lock_sha256'] != reviewed['dependency_lock_sha256']):
        raise CorpusError('current candidate package identity differs')
    return reviewed, llama_source


def preflight_document(revision: str, library: Path,
                       bindings: Mapping[str, dict[str, JsonValue]]) -> dict[str, JsonValue]:
    cases: list[JsonValue] = [
        {'profile': name, 'framing': framing, **row}
        for name, rows in corpus(revision).items()
        for framing in ('regression-tiles', 'planner-blocks') for row in rows
    ]
    return {
        'status': 'PREFLIGHT_ONLY', 'cases_planned': len(cases), 'cases_attempted': 0,
        'corpus_authority': authority_reference(revision),
        'model_library_sha256': hashlib.sha256(library.read_bytes()).hexdigest(),
        'rtl_build_binding_sha256': {name: binding['sha256'] for name, binding in bindings.items()},
        'cases': cases,
    }
