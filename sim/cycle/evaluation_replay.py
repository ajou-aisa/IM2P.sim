"""FAST_EVALUATION isolated NPU replay: the unchanged certified npu_trace.replay answered from a parallel memo.

An isolated cycle-model answer is a pure function of (library bytes, model document). While this process parses
the trace, the documents the serial replay will request (its binding-cache misses) are estimated in a process pool;
the serial replay (certificate checks, input snapshots, row order, binding cache, summary) then runs unmodified
with sim.cycle.cli replaced by a lookup that serves those answers for the same library bytes and calls the real
model for anything it lacks. Certification and audit keep calling sim.cycle.npu_trace directly; parity with it is
checked byte-for-byte before this path is used.
"""
from __future__ import annotations

import argparse
from concurrent.futures import Future, ProcessPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.gemmini_resolve_profile import BuildFailure
from sim.cycle import cli, npu_trace
from sim.cycle.npu_trace import ReplayArtifacts, ReplayOutputs, model_document, work_binding
from sim.cycle.npu_trace_integrity import read_records, start_trace
from sim.cycle.npu_trace_schema import Record, require


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def document_key(document: Record) -> str:
    return json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _estimate(library: str, document: Record) -> Record:
    return cli.estimate(Path(library), document)


SERIAL_CACHE_ENTRIES = 4096  # npu_trace._replay FIFO binding cache; a mismatch only costs fallback calls


def precompute(trace: Path, library: Path, workers: int) -> tuple[dict[str, Record], int]:
    """Answer the documents the serial replay will request; only this process reads the trace."""
    records = read_records(trace)
    state = start_trace(records)
    futures: dict[str, Future[Record]] = {}
    cache: dict[str, None] = {}
    works = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for record in records:
            work = state.consume(record)
            if work is None:
                continue
            works += 1
            binding = work_binding(work)
            if binding in cache:
                continue
            if len(cache) == SERIAL_CACHE_ENTRIES:
                cache.pop(next(iter(cache)))
            cache[binding] = None
            document = model_document(state.run.profile, work)
            key = document_key(document)
            if key not in futures:
                futures[key] = pool.submit(_estimate, str(library), document)
        return {key: future.result() for key, future in futures.items()}, works


class MemoModel:
    """Stands in for sim.cycle.cli inside the unchanged replay; other library bytes fail closed."""

    def __init__(self, memo: dict[str, Record], library_sha256: str) -> None:
        self.memo = memo
        self.library_sha256 = library_sha256
        self.verified: set[Path] = set()
        self.calls = 0
        self.fallback_calls = 0

    def estimate(self, library: Path, document: Record) -> Record:
        if library not in self.verified:
            require(sha256(library) == self.library_sha256, 'evaluation replay library differs from the memo library')
            self.verified.add(library)
        self.calls += 1
        answer = self.memo.get(document_key(document))
        if answer is None:
            self.fallback_calls += 1
            return cli.estimate(library, document)
        return deepcopy(answer)


def replay(trace: Path, artifacts: ReplayArtifacts, outputs: ReplayOutputs, workers: int) -> tuple[Record, Record]:
    require(workers > 0, 'positive worker count required')
    started = time.monotonic()
    library_sha256 = sha256(artifacts.library)
    with tempfile.TemporaryDirectory(prefix='evaluation-library-', dir=outputs.results.parent) as directory:
        library = Path(directory) / artifacts.library.name
        shutil.copyfile(artifacts.library, library)
        require(sha256(library) == library_sha256, 'library changed while copying for workers')
        memo, works = precompute(trace, library, workers)
    precomputed = time.monotonic()
    model = MemoModel(memo, library_sha256)
    original = npu_trace.cli
    npu_trace.cli = model  # type: ignore[assignment]
    try:
        summary = npu_trace.replay(trace, artifacts, outputs)
    finally:
        npu_trace.cli = original
    finished = time.monotonic()
    require(summary['npu_work_count'] == works, 'evaluation replay work count differs from precompute')
    stats: Record = {'mode': 'FAST_EVALUATION_ISOLATED', 'workers': workers, 'npu_work_count': works,
                     'precomputed_documents': len(memo), 'model_calls': model.calls, 'fallback_calls': model.fallback_calls,
                     'library_sha256': library_sha256, 'precompute_seconds': round(precomputed - started, 3),
                     'serial_replay_seconds': round(finished - precomputed, 3),
                     'total_seconds': round(finished - started, 3)}
    return summary, stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace', type=Path)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--cycle-certificate', type=Path, required=True)
    parser.add_argument('--run-aware-certificate', type=Path)
    parser.add_argument('--transition-certificate', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--summary', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 2))
    args = parser.parse_args()
    try:
        summary, stats = replay(args.trace, ReplayArtifacts(args.library, args.cycle_certificate,
                                                            args.run_aware_certificate, args.transition_certificate),
                                ReplayOutputs(args.output, args.summary), args.workers)
        print(json.dumps({**{key: summary[key] for key in ('status', 'npu_work_count', 'isolated_cycle_sum')},
                          'evaluation': stats}, sort_keys=True))
    except (OSError, ValueError, BuildFailure, RuntimeError) as error:
        print(f'NPU trace evaluation replay failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
