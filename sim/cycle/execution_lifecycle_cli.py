from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys

from sim.cycle.certificate_contract import read_document
from sim.cycle.collection_native import validate_receipt
from sim.cycle.execution_cli import publish
from sim.cycle.execution_ir import ensure
from sim.cycle.execution_lifecycle import project_lifecycle
from sim.cycle.npu_trace_schema import Record, integer, object_value
from sim.cycle.reconstruct_graph import array, json_records, read_manifest, sha256
from scripts.gemmini_resolve_profile import JsonValue


@dataclass(frozen=True, slots=True)
class LifecycleFiles:
    sidecar: Path
    semantic_graph: Path
    provenance: Path
    application: Path
    dataset: Path
    npu_results: Path
    join_summary: Path


@dataclass(frozen=True, slots=True)
class CpuScenario:
    worker_resources: Record
    policy: str
    sampler_resource: str


def producer_payload(row: Record) -> Record:
    return {key: value for key, value in row.items()
            if key not in ('schema', 'version', 'kind', 'sequence')}


def build(files: LifecycleFiles, scenario: CpuScenario) -> Record:
    proof = read_document(files.provenance)
    ensure(proof.get('schema') == 'im2p-collection-provenance' and proof.get('version') == 2 and
           proof.get('source_role') == 'POTAL_COLLECTION' and proof.get('collection_success') is True and
           proof.get('process_exit_code') == 0 and proof.get('build_inputs_unchanged') is True,
           'successful native PoTal build-bound collection required')
    receipt = object_value(proof['native_build'])
    validate_receipt(receipt)
    ensure(object_value(receipt['project_artifacts'])['llama-eval-workload'] == proof['executable_sha256'],
           'native producer binary binding mismatch')
    inputs = object_value(receipt['actual_compile_inputs'])
    for suffix in ('tools/eval/evaluation-lifecycle.h', 'tools/eval/evaluation-trace.cpp',
                   'ggml/src/ggml-gemmini-utils/src/semantic.cpp'):
        ensure(any(path.endswith(suffix) for path in inputs), 'producer build lacks lifecycle implementation: ' + suffix)
    artifacts = object_value(proof['artifacts'])
    for key, path in (('execution_lifecycle', files.sidecar), ('semantic_graph', files.semantic_graph),
                      ('application_cpu', files.application)):
        ensure(object_value(artifacts.get(key))['sha256'] == sha256(path), 'producer artifact binding mismatch: ' + key)
    summary = read_document(files.join_summary)
    ensure(summary.get('status') == 'PASS' and summary.get('scope') == 'structural-three-source-reconstruction',
           'complete official structural join required')
    source = object_value(summary['source_artifacts'])
    ensure(object_value(source['potal_semantic_graph'])['sha256'] == sha256(files.semantic_graph) and
           object_value(source['npu_results'])['sha256'] == sha256(files.npu_results) and
           summary['potal_provenance_sha256'] == sha256(files.provenance), 'join source binding mismatch')
    fingerprints = object_value(summary['decode_token_fingerprint_matches'])
    ensure(bool(fingerprints) and all(value is True for value in fingerprints.values()), 'decode input trajectory mismatch')
    graph = read_manifest(files.semantic_graph)
    sidecar = tuple(json_records(files.sidecar))
    projection = project_lifecycle(sidecar, graph)
    pipeline = bool(sidecar) and integer(sidecar[0], 'version') == 3
    ensure(not pipeline or bool(projection.pipeline_parents), 'PIPELINE collection has no declared target parents')
    app = tuple(json_records(files.application))
    samples = [row for row in sidecar if row['kind'] == 'SAMPLE']
    application_samples = [row for row in app if row.get('stage') == 'sample_accept'] if pipeline else list(app)
    preparations = [row for row in app if row.get('stage') == 'prefill_batch_prepare'] if pipeline else []
    ensure(len(application_samples) == len(samples) == projection.expected_samples,
           'application/lifecycle sample coverage mismatch')
    if pipeline:
        ensure(len(app) == len(application_samples) + len(preparations) and
               len(preparations) == len(projection.prefill_steps) and
               all(integer(row, 'version') == 2 for row in app),
               'pipeline application preparation coverage mismatch')
        for left, right in zip(preparations, projection.prefill_steps, strict=True):
            ensure(left['batch_index'] == right['batch_index'] and left['dispatch_id'] == right['dispatch_id'] and
                   left['phase'] == 'prefill' and left['decode_index'] is None and
                   left['source_role'] == 'potal_collection',
                   'application preparation/producer dispatch mismatch')
    for left, right in zip(application_samples, samples, strict=True):
        ensure(left['sample_index'] == right['sample_index'] and left['token_id'] == right['token_id'] and
               left['phase'] == right['phase_kind'] and left['decode_index'] == right['decode_index'],
               'application/lifecycle token or phase mismatch')
    ensure(not pipeline or all(any(path.endswith(suffix) for path in inputs) for suffix in (
        'frontend/src/im2p_gemmini_frontend.cpp',
        'ggml/src/ggml-gemmini-utils/src/cycle_sim_log.cpp',
        'ggml/src/ggml-gemmini/quants/act/exsia/exsia.cpp')),
        'producer build lacks PIPELINE ownership implementation')
    slots: Record = {}
    submission: list[str] = []
    work_rows: dict[int, Record] = {}
    for row in json_records(files.npu_results):
        ensure(row.get('schema') == 'im2p-npu-cycle-result' and row.get('cycle_model_validation') == 'CURRENT_CERTIFIED',
               'current official NPU results required')
        ensure(row['scope'] in (('stripe', 'residual_compact') if pipeline else ('full', 'residual_compact')) and
               (pipeline or row['host_slot'] is None), 'NPU result scope differs from producer lifecycle')
        call = str(integer(row, 'call_id'))
        ensure(call not in slots, 'multiple NPU works claim one invocation')
        slots[call] = None
        work_id = integer(row, 'work_id')
        ensure(work_id not in work_rows, 'duplicate NPU result work ID')
        work_rows[work_id] = row
        submission.append('npu:' + str(work_id))
    if pipeline:
        declared: set[int] = set()
        for parent in projection.pipeline_parents:
            raw_ids = array(parent['required_work_ids'])
            ensure(all(type(value) is int and value >= 0 for value in raw_ids),
                   'PIPELINE parent has invalid work identity')
            work_ids = {value for value in raw_ids if type(value) is int}
            ensure(not (declared & work_ids), 'PIPELINE work claimed by two parents')
            declared.update(work_ids)
            dense = [work_rows[work_id] for work_id in work_ids
                     if work_id in work_rows and work_rows[work_id]['scope'] == 'stripe']
            ensure(bool(dense) and all(integer(row, 'parent_id') == integer(parent, 'parent_id') and
                       integer(row, 'operation_id') == integer(parent, 'operation_id') and
                       integer(row, 'phase_id') == integer(parent, 'phase_id') for row in dense),
                   'PIPELINE dense stripe differs from declared parent')
            ranges = sorted((integer(row, 'row_begin'), integer(row, 'row_begin') + integer(row, 'row_count'))
                            for row in dense)
            ensure(ranges[0][0] == 0 and ranges[-1][1] == integer(parent, 'parent_m') and
                   all(left[1] == right[0] for left, right in zip(ranges, ranges[1:])),
                   'PIPELINE parent has missing/overlapping dense rows')
            bindings = [object_value(value) for value in array(parent['residual_bindings'])]
            residual_ids = {work_id for work_id in work_ids if work_id in work_rows and
                            work_rows[work_id]['scope'] == 'residual_compact'}
            ensure({integer(row, 'work_id') for row in bindings} == residual_ids and
                   all(integer(work_rows[integer(row, 'work_id')], 'parent_id') == integer(row, 'child_parent_id') and
                       integer(work_rows[integer(row, 'work_id')], 'call_id') == integer(row, 'call_id') and
                       integer(row, 'dense_parent_id') == integer(parent, 'parent_id') and
                       integer(row, 'dense_work_id') in {integer(d, 'work_id') for d in dense}
                       for row in bindings), 'PIPELINE residual child ownership mismatch')
        ensure(declared == set(work_rows), 'PIPELINE parent/NPU result bijection mismatch')
        ensure(bool(projection.pipeline_owners), 'PIPELINE producer has no ownership transitions')
    result: Record = {'schema': 'im2p-execution-lifecycle', 'version': 2 if pipeline else 1,
            'source_kind': 'PRODUCER_DECLARED',
            'dataset_sha256': sha256(files.dataset), 'npu_results_sha256': sha256(files.npu_results),
            'producer_binding': {'sidecar_sha256': sha256(files.sidecar), 'semantic_graph_sha256': sha256(files.semantic_graph),
                                 'provenance_sha256': sha256(files.provenance), 'executable_sha256': proof['executable_sha256'],
                                 'native_build_sha256': receipt['sha256'], 'join_summary_sha256': sha256(files.join_summary)},
            'operation_exit_policy': 'ALL_MEMBER_COMPLETIONS', 'publish_policy': 'NONBLOCKING_SUBMISSION',
            'slot_release_policy': 'NPU_RESOURCE_READY', 'phase_graph_policy': 'EXPLICIT_EDGES',
            'entry_dependencies': projection.entry_dependencies, 'barriers': [row for row in projection.barriers],
            'call_slots': slots, 'submission_order': [value for value in submission],
            'cpu_policy': scenario.policy, 'worker_resources': scenario.worker_resources,
            'application': {'sha256': sha256(files.application), 'sampler_policy': 'SINGLE_CALLING_THREAD',
                            'resource': scenario.sampler_resource, 'metric_policy': scenario.policy,
                            'expected_samples': projection.expected_samples,
                            'steps': [row for row in projection.application_steps]}}
    if pipeline:
        result['pipeline_parents'] = list[JsonValue](producer_payload(row) for row in projection.pipeline_parents)
        result['pipeline_owners'] = list[JsonValue](producer_payload(row) for row in projection.pipeline_owners)
        application = object_value(result['application'])
        application['prefill_steps'] = list[JsonValue](projection.prefill_steps)
    return result


