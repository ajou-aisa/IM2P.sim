# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# PYTHONPATH=. uv run pytest -q sim/tests/cycle/test_sequence_mutations.py
# ──────────────────
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Literal, assert_never

import pytest

from scripts.gemmini_replay_contract import contract_digest
from scripts.gemmini_resolve_profile import JsonValue
from scripts.gemmini_rtl_build_binding import (
    OBJECTS,
    RUN_AWARE_BINARY,
    build_inputs,
    verify_build,
)
from sim.cycle.certificate_contract import read_document
from sim.cycle.npu_trace_schema import Record, integer, object_value
from sim.cycle.reconstruct_graph import array, sha256
from sim.tests.cycle import compositional_sequence_work as sequence
from sim.tests.cycle.compositional_sequence_v2_stream import compare_selected_events
from sim.tests.cycle.test_sequence_absolute_offer import _coherent_log, _sealed_manifest
from sim.tests.cycle.test_sequence_corpus import _check, _control
from sim.tests.cycle.test_sequence_repeat_stream import _log

EventMutation = Literal['shift', 'delete', 'duplicate']


def _reseal_plan(stimulus: Record, plan: Record, path: Path) -> None:
    _ = path.write_text(json.dumps(plan, sort_keys=True) + '\n')
    object_value(stimulus['producer_corpus'])['plan'] = {
        'path': str(path), 'sha256': sha256(path),
    }
    stimulus['stimulus_sha256'] = sequence.stimulus_digest(stimulus)


def _offer_control(tmp_path: Path) -> tuple[Record, Record]:
    stimulus, projected = _control(tmp_path)
    plan_binding = object_value(object_value(stimulus['producer_corpus'])['plan'])
    plan = read_document(Path(str(plan_binding['path'])))
    grid_binding = object_value(stimulus['predeclared_offer_grid'])
    grid = read_document(Path(str(grid_binding['path'])))
    object_value(array(stimulus['works'])[1])['port_offer_cycle'] = 8
    array(array(plan['offer_epochs'])[1])[1] = 8
    case = object_value(array(grid['cases'])[0])
    array(case['electrical_port_offer_cycles'])[1] = 8
    grid_path = tmp_path / 'reference-grid.json'
    _ = grid_path.write_text(json.dumps(grid, sort_keys=True) + '\n')
    stimulus['predeclared_offer_grid'] = {'path': str(grid_path), 'sha256': sha256(grid_path)}
    plan['predeclared_offer_grid_sha256'] = sha256(grid_path)
    _reseal_plan(stimulus, plan, tmp_path / 'reference-plan.json')
    _check(stimulus, projected)
    return stimulus, projected


@pytest.mark.parametrize('offset', (-1, 1))
def test_resealed_offer_shift_rejected_by_unchanged_grid(tmp_path: Path, offset: int) -> None:
    # Given: a valid source-bound 5/8 offer pair and its unchanged external grid.
    stimulus, projected = _offer_control(tmp_path)
    grid_sha = object_value(stimulus['predeclared_offer_grid'])['sha256']
    object_value(array(stimulus['works'])[1])['port_offer_cycle'] = 8 + offset
    plan_binding = object_value(object_value(stimulus['producer_corpus'])['plan'])
    plan = read_document(Path(str(plan_binding['path'])))
    array(array(plan['offer_epochs'])[1])[1] = 8 + offset
    _reseal_plan(stimulus, plan, tmp_path / 'shifted-plan.json')

    # When: the copied plan and stimulus agree but the original grid remains bound.
    with pytest.raises(ValueError, match='predeclared offer grid epochs differ'):
        _check(stimulus, projected)

    # Then: the independent offer authority was not changed to match the mutation.
    assert object_value(stimulus['predeclared_offer_grid'])['sha256'] == grid_sha


@pytest.mark.parametrize('field', ('tile_i_count', 'activation_stride_bytes'))
def test_resealed_geometry_change_rejected_by_source_projection(tmp_path: Path, field: str) -> None:
    # Given: one tile or stride field changes after source projection.
    stimulus, projected = _control(tmp_path)
    inputs = object_value(object_value(array(stimulus['works'])[1])['input'])
    inputs[field] = 2
    stimulus['stimulus_sha256'] = sequence.stimulus_digest(stimulus)

    # When/Then: the unchanged source projection rejects the changed descriptor.
    with pytest.raises(ValueError, match='producer corpus work projection differs'):
        _check(stimulus, projected)


def test_resealed_original_run_mask_change_rejected_by_source_projection(tmp_path: Path) -> None:
    # Given: one residual original-block mask changes under the same work ID.
    stimulus, projected = _control(tmp_path)
    run = object_value(array(object_value(array(stimulus['works'])[1])['runs'])[0])
    run['original_k_mask'] = 3
    stimulus['stimulus_sha256'] = sequence.stimulus_digest(stimulus)

    # When/Then: ordered run content remains bound to the producer corpus.
    with pytest.raises(ValueError, match='producer corpus work projection differs'):
        _check(stimulus, projected)


@pytest.mark.parametrize('mutation', ('missing', 'reordered'))
def test_resealed_work_coverage_rejected_by_source_projection(
        tmp_path: Path, mutation: str) -> None:
    # Given: the source-bound manifest omits or reorders a work.
    stimulus, projected = _control(tmp_path)
    works = array(stimulus['works'])
    if mutation == 'missing':
        _ = works.pop()
    else:
        works.reverse()
    stimulus['stimulus_sha256'] = sequence.stimulus_digest(stimulus)

    # When/Then: source projection retains exact work coverage and order.
    with pytest.raises(ValueError, match='producer corpus work projection differs'):
        _check(stimulus, projected)


def test_resealed_fence_change_rejected_by_source_projection(tmp_path: Path) -> None:
    # Given: one parent omits its fence while retaining the source work.
    stimulus, projected = _control(tmp_path)
    object_value(array(stimulus['pipeline_parents'])[1])['fence_required_work_ids'] = []
    stimulus['stimulus_sha256'] = sequence.stimulus_digest(stimulus)

    # When/Then: source parent authority rejects the incomplete fence.
    with pytest.raises(ValueError, match='producer corpus parent projection differs'):
        _check(stimulus, projected)


