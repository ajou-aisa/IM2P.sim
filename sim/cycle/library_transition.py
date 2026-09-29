"""Admit a CURRENT cycle library for an OLD-library base certificate only through a reviewed exact transition.

The base single-GEMM certificate stays the authority for its corpus and is still validated, unchanged,
against the exact OLD library bytes it names. A transition certificate only adds that the CURRENT
library produced canonical outputs identical to that OLD library on the preserved certification corpora
and reproduced their retained RTL evidence. It is not an RTL recertification. Reviewed certificate
identities are pinned; every use re-hashes the whole evidence closure and recomputes the per-scope
OLD/CURRENT equality and denominators from the bound captures instead of trusting recorded labels.
"""
from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.certificate_contract import read_document
from sim.cycle.reconstruct_graph import sha256

SCHEMA: Final = 'im2p-cycle-library-transition-certificate-v1'
# Reviewed immutable identities: certificate SHA256 -> (OLD library, CURRENT library, base certificate).
REVIEWED: Final = {
    '245f08fc788bc47a3e2ed90e17db33dd4c6c89791947b0cbcd4701284c6a0546': (
        'c443ec1d9dd72720994005367d626d34195d4773892c4de0fcbdb9ea07d13fdd',
        'f648b4bc22368ef0bf13b24dfd85028cc76dc8c03ebf13c2295322337c55b0d5',
        '44672d186182a224ee10e5e3723b66119a424a8752963e47ad8dac9b0cfe2ee8'),
}
# scope -> (capture records, admitted units, retained-RTL exact count); error probe has no RTL anchor.
SCOPES: Final = {'base240': (240, 240, 240), 'run-aware42': (42, 42, 42), 'drained1056': (1056, 1056, 1056),
                 'exact30': (120, 120, 30), 'guarded-stateful80': (6, 80, 80), 'error-class-probe': (24, 24, None)}


class TransitionError(ValueError):
    def __init__(self, detail: str) -> None:
        super().__init__('transition: ' + detail)


def _require(condition: bool, detail: str) -> None:
    if not condition:
        raise TransitionError(detail)


def _object(value: JsonValue, label: str) -> dict[str, JsonValue]:
    _require(isinstance(value, dict), label + ': object required')
    assert isinstance(value, dict)
    return value


def _path(value: JsonValue, label: str) -> Path:
    item = _object(value, label)
    return Path(str(item.get('path')))


def _closure(value: JsonValue) -> None:
    """Every absolute path/SHA256 reference inside the certificate must still name the same bytes."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, dict):
            path, digest = item.get('path'), item.get('sha256')
            if isinstance(path, str) and Path(path).is_absolute() and isinstance(digest, str):
                _require(Path(path).is_file() and sha256(Path(path)) == digest, 'changed or missing: ' + path)
            pending.extend(item.values())


def _lines(path: Path) -> list[bytes]:
    with gzip.open(path, 'rb') as stream:
        return stream.read().splitlines()


@dataclass(frozen=True, slots=True)
class Transition:
    certificate_sha256: str
    old_library: Path
    old_library_sha256: str
    current_library_sha256: str


def validate_transition(path: Path, base_certificate: Path, library: Path) -> Transition:
    digest = sha256(path)
    _require(digest in REVIEWED, 'unreviewed transition certificate')
    old_sha, current_sha, base_sha = REVIEWED[digest]
    document = read_document(path)
    _require(document.get('schema') == SCHEMA and document.get('version') == 1 and
             document.get('status') == 'EXACT' and document.get('fresh_rtl_execution') is False,
             'schema, status or scope differs')
    old, current = _object(document.get('old'), 'old'), _object(document.get('current'), 'current')
    _require(sha256(base_certificate) == base_sha == _object(old.get('base_certificate'), 'base')['sha256'],
             'base certificate differs from the reviewed transition')
    _require(read_document(base_certificate).get('model_library_sha256') == old_sha,
             'base certificate names another library')
    _require(_object(old.get('library'), 'old library')['sha256'] == old_sha != current_sha,
             'OLD library identity differs')
    _require(sha256(library) == current_sha == _object(current.get('library'), 'current library')['sha256'],
             'caller library is not the transition CURRENT library')
    _closure(document)
    scopes = _object(document.get('corpus_and_comparison'), 'scopes')
    _require(set(scopes) == set(SCOPES), 'scope set differs')
    for name, (records, units, rtl_exact) in SCOPES.items():
        row = _object(scopes[name], name)
        old_lines = _lines(_path(row.get('old_capture'), name))
        current_lines = _lines(_path(row.get('current_capture'), name))
        _require(old_lines == current_lines, name + ': OLD/CURRENT canonical outputs differ')
        parsed = [_object(json.loads(line), name) for line in current_lines]
        observed = (sum(int(str(r.get('work_count', 0))) for r in parsed)
                    if name == 'guarded-stateful80' else len(parsed))
        _require(len(parsed) == records and observed == units, name + ': denominator differs')
        for side, want in (('old', old_sha), ('current', current_sha)):
            summary = read_document(_path(row.get(side + '_capture_summary'), name))
            _require(_object(summary.get('library'), 'capture library')['sha256'] == want and
                     _object(summary.get('output'), 'capture output')['sha256'] ==
                     _object(row.get(side + '_capture'), name)['sha256'] and
                     summary.get('scope') == name, name + ': capture was not produced by the bound library')
        if rtl_exact is None:
            continue
        _require(all(r.get('outcome') == 'PASS' for r in parsed), name + ': non-admitted corpus case')
        receipt = read_document(_path(_object(row.get('rtl_anchor'), 'rtl anchor').get('current_receipt'), name))
        _require(receipt.get('classification') == 'FRESH_MODEL_REUSED_RTL' and receipt.get('status') == 'PASS' and
                 receipt.get('expected') == receipt.get('exact') == rtl_exact and receipt.get('mismatch_count') == 0 and
                 _object(receipt.get('current_library'), 'receipt library')['sha256'] == current_sha and
                 receipt.get('legacy_source') == row.get('corpus_authority'),
                 name + ': CURRENT does not reproduce the retained RTL evidence of this corpus')
    return Transition(digest, _path(old.get('library'), 'old library'), old_sha, current_sha)


def certified_library(certificate: Path, library: Path, transition: Path | None) -> tuple[Path, str | None]:
    """Library bytes the base certificate is validated against, and the transition that admitted CURRENT."""
    if transition is None:
        return library, None
    admitted = validate_transition(transition, certificate, library)
    return admitted.old_library, admitted.certificate_sha256
