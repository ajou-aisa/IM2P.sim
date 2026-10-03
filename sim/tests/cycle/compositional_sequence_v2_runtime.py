from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from sim.cycle.npu_trace_schema import Record, integer, object_value
from sim.cycle.reconstruct_graph import array
from sim.tests.cycle.compositional_sequence_v2_stimulus import (
    AbsoluteOfferError,
    validate_stimulus,
)
from sim.tests.cycle.compositional_sequence_v2_stream import (
    compare_selected_events,
    non_event_lines,
    validate_tag_windows,
)
from sim.tests.cycle.compositional_sequence_v2_values import validated_values


def validate_absolute_log(raw: str, stimulus: Record, *,
                          expected_stimulus_sha256: str | None = None) -> list[Record]:
    return _validate_absolute_records(raw, stimulus, events_verified=False,
                                      expected_stimulus_sha256=expected_stimulus_sha256,
                                      expected_repeats=None)


def validate_absolute_log_stream(path: Path, stimulus: Record,
                                 work_ids: tuple[int, ...], *,
                                 expected_stimulus_sha256: str | None,
                                 expected_repeats: int | None,
                                 require_boundary_v2: bool = False,
                                 require_queue_payload_v2: bool = False) -> list[Record]:
    validate_stimulus(stimulus, expected_stimulus_sha256=expected_stimulus_sha256,
                      expected_repeats=expected_repeats)
    required_boundary_v2 = 'BOUNDARY_V2' in array(stimulus.get('required_observations', []))
    if required_boundary_v2 and not require_boundary_v2:
        raise AbsoluteOfferError('required boundary v2 stream gate missing')
    compare_selected_events(path, work_ids, require_counts=True,
                            require_boundary_v2=require_boundary_v2,
                            require_queue_edges=required_boundary_v2 or require_queue_payload_v2,
                            require_queue_payload_v2=require_queue_payload_v2,
                            payload_stimulus=stimulus if require_queue_payload_v2 else None)
    return _validate_absolute_records(non_event_lines(path), stimulus, events_verified=True,
                                      expected_stimulus_sha256=expected_stimulus_sha256,
                                      expected_repeats=expected_repeats)