def test_resealed_expected_corpus_shrink_rejected_by_source_plan(tmp_path: Path) -> None:
    # Given: both copied manifest and copied plan omit the second source work.
    stimulus, projected = _control(tmp_path)
    plan_binding = object_value(object_value(stimulus['producer_corpus'])['plan'])
    plan = read_document(Path(str(plan_binding['path'])))
    for key in ('required_work_ids', 'work_bindings', 'work_slots', 'offer_epochs'):
        _ = array(plan[key]).pop()
    _ = array(stimulus['works']).pop()
    parent = object_value(array(stimulus['pipeline_parents'])[1])
    parent['required_work_ids'] = []
    parent['fence_required_work_ids'] = []
    _reseal_plan(stimulus, plan, tmp_path / 'shrunken-plan.json')

    # When/Then: reprojection from unchanged producer bytes defeats the shrink.
    with pytest.raises(ValueError, match='producer corpus plan content differs'):
        _check(stimulus, projected)


def test_wrong_raw_rtl_work_id_rejected_at_producer_identity(tmp_path: Path) -> None:
    # Given: raw RTL work 0 claims an ID different from the sealed producer work.
    stimulus = _sealed_manifest(tmp_path)
    raw = _coherent_log(stimulus).replace('"work_id": 11', '"work_id": 99', 1)

    # When/Then: the comparator identifies the first wrong producer work.
    with pytest.raises(ValueError, match='absolute offer producer identity differs at work 0'):
        _ = sequence.validate_absolute_log(raw, stimulus)


def _mutate_event(raw: str, kind: str, mutation: EventMutation) -> str:
    original = f'{kind} 1 12 6 work'
    match mutation:
        case 'shift':
            replacement = f'{kind} 1 12 7 work'
        case 'delete':
            replacement = ''
        case 'duplicate':
            replacement = original + '\n' + original
        case unreachable:
            assert_never(unreachable)
    assert original in raw
    return raw.replace(original, replacement, 1)


@pytest.mark.parametrize('mutation', ('shift', 'delete', 'duplicate'))
def test_raw_selected_event_mutation_reports_first_cycle(
        tmp_path: Path, mutation: EventMutation) -> None:
    # Given: RTL event evidence changes while endpoint and counter rows stay fixed.
    stimulus = _sealed_manifest(tmp_path)
    raw = _mutate_event(_coherent_log(stimulus), 'RTL_EVENT', mutation)

    # When/Then: exact multiset comparison reports the first divergent cycle.
    with pytest.raises(ValueError, match=r'selected event divergence at work 1 cycle 6 .*window='):
        _ = sequence.validate_absolute_log(raw, stimulus)


def test_streamed_event_shift_reports_first_cycle_and_window(tmp_path: Path) -> None:
    # Given: a counted streaming log with one RTL event moved by one cycle.
    path = tmp_path / 'raw.log'
    _log(path)
    _ = path.write_text(path.read_text().replace('RTL_EVENT 1 1 1 work',
                                                 'RTL_EVENT 1 1 2 work', 1))

    # When/Then: streaming comparison exposes the first cycle and nearby raw rows.
    with pytest.raises(ValueError, match=r'selected event divergence at work 1 cycle 1 .*window=') as error:
        _ = compare_selected_events(path, (0, 1), require_counts=True)
    assert "'RTL_EVENT': [(0, 'work', 1), (2, 'work', 1)]" in str(error.value)
    assert "'MODEL_EVENT': [(0, 'work', 1), (1, 'work', 1)]" in str(error.value)


def test_streamed_duplicate_key_window_retains_multiplicity(tmp_path: Path) -> None:
    # Given: one RTL event moves onto an existing key without changing work counts.
    path = tmp_path / 'raw.log'
    _log(path)
    _ = path.write_text(path.read_text().replace('RTL_EVENT 1 1 1 work',
                                                 'RTL_EVENT 1 1 0 work', 1))

    # When/Then: the first cycle and nearby counts expose both copies of the key.
    with pytest.raises(ValueError, match=r'selected event divergence at work 1 cycle 0 .*rtl=2 model=1 .*window=') as error:
        _ = compare_selected_events(path, (0, 1), require_counts=True)
    assert "'RTL_EVENT': [(0, 'work', 2)]" in str(error.value)
    assert "'MODEL_EVENT': [(0, 'work', 1), (1, 'work', 1)]" in str(error.value)


@pytest.mark.parametrize(('mutation', 'parsed'), [('delete', 1), ('duplicate', 3)])
def test_streamed_event_count_mutation_rejected_before_work_comparison(
        tmp_path: Path, mutation: EventMutation, parsed: int) -> None:
    # Given: an event is deleted or duplicated while its independent count stays two.
    path = tmp_path / 'raw.log'
    _log(path)
    raw = path.read_text().replace('RTL_EVENT 1 1 1 work',
                                   '' if mutation == 'delete' else
                                   'RTL_EVENT 1 1 1 work\nRTL_EVENT 1 1 1 work', 1)
    _ = path.write_text(raw)

    # When/Then: the declared count rejects the changed work-1 stream.
    with pytest.raises(ValueError, match=rf'RTL selected event count differs at work 1: declared=2 parsed={parsed}'):
        _ = compare_selected_events(path, (0, 1), require_counts=True)


def _boundary_log(path: Path) -> None:
    _log(path)
    lines = path.read_text().splitlines()
    for kind, work_marker in (('RTL', 'COMPOSITION_WORK'), ('MODEL', 'MODEL_WORK')):
        boundaries: list[str] = []
        for phase, ordinal in (('PUBLIC_READY', 0), ('WORK1_OFFER', 1), ('WORK1_FIRE', 1)):
            rows: list[JsonValue] = [[4, 2], [5, 3]]
            tags: list[JsonValue] = [[1, 16, 0, 0], [2, 8, 1, 0]]
            bank: list[JsonValue] = [list[JsonValue]([0] * 6) for _ in range(4)]
            row: Record = {
                'phase': phase, 'ordinal': ordinal, 'work_id': ordinal, 'cycle': 2,
                'row_count': 2, 'rows': rows, 'tag_count': 2,
                'tags': tags, 'bank_pipe': bank,
            }
            if kind == 'RTL':
                row.update({'row_read': 3, 'row_write': 5, 'row_enq_fire': 1})
            boundaries.append(f'{kind}_BOUNDARY ' + json.dumps(row, separators=(',', ':')))
        first = next(i for i, line in enumerate(lines)
                     if line.startswith(f'{work_marker} {{"ordinal": 0'))
        lines.insert(first, boundaries[0])
        second = next(i for i, line in enumerate(lines)
                      if line.startswith(f'{kind}_EVENT 1 1 0 work'))
        lines[second:second] = boundaries[1:]
    _ = path.write_text('\n'.join(lines) + '\n')


