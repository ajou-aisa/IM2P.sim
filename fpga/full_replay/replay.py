#!/usr/bin/env python3
"""Same owned host fixture through simulator and real UART RTL, then f_out."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import time
import protocol


def run(command, log, marker):
    start=time.monotonic()
    with log.open('x') as output:
        result=subprocess.run(list(map(str,command)),stdout=output,stderr=subprocess.STDOUT)
    text=log.read_text()
    if result.returncode or marker not in text or any(s in text for s in ('Dynamic assertion failed','unexpected RTL $finish','FULL_REPLAY_FAIL:','RTL_FAIL:')):
        raise RuntimeError('incomplete/failed execution: '+str(log))
    return time.monotonic()-start


def main():
    p=argparse.ArgumentParser();p.add_argument('--host',type=Path,required=True);p.add_argument('--driver',type=Path,required=True)
    p.add_argument('--numerical-only',action='store_true',help='Explicitly omit passive address/scale monitors (e.g. assertion RTL); not full boundary proof')
    p.add_argument('--out',type=Path,required=True);p.add_argument('fixtures',nargs='+',type=Path);a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=False);dirs=[];results={}
    for fixture in a.fixtures:
        protocol.verify(fixture);directory=a.out/fixture.name;directory.mkdir();dirs.append(directory)
        for name in (*json.loads((fixture/'sha256.json').read_text()).keys(),'sha256.json'):shutil.copy2(fixture/name,directory/name)
        elapsed=run([a.host.resolve(),'simulate',directory.resolve()],directory/'r1.log','simulator_creates=1 simulator_executes=1')
        protocol.verify(directory)
        results[fixture.name]={'simulator_seconds':elapsed,'r1':json.loads((directory/'r1-stats.json').read_text())}
    protocol.prepare(dirs,a.out/'itinerary.txt')
    elapsed=run([a.driver.resolve(),(a.out/'itinerary.txt').resolve()],a.out/'rtl.log',f'RTL_PACKET_COMPLETE transactions={2*len(dirs)}')
    protocol.finish(dirs)
    for directory in dirs:
        run([a.host.resolve(),'replay',directory.resolve()],directory/'r2.log','simulator_creates=0 simulator_executes=0')
        results[directory.name]['r2']=json.loads((directory/'r2-stats.json').read_text())
        events=directory/'run-response.bin.events.json'
        if not events.exists() and not a.numerical_only:
            raise RuntimeError('required passive boundary evidence missing: '+str(events))
        if events.exists():
            observed=json.loads(events.read_text());m=json.loads((directory/'manifest.json').read_text())
            addresses=[0x40000+(b*m['I']+row)*256+j*4 for i in range(0,m['I'],16) for j in range(0,m['J'],16)
                       for b in range(m['K']//32) for row in range(i,min(i+16,m['I']))]
            works=((m['I']+15)//16)*((m['J']+15)//16)
            if (observed['output_addresses']!=addresses or observed['scale_block_transitions']!=works*(m['K']//32-1)
                or observed['launches']!=dirs.index(directory)+1 or observed['scale_requests']<1):
                raise ValueError('observed output/scale boundaries')
            results[directory.name]['observed']=observed
        protocol.verify(directory)
    (a.out/'summary.json').write_text(json.dumps({'status':'PASS','backend':'FPGA_REPLAY_V1 UART RTL',
      'physical_hardware':False,'passive_boundaries_required':not a.numerical_only,
      'fixtures':results,'RTL_wall_seconds':elapsed},indent=2)+'\n')
    print(f'{"NUMERICAL_REPLAY_PASS" if a.numerical_only else "FULL_REPLAY_PASS"} fixtures={len(dirs)} exact_raw_and_fout=true physical_hardware=false')


if __name__=='__main__':main()
