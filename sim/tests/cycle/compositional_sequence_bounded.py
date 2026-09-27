from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter, deque
from collections.abc import Generator, Iterable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, final

from sim.cycle.npu_trace_schema import Record, integer, object_value, unique_pairs
from sim.tests.cycle.compositional_sequence_v2_base import AbsoluteOfferError
from sim.tests.cycle.compositional_sequence_v2_payload import (
    PayloadGeometry,
    payload_geometry,
)
from sim.tests.cycle.compositional_sequence_v2_stream import (
    _event,
    _queue_compare_edge,
    _queue_edge,
    _queue_order,
    _work,
)

# allow: SIZE_OK - one-pass bounded comparator state machine
Side = Literal['RTL', 'MODEL']
Source = Path | Iterable[bytes]
FactKind = Literal['run', 'events', 'queue', 'work', 'count']
WINDOW: Final = 4


@dataclass(frozen=True, slots=True)
class StreamSummary:
    lines: int
    bytes: int
    sha256: str
    events: int
    queue_edges: int
    works: int


@dataclass(frozen=True, slots=True)
class ComparisonReceipt:
    rtl: StreamSummary
    model: StreamSummary
    completed_work_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _Fact:
    kind: FactKind
    ordinal: int
    work_id: int
    cycle: int | None = None
    events: Counter[bytes] | None = None
    edge: tuple[int, Record, Record] | None = None
    count: int | None = None

    def label(self) -> str:
        if self.kind == 'events':
            assert self.events is not None
            return f'events work={self.work_id} cycle={self.cycle} values={dict(self.events)}'
        if self.kind == 'queue':
            assert self.edge is not None
            return f'queue generation={self.edge[0]} cycle={self.cycle}'
        return f'{self.kind} work={self.work_id} ordinal={self.ordinal}'


@contextmanager
def _open(source: Source) -> Generator[Iterator[bytes]]:
    if isinstance(source, Path):
        if source.suffix == '.gz':
            with gzip.open(source, 'rb') as stream:
                yield iter(stream)
        else:
            with source.open('rb') as stream:
                yield iter(stream)
    else:
        yield iter(source)


