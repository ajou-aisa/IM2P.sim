from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256 as new_sha256
from pathlib import Path
from typing import Final

from scripts.gemmini_rtl_build_binding import verify_build
from sim.cycle.certificate_contract import (
    PROFILES,
    array_value,
    number,
    object_value,
    read_document,
)
from sim.cycle.npu_trace_schema import Record
from sim.cycle.reconstruct_graph import sha256
from sim.cycle.sequence_binding import source_identity
from sim.tests.cycle.compositional_sequence_v2_stimulus import validate_stimulus

ROOT: Final = Path(__file__).resolve().parents[2]
HELPER_SOURCE: Final = 'sim/cycle/stateful_sequence_evidence.py'
RTL_OBJECT: Final = 'rtl-test-obj/VIM2PGemminiWSHP1RtlTest__ALL.a'


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    evidence_root: Path
    library: Path
    shared_library: Path
    base_parent: Path
    run_aware_parent: Path
    service_parent: Path
    current_evidence_input: Path | None = None
    domain_delta_input: Path | None = None


class StatefulCertificateError(ValueError):
    __slots__ = ('boundary', 'detail')

    boundary: str
    detail: str

    def __init__(self, boundary: str, detail: str) -> None:
        super().__init__(boundary, detail)
        self.boundary = boundary
        self.detail = detail

    def __str__(self) -> str:
        return f'{self.boundary}: {self.detail}'


def require(condition: bool, boundary: str, detail: str) -> None:
    if not condition:
        raise StatefulCertificateError(boundary, detail)


def reference(path: Path, digest: str) -> Record:
    require(path.is_absolute() and path.is_file() and sha256(path) == digest,
            'artifact', f'changed or missing: {path}')
    return {'path': str(path), 'sha256': digest}


def source_hashes(context: EvidenceContext, evidence: dict[str, Record]) -> Record:
    audit_pins = object_value(object_value(evidence['state_audit'].get('pins'), 'audit pins')
                              .get('current_source_sha256'), 'audited sources')
    first = object_value(array_value(evidence['aggregate']['cases'], 'cases')[0], 'first case')
    case_pins = object_value(first.get('source_binary_input_build_pins'), 'case pins')
    sources: Record = object_value(case_pins.get('source_sha256'), 'producer sources').copy()
    for name, digest in audit_pins.items():
        require(name not in sources or sources[name] == digest, 'source closure',
                f'producer and audit source differ: {name}')
        sources[name] = digest
    for profile in PROFILES:
        for name, digest in source_identity(context.library, profile).source_sha256:
            require(name not in sources or sources[name] == digest, 'source closure',
                    f'evidence and native source differ: {name}')
            sources[name] = digest
    sources[HELPER_SOURCE] = sha256(Path(__file__))
    for name, digest in sources.items():
        require(isinstance(name, str) and isinstance(digest, str) and
                sha256(ROOT / name) == digest,
                'source closure', f'changed: {name}')
    return sources


def raw_digests(path: Path) -> Record:
    hashes = {name: new_sha256() for name in ('raw', 'selected_event', 'queue_v2', 'boundary_v2')}
    with path.open('rb') as stream:
        for line in stream:
            hashes['raw'].update(line)
            if line.startswith((b'MODEL_EVENT ', b'RTL_EVENT ')):
                hashes['selected_event'].update(line)
            if line.startswith((b'MODEL_QUEUE_EDGE_V2 ', b'RTL_QUEUE_EDGE_V2 ')):
                hashes['queue_v2'].update(line)
            if line.startswith((b'MODEL_BOUNDARY_V2 ', b'RTL_BOUNDARY_V2 ')):
                hashes['boundary_v2'].update(line)
    require(all(value.digest() != new_sha256().digest() for name, value in hashes.items()
                if name != 'raw'), 'raw RTL', 'selected event, queue, or boundary stream absent')
    return {name: digest.hexdigest() for name, digest in hashes.items()}


