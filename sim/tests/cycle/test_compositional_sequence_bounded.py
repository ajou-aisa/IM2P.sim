# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# PYTHONPATH=. uv run pytest -q sim/tests/cycle/test_compositional_sequence_bounded.py
# ──────────────────
from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Literal, assert_never

import pytest

from sim.cycle.npu_trace_schema import Record, integer, object_value
from sim.tests.cycle.compositional_sequence_bounded import compare_bounded_sequences

WORK_IDS = (41, 73)
EventMutation = Literal['plus_one', 'duplicate', 'delete']
QueueMutation = Literal['occupancy', 'fire', 'id', 'rob']


def _queue_rows() -> tuple[Record, Record]:
    old: Record = {
        'cycle': 1, 'row_count': 0, 'rows': [], 'tag_count': 0, 'tags': [],
    }
    nxt: Record = {
        'cycle': 2, 'row_count': 1, 'rows': [[4, 16]],
        'tag_count': 1, 'tags': [[41, 1, 1, 7]],
    }
    return old, nxt


def _rtl_edge() -> bytes:
    old, nxt = _queue_rows()
    controls = {
        'row_read': 0, 'row_write': 0, 'row_empty': 1,
        'row_maybe_full': 0, 'row_enq_fire': 1, 'row_deq_fire': 0,
        'tag_read': 0, 'tag_write': 0, 'tag_enq_fire': 1,
        'tag_deq_fire': 0, 'request_valid': 1, 'last_response_fire': 0,
    }
    edge = {
        'queue_schema': 1, 'generation': 1,
        'old': {**old, **controls},
        'next': {
            **nxt, **controls, 'row_write': 1, 'row_empty': 0,
            'row_maybe_full': 1, 'row_enq_fire': 0, 'tag_write': 1,
            'tag_enq_fire': 0, 'request_valid': 0,
        },
    }
    return b'RTL_QUEUE_EDGE_V1 ' + json.dumps(
        edge, separators=(',', ':')).encode() + b'\n'


def _model_edge() -> bytes:
    old, nxt = _queue_rows()
    edge = {
        'queue_schema': 1, 'generation': 1,
        'old': {**old, 'tag_enqueues': 0, 'tag_dequeues': 0},
        'next': {**nxt, 'tag_enqueues': 1, 'tag_dequeues': 0},
    }
    return b'MODEL_QUEUE_EDGE_V1 ' + json.dumps(
        edge, separators=(',', ':')).encode() + b'\n'


def _lines(side: Literal['RTL', 'MODEL']) -> list[bytes]:
    run: Record = {
        'stimulus_sha256': '1' * 64, 'work_count': 2,
        'instance_count': 1, 'reset_count': 1,
    }
    if side == 'MODEL':
        run['generation'] = 1
    run_kind = b'COMPOSITION_RUN ' if side == 'RTL' else b'MODEL_RUN '
    event = b'RTL_EVENT' if side == 'RTL' else b'MODEL_EVENT'
    work = b'COMPOSITION_WORK ' if side == 'RTL' else b'MODEL_WORK '
    count = (b'RTL_SELECTED_EVENT_COUNT ' if side == 'RTL'
             else b'MODEL_SELECTED_EVENT_COUNT ')
    summaries = [
        {'ordinal': 0, 'work_id': 41, 'mesh_tag_enqueues': 1,
         'mesh_tag_dequeues': 0},
        {'ordinal': 1, 'work_id': 73, 'mesh_tag_enqueues': 0,
         'mesh_tag_dequeues': 0},
    ]
    return [
        run_kind + json.dumps(run, separators=(',', ':')).encode() + b'\n',
        event + b' 0 41 0 load\n',
        event + b' 0 41 0 load\n',
        event + b' 0 41 1 store\n',
        _rtl_edge() if side == 'RTL' else _model_edge(),
        work + json.dumps(summaries[0], separators=(',', ':')).encode() + b'\n',
        count + b'0 3\n',
        event + b' 1 73 5 done\n',
        work + json.dumps(summaries[1], separators=(',', ':')).encode() + b'\n',
        count + b'1 1\n',
    ]