@final
class _Scanner:
    """Mutable one-pass parser retaining only one event cycle and queue edge."""

    def __init__(self, lines: Iterator[bytes], side: Side,
                 work_ids: tuple[int, ...], geometry: PayloadGeometry | None) -> None:
        self.lines = lines
        self.side = side
        self.work_ids = work_ids
        self.geometry = geometry
        self.digest = hashlib.sha256()
        self.line_count = self.byte_count = self.event_count = 0
        self.queue_count = self.work_count = self.ordinal = 0
        self.run_seen = False
        self.run_generation: int | None = None
        self.declared: set[int] = set()
        self.events_per_work = [0] * len(work_ids)
        self.queue_previous: tuple[int, int, Record] | None = None
        self.queue_enqueues = self.queue_dequeues = 0
        self.declared_enqueues = self.declared_dequeues = 0

    def _lines(self) -> Iterator[bytes]:
        for line in self.lines:
            if not line.endswith(b'\n'):
                raise AbsoluteOfferError(f'{self.side} stream line malformed')
            self.digest.update(line)
            self.line_count += 1
            self.byte_count += len(line)
            yield line

    def _count(self, line: bytes, prefix: bytes) -> _Fact:
        fields = line.removeprefix(prefix).split()
        if len(fields) != 2:
            raise AbsoluteOfferError(f'{self.side} selected event count malformed')
        ordinal, count = int(fields[0]), int(fields[1])
        if (not 0 <= ordinal < len(self.work_ids) or ordinal in self.declared or
                count < 0 or count != self.events_per_work[ordinal]):
            raise AbsoluteOfferError(
                f'{self.side} selected event count differs at work {ordinal}')
        self.declared.add(ordinal)
        return _Fact('count', ordinal, self.work_ids[ordinal], count=count)

    def _queue(self, line: bytes, kind: bytes) -> _Fact:
        generation, old, nxt = _queue_edge(line, kind, self.geometry)
        if self.side == 'MODEL' and self.queue_previous is None and any((
                integer(old, 'tag_enqueues'), integer(old, 'tag_dequeues'),
                integer(old, 'row_count'), integer(old, 'tag_count'))):
            raise AbsoluteOfferError(
                f'queue edge generation {generation} cycle {old["cycle"]} reset origin differs')
        self.queue_previous = _queue_order(
            self.queue_previous, generation, old, nxt, kind)
        if self.side == 'RTL':
            self.queue_enqueues += integer(old, 'tag_enq_fire')
            self.queue_dequeues += integer(old, 'tag_deq_fire')
        else:
            self.queue_enqueues += integer(nxt, 'tag_enqueues') - integer(old, 'tag_enqueues')
            self.queue_dequeues += integer(nxt, 'tag_dequeues') - integer(old, 'tag_dequeues')
        self.queue_count += 1
        return _Fact('queue', self.ordinal,
                     self.work_ids[min(self.ordinal, len(self.work_ids) - 1)],
                     integer(old, 'cycle'), edge=(generation, old, nxt))

    def facts(self) -> Iterator[_Fact]:
        run_prefix = b'COMPOSITION_RUN ' if self.side == 'RTL' else b'MODEL_RUN '
        event_prefix = b'RTL_EVENT ' if self.side == 'RTL' else b'MODEL_EVENT '
        queue_prefix = b'RTL_QUEUE_EDGE' if self.side == 'RTL' else b'MODEL_QUEUE_EDGE'
        work_prefix = b'COMPOSITION_WORK ' if self.side == 'RTL' else b'MODEL_WORK '
        count_prefix = (b'RTL_SELECTED_EVENT_COUNT ' if self.side == 'RTL'
                        else b'MODEL_SELECTED_EVENT_COUNT ')
        pending: tuple[int, int, int] | None = None
        last_event_cycle: int | None = None
        values: Counter[bytes] = Counter()
        for line in self._lines():
            fact: _Fact | None = None
            if line.startswith(run_prefix):
                if self.run_seen or self.ordinal or pending is not None or self.queue_count:
                    raise AbsoluteOfferError(f'{self.side} middle reset/run header')
                self.run_seen = True
                row = object_value(json.loads(
                    line.removeprefix(run_prefix), object_pairs_hook=unique_pairs))
                generation = row.get('generation')
                self.run_generation = generation if type(generation) is int else None
                fact = _Fact('run', -1, -1)
            elif line.startswith(event_prefix):
                if self.ordinal >= len(self.work_ids):
                    raise AbsoluteOfferError(f'{self.side} event after work coverage')
                cycle, event = _event(
                    line, event_prefix.rstrip(), self.ordinal, self.work_ids[self.ordinal])
                key = self.ordinal, self.work_ids[self.ordinal], cycle
                if last_event_cycle is not None and cycle < last_event_cycle:
                    raise AbsoluteOfferError(
                        f'{self.side} event cycle nonmonotonic at work {self.work_ids[self.ordinal]}')
                if pending is not None and key != pending:
                    yield _Fact('events', pending[0], pending[1], pending[2], values.copy())
                    values.clear()
                pending = key
                last_event_cycle = cycle
                values[event] += 1
                self.events_per_work[self.ordinal] += 1
                self.event_count += 1
                continue
            elif line.startswith(queue_prefix):
                version = b'V2' if line.startswith(queue_prefix + b'_V2 ') else b'V1'
                fact = self._queue(line, queue_prefix + b'_' + version)
            elif line.startswith(work_prefix):
                if self.ordinal >= len(self.work_ids):
                    raise AbsoluteOfferError(f'{self.side} extra work')
                row = _work(line, work_prefix, self.ordinal, self.work_ids[self.ordinal])
                self.declared_enqueues += integer(row, 'mesh_tag_enqueues')
                self.declared_dequeues += integer(row, 'mesh_tag_dequeues')
                fact = _Fact('work', self.ordinal, self.work_ids[self.ordinal])
                self.ordinal += 1
                self.work_count += 1
            elif line.startswith(count_prefix):
                fact = self._count(line, count_prefix)
            if fact is not None:
                if pending is not None and fact.kind != 'queue':
                    yield _Fact('events', pending[0], pending[1], pending[2], values.copy())
                    pending = None
                    values.clear()
                yield fact
                if fact.kind == 'work':
                    last_event_cycle = None
        if pending is not None:
            yield _Fact('events', pending[0], pending[1], pending[2], values.copy())
        if self.ordinal != len(self.work_ids) or self.declared != set(range(len(self.work_ids))):
            raise AbsoluteOfferError(f'{self.side} completed work/count prefix incomplete')
        if self.queue_count:
            if self.run_generation is not None and (
                    self.queue_previous is None or
                    self.queue_previous[0] != self.run_generation):
                raise AbsoluteOfferError(f'{self.side} queue generation differs from run')
            for field, declared, observed in (
                    ('mesh_tag_enqueues', self.declared_enqueues, self.queue_enqueues),
                    ('mesh_tag_dequeues', self.declared_dequeues, self.queue_dequeues)):
                if declared != observed:
                    raise AbsoluteOfferError(
                        f'{self.side} queue {field} whole-stream count differs')

    def drain(self) -> None:
        for _ in self._lines():
            pass

    def summary(self) -> StreamSummary:
        return StreamSummary(
            self.line_count, self.byte_count, self.digest.hexdigest(),
            self.event_count, self.queue_count, self.work_count)


