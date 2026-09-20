from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sqlite3

from scripts.gemmini_replay_contract import contract_digest
from sim.cycle.npu_trace_schema import Record, SemanticKey, integer, object_value, require, semantic_key, text, unique_pairs
from sim.cycle.reconstruct_graph import Manifest, array, json_records, sha256

CPU_STAGE_ALIASES = {'SOFT_MAX': 'cpu.softmax', 'SOFT_MAX_BACK': 'cpu.softmax_back'}

@dataclass(frozen=True, slots=True)
class CollectionFiles:
    log: Path
    graph: Path
    provenance: Path


def verify_provenance(files: CollectionFiles, role: str, npu_trace: Path | None = None) -> Record:
    document = object_value(json.loads(files.provenance.read_text(), object_pairs_hook=unique_pairs))
    require(document.get('schema') == 'im2p-collection-provenance' and type(document.get('version')) is int and
            document['version'] == 1 and document.get('source_role') == role, 'invalid collection provenance schema/role')
    require(type(document.get('process_exit_code')) is int and document['process_exit_code'] == 0 and
            document.get('collection_success') is True and document.get('build_inputs_unchanged') is True,
            'collection failed or build/model changed')
    for key in ('model_sha256', 'executable_sha256', 'compile_commands_sha256'):
        require(re.fullmatch('[0-9a-f]{64}', text(document, key)) is not None, 'missing provenance content hash')
    kernel = object_value(document.get('cpu_kernel_contract'))
    require(kernel.get('schema') == 'im2p-ordinary-cpu-kernel-contract' and type(kernel.get('version')) is int and
            kernel['version'] == 1 and kernel.get('sha256') == contract_digest(kernel), 'invalid CPU kernel contract')
    require(all(bool(object_value(kernel.get(key))) for key in ('units', 'dependencies', 'compilers')), 'incomplete CPU kernel proof')
    require(isinstance(document.get('command_arguments'), list), 'missing actual command arguments')
    arguments = array(document['command_arguments'])
    require(all(isinstance(value, str) for value in arguments), 'normalized arguments must be strings')
    fresh = object_value(document.get('fresh_build'))
    require(fresh.get('kind') == 'FRESH_CONFIGURE_AND_COMPILE', 'collection did not use a fresh compiled build')
    require(re.fullmatch('[0-9a-f]{64}', text(fresh, 'reference_cache_sha256')) is not None, 'fresh build reference cache missing')
    text(fresh, 'build')
    configure = array(fresh.get('configure_command'))
    require(bool(configure) and all(isinstance(item, str) and bool(item) for item in configure), 'fresh configure command missing')
    compiled = object_value(fresh.get('actual_compile_inputs'))
    require(bool(compiled) and all(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None
                                  for value in compiled.values()), 'actual compiler input binding missing')
    runtime = object_value(document.get('runtime_dependencies'))
    require(bool(runtime), 'actual runtime dependency closure missing')
    for dependency in runtime.values():
        bound = object_value(dependency)
        if bound.get('kind') == 'SYSTEM_SHARED_CACHE':
            require(bool(array(bound.get('os'))), 'system shared-cache OS identity missing')
        else:
            require(bound.get('kind') in ('PROJECT', 'EXTERNAL', 'SYSTEM') and
                    re.fullmatch('[0-9a-f]{64}', text(bound, 'sha256')) is not None, 'runtime dependency content hash missing')
    input_files = object_value(document.get('input_files'))
    input_hashes: set[str] = set()
    for value in input_files.values():
        require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None, 'input file content hash missing')
        if isinstance(value, str): input_hashes.add(value)
    argument_hashes = {value.removeprefix('sha256:') for value in arguments if isinstance(value, str) and value.startswith('sha256:')}
    require(argument_hashes == input_hashes, 'normalized file arguments lack exact content-hash coverage')
    for value in array(document.get('project_libraries')):
        library = object_value(value); text(library, 'path')
        require(re.fullmatch('[0-9a-f]{64}', text(library, 'sha256')) is not None, 'invalid library content hash')
    artifacts = object_value(document.get('artifacts'))
    bindings = {'cycle_log': files.log, 'semantic_graph': files.graph}
    if npu_trace is not None: bindings['npu_trace'] = npu_trace
    for name, path in bindings.items():
        require(object_value(artifacts.get(name)).get('sha256') == sha256(path), 'collection artifact mismatch: ' + name)
    require(role != 'FULL_CPU' or 'npu_trace' not in artifacts, 'FullCPU collection emitted NPU trace')
    return document


