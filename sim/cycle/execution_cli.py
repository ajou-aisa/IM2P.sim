from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import sqlite3

from sim.cycle.certificate_contract import read_document
from sim.cycle.execution_adapter import AdapterFiles, adapt
from sim.cycle.execution_cycle_provider import CycleServiceProvider, ReferenceMemoryScenario
from sim.cycle.execution_ir import ensure, ir_record, parse_ir
from sim.cycle.execution_services import NpuProvider, NpuService, PhaseTable, parse_services, services_record
from sim.cycle.npu_trace_schema import Record, integer, object_value, text
from sim.cycle.reconstruct_graph import array, fields, sha256
from sim.cycle.scheduler import Scenario, ScheduleInputs, schedule


class Arguments(argparse.Namespace):
    action: str = ''
    dataset: Path = Path()
    lifecycle: Path = Path()
    npu_results: Path = Path()
    join_summary: Path = Path()
    output: Path = Path()
    bundle: Path = Path()
    phase_table: Path | None = None
    frequency_hz: int = 0
    synthetic: bool = False
    clock_selection: Path | None = None
    profile: str = ''
    application: Path | None = None
    cycle_library: Path | None = None
    npu_trace: Path | None = None
    timing: Path | None = None
    initial_scratchpad_half: int | None = None
    initial_accumulator_half: int | None = None
    streaming: bool = False


def phase_table(document: Record) -> PhaseTable:
    fields(document, {'schema', 'version', 'scope', 'period', 'samples'})
    ensure(document['schema'] == 'im2p-service-phase-table' and integer(document, 'version') == 1,
           'unsupported phase table')
    ensure(document['scope'] == 'SYNTHETIC_ONLY', 'finite observations are not a certified general service provider')
    period = integer(document, 'period')
    ensure(period > 0, 'positive phase period required')
    samples: dict[tuple[str, str, int], NpuService] = {}
    for value in array(document['samples']):
        row = object_value(value)
        fields(row, {'profile', 'request_sha256', 'phase', 'result_ready_cycles', 'resource_ready_cycles', 'evidence_id'})
        phase = integer(row, 'phase')
        ensure(phase < period, 'phase outside declared period')
        key = (text(row, 'profile'), text(row, 'request_sha256'), phase)
        ensure(key not in samples, 'duplicate work/phase service')
        samples[key] = NpuService(integer(row, 'result_ready_cycles'), integer(row, 'resource_ready_cycles'),
                                  text(row, 'evidence_id'))
    return PhaseTable(period, samples)


def publish(path: Path, document: Record) -> None:
    ensure(not path.exists(), 'output must be new')
    with tempfile.TemporaryDirectory(prefix='execution-', dir=path.parent) as directory:
        staged = Path(directory) / 'result.json'
        with staged.open('x', encoding='utf-8') as stream:
            json.dump(document, stream, sort_keys=True, indent=2, allow_nan=False)
            _ = stream.write('\n')
        os.link(staged, path)


def provider(arguments: Arguments) -> NpuProvider:
    if arguments.phase_table is not None:
        return phase_table(read_document(arguments.phase_table))
    from sim.cycle.execution_ir import ExecutionError
    if (arguments.cycle_library is None or arguments.npu_trace is None or arguments.timing is None or
            arguments.initial_scratchpad_half is None or arguments.initial_accumulator_half is None):
        raise ExecutionError('cycle service requires library, exact trace, timing and explicit initial halves')
    scenario = ReferenceMemoryScenario(read_document(arguments.timing), arguments.initial_scratchpad_half,
                                       arguments.initial_accumulator_half)
    return CycleServiceProvider(arguments.cycle_library, arguments.npu_trace, scenario)