def case_binding(case: Record, audited: Record) -> Record:
    declared = object_value(case.get('declared'), 'declared case')
    pins = object_value(case.get('source_binary_input_build_pins'), 'case pins')
    profile = str(case['profile'])
    require(profile == declared.get('profile') == audited.get('profile') and
            case.get('case_id') == declared.get('case_id') and
            case.get('status') in ('PASS', 'REUSED_VERIFIED') and
            case.get('first_failure') is None and case.get('first_mismatch') is None and
            case.get('one_instance_one_reset') is True and
            case.get('work_count') == declared.get('work_count') == case.get('numeric_pass_count') and
            number(case.get('maximum_tag_occupancy'), 'tag occupancy') <= 4 and
            case.get('tag_full_pressure_status') == 'NOT_RUN',
            'case scope', f'unsupported case: {profile}')
    report_path = Path(str(case['report_path']))
    root = report_path.parent
    stimulus_path = Path(str(object_value(audited.get('stimulus'), 'audited stimulus')['path']))
    build_path = Path(str(object_value(audited.get('rtl_build_binding'), 'audited RTL build')['path']))
    binding = verify_build(build_path.parent, profile)
    build_artifacts = object_value(binding.get('artifact_sha256'), 'RTL build artifacts')
    object_digest = build_artifacts.get(RTL_OBJECT)
    require(isinstance(object_digest, str), 'RTL build object', f'{profile} object missing')
    producer = object_value(read_document(report_path).get('producer_artifacts'), 'producer triplet')
    artifacts: Record = {
        'stimulus': reference(stimulus_path, str(declared['stimulus_file_sha256'])),
        'report': reference(report_path, str(case['report_sha256'])),
        'raw': {'path': str(case['raw_path']), 'sha256': str(case['raw_sha256'])},
        'binary': reference(root / 'compositional-probe', str(case['binary_sha256'])),
        'rtl_build_binding': reference(build_path, str(pins['rtl_build_binding_sha256'])),
        'rtl_object': reference(build_path.parent / RTL_OBJECT, str(object_digest)),
        'numeric_projection': reference(root / 'projection.txt', str(case['numeric_projection_sha256'])),
        'projection': reference(root / 'projection.json', sha256(root / 'projection.json')),
        'compile_command': reference(root / 'compile-command.json', sha256(root / 'compile-command.json')),
        'diagnostics': reference(root / 'rtl-diagnostics.log',
                                 str(read_document(report_path)['rtl_diagnostics_sha256'])),
    }
    for name in ('trace', 'lifecycle', 'semantic_graph'):
        item = object_value(producer.get(name), name)
        artifacts[name] = reference(Path(str(item['path'])), str(item['sha256']))
        require(item['sha256'] == object_value(pins['producer_triplet_sha256'], 'triplet')[name],
                'producer corpus', f'{profile}/{name} differs')
    stimulus = read_document(stimulus_path)
    validate_stimulus(stimulus, expected_stimulus_sha256=str(declared['stimulus_file_sha256']))
    works = [object_value(row, 'stimulus work') for row in array_value(stimulus.get('works'), 'works')]
    reported = read_document(report_path)
    first_work = object_value(array_value(reported.get('works'), 'reported works')[0], 'first work')
    require(stimulus.get('case_id') == case['case_id'] and
            stimulus.get('cycle_library_sha256') == pins['native_abi2_library_sha256'] and
            [work['work_id'] for work in works] == declared['work_ids'] and
            [work['port_offer_cycle'] for work in works] == declared['offer_cycles'] and
            reported.get('works') == case.get('works_endpoints_counters_halves') and
            reported.get('rtl_log_sha256') == case['raw_sha256'] and
            reported.get('binary_sha256') == case['binary_sha256'] and
            (first_work.get('initial_scratchpad_half'), first_work.get('initial_accumulator_half')) == (0, 0),
            'work/run/offer', f'{profile} producer or RTL report differs')
    raw = raw_digests(Path(str(case['raw_path'])))
    require(raw['raw'] == case['raw_sha256'], 'raw RTL', f'{profile} changed')
    work_inputs = [{key: work[key] for key in ('work_id', 'work_binding', 'input', 'runs',
                                                'original_k', 'request_available_cycle',
                                                'port_offer_cycle')} for work in works]
    result: Record = {'case_id': case['case_id'], 'profile': profile, 'declared': declared,
            'work_inputs_sha256': new_sha256(json.dumps(work_inputs, sort_keys=True,
                                                        separators=(',', ':')).encode()).hexdigest(),
            'artifacts': artifacts, 'raw_digests': raw,
            'comparison': {key: case[key] for key in ('selected_event_parity_count',
                'selected_event_counts', 'queue_v2_parity_count', 'boundary_v2_model_count',
                'boundary_v2_rtl_count', 'numeric_pass_count', 'maximum_tag_occupancy',
                'first_failure', 'first_mismatch')}}
    return result