def _write_sources(tmp_path: Path) -> tuple[Path, Path]:
    rtl = tmp_path / 'rtl.log'
    model = tmp_path / 'model.log.gz'
    rtl.write_bytes(b''.join(_lines('RTL')))
    with gzip.open(model, 'wb') as stream:
        stream.write(b''.join(_lines('MODEL')))
    return rtl, model


def _iter(lines: Iterable[bytes]) -> Iterator[bytes]:
    yield from lines


def test_bounded_comparator_accepts_plain_and_gzip_streams(tmp_path: Path) -> None:
    # Given: separate exact RTL and gzip-model streams with original IDs.
    rtl, model = _write_sources(tmp_path)

    # When: all selected events and queue edges are compared.
    receipt = compare_bounded_sequences(rtl, model, WORK_IDS)

    # Then: full logical streams are hashed and exact prefix coverage is returned.
    assert receipt.completed_work_ids == WORK_IDS
    assert (receipt.rtl.events, receipt.model.events) == (4, 4)
    assert (receipt.rtl.queue_edges, receipt.model.queue_edges) == (1, 1)
    assert receipt.rtl.sha256 == hashlib.sha256(b''.join(_lines('RTL'))).hexdigest()
    assert receipt.model.sha256 == hashlib.sha256(b''.join(_lines('MODEL'))).hexdigest()


def test_bounded_comparator_accepts_combined_file_through_two_iterators() -> None:
    # Given: one combined capture viewed independently by both side scanners.
    combined = _lines('RTL') + _lines('MODEL')

    # When/Then: opposite-side lines are ignored without changing whole-stream identity.
    receipt = compare_bounded_sequences(
        _iter(combined), _iter(combined), WORK_IDS)
    expected = hashlib.sha256(b''.join(combined)).hexdigest()
    assert receipt.rtl.sha256 == receipt.model.sha256 == expected
    assert receipt.completed_work_ids == WORK_IDS


def test_bounded_comparator_accepts_queue_payload_v2(tmp_path: Path) -> None:
    # Given: the existing synthetic queue-schema-v2 capture and geometry authority.
    from sim.tests.cycle.test_sequence_payload_v2 import _synthetic_v2

    path = tmp_path / 'combined-v2.log'
    authority = _synthetic_v2(path, 'a4w4-d16-hp1')

    # When/Then: the bounded comparator reuses exact V2 queue payload semantics.
    receipt = compare_bounded_sequences(
        path, path, (0, 1), payload_stimulus=authority)
    assert receipt.completed_work_ids == (0, 1)
    assert receipt.rtl.queue_edges == receipt.model.queue_edges == 1


def test_bounded_comparator_keeps_one_counter_across_queue_row() -> None:
    # Given: same-cycle selected events surround a queue edge on both sides.
    rtl, model = _lines('RTL'), _lines('MODEL')
    rtl.insert(5, b'RTL_EVENT 0 41 1 store\n')
    model.insert(5, b'MODEL_EVENT 0 41 1 store\n')
    rtl[7] = b'RTL_SELECTED_EVENT_COUNT 0 4\n'
    model[7] = b'MODEL_SELECTED_EVENT_COUNT 0 4\n'

    # When/Then: one cycle-local Counter retains both copies across the edge row.
    receipt = compare_bounded_sequences(_iter(rtl), _iter(model), WORK_IDS)
    assert receipt.rtl.events == receipt.model.events == 5


def test_bounded_comparator_rejects_event_regression_across_queue_row() -> None:
    # Given: RTL returns to cycle zero after a cycle-one event and queue edge.
    rtl, model = _lines('RTL'), _lines('MODEL')
    rtl.insert(5, b'RTL_EVENT 0 41 0 load\n')

    # When/Then: non-event rows cannot reset selected-event monotonicity.
    with pytest.raises(ValueError, match=r'event cycle nonmonotonic'):
        _ = compare_bounded_sequences(_iter(rtl), _iter(model), WORK_IDS)