def main() -> int:
    parser = argparse.ArgumentParser(description='Explicit execution IR and diagnostic ready-set scheduling; no inferred latency.')
    actions = parser.add_subparsers(dest='action', required=True)
    adapter = actions.add_parser('adapt', help='Bind structural join to explicit lifecycle semantics')
    for name in ('dataset', 'lifecycle', 'npu-results', 'join-summary', 'output'):
        _ = adapter.add_argument('--' + name, type=Path, required=True)
    _ = adapter.add_argument('--application', type=Path, help='PoTal sample_accept measurements bound by lifecycle.application')
    _ = adapter.add_argument('--streaming', action='store_true', help='Publish bounded-memory SQLite execution IR instead of JSON bundle')
    runner = actions.add_parser('schedule', help='Schedule explicit service boundaries; synthetic tables are diagnostic only')
    for name in ('bundle', 'output'):
        _ = runner.add_argument('--' + name, type=Path, required=True)
    timing_provider = runner.add_mutually_exclusive_group(required=True)
    _ = timing_provider.add_argument('--phase-table', type=Path)
    _ = timing_provider.add_argument('--cycle-library', type=Path)
    for name in ('npu-trace', 'timing'):
        _ = runner.add_argument('--' + name, type=Path)
    for name in ('initial-scratchpad-half', 'initial-accumulator-half'):
        _ = runner.add_argument('--' + name, type=int, choices=(0, 1))
    _ = runner.add_argument('--frequency-hz', type=int, required=True)
    _ = runner.add_argument('--synthetic', action='store_true', help='Label all schedule output SYNTHETIC; never paper latency')
    _ = runner.add_argument('--clock-selection', type=Path)
    _ = runner.add_argument('--profile', default='')
    args = parser.parse_args(namespace=Arguments())
    try:
        if args.action == 'adapt':
            summary, lifecycle = read_document(args.join_summary), read_document(args.lifecycle)
            ensure(summary.get('status') == 'PASS' and summary.get('scope') == 'structural-three-source-reconstruction',
                   'successful current structural join required')
            fingerprints = object_value(summary['decode_token_fingerprint_matches'])
            ensure(bool(fingerprints) and all(value is True for value in fingerprints.values()),
                   'decode trajectory mismatch or missing fingerprints')
            ensure(object_value(object_value(summary['source_artifacts'])['npu_results'])['sha256'] == sha256(args.npu_results),
                   'join/NPU result binding mismatch')
            files = AdapterFiles(args.dataset, args.lifecycle, args.npu_results, args.application)
            if args.streaming:
                from sim.cycle.execution_stream import adapt_stream
                result = adapt_stream(files, args.output)
                print(json.dumps(result, sort_keys=True))
                return 0
            ir, services = adapt(files)
            result: Record = {'schema': 'im2p-execution-bundle', 'version': 1, 'ir': ir_record(ir),
                              'services': services_record(services), 'join_summary_sha256': sha256(args.join_summary),
                              'lifecycle_sha256': sha256(args.lifecycle), 'dataset_sha256': lifecycle['dataset_sha256']}
        else:
            scenario = Scenario(args.frequency_hz, 'SYNTHETIC' if args.synthetic else 'RECONSTRUCTED',
                                args.clock_selection, args.profile)
            ensure(args.synthetic or args.clock_selection is not None, 'validated clock artifact required')
            with args.bundle.open('rb') as stream:
                sqlite_input = stream.read(16) == b'SQLite format 3\x00'
            if sqlite_input:
                from sim.cycle.scheduler_sqlite import SqliteScheduleInputs, schedule_sqlite
                result = schedule_sqlite(args.bundle, args.output, SqliteScheduleInputs(provider(args), scenario))
                print(json.dumps(result, sort_keys=True))
                return 0
            bundle = read_document(args.bundle)
            ensure(bundle.get('schema') == 'im2p-execution-bundle' and integer(bundle, 'version') == 1, 'unsupported execution bundle')
            inputs = ScheduleInputs(parse_ir(object_value(bundle['ir'])), parse_services(object_value(bundle['services'])),
                                    provider(args))
            result = schedule(inputs, scenario).record()
            result['input_bundle_sha256'] = sha256(args.bundle)
            result['phase_table_sha256'] = sha256(args.phase_table) if args.phase_table is not None else None
            result['cycle_library_sha256'] = sha256(args.cycle_library) if args.cycle_library is not None else None
        publish(args.output, result)
        print(json.dumps({'status': 'PASS', 'output': str(args.output), 'schema': result['schema']}))
    except (OSError, ValueError, KeyError, AttributeError, sqlite3.Error) as error:
        print(f'execution failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
