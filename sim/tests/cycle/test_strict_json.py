"""strict_json equals the pinned reconstruction helpers: same values, same duplicate-key and format errors."""
from __future__ import annotations

import gzip
import io
import json
from pathlib import Path

import pytest

from sim.cycle.npu_trace_schema import unique_pairs
from sim.cycle.reconstruct_cpu import encoded_key
from sim.cycle.reconstruct_graph import emit, json_records
from sim.cycle.strict_json import record_line, strict_loads, strict_records

VALID = [
    '{"a":1,"b":[1,2,{"c":null}],"d":{"e":"\\u00e9","f":-0.0}}',
    '{"big":18446744073709551616,"neg":-9223372036854775809,"exp":1e300,"frac":0.1}',
    '{"nested":{"x":{"y":{"z":[{"k":true},{"k":false}]}}}}',
    '{}', '{"unicode":"한글 ✓","esc":"\\n\\t\\"q\\""}',
]
DUPLICATES = ['{"a":1,"a":2}', '{"a":{"b":1,"b":2}}', '{"x":[{"k":1},{"k":2,"j":3,"k":4}]}', '{"a":1,"b":2,"b":3,"a":4}']


def reference(text: str) -> object:
    return json.loads(text, object_pairs_hook=unique_pairs)


@pytest.mark.parametrize('text', VALID)
def test_values_and_key_order_match_unique_pairs(text: str) -> None:
    value = strict_loads(text)
    assert value == reference(text)
    assert json.dumps(value) == json.dumps(reference(text))  # same key order, same number types


@pytest.mark.parametrize('text', DUPLICATES + ['﻿{"a":1}', '{"a":1', '[1,2', '{"a":NaN}x'])
def test_errors_match_unique_pairs(text: str) -> None:
    with pytest.raises(ValueError) as expected:
        reference(text)
    with pytest.raises(ValueError) as actual:
        strict_loads(text)
    assert type(actual.value) is type(expected.value) and str(actual.value) == str(expected.value)


def records_of(function, path: Path) -> tuple[list[object], str | None]:  # type: ignore[no-untyped-def]
    rows: list[object] = []
    try:
        for row in function(path):
            rows.append(row)  # noqa: PERF402 (rows read before a failure are part of the comparison)
    except ValueError as error:
        return rows, f'{type(error).__name__}: {error}'
    return rows, None


@pytest.mark.parametrize('lines', [
    VALID, VALID[:2] + ['   '] + VALID[2:], VALID[:1] + ['[1,2]'], VALID[:2] + DUPLICATES[1:2], ['﻿' + VALID[0]],
])
@pytest.mark.parametrize('suffix', ['.jsonl', '.jsonl.gz'])
def test_records_match_json_records(tmp_path: Path, lines: list[str], suffix: str) -> None:
    path = tmp_path / ('input' + suffix)
    text = ''.join(line + '\n' for line in lines)
    if suffix.endswith('.gz'):
        with gzip.open(path, 'wt', encoding='utf-8') as stream:
            stream.write(text)
    else:
        path.write_text(text, encoding='utf-8')
    assert records_of(strict_records, path) == records_of(json_records, path)


def test_record_line_is_the_emitted_line() -> None:
    for text in VALID:
        record = reference(text)
        assert isinstance(record, dict)
        stream = io.StringIO()
        emit(stream, record)
        assert record_line(record) == stream.getvalue()


def test_encoded_key_is_unchanged() -> None:
    for key in (('prefill', None, 0, 12), ('decode', 5, 1, 300), ('decode', 127, 0, 0)):
        assert encoded_key(key) == json.dumps(key, separators=(',', ':'))
        assert encoded_key(key) is encoded_key(key)