def _validate_absolute_records(raw: str | Iterable[str], stimulus: Record,
                               *, events_verified: bool,
                               expected_stimulus_sha256: str | None,
                               expected_repeats: int | None) -> list[Record]:
    validate_stimulus(stimulus, expected_stimulus_sha256=expected_stimulus_sha256,
                      expected_repeats=expected_repeats)
    required = stimulus.get('required_observations', [])
    if 'BOUNDARY_V2' in array(required) and not events_verified:
        raise AbsoluteOfferError('required boundary v2 needs stream validation')
    strict_scope = required != []
    runs: list[Record] = []
    model_runs: list[Record] = []
    rtl_works: list[Record] = []
    model_works: list[Record] = []
    edges: dict[str, dict[int, list[tuple[int, ...]]]] = {
        'AVAIL_EDGE': {}, 'OFFER_EDGE': {}, 'MODEL_OFFER_EDGE': {},
    }
    events: dict[str, Counter[tuple[int, int, int, str]]] = {
        'RTL_EVENT': Counter(), 'MODEL_EVENT': Counter(),
    }
    tag_edges: dict[str, list[tuple[int, str, int, int, int, int, int]]] = {
        'RTL_TAG_EDGE': [], 'MODEL_TAG_EDGE': [],
    }
    for line in raw.splitlines() if isinstance(raw, str) else raw:
        if line.startswith('COMPOSITION_RUN '):
            runs.append(object_value(json.loads(line.removeprefix('COMPOSITION_RUN '))))
        elif line.startswith('MODEL_RUN '):
            model_runs.append(object_value(json.loads(line.removeprefix('MODEL_RUN '))))
        elif line.startswith('COMPOSITION_WORK '):
            rtl_works.append(object_value(json.loads(line.removeprefix('COMPOSITION_WORK '))))
        elif line.startswith('MODEL_WORK '):
            model_works.append(object_value(json.loads(line.removeprefix('MODEL_WORK '))))
        elif strict_scope and line.startswith(('RTL_TAG_EDGE ', 'MODEL_TAG_EDGE ')):
            fields = line.split()
            if len(fields) != 8 or fields[2] not in ('OFFER', 'RESOURCE'):
                raise AbsoluteOfferError('required observation TAG_EDGE malformed')
            name, ordinal, phase, cycle, length, head, enqueues, dequeues = fields
            tag_edges[name].append((int(ordinal), phase, int(cycle), int(length),
                                    int(head), int(enqueues), int(dequeues)))
        elif line.startswith(('AVAIL_EDGE ', 'OFFER_EDGE ', 'MODEL_OFFER_EDGE ')):
            name, *values = line.split()
            if len(values) != 7:
                raise AbsoluteOfferError('absolute offer edge has wrong field count')
            ordinal, *fields = map(int, values)
            edges[name].setdefault(ordinal, []).append(tuple(fields))
        elif line.startswith(('RTL_EVENT ', 'MODEL_EVENT ')):
            name, ordinal, work_id, cycle, event = line.split()
            events[name][int(ordinal), int(work_id), int(cycle), event] += 1
    expected = [object_value(row) for row in array(stimulus['works'])]
    if (len(runs) != 1 or runs[0].get('instance_count') != 1 or
            runs[0].get('reset_count') != 1 or
            runs[0].get('work_count') != len(expected) or
            runs[0].get('stimulus_sha256') != stimulus['stimulus_sha256'] or
            len(rtl_works) != len(expected)):
        raise AbsoluteOfferError('absolute offer run/digest/work evidence missing')
    if (len(model_runs) != 1 or model_runs[0].get('instance_count') != 1 or
            model_runs[0].get('reset_count') != 1 or
            model_runs[0].get('period') != 5 or model_runs[0].get('generation') != 1 or
            model_runs[0].get('work_count') != len(expected) or
            model_runs[0].get('stimulus_sha256') != stimulus['stimulus_sha256'] or
            len(model_works) != len(expected)):
        raise AbsoluteOfferError('absolute model run/digest/work evidence missing')
    ordinals = set(range(len(expected)))
    if (any(set(edges[kind]) != ordinals for kind in ('OFFER_EDGE', 'MODEL_OFFER_EDGE')) or
            set(edges['AVAIL_EDGE']) - ordinals):
        raise AbsoluteOfferError('absolute RTL/model offer edge coverage differs')
    for ordinal, (requested, observed) in enumerate(zip(expected, rtl_works, strict=True)):
        if (observed.get('ordinal'), observed.get('work_id'),
                observed.get('work_binding'), observed.get('slot')) != \
                (ordinal, requested['work_id'], requested['work_binding'], requested['slot']):
            raise AbsoluteOfferError(f'absolute offer producer identity differs at work {ordinal}')
        offer = integer(requested, 'port_offer_cycle')
        available = integer(requested, 'request_available_cycle')
        prior_rtl_ready = integer(rtl_works[ordinal - 1], 'resource_ready') if ordinal else 0
        pending = edges['AVAIL_EDGE'].get(ordinal, [])
        if ordinal and available <= prior_rtl_ready:
            for position, row in enumerate(pending):
                cycle, present, valid, ready, credit, fire = row
                if (cycle != available + position or cycle > prior_rtl_ready or
                        (present, valid, credit, fire) !=
                        (1, 0, int(cycle == prior_rtl_ready), 0) or ready not in (0, 1)):
                    window = pending[max(0, position - 2):position + 3]
                    raise AbsoluteOfferError(f'AVAIL_EDGE pending/port evidence differs at work {ordinal}, '
                                     f'expected cycle {available + position}, window={window}')
            if len(pending) != prior_rtl_ready - available + 1:
                raise AbsoluteOfferError(f'AVAIL_EDGE missing at work {ordinal} '
                                 f'cycle {available + len(pending)}')
        elif pending:
            raise AbsoluteOfferError(f'AVAIL_EDGE unexpected at work {ordinal}')
        if (observed.get('offered') != offer or observed.get('port_offer_cycle') != offer or
                observed.get('request_available_cycle') != requested['request_available_cycle']):
            raise AbsoluteOfferError(f'absolute port offer differs at work {ordinal}')
        for kind, work in (('OFFER_EDGE', observed),
                           ('MODEL_OFFER_EDGE', model_works[ordinal])):
            stream = edges[kind].get(ordinal, [])
            if not stream:
                raise AbsoluteOfferError(f'{kind} offer edges missing at work {ordinal}')
            previous = (rtl_works if kind == 'OFFER_EDGE' else model_works)[ordinal - 1] if ordinal else None
            prior_ready = integer(previous, 'resource_ready') if previous is not None else 0
            if offer < prior_ready:
                raise AbsoluteOfferError(f'{kind} port offer preceded prior resource_ready at work {ordinal}: '
                                 f'offer={offer} prior={prior_ready}')
            if work.get('offered') != offer:
                raise AbsoluteOfferError(f'{kind} port offer differs at work {ordinal}')
            for position, edge in enumerate(stream):
                cycle, available, valid, ready, credit, fire = edge
                window = stream[max(0, position - 2):position + 3]
                if cycle != offer + position or available != 1 or \
                        (valid, credit) != (1, 1) or \
                        any(value not in (0, 1) for value in edge[1:]):
                    raise AbsoluteOfferError(f'{kind} offer hold/epoch differs at work {ordinal}, '
                                     f'cycle {cycle}, window={window}')
                if fire and not ready:
                    raise AbsoluteOfferError(f'{kind} accepted before ready at work {ordinal}, '
                                     f'cycle {cycle}, window={window}')
                if fire != (valid and ready and credit) or (fire and position != len(stream) - 1):
                    raise AbsoluteOfferError(f'{kind} earliest ready/fire differs at work {ordinal}, '
                                     f'cycle {cycle}, window={window}')
            if stream[-1][-1] != 1 or work.get('accepted') != stream[-1][0]:
                raise AbsoluteOfferError(f'{kind} accepted edge differs at work {ordinal}')
    compared = ('offered', 'accepted', 'result_ready', 'final_scale_release', 'resource_ready',
                'submissions', 'scale_read_requests', 'scale_read_responses',
                'scale_release_count', 'load_requests', 'load_responses',
                'store_requests', 'store_responses', 'initial_scratchpad_half',
                'initial_accumulator_half', 'next_scratchpad_half',
                'next_accumulator_half')
    if strict_scope:
        compared += ('mesh_tag_queue_len', 'mesh_tag_enqueues',
                     'mesh_tag_dequeues', 'mesh_tag_max_occupancy')
    unresolved_full = False
    for ordinal, (rtl, model) in enumerate(zip(rtl_works, model_works, strict=True)):
        requested = expected[ordinal]
        if (model.get('ordinal'), model.get('work_id'), model.get('work_binding'),
                model.get('request_available_cycle'), model.get('port_offer_cycle')) != \
                (ordinal, requested['work_id'], requested['work_binding'],
                 requested['request_available_cycle'], requested['port_offer_cycle']):
            raise AbsoluteOfferError(f'model producer identity differs at work {ordinal}')
        for key in ('planner_loop_count', 'fragment_count', 'event_count'):
            integer(model, key)
        if strict_scope:
            if 'physical_fragment_count' not in rtl or any(
                    key not in rtl or key not in model for key in compared[-4:]):
                raise AbsoluteOfferError(f'required observation FRAGMENTS/TAG_STATE missing at work {ordinal}')
            if rtl['physical_fragment_count'] != model['fragment_count']:
                raise AbsoluteOfferError(f'physical fragment count differs at work {ordinal}: '
                                         f'rtl={rtl["physical_fragment_count"]} '
                                         f'model={model["fragment_count"]}')
        for key in compared:
            if key not in rtl or key not in model or rtl[key] != model[key]:
                raise AbsoluteOfferError(f'absolute RTL/model {key} differs at work {ordinal}: '
                                 f'rtl={rtl.get(key)} model={model.get(key)}')
        if strict_scope:
            tag_len = integer(model, 'mesh_tag_queue_len')
            if tag_len and rtl.get('mesh_tag_head_id') != model.get('mesh_tag_head_id'):
                raise AbsoluteOfferError(f'mesh tag head differs at work {ordinal}')
            if max(tag_len, integer(model, 'mesh_tag_max_occupancy')) >= 6:
                raise AbsoluteOfferError(f'FULL_TAG_PRESSURE_REQUIRES_TODO14 at work {ordinal}')
            if required == ['FRAGMENTS', 'TAG_STATE', 'TAG_FULL_PRESSURE']:
                if model.get('mesh_tag_full_backpressure_mapped') is not True:
                    unresolved_full = True
                elif rtl.get('mesh_tag_full_backpressure_cycles') != model.get('mesh_tag_full_backpressure_cycles'):
                    raise AbsoluteOfferError(f'tag full pressure count differs at work {ordinal}')
        if rtl.get('numeric_pass') is not True:
            raise AbsoluteOfferError(f'absolute RTL numeric output failed at work {ordinal}')
    if not events_verified and (not events['RTL_EVENT'] or events['RTL_EVENT'] != events['MODEL_EVENT']):
        rtl_events, model_events = events['RTL_EVENT'], events['MODEL_EVENT']
        first = next((key for key in sorted(rtl_events.keys() | model_events.keys(),
                                            key=lambda row: (row[2], row[0], row[1], row[3]))
                      if rtl_events[key] != model_events[key]), None)
        if first is None:
            raise AbsoluteOfferError('absolute RTL/model selected event stream missing')
        ordinal, work_id, cycle, name = first
        window = {kind: sorted((*key, count) for key, count in stream.items()
                                if key[0] == ordinal and abs(key[2] - cycle) <= 2)[:12]
                  for kind, stream in events.items()}
        raise AbsoluteOfferError(f'absolute RTL/model selected event divergence at work {ordinal} '
                         f'cycle {cycle} event {name} work_id {work_id}: '
                         f'rtl_count={rtl_events[first]} model_count={model_events[first]} '
                         f'window={window}')
    if strict_scope:
        validate_tag_windows(tag_edges, expected, (rtl_works, model_works))
    if unresolved_full:
        raise AbsoluteOfferError('UNRESOLVED_TAG_FULL_PRESSURE')
    if isinstance(raw, str):
        return validated_values(raw, stimulus, rtl_works)
    if 'value_fixture' in stimulus:
        raise AbsoluteOfferError('streaming numeric fixture unsupported')
    return rtl_works
