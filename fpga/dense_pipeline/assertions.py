#!/usr/bin/env python3
"""Focused frozen-core/passive and fresh provider BSC assertion verification."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def run(command,cwd,stem):
    stem.parent.mkdir(parents=True,exist_ok=True)
    (stem.with_suffix('.command.json')).write_text(json.dumps({'argv':[str(x) for x in command],'cwd':str(cwd)},indent=2)+'\n')
    start=time.monotonic()
    with stem.with_suffix('.log').open('x') as log:
        result=subprocess.run([str(x) for x in command],cwd=cwd,stdout=log,stderr=subprocess.STDOUT)
    text=stem.with_suffix('.log').read_text()
    status={'exit':result.returncode,'seconds':time.monotonic()-start,'log_sha256':digest(stem.with_suffix('.log'))}
    stem.with_suffix('.status.json').write_text(json.dumps(status,indent=2)+'\n')
    if result.returncode:raise RuntimeError(str(stem)+' failed')
    return text


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--candidate',type=Path,required=True)
    parser.add_argument('--legacy',type=Path,required=True);parser.add_argument('--fixture',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    candidate,legacy,fixture,out=(p.resolve() for p in (args.candidate,args.legacy,args.fixture,args.out))
    out.mkdir(parents=True,exist_ok=False)
    identities={}
    manifest=json.loads((candidate/'source-sha256.json').read_text())
    for name,expected in manifest.items():
        p=candidate/'source'/name
        if digest(p)!=expected:raise ValueError('candidate source changed: '+name)
        identities[str(p)]=expected
    core=['src/common/Arithmetic.bsv','src/common/Config.bsv','src/core/IM2PCore.bsv',
          'src/control/MatmulScheduler.bsv','src/control/WorkScheduler.bsv']
    for name in core:
        old=legacy/'work/fixed-source'/name
        if digest(old)!=digest(candidate/'source'/name):raise ValueError('legacy/new core differs: '+name)
        identities[str(old)]=digest(old)
    guard=(candidate/'source/src/core/IM2PCore.bsv').read_text()
    if '!activationResponsePendingReg' not in guard:raise ValueError('activation capacity guard missing')
    strict_path=legacy/'evidence/passive-strict-gate/verify.py'
    spec=importlib.util.spec_from_file_location('strict',strict_path);strict=importlib.util.module_from_spec(spec);spec.loader.exec_module(strict)
    records=[]
    for variant in ('A','B'):
        folder=out/('minimal-'+variant);folder.mkdir()
        binary=legacy/('shape-fixed-'+variant)/'resident_shape';identities[str(binary)]=digest(binary)
        text=run([binary,'--shape','9','1','1','--wave','--trace','--monitor'],folder,folder/'run')
        status=strict.validate_log(text,(9,1,1));status['bsc_assertions']=variant=='B'
        status['rtl_cycles']=[json.loads(line)['rtl_cycles'] for line in text.splitlines() if line.startswith('{') and '"rtl_cycles"' in line]
        status['clock_half_period_ps']=20
        (folder/'validated.json').write_text(json.dumps(status,indent=2)+'\n');records.append(status)
    for name,marker in (('IM2PCoreActivationBuffer','IM2P ACTIVATION BUFFER: PASS'),
                        ('IM2PLookahead','IM2P LOOKAHEAD: PASS'),('IM2PCoreMatrix','IM2P CORE MATRIX: PASS'),
                        ('IM2PCoreMatrixScale','IM2P MATRIX SCALE: PASS')):
        folder=out/name;folder.mkdir();binary=legacy/'evidence/fixed-related-bsv'/('mkTb'+name)/'build/bin'/('Tb'+name)
        identities[str(binary)]=digest(binary)
        for shared in binary.parent.glob(binary.name+'*.so'):identities[str(shared)]=digest(shared)
        text=run([binary],folder,folder/'run')
        if text.splitlines().count(marker)!=1 or re.search(r'FAIL|Dynamic assertion failed|Timeout|%Error',text):
            raise ValueError('legacy assertion bench failed: '+name)
        records.append({'bench':name,'status':'PASS','physical_dim':2,'bsc_assertions':True})
    generated=out/'provider';generated.mkdir()
    for name in ('bsc','info','rtl','primitives'):(generated/name).mkdir()
    source=candidate/'source'
    command=['bsc','-u','-verilog','-check-assert','-p','+:src/common:src/io:src/array:src/vector:src/accumulator:src/control:src/core:synth',
             '-steps','4000000','-steps-warn-interval','1000000','-steps-max-intervals','20','+RTS','-K256M','-RTS',
             '-bdir',generated/'bsc','-info-dir',generated/'info','-vdir',generated/'rtl','-g','mkDensePipeline','synth/DensePipeline.bsv']
    run(command,source,generated/'bsc-build')
    primitives=Path(shutil.which('bsc')).resolve().parents[1]/'lib/Verilog'
    for name in ('FIFO2.v','BRAM1.v','BRAM2.v','RegFile.v'):shutil.copy2(primitives/name,generated/'primitives'/name)
    test=HERE/'provider_assert.cpp';identities[str(test)]=digest(test)
    command=['verilator','--cc','--exe','--build','-j','2','--timing','--assert','-Wno-fatal','--top-module','mkDensePipeline',
             '--Mdir',generated/'obj','--output-split','20000','--output-split-cfuncs','500',
             '-CFLAGS','-O2 -g0 -std=c++20',generated/'rtl/mkDensePipeline.v',*sorted((generated/'primitives').glob('*.v')),test]
    run(command,out,generated/'verilator-build')
    f=json.loads((fixture/'manifest.json').read_text())
    for name in ('manifest.json','staging.bin','expected-raw.bin'):identities[str(fixture/name)]=digest(fixture/name)
    binary=generated/'obj/VmkDensePipeline'
    text=run([binary,fixture/'staging.bin',fixture/'expected-raw.bin',f['I'],f['J'],f['K'],f['activation_rows_per_stripe']],out,generated/'run')
    count=2*f['I']*f['J']*(f['K']//32)
    marker=f'PROVIDER_ASSERT_PASS jobs=2 stripes=3 comparisons={count}'
    if text.count(marker)!=1 or text.count('PROVIDER_JOB_PASS')!=2 or re.search(r'FAIL|Dynamic assertion failed|%Error|unexpected.*finish',text):
        raise ValueError('provider assertion count/marker')
    if any(digest(Path(path))!=expected for path,expected in identities.items()):raise ValueError('source/artifact changed')
    (out/'input-sha256.json').write_text(json.dumps(identities,indent=2)+'\n')
    (out/'summary.json').write_text(json.dumps({'status':'PASS','board_access':False,'legacy':records,
        'provider':{'bsc_assertions':True,'jobs':2,'stripes':3,'raw_comparisons':count,'clock_period_ns':40},
        'not_run':['host reconstruction in direct-provider bench; covered by host integration',
                   'physical board access'], 'all_inputs_preserved':True},indent=2)+'\n')
    print('FOCUSED_ASSERTIONS_PASS legacy=6 provider_jobs=2 provider_stripes=3 raw='+str(count),flush=True)


if __name__=='__main__':main()
