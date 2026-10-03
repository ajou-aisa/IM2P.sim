from __future__ import annotations

from sim.cycle.npu_trace_schema import Record, integer, require, text

NAMED_TIMING_FIELDS = ('cpu_work_cycles', 'cpu_work_cycles_source', 'cpu_work_cycles_unit', 'cpu_work_cycles_valid',
                       'cpu_work_cycles_reason', 'thread_cpu_ns', 'thread_cpu_valid', 'thread_cpu_reason',
                       'host_elapsed_ns', 'host_elapsed_valid', 'host_elapsed_reason', 'host_start_ns',
                       'host_end_ns', 'host_execution_id', 'thread_id', 'interval_class')
# Optional observed-host fields (Linux collectors): sched_getcpu at both endpoints, the cycle scope and the native
# sample reason. Older logs lack them; when present they are validated and carried to the timeline unchanged.
HOST_CORE_FIELDS = ('host_cpu_core_start', 'host_cpu_core_end', 'cpu_migrated', 'cpu_work_cycles_scope',
                    'cpu_work_cycles_sample_reason')


def valid_measurement(record: Record) -> None:
    require(record.get('valid') is True and record.get('cpu_service') is True,
            'required CPU service measurement invalid/excluded')
    start, end, delta = (integer(record, key) for key in ('start', 'end', 'delta'))
    require(end >= start and delta == end-start, 'CPU interval endpoint/delta mismatch')
    require((text(record, 'source'), text(record, 'unit')) in (
        ('host_tick', 'tick'), ('riscv_cycle', 'cycle'), ('linux_perf_cpu_cycles', 'cycle'),
        ('thread_cpu_clock', 'nanosecond'), ('steady_clock', 'nanosecond')), 'unknown CPU clock source/unit')
    require(record.get('operation_success', True) is True, 'host operation failed')


def duration_sample(record: Record) -> Record:
    keys = ('source', 'unit', 'start', 'end', 'delta', 'valid', 'worker_id', 'worker_count',
            'reason', 'sample_reason', 'source_line')
    return {**{name: record[name] for name in (*keys, *NAMED_TIMING_FIELDS, *HOST_CORE_FIELDS) if name in record},
            'stage': record['op']}


def validate_host_cores(record: Record) -> None:
    """Observed cores are non-negative CPU indices or null; cpu_migrated must equal start != end."""
    if not any(name in record for name in HOST_CORE_FIELDS):
        return
    require(all(name in record for name in HOST_CORE_FIELDS), 'incomplete host core timing fields')
    cores = [record['host_cpu_core_start'], record['host_cpu_core_end']]
    require(all(core is None or (type(core) is int and core >= 0) for core in cores), 'malformed host CPU core id')
    migrated = record['cpu_migrated']
    require(migrated is None if None in cores or record.get('thread_id') is None else migrated is (cores[0] != cores[1]),
            'host CPU migration flag differs from its cores')
    require(record['cpu_work_cycles_scope'] == ('user+kernel' if record.get('cpu_work_cycles_valid') is True else None),
            'CPU cycle scope must be user+kernel for valid cycles')


def validate_named_timing(record: Record, host: int | None) -> None:
    present = tuple(name for name in NAMED_TIMING_FIELDS if name in record)
    require(present == NAMED_TIMING_FIELDS, 'incomplete named timing contract')
    cycles_valid = record['cpu_work_cycles_valid'] is True
    require(type(record['cpu_work_cycles_valid']) is bool and record['cpu_work_cycles_unit'] == 'cycle',
            'invalid CPU work-cycle metric')
    if cycles_valid:
        require(integer(record, 'cpu_work_cycles') >= 0 and isinstance(record['cpu_work_cycles_source'], str) and
                bool(record['cpu_work_cycles_source']) and record['cpu_work_cycles_reason'] is None,
                'invalid CPU work-cycle metric')
    else:
        require(record['cpu_work_cycles'] is None and record['cpu_work_cycles_source'] is None and
                isinstance(record['cpu_work_cycles_reason'], str) and bool(record['cpu_work_cycles_reason']),
                'invalid CPU work-cycle metric')
    thread_valid = record['thread_cpu_valid'] is True
    require(type(record['thread_cpu_valid']) is bool, 'invalid thread CPU metric')
    if thread_valid:
        require(integer(record, 'thread_cpu_ns') >= 0 and record['thread_cpu_reason'] is None,
                'invalid thread CPU metric')
    else:
        require(record['thread_cpu_ns'] is None and isinstance(record['thread_cpu_reason'], str) and
                bool(record['thread_cpu_reason']), 'invalid thread CPU metric')
    host_valid = record['host_elapsed_valid'] is True
    require(type(record['host_elapsed_valid']) is bool and bool(text(record, 'host_execution_id')),
            'invalid host elapsed metric')
    if host_valid:
        start, end = integer(record, 'host_start_ns'), integer(record, 'host_end_ns')
        require(end >= start and integer(record, 'host_elapsed_ns') == end-start and
                record['host_elapsed_reason'] is None and integer(record, 'thread_id') >= 0,
                'invalid host elapsed metric')
    else:
        require(record['host_elapsed_ns'] is None and record['host_start_ns'] is None and
                record['host_end_ns'] is None and record['thread_id'] is None and
                isinstance(record['host_elapsed_reason'], str) and bool(record['host_elapsed_reason']),
                'invalid host elapsed metric')
    expected = 'CANONICAL_ADDITIVE' if host is not None else 'PER_WORKER_CPU_WORK'
    require(record['interval_class'] == expected, 'invalid timing interval class')
    require((host is not None and host_valid) or (host is None and (cycles_valid or thread_valid)),
            'authoritative timing metric unavailable')
    validate_host_cores(record)
