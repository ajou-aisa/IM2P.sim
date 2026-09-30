"""Persistent per-work NPU result cache for the NANO_LOCAL isolated replay (evaluation-only, never certified).

A cached answer stands in for one `cli.estimate` call, so its key is the numerical identity of that call:
  * the complete model document the cycle model receives (`npu_trace.model_document`): profile, limits, every request
    field and, for run-aware work, `original_k` and each run span (`original_block_id`, mask, compact range). Only
    `request.logical_work_id` is removed: the isolated engine copies it into diagnostics (request owner, state tags,
    trace events; `WorkContext`/`Engine` in sim/cycle/control_engine.cpp) and never into a timing decision, and the
    replay already shares one answer across work ids through its binding cache;
  * the cycle-library bytes, the semantic build options the local validation receipt admits for them, and the code and
    configuration that turn the document into the model configuration (sim/cycle/cli.py, the profile resolver, the
    hardware catalog and the profile's host-memory contract), plus this module.
Stateful (session) answers are never stored here: they depend on the entering state and the offer, not on the document.

Each answer is committed in its own transaction as soon as it arrives (WAL, synchronous=FULL), so an interrupted replay
keeps every completed answer and resumes from them. A row is used only when it is complete, its stored timing input
equals the request and its result checksum matches; any other row is moved to `rejected` and the work is recomputed.
Rows are only ever inserted, never updated; a conflicting second insert of the same key must carry the same result.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from scripts.gemmini_resolve_profile import DEFAULT_CATALOG
from sim.cycle.npu_trace_schema import Record, object_value, require

SCHEMA: Final = 'im2p-nano-local-npu-task-cache'
VERSION: Final = 1
ROOT: Final = Path(__file__).resolve().parents[2]
IMPLEMENTATION_SOURCES: Final = ('sim/cycle/cli.py', 'scripts/gemmini_resolve_profile.py', 'sim/cycle/npu_task_cache.py')
CONTRACTS: Final = ROOT / 'config/gemmini_host_memory_contracts'


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def sha256_file(path: Path) -> str:
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def timing_input(document: Record) -> Record:
    """The model document without the diagnostic work id; every timing-relevant field is kept."""
    request = dict(object_value(document.get('request')))
    request.pop('logical_work_id', None)
    return {**document, 'request': request}


def implementation(library_sha256: str, semantic_options_sha256: str, profile: str) -> Record:
    contract = CONTRACTS / (profile + '.json')
    require(contract.is_file(), 'no host-memory contract for profile ' + profile)
    return {'library_sha256': library_sha256, 'semantic_options_sha256': semantic_options_sha256,
            'sources': {name: sha256_file(ROOT / name) for name in IMPLEMENTATION_SOURCES},
            'catalog_sha256': sha256_file(DEFAULT_CATALOG), 'memory_contract_sha256': sha256_file(contract)}


@dataclass(slots=True)
class TaskCache:
    """One writer connection guarded by a lock: answers arrive on the process pool's completion thread."""
    path: Path
    implementation: Record
    counters: dict[str, int] = field(default_factory=lambda: {'hit': 0, 'miss': 0, 'persisted': 0, 'rejected': 0})
    implementation_sha256: str = ''
    library_sha256: str = ''
    _database: sqlite3.Connection | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        database = sqlite3.connect(self.path, timeout=60, isolation_level=None, check_same_thread=False)
        database.execute('PRAGMA journal_mode=WAL')
        database.execute('PRAGMA synchronous=FULL')
        database.executescript('''CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS tasks(key TEXT PRIMARY KEY, timing_input TEXT NOT NULL,
            timing_input_sha256 TEXT NOT NULL, implementation TEXT NOT NULL, implementation_sha256 TEXT NOT NULL,
            library_sha256 TEXT NOT NULL, result TEXT NOT NULL, result_sha256 TEXT NOT NULL,
            status TEXT NOT NULL, created_utc TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS rejected(key TEXT NOT NULL, reason TEXT NOT NULL, row TEXT NOT NULL,
            rejected_utc TEXT NOT NULL);''')
        database.execute('INSERT OR IGNORE INTO meta VALUES(?,?)', ('schema', canonical([SCHEMA, VERSION])))
        stored = database.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        require(stored is not None and stored[0] == canonical([SCHEMA, VERSION]), 'unsupported NPU task cache schema')
        self._database = database
        self.implementation_sha256 = sha256_text(canonical(self.implementation))
        self.library_sha256 = str(self.implementation['library_sha256'])

    def key(self, document: Record) -> tuple[str, str]:
        text = canonical(timing_input(document))
        return sha256_text(canonical({'schema': SCHEMA, 'version': VERSION, 'timing_input_sha256': sha256_text(text),
                                      'implementation_sha256': self.implementation_sha256})), text

    def lookup(self, document: Record) -> Record | None:
        """A verified complete answer for the document, or None (miss; a bad row is quarantined first)."""
        key, text = self.key(document)
        database = self._connection()
        with self._lock:
            row = database.execute('SELECT timing_input,implementation_sha256,library_sha256,result,result_sha256,status '
                                   'FROM tasks WHERE key=?', (key,)).fetchone()
            if row is None:
                self.counters['miss'] += 1
                return None
            stored_input, implementation_sha256, library_sha256, result, result_sha256, status = row
            reason = ('incomplete entry' if status != 'COMPLETE' else
                      'timing input differs from its key' if stored_input != text else
                      'implementation differs from its key' if implementation_sha256 != self.implementation_sha256 else
                      'library differs from its key' if library_sha256 != self.library_sha256 else
                      'result checksum mismatch' if sha256_text(result) != result_sha256 else None)
            if reason is None:
                try:
                    answer = object_value(json.loads(result))
                except ValueError:
                    reason = 'result is not a JSON object'
                else:
                    if canonical(answer) == result:
                        self.counters['hit'] += 1
                        return answer
                    reason = 'result is not canonical'
            database.execute('BEGIN IMMEDIATE')
            database.execute('INSERT INTO rejected VALUES(?,?,?,?)', (key, reason, canonical(list(row)), _now()))
            database.execute('DELETE FROM tasks WHERE key=?', (key,))
            database.execute('COMMIT')
            self.counters['rejected'] += 1
            self.counters['miss'] += 1
            return None

    def store(self, document: Record, answer: Record) -> None:
        """Commit one complete answer; a concurrent writer of the same key must have stored the same result."""
        key, text = self.key(document)
        result = canonical(answer)
        database = self._connection()
        with self._lock:
            database.execute('BEGIN IMMEDIATE')
            try:
                database.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(key) DO NOTHING',
                                 (key, text, sha256_text(text), canonical(self.implementation),
                                  self.implementation_sha256, self.library_sha256, result, sha256_text(result),
                                  'COMPLETE', _now()))
                stored = database.execute('SELECT result FROM tasks WHERE key=?', (key,)).fetchone()
                require(stored is not None and stored[0] == result, 'NPU task cache holds a different result for ' + key)
                database.execute('COMMIT')
            except BaseException:
                database.execute('ROLLBACK')
                raise
            self.counters['persisted'] += 1

    def close(self) -> None:
        with self._lock:
            if self._database is not None:
                self._database.close()
                self._database = None

    def _connection(self) -> sqlite3.Connection:
        require(self._database is not None, 'NPU task cache is closed')
        assert self._database is not None
        return self._database


def _now() -> str:
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
