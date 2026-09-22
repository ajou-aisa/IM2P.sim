"""Join three authoritative cost sources structurally; do not schedule or convert units.

The JSONL dataset uses namespaced node IDs and explicit dependency references.
Only is_execution_node=true rows belong to the six-class execution-node ledger;
operation containers and call boundaries carry structure, not additional costs.
CPU durations retain worker vectors in native units. Their one-based source_line
points into the FullCPU or PoTal cycle log bound by summary.source_artifacts.
NPU work_id/result sequence and trace_sequence point to the immutable, hashed
NPU result and trace files, which retain full geometry, counters and provenance.
The normalized dataset deliberately does not duplicate those complete records.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack, closing
from dataclasses import dataclass
import json
import gzip
import io
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.gemmini_replay_contract import canonical_json
from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.npu_trace import ReplayArtifacts, ReplayOutputs, snapshot_inputs, verify_input_snapshots
from sim.cycle.npu_trace_schema import Record, integer, object_value, require
from sim.cycle.reconstruct_cpu import CollectionFiles, CpuIndex, duration_sample, encoded_key, verify_provenance
from sim.cycle.reconstruct_graph import compare_manifests, emit, read_manifest, service_identity, sha256
from sim.cycle.reconstruct_npu import NpuFiles, NpuJoin


@dataclass(frozen=True, slots=True)
class Inputs:
    full_cpu: CollectionFiles
    potal: CollectionFiles
    npu: NpuFiles


def reconstruct(inputs: Inputs, outputs: ReplayOutputs) -> Record:
    sources = (inputs.full_cpu.log, inputs.full_cpu.graph, inputs.full_cpu.provenance,
               inputs.potal.log, inputs.potal.graph, inputs.potal.provenance,
               inputs.npu.trace, inputs.npu.results, inputs.npu.artifacts.certificate, inputs.npu.artifacts.library) + (
               (inputs.npu.artifacts.run_certificate,) if inputs.npu.artifacts.run_certificate is not None else ())
    paths = [path.resolve() for path in (*sources, outputs.results, outputs.summary)]
    require(len(set(paths)) == len(paths), 'reconstruction input/output paths must be distinct')
    require(not outputs.results.exists() and not outputs.summary.exists(), 'reconstruction outputs must be new')
    storage_encoding = 'gzip' if outputs.results.suffix == '.gz' else 'plain'
    with tempfile.TemporaryDirectory(prefix='reconstruct-snapshot-', dir=outputs.results.parent) as snapshot_directory, \
         tempfile.TemporaryDirectory(prefix='reconstruct-', dir=outputs.results.parent) as directory, \
         tempfile.TemporaryDirectory(prefix='reconstruct-summary-', dir=outputs.summary.parent) as summary_directory:
        snapshots = snapshot_inputs(sources, Path(snapshot_directory))
        copied = [snapshot.snapshot for snapshot in snapshots]
        full_cpu = CollectionFiles(*copied[:3])
        potal_cpu = CollectionFiles(*copied[3:6])
        npu = NpuFiles(copied[6], copied[7], ReplayArtifacts(copied[9], copied[8],
                       copied[10] if len(copied) == 11 else None))
        effective = Inputs(full_cpu, potal_cpu, npu)
        full, potal = read_manifest(effective.full_cpu.graph), read_manifest(effective.potal.graph)
        compare_manifests(full, potal)
        proof_full = verify_provenance(effective.full_cpu, 'FULL_CPU')
        proof_potal = verify_provenance(effective.potal, 'POTAL_COLLECTION', effective.npu.trace)
        require(proof_full['model_sha256'] == proof_potal['model_sha256'], 'model contents differ across collections')
        require(proof_full['command_arguments'] == proof_potal['command_arguments'], 'normalized collection arguments differ')
        require(canonical_json(object_value(proof_full['cpu_kernel_contract'])) ==
                canonical_json(object_value(proof_potal['cpu_kernel_contract'])), 'ordinary CPU kernel/build contract mismatch')
        root = Path(directory)
        with closing(sqlite3.connect(root/'index.sqlite')) as connection, ExitStack() as streams:
            raw = streams.enter_context((root/'dataset.jsonl').open('xb'))
            binary = streams.enter_context(gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0, compresslevel=1)) \
                if storage_encoding == 'gzip' else raw
            stream = streams.enter_context(io.TextIOWrapper(binary, encoding='utf-8'))
            cpu = CpuIndex(connection, potal)
            cpu.load(effective.full_cpu.log, full)
            cpu.load(effective.potal.log, potal)
            counts: Counter[str] = Counter()
            for key, node in potal.nodes.items():
                node_class = str(potal.executions[key]['execution_class'])
                counts[node_class] += 1
                identity = service_identity(key, node)
                operation_node = 'operation:' + encoded_key(key)
                predecessors = ['operation:' + encoded_key(source) for source in potal.edges[key]]
                if node_class == 'ORDINARY_CPU':
                    vector = cpu.ordinary(key)
                    emit(stream, {'kind': 'SERVICE', 'node_class': node_class, 'duration_source': 'FULL_CPU', 'resource_kind': 'CPU',
                                  'duration': {'worker_intervals': [duration_sample(sample) for sample in vector],
                                               'aggregation': 'PER_WORKER_NOT_NODE_LATENCY'},
                                  'cost_included': True, 'potal_ordinary_measurements': 'OBSERVATION_ONLY', **identity,
                                  'node_id': 'ordinary:' + encoded_key(key), 'is_execution_node': True,
                                  'operation_node_id': operation_node, 'dependencies': list[JsonValue](predecessors)})
                elif node_class == 'EXCLUDED':
                    emit(stream, {'kind': 'SERVICE', 'node_class': node_class, 'duration_source': 'NONE',
                                  'resource_kind': 'STRUCTURAL', 'duration': None, 'cost_included': False,
                                  'reason': 'EXCLUDED_GRAPH_NODE', **identity, 'node_id': 'excluded:' + encoded_key(key),
                                  'is_execution_node': True, 'operation_node_id': operation_node, 'dependencies': list[JsonValue](predecessors)})
                emit(stream, {'kind': 'OPERATION_CONTAINER', 'node_class': node_class, 'duration_source': 'NONE',
                              'resource_kind': 'STRUCTURAL', 'duration': None, **identity,
                              'node_id': operation_node, 'is_execution_node': False, 'dependencies': list[JsonValue](predecessors),
                              'reason': 'REPLACED_BY_NPU' if node_class == 'TARGET_NPU' else 'LOGICAL_OPERATION_METADATA'})
            npu = NpuJoin(effective.npu, potal, cpu).write(stream)
            execution_counts: Record = {'ORDINARY_CPU': counts['ORDINARY_CPU'], 'POTAL_HOST': npu['potal_host_count'],
                'TARGET_NPU': npu['npu_work_count'], 'FUNCTIONAL_EMULATION': npu['functional_emulation_count'],
                'EXCLUDED': counts['EXCLUDED'], 'UNSUPPORTED': 0}
            summary: Record = {'schema': 'im2p-reconstruction-summary', 'version': 1, 'status': 'PASS',
                'storage_encoding': storage_encoding,
                'scope': 'structural-three-source-reconstruction', 'ordinary_cpu_bijection': 'PASS',
                'graph_node_counts': dict(sorted(counts.items())), 'observations': dict(sorted(cpu.observations.items())),
                'execution_node_counts': execution_counts,
                'execution_node_count': sum(integer(execution_counts, name) for name in execution_counts),
                'logical_operation_container_count': len(potal.nodes),
                'model_sha256': proof_full['model_sha256'], 'run_config_id': full.run['run_config_id'],
                'cpu_kernel_contract_sha256': object_value(proof_full['cpu_kernel_contract'])['sha256'],
                'full_cpu_provenance_sha256': sha256(effective.full_cpu.provenance),
                'potal_provenance_sha256': sha256(effective.potal.provenance), **npu,
                'source_artifacts': {'full_cpu_cycle_log': object_value(proof_full['artifacts'])['cycle_log'],
                    'full_cpu_semantic_graph': object_value(proof_full['artifacts'])['semantic_graph'],
                    'potal_cycle_log': object_value(proof_potal['artifacts'])['cycle_log'],
                    'potal_semantic_graph': object_value(proof_potal['artifacts'])['semantic_graph'],
                    'npu_trace': object_value(proof_potal['artifacts'])['npu_trace'],
                    'npu_results': {'sha256': sha256(effective.npu.results)}},
                'source_reference_contract': 'CPU source_line is one-based in the bound cycle log; NPU work_id and trace_sequence index bound original files',
                'decode_token_fingerprint_matches': {str(left['decode_index']): left['token_fingerprint'] == right['token_fingerprint']
                    for left, right in zip(full.phases, potal.phases, strict=True) if left['phase_kind'] == 'decode'},
                'decode_token_policy': 'values observed; semantic structure and counts must match',
                'worker_aggregation': 'NOT_MODELED', 'system_latency': 'NOT_MODELED',
                'frequency_conversion': 'NOT_IMPLEMENTED', 'scheduler': 'NOT_IMPLEMENTED',
                'timing_sources': ['FULL_CPU', 'POTAL_COLLECTION', 'NPU_MODEL']}
        verify_input_snapshots(snapshots, 'reconstruction')
        temporary_summary = Path(summary_directory)/'summary.json'
        with temporary_summary.open('x', encoding='utf-8') as stream:
            json.dump(summary, stream, indent=2, sort_keys=True, allow_nan=False); stream.write('\n')
        os.link(root/'dataset.jsonl', outputs.results)
        os.link(temporary_summary, outputs.summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for source in ('full-cpu', 'potal'):
        for kind in ('log', 'graph', 'provenance'):
            parser.add_argument('--'+source+'-'+kind, type=Path, required=True)
    for name in ('npu-trace', 'npu-results', 'library', 'cycle-certificate', 'summary'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--run-aware-certificate', type=Path)
    parser.add_argument('--output', type=Path, required=True, help='New JSONL dataset; .gz selects deterministic gzip storage')
    args = parser.parse_args()
    inputs = Inputs(CollectionFiles(args.full_cpu_log, args.full_cpu_graph, args.full_cpu_provenance),
                    CollectionFiles(args.potal_log, args.potal_graph, args.potal_provenance),
                    NpuFiles(args.npu_trace, args.npu_results, ReplayArtifacts(
                        args.library, args.cycle_certificate, args.run_aware_certificate)))
    try:
        summary = reconstruct(inputs, ReplayOutputs(args.output, args.summary))
        print(json.dumps({'status': summary['status'], 'scope': summary['scope'], 'npu_work_count': summary['npu_work_count']}))
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f'reconstruction failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
