from __future__ import annotations

# allow: SIZE_OK — existing parser/command surface stays together to preserve legacy CLI and publication bindings.
import argparse
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path

from sim.cycle.certificate_contract import read_document
from sim.cycle.execution_adapter import AdapterFiles, adapt
from sim.cycle.execution_cycle_provider import (
    CycleServiceProvider,
    ReferenceMemoryScenario,
)
from sim.cycle.execution_ir import ExecutionError, ensure, ir_record, parse_ir
from sim.cycle.execution_sequence_provider import (
    DiagnosticStatefulProvider,
    StatefulSequenceProvider,
)
from sim.cycle.execution_services import (
    NpuService,
    PhaseTable,
    parse_services,
    services_record,
)
from sim.cycle.npu_trace_schema import Record, integer, object_value, text
from sim.cycle.reconstruct_graph import array, fields, sha256
from sim.cycle.scheduler import (
    Scenario,
    ScheduleInputs,
    TimingProvider,
    schedule,
    schedule_source_binding,
    service_binding,
)
from sim.cycle.stateful_sequence_evidence import EvidenceContext, reference


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
    schedule: Path = Path()
    service_certificate: Path | None = None
    cycle_certificate: Path | None = None
    run_aware_certificate: Path | None = None
    stateful_sequence_certificate: Path | None = None
    stateful_evidence_root: Path | None = None
    stateful_diagnostic: bool = False


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


def publish(path: Path, document: Record, bound: TimingProvider | None = None) -> None:
    ensure(not path.exists(), 'output must be new')
    with tempfile.TemporaryDirectory(prefix='execution-', dir=path.parent) as directory:
        staged = Path(directory) / 'result.json'
        with staged.open('x', encoding='utf-8') as stream:
            json.dump(document, stream, sort_keys=True, indent=2, allow_nan=False)
            _ = stream.write('\n')
        if isinstance(bound, (DiagnosticStatefulProvider, StatefulSequenceProvider)):
            bound.verify_complete()
            ensure(all(document.get(key) == digest for key, digest in schedule_source_binding().items()),
                   'JSON schedule source binding changed before publication')
        os.link(staged, path)


def provider(arguments: Arguments) -> TimingProvider:
    ensure(not (arguments.service_certificate and arguments.stateful_sequence_certificate),
           'service and stateful certificates are mutually exclusive')
    ensure(not arguments.stateful_diagnostic or arguments.stateful_sequence_certificate is not None,
           'stateful diagnostic requires stateful sequence certificate')
    if arguments.phase_table is not None:
        ensure(arguments.stateful_sequence_certificate is None, 'stateful certificate requires native cycle library')
        return phase_table(read_document(arguments.phase_table))
    if (arguments.cycle_library is None or arguments.npu_trace is None or arguments.timing is None or
            arguments.initial_scratchpad_half is None or arguments.initial_accumulator_half is None):
        raise ExecutionError('cycle service requires library, exact trace, timing and explicit initial halves')
    scenario = ReferenceMemoryScenario(read_document(arguments.timing), arguments.initial_scratchpad_half,
                                       arguments.initial_accumulator_half)
    if arguments.stateful_sequence_certificate is not None:
        if arguments.stateful_evidence_root is None:
            raise ExecutionError('stateful evidence root required')
        certificate = read_document(arguments.stateful_sequence_certificate)

        def path_from(row: Record) -> Path:
            path = Path(text(row, 'path'))
            reference(path, text(row, 'sha256'))
            return path

        parents = object_value(certificate['parents'])
        memory = object_value(certificate['reference_memory'])
        ensure(memory['timing'] == scenario.timing and memory['initial_scratchpad_half'] == arguments.initial_scratchpad_half and
               memory['initial_accumulator_half'] == arguments.initial_accumulator_half,
               'stateful certificate reference memory mismatch')
        context = EvidenceContext(arguments.stateful_evidence_root, path_from(object_value(certificate['library'])),
            path_from(object_value(certificate['shared_library'])), path_from(object_value(parents['base'])),
            path_from(object_value(parents['run_aware'])), path_from(object_value(parents['service'])),
             path_from(object_value(certificate['evidence_input'])) if 'evidence_input' in certificate else None,
             path_from(object_value(object_value(object_value(certificate['domain_delta'])['artifacts'])['input']))
             if certificate['version'] == 3 else None)
        constructor = DiagnosticStatefulProvider if arguments.stateful_diagnostic else StatefulSequenceProvider
        return constructor(arguments.cycle_library, arguments.npu_trace, arguments.stateful_sequence_certificate, context)
    return CycleServiceProvider(arguments.cycle_library, arguments.npu_trace, scenario,
                                service_certificate=arguments.service_certificate,
                                base_certificate=arguments.cycle_certificate,
                                run_certificate=arguments.run_aware_certificate)