def _boundary_v2_log(path: Path) -> None:
    _boundary_log(path)
    lines = path.read_text().splitlines()
    for index, line in enumerate(lines):
        if not line.startswith(('RTL_BOUNDARY ', 'MODEL_BOUNDARY ')):
            continue
        kind, _, payload = line.partition(' ')
        row = object_value(json.loads(payload))
        row.update({
            'boundary_schema': 2,
            'tag_count': 1, 'tags': [[1, 1, 1, 7]],
            'row_count': 1, 'rows': [[4, 16]],
        })
        if kind == 'RTL_BOUNDARY':
            row.update({
                'row_read': 0, 'row_write': 1, 'row_empty': 0,
                'row_maybe_full': 0, 'row_enq_fire': 0, 'row_deq_fire': 0,
                'tag_read': 5, 'tag_write': 0, 'tag_enq_fire': 0,
                'tag_deq_fire': 0, 'request_valid': 0,
                'last_response_fire': 0,
            })
        lines[index] = kind + '_V2 ' + json.dumps(row, separators=(',', ':'))
    _ = path.write_text('\n'.join(lines) + '\n')


def _queue_edge_v1_log(path: Path) -> None:
    _boundary_v2_log(path)
    lines = path.read_text().splitlines()
    run = {'stimulus_sha256': '1' * 64, 'work_count': 2,
           'instance_count': 1, 'reset_count': 1}
    lines[0] = 'COMPOSITION_RUN ' + json.dumps(run)
    model_run = lines.index('MODEL_RUN {}')
    lines[model_run] = 'MODEL_RUN ' + json.dumps({**run, 'generation': 1})
    old: Record = {'cycle': 1, 'row_count': 0, 'rows': [],
                   'tag_count': 0, 'tags': []}
    nxt: Record = {'cycle': 2, 'row_count': 1, 'rows': [[4, 16]],
                   'tag_count': 1, 'tags': [[1, 1, 1, 7]]}
    controls = {'row_read': 0, 'row_write': 0, 'row_empty': 1,
                'row_maybe_full': 0, 'row_enq_fire': 1, 'row_deq_fire': 0,
                'tag_read': 0, 'tag_write': 0, 'tag_enq_fire': 1,
                'tag_deq_fire': 0, 'request_valid': 1,
                'last_response_fire': 0}
    for kind, marker in (('RTL', 'RTL_BOUNDARY_V2'),
                         ('MODEL', 'MODEL_BOUNDARY_V2')):
        if kind == 'RTL':
            edge = {'queue_schema': 1, 'generation': 1,
                    'old': {**old, **controls},
                    'next': {**nxt, **controls, 'row_write': 1,
                             'row_empty': 0, 'row_maybe_full': 1,
                             'row_enq_fire': 0, 'tag_write': 1,
                             'tag_enq_fire': 0, 'request_valid': 0}}
        else:
            edge = {'queue_schema': 1, 'generation': 1,
                    'old': {**old, 'tag_enqueues': 0, 'tag_dequeues': 0},
                    'next': {**nxt, 'tag_enqueues': 1, 'tag_dequeues': 0}}
        index = next(i for i, line in enumerate(lines) if line.startswith(marker))
        lines.insert(index, f'{kind}_QUEUE_EDGE_V1 ' + json.dumps(edge, separators=(',', ':')))
    for index, line in enumerate(lines):
        if line.startswith(('COMPOSITION_WORK ', 'MODEL_WORK ')):
            kind, _, payload = line.partition(' ')
            row = object_value(json.loads(payload))
            row['mesh_tag_enqueues'] = int(row['ordinal'] == 0)
            row['mesh_tag_dequeues'] = 0
            lines[index] = kind + ' ' + json.dumps(row, separators=(',', ':'))
    _ = path.write_text('\n'.join(lines) + '\n')


def test_streamed_required_queue_edge_rejects_absent_rows(tmp_path: Path) -> None:
    # Given: paired v2 boundaries but no queue transitions.
    path = tmp_path / 'raw.log'
    _boundary_v2_log(path)

    # When/Then: the opt-in transition gate requires independent queue evidence.
    with pytest.raises(ValueError, match='queue edge.*missing'):
        _ = compare_selected_events(path, (0, 1), require_counts=True,
                                    require_boundary_v2=True, require_queue_edges=True)


def test_streamed_queue_edge_accepts_one_paired_transition(tmp_path: Path) -> None:
    # Given: a synthetic one-step enqueue and independent work counters.
    path = tmp_path / 'raw.log'
    _queue_edge_v1_log(path)

    # When/Then: the counted v2 stream admits the paired transition.
    assert compare_selected_events(path, (0, 1), require_counts=True,
                                   require_boundary_v2=True, require_queue_edges=True) == (2, 2)


@pytest.mark.parametrize('field', ('tag_write', 'row_count'))
def test_streamed_queue_edge_rejects_next_state_mutation(
        tmp_path: Path, field: str) -> None:
    # Given: a paired edge with one RTL next-state integer increased by one.
    path = tmp_path / 'raw.log'
    _queue_edge_v1_log(path)
    lines = path.read_text().splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith('RTL_QUEUE_EDGE_V1 '))
    row = object_value(json.loads(lines[index].partition(' ')[2]))
    nxt = object_value(row['next'])
    nxt[field] = integer(nxt, field) + 1
    lines[index] = 'RTL_QUEUE_EDGE_V1 ' + json.dumps(row, separators=(',', ':'))
    _ = path.write_text('\n'.join(lines) + '\n')

    # When/Then: present evidence is checked even without the required flag.
    with pytest.raises(ValueError, match=rf'cycle 2 .*{field}'):
        _ = compare_selected_events(path, (0, 1), require_counts=True,
                                    require_boundary_v2=True)


