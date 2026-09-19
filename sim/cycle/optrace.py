#!/usr/bin/env python3
"""Strict production op-trace v1 validation and isolated C-model accounting.

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
from pathlib import Path
import re
import sys
from typing import Any, Final

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from sim.cycle import cli

SCHEMA = 'im2p-production-optrace'
VERSION = 1
# Software admission guard for complete vocabulary projections, not a timing
# prediction or a hardware capacity. Never derive this limit from observations.
REPLAY_MAX_CYCLES: Final = 100_000_000
COMMON = {'kind','sequence','run_id'}
RUN = COMMON | {'schema','version','model','profile','activation_bits','weight_bits','dim',
                'backend','mode','residual_enabled','prompt_tokens','requested_generated_tokens',
                'source_commits','source_worktree_sha256'}
PHASE = COMMON | {'phase_id','phase_kind','decode_index','input_tokens'}
WORK_NUMBERS = {'phase_id','activation_bits','weight_bits','dim','m','n','k','tile_i_count',
                'tile_j_count','tile_k_count','geometry_m','row_begin','row_count',
                'activation_stride_bytes','weight_stride_bytes','output_stride_bytes',
                'scale_stride_elements','block_size','vector_op','output_domain',
                'production_geometry_version','work_context','source_row_begin','source_row_count',
                'column_begin','group_index','logical_work_id'}
WORK_OPTIONALS = {'stripe_id','host_slot','original_block_id'}
WORK_STRINGS = {'layer','operation','provenance','numerical_datapath','scope'}
WORK = COMMON | WORK_NUMBERS | WORK_OPTIONALS | WORK_STRINGS | {'rmd_raw','host_integer_block_multiply'}
OBSERVATIONS = {'observed_rtl_start','observed_rtl_done','observed_rtl_elapsed'}
END = COMMON | {'status','reason','work_count','independent_counts'}
SOURCE_NAMES = {'IM2P.sim','llama.cpp-gemmini','headers'}

class TraceError(ValueError):
    """A trace is incomplete, incompatible, ambiguous, or not production work."""

@dataclass
class Trace:
    run: dict[str, Any]
    phases: list[dict[str, Any]]
    works: list[dict[str, Any]]
    end: dict[str, Any]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise TraceError(message)


def integer(value: Any, name: str, positive: bool = False) -> int:
    require(type(value) is int and (1 if positive else 0) <= value <= 2**64-1,
            f'{name}: expected {"positive" if positive else "nonnegative"} uint64')
    return value


def text(value: Any, name: str) -> str:
    require(isinstance(value, str) and bool(value), f'{name}: nonempty string required')
    return value


def fields(record: dict[str, Any], required: set[str], optional: set[str] | None = None) -> None:
    require(required <= record.keys(), f'missing fields: {sorted(required-record.keys())}')
    require(record.keys() <= required | (optional or set()),
            f'unknown fields: {sorted(record.keys()-required-(optional or set()))}')


def validate_work(w: dict[str, Any], run: dict[str, Any], phase_id: int) -> None:
    fields(w, WORK, OBSERVATIONS)
    for k in WORK_NUMBERS:
        integer(w[k], k)
    for k in WORK_OPTIONALS | OBSERVATIONS:
        if w.get(k) is not None:
            integer(w[k], k)
    for k in WORK_STRINGS:
        text(w[k], k)
    require(w['phase_id'] == phase_id, 'work does not belong to current explicit phase')
    require(all(w[k] == run[k] for k in ('activation_bits','weight_bits','dim')), 'work/run profile mismatch')
    for k in ('m','n','k','tile_i_count','tile_j_count','tile_k_count'):
        integer(w[k], k, True)
    require(w['tile_i_count'] <= 65535//w['dim'] and w['tile_j_count'] <= 65535//w['dim']
            and w['tile_k_count'] <= (2**32-1)//w['dim'], 'tile factor exceeds hardware domain')
    require(w['row_count'] == w['m'] and w['row_begin']+w['row_count'] <= w['geometry_m'],
            'invalid accepted stripe/compact row range')
    require(w['activation_stride_bytes'] >= w['k'] and w['weight_stride_bytes'] >= w['n']
            and w['output_stride_bytes'] >= 4*w['n'] and w['output_stride_bytes']%4 == 0
            and w['scale_stride_elements'] >= w['n'], 'invalid descriptor strides')
    require((w['block_size'],w['vector_op'],w['output_domain'],w['production_geometry_version'])
            == (32,5,2,1), 'unsupported production HP1 descriptor contract')
    require(w['numerical_datapath'] == 'hp1_scu' and w['rmd_raw'] is False
            and w['host_integer_block_multiply'] is False, 'raw/host-integer work is not production HP1 SCU')
    require(w['logical_work_id'] == w['sequence'], 'logical work identity must match the deterministic sequence')
    if w['provenance'] == 'residual':
        require(run['residual_enabled'] is True, 'residual work in residual-disabled run')
        require(w['scope']=='residual_compact' and w['k']<=32 and w['original_block_id'] is not None,
                'residual requires compact geometry and original block id')
    else:
        require(w['provenance']=='dense_main' and w['scope'] in ('full','stripe')
                and w['original_block_id'] is None, 'invalid dense provenance/scope')
    if w['scope']=='stripe':
        require(w['stripe_id'] is not None and w['host_slot'] is not None, 'stripe id and host slot required')
    else:
        require(w['row_begin']==0 and w['geometry_m']==w['m'], 'full/compact geometry must describe exactly this work')


def validate_records(records: list[dict[str, Any]]) -> Trace:
    require(len(records)>=3, 'incomplete trace')
    run=records[0]
    require(isinstance(run,dict),'JSONL run record must be an object')
    fields(run,RUN)
    require(run['kind']=='run' and run['schema']==SCHEMA and type(run['version']) is int
            and run['version']==VERSION, 'unsupported trace schema/version')
    for k in ('run_id','model','profile','backend','mode'):
        text(run[k],k)
    for k in ('activation_bits','weight_bits','dim','prompt_tokens','requested_generated_tokens'):
        integer(run[k],k)
    bits,dim=run['activation_bits'],run['dim']
    require(bits in (4,8) and bits==run['weight_bits'] and dim in (16,32,64)
            and run['profile']==f'a{bits}w{bits}-d{dim}-hp1', 'invalid run profile')
    require(run['backend']=='IM2P_SIM/GEMMINI_HP1', 'unsupported production backend')
    require(type(run['residual_enabled']) is bool, 'residual_enabled must be boolean')
    for field,length in (('source_commits',40),('source_worktree_sha256',64)):
        values=run[field]
        require(isinstance(values,dict) and set(values)==SOURCE_NAMES, 'missing source identities')
        require(all(isinstance(v,str) and re.fullmatch('[0-9a-f]{'+str(length)+'}',v) for v in values.values()),
                f'invalid {field}')
    phases: list[dict[str, Any]]=[]
    works: list[dict[str, Any]]=[]
    observed_counts: Counter[tuple[int,str,str]]=Counter()
    for sequence,r in enumerate(records):
        require(isinstance(r,dict), 'JSONL record must be an object')
        require(type(r.get('sequence')) is int and r['sequence']==sequence, 'duplicate/gapped/nonmonotonic sequence')
        require(r.get('run_id')==run['run_id'], 'mixed run identity')
        if sequence==0:
            continue
        kind=r.get('kind')
        if kind=='phase':
            fields(r,PHASE)
            integer(r['phase_id'],'phase_id');integer(r['input_tokens'],'input_tokens')
            require(r['phase_id']==len(phases), 'nonmonotonic phase identity')
            if not phases:
                require(r['phase_kind']=='prefill' and r['decode_index'] is None, 'first phase must be prefill')
            else:
                integer(r['decode_index'],'decode_index')
                require(r['phase_kind']=='decode' and r['decode_index']==len(phases)-1, 'decode indices must start at zero')
            phases.append(r)
        elif kind=='npu_work':
            require(bool(phases),'work before phase')
            validate_work(r,run,phases[-1]['phase_id'])
            works.append(r);observed_counts[(r['phase_id'],r['layer'],r['provenance'])]+=1
        elif kind=='run_end':
            require(sequence==len(records)-1,'record after run end')
            fields(r,END)
            require(r['status']=='success','production run did not complete successfully')
            require(isinstance(r['reason'],str),'run-end reason must be a string')
            integer(r['work_count'],'work_count')
            require(r['work_count']==len(works),'run-end work count mismatch')
            require(isinstance(r['independent_counts'],list),'independent production counts required')
            counts: Counter[tuple[int,str,str]]=Counter()
            for c in r['independent_counts']:
                require(isinstance(c,dict),'invalid independent count record')
                fields(c,{'phase_id','layer','provenance','count'})
                integer(c['phase_id'],'counter phase_id');integer(c['count'],'counter count',True)
                text(c['layer'],'counter layer');text(c['provenance'],'counter provenance')
                key=(c['phase_id'],c['layer'],c['provenance'])
                require(key not in counts,'duplicate independent counter key')
                counts[key]=c['count']
            require(counts==observed_counts,'trace vs independent accepted-dispatch count mismatch')
        else:
            raise TraceError(f'unsupported record kind: {kind}')
    require(records[-1]['kind']=='run_end' and bool(phases),'missing run end or explicit phases')
    return Trace(run,phases,works,records[-1])


def no_duplicate_keys(pairs: list[tuple[str,Any]]) -> dict[str,Any]:
    result: dict[str,Any]={}
    for key,value in pairs:
        require(key not in result,f'duplicate JSON key: {key}')
        result[key]=value
    return result


def read_trace(path: Path) -> Trace:
    records=[]
    try:
        with path.open(encoding='utf-8') as stream:
            for number,line in enumerate(stream,1):
                require(bool(line.strip()),f'blank record at line {number}')
                try:
                    records.append(json.loads(line,object_pairs_hook=no_duplicate_keys))
                except (json.JSONDecodeError,TraceError) as error:
                    raise TraceError(f'line {number}: {error}') from error
    except UnicodeError as error:
        raise TraceError('trace must be valid UTF-8') from error
    return validate_records(records)


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


def replay(trace: Trace, library: Path, sources: dict[str,Any]) -> dict[str,Any]:
    check_sources(trace,sources)
    per_layer: dict[str,int]=defaultdict(int)
    per_phase: dict[int,int]=defaultdict(int)
    works=[]
    for w in trace.works:
        answer=cli.estimate(library,model_document(trace,w))
        require(answer.get('status')=='PASS',f'first model rejection: sequence {w["sequence"]}: {answer.get("diagnostic",answer.get("status"))}')
        result=answer['result']
        cycles=integer(result['total_cycles'],'model total_cycles')
        per_layer[w['layer']]+=cycles;per_phase[w['phase_id']]+=cycles
        works.append({'sequence':w['sequence'],'layer':w['layer'],'phase_id':w['phase_id'],
                      'provenance':w['provenance'],'scope':w['scope'],'shape':[w['m'],w['n'],w['k']],
                      'tile_counts':[w['tile_i_count'],w['tile_j_count'],w['tile_k_count']],
                      'model_status':answer['status'],'model_result':result,'submission_count':result['loop_count']})
    counts=Counter(w['provenance'] for w in trace.works)
    return {'schema':'im2p-production-cycle-replay','version':1,'status':'PASS',
            'accounting_kind':'isolated-work-accounting','trace_schema':SCHEMA,'trace_version':VERSION,
            'profile':trace.run['profile'],'run_id':trace.run['run_id'],
            'source_commits':trace.run['source_commits'],'source_worktree_sha256':trace.run['source_worktree_sha256'],
            'source_compatibility':'PASS','cycle_library_sha256':hashlib.sha256(library.read_bytes()).hexdigest(),
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


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace',type=Path)
    parser.add_argument('--library',type=Path,required=True)
    parser.add_argument('--sources',type=Path,required=True,help='independent producer build source-identities.json')
    parser.add_argument('--cycle-certificate',type=Path,required=True,help='current isolated single-GEMM certificate bound to this library hash')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:
        cert=json.loads(args.cycle_certificate.read_text())
        require(cert.get('status')=='PASS' and cert.get('marker')=='IM2P_SINGLE_GEMM_CYCLE_MODEL_CURRENT', 'current single-GEMM certification required')
        require(cert.get('model_library_sha256')==hashlib.sha256(args.library.read_bytes()).hexdigest(),'cycle library differs from certificate')
        result=replay(read_trace(args.trace),args.library,json.loads(args.sources.read_text()))
        with args.output.open('x',encoding='utf-8') as stream:
            json.dump(result,stream,indent=2,sort_keys=True);stream.write('\n')
        print(json.dumps({k:result[k] for k in ('status','work_count','isolated_cycle_sum','accounting_kind')}))
    except (OSError,ValueError,KeyError,TypeError) as error:
        print(f'optrace replay failed: {error}',file=sys.stderr)
        return 1
    return 0

if __name__=='__main__':
    raise SystemExit(main())