def schedule_json(arguments: Arguments, bound: TimingProvider, scenario: Scenario) -> Record:
    bundle_digest = sha256(arguments.bundle)
    bundle = read_document(arguments.bundle)
    ensure(bundle.get('schema') == 'im2p-execution-bundle' and integer(bundle, 'version') == 1,
           'unsupported execution bundle')
    ir = parse_ir(object_value(bundle['ir']))
    if scenario.scope != 'SYNTHETIC':
        ensure(ir.scope == 'BOUND_DATASET' and bundle.get('dataset_sha256') == ir.source_sha256,
               'reconstruction requires bound dataset input')
    result = schedule(ScheduleInputs(ir, parse_services(object_value(bundle['services'])), bound), scenario).record()
    ensure(sha256(arguments.bundle) == bundle_digest, 'JSON bundle changed during scheduling')
    result['input_bundle_sha256'] = bundle_digest
    result['phase_table_sha256'] = sha256(arguments.phase_table) if arguments.phase_table is not None else None
    result['cycle_library_sha256'] = sha256(arguments.cycle_library) if arguments.cycle_library is not None else None
    result['clock_selection_sha256'] = sha256(arguments.clock_selection) if arguments.clock_selection is not None else None
    return result


def execution_scenario(arguments: Arguments, *, verifying: bool = False) -> Scenario:
    from scripts.evaluation_clock import load_selection
    if arguments.stateful_diagnostic:
        ensure(not arguments.synthetic and arguments.clock_selection is None and bool(arguments.profile),
               'stateful diagnostic requires explicit profile and configured test clock only')
        return Scenario(arguments.frequency_hz, 'STATEFUL_DIAGNOSTIC', None, arguments.profile)
    if arguments.synthetic:
        ensure(not verifying, 'synthetic schedule is not certified verification')
        return Scenario(arguments.frequency_hz, 'SYNTHETIC')
    if arguments.clock_selection is None or not arguments.profile:
        raise ExecutionError('profile and validated operating clock required')
    frequency = load_selection(arguments.clock_selection, arguments.profile).frequency_hz if verifying else arguments.frequency_hz
    return Scenario(frequency, 'RECONSTRUCTED', arguments.clock_selection, arguments.profile)


def verify_schedule(arguments: Arguments) -> Record:
    scenario = execution_scenario(arguments, verifying=True)
    with ExitStack() as resources:
        bound = provider(arguments)
        if isinstance(bound, (DiagnosticStatefulProvider, StatefulSequenceProvider)):
            resources.enter_context(bound)
        return _verify_schedule(arguments, bound, scenario)


