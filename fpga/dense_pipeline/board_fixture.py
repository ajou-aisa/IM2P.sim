#!/usr/bin/env python3
"""Existing portable FULL fixture, canonical three-stripe IFR2 board replay."""
import argparse
import importlib.util
import json
from pathlib import Path
import struct
import zlib

HEADER = struct.Struct('<4sBBHQIHHHHI')
def packet(op, run, generation, fields=(0,0,0,0), payload=b''):
    body = HEADER.pack(b'IFR2', 2, op, 0x0810, run, generation, *fields, len(payload)) + payload
    return body + struct.pack('<I', zlib.crc32(body))

def prepare(fixture, output):
    output.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location('full_protocol', Path(__file__).parents[1]/'full_replay/protocol.py')
    old = importlib.util.module_from_spec(spec); spec.loader.exec_module(old); old.verify(fixture)
    manifest = json.loads((fixture/'manifest.json').read_text())
    m,n,k = (manifest[x] for x in ('I','J','K'))
    stripe = manifest['activation_rows_per_stripe']; identity = manifest['run_id']
    assert 0 < m <= 336 and 0 < n <= 48 and k in (32,64,96) and 0 < stripe <= 336 and stripe%16 == 0
    staging = (fixture/'staging.bin').read_bytes(); assert len(staging)==m*128+k*64
    commands = [('cap',packet(0,0,0)),('begin',packet(4,identity,1,(m,n,k,stripe),staging[m*128:]))]
    for index,begin in enumerate(range(0,m,stripe)):
        rows=min(stripe,m-begin)
        commands += [(f'publish-{index}',packet(5,identity,1,(begin,rows,index,index%2),staging[begin*128:(begin+rows)*128])),
                     (f'poll-{index}',packet(6,identity,1))]
    commands += [('finish',packet(7,identity,1)),('release',packet(2,identity,1))]
    lines=[]
    for name,request in commands:
        path=output/(name+'-request.bin');path.write_bytes(request)
        lines.append(f'{path.resolve()} {(output/(name+"-response.bin")).resolve()}')
    (output/'itinerary.txt').write_text('\n'.join(lines)+'\n')
    (output/'fixture.json').write_text(json.dumps({'fixture':str(fixture.resolve()),'manifest':manifest,
        'transactions':len(commands),'jobs':1,'publications':(m+stripe-1)//stripe,
        'source':'existing native host fixture; deterministic stripe replay, not live producer'},indent=2)+'\n')

def finish(output):
    plan=json.loads((output/'fixture.json').read_text()); manifest=plan['manifest'];fixture=Path(plan['fixture'])
    m,n,k=(manifest[x] for x in ('I','J','K')); blocks=k//32;tiles=(n+15)//16
    raw=bytearray(4*m*n*blocks); wire=bytearray(64*m*tiles*blocks); transactions=0
    for request_path,response_path in [line.split() for line in (output/'itinerary.txt').read_text().splitlines()]:
        q=Path(request_path).read_bytes();r=Path(response_path).read_bytes(); op=q[5]
        assert len(r)>=148 and r[:4]==b'OFR2' and r[4]==2 and r[5]==0 and r[6:8]==b'\x10\x08'
        assert r[8:16]==q[8:16] and struct.unpack_from('<I',r,16)[0]==(0 if op==0 else 1)
        assert len(r)==148+struct.unpack_from('<I',r,20)[0] and zlib.crc32(r[:-4])==struct.unpack_from('<I',r,len(r)-4)[0]
        assert r[88]==op
        if op==6:
            assert r[89]==1
            stripe_id,begin,rows=struct.unpack_from('<HHH',r,90)
            assert begin==stripe_id*manifest['activation_rows_per_stripe'] and rows==min(manifest['activation_rows_per_stripe'],m-begin)
            assert len(r)==148+64*blocks*rows*tiles
            for block in range(blocks):
                for row in range(rows):
                    source=144+(block*rows+row)*tiles*64
                    wire[(block*m+begin+row)*tiles*64:(block*m+begin+row+1)*tiles*64]=r[source:source+tiles*64]
                    raw[(block*m+begin+row)*n*4:(block*m+begin+row+1)*n*4]=r[source:source+n*4]
                    assert not any(r[source+n*4:source+tiles*64])
        else: assert r[89]==0
        transactions+=1
    assert raw==(fixture/'expected-raw.bin').read_bytes()
    final=(output/'finish-response.bin').read_bytes(); cycles,fragments,works,a,w,writes,acks=struct.unpack_from('<7Q',final,32)
    assert works==((m+15)//16)*tiles and fragments==works*(k//16) and writes==acks==m*tiles*blocks
    # The existing host replay consumes this owned, fully decoded signed32 result.
    decoded=struct.pack('<Q',manifest['run_id'])+final[32:88]+wire
    (output/'decoded.bin').write_bytes(decoded)
    (output/'raw.bin').write_bytes(raw)
    result={'pass':True,'transactions':transactions,'logical_jobs':1,'stripes':plan['publications'],
            'raw_comparisons':m*n*blocks,'cycles':cycles,'fragments':fragments,'works':works,
            'host_reconstruction':'pending existing capture replay with decoded.bin'}
    (output/'numerical.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','finish']);p.add_argument('output',type=Path);p.add_argument('--fixture',type=Path)
    a=p.parse_args()
    if a.stage=='prepare':prepare(a.fixture.resolve(),a.output.resolve())
    else:finish(a.output.resolve())
