# How to run: PYTHONPATH=IM2P.sim python3 -B -m sim.cycle.local_validation build --build-receipt RECEIPT \
#   --trace NPU_TRACE --output RECEIPT.json [--workers N] [--stateful-budget CYCLES]
"""Host-local validation of a freshly built cycle library: NANO_LOCAL_VALIDATED, never CURRENT_CERTIFIED.

A certified library is one whose exact bytes a reviewed certificate names (RTL-anchored, CURRENT_CERTIFIED).
A library rebuilt on another host has different bytes, so no certificate can name it. This module records what
can be checked on the host without RTL: build/CTest pass, source closure, exported C API, a small deterministic
probe, byte parity between the serial and FAST_EVALUATION isolated replays, and parity between the event and
no-event stateful replays. The resulting receipt admits the library to evaluation replay under the distinct
validation NANO_LOCAL_VALIDATED (sim.cycle.local_replay). Certified replay, join, IR and publication stages are
unchanged and keep requiring CURRENT_CERTIFIED results, so they reject local results.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Final

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.certificate_contract import object_value
from sim.cycle.npu_trace_schema import Record, require

SCHEMA: Final = 'im2p-nano-local-cycle-validation'
VERSION: Final = 2
# Host-local authority code bound by a v2 receipt; a change to any of them invalidates the receipt.
VALIDATOR_SOURCES: Final = ('sim/cycle/local_validation.py', 'sim/cycle/local_replay.py',
                            'sim/cycle/nano_local_execution.py', 'sim/cycle/npu_result_admission.py',
                            'sim/cycle/npu_task_cache.py')
NANO_LOCAL_VALIDATED: Final = 'NANO_LOCAL_VALIDATED'
NANO_LOCAL_CANDIDATE: Final = 'NANO_LOCAL_CANDIDATE'
HEADERS: Final = ('im2p_cycle_model.h', 'im2p_cycle_service.h', 'im2p_cycle_sequence.h')
PROBE_WORKS: Final = 8


def sha256(path: Path) -> str:
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def digest(value: JsonValue) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def read_receipt(path: Path) -> Record:
    document = object_value(json.loads(path.read_text()), 'local validation receipt')
    require(document.get('schema') == SCHEMA and document.get('version') == VERSION,
            'unsupported local validation receipt')
    return document


def closure_matches(closure: Record, root: Path = ROOT) -> bool:
    files = object_value(closure.get('files'), 'source closure files')
    return bool(files) and all((root / name).is_file() and sha256(root / name) == value for name, value in files.items())


def admit_library(receipt_path: Path, library: Path) -> tuple[Record, str]:
    """The receipt must name these library bytes and a source closure identical to the current tree."""
    receipt = read_receipt(receipt_path)
    bound = object_value(receipt.get('library'), 'receipt library')
    require(bound.get('sha256') == sha256(library), 'library differs from the local validation receipt')
    require(closure_matches(object_value(receipt.get('source_closure'), 'receipt source closure')),
            'cycle-model source changed after local validation')
    validator = object_value(receipt.get('validator'), 'receipt validator')
    require(set(validator) == set(VALIDATOR_SOURCES) and
            all(sha256(ROOT / name) == validator[name] for name in VALIDATOR_SOURCES),
            'local authority source changed after local validation')
    status = receipt.get('status')
    require(status in ('PASS', 'CANDIDATE'), 'local validation receipt is not PASS')
    return receipt, NANO_LOCAL_VALIDATED if status == 'PASS' else NANO_LOCAL_CANDIDATE


def exported_api(library: Path) -> Record:
    """Every C function the public headers declare must resolve in the built library."""
    names: set[str] = set()
    declared: list[JsonValue] = []
    for header in HEADERS:
        text = (ROOT / 'sim/include' / header).read_text()
        names.update(re.findall(r'\b(im2p_cycle_[a-z0-9_]+)\s*\(', text))
    handle = ctypes.CDLL(str(library))
    declared.extend(name for name in sorted(names))
    missing: list[JsonValue] = [name for name in sorted(names) if not hasattr(handle, name)]
    headers: Record = {header: sha256(ROOT / 'sim/include' / header) for header in HEADERS}
    return {'status': 'PASS' if not missing else 'FAILED', 'declared_functions': declared,
            'declared_count': len(names), 'missing': missing, 'headers': headers}


def probe(library: Path, trace: Path) -> Record:
    """A few real trace works estimated twice; answers must be equal, integral and positive."""
    from sim.cycle import cli
    from sim.cycle.npu_trace import model_document
    from sim.cycle.npu_trace_integrity import read_records, start_trace
    records = read_records(trace)
    state = start_trace(records)
    rows: list[JsonValue] = []
    for record in records:
        work = state.consume(record)
        if work is None:
            continue
        document = model_document(state.run.profile, work)
        first, second = cli.estimate(library, document), cli.estimate(library, document)
        require(first.get('status') == 'PASS' and first == second, 'cycle probe is not deterministic')
        result = object_value(first['result'], 'probe result')
        require(all(isinstance(value, int) and not isinstance(value, bool) for value in result.values()) and
                int(str(result['total_cycles'])) > 0, 'cycle probe result is not a positive integer record')
        rows.append({'work_id': work.identity, 'result': result})
        if len(rows) == PROBE_WORKS:
            break
    require(len(rows) == PROBE_WORKS, 'probe trace has too few NPU works')
    return {'status': 'PASS', 'profile': state.run.profile, 'works': rows, 'answers_sha256': digest(rows)}


def ctest(build_dir: Path) -> Record:
    done = subprocess.run(['ctest', '--test-dir', str(build_dir), '--output-on-failure'],
                          capture_output=True, text=True, check=False, timeout=1800)
    summary: list[JsonValue] = [line for line in done.stdout.splitlines() if 'tests passed' in line or 'tests failed' in line]
    return {'status': 'PASS' if done.returncode == 0 else 'FAILED', 'exit_code': done.returncode,
            'summary': summary, 'build_dir': str(build_dir)}


def isolated_parity(library: Path, candidate: Path, trace: Path, directory: Path, workers: int) -> Record:
    """The serial local replay and its FAST_EVALUATION memo variant must publish identical bytes."""
    from sim.cycle import cli, local_replay
    from sim.cycle.npu_trace import ReplayOutputs
    artifacts = local_replay.LocalArtifacts(library, candidate)
    serial = ReplayOutputs(directory / 'serial.jsonl', directory / 'serial-summary.json')
    fast = ReplayOutputs(directory / 'fast.jsonl', directory / 'fast-summary.json')
    local_replay.replay(trace, artifacts, serial)
    summary, stats = local_replay.replay_fast(trace, artifacts, fast, workers)
    identical: Record = {'results': sha256(serial.results) == sha256(fast.results),
                 'summary': sha256(serial.summary) == sha256(fast.summary)}
    require(local_replay.cli is cli, 'fast replay did not restore the cycle module')
    return {'status': 'PASS' if all(value is True for value in identical.values()) and stats['fallback_calls'] == 0 else 'FAILED',
            'identical': identical, 'npu_work_count': summary['npu_work_count'],
            'isolated_cycle_sum': summary['isolated_cycle_sum'], 'fast': stats,
            'results_sha256': sha256(fast.results), 'summary_sha256': sha256(fast.summary)}


def task_cache_parity(library: Path, candidate: Path, trace: Path, directory: Path, workers: int) -> Record:
    """A replay filling a fresh per-work answer cache and one served only from it publish the uncached fast bytes."""
    from sim.cycle import local_replay
    from sim.cycle.npu_trace import ReplayOutputs
    artifacts, cache = local_replay.LocalArtifacts(library, candidate), directory / 'npu-task-cache.sqlite'
    runs: Record = {}
    for name in ('fill', 'resume'):
        outputs = ReplayOutputs(directory / f'cache-{name}.jsonl', directory / f'cache-{name}-summary.json')
        _, stats = local_replay.replay_fast(trace, artifacts, outputs, workers, cache)
        runs[name] = {'identical': sha256(outputs.results) == sha256(directory / 'fast.jsonl') and
                                   sha256(outputs.summary) == sha256(directory / 'fast-summary.json'),
                      'model_calls': stats['model_calls'], 'binding_misses': stats['binding_misses'],
                      'counters': stats['npu_task_cache']}
    fill, resume = object_value(runs['fill'], 'fill'), object_value(runs['resume'], 'resume')
    fill_counters, resume_counters = object_value(fill['counters'], 'fill'), object_value(resume['counters'], 'resume')
    ok = (fill['identical'] is True and resume['identical'] is True and resume['model_calls'] == 0 and
          fill_counters['persisted'] == fill['model_calls'] == fill['binding_misses'] and
          resume_counters['hit'] == resume['binding_misses'] and resume_counters['rejected'] == 0)
    return {'status': 'PASS' if ok else 'FAILED', **runs}


def stateful_parity(library: Path, trace: Path, directory: Path, budget: int) -> Record:
    """Event-materializing boundary replay and the no-event evaluation replay must agree record by record."""
    from sim.cycle import evaluation_stateful_replay
    from sim.tests.cycle import decode_trace_replay
    identity = directory / 'identity.json'
    identity.write_text(json.dumps({'trace': {'npu_trace': {'path': str(trace), 'sha256': sha256(trace)}}}) + '\n')
    events = decode_trace_replay.replay(library, identity, directory / 'events', budget, 'boundary')
    fast = evaluation_stateful_replay.replay(library, trace, directory / 'no-events', budget)
    parity = evaluation_stateful_replay.compare(directory / 'no-events/works.jsonl', directory / 'events/works.jsonl')
    ok = parity['identical'] is True and parity['compared_works'] == events['work_count'] == fast['work_count']
    return {'status': 'PASS' if ok else 'FAILED', 'work_count': events['work_count'],
            'event_count': events['event_count'], 'final_cursor': {'events': events['final_cursor'],
                                                                   'no_events': fast['final_cursor']},
            'parity': parity, 'event_hashes': events['hashes']}


def build(args: argparse.Namespace) -> Record:
    build_receipt = json.loads(args.build_receipt.read_text())
    require(build_receipt.get('schema') == 'im2p-cycle-model-build-receipt' and
            build_receipt.get('verification') == 'PASS', 'cycle-model build receipt is not a PASS receipt')
    bound = object_value(build_receipt.get('library'), 'build library')
    library = Path(str(bound['path'])).resolve(strict=True)
    require(sha256(library) == bound.get('sha256'), 'library differs from its build receipt')
    closure = object_value(build_receipt.get('source_closure'), 'build source closure')
    require(closure_matches(closure), 'cycle-model source changed after the build')
    trace = args.trace.resolve(strict=True)
    base: Record = {'schema': SCHEMA, 'version': VERSION, 'mode': NANO_LOCAL_VALIDATED,
                    'certification': 'NOT_CERTIFIED', 'status': 'CANDIDATE',
                    'library': {'path': str(library), 'sha256': sha256(library)},
                    'build_receipt': {'path': str(args.build_receipt.resolve()), 'sha256': sha256(args.build_receipt)},
                    'source_closure': {'files': closure['files'], 'closure_sha256': digest(closure['files'])},
                    'semantic_options': build_receipt.get('options'),
                    'semantic_options_sha256': digest(build_receipt.get('options')),
                    'platform': build_receipt.get('platform'), 'toolchain': build_receipt.get('toolchain'),
                    'source': build_receipt.get('source'),
                    'validator': {name: sha256(ROOT / name) for name in VALIDATOR_SOURCES},
                    'include_repo_dependency': closure.get('include_repo_dependency'),
                    'compiler': object_value(build_receipt.get('toolchain'), 'toolchain').get('cxx'),
                    'run_aware': 'LOCAL_CTEST_ONLY', 'hardware_contract': 'REPOSITORY_PROFILE_CONTRACT',
                    'reference_memory': None}
    checks: Record = {}
    with tempfile.TemporaryDirectory(prefix='local-validation-', dir=args.output.parent) as scratch:
        directory = Path(scratch)
        candidate = directory / 'candidate.json'
        candidate.write_text(json.dumps(base, indent=2, sort_keys=True) + '\n')
        checks['ctest'] = ctest(library.parent)
        checks['exported_api'] = exported_api(library)
        checks['probe'] = probe(library, trace)
        checks['isolated_parity'] = isolated_parity(library, candidate, trace, directory, args.workers)
        checks['task_cache_parity'] = task_cache_parity(library, candidate, trace, directory, args.workers)
        checks['stateful_parity'] = stateful_parity(library, trace, directory, args.stateful_budget)
    passed = all(object_value(value, name).get('status') == 'PASS' for name, value in checks.items())
    trace_record: Record = {'path': str(trace), 'sha256': sha256(trace)}
    results: Record = {name: digest(value) for name, value in checks.items()}
    receipt: Record = {**base, 'status': 'PASS' if passed else 'FAILED', 'checks': checks, 'trace': trace_record,
                       'check_result_sha256': results,
                       'not_claimed': ['RTL equivalence', 'CURRENT_CERTIFIED', 'publication readiness',
                                       'identity with any library built on another host']}
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    make = sub.add_parser('build')
    make.add_argument('--build-receipt', type=Path, required=True, help='campaign_build cycle-model build-receipt.json')
    make.add_argument('--trace', type=Path, required=True, help='bounded exact NPU trace for probe and parity')
    make.add_argument('--output', type=Path, required=True)
    make.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 2))
    make.add_argument('--stateful-budget', type=int, default=1 << 40)
    check = sub.add_parser('check')
    check.add_argument('--receipt', type=Path, required=True)
    check.add_argument('--library', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'build':
            require(not args.output.exists(), 'receipt output exists')
            receipt = build(args)
            args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
            print(json.dumps({'status': receipt['status'], 'mode': receipt['mode'],
                              'checks': {name: object_value(value, name)['status']
                                         for name, value in object_value(receipt['checks'], 'checks').items()},
                              'receipt_sha256': sha256(args.output)}, sort_keys=True))
            return 0 if receipt['status'] == 'PASS' else 1
        _, validation = admit_library(args.receipt, args.library)
        print(json.dumps({'validation': validation}))
        return 0
    except (OSError, ValueError, RuntimeError, TypeError) as error:
        print(f'local validation failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