def _verify_schedule(arguments: Arguments, bound: TimingProvider, scenario: Scenario) -> Record:
    from sim.cycle.scheduler_sqlite import SqliteScheduleInputs, verify_schedule_sqlite
    with arguments.bundle.open('rb') as stream:
        sqlite_input = stream.read(16) == b'SQLite format 3\x00'
    with arguments.schedule.open('rb') as stream:
        sqlite_output = stream.read(16) == b'SQLite format 3\x00'
    ensure(sqlite_input == sqlite_output, 'bundle/schedule storage kind mismatch')
    from sim.cycle import scheduler_sqlite
    consumer = Path(scheduler_sqlite.__file__) if sqlite_input else None
    sources = schedule_source_binding(consumer)
    if sqlite_input:
        schedule_digest = verify_schedule_sqlite(arguments.bundle, arguments.schedule,
                                                 SqliteScheduleInputs(bound, scenario))
    else:
        schedule_digest = sha256(arguments.schedule)
        expected = schedule_json(arguments, bound, scenario)
        actual = read_document(arguments.schedule)
        ensure(json.dumps(actual, sort_keys=True, separators=(',', ':'), allow_nan=False) ==
               json.dumps(expected, sort_keys=True, separators=(',', ':'), allow_nan=False),
               'JSON schedule node/endpoint or service/clock/source binding mismatch')
        ensure(sha256(arguments.schedule) == schedule_digest, 'JSON schedule changed during verification')
    if not isinstance(bound, (CycleServiceProvider, DiagnosticStatefulProvider, StatefulSequenceProvider)):
        raise ExecutionError('typed certified schedule provider required')
    if isinstance(bound, (DiagnosticStatefulProvider, StatefulSequenceProvider)):
        bound.verify_complete()
        ensure(sources == schedule_source_binding(consumer), 'schedule source binding changed during verification')
    return {'status': 'PASS', 'schema': 'im2p-execution-schedule-verification',
            'version': 2 if isinstance(bound, (DiagnosticStatefulProvider, StatefulSequenceProvider)) else 1,
            'schedule_sha256': schedule_digest, 'service_binding': service_binding(bound, scenario),
            'scheduled_npu_work_count': len(bound.requests)}


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
    _ = runner.add_argument('--profile', default='')
    verifier = actions.add_parser('verify-schedule', help='Revalidate a certified schedule and its exact NPU services')
    for name in ('schedule', 'bundle', 'cycle-library', 'npu-trace', 'timing'):
        _ = verifier.add_argument('--' + name, type=Path, required=True)
    _ = verifier.add_argument('--profile', required=True)
    for name in ('initial-scratchpad-half', 'initial-accumulator-half'):
        _ = verifier.add_argument('--' + name, type=int, choices=(0, 1), required=True)
    _ = verifier.add_argument('--frequency-hz', type=int, default=0, help='Explicit configured test clock for diagnostic verification')
    for command in (runner, verifier):
        certificates = command.add_mutually_exclusive_group()
        for name in ('service-certificate', 'stateful-sequence-certificate'):
            _ = certificates.add_argument('--' + name, type=Path)
        for name in ('cycle-certificate', 'run-aware-certificate', 'clock-selection', 'stateful-evidence-root'):
            _ = command.add_argument('--' + name, type=Path)
        mode = command.add_mutually_exclusive_group()
        _ = mode.add_argument('--stateful-diagnostic', action='store_true', help='Native diagnostic test clock only; never reconstructed latency')
        _ = mode.add_argument('--synthetic', action='store_true', help='Label output SYNTHETIC; never paper latency')
    args = parser.parse_args(namespace=Arguments())
    try:
        if args.action == 'verify-schedule':
            print(json.dumps(verify_schedule(args), sort_keys=True))
            return 0
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
            scenario = execution_scenario(args)
            with ExitStack() as resources:
                bound = provider(args)
                if isinstance(bound, (DiagnosticStatefulProvider, StatefulSequenceProvider)):
                    resources.enter_context(bound)
                with args.bundle.open('rb') as stream:
                    sqlite_input = stream.read(16) == b'SQLite format 3\x00'
                if sqlite_input:
                    from sim.cycle.scheduler_sqlite import (
                        SqliteScheduleInputs,
                        schedule_sqlite,
                    )
                    result = schedule_sqlite(args.bundle, args.output, SqliteScheduleInputs(bound, scenario))
                else:
                    result = schedule_json(args, bound, scenario)
                    publish(args.output, result, bound)
                print(json.dumps(result if sqlite_input else
                                 {'status': 'PASS', 'output': str(args.output), 'schema': result['schema']}, sort_keys=True))
                return 0
        publish(args.output, result)
        print(json.dumps({'status': 'PASS', 'output': str(args.output), 'schema': result['schema']}))
    except (OSError, ValueError, KeyError, AttributeError, sqlite3.Error) as error:
        print(f'execution failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
