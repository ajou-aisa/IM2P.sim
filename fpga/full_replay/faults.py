#!/usr/bin/env python3
"""Real UART/core RTL faults. Writes fresh requests; never fabricates results."""
import argparse
import json
from pathlib import Path
import struct
import subprocess
import protocol

p=argparse.ArgumentParser();p.add_argument('driver',type=Path);p.add_argument('fixture',type=Path);p.add_argument('out',type=Path)
a=p.parse_args();a.out.mkdir(parents=True,exist_ok=False)
staging=(a.fixture/'staging.bin').read_bytes()
run=lambda identity,generation:protocol.packet(1,identity,generation,(16,16,32),staging)
control=lambda op,identity=1,generation=0:protocol.packet(op,identity,generation)
bad_profile=bytearray(control(0));bad_profile[6]^=1
bad_length=bytearray(run(1,1)[:32]);struct.pack_into('<I',bad_length,28,1)
over_capacity=bytearray(run(1,1)[:32]);struct.pack_into('<H',over_capacity,20,33)
bad_crc=bytearray(run(1,1));bad_crc[-1]^=1
cases=[('cap',control(0),0),('profile',bytes(bad_profile),2),
 ('length',bytes(bad_length),2),('abort_length',control(3),0),
 ('capacity',bytes(over_capacity),2),('abort_capacity',control(3),0),
 ('crc',bytes(bad_crc),3),('sticky',control(0),8),('abort_crc',control(3),0),
 ('run1',run(1,1),0),('wrong_release',control(2,1,2),6),('release1',control(2,1,1),0),
 ('duplicate',run(1,1),6),('run2',run(2,2),0),('release2',control(2,2,2),0),
 ('partial',run(3,3)[:40],8),('@reset',None,None),
 ('stale_after_reset',run(3,2),6),('run3',run(3,3),0),('wrong_identity',control(2,4,3),6),('release3',control(2,3,3),0)]
lines=[]
for name,data,status in cases:
    if name=='@reset':lines.append('@reset unused');continue
    source=a.out/(name+'.request');source.write_bytes(data)
    lines.append(f'{source.resolve()} {(a.out/(name+".response")).resolve()}')
plan=a.out/'itinerary.txt';plan.write_text('\n'.join(lines)+'\n')
with (a.out/'rtl.log').open('w') as log:
    result=subprocess.run([str(a.driver.resolve()),str(plan.resolve())],stdout=log,stderr=subprocess.STDOUT)
if result.returncode:raise RuntimeError('fault RTL run failed')
for name,data,status in cases:
    if name=='@reset':continue
    response=(a.out/(name+'.response')).read_bytes()
    if len(response)<100 or response[5]!=status:raise ValueError(f'{name}: unexpected response status')
    if name.startswith('run'):
        identity=int(name[-1]);protocol.decode(response,identity,identity,(16,16,32))
        if response[96:-4]!=(a.fixture/'expected-raw.bin').read_bytes():raise ValueError('fault-run numerical mismatch')
log=(a.out/'rtl.log').read_text()
if 'RTL_PACKET_COMPLETE transactions=20 launches=3' not in log:raise ValueError('fault launch conservation')
summary={'status':'PASS','transactions':20,'actual_launches':3,'valid_outputs':768,
         'faults':['profile','payload length','capacity','CRC','sticky','abort','wrong release generation',
                   'duplicate generation','partial input timeout','reset','stale generation after reset','wrong release identity'],
         'not_run':['abort packet while core RUN','forced output BRAM backpressure','UART break/stop-bit injection']}
(a.out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))
