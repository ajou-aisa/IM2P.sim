"""NANO_LOCAL_VALIDATED receipts admit only the exact library bytes and current source; never CURRENT_CERTIFIED."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sim.cycle import local_validation
from sim.cycle.local_validation import (NANO_LOCAL_CANDIDATE, NANO_LOCAL_VALIDATED, SCHEMA, VALIDATOR_SOURCES, VERSION,
                                       admit_library, sha256)


def receipt(tmp_path: Path, library: Path, **changes: object) -> Path:
    closure = {'sim/cycle/local_validation.py': sha256(local_validation.ROOT / 'sim/cycle/local_validation.py')}
    document = {'schema': SCHEMA, 'version': VERSION, 'mode': NANO_LOCAL_VALIDATED, 'status': 'PASS',
                'certification': 'NOT_CERTIFIED', 'library': {'path': str(library), 'sha256': sha256(library)},
                'source_closure': {'files': closure},
                'validator': {name: sha256(local_validation.ROOT / name) for name in VALIDATOR_SOURCES}}
    document.update(changes)
    path = tmp_path / 'receipt.json'
    path.write_text(json.dumps(document))
    return path


@pytest.fixture
def library(tmp_path: Path) -> Path:
    path = tmp_path / 'libim2p_cycle_model.so'
    path.write_bytes(b'library bytes')
    return path


def test_pass_receipt_admits_its_library_as_local_not_certified(tmp_path: Path, library: Path) -> None:
    _, validation = admit_library(receipt(tmp_path, library), library)
    assert validation == NANO_LOCAL_VALIDATED != 'CURRENT_CERTIFIED'


def test_candidate_receipt_is_labelled_candidate(tmp_path: Path, library: Path) -> None:
    assert admit_library(receipt(tmp_path, library, status='CANDIDATE'), library)[1] == NANO_LOCAL_CANDIDATE


@pytest.mark.parametrize('changes, reason', [
    ({'status': 'FAILED'}, 'not PASS'),
    ({'schema': 'im2p-cycle-certificate'}, 'unsupported local validation receipt'),
    ({'source_closure': {'files': {'sim/cycle/local_validation.py': '0' * 64}}}, 'source changed'),
    ({'source_closure': {'files': {}}}, 'source changed'),
    ({'version': 1}, 'unsupported local validation receipt'),
    ({'validator': {name: '0' * 64 for name in VALIDATOR_SOURCES}}, 'authority source changed'),
    ({'validator': {}}, 'authority source changed'),
])
def test_invalid_receipt_is_rejected(tmp_path: Path, library: Path, changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        admit_library(receipt(tmp_path, library, **changes), library)


def test_other_library_bytes_are_rejected(tmp_path: Path, library: Path) -> None:
    path = receipt(tmp_path, library)
    library.write_bytes(b'rebuilt library bytes')
    with pytest.raises(ValueError, match='library differs'):
        admit_library(path, library)


def test_local_replay_rejects_a_foreign_receipt_before_reading_the_trace(tmp_path: Path, library: Path) -> None:
    from sim.cycle.local_replay import LocalArtifacts, _replay
    other = tmp_path / 'other.so'
    other.write_bytes(b'other')
    with pytest.raises(ValueError, match='library differs'), (tmp_path / 'out.jsonl').open('x') as stream:
        _replay(tmp_path / 'missing-trace.jsonl', LocalArtifacts(other, receipt(tmp_path, library)), stream)
