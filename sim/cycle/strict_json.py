"""Strict JSON reading and canonical writing with the exact behaviour of the reconstruction helpers, minus their
per-call overhead.

`strict_pairs` is the object hook of `npu_trace_schema.unique_pairs`: duplicate keys are detected on the key/value
pair list the decoder produces, before any dict exists, and the result is the same dict in the same order. Only a
duplicate builds an error message, by handing the pairs to `unique_pairs` itself, so the error is identical.
`strict_records` is `reconstruct_graph.json_records` (same blank-line and object checks, same leading-BOM rejection as
`json.loads`) with one shared decoder instead of a decoder per line. `record_line` is `reconstruct_graph.emit`'s line
with one shared encoder. The pinned modules are left unchanged; their unpinned callers use these instead.
"""
from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from pathlib import Path

from sim.cycle.npu_trace_schema import (
    JsonValue,
    Record,
    object_value,
    require,
    unique_pairs,
)


def strict_pairs(pairs: list[tuple[str, JsonValue]]) -> Record:
    result: Record = dict(pairs)
    if len(result) != len(pairs):
        return unique_pairs(pairs)  # raises the duplicate-key error at the first repeated key
    return result


_DECODER = json.JSONDecoder(object_pairs_hook=strict_pairs)
_COMPACT_SORTED = json.JSONEncoder(sort_keys=True, separators=(',', ':'), allow_nan=False)


def strict_loads(text: str) -> JsonValue:
    """json.loads(text, object_pairs_hook=unique_pairs) for str input."""
    if text.startswith('﻿'):
        raise json.JSONDecodeError('Unexpected UTF-8 BOM (decode using utf-8-sig)', text, 0)
    return _DECODER.decode(text)


def strict_records(path: Path) -> Iterator[Record]:
    """reconstruct_graph.json_records: one JSON object per non-blank line, duplicate keys rejected."""
    with (gzip.open(path, 'rt', encoding='utf-8') if path.suffix == '.gz'
          else path.open(encoding='utf-8')) as stream:
        for line in stream:
            require(bool(line.strip()), 'blank JSONL record')
            yield object_value(strict_loads(line))


def record_line(record: Record) -> str:
    """reconstruct_graph.emit's line for a record."""
    return _COMPACT_SORTED.encode({'schema': 'im2p-reconstruction', 'version': 1, **record}) + '\n'