class Arguments(argparse.Namespace):
    sidecar: Path = Path()
    semantic_graph: Path = Path()
    provenance: Path = Path()
    application: Path = Path()
    dataset: Path = Path()
    npu_results: Path = Path()
    join_summary: Path = Path()
    worker_resources: Path = Path()
    cpu_policy: str = ''
    sampler_resource: str = ''
    output: Path = Path()


def main() -> int:
    parser = argparse.ArgumentParser(description='Project actual FULL or PIPELINE producer lifecycle into bound execution semantics.')
    for name in ('sidecar', 'semantic-graph', 'provenance', 'application', 'dataset', 'npu-results',
                 'join-summary', 'worker-resources', 'output'):
        _ = parser.add_argument('--' + name, type=Path, required=True)
    _ = parser.add_argument('--cpu-policy', choices=('THREAD_CPU_NS_GANG', 'HOST_ELAPSED_NS_GANG'), required=True)
    _ = parser.add_argument('--sampler-resource', required=True)
    args = parser.parse_args(namespace=Arguments())
    try:
        files = LifecycleFiles(args.sidecar, args.semantic_graph, args.provenance, args.application,
                                args.dataset, args.npu_results, args.join_summary)
        result = build(files, CpuScenario(read_document(args.worker_resources), args.cpu_policy, args.sampler_resource))
        publish(args.output, result)
        print(json.dumps({'status': 'PASS', 'source_kind': result['source_kind'], 'output': str(args.output)}))
    except (OSError, ValueError, KeyError) as error:
        print(f'lifecycle failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
