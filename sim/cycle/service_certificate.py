from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Final

from scripts.gemmini_replay_contract import compatible, hardware_contract
from scripts.gemmini_rtl_build_binding import verify_build
from sim.cycle.certificate_contract import PROFILES, array_value, object_value, read_document, validate_certificate
from sim.cycle.npu_trace_integrity import read_records, start_trace
from sim.cycle.npu_trace_schema import Record, text
from sim.cycle.production_sequence_certificate import CertifiedCase, validate_production_sequence
from sim.cycle.reconstruct_graph import sha256 as file_sha256
from sim.cycle.run_aware_certificate import validate_run_certificate
from sim.tests.cycle.service_boundary import Observation, parse_rows, validate_rows

ROOT: Final = Path(__file__).resolve().parents[2]
SCHEMA: Final = 'im2p-drained-service-certificate'
SCENARIO: Final = 'one-npu-drained-reference-memory-v1'
CORPUS: Final = ROOT / 'sim/tests/cycle/service_certificate_corpus.json'
CORPUS_SHA256: Final = '540ea992aff302b6abbb769ed25bd6ce02e5df58a92775244ba7f75d9daec007'
SOURCE_PATHS: Final = (
    'sim/cycle/c_api.cpp', 'sim/cycle/control_engine.cpp', 'sim/cycle/scheduled_work.cpp',
    'sim/cycle/timing_profile.hpp', 'sim/cycle/service_certificate.py',
    'sim/tests/cycle/service_boundary.py', 'sim/tests/cycle/service_boundary_probe.cpp',
    'sim/tests/cycle/service_certificate_probe.cpp',
    'sim/tests/cycle/service_certificate_build.py',
    'sim/common/gemmini_schedule.cpp', 'fpga/gemmini_hp1/host/test_ws_rtl.cpp',
    'fpga/gemmini_hp1/host/run_aware_rtl_driver.inc',
    'src/gemmini/control/src/main/scala/im2p/gemmini/HostCommandBridge.scala',
    'src/gemmini/control/src/main/scala/im2p/gemmini/ScaleBackingLoader.scala',
    'src/gemmini/control/src/main/scala/im2p/gemmini/UpstreamWsHp1Top.scala',
    'src/gemmini/control/src/main/scala/im2p/gemmini/UpstreamWsMemory.scala',
)
ARTIFACT_NAMES: Final = (
    'rtl_build_binding', 'boundary_report', 'boundary_log', 'boundary_binary',
    'guarded_command', 'guarded_log', 'guarded_binary',
)


class ServiceCertificateError(ValueError):
    def __init__(self, detail: str) -> None:
        super().__init__('service certificate: ' + detail)


@dataclass(frozen=True, slots=True)
class ServiceAdmission:
    certificate_sha256: str
    base_certificate_sha256: str
    run_certificate_sha256: str
    trace_sha256: str
    scenario_id: str
    profile: str
    work_count: int


def require(condition: bool, detail: str) -> None:
    if not condition:
        raise ServiceCertificateError(detail)


def artifact(row: Record, name: str) -> Path:
    entry = object_value(row.get(name), name)
    path = Path(text(entry, 'path'))
    require(path.is_absolute() and path.is_file() and entry.get('sha256') == file_sha256(path),
            name + ' artifact changed')
    return path


