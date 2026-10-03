from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Final

from scripts.gemmini_rtl_build_binding import verify_build
from sim.cycle.certificate_contract import array_value, object_value, read_document
from sim.cycle.cli import estimate_service
from sim.cycle.npu_trace import model_document, work_binding
from sim.cycle.npu_trace_integrity import read_records, start_trace
from sim.cycle.npu_trace_schema import Record, integer, text
from sim.cycle.reconstruct_graph import sha256
from sim.tests.cycle.production_sequence_work import (
    SOURCE_PATHS as RUNNER_SOURCE_PATHS, numeric_projection_text, parse_probe_log,
    project_inputs, validate_probe_log,
)
from sim.tests.cycle.production_tag_carry import proof_document
from sim.tests.cycle.rtl_hardening import SELECTED_EVENTS

ROOT: Final = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class CertifiedWork:
    work_id: int
    work_binding: str
    accepted_phase: int
    initial_scratchpad_half: int
    initial_accumulator_half: int
    result_ready_cycles: int
    final_scale_release_cycles: int
    resource_ready_cycles: int
    next_scratchpad_half: int
    next_accumulator_half: int


@dataclass(frozen=True, slots=True)
class CertifiedCase:
    case_id: str
    profile: str
    period: int
    works: tuple[CertifiedWork, ...]


def require(condition: bool, detail: str) -> None:
    if not condition:
        raise ValueError('production service certificate: ' + detail)


def reference(value: Record, label: str) -> Path:
    path = Path(text(value, 'path'))
    require(path.is_absolute() and path.is_file() and value.get('sha256') == sha256(path),
            label + ' artifact changed')
    return path


def compare_raw_work(row: Record, projected: Record, answer: Record, request_document: Record,
                     ordinal: int, period: int, halves: tuple[int, int],
                     inert_tag_carry_proved: bool = False) -> CertifiedWork:
    request = object_value(request_document['request'], 'model request')
    service = object_value(answer.get('service'), 'model service')
    result = object_value(answer.get('result'), 'model result')
    accepted = integer(row, 'accepted')
    binding = text(projected, 'work_binding')
    trace = object_value(projected['trace_record'], 'producer trace work')
    inputs = object_value(projected['input'], 'producer geometry')
    require(row.get('ordinal') == ordinal and row.get('work_id') == projected.get('work_id') and
            row.get('work_binding') == binding and row.get('slot') == projected.get('slot') and
            row.get('accepted_phase') == accepted % period == projected.get('accepted_phase') and
            row.get('backing_cycle_offset') == 5 and
            (row.get('initial_scratchpad_half'), row.get('initial_accumulator_half')) == halves and
            row.get('numeric_pass') is True,
            'raw work identity, phase, timing or initial halves differ')
    for raw_key, producer_value in (
        ('parent_id', trace['parent_id']), ('call_id', trace['call_id']),
        ('stripe_id', trace['stripe_id'] if trace['stripe_id'] is not None else 0),
        ('row_begin', trace['row_begin']), ('parent_m', trace['parent_m']),
        ('m', inputs['m']), ('n', inputs['n']), ('k', inputs['k']),
        ('tile_i', inputs['tile_i_count']), ('tile_j', inputs['tile_j_count']),
        ('tile_k', inputs['tile_k_count'])):
        require(row.get(raw_key) == producer_value, 'raw work geometry differs: ' + raw_key)
    for flag in ('drain_work_ready', 'drain_busy_clear', 'drain_memory', 'drain_writeback',
                 'drain_controller', 'drain_backing_reads', 'drain_backing_writes',
                 'metadata_queue_empty', 'mesh_row_queue_empty', 'mesh_request_idle'):
        require(row.get(flag) is True, 'RTL internal drain incomplete: ' + flag)
    empty = (row.get('internal_queue_drain_observed') is True and
             row.get('mesh_tag_queue_len') == row.get('passive_mesh_tag_queue_len') == 0)
    inert = (row.get('internal_queue_drain_observed') is False and inert_tag_carry_proved and
             row.get('mesh_tag_queue_len') == row.get('passive_mesh_tag_queue_len') == 1)
    require((empty or inert) and integer(row, 'passive_drain_cycle') >= integer(row, 'resource_ready'),
            'RTL internal drain incomplete: mesh tag carry proof')
    require((request.get('accepted_cycle'), request.get('initial_scratchpad_half'),
             request.get('initial_accumulator_half')) == (accepted, *halves),
            'recomputed model request differs')
    for rtl_key, model_key in (('result_ready', 'result_ready_cycle'),
                               ('final_scale_release', 'final_scale_release_cycle'),
                               ('resource_ready', 'resource_ready_cycle'),
                               ('next_scratchpad_half', 'next_scratchpad_half'),
                               ('next_accumulator_half', 'next_accumulator_half')):
        require(row.get(rtl_key) == service.get(model_key) == row.get('model_' + rtl_key),
                'RTL/model service endpoint or next half differs: ' + rtl_key)
    releases = sum(object_value(event, 'model event').get('type') == 'scale_release'
                   for event in array_value(answer.get('events'), 'model events'))
    for rtl_key, model_key in (('submissions', 'loop_count'),
                               ('scale_read_requests', 'scale_request_count'),
                               ('scale_read_responses', 'scale_response_count'),
                               ('load_requests', 'load_request_count'),
                               ('load_responses', 'load_response_count'),
                               ('store_requests', 'store_request_count'),
                               ('store_responses', 'store_response_count')):
        require(row.get(rtl_key) == result.get(model_key) == row.get('model_' + rtl_key),
                'RTL/model service counter differs: ' + rtl_key)
    require(row.get('scale_release_count') == releases == row.get('model_scale_release_count') and
            accepted < integer(row, 'result_ready') < integer(row, 'final_scale_release') <
            integer(row, 'resource_ready'), 'scale release count or endpoint order differs')
    return CertifiedWork(integer(row, 'work_id'), binding, integer(row, 'accepted_phase'),
                         *halves, integer(row, 'result_ready') - accepted,
                         integer(row, 'final_scale_release') - accepted,
                         integer(row, 'resource_ready') - accepted,
                         integer(row, 'next_scratchpad_half'), integer(row, 'next_accumulator_half'))


