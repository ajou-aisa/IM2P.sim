#!/usr/bin/env python3
"""Trace actual generic FULL/accepted-stripe dispatches and compare provenance.

Native probes also verify numerical output and the actual RTL work payload.
Fixture source identities are explicit zeros; compiled source/archive identities
are independently recorded in each probe's provenance.json, never called a
real-model trace. No observed cycle answer enters replay.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from sim.cycle import optrace
from sim.tests.cycle.certify_production_geometry import build_native
from sim.tests.cycle.rtl_hardening import PROFILES, run_logged


def compare(trace: optrace.Trace, observations: list[dict[str,Any]], mode: str) -> dict[str,Any]:
    # Observation event numbers are part of the public passive test-hook ABI.
    header=(ROOT/'sim/tests/cycle/accepted_work_observer.h').read_text()
    import re
    event_name='IM2P_OBSERVE_ADMISSION' if mode=='full' else 'IM2P_OBSERVE_PUBLICATION'
    match=re.search(r'\b'+event_name+r'\s*=\s*(\d+)',header)
    if not match:
        raise ValueError('observer event ABI is not explicit: '+event_name)
    native=[r for r in observations if r['event']==int(match.group(1))]
    if len(native)!=len(trace.works):
        raise ValueError(f'work-count divergence: trace={len(trace.works)}, native={len(native)}')
    checked=[]
    for work,actual in zip(trace.works,native):
        g=actual['geometry']
        expected={k:g[k] for k in ('activation_bits','weight_bits','dim','n','k',
                                   'tile_i_count','tile_j_count','tile_k_count','row_begin','row_count')}
        if actual['scale_host_stride'] % 4:
            raise ValueError('native scale byte stride is not an int32 element stride')
        expected.update(m=g['row_count'],geometry_m=g['m'],production_geometry_version=g['version'],
                        activation_stride_bytes=actual['activation_host_stride'],
                        weight_stride_bytes=actual['weight_host_stride'],
                        output_stride_bytes=actual['output_host_stride'],
                        scale_stride_elements=actual['scale_host_stride']//4,
                        scope='full' if mode=='full' else 'stripe',provenance='dense_main',
                        numerical_datapath='hp1_scu',rmd_raw=False,host_integer_block_multiply=False)
        if mode=='pipeline':
            # Publication records transport geometry before this stripe owns
            # a physical slot. Read the slot from its actual RTL acceptance,
            # not from the previously active stripe at publication time.
            accepted_enum=re.search(r'\bIM2P_OBSERVE_ACCEPTED\s*=\s*(\d+)',header)
            if not accepted_enum:
                raise ValueError('missing acceptance observer ABI')
            owners=[r for r in observations if r['event']==int(accepted_enum.group(1))
                    and r['geometry']['stripe_id']==g['stripe_id']
                    and r['geometry']['row_begin']==g['row_begin']]
            if not owners or len({r['host_slot'] for r in owners})!=1:
                raise ValueError('stripe does not have one independently observed physical slot')
            expected.update(stripe_id=g['stripe_id'],host_slot=owners[0]['host_slot'])
        differences={k:{'trace':work[k],'production':v} for k,v in expected.items() if work[k]!=v}
        if differences:
            raise ValueError(f'field equality failed at {work["sequence"]}: {differences}')
        checked.append({'sequence':work['sequence'],'compared_fields':sorted(expected),'status':'PASS'})
    return {'status':'PASS','trace_work_count':len(trace.works),'independent_observer_count':len(native),
            'independent_runtime_count':trace.end['independent_counts'],'work_field_comparisons':checked}


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('golden-root','cargo-root','cycle-build','llama-config','out'):
        p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--profiles',nargs='+',choices=PROFILES,default=list(PROFILES))
    a=p.parse_args();out=a.out.resolve();out.mkdir(parents=True,exist_ok=False)
    rows=[];failure=None
    try:
        for profile in a.profiles:
            exe=build_native(a.golden_root.resolve(),a.cargo_root.resolve(),a.cycle_build.resolve(),
                             a.llama_config.resolve(),out,profile)
            dim=int(profile.split('-d')[1].split('-')[0])
            cases=[('full',shape) for shape in
                   ((1,1,32),(2,3,64),(65,67,96),(129,129,96),(dim*9+1,1,4096),(1,1,3072))]
            cases.append(('pipeline',(129,129,96)))
            for mode,shape in cases:
                case=exe.parent/f'{mode}-{shape[0]}-{shape[1]}-{shape[2]}'
                case.mkdir()
                observation=case/'accepted.jsonl';tracefile=case/'trace.jsonl'
                cmd=[str(exe),*map(str,shape),mode,str(observation),str(tracefile)]
                rc=run_logged(out,cmd,case/'run.log')
                if rc:
                    raise ValueError(f'{profile} native trace failed: {case}/run.log (exit {rc})')
                trace=optrace.read_trace(tracefile)
                observations=[json.loads(line) for line in observation.read_text().splitlines()]
                result=compare(trace,observations,mode)
                result.update(profile=profile,mode=mode,shape=shape,trace=str(tracefile))
                library=a.cycle_build/('libim2p_cycle_model.dylib' if sys.platform=='darwin' else 'libim2p_cycle_model.so')
                first=optrace._replay_fixture(trace,library);second=optrace._replay_fixture(trace,library)
                if first!=second:
                    raise ValueError('nondeterministic replay')
                (case/'replay-summary.json').write_text(json.dumps(first,indent=2)+'\n')
                result['replay_deterministic']=True
                (case/'comparison.json').write_text(json.dumps(result,indent=2)+'\n')
                rows.append(result)
                print(profile,mode,shape,'TRACE_DESCRIPTOR_EQUALITY_PASS',flush=True)
    except Exception as error:
        failure=str(error)
    result={'status':'PASS' if failure is None else 'FAIL','first_failure':failure,'cases':rows,
            'scope':'real production adapter/native runtime using synthetic numerical fixtures',
            'real_model_trace':False,'pipeline_overlap':'NOT_MODELED'}
    (out/'trace-validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('status','first_failure')}))
    return 0 if failure is None else 1

if __name__=='__main__':
    raise SystemExit(main())
