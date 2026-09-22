from __future__ import annotations

import json
import re
from typing import Final, NoReturn, TypeVar

_DEVICE_RECORD: Final = re.compile(r'\{"op":"rmd\.device_host_call",[^{}\r\n]*\}\r?\n')
_FIELDS: Final = frozenset((
    'op', 'kind', 'layer', 'start', 'end', 'delta', 'ns_start', 'ns_end', 'tid',
    'valid', 'reason', 'operation_success',
))
_CURRENT_FIELDS: Final = _FIELDS | frozenset((
    'cpu_work_cycles', 'cpu_work_cycles_valid', 'cpu_work_cycles_source',
    'cpu_work_cycles_unit', 'cpu_work_cycles_reason', 'thread_cpu_ns',
    'thread_cpu_valid', 'thread_cpu_reason', 'host_elapsed_ns',
    'host_elapsed_valid', 'host_elapsed_reason', 'host_execution_id',
    'host_start_ns', 'host_end_ns', 'host_start_tid', 'host_end_tid',
    'host_thread_id', 'thread_id', 'interval_class', 'duration_role',
    'exclusion_reason',
))
_T = TypeVar('_T')


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(f'non-JSON constant: {value}')


def _unique_object(pairs: list[tuple[str, _T]]) -> dict[str, _T]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('duplicate JSON diagnostic key')
    return result


def strip_device_host_call_records(output: str) -> str:
    def remove(match: re.Match[str]) -> str:
        try:
            record = json.loads(match.group(), parse_constant=_reject_constant,
                                object_pairs_hook=_unique_object)
        except ValueError:
            return match.group()
        return '' if isinstance(record, dict) and record.get('op') == 'rmd.device_host_call' \
            and set(record) in (_FIELDS, _CURRENT_FIELDS) else match.group()

    return _DEVICE_RECORD.sub(remove, output)
