from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath

from sim.cycle.npu_trace_calls import Calls
from sim.cycle.npu_trace_schema import NpuTraceError, Record, integer, require, text


def references(record: Record, key: str) -> list[int]:
    values = record[key]
    if not isinstance(values, list):
        raise NpuTraceError(key + ': array required')
    result = [integer({'id': value}, 'id') for value in values]
    require(len(result) == len(set(result)), 'duplicate dependency references')
    return result


class Hosts:
    def __init__(self) -> None:
        self.active: dict[int, Record] = {}
        self.completed_owners: dict[int, int] = {}
        self.counts: Counter[str] = Counter({'POTAL_HOST': 0, 'FUNCTIONAL_EMULATION': 0})
        self.count = 0
        self.completed = 0

    def consume(self, record: Record, calls: Calls) -> None:
        identity, operation = integer(record, 'host_stage_id'), integer(record, 'operation_id')
        kind = text(record, 'execution_class')
        require(kind in self.counts, 'invalid host stage execution class')
        text(record, 'stage_name')
        require(record['source_owner'] in ('llama.cpp-gemmini', 'IM2P.sim'), 'invalid host stage source owner')
        location = text(record, 'source_location')
        path, delimiter, function = location.partition(':')
        require(bool(delimiter and function) and not PurePosixPath(path).is_absolute() and
                '..' not in PurePosixPath(path).parts and '\\' not in path, 'host source location must be relative file:function')
        if record['parent_id'] is not None:
            require(calls.parent_operations.get(integer(record, 'parent_id')) == operation, 'host parent ownership mismatch')
        if record['call_id'] is not None:
            require(calls.call_operations.get(integer(record, 'call_id')) == operation, 'host call ownership mismatch')
        descriptor = {key: value for key, value in record.items() if key not in ('sequence', 'event', 'status')}
        if record['event'] == 'BEGIN':
            require(identity == self.count and record['status'] == 'declared', 'duplicate/unordered host stage declaration')
            require(all(calls.work_operations.get(work) == operation for work in references(record, 'required_work_ids')),
                    'host dependency references unknown/cross-operation work')
            require(all(self.completed_owners.get(stage) == operation for stage in references(record, 'required_host_stage_ids')),
                    'host dependency references incomplete/cross-operation stage')
            self.active[identity] = descriptor
            self.counts[kind] += 1
            self.count += 1
        else:
            require(record['event'] == 'END' and record['status'] == 'success', 'host stage did not complete successfully')
            require(self.active.get(identity) == descriptor, 'undeclared/duplicate/mutated host stage completion')
            del self.active[identity]
            self.completed_owners[identity] = operation
            self.completed += 1

    def operation_complete(self, identity: int) -> None:
        require(not any(record['operation_id'] == identity for record in self.active.values()), 'unfinished host stage')
        self.completed_owners = {key: owner for key, owner in self.completed_owners.items() if owner != identity}

    def before_work(self, record: Record) -> None:
        operation = integer(record, 'operation_id')
        require(all(self.completed_owners.get(stage) == operation for stage in references(record, 'required_host_stage_ids')),
                'NPU work references incomplete/cross-operation host preparation')

    def boundary(self) -> None:
        require(not self.active and self.count == self.completed, 'host stage coverage incomplete')