def validate_case(report_path: Path, library: Path, profile: str, phases: tuple[int, ...],
                  pinned: Record, timing: Record) -> CertifiedCase:
    report = read_document(report_path)
    root = report_path.parent
    require(report.get('schema') == 'im2p-producer-continuous-rtl-run' and
            report.get('version') == 1 and report.get('status') == 'PASS' and
            report.get('profile') == profile and report.get('period') == 5 and
            report.get('phases') == list(phases) and report.get('work_count') == 4 and
            report.get('instance_count') == 1 and report.get('reset_count') == 1 and
            report.get('event_plus_one_mutation_rejected') is True,
            'continuous RTL report scope or mutation proof incomplete')
    source_hashes = object_value(report.get('source_sha256'), 'runner sources')
    require(set(source_hashes) == set(RUNNER_SOURCE_PATHS) and
            all(source_hashes[name] == sha256(ROOT / name) for name in RUNNER_SOURCE_PATHS),
            'runner source closure changed')
    producer = object_value(report.get('producer_artifacts'), 'producer artifacts')
    require(set(producer) == {'trace', 'lifecycle', 'semantic_graph'},
            'producer artifact closure incomplete')
    artifacts = {name: reference(object_value(value, name), name) for name, value in producer.items()}
    require(all(object_value(producer[name], name).get('sha256') == pinned.get(name + '_sha256')
                for name in ('trace', 'lifecycle', 'semantic_graph')),
            'producer corpus digest differs')
    require(reference(object_value(report.get('cycle_library'), 'cycle library'), 'cycle library') ==
            library.resolve(strict=True), 'sequence uses other cycle library')
    build_binding = reference(object_value(report.get('rtl_build_binding'), 'RTL build'), 'RTL build')
    build = build_binding.parent
    binding = verify_build(build, profile)
    projection_path = root / 'projection.json'
    numeric_path = root / 'projection.txt'
    command_path = root / 'compile-command.json'
    binary_path = root / 'production-sequence-probe'
    raw_path = root / 'rtl.log'
    for path, key in ((projection_path, 'projection_sha256'),
                      (numeric_path, 'numeric_projection_sha256'),
                      (command_path, 'compile_command_sha256'),
                      (binary_path, 'binary_sha256'), (raw_path, 'rtl_log_sha256')):
        require(path.is_file() and report.get(key) == sha256(path), 'RTL artifact changed: ' + key)
    command = array_value(json.loads(command_path.read_text()), 'compile command')
    require(all(isinstance(value, str) for value in command) and
            str(ROOT / RUNNER_SOURCE_PATHS[0]) in command and str(binary_path) in command and
            str(library.resolve(strict=True)) in command and
            str(build / 'rtl-test-obj/VIM2PGemminiWSHP1RtlTest__ALL.a') in command,
            'official sequence probe compile route differs')
    projection = read_document(projection_path)
    recomputed = project_inputs(artifacts['trace'], artifacts['lifecycle'],
                                artifacts['semantic_graph'], phases)
    require(projection == recomputed and projection.get('profile') == profile and
            projection.get('hardware_contract') == binding['hardware_contract'] and
            numeric_path.read_text() == numeric_projection_text(recomputed, 5),
            'producer projection or numeric RTL input differs')
    projected = [object_value(value, 'projected work') for value in
                 array_value(projection.get('works'), 'works')]
    raw = raw_path.read_text()
    expected = [(integer(row, 'work_id'), text(row, 'work_binding')) for row in projected]
    observed = validate_probe_log(raw, expected)
    run, _, rtl_events, logged_model_events = parse_probe_log(raw)
    require(run.get('period') == 5 and run.get('work_count') == 4 and
            report.get('works') == observed, 'raw same-instance work coverage differs')
    proof_path = root / 'tag-carry-proof.json'
    require(proof_path.is_file() and report.get('tag_carry_proof_sha256') == sha256(proof_path),
            'inert tag carry proof artifact changed')
    proof = read_document(proof_path)
    require(proof == proof_document(observed, sha256(raw_path)) and proof.get('status') == 'PASS' and
            proof.get('id_wrap_exercised') is True,
            'inert tag carry proof missing or inconsistent with raw RTL')
    records = read_records(artifacts['trace'])
    state = start_trace(records)
    parsed = [work for record in records if (work := state.consume(record)) is not None]
    _ = state.summary()
    require(len(parsed) == len(projected) == len(observed) == 4 and
            [work.identity for work in parsed] == [0, 1, 2, 3],
            'bound producer trace work count/order differs')
    certified: list[CertifiedWork] = []
    model_events: Counter[tuple[int, int, int, str]] = Counter()
    halves = (0, 0)
    previous_resource = 0
    for ordinal, (work, projected_row, rtl_row) in enumerate(zip(parsed, projected, observed, strict=True)):
        require(work_binding(work) == projected_row.get('work_binding') and
                integer(rtl_row, 'accepted') >= previous_resource,
                'producer work binding or drained acceptance differs')
        original = model_document(profile, work)
        request = {**object_value(original['request'], 'request'),
                   'accepted_cycle': integer(rtl_row, 'accepted'),
                   'initial_scratchpad_half': halves[0],
                   'initial_accumulator_half': halves[1], 'record_events': 1}
        model_request: Record = {**original, 'request': request, 'timing': timing}
        answer = estimate_service(library, model_request)
        certified_row = compare_raw_work(rtl_row, projected_row, answer, model_request,
                                         ordinal, 5, halves, inert_tag_carry_proved=True)
        certified.append(certified_row)
        halves = (certified_row.next_scratchpad_half, certified_row.next_accumulator_half)
        previous_resource = integer(rtl_row, 'resource_ready')
        model_events.update((ordinal, work.identity, integer(event, 'cycle'), text(event, 'type'))
                            for value in array_value(answer.get('events'), 'events')
                            if (event := object_value(value, 'event')).get('type') in SELECTED_EVENTS)
    require(bool(model_events) and model_events == rtl_events == logged_model_events,
            'raw RTL/model selected events differ from independent cycle library')
    first = next(iter(model_events))
    shifted = model_events.copy()
    shifted[first] -= 1
    shifted[(first[0], first[1], first[2] + 1, first[3])] += 1
    require(shifted != rtl_events, 'selected event +1 mutation was admitted')
    return CertifiedCase(profile + '/p5/' + ''.join(map(str, phases)), profile, 5, tuple(certified))