def test_streamed_queue_edge_rejects_missing_model_counterpart(tmp_path: Path) -> None:
    # Given: the RTL transition remains but the corresponding MODEL edge is absent.
    path = tmp_path / 'raw.log'
    _queue_edge_v1_log(path)
    lines = [line for line in path.read_text().splitlines()
             if not line.startswith('MODEL_QUEUE_EDGE_V1 ')]
    _ = path.write_text('\n'.join(lines) + '\n')

    # When/Then: an RTL-only transition cannot certify parity.
    with pytest.raises(ValueError, match='queue edge.*counterpart|queue edge.*coverage'):
        _ = compare_selected_events(path, (0, 1), require_counts=True,
                                    require_boundary_v2=True)


def test_streamed_queue_edge_rejects_paired_fire_omission(tmp_path: Path) -> None:
    # Given: two paired enqueues, then both sides omit the second edge.
    path = tmp_path / 'raw.log'
    _queue_edge_v1_log(path)
    lines = path.read_text().splitlines()
    for kind, work_marker in (('RTL', 'COMPOSITION_WORK'), ('MODEL', 'MODEL_WORK')):
        first = next(line for line in lines if line.startswith(f'{kind}_QUEUE_EDGE_V1 '))
        first_edge = object_value(json.loads(first.partition(' ')[2]))
        old = {**object_value(first_edge['next']), 'cycle': 3}
        nxt = {**old, 'cycle': 4, 'row_count': 2,
               'rows': [[4, 16], [5, 16]], 'tag_count': 2,
               'tags': [[1, 1, 1, 7], [2, 1, 1, 8]]}
        if kind == 'RTL':
            old.update({'row_enq_fire': 1, 'tag_enq_fire': 1, 'request_valid': 1})
            nxt.update({'row_write': 2, 'tag_write': 2, 'row_enq_fire': 0,
                        'tag_enq_fire': 0, 'request_valid': 0})
        else:
            nxt['tag_enqueues'] = 2
        edge = f'{kind}_QUEUE_EDGE_V1 ' + json.dumps(
            {'queue_schema': 1, 'generation': 1, 'old': old, 'next': nxt},
            separators=(',', ':'))
        work_index = next(i for i, line in enumerate(lines)
                          if line.startswith(work_marker + ' ') and
                          object_value(json.loads(line.partition(' ')[2]))['ordinal'] == 1)
        lines.insert(work_index, edge)
        summary = object_value(json.loads(lines[work_index + 1].partition(' ')[2]))
        summary['mesh_tag_enqueues'] = 1
        lines[work_index + 1] = work_marker + ' ' + json.dumps(summary, separators=(',', ':'))
    full = '\n'.join(lines) + '\n'
    _ = path.write_text(full)
    assert compare_selected_events(path, (0, 1), require_counts=True,
                                   require_boundary_v2=True, require_queue_edges=True) == (2, 2)
    second_rtl = [line for line in lines if line.startswith('RTL_QUEUE_EDGE_V1 ')][-1]
    second_model = [line for line in lines if line.startswith('MODEL_QUEUE_EDGE_V1 ')][-1]
    _ = path.write_text(full.replace(second_rtl + '\n', '', 1)
                        .replace(second_model + '\n', '', 1))

    # When/Then: independent work totals expose the paired omission.
    with pytest.raises(ValueError, match='queue edge RTL mesh_tag_enqueues coverage differs'):
        _ = compare_selected_events(path, (0, 1), require_counts=True,
                                    require_boundary_v2=True, require_queue_edges=True)


@pytest.mark.parametrize('mutation', ('duplicate_key', 'missing_schema',
                                      'duplicate_origin', 'line_cap'))
def test_streamed_queue_edge_rejects_malformed_rtl_row(
        tmp_path: Path, mutation: str) -> None:
    # Given: one exact V1 edge and a malformed RTL copy.
    path = tmp_path / 'raw.log'
    _queue_edge_v1_log(path)
    raw = path.read_text()
    original = next(line for line in raw.splitlines() if line.startswith('RTL_QUEUE_EDGE_V1 '))
    if mutation == 'duplicate_key':
        changed = original.replace('"queue_schema":1',
                                   '"queue_schema":1,"queue_schema":1', 1)
    elif mutation == 'missing_schema':
        changed = original.replace('"queue_schema":1,', '', 1)
    elif mutation == 'duplicate_origin':
        changed = original + '\n' + original
    else:
        changed = original + ' ' * 2048
    _ = path.write_text(raw.replace(original, changed, 1))

    # When/Then: duplicate, malformed, and oversized rows fail in the stream.
    with pytest.raises(ValueError, match='queue edge|QUEUE_EDGE'):
        _ = compare_selected_events(path, (0, 1), require_counts=True,
                                    require_boundary_v2=True, require_queue_edges=True)


