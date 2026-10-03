from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
import gzip
from pathlib import Path

from sim.cycle.npu_trace_schema import (
    CLASSES, NpuTraceError, Record, Run, SemanticKey, Work, integer, parse_record, parse_run, parse_work, require, semantic_key, text,
)
from sim.cycle.npu_trace_calls import Calls, CompletedIDs
from sim.cycle.npu_trace_hosts import Hosts


@dataclass(slots=True)
class Operation:
    works: int = 0
    node: int | None = None
    layer: str | None = None
    name: str | None = None
    dense_shape: tuple[int, int, int] | None = None
    semantic: SemanticKey | None = None


@dataclass(slots=True)
class Dispatch:
    first: Work
    next_row: int = 0
    next_stripe: int = 0


class TraceState:
    def __init__(self, run: Run) -> None:
        self.run = run
        self.phases: list[Record] = []
        self.pending: dict[int, Operation] = {}
        self.dispatches: dict[int, Dispatch] = {}
        self.dispatch_done = CompletedIDs()
        self.operation_done = CompletedIDs()
        self.calls = Calls()
        self.hosts = Hosts()
        self.operation_counts: Counter[str] = Counter({key: 0 for key in CLASSES})
        self.work_counts: Counter[str] = Counter()
        self.eligible_cpu = 0
        self.completed_operations = 0
        self.ended = False

    def operation(self, identity: int) -> Operation:
        require(not self.operation_done.contains(identity), 'record after operation completion')
        return self.pending.setdefault(identity, Operation())

    def phase_boundary(self) -> None:
        require(not self.pending and not self.dispatches, 'incomplete operation/parent at phase boundary')
        self.calls.boundary()
        self.hosts.boundary()
        require(self.operation_done.cursor == self.completed_operations, 'registered operation identity crosses phase boundary')

    def phase(self, record: Record) -> None:
        self.phase_boundary()
        identity = integer(record, 'phase_id')
        require(identity == len(self.phases), 'nonmonotonic phase identity')
        integer(record, 'input_tokens')
        expected = ('prefill', None) if identity == 0 else ('decode', identity-1)
        require((record['phase_kind'], record['decode_index']) == expected, 'invalid prefill/decode phase')
        if record['decode_index'] is not None:
            integer(record, 'decode_index')
        self.phases.append(record)

    def work(self, record: Record) -> Work:
        work = parse_work(record, self.run)
        self.hosts.before_work(record)
        require(work.identity == self.work_counts.total(), 'duplicate/gapped work identity')
        operation = self.operation(work.operation_id)
        require(operation.node in (None, work.node_id), 'operation/node identity mismatch')
        operation.node = work.node_id
        if operation.layer is not None:
            require((operation.layer, operation.name) == (work.layer, work.operation), 'work operation identity mismatch')
        operation.layer, operation.name = work.layer, work.operation
        if work.provenance == 'dense_main':
            shape = (work.parent_rows, work.inputs[1], work.inputs[2])
            require(operation.dense_shape in (None, shape), 'dense operation shape mismatch')
            operation.dense_shape = shape
        require(not self.dispatch_done.contains(work.parent_id), 'work after completed parent')
        dispatch = self.dispatches.setdefault(work.parent_id, Dispatch(work))
        first = dispatch.first
        require((work.operation_id, work.layer, work.operation, work.provenance, work.scope, work.parent_rows,
                 work.inputs[1:3], work.inputs[6:]) ==
                (first.operation_id, first.layer, first.operation, first.provenance, first.scope, first.parent_rows,
                 first.inputs[1:3], first.inputs[6:]), 'dispatch parent/descriptor mismatch')
        require(work.row_begin == dispatch.next_row, 'stripe overlap/gap')
        require(work.scope != 'stripe' or work.stripe_id == dispatch.next_stripe, 'duplicate/unordered stripe identity')
        dispatch.next_row += work.inputs[0]
        dispatch.next_stripe += 1
        if dispatch.next_row == work.parent_rows:
            del self.dispatches[work.parent_id]
            self.dispatch_done.add(work.parent_id)
        operation.works += 1
        self.calls.work(work)
        self.work_counts[work.provenance] += 1
        return work

    def call(self, record: Record) -> None:
        operation = self.operation(integer(record, 'operation_id'))
        node = integer(record, 'node_id')
        require(operation.node in (None, node), 'operation/call node mismatch')
        operation.node = node
        self.calls.stage(record)

    def host(self, record: Record) -> None:
        self.hosts.consume(record, self.calls)

    def complete_operation(self, record: Record) -> None:
        identity = integer(record, 'operation_id')
        operation = self.operation(identity)
        require(record['status'] == 'success', 'target operation failed')
        classification = text(record, 'selected_target')
        require(classification in CLASSES, 'unsupported operation classification')
        require(type(record['target_eligible']) is bool, 'target eligibility must be boolean')
        require(isinstance(record['reason'], str), 'target selection reason must be string')
        require(operation.node in (None, integer(record, 'node_id')), 'target operation/node mismatch')
        for key in ('layer', 'operation', 'actual_backend', 'activation_type', 'weight_type'):
            text(record, key)
        shape = tuple(integer(record, key) for key in ('m', 'n', 'k'))
        require((classification == 'EXCLUDED') == any(value == 0 for value in shape),
                'only empty target operations are excluded')
        require(operation.dense_shape in (None, shape), 'target operation/dense work shape mismatch')
        require(operation.layer is None or (operation.layer, operation.name) == (record['layer'], record['operation']),
                'target operation/work identity mismatch')
        require(integer(record, 'npu_work_count') == operation.works, 'operation work count mismatch')
        require((classification == 'TARGET_NPU') == (operation.works > 0), 'operation route/work mismatch')
        require(not any(d.first.operation_id == identity for d in self.dispatches.values()),
                'operation completion lacks full stripe coverage')
        self.hosts.operation_complete(identity)
        self.calls.operation_complete(identity)
        self.eligible_cpu += int(classification == 'ORDINARY_CPU' and record['target_eligible'] is True)
        self.operation_counts[classification] += 1
        self.completed_operations += 1
        del self.pending[identity]
        self.operation_done.add(identity)

    def end(self, record: Record) -> None:
        self.phase_boundary()
        require(bool(self.phases) and record['status'] == 'success' and isinstance(record['reason'], str), 'run incomplete/failed')
        expected = {'registered_operation_count': self.completed_operations, 'completed_operation_count': self.completed_operations,
                    'npu_work_count': self.work_counts.total(), 'call_count': self.calls.count, 'phase_count': len(self.phases),
                    'host_stage_count': self.hosts.count, 'completed_host_stage_count': self.hosts.completed,
                    'potal_host_count': self.hosts.counts['POTAL_HOST'],
                    'functional_emulation_count': self.hosts.counts['FUNCTIONAL_EMULATION']}
        expected.update({name.lower() + '_count': self.operation_counts[name] for name in CLASSES})
        for key, value in expected.items():
            require(integer(record, key) == value, 'independent run count mismatch: ' + key)
        require(self.operation_done.cursor == self.completed_operations and not self.operation_done.ahead,
                'missing registered target operation')
        self.ended = True

    def consume(self, record: Record) -> Work | None:
        require(not self.ended and record['run_id'] == self.run.identity, 'record after run end or mixed run identity')
        require(record['collection_run_id'] == self.run.collection_id, 'mixed collection identity')
        require(record['run_config_id'] == self.run.run_config_id, 'mixed run configuration reference')
        kind = text(record, 'kind')
        if kind not in ('PHASE', 'RUN_END'):
            require(bool(self.phases) and integer(record, 'phase_id') == len(self.phases)-1, 'missing/stale phase')
            key = semantic_key(record)
            require(key[:2] == (self.phases[-1]['phase_kind'], self.phases[-1]['decode_index']), 'semantic phase mismatch')
            operation = self.operation(integer(record, 'operation_id'))
            require(operation.semantic in (None, key), 'operation semantic identity changed')
            operation.semantic = key
        handlers = {'PHASE': self.phase, 'NPU_CALL': self.call, 'HOST_STAGE': self.host,
                    'TARGET_OPERATION': self.complete_operation, 'RUN_END': self.end}
        if kind == 'NPU_WORK':
            return self.work(record)
        require(kind in handlers, 'duplicate RUN or unknown record kind')
        handlers[kind](record)
        return None

    def summary(self) -> Record:
        require(self.ended, 'missing RUN_END')
        return {'profile': self.run.profile, 'run_id': self.run.identity, 'collection_run_id': self.run.collection_id,
                'run_config_id': self.run.run_config_id,
                'npu_work_count': self.work_counts.total(), 'dense_work_count': self.work_counts['dense_main'],
                'residual_work_count': self.work_counts['residual'], 'phase_count': len(self.phases),
                'operation_count': self.completed_operations, 'operation_counts': dict(self.operation_counts),
                'eligible_cpu_operation_count': self.eligible_cpu, 'call_count': self.calls.count,
                'host_stage_count': self.hosts.count, 'potal_host_count': self.hosts.counts['POTAL_HOST'],
                'functional_emulation_count': self.hosts.counts['FUNCTIONAL_EMULATION'],
                'target_operation_coverage': 'PASS', 'target_work_validation': 'PASS',
                'hardware_contract_sha256': self.run.contract_hash}


def read_records(path: Path) -> Iterator[Record]:
    with (gzip.open(path, 'rt', encoding='utf-8') if path.suffix == '.gz'
          else path.open(encoding='utf-8')) as stream:
        for sequence, line in enumerate(stream):
            require(bool(line.strip()), f'blank JSONL record at line {sequence+1}')
            record = parse_record(line)
            require(integer(record, 'sequence') == sequence, 'duplicate/gapped/nonmonotonic sequence')
            yield record


def start_trace(records: Iterator[Record]) -> TraceState:
    record = next(records, None)
    if record is None:
        raise NpuTraceError('empty log')
    return TraceState(parse_run(record))
