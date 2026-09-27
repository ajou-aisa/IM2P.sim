from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from sim.cycle.npu_trace_schema import Record, integer, object_value, unique_pairs
from sim.cycle.reconstruct_graph import array, sha256
from sim.tests.cycle.production_run_work import read_work_fixture

RUN_KEYS: Final = ('original_block_id', 'original_k_mask',
                   'compact_k_begin', 'compact_k_count')
NUMERIC_SOURCES: Final = (
    Path(__file__).resolve(),
    Path(__file__).resolve().parents[3] / 'sim/tests/cycle/production_run_work.py',
    Path(__file__).resolve().parents[3] / 'fpga/gemmini_hp1/host/run_aware_numeric_fixture.hpp',
)


class ValueFixtureError(ValueError):
    detail: str

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class ValueBinding:
    path: Path
    sha256: str
    work_id: int
    ordinal: int
    expected_count: int


def value_binding(stimulus: Record) -> ValueBinding | None:
    if 'value_fixture' not in stimulus:
        return None
    declared = object_value(stimulus['value_fixture'])
    if set(declared) != {'schema', 'path', 'sha256', 'work_id'} or declared['schema'] != 'RMD_RUN_WORK_V1':
        raise ValueFixtureError('value fixture binding shape differs')
    raw_path = declared['path']
    if not isinstance(raw_path, str) or not Path(raw_path).is_absolute() or not Path(raw_path).is_file():
        raise ValueFixtureError('value fixture missing')
    path = Path(raw_path)
    digest = declared['sha256']
    if not isinstance(digest, str) or re.fullmatch(r'[0-9a-f]{64}', digest) is None or sha256(path) != digest:
        raise ValueFixtureError('value fixture digest mismatch')
    work_id = integer(declared, 'work_id')
    matches = [object_value(row) for row in array(stimulus['works'])
               if object_value(row).get('work_id') == work_id]
    if len(matches) != 1:
        raise ValueFixtureError('value fixture work ID differs')
    work = matches[0]
    if work.get('scope') != 'residual_compact':
        raise ValueFixtureError('value fixture requires residual work')
    try:
        fixture = read_work_fixture(path)
    except (ValueError, IndexError) as error:
        raise ValueFixtureError(f'value fixture malformed: {error}') from error
    if fixture['descriptor'][22] != work_id:
        raise ValueFixtureError('value fixture work ID differs')
    inputs = object_value(work['input'])
    profile = re.fullmatch(r'a([48])w([48])-d(16|32|64)-hp1', str(stimulus['profile']))
    if profile is None:
        raise ValueFixtureError('value fixture profile differs')
    profile_shape = [int(group) for group in profile.groups()]
    shape = [integer(inputs, key) for key in ('m', 'n', 'k')]
    tile = [integer(inputs, key) for key in ('tile_i_count', 'tile_j_count', 'tile_k_count')]
    strides = [integer(inputs, key) for key in
               ('activation_stride_bytes', 'weight_stride_bytes', 'output_stride_bytes')]
    if (fixture['shape'] != shape or fixture['tile'] != tile or
            fixture['descriptor'][9:11] + [fixture['descriptor'][11] * 4] != strides or
            fixture['descriptor'][16] != integer(inputs, 'scale_stride_elements') or
            [fixture['descriptor'][index] for index in (1, 3, 5)] != profile_shape or
            fixture['geometry'][2:5] != profile_shape or
            fixture['geometry'][12:16] != [shape[0], 0, shape[0], 0] or
            fixture['original_k'] != integer(work, 'original_k')):
        raise ValueFixtureError('value fixture request differs')
    runs = [[integer(object_value(row), key) for key in RUN_KEYS]
            for row in array(work['runs'])]
    if fixture['runs'] != runs:
        raise ValueFixtureError('value fixture runs differ')
    return ValueBinding(path, digest, work_id, integer(work, 'ordinal'), shape[0] * shape[1])


def validated_values(raw: str, stimulus: Record, works: list[Record]) -> list[Record]:
    binding = value_binding(stimulus)
    rows = [line.removeprefix('NUMERIC_WORK ') for line in raw.splitlines()
            if line.startswith('NUMERIC_WORK ')]
    if binding is None:
        if rows:
            raise ValueFixtureError('unexpected numeric fixture evidence')
        return works
    if len(rows) != 1:
        raise ValueFixtureError('numeric fixture evidence missing')
    try:
        row = object_value(json.loads(rows[0], object_pairs_hook=unique_pairs))
    except (ValueError, TypeError) as error:
        raise ValueFixtureError('numeric fixture evidence malformed') from error
    if set(row) != {'ordinal', 'work_id', 'expected_count', 'actual_count',
                    'first_expected', 'first_actual', 'numeric_pass'}:
        raise ValueFixtureError('numeric fixture evidence malformed')
    if (row['ordinal'], row['work_id']) != (binding.ordinal, binding.work_id):
        raise ValueFixtureError('numeric fixture work ID differs')
    first_expected, first_actual = row['first_expected'], row['first_actual']
    if (any(type(row[key]) is not int or row[key] != binding.expected_count
            for key in ('expected_count', 'actual_count')) or
            any(type(value) is not int or not -(2**31) <= value < 2**31
                for value in (first_expected, first_actual)) or
            first_expected != first_actual or row['numeric_pass'] is not True):
        raise ValueFixtureError('numeric fixture failed')
    return works


def value_argv(stimulus: Record) -> list[str]:
    binding = value_binding(stimulus)
    return [] if binding is None else ['--values', str(binding.path),
                                       '--work-id', str(binding.work_id)]


def value_report(stimulus: Record, executed: bool) -> Record:
    binding = value_binding(stimulus)
    if binding is None:
        return {}
    return {'value_fixture': {'schema': 'RMD_RUN_WORK_V1', 'path': str(binding.path),
                              'sha256': binding.sha256, 'work_id': binding.work_id,
                              'ordinal': binding.ordinal, 'expected_count': binding.expected_count,
                              'status': 'PASS' if executed else 'RUNTIME_PENDING'}}
