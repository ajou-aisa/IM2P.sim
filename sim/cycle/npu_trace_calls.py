from __future__ import annotations

from dataclasses import dataclass, field

from sim.cycle.npu_trace_schema import NpuTraceError, Record, Work, integer, require, text

KINDS = {'FULL', 'STRIPE', 'FENCE', 'RESIDUAL_PREPARE', 'RESIDUAL_COMPACT', 'RESIDUAL_RECOMPOSE', 'RESIDUAL_MERGE'}
ORDER = ('PREPARE', 'INVOKE', 'PUBLISH', 'COMPLETE_REQUIRED', 'FENCE', 'CONTINUATION')


class CompletedIDs:
    def __init__(self) -> None:
        self.cursor = 0
        self.ahead: set[int] = set()

    def contains(self, identity: int) -> bool:
        return identity < self.cursor or identity in self.ahead

    def add(self, identity: int) -> None:
        require(not self.contains(identity), 'duplicate completed identity')
        self.ahead.add(identity)
        while self.cursor in self.ahead:
            self.ahead.remove(self.cursor)
            self.cursor += 1


@dataclass(slots=True)
class Call:
    operation: int
    node: int
    parent: int | None
    kind: str
    stages: list[str] = field(default_factory=list)
    works: set[int] = field(default_factory=set)
    required: set[int] = field(default_factory=set)


class Calls:
    def __init__(self) -> None:
        self.active: dict[int, Call] = {}
        self.done = CompletedIDs()
        self.count = 0
        self.work_operations: dict[int, int] = {}
        self.operation_works: dict[int, set[int]] = {}
        self.call_operations: dict[int, int] = {}
        self.parent_operations: dict[int, int] = {}

    def stage(self, record: Record) -> None:
        identity, operation, node = (integer(record, key) for key in ('call_id', 'operation_id', 'node_id'))
        parent = None if record['parent_id'] is None else integer(record, 'parent_id')
        kind, stage = text(record, 'call_kind'), text(record, 'stage')
        require(kind in KINDS and stage in ORDER, 'unsupported NPU call kind/stage')
        require(not self.done.contains(identity), 'stage after call continuation')
        if identity not in self.active:
            require(stage == 'PREPARE', 'call must begin with PREPARE')
            require(identity == self.count, 'duplicate/gapped/nonmonotonic call identity')
            self.active[identity] = Call(operation, node, parent, kind)
            self.call_operations[identity] = operation
            self.count += 1
        call = self.active[identity]
        require((operation, node, kind) == (call.operation, call.node, call.kind), 'call ownership mismatch')
        if call.parent is None:
            call.parent = parent
        require(parent == call.parent, 'call parent changed')
        if parent is not None:
            require(self.parent_operations.get(parent, operation) == operation, 'parent has multiple operations')
            self.parent_operations[parent] = operation
        require(stage not in call.stages and (not call.stages or ORDER.index(stage) > ORDER.index(call.stages[-1])),
                'duplicate or reordered NPU call stage')
        require(stage in ('PREPARE', 'INVOKE') or 'INVOKE' in call.stages, 'call stage before INVOKE')
        values = record['required_work_ids']
        if not isinstance(values, list):
            raise NpuTraceError('required_work_ids array required')
        required = [integer({'work_id': value}, 'work_id') for value in values]
        require(len(required) == len(set(required)), 'duplicate required work identity')
        require(not required or stage == 'COMPLETE_REQUIRED', 'dependencies only belong to COMPLETE_REQUIRED')
        require(all(self.work_operations.get(work) == operation for work in required), 'unknown/cross-operation required work')
        require(stage != 'PUBLISH' or kind == 'STRIPE', 'PUBLISH requires STRIPE call')
        require(stage != 'FENCE' or kind == 'FENCE', 'FENCE requires FENCE call')
        if stage == 'COMPLETE_REQUIRED':
            call.required.update(required)
        if stage == 'PUBLISH':
            require(bool(call.works), 'PUBLISH before selected work')
        call.stages.append(stage)
        if stage == 'CONTINUATION':
            if kind in ('FULL', 'STRIPE', 'RESIDUAL_COMPACT'):
                require(len(call.works) == 1 and call.works <= call.required, 'continuation lacks own work completion dependency')
                require(kind != 'STRIPE' or 'PUBLISH' in call.stages, 'stripe continuation without PUBLISH')
            else:
                require(not call.works, 'host/fence call cannot own NPU work')
            require(kind != 'FENCE' or {'COMPLETE_REQUIRED', 'FENCE'} <= set(call.stages), 'incomplete fence call')
            del self.active[identity]
            self.done.add(identity)

    def work(self, work: Work) -> None:
        require(work.call_id in self.active, 'NPU work has no declared active call')
        call = self.active[work.call_id]
        require(call.stages == ['PREPARE', 'INVOKE'], 'work outside call invocation boundary')
        require((work.operation_id, work.node_id, work.parent_id) == (call.operation, call.node, call.parent),
                'work/call ownership mismatch')
        expected = {'full': 'FULL', 'stripe': 'STRIPE', 'residual_compact': 'RESIDUAL_COMPACT'}[work.scope]
        require(call.kind == expected and not call.works, 'work/call kind mismatch or duplicate call work')
        call.works.add(work.identity)
        self.work_operations[work.identity] = work.operation_id
        self.operation_works.setdefault(work.operation_id, set()).add(work.identity)

    def operation_complete(self, identity: int) -> None:
        require(not any(call.operation == identity for call in self.active.values()), 'operation has unfinished NPU call')
        for work in self.operation_works.pop(identity, set()):
            del self.work_operations[work]
        self.call_operations = {key: owner for key, owner in self.call_operations.items() if owner != identity}
        self.parent_operations = {key: owner for key, owner in self.parent_operations.items() if owner != identity}

    def boundary(self) -> None:
        require(not self.active and self.done.cursor == self.count and not self.done.ahead, 'unfinished/missing NPU call at phase boundary')