def validate_guarded_rows(raw: str) -> list[Observation]:
    rows = parse_rows(raw)
    pair_rows = [row for row in rows if row.sequence < 5]
    validate_rows(pair_rows)
    extra = [row for row in rows if row.sequence == 5]
    expected = {(5, period, phase, 0, work) for period in (3, 5)
                for phase in range(period) for work in (0, 1)}
    require(len(rows) == 176 and
            {(row.sequence, row.period, row.phase, row.reset_isolated, row.work) for row in extra} == expected and
            len(extra) == len(expected), 'primed sequence corpus incomplete')
    drained: dict[tuple[int, int, int, int, int], int] = {}
    for line in raw.splitlines():
        if not line.startswith('DRAINED '):
            continue
        fields = line.split()
        require(len(fields) == 7 and all(item.isdecimal() for item in fields[1:]),
                'malformed drained observation')
        key = (int(fields[1]), int(fields[2]), int(fields[3]), int(fields[4]), int(fields[5]))
        require(key not in drained, 'duplicate drained observation')
        drained[key] = int(fields[6])
    require(len(drained) == len(rows), 'missing drained-idle observations')
    for row in rows:
        key = (row.sequence, row.period, row.phase, row.reset_isolated, row.work)
        require(drained.get(key) == row.resource_ready and
                row.accepted % row.period == row.phase and
                row.model_done == row.result_ready and
                row.model_service_release == row.final_scale_release and
                row.model_resource_ready == row.resource_ready and
                row.result_ready < row.final_scale_release < row.resource_ready,
                'guarded result/release/resource mismatch')
        if row.reset_isolated == 0:
            require(row.backing_cycle_offset == 5, 'continuous backing epoch changed')
        if row.sequence == 5:
            require((row.m, row.n, row.k, row.run_count, row.slot,
                     row.initial_scratchpad_half, row.initial_accumulator_half) ==
                    ((2, 3, 37, 2, 0, 1, 1) if row.work == 0 else
                     (2, 3, 64, 0, 0, 1, 0)), 'primed carried state changed')
    for period in (3, 5):
        for phase in range(period):
            halves = {(row.initial_scratchpad_half, row.initial_accumulator_half)
                      for row in rows if row.reset_isolated == 0 and
                      row.period == period and row.phase == phase}
            require(halves == {(0, 0), (0, 1), (1, 0), (1, 1)},
                    'acceptance phase/buffer-half coverage incomplete')
    return rows


def validate_evidence(document: Record, library: Path) -> None:
    require(document.get('schema') == SCHEMA and type(document.get('version')) is int and
            document['version'] == 1 and
            document.get('status') == 'PASS' and document.get('scenario_id') == SCENARIO and
            document.get('corpus_sha256') == CORPUS_SHA256 and
            document.get('library_sha256') == file_sha256(library),
            'schema, corpus or library binding mismatch')
    require(file_sha256(CORPUS) == CORPUS_SHA256, 'independent service corpus changed')
    corpus = read_document(CORPUS)
    require(corpus.get('schema') == 'im2p-drained-service-corpus' and
            corpus.get('version') == 1 and corpus.get('revision') == 'one-npu-drained-hp1-v1' and
            corpus.get('profiles') == list(PROFILES) and corpus.get('read_ready_periods') == [3, 5] and
            corpus.get('observations_per_profile') == 176,
            'independent service corpus unsupported')
    sources = object_value(document.get('source_sha256'), 'source hashes')
    require(set(sources) == set(SOURCE_PATHS), 'service source closure incomplete')
    for name in SOURCE_PATHS:
        require(sources.get(name) == file_sha256(ROOT / name), 'service source changed: ' + name)
    entries = object_value(document.get('profiles'), 'profiles')
    require(set(entries) == set(PROFILES), 'service profile coverage incomplete')
    for profile in PROFILES:
        artifacts = object_value(entries[profile], profile)
        require(set(artifacts) == set(ARTIFACT_NAMES), 'service artifact closure incomplete')
        paths = {name: artifact(artifacts, name) for name in ARTIFACT_NAMES}
        build = paths['rtl_build_binding'].parent
        binding = verify_build(build, profile)
        report = read_document(paths['boundary_report'])
        raw = paths['boundary_log'].read_text()
        boundary_rows = parse_rows(raw)
        validate_rows(boundary_rows)
        require(report.get('schema') == 'im2p-npu-service-boundary-probe' and
                report.get('version') == 1 and report.get('status') == 'OBSERVATIONS_COMPLETE' and
                report.get('execution_kind') == 'FRESH_RTL_EXECUTION_WITH_VERIFIED_OBJECT_REUSE' and
                report.get('profile') == profile and report.get('observation_count') == 160 and
                report.get('observations') == [asdict(row) for row in boundary_rows] and
                report.get('result_mismatches') == [] and report.get('service_mismatches') == [] and
                report.get('logical_result_comparison') == 'PASS' and
                report.get('resource_model_comparison') == 'PASS' and
                report.get('rtl_build_binding_sha256') == file_sha256(paths['rtl_build_binding']) and
                report.get('cycle_library_sha256') == file_sha256(library) and
                report.get('probe_binary_sha256') == file_sha256(paths['boundary_binary']) and
                report.get('hardware_contract') == binding['hardware_contract'],
                'boundary evidence differs from raw current RTL')
        report_sources = object_value(report.get('source_sha256'), 'boundary sources')
        require(bool(report_sources) and all(sources.get(name) == digest for name, digest in report_sources.items()),
                'boundary probe source changed')
        command = array_value(json.loads(paths['guarded_command'].read_text()), 'guard command')
        require(str(ROOT / 'sim/tests/cycle/service_certificate_probe.cpp') in command and
                str(paths['guarded_binary']) in command and
                str(library.resolve(strict=True)) in command and
                str(build / 'rtl-test-obj/VIM2PGemminiWSHP1RtlTest__ALL.a') in command,
                'guarded compile command binding incomplete')
        _ = validate_guarded_rows(paths['guarded_log'].read_text())
    require(document.get('observations') == 176 * len(PROFILES) and
            document.get('result_mismatches') == 0 and
            document.get('service_mismatches') == 0 and
            document.get('drain_failures') == 0,
            'service summary incomplete')


