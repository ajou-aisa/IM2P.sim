from __future__ import annotations

from sim.cycle.npu_trace_schema import NpuTraceError, Record, RowIdentity, RunSpan, integer, object_value, require


def parse_run_view(record: Record, profile: tuple[int, int, int],
                   m: int, n: int, k: int) -> tuple[int, tuple[RunSpan, ...], tuple[RowIdentity, ...]]:
    bits, _, dim = profile
    original_k = integer(record, 'original_k')
    require(0 < original_k <= 2**32 - 1 and 0 < m <= 2**32 - 1 and
            0 < n <= 2**32 - 1 and 0 < k <= 2**32 - 1,
            'run work exceeds public numerical integer widths')
    raw_runs = record['runs']
    if not isinstance(raw_runs, list):
        raise NpuTraceError('runs must be an array')
    require(0 < len(raw_runs) <= k, 'run count must be nonzero and bounded by compact K')
    spans: list[RunSpan] = []
    cursor = 0
    previous = -1
    fragments_per_block = 32 // min(dim, 32)
    for raw in raw_runs:
        item = object_value(raw)
        require(set(item) == {'original_block_id', 'original_k_mask', 'compact_k_begin', 'compact_k_count'},
                'invalid run fields')
        block = integer(item, 'original_block_id')
        mask = integer(item, 'original_k_mask')
        begin = integer(item, 'compact_k_begin')
        count = integer(item, 'compact_k_count')
        require(block <= (2**16 - 1) // fragments_per_block and block > previous and
                0 < mask < 2**32 and 0 < count <= 32 and mask.bit_count() == count and
                begin == cursor and block * 32 + mask.bit_length() <= original_k,
                'invalid run coverage/owner/mask')
        spans.append(RunSpan(block, mask, begin, count))
        cursor += count
        previous = block
    require(cursor == k, 'runs must cover compact K exactly')

    raw_rows = record['row_map']
    if not isinstance(raw_rows, list):
        raise NpuTraceError('row_map must be an array')
    source_rows = integer(record, 'source_row_count')
    source_begin = integer(record, 'source_row_begin')
    require(0 < source_rows <= 2**32 - 1 and source_begin + source_rows < 2**64 and len(raw_rows) == m,
            'row_map/source stripe extent mismatch')
    mapped: list[RowIdentity] = []
    last = (-1, -1)
    for raw in raw_rows:
        item = object_value(raw)
        require(set(item) == {'source_row', 'lane_id'}, 'invalid row_map fields')
        row = integer(item, 'source_row')
        lane = integer(item, 'lane_id')
        key = (lane, row)
        require(row < source_rows and lane < 32 // bits + 1 and key > last,
                'invalid/duplicate/unsorted original row or radix lane')
        mapped.append(RowIdentity(row, lane))
        last = key
    return original_k, tuple(spans), tuple(mapped)
