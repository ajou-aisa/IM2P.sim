#!/usr/bin/env python3
"""Certified production op-trace v2 and isolated C-model accounting.

No numerical data, tiler, or timing equation lives here. Final tile factors and
strides are mandatory. Observed RTL timing is validation-only and never enters
model_document(). Source identities must be checked against a separate build
manifest, not accepted merely because the trace asserts them.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Any, NotRequired, TypedDict

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from sim.cycle import cli
from scripts.gemmini_replay_contract import compatible
from scripts.gemmini_resolve_profile import JsonValue
from sim.cycle.certificate_contract import (
    SCHEMA as CERTIFICATE_SCHEMA, VERSION as CERTIFICATE_VERSION,
    array_value, digest_value, number, object_value, read_document, validate_certificate,
)

from sim.cycle.optrace_schema import (
    SCHEMA, VERSION, REPLAY_MAX_CYCLES, Trace, TraceError, require, integer,
    fields, text, validate_work, validate_records, no_duplicate_keys, read_trace,
)


class WorkResult(TypedDict):
    sequence: int
    layer: str
    phase_id: int
    parent_invocation_id: int
    provenance: str
    scope: str
    shape: list[int]
    tile_counts: list[int]
    model_status: str
    model_result: dict[str, int]
    submission_count: int


class ReplaySummary(TypedDict):
    schema: str
    version: int
    status: str
    accounting_kind: str
    trace_schema: str
    trace_version: int
    profile: str
    run_id: str
    source_commits: dict[str, str]
    source_worktree_sha256: dict[str, str]
    cycle_library_sha256: str
    timing_contract: str
    software_limits: dict[str, int]
    work_count: int
    dense_work_count: int
    residual_work_count: int
    phase_count: int
    works: list[WorkResult]
    isolated_cycle_sum: int
    per_layer_cycle_sums: dict[str, int]
    prefill_cycle_sum: int
    per_decode_token_cycle_sums: dict[str, int]
    per_phase_cycle_sums: dict[str, int]
    trace_accepted_work_equality: str
    measured_answer_injection: bool
    not_modeled: list[str]
    validation_scope: NotRequired[str]
    source_compatibility: NotRequired[str]
    hardware_contract_sha256: NotRequired[str]
    runtime_manifest_sha256: NotRequired[str]
    reference_memory: NotRequired[dict[str, JsonValue]]
    certificate_schema: NotRequired[str]
    certificate_version: NotRequired[int]
    certificate_sha256: NotRequired[str]


def check_sources(trace: Trace, expected: dict[str,Any]) -> None:
    for field in ('source_commits','source_worktree_sha256','profile'):
        require(expected.get(field)==trace.run[field],f'incompatible {field}; use the actual producer build manifest')


def model_document(trace: Trace, work: dict[str,Any]) -> dict[str,Any]:
    # This explicit allowlist is the no-measured-answer/no-retiling boundary.
    return {'profile':trace.run['profile'],'limits':{'max_cycles':REPLAY_MAX_CYCLES},'request':{
        'm':work['m'],'n':work['n'],'k':work['k'],
        'tile_i':work['tile_i_count'],'tile_j':work['tile_j_count'],'tile_k':work['tile_k_count'],
        'activation_stride_bytes':work['activation_stride_bytes'],
        'weight_stride_bytes':work['weight_stride_bytes'],
        'output_stride_bytes':work['output_stride_bytes'],
        'scale_stride_elements':work['scale_stride_elements'],
        'accepted_cycle':1,'logical_work_id':work['logical_work_id'],
        'submission':'planner-blocks','record_events':0}}


def _estimate_trace(trace: Trace, library: Path) -> ReplaySummary:
    per_layer: dict[str,int]=defaultdict(int)
    per_phase: dict[int,int]=defaultdict(int)
    works: list[WorkResult]=[]
    for w in trace.works:
        answer=cli.estimate(library,model_document(trace,w))
        require(answer.get('status')=='PASS',f'first model rejection: sequence {w["sequence"]}: {answer.get("diagnostic",answer.get("status"))}')
        result=answer['result']
        cycles=integer(result['total_cycles'],'model total_cycles')
        per_layer[w['layer']]+=cycles;per_phase[w['phase_id']]+=cycles
        works.append({'sequence':w['sequence'],'layer':w['layer'],'phase_id':w['phase_id'],
                      'parent_invocation_id':w['parent_invocation_id'],
                      'provenance':w['provenance'],'scope':w['scope'],'shape':[w['m'],w['n'],w['k']],
                      'tile_counts':[w['tile_i_count'],w['tile_j_count'],w['tile_k_count']],
                      'model_status':answer['status'],'model_result':result,'submission_count':result['loop_count']})
    counts=Counter(w['provenance'] for w in trace.works)
    return {'schema':'im2p-production-cycle-replay','version':2,'status':'ESTIMATED',
            'accounting_kind':'isolated-work-accounting','trace_schema':SCHEMA,'trace_version':VERSION,
            'profile':trace.run['profile'],'run_id':trace.run['run_id'],
            'source_commits':trace.run['source_commits'],'source_worktree_sha256':trace.run['source_worktree_sha256'],
            'cycle_library_sha256':hashlib.sha256(library.read_bytes()).hexdigest(),
            'timing_contract':'rtl-regression reference memory, independently drained accepted_cycle=1 per work',
            'software_limits':{'max_cycles_per_work':REPLAY_MAX_CYCLES},
            'work_count':len(works),'dense_work_count':counts['dense_main'],'residual_work_count':counts['residual'],
            'phase_count':len(trace.phases),'works':works,'isolated_cycle_sum':sum(per_phase.values()),
            'per_layer_cycle_sums':dict(sorted(per_layer.items())),
            'prefill_cycle_sum':per_phase[0],
            'per_decode_token_cycle_sums':{str(p['decode_index']):per_phase[p['phase_id']] for p in trace.phases if p['phase_kind']=='decode'},
            'per_phase_cycle_sums':{str(p['phase_id']):per_phase[p['phase_id']] for p in trace.phases},
            'trace_accepted_work_equality':'PASS','measured_answer_injection':False,
            'not_modeled':['aggregate overlapped pipeline latency','CPU/NPU overlap','system timeline','wall-clock latency','frequency conversion']}


@dataclass(frozen=True, slots=True)
class ReplayArtifacts:
    library: Path
    sources: Path
    certificate: Path


def _producer_binding(trace: Trace, sources: dict[str, JsonValue], cert: dict[str, JsonValue]) -> None:
    check_sources(trace, sources)
    producer = object_value(sources.get('hardware_contract'), 'producer hardware contract')
    contracts = object_value(cert.get('hardware_contracts'), 'certificate hardware contracts')
    model = object_value(contracts.get(trace.run['profile']), 'model hardware contract')
    compatible(producer, model)
    require(trace.run['hardware_contract_sha256'] == producer['sha256'], 'trace/producer hardware contract mismatch')
    runtime = object_value(sources.get('runtime_artifact'), 'producer runtime artifact')
    require(runtime.get('hardware_contract_sha256') == producer['sha256'], 'runtime/hardware contract mismatch')
    require(digest_value(runtime.get('manifest_sha256'), 'runtime manifest') == trace.run['runtime_manifest_sha256'],
            'trace/selected runtime manifest mismatch')
    digest_value(runtime.get('fingerprint'), 'runtime fingerprint')
    require(runtime.get('execution_kind') in ('FRESH_BUILD', 'VERIFIED_REUSE'), 'runtime build provenance missing')
    names: list[str] = []
    for item in array_value(runtime.get('artifacts'), 'runtime artifacts'):
        artifact = object_value(item, 'runtime archive')
        name = artifact.get('path')
        if not isinstance(name, str) or not name:
            raise TraceError('runtime archive path must be a nonempty string')
        path = PurePosixPath(name)
        require(not path.is_absolute() and '..' not in path.parts, 'runtime archive path must be relative')
        names.append(path.name)
        digest_value(artifact.get('sha256'), 'runtime archive')
        require(number(artifact.get('size'), 'runtime archive size') > 0, 'empty runtime archive')
    require(sorted(names) == ['libim2p_gemmini_frontend.a', 'libim2p_sim.a'], 'runtime archive closure mismatch')


def replay(trace_path: Path, artifacts: ReplayArtifacts) -> ReplaySummary:
    cert = read_document(artifacts.certificate)
    validate_certificate(cert, artifacts.library)
    trace = read_trace(trace_path)
    sources = read_document(artifacts.sources)
    _producer_binding(trace, sources, cert)
    result = _estimate_trace(trace, artifacts.library)
    result.update(status='PASS', validation_scope='CURRENT_CERTIFIED', source_compatibility='PASS',
                  hardware_contract_sha256=trace.run['hardware_contract_sha256'],
                  runtime_manifest_sha256=trace.run['runtime_manifest_sha256'],
                  reference_memory=object_value(cert['reference_memory'], 'reference memory'),
                  certificate_schema=CERTIFICATE_SCHEMA, certificate_version=CERTIFICATE_VERSION,
                  certificate_sha256=hashlib.sha256(artifacts.certificate.read_bytes()).hexdigest())
    return result


def _replay_fixture(trace: Trace, library: Path) -> ReplaySummary:
    result = _estimate_trace(trace, library)
    result.update(status='FIXTURE_ONLY', validation_scope='UNCERTIFIED_SYNTHETIC_FIXTURE',
                  source_compatibility='NOT_CERTIFIED')
    return result


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace',type=Path)
    parser.add_argument('--library',type=Path,required=True)
    parser.add_argument('--sources',type=Path,required=True,help='independent producer build source-identities.json')
    parser.add_argument('--cycle-certificate',type=Path,required=True,help='current isolated single-GEMM certificate bound to this library hash')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:
        result=replay(args.trace,ReplayArtifacts(args.library,args.sources,args.cycle_certificate))
        with args.output.open('x',encoding='utf-8') as stream:
            json.dump(result,stream,indent=2,sort_keys=True);stream.write('\n')
        print(json.dumps({k:result[k] for k in ('status','work_count','isolated_cycle_sum','accounting_kind')}))
    except (OSError,ValueError,KeyError,TypeError) as error:
        print(f'optrace replay failed: {error}',file=sys.stderr)
        return 1
    return 0

if __name__=='__main__':
    raise SystemExit(main())