def encoded_key(key: SemanticKey) -> str:
    return json.dumps(key, separators=(',', ':'))


def valid_measurement(record: Record) -> None:
    require(record.get('valid') is True and record.get('cpu_service') is True, 'required CPU service measurement invalid/excluded')
    start, end, delta = (integer(record, key) for key in ('start', 'end', 'delta'))
    require(end >= start and delta == end-start, 'CPU interval endpoint/delta mismatch')
    require((text(record, 'source'), text(record, 'unit')) in (
        ('host_tick', 'tick'), ('riscv_cycle', 'cycle'), ('linux_perf_cpu_cycles', 'cycle'),
        ('thread_cpu_clock', 'nanosecond'), ('steady_clock', 'nanosecond')), 'unknown CPU clock source/unit')
    require(record.get('operation_success', True) is True, 'host operation failed')


def duration_sample(record: Record) -> Record:
    keys = ('source', 'unit', 'start', 'end', 'delta', 'valid', 'worker_id', 'worker_count',
            'reason', 'sample_reason', 'source_line')
    return {**{name: record[name] for name in keys if name in record}, 'stage': record['op']}


class CpuIndex:
    def __init__(self, connection: sqlite3.Connection, target: Manifest) -> None:
        self.db = connection
        self.target = target
        self.observations: Counter[str] = Counter()
        self.db.execute('CREATE TABLE samples(role TEXT, semantic TEXT, stage TEXT, worker INTEGER, workers INTEGER, host INTEGER, body TEXT)')
        self.db.execute('CREATE UNIQUE INDEX host_identity ON samples(role,host) WHERE host IS NOT NULL')
        self.db.execute('CREATE UNIQUE INDEX worker_identity ON samples(role,semantic,stage,worker) WHERE host IS NULL')

    def load(self, path: Path, manifest: Manifest) -> None:
        role = text(manifest.run, 'source_role')
        for source_line, record in enumerate(json_records(path), 1):
            require(record.get('schema') == 'gemmini.cycle' and type(record.get('version')) is int and record['version'] == 2,
                    'unsupported CPU CycleLog schema/version')
            if record.get('exclusion_reason') == 'outside_collection':
                require(record.get('duration_role') == 'OBSERVATION_ONLY', 'outside-collection record claimed duration authority')
                self.observations[role + ':outside_collection'] += 1
                continue
            key = semantic_key(record)
            require(key in manifest.nodes and record.get('run_config_id') == manifest.run['run_config_id'] and
                    record.get('source_role') == role, 'unknown CPU semantic identity/run/source')
            duration_role = text(record, 'duration_role')
            require(record.get('duration_source') == role, 'CPU duration source mismatch')
            allowed = ('ORDINARY_CPU_REFERENCE', 'OBSERVATION_ONLY') if role == 'FULL_CPU' else ('POTAL_HOST', 'OBSERVATION_ONLY')
            require(duration_role in allowed, 'CPU duration ownership mismatch')
            host = None if record.get('host_stage_id') is None else integer(record, 'host_stage_id')
            if record.get('record_type') != 'CYCLE_INTERVAL':
                require(duration_role == 'OBSERVATION_ONLY' and host is None, 'non-interval telemetry cannot be CPU service cost')
                self.observations[role + ':telemetry'] += 1
                continue
            op = text(record, 'op')
            logical_op = text(object_value(manifest.nodes[key]['payload']), 'op')
            canonical_cpu = op == CPU_STAGE_ALIASES.get(logical_op, 'cpu.' + logical_op.lower())
            if host is None and not canonical_cpu:
                require(duration_role == 'OBSERVATION_ONLY' or
                        (role == 'FULL_CPU' and self.target.executions[key]['execution_class'] in ('TARGET_NPU', 'EXCLUDED')),
                        'unclassified CPU service stage')
                self.observations[role + ':noncanonical'] += 1
                continue
            require(host is None or role == 'POTAL_COLLECTION', 'FullCPU cannot own PoTal host stages')
            if duration_role == 'POTAL_HOST': require(host is not None, 'PoTal host cost lacks declared stage identity')
            worker = None if record.get('worker_id') is None else integer(record, 'worker_id')
            workers = None if record.get('worker_count') is None else integer(record, 'worker_count')
            if host is None:
                require(worker is not None and workers is not None and workers > 0 and worker < workers, 'invalid CPU worker vector identity')
                cpu = object_value(object_value(manifest.run['workload'])['cpu_batch' if key[0] == 'prefill' else 'cpu'])
                require(workers is not None and workers <= integer(cpu, 'threads'), 'CPU workers exceed declared thread configuration')
            try:
                keys = ('source', 'unit', 'start', 'end', 'delta', 'valid', 'worker_id', 'worker_count',
                        'reason', 'sample_reason', 'op', 'cpu_service', 'cpu_service_exclusion',
                        'duration_role', 'operation_success')
                stored: Record = {**{name: record[name] for name in keys if name in record}, 'source_line': source_line}
                self.db.execute('INSERT INTO samples VALUES(?,?,?,?,?,?,?)',
                                (role, encoded_key(key), op, worker, workers, host, json.dumps(stored, sort_keys=True)))
            except sqlite3.IntegrityError as error:
                raise ValueError('duplicate/ambiguous CPU interval identity') from error

    def ordinary(self, key: SemanticKey) -> list[Record]:
        values = list(self.db.execute('SELECT body FROM samples WHERE role=? AND semantic=? AND host IS NULL ORDER BY stage,worker',
                                     ('FULL_CPU', encoded_key(key))))
        require(bool(values), 'missing FullCPU ordinary reference cost')
        records = [object_value(json.loads(row[0], object_pairs_hook=unique_pairs)) for row in values]
        groups: defaultdict[str, list[Record]] = defaultdict(list)
        for record in records:
            require(record['duration_role'] == 'ORDINARY_CPU_REFERENCE', 'ordinary cost is not FullCPU reference')
            valid_measurement(record)
            groups[text(record, 'op')].append(record)
        for group in groups.values():
            workers = integer(group[0], 'worker_count')
            require(all(integer(row, 'worker_count') == workers for row in group) and
                    [integer(row, 'worker_id') for row in group] == list(range(workers)), 'incomplete/ambiguous worker vector')
        return records

    def host(self, record: Record) -> Record | None:
        identity = integer(record, 'host_stage_id')
        row = self.db.execute('SELECT semantic,body FROM samples WHERE role=? AND host=?', ('POTAL_COLLECTION', identity)).fetchone()
        if row is not None:
            require(row[0] == encoded_key(semantic_key(record)), 'host measurement semantic identity mismatch')
            require(object_value(json.loads(row[1], object_pairs_hook=unique_pairs))['op'] == record['stage_name'], 'host stage name mismatch')
        if record['execution_class'] == 'FUNCTIONAL_EMULATION':
            if row is not None:
                observation = object_value(json.loads(row[1], object_pairs_hook=unique_pairs))
                require(observation['duration_role'] == 'OBSERVATION_ONLY' and observation.get('cpu_service') is False,
                        'functional emulation claimed target CPU cost')
        else:
            require(row is not None, 'missing measured PoTal host stage')
            if row is None: raise ValueError('missing host measurement')
            require(row[0] == encoded_key(semantic_key(record)), 'host measurement semantic identity mismatch')
            measurement = object_value(json.loads(row[1], object_pairs_hook=unique_pairs))
            require(measurement['duration_role'] == 'POTAL_HOST' and measurement['op'] == record['stage_name'],
                    'host stage measurement ownership/name mismatch')
            valid_measurement(measurement)
            self.db.execute('DELETE FROM samples WHERE role=? AND host=?', ('POTAL_COLLECTION', identity))
            return measurement
        self.db.execute('DELETE FROM samples WHERE role=? AND host=?', ('POTAL_COLLECTION', identity))
        return None

    def finish(self) -> None:
        require(self.db.execute('SELECT COUNT(*) FROM samples WHERE host IS NOT NULL').fetchone()[0] == 0,
                'unexplained extra host measurement')