def test_streamed_queue_edge_rejects_byte_cap(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a real paired raw whose first queue line exceeds a reduced scan budget.
    path = tmp_path / 'raw.log'
    _queue_edge_v1_log(path)
    from sim.tests.cycle import compositional_sequence_v2_stream as stream
    monkeypatch.setattr(stream, 'QUEUE_EDGE_BYTE_CAP', 100)

    # When/Then: the byte cap is checked before parsing more queue rows.
    with pytest.raises(ValueError, match='queue edge byte cap exceeded'):
        _ = compare_selected_events(path, (0, 1), require_counts=True,
                                    require_boundary_v2=True, require_queue_edges=True)


def test_streamed_queue_edge_rejects_model_next_counter_mutation(tmp_path: Path) -> None:
    # Given: a one-byte cumulative enqueue counter corruption in MODEL next.
    path = tmp_path / 'raw.log'
    _queue_edge_v1_log(path)
    raw = path.read_text()
    original = next(line for line in raw.splitlines()
                    if line.startswith('MODEL_QUEUE_EDGE_V1 '))
    row = object_value(json.loads(original.partition(' ')[2]))
    object_value(row['next'])['tag_enqueues'] = 2
    changed = 'MODEL_QUEUE_EDGE_V1 ' + json.dumps(row, separators=(',', ':'))
    _ = path.write_text(raw.replace(original, changed, 1))

    # When/Then: the model counter cannot disagree with occupied tag state.
    with pytest.raises(ValueError, match=r'cycle 2 tag_enqueues'):
        _ = compare_selected_events(path, (0, 1), require_counts=True,
                                    require_boundary_v2=True, require_queue_edges=True)


def test_streamed_queue_edge_rejects_old_full_enqueue_with_dequeue(tmp_path: Path) -> None:
    # Given: the old six-entry row queue reports simultaneous enqueue/dequeue.
    path = tmp_path / 'raw.log'
    _queue_edge_v1_log(path)
    raw = path.read_text()
    original = next(line for line in raw.splitlines()
                    if line.startswith('RTL_QUEUE_EDGE_V1 '))
    row = object_value(json.loads(original.partition(' ')[2]))
    old = object_value(row['old'])
    old.update({'row_count': 6, 'rows': [[index % 5, 16] for index in range(6)],
                'row_maybe_full': 1, 'row_empty': 0, 'row_deq_fire': 1,
                'last_response_fire': 1})
    changed = 'RTL_QUEUE_EDGE_V1 ' + json.dumps(row, separators=(',', ':'))
    _ = path.write_text(raw.replace(original, changed, 1))

    # When/Then: a same-edge dequeue cannot unblock the old-full enqueue.
    with pytest.raises(ValueError, match=r'cycle 1 old-full enqueue fire'):
        _ = compare_selected_events(path, (0, 1), require_counts=True,
                                    require_boundary_v2=True, require_queue_edges=True)


@pytest.mark.parametrize('dequeued', ('row', 'tag'))
def test_streamed_queue_edge_allows_independent_dequeues(
        tmp_path: Path, dequeued: str) -> None:
    # Given: a second real step removes only one of the two queue heads.
    path = tmp_path / 'raw.log'
    _queue_edge_v1_log(path)
    lines = path.read_text().splitlines()
    for kind, marker in (('RTL', 'COMPOSITION_WORK'), ('MODEL', 'MODEL_WORK')):
        first = next(line for line in lines if line.startswith(f'{kind}_QUEUE_EDGE_V1 '))
        first_edge = object_value(json.loads(first.partition(' ')[2]))
        old = {**object_value(first_edge['next']), 'cycle': 3}
        nxt = {**old, 'cycle': 4}
        if dequeued == 'row':
            nxt.update({'row_count': 0, 'rows': []})
            if kind == 'RTL':
                old.update({'row_deq_fire': 1, 'last_response_fire': 1})
                nxt.update({'row_read': 1, 'row_empty': 1, 'row_maybe_full': 0,
                            'last_response_fire': 0})
        else:
            nxt.update({'tag_count': 0, 'tags': []})
            if kind == 'RTL':
                old.update({'tag_deq_fire': 1, 'last_response_fire': 1})
                nxt.update({'tag_read': 1, 'last_response_fire': 0})
            else:
                nxt['tag_dequeues'] = 1
        edge = f'{kind}_QUEUE_EDGE_V1 ' + json.dumps(
            {'queue_schema': 1, 'generation': 1, 'old': old, 'next': nxt},
            separators=(',', ':'))
        index = next(i for i, line in enumerate(lines)
                     if line.startswith(marker + ' ') and
                     object_value(json.loads(line.partition(' ')[2]))['ordinal'] == 1)
        lines.insert(index, edge)
        if dequeued == 'tag':
            summary = object_value(json.loads(lines[index + 1].partition(' ')[2]))
            summary['mesh_tag_dequeues'] = 1
            lines[index + 1] = marker + ' ' + json.dumps(summary, separators=(',', ':'))
    _ = path.write_text('\n'.join(lines) + '\n')

    # When/Then: each queue advances its own read state without forcing the other.
    assert compare_selected_events(path, (0, 1), require_counts=True,
                                   require_boundary_v2=True, require_queue_edges=True) == (2, 2)


def _change_boundary(path: Path, kind: str, phase: str, changes: Record) -> None:
    raw = path.read_text()
    original = next(line for line in raw.splitlines()
                    if line.startswith(f'{kind}_BOUNDARY {{"phase":"{phase}"'))
    row = object_value(json.loads(original.partition(' ')[2]))
    row.update(changes)
    replacement = f'{kind}_BOUNDARY ' + json.dumps(row, separators=(',', ':'))
    _ = path.write_text(raw.replace(original, replacement, 1))


def _change_boundary_v2(path: Path, kind: str, phase: str, changes: Record) -> None:
    raw = path.read_text()
    original = next(line for line in raw.splitlines()
                    if line.startswith(f'{kind}_BOUNDARY_V2 {{"phase":"{phase}"'))
    row = object_value(json.loads(original.partition(' ')[2]))
    row.update(changes)
    replacement = f'{kind}_BOUNDARY_V2 ' + json.dumps(row, separators=(',', ':'))
    _ = path.write_text(raw.replace(original, replacement, 1))


@pytest.mark.parametrize(('field', 'value'), [('tag_read', 4), ('row_read', 1)])
def test_streamed_v2_rejects_one_line_pointer_corruption(
        tmp_path: Path, field: str, value: int) -> None:
    path = tmp_path / 'raw.log'
    _boundary_v2_log(path)
    _change_boundary_v2(path, 'RTL', 'PUBLIC_READY', {field: value})
    with pytest.raises(ValueError, match=rf'work 0 cycle 2 {field}.*occupancy'):
        _ = compare_selected_events(path, (0, 1), require_counts=True,
                                    require_boundary_v2=True)


def test_streamed_v2_rejects_old_full_enqueue_with_dequeue(tmp_path: Path) -> None:
    path = tmp_path / 'raw.log'
    _boundary_v2_log(path)
    rows: list[JsonValue] = [[index % 5, 16] for index in range(6)]
    _change_boundary_v2(path, 'RTL', 'PUBLIC_READY', {
        'row_count': 6, 'rows': rows, 'row_read': 0, 'row_write': 0,
        'row_empty': 0, 'row_maybe_full': 1, 'row_enq_fire': 1,
        'row_deq_fire': 1, 'tag_enq_fire': 1, 'last_response_fire': 1,
    })
    _change_boundary_v2(path, 'MODEL', 'PUBLIC_READY', {'row_count': 6, 'rows': rows})
    with pytest.raises(ValueError, match=r'work 0 cycle 2 old-full'):
        _ = compare_selected_events(path, (0, 1), require_counts=True,
                                    require_boundary_v2=True)


def test_streamed_v2_rejects_row_empty_occupancy_mismatch(tmp_path: Path) -> None:
    path = tmp_path / 'raw.log'
    _boundary_v2_log(path)
    _change_boundary_v2(path, 'RTL', 'PUBLIC_READY', {'row_empty': 1})
    with pytest.raises(ValueError, match=r'work 0 cycle 2 row_empty occupancy'):
        _ = compare_selected_events(path, (0, 1), require_counts=True,
                                    require_boundary_v2=True)


@pytest.mark.parametrize(('field', 'value'), [
    ('tag_read', -1), ('row_write', 6), ('row_empty', 2),
    ('request_valid', True),
])
def test_streamed_v2_rejects_malformed_rtl_control(
        tmp_path: Path, field: str, value: JsonValue) -> None:
    path = tmp_path / 'raw.log'
    _boundary_v2_log(path)
    _change_boundary_v2(path, 'RTL', 'PUBLIC_READY', {field: value})
    with pytest.raises(ValueError, match=rf'work 0 cycle 2 {field} malformed'):
        _ = compare_selected_events(path, (0, 1), require_counts=True,
                                    require_boundary_v2=True)


@pytest.mark.parametrize('field', (
    'row_read', 'row_write', 'row_empty', 'row_maybe_full',
    'row_enq_fire', 'row_deq_fire', 'tag_read', 'tag_write',
    'tag_enq_fire', 'tag_deq_fire', 'request_valid', 'last_response_fire',
))
def test_streamed_v2_rejects_missing_rtl_control(tmp_path: Path, field: str) -> None:
    path = tmp_path / 'raw.log'
    _boundary_v2_log(path)
    raw = path.read_text()
    original = next(line for line in raw.splitlines()
                    if line.startswith('RTL_BOUNDARY_V2 '))
    row = object_value(json.loads(original.partition(' ')[2]))
    del row[field]
    replacement = 'RTL_BOUNDARY_V2 ' + json.dumps(row, separators=(',', ':'))
    _ = path.write_text(raw.replace(original, replacement, 1))
    with pytest.raises(ValueError, match=rf'work 0 cycle 2 {field} malformed'):
        _ = compare_selected_events(path, (0, 1), require_counts=True,
                                    require_boundary_v2=True)


@pytest.mark.parametrize(('changes', 'field'), [
    ({'tag_enq_fire': 1}, 'tag_enq_fire'),
    ({'tag_deq_fire': 1}, 'tag_deq_fire'),
])
def test_streamed_v2_rejects_incoherent_rtl_fire(
        tmp_path: Path, changes: Record, field: str) -> None:
    path = tmp_path / 'raw.log'
    _boundary_v2_log(path)
    _change_boundary_v2(path, 'RTL', 'PUBLIC_READY', changes)
    with pytest.raises(ValueError, match=rf'work 0 cycle 2 {field}'):
        _ = compare_selected_events(path, (0, 1), require_counts=True,
                                    require_boundary_v2=True)


@pytest.mark.parametrize('field', ('row_deq_fire', 'tag_deq_fire'))
def test_streamed_v2_allows_independent_old_queue_dequeues(tmp_path: Path,
                                                           field: str) -> None:
    path = tmp_path / 'raw.log'
    _boundary_v2_log(path)
    _change_boundary_v2(path, 'RTL', 'PUBLIC_READY',
                        {field: 1, 'last_response_fire': 1, 'row_maybe_full': 1})
    assert compare_selected_events(path, (0, 1), require_counts=True,
                                   require_boundary_v2=True) == (2, 2)


def test_streamed_v2_rejects_dequeue_from_empty_row_queue(tmp_path: Path) -> None:
    path = tmp_path / 'raw.log'
    _boundary_v2_log(path)
    _change_boundary_v2(path, 'RTL', 'PUBLIC_READY', {
        'row_count': 0, 'rows': [], 'row_read': 0, 'row_write': 0,
        'row_empty': 1, 'row_deq_fire': 1, 'last_response_fire': 1,
    })
    _change_boundary_v2(path, 'MODEL', 'PUBLIC_READY', {'row_count': 0, 'rows': []})
    with pytest.raises(ValueError, match=r'work 0 cycle 2 row_deq_fire'):
        _ = compare_selected_events(path, (0, 1), require_counts=True,
                                    require_boundary_v2=True)


def _invalid_tag_v2_log(path: Path) -> None:
    _boundary_v2_log(path)
    for kind, output_rows in (('RTL', 16), ('MODEL', 0)):
        for phase in ('PUBLIC_READY', 'WORK1_OFFER', 'WORK1_FIRE'):
            _change_boundary_v2(path, kind, phase, {'tags': [[1, output_rows, 0, 0]]})


def test_streamed_v2_ignores_only_invalid_tag_output_rows(tmp_path: Path) -> None:
    path = tmp_path / 'raw.log'
    _invalid_tag_v2_log(path)
    assert compare_selected_events(path, (0, 1), require_counts=True,
                                   require_boundary_v2=True) == (2, 2)


@pytest.mark.parametrize(('changes', 'field'), [
    ({'tags': [[1, 16, 1, 0]]}, r'tags\[0\]\.rob_valid'),
    ({'tags': [[2, 16, 0, 0]]}, r'tags\[0\]\.id'),
    ({'tags': [[1, 16, 0, 1]]}, r'tags\[0\]\.rob_id'),
    ({'ordinal': 1}, 'ordinal'),
    ({'rows': [[4, 17]]}, r'rows\[0\]\.mesh_total_rows'),
    ({'bank_pipe': [[0] * 6, [0] * 6, [0, 0, 0, 1, 0, 0], [0] * 6]},
     r'bank_pipe\[2\]\[3\]'),
])
def test_streamed_v2_invalid_tag_keeps_live_state_checks(
        tmp_path: Path, changes: Record, field: str) -> None:
    path = tmp_path / 'raw.log'
    _invalid_tag_v2_log(path)
    _change_boundary_v2(path, 'RTL', 'PUBLIC_READY', changes)
    with pytest.raises(ValueError, match=rf'work 0 cycle 2 {field}'):
        _ = compare_selected_events(path, (0, 1), require_counts=True,
                                    require_boundary_v2=True)


def test_streamed_v2_invalid_tag_rejects_nonbit_rob_valid(tmp_path: Path) -> None:
    path = tmp_path / 'raw.log'
    _invalid_tag_v2_log(path)
    _change_boundary_v2(path, 'RTL', 'PUBLIC_READY', {'tags': [[1, 16, 2, 0]]})
    _change_boundary_v2(path, 'MODEL', 'PUBLIC_READY', {'tags': [[1, 0, 2, 0]]})
    with pytest.raises(ValueError, match='rob_valid malformed'):
        _ = compare_selected_events(path, (0, 1), require_counts=True,
                                    require_boundary_v2=True)


def test_streamed_v2_distinguishes_output_and_mesh_rows(tmp_path: Path) -> None:
    path = tmp_path / 'raw.log'
    _boundary_v2_log(path)
    assert compare_selected_events(path, (0, 1), require_counts=True,
                                   require_boundary_v2=True) == (2, 2)


def test_streamed_required_v2_rejects_legacy_and_absent_boundaries(tmp_path: Path) -> None:
    path = tmp_path / 'raw.log'
    _boundary_log(path)
    with pytest.raises(ValueError, match='boundary.*v2|v2.*boundary'):
        _ = compare_selected_events(path, (0, 1), require_counts=True,
                                    require_boundary_v2=True)
    _log(path)
    with pytest.raises(ValueError, match='boundary.*v2|v2.*boundary'):
        _ = compare_selected_events(path, (0, 1), require_counts=True,
                                    require_boundary_v2=True)


@pytest.mark.parametrize(('field', 'value'), [
    ('tags', [[1, 2, 1, 7]]),
    ('rows', [[4, 15]]),
])
def test_streamed_v2_reports_first_semantic_row_divergence(
        tmp_path: Path, field: str, value: JsonValue) -> None:
    path = tmp_path / 'raw.log'
    _boundary_v2_log(path)
    _change_boundary_v2(path, 'RTL', 'PUBLIC_READY', {field: value})
    detail = 'tags\\[0\\]\\.output_rows' if field == 'tags' else \
        'rows\\[0\\]\\.mesh_total_rows'
    with pytest.raises(ValueError, match=rf'work 0 cycle 2 {detail}'):
        _ = compare_selected_events(path, (0, 1), require_counts=True,
                                    require_boundary_v2=True)


def test_streamed_optional_v2_compares_when_present(tmp_path: Path) -> None:
    path = tmp_path / 'raw.log'
    _boundary_v2_log(path)
    _change_boundary_v2(path, 'RTL', 'PUBLIC_READY', {'tags': [[1, 2, 1, 7]]})
    with pytest.raises(ValueError, match=r'work 0 cycle 2 tags\[0\]\.output_rows'):
        _ = compare_selected_events(path, (0, 1), require_counts=True)


@pytest.mark.parametrize('mutation', ('missing_schema', 'duplicate_key', 'partial',
                                      'malformed_shape', 'duplicate'))
def test_streamed_required_v2_rejects_malformed_boundary(
        tmp_path: Path, mutation: str) -> None:
    path = tmp_path / 'raw.log'
    _boundary_v2_log(path)
    raw = path.read_text()
    rtl = next(line for line in raw.splitlines() if line.startswith('RTL_BOUNDARY_V2 '))
    if mutation == 'missing_schema':
        row = object_value(json.loads(rtl.partition(' ')[2]))
        del row['boundary_schema']
        raw = raw.replace(rtl, 'RTL_BOUNDARY_V2 ' + json.dumps(row, separators=(',', ':')), 1)
    elif mutation == 'duplicate_key':
        raw = raw.replace('"boundary_schema":2', '"boundary_schema":2,"boundary_schema":2', 1)
    elif mutation == 'partial':
        raw = raw.replace(rtl + '\n', '', 1)
    elif mutation == 'malformed_shape':
        raw = raw.replace('"rows":[[4,16]]', '"rows":[[4,16,0]]', 1)
    else:
        raw = raw.replace(rtl + '\n', rtl + '\n' + rtl + '\n', 1)
    _ = path.write_text(raw)
    with pytest.raises(ValueError, match='boundary|JSON|duplicate'):
        _ = compare_selected_events(path, (0, 1), require_counts=True,
                                    require_boundary_v2=True)


def test_streamed_paired_boundaries_preserve_diagnostic_rtl_fields(tmp_path: Path) -> None:
    # Given: all three paired phases, with RTL-only physical pointer/fire diagnostics.
    path = tmp_path / 'raw.log'
    _boundary_log(path)

    # When/Then: only shared state and identity are paired.
    assert compare_selected_events(path, (0, 1), require_counts=True) == (2, 2)


def test_streamed_boundary_bank_bit_reports_first_divergence(tmp_path: Path) -> None:
    # Given: one copied RTL bank pipe bit differs at the public-ready boundary.
    path = tmp_path / 'raw.log'
    _boundary_log(path)
    bank = [[0] * 6 for _ in range(4)]
    bank[2][3] = 1
    _change_boundary(path, 'RTL', 'PUBLIC_READY',
                     {'bank_pipe': list[JsonValue](list[JsonValue](bits) for bits in bank)})

    # When/Then: the first changed work, cycle, and bit are reported.
    with pytest.raises(ValueError, match=r'work 0 cycle 2 bank_pipe\[2\]\[3\]'):
        _ = compare_selected_events(path, (0, 1), require_counts=True)


@pytest.mark.parametrize(('changes', 'field'), [
    ({'rows': [[5, 3], [4, 2]]}, 'rows'),
    ({'tags': [[2, 8, 1, 0], [1, 16, 0, 0]]}, 'tags'),
    ({'row_count': 1}, 'row_count'),
    ({'rows': [[4, 2, 0], [5, 3]]}, 'rows'),
    ({'bank_pipe': [[0] * 6 for _ in range(3)]}, 'bank_pipe'),
    ({'bank_pipe': [[0] * 6, [0] * 6, [0, 0, 0, 2, 0, 0], [0] * 6]},
     r'bank_pipe\[2\]\[3\]'),
    ({'cycle': 3}, 'cycle'),
    ({'phase': 'UNKNOWN'}, 'phase'),
    ({'ordinal': 1}, 'ordinal'),
    ({'work_id': 9}, 'work_id'),
])
def test_streamed_boundary_changed_field_rejected(
        tmp_path: Path, changes: Record, field: str) -> None:
    # Given: a paired stream with one mutated shared field or identity.
    path = tmp_path / 'raw.log'
    _boundary_log(path)
    _change_boundary(path, 'RTL', 'PUBLIC_READY', changes)

    # When/Then: the mismatch is attributed to the first work and cycle.
    with pytest.raises(ValueError, match=rf'work 0 cycle 2 {field}'):
        _ = compare_selected_events(path, (0, 1), require_counts=True)


def test_streamed_boundary_phase_order_rejected(tmp_path: Path) -> None:
    # Given: two RTL work-1 boundary phases appear in reverse order.
    path = tmp_path / 'raw.log'
    _boundary_log(path)
    lines = path.read_text().splitlines()
    offer = next(i for i, line in enumerate(lines)
                 if line.startswith('RTL_BOUNDARY {"phase":"WORK1_OFFER"'))
    fire = next(i for i, line in enumerate(lines)
                if line.startswith('RTL_BOUNDARY {"phase":"WORK1_FIRE"'))
    lines[offer], lines[fire] = lines[fire], lines[offer]
    _ = path.write_text('\n'.join(lines) + '\n')

    # When/Then: the first expected phase is named at work 1.
    with pytest.raises(ValueError, match=r'work 1 cycle 2 phase'):
        _ = compare_selected_events(path, (0, 1), require_counts=True)


@pytest.mark.parametrize('mutation', ('missing', 'duplicate', 'extra_model', 'partial',
                                      'bad_json', 'duplicate_key'))
def test_streamed_boundary_incomplete_or_malformed_rejected(
        tmp_path: Path, mutation: str) -> None:
    # Given: a boundary stream with missing, extra, partial, or invalid JSON evidence.
    path = tmp_path / 'raw.log'
    _boundary_log(path)
    raw = path.read_text()
    rtl = next(line for line in raw.splitlines() if line.startswith('RTL_BOUNDARY '))
    model_fire = next(line for line in raw.splitlines()
                      if line.startswith('MODEL_BOUNDARY {"phase":"WORK1_FIRE"'))
    rtl_fire = next(line for line in raw.splitlines()
                    if line.startswith('RTL_BOUNDARY {"phase":"WORK1_FIRE"'))
    if mutation == 'missing':
        raw = raw.replace(model_fire + '\n', '', 1)
    elif mutation == 'duplicate':
        raw = raw.replace(rtl + '\n', rtl + '\n' + rtl + '\n', 1)
    elif mutation == 'extra_model':
        raw = raw.replace(model_fire + '\n', model_fire + '\n' + model_fire + '\n', 1)
    elif mutation == 'partial':
        raw = raw.replace(rtl_fire + '\n', '', 1).replace(model_fire + '\n', '', 1)
    elif mutation == 'bad_json':
        raw = raw.replace(rtl, rtl.replace('"row_count":2', '"row_count":', 1), 1)
    else:
        raw = raw.replace(rtl, rtl.replace('"row_count":2',
                                           '"row_count":2,"row_count":2', 1), 1)
    _ = path.write_text(raw)

    # When/Then: no incomplete or ambiguous boundary evidence is accepted.
    with pytest.raises(ValueError, match='boundary|JSON|duplicate'):
        _ = compare_selected_events(path, (0, 1), require_counts=True)


@pytest.mark.parametrize('source', ('source-hash', 'trace-bytes'))
def test_resealed_changed_source_rejected_at_admission(tmp_path: Path, source: str) -> None:
    # Given: a recomputed outer seal cannot replace a source file or source hash.
    stimulus = _sealed_manifest(tmp_path)
    if source == 'source-hash':
        object_value(stimulus['source_sha256'])['sim/cycle/execution_pipeline_contract.py'] = '0' * 64
        reason = 'stimulus source digest mismatch'
    else:
        trace = object_value(object_value(stimulus['producer_artifacts'])['trace'])
        _ = Path(str(trace['path'])).write_text('{"changed":true}\n')
        reason = 'stimulus producer trace digest mismatch'
    stimulus['stimulus_sha256'] = sequence.stimulus_digest(stimulus)

    # When/Then: source closure is checked before a result can be accepted.
    with pytest.raises(ValueError, match=reason):
        sequence.validate_stimulus(stimulus)


def test_changed_build_artifact_rejected_by_binding_verifier(tmp_path: Path) -> None:
    # Given: a self-consistent build binding whose one emitted artifact changes later.
    profile = 'a4w4-d16-hp1'
    names = ('rtl/top.sv', *(f'rtl-test-obj/{name}' for name in OBJECTS), RUN_AWARE_BINARY)
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(b'original')
    _ = (tmp_path / 'resolved-profile.json').write_text('{}\n')
    binding: Record = {'schema': 'im2p-rtl-build-binding', 'version': 1,
                       'execution_kind': 'FRESH_BUILD', **build_inputs(profile),
                       'artifact_sha256': {name: sha256(tmp_path / name) for name in names}}
    binding['sha256'] = contract_digest(binding)
    _ = (tmp_path / 'rtl-build-binding.json').write_text(json.dumps(binding) + '\n')
    assert verify_build(tmp_path, profile) == binding
    _ = (tmp_path / 'rtl/top.sv').write_bytes(b'changed')

    # When/Then: the stale sealed build cannot pass the official build verifier.
    with pytest.raises(ValueError, match=r'RTL build artifact changed: rtl/top\.sv'):
        _ = verify_build(tmp_path, profile)


def test_static_cli_rejects_resealed_bad_source_before_output(tmp_path: Path) -> None:
    # Given: a well-formed seal with a stale source-closure entry.
    stimulus = _sealed_manifest(tmp_path)
    object_value(stimulus['source_sha256'])['sim/cycle/execution_pipeline_contract.py'] = '0' * 64
    stimulus['stimulus_sha256'] = sequence.stimulus_digest(stimulus)
    path = tmp_path / 'resealed-bad-source.json'
    _ = path.write_text(json.dumps(stimulus, indent=2, sort_keys=True) + '\n')
    out = tmp_path / 'out'

    # When: the public static CLI consumes the bad source-bound manifest.
    result = subprocess.run(
        [sys.executable, '-B', str(Path(sequence.__file__)), '--stimulus', str(path),
         '--case', str(stimulus['case_id']), '--out', str(out)],
        capture_output=True, text=True, check=False, timeout=30,
    )

    # Then: rejection is nonzero, names the source boundary, and publishes no report.
    assert result.returncode == 1
    assert 'stimulus source digest mismatch' in result.stderr
    assert not out.exists()