def validate_service_certificate(
    path: Path, library: Path, trace: Path, *, base_certificate: Path,
    run_certificate: Path, timing: Record, initial_scratchpad_half: int,
    initial_accumulator_half: int,
) -> ServiceAdmission | tuple[ServiceAdmission, tuple[CertifiedCase, ...]]:
    document = read_document(path)
    production = document.get('version') == 2
    if production:
        fixture = artifact(document, 'fixture_certificate')
        fixture_document = read_document(fixture)
        validate_evidence(fixture_document, library)
        require(fixture_document.get('base_certificate_sha256') == file_sha256(base_certificate) and
                fixture_document.get('run_certificate_sha256') == file_sha256(run_certificate),
                'fixture base/run certificate artifact mismatch')
    else:
        validate_evidence(document, library)
    require(document.get('base_certificate_sha256') == file_sha256(base_certificate) and
            document.get('run_certificate_sha256') == file_sha256(run_certificate),
            'base/run certificate artifact mismatch')
    base = read_document(base_certificate)
    validate_certificate(base, library)
    require(validate_run_certificate(run_certificate, library) == 'PRODUCTION_GENERATED',
            'production run-aware certificate required')
    corpus = read_document(CORPUS)
    expected_timing = object_value(corpus['timing'], 'reference timing')
    require(set(timing) == set(expected_timing) | {'read_ready_period'} and
            all(type(value) is int for value in timing.values()) and
            all(timing[key] == value for key, value in expected_timing.items()) and
            timing['read_ready_period'] in (3, 5), 'unsupported reference-memory timing')
    require(type(initial_scratchpad_half) is int and type(initial_accumulator_half) is int and
            initial_scratchpad_half in (0, 1) and initial_accumulator_half in (0, 1),
            'initial halves outside certified state')
    records = read_records(trace)
    state = start_trace(records)
    require(state.run.trace_version == 2 and state.run.profile in PROFILES,
            'unsupported trace revision/profile')
    compatible(state.run.contract, object_value(object_value(base['hardware_contracts'], 'contracts')[state.run.profile], 'contract'))
    compatible(state.run.contract, hardware_contract(state.run.profile))
    work_count = 0
    residual = False
    for record in records:
        work = state.consume(record)
        if work is None:
            continue
        work_count += 1
        residual = residual or work.provenance == 'residual'
        require(work.scope != 'full' or record.get('host_slot') in (None, 0),
                'FULL work uses unsupported host slot')
        require(work.provenance != 'residual' or bool(work.runs),
                'residual work lacks current run view')
        require(work.provenance != 'residual' or record.get('host_slot') in (None, 0),
                'residual work uses unsupported host slot')
    _ = state.summary()
    require(work_count > 0, 'empty NPU service trace')
    period = timing['read_ready_period']
    require(period == 5 or not residual,
            'run-aware production evidence supports only read-ready period 5')
    admission = ServiceAdmission(file_sha256(path), file_sha256(base_certificate),
                                 file_sha256(run_certificate), file_sha256(trace),
                                 f'{SCENARIO}/p{period}/halves{initial_scratchpad_half}{initial_accumulator_half}',
                                 state.run.profile, work_count)
    if not production:
        return admission
    cases = validate_production_sequence(document, library, trace, timing,
                                         (initial_scratchpad_half, initial_accumulator_half))
    require(all(case.profile == admission.profile and len(case.works) == work_count for case in cases),
            'production sequence trace work coverage differs')
    return admission, cases
