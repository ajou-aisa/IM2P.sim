"""Persistent per-work NPU answer cache: numerical key, durable resume, corruption rejection, byte-identical replay."""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from sim.cycle import local_replay, local_validation
from sim.cycle.cli import RESULT_FIELDS
from sim.cycle.local_validation import (
    NANO_LOCAL_VALIDATED,
    SCHEMA,
    VALIDATOR_SOURCES,
    VERSION,
    sha256,
)
from sim.cycle.npu_task_cache import TaskCache, implementation, timing_input
from sim.cycle.npu_trace import ReplayOutputs
from sim.tests.cycle.test_npu_trace import records, resequence, write_trace

PROFILE = 'a8w8-d16-hp1'
CALLS: list[int] = []


def fake_estimate(library: object, document: dict) -> dict:
    """Deterministic stand-in for the cycle model: counters derived from the timing input only (> 2**32)."""
    CALLS.append(1)
    request = document['request']
    total = (1 << 33) + 1000 * request['k'] + request['m']
    result = {name: 0 for name in RESULT_FIELDS}
    result.update(start_cycle=1, done_cycle=1 + total, total_cycles=total, logical_work_count=1)
    return {'status': 'PASS', 'result': result}


def fake_estimate_worker(library: str, document: dict) -> dict:
    return fake_estimate(library, document)