@pytest.mark.parametrize('mutation', ('plus_one', 'duplicate', 'delete'))
def test_bounded_comparator_rejects_event_multiset_mutation(
        mutation: EventMutation) -> None:
    # Given: work 73 has one shifted, duplicated, or deleted RTL event.
    rtl, model = _lines('RTL'), _lines('MODEL')
    index = rtl.index(b'RTL_EVENT 1 73 5 done\n')
    match mutation:
        case 'plus_one':
            rtl[index] = b'RTL_EVENT 1 73 6 done\n'
        case 'duplicate':
            rtl.insert(index, rtl[index])
        case 'delete':
            _ = rtl.pop(index)
        case unreachable:
            assert_never(unreachable)

    # When/Then: exact per-cycle multiplicity fails after completed work 41.
    with pytest.raises(
            ValueError,
            match=r'per-cycle multiset differs.*completed_prefix=\[41\].*sha256='):
        compare_bounded_sequences(_iter(rtl), _iter(model), WORK_IDS)


@pytest.mark.parametrize('mutation', ('occupancy', 'fire', 'id', 'rob'))
def test_bounded_comparator_rejects_queue_mutation(
        mutation: QueueMutation) -> None:
    # Given: one RTL queue edge changes occupancy, fire, ID, or ROB validity.
    rtl, model = _lines('RTL'), _lines('MODEL')
    index = next(i for i, line in enumerate(rtl)
                 if line.startswith(b'RTL_QUEUE_EDGE_V1 '))
    edge = object_value(json.loads(rtl[index].removeprefix(b'RTL_QUEUE_EDGE_V1 ')))
    old, nxt = object_value(edge['old']), object_value(edge['next'])
    match mutation:
        case 'occupancy':
            nxt['row_count'] = integer(nxt, 'row_count') + 1
        case 'fire':
            old['tag_enq_fire'] = 0
        case 'id':
            tags = nxt['tags']
            assert isinstance(tags, list) and isinstance(tags[0], list)
            tag_id = tags[0][0]
            assert type(tag_id) is int
            tags[0][0] = tag_id + 1
        case 'rob':
            tags = nxt['tags']
            assert isinstance(tags, list) and isinstance(tags[0], list)
            tags[0][2] = 0
        case unreachable:
            assert_never(unreachable)
    rtl[index] = b'RTL_QUEUE_EDGE_V1 ' + json.dumps(
        edge, separators=(',', ':')).encode() + b'\n'

    # When/Then: reused queue shape/transition/comparison logic finds divergence.
    with pytest.raises(ValueError, match=r'bounded first divergence: .*queue|bounded first divergence: .*tag'):
        compare_bounded_sequences(_iter(rtl), _iter(model), WORK_IDS)


def test_bounded_comparator_rejects_middle_reset() -> None:
    # Given: a second producer run/reset appears between original works.
    rtl, model = _lines('RTL'), _lines('MODEL')
    rtl.insert(7, rtl[0])

    # When/Then: generation cannot restart inside completed-prefix comparison.
    with pytest.raises(
            ValueError,
            match=r'middle reset/run header.*completed_prefix=\[41\]'):
        compare_bounded_sequences(_iter(rtl), _iter(model), WORK_IDS)


def test_bounded_comparator_preserves_original_work_ids() -> None:
    # Given: ordinal one is relabeled instead of retaining producer work ID 73.
    rtl, model = _lines('RTL'), _lines('MODEL')
    rtl[7] = b'RTL_EVENT 1 1 5 done\n'

    # When/Then: helper rejects renumbering before accepting second work.
    with pytest.raises(ValueError, match=r'work order or ID differs.*completed_prefix=\[41\]'):
        compare_bounded_sequences(_iter(rtl), _iter(model), WORK_IDS)