def _compare(rtl: _Fact, model: _Fact,
             geometry: PayloadGeometry | None) -> None:
    if (rtl.kind, rtl.ordinal, rtl.work_id) != (model.kind, model.ordinal, model.work_id):
        if ((rtl.kind == 'events' or model.kind == 'events') and
                (rtl.ordinal, rtl.work_id) == (model.ordinal, model.work_id)):
            raise AbsoluteOfferError(
                'selected event per-cycle multiset differs: '
                f'rtl={rtl.label()} model={model.label()}')
        raise AbsoluteOfferError(f'fact identity differs: rtl={rtl.label()} model={model.label()}')
    if rtl.kind == 'events':
        if rtl.cycle != model.cycle or rtl.events != model.events:
            raise AbsoluteOfferError(
                f'selected event per-cycle multiset differs: rtl={rtl.label()} model={model.label()}')
    elif rtl.kind == 'queue':
        assert rtl.edge is not None and model.edge is not None
        if (rtl.edge[0], rtl.cycle) != (model.edge[0], model.cycle):
            raise AbsoluteOfferError(
                f'queue origin differs: rtl={rtl.label()} model={model.label()}')
        _ = _queue_compare_edge(
            *rtl.edge[1:], *model.edge[1:], rtl.edge[0], geometry)
    elif rtl.kind == 'count' and rtl.count != model.count:
        raise AbsoluteOfferError(
            f'selected event whole-work count differs at work {rtl.work_id}')


def compare_bounded_sequences(
        rtl_source: Source, model_source: Source,
        work_ids: tuple[int, ...], *,
        payload_stimulus: Record | None = None) -> ComparisonReceipt:
    """Compare exact selected events and queues with bounded streaming state."""
    if not work_ids or len(set(work_ids)) != len(work_ids):
        raise AbsoluteOfferError('original work IDs must be nonempty and unique')
    completed: list[int] = []
    history: deque[str] = deque(maxlen=WINDOW)
    geometry = payload_geometry(payload_stimulus) if payload_stimulus is not None else None
    with ExitStack() as stack:
        rtl_scanner = _Scanner(
            stack.enter_context(_open(rtl_source)), 'RTL', work_ids, geometry)
        model_scanner = _Scanner(
            stack.enter_context(_open(model_source)), 'MODEL', work_ids, geometry)
        rtl_facts, model_facts = rtl_scanner.facts(), model_scanner.facts()
        try:
            while True:
                rtl = next(rtl_facts, None)
                model = next(model_facts, None)
                if rtl is None or model is None:
                    if rtl is not model:
                        raise AbsoluteOfferError('semantic fact coverage differs')
                    break
                _compare(rtl, model, geometry)
                history.append(f'rtl={rtl.label()} model={model.label()}')
                if rtl.kind == 'count':
                    completed.append(rtl.work_id)
        except AbsoluteOfferError as error:
            after: list[str] = []
            for name, facts in (('rtl', rtl_facts), ('model', model_facts)):
                try:
                    for _ in range(WINDOW):
                        after.append(f'{name}={next(facts).label()}')
                except (StopIteration, AbsoluteOfferError) as tail_error:
                    if isinstance(tail_error, AbsoluteOfferError):
                        after.append(f'{name}_error={tail_error}')
            rtl_scanner.drain()
            model_scanner.drain()
            raise AbsoluteOfferError(
                f'bounded first divergence: {error}; completed_prefix={completed}; '
                f'window={list(history) + after}; rtl={rtl_scanner.summary()}; '
                f'model={model_scanner.summary()}') from error
        if not rtl_scanner.queue_count:
            raise AbsoluteOfferError('queue edge coverage missing')
        return ComparisonReceipt(
            rtl_scanner.summary(), model_scanner.summary(), tuple(completed))