def trace(path: Path, ks: list[int]) -> Path:
    """One FULL work per k (k is the only varied descriptor field; repeats share a binding)."""
    base = records()
    rows = base[:2]
    for index, k in enumerate(ks):
        block = copy.deepcopy(base[2:8])
        for row in block:
            row['operation_id'] = row['node_id'] = index
            if 'call_id' in row:
                row['call_id'] = index
            if 'parent_id' in row:
                row['parent_id'] = index
            if row['kind'] == 'NPU_WORK':
                row.update(work_id=index, k=k, tile_k_count=k // 1024, activation_stride_bytes=k)
            if row.get('stage') == 'COMPLETE_REQUIRED':
                row['required_work_ids'] = [index]
            if row['kind'] == 'TARGET_OPERATION':
                row['k'] = k
        rows += block
    end = copy.deepcopy(base[-1])
    count = len(ks)
    end.update(registered_operation_count=count, completed_operation_count=count, target_npu_count=count,
               npu_work_count=count, call_count=count)
    write_trace(path, resequence(rows + [end]))
    return path


def artifacts(tmp_path: Path, library_bytes: bytes = b'library bytes') -> local_replay.LocalArtifacts:
    library = tmp_path / 'libim2p_cycle_model.so'
    library.write_bytes(library_bytes)
    closure = {'sim/cycle/local_validation.py': sha256(local_validation.ROOT / 'sim/cycle/local_validation.py')}
    receipt = tmp_path / 'receipt.json'
    receipt.write_text(json.dumps({
        'schema': SCHEMA, 'version': VERSION, 'mode': NANO_LOCAL_VALIDATED, 'status': 'PASS',
        'certification': 'NOT_CERTIFIED', 'library': {'path': str(library), 'sha256': sha256(library)},
        'source_closure': {'files': closure}, 'semantic_options_sha256': '0' * 64,
        'validator': {name: sha256(local_validation.ROOT / name) for name in VALIDATOR_SOURCES}}))
    return local_replay.LocalArtifacts(library, receipt)


def replay(tmp_path: Path, name: str, source: Path, local: local_replay.LocalArtifacts,
           cache: Path | None, workers: int | None = None) -> tuple[bytes, bytes, dict]:
    outputs = ReplayOutputs(tmp_path / f'{name}.jsonl', tmp_path / f'{name}-summary.json')
    stats: dict = {}
    if workers is None:
        local_replay._publish(source, local, outputs, None, stats, cache)
    else:
        _, stats = local_replay.replay_fast(source, local, outputs, workers, cache)
    return outputs.results.read_bytes(), outputs.summary.read_bytes(), stats


@pytest.fixture(autouse=True)
def fake_model(monkeypatch: pytest.MonkeyPatch) -> None:
    CALLS.clear()
    monkeypatch.setattr(local_replay.cli, 'estimate', fake_estimate)
    monkeypatch.setattr(local_replay, '_estimate', fake_estimate_worker)


def document(**request: int) -> dict:
    return {'profile': PROFILE, 'limits': {'max_cycles': 1 << 40},
            'request': {'m': 1, 'n': 1, 'k': 3072, 'logical_work_id': 7, 'accepted_cycle': 1, **request},
            'original_k': 4096, 'runs': [{'original_block_id': 3, 'original_k_mask': 7, 'compact_k_begin': 0,
                                          'compact_k_count': 3072}]}


def test_key_ignores_only_the_diagnostic_work_id(tmp_path: Path) -> None:
    cache = TaskCache(tmp_path / 'cache.sqlite', implementation('a' * 64, 'b' * 64, PROFILE))
    base = cache.key(document())[0]
    # The work id is a diagnostic tag; every other document field is part of the numerical identity.
    assert cache.key(document(logical_work_id=8))[0] == base
    assert 'logical_work_id' not in timing_input(document())['request']
    changed = [document(k=2048), document(m=2), document(accepted_cycle=2)]
    runs = document()
    runs['runs'][0]['original_block_id'] = 4
    limits = document()
    limits['limits'] = {'max_cycles': 1 << 41}
    residual = document()
    residual['original_k'] = 8192
    assert len({base, *(cache.key(value)[0] for value in (*changed, runs, limits, residual))}) == 7
    # The library, semantic options and profile configuration are part of the key as well.
    other = TaskCache(tmp_path / 'other.sqlite', implementation('c' * 64, 'b' * 64, PROFILE))
    options = TaskCache(tmp_path / 'options.sqlite', implementation('a' * 64, 'd' * 64, PROFILE))
    assert len({base, other.key(document())[0], options.key(document())[0]}) == 3


def test_answers_round_trip_exactly_and_bad_rows_are_quarantined(tmp_path: Path) -> None:
    path = tmp_path / 'cache.sqlite'
    cache = TaskCache(path, implementation('a' * 64, 'b' * 64, PROFILE))
    answer = fake_estimate(None, document())
    assert answer['result']['total_cycles'] > 1 << 32
    assert cache.lookup(document()) is None
    cache.store(document(), answer)
    assert cache.lookup(document(logical_work_id=99)) == answer
    with pytest.raises(ValueError, match='different result'):
        cache.store(document(), {**answer, 'result': {**answer['result'], 'total_cycles': 1}})
    key = cache.key(document())[0]
    for column, value, reason in (('result', '{"status":"PASS"}', 'result checksum mismatch'),
                                  ('status', 'PENDING', 'incomplete entry'),
                                  ('timing_input', '{}', 'timing input differs')):
        cache.store(document(), answer)
        with sqlite3.connect(path) as database:
            database.execute(f'UPDATE tasks SET {column}=? WHERE key=?', (value, key))
        assert cache.lookup(document()) is None
        with sqlite3.connect(path) as database:
            assert database.execute('SELECT reason FROM rejected ORDER BY rowid DESC LIMIT 1').fetchone()[0].startswith(reason)
            assert database.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
    assert cache.counters['rejected'] == 3
    cache.close()


def test_concurrent_writers_share_one_answer(tmp_path: Path) -> None:
    path = tmp_path / 'cache.sqlite'
    first = TaskCache(path, implementation('a' * 64, 'b' * 64, PROFILE))
    second = TaskCache(path, implementation('a' * 64, 'b' * 64, PROFILE))
    answer = fake_estimate(None, document())
    first.store(document(), answer)
    second.store(document(), answer)
    assert second.lookup(document()) == answer
    with pytest.raises(ValueError, match='different result'):
        second.store(document(), {**answer, 'result': {**answer['result'], 'loop_count': 5}})


def test_replay_resumes_from_completed_answers_with_identical_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = trace(tmp_path / 'trace.jsonl', [1024, 2048, 1024, 3072, 4096, 2048, 5120])
    local = artifacts(tmp_path)
    plain = replay(tmp_path, 'plain', source, local, None)
    assert len(CALLS) == 5
    # Given: a replay interrupted after two completed answers.
    cache = tmp_path / 'tasks.sqlite'
    CALLS.clear()

    def failing(library: object, document: dict) -> dict:
        if len(CALLS) == 2:
            raise RuntimeError('interrupted')
        return fake_estimate(library, document)
    monkeypatch.setattr(local_replay.cli, 'estimate', failing)
    with pytest.raises(RuntimeError, match='interrupted'):
        replay(tmp_path, 'interrupted', source, local, cache)
    assert not (tmp_path / 'interrupted.jsonl').exists()
    with sqlite3.connect(cache) as database:
        assert database.execute("SELECT COUNT(*) FROM tasks WHERE status='COMPLETE'").fetchone()[0] == 2
    # When: the replay is repeated.
    monkeypatch.setattr(local_replay.cli, 'estimate', fake_estimate)
    CALLS.clear()
    resumed = replay(tmp_path, 'resumed', source, local, cache)
    # Then: only the missing answers are computed, and the published bytes equal the uncached replay.
    assert resumed[:2] == plain[:2] and len(CALLS) == 3
    assert resumed[2]['npu_task_cache']['hit'] == 2 and resumed[2]['model_calls'] == 3
    CALLS.clear()
    again = replay(tmp_path, 'again', source, local, cache)
    assert again[:2] == plain[:2] and not CALLS and again[2]['npu_task_cache']['hit'] == 5


def test_fast_replay_persists_every_answer_and_other_libraries_miss(tmp_path: Path) -> None:
    source = trace(tmp_path / 'trace.jsonl', [1024, 2048, 1024, 3072])
    local = artifacts(tmp_path)
    cache = tmp_path / 'tasks.sqlite'
    serial = replay(tmp_path, 'serial', source, local, None)
    fast = replay(tmp_path, 'fast', source, local, cache, workers=2)
    assert fast[:2] == serial[:2]
    assert fast[2]['model_calls'] == fast[2]['npu_task_cache']['persisted'] == 3
    hit = replay(tmp_path, 'hit', source, local, cache, workers=2)
    assert hit[:2] == serial[:2] and hit[2]['model_calls'] == 0 and hit[2]['npu_task_cache']['hit'] == 3
    # Other library bytes are another numerical identity: nothing is reused.
    other_root = tmp_path / 'other'
    other_root.mkdir()
    other = replay(other_root, 'other', source, artifacts(other_root, b'rebuilt library'), cache, workers=2)
    assert other[2]['model_calls'] == 3 and other[2]['npu_task_cache']['hit'] == 0
    assert hashlib.sha256(other[0]).hexdigest() != hashlib.sha256(serial[0]).hexdigest()  # library identity differs
