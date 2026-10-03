from __future__ import annotations

import os
from pathlib import Path

import pytest

from sim.cycle import evaluation_replay, npu_trace
from sim.cycle.npu_trace import ReplayArtifacts, ReplayOutputs
from sim.cycle.npu_trace_schema import Record


def test_memo_serves_precomputed_answers_and_falls_back_on_a_miss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a memo for one document of one library.
    library = tmp_path / 'lib.dylib'
    library.write_bytes(b'library')
    known: Record = {'request': {'m': 1}}
    unknown: Record = {'request': {'m': 2}}
    model = evaluation_replay.MemoModel({evaluation_replay.document_key(known): {'status': 'PASS', 'result': {'total_cycles': 7}}},
                                        evaluation_replay.sha256(library))
    calls: list[object] = []
    monkeypatch.setattr(evaluation_replay.cli, 'estimate', lambda _, document: calls.append(document) or {'status': 'PASS'})
    # When the replay asks for a known and an unknown document.
    assert model.estimate(library, known)['result'] == {'total_cycles': 7}
    assert model.estimate(library, unknown) == {'status': 'PASS'}
    # Then only the unknown one reaches the real model.
    assert (model.calls, model.fallback_calls, calls) == (2, 1, [unknown])


def test_memo_rejects_other_library_bytes(tmp_path: Path) -> None:
    library = tmp_path / 'lib.dylib'
    library.write_bytes(b'certified')
    model = evaluation_replay.MemoModel({}, evaluation_replay.sha256(library))
    library.write_bytes(b'other')
    with pytest.raises(ValueError, match='library differs'):
        model.estimate(library, {})


def _env(name: str) -> Path:
    value = os.environ.get(name)
    if value is None:
        pytest.skip(name + ' not set')
    return Path(value)


def test_parallel_replay_is_byte_identical_to_the_certified_serial_replay(tmp_path: Path) -> None:
    # Given a real trace and the certified replay inputs (opt-in; about three minutes on the 374-work smoke).
    trace = _env('IM2P_SMOKE_TRACE')
    artifacts = ReplayArtifacts(_env('IM2P_CYCLE_LIBRARY'), _env('IM2P_REPLAY_CYCLE_CERTIFICATE'),
                                _env('IM2P_REPLAY_RUN_AWARE_CERTIFICATE'), _env('IM2P_TRANSITION_CERTIFICATE'))
    serial = ReplayOutputs(tmp_path / 'serial.jsonl', tmp_path / 'serial.json')
    fast = ReplayOutputs(tmp_path / 'fast.jsonl', tmp_path / 'fast.json')
    # When both paths replay it.
    npu_trace.replay(trace, artifacts, serial)
    _, stats = evaluation_replay.replay(trace, artifacts, fast, 8)
    # Then results and summary are the same bytes and the certified module is restored.
    assert serial.results.read_bytes() == fast.results.read_bytes()
    assert serial.summary.read_bytes() == fast.summary.read_bytes()
    assert stats['fallback_calls'] == 0 and npu_trace.cli is evaluation_replay.cli
