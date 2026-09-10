#!/usr/bin/env python3
"""Approved SRAM candidate measurement only; this script never programs FPGA."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import struct
import subprocess
import time
import protocol


def check_host(log, backend, simulator_calls):
    text=log.read_text()
    if (f'{backend} PASS logical=1 raw=' not in text or
        f'simulator_creates={simulator_calls} simulator_executes={simulator_calls}' not in text or
        any(marker in text for marker in ('FULL_REPLAY_FAIL:', 'Dynamic assertion failed', 'unexpected RTL $finish'))):
        raise RuntimeError('host numerical/backend completion missing: '+str(log))


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--device',required=True)
    p.add_argument('--bitstream',type=Path,required=True)
    p.add_argument('--approved-sha256',required=True)
    p.add_argument('--programming-log',type=Path,required=True)
    p.add_argument('--host',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--repetitions',type=int,default=5)
    p.add_argument('--timeout',type=float,default=5.0)
    p.add_argument('fixtures',nargs='+',type=Path)
    a=p.parse_args()
    if a.repetitions<5 or len(a.approved_sha256)!=64:
        raise ValueError('five measured repetitions and full approved SHA256 required')
    actual=hashlib.sha256(a.bitstream.read_bytes()).hexdigest()
    if actual!=a.approved_sha256 or not a.programming_log.is_file():
        raise ValueError('approved bitstream/programming evidence mismatch')
    a.out.mkdir(parents=True,exist_ok=False)
    frozen={str(x.resolve()):hashlib.sha256(x.read_bytes()).hexdigest()
            for x in (a.bitstream,a.programming_log,a.host,Path(__file__),Path(protocol.__file__))}
    for fixture in a.fixtures:protocol.verify(fixture)
    (a.out/'identity.json').write_text(json.dumps({'backend':'FPGA_REPLAY_V1','hardware':True,
      'clock_hz_nominal':25000000,'artifact_hashes':frozen,'repetitions':a.repetitions,'warmups':1,
      'fixture_prequantized':True,'programming_performed_by_harness':False,
      'timing_scope':'prequantized owned fixture: pack, UART execution/results, existing reconstruction and commit; host process/file overhead included; quantization capture excluded',
      'simulator_scope':'same restored fixture, production simulator, block reconstruction, exact comparison and process/file overhead; no UART',
      'caution':'programming log must independently verify live SRAM; SHA argument alone does not'},indent=2)+'\n')
    serial=protocol.UART(a.device,a.timeout)
    samples=[]
    try:
        cap=serial.transfer(protocol.packet(0,0x46554c4c,0))
        generation,_=protocol.decode(cap,0x46554c4c)
        (a.out/'capability-response.bin').write_bytes(cap)
        for fixture in a.fixtures:
            manifest=json.loads((fixture/'manifest.json').read_text());identity=manifest['run_id']
            shape=tuple(manifest[k] for k in ('I','J','K'))
            for repetition in range(a.repetitions+1):
                directory=a.out/f'{fixture.name}-{repetition:02d}';directory.mkdir()
                for name in json.loads((fixture/'sha256.json').read_text()):shutil.copy2(fixture/name,directory/name)
                shutil.copy2(fixture/'sha256.json',directory/'sha256.json')
                start=time.perf_counter_ns()
                payload=(directory/'staging.bin').read_bytes();generation+=1
                request=protocol.packet(1,identity,generation,shape,payload)
                prepared=time.perf_counter_ns()
                response=serial.transfer(request);received=time.perf_counter_ns()
                protocol.decode(response,identity,generation,shape)
                (directory/'run-request.bin').write_bytes(request);(directory/'run-response.bin').write_bytes(response)
                (directory/'decoded.bin').write_bytes(struct.pack('<Q',identity)+response[32:88]+response[96:-4])
                with (directory/'host.log').open('w') as log:
                    subprocess.run([str(a.host.resolve()),'replay',str(directory.resolve())],check=True,stdout=log,stderr=subprocess.STDOUT,timeout=a.timeout)
                check_host(directory/'host.log','FPGA_REPLAY_V1',0)
                committed=time.perf_counter_ns()
                release=serial.transfer(protocol.packet(2,identity,generation))
                (directory/'release-response.bin').write_bytes(release)
                sim_start=time.perf_counter_ns()
                with (directory/'simulator.log').open('w') as log:
                    subprocess.run([str(a.host.resolve()),'simulate',str(directory.resolve())],check=True,stdout=log,stderr=subprocess.STDOUT,timeout=a.timeout)
                check_host(directory/'simulator.log','IM2P_SIM',1)
                sim_done=time.perf_counter_ns()
                cycles=struct.unpack_from('<Q',response,32)[0]
                sample={'fixture':fixture.name,'warmup':repetition==0,'generation':generation,
                  'prepare_pack_ns':prepared-start,'transfer_execute_receive_ns':received-prepared,
                  'decode_reconstruct_commit_ns':committed-received,'full_ns':committed-start,
                  'resident_core_cycles':cycles,'resident_nominal_ns':cycles*40,
                  'matched_simulator_ns':sim_done-sim_start,'request_bytes':len(request),
                  'response_bytes':len(response),'release_bytes':136,'failures':0,'retries':0}
                samples.append(sample)
                with (a.out/'samples.jsonl').open('a') as log:log.write(json.dumps(sample)+'\n')
        summary={}
        for fixture in a.fixtures:
            selected=[s for s in samples if s['fixture']==fixture.name and not s['warmup']]
            summary[fixture.name]={name:{'median':statistics.median(s[name] for s in selected),
              'min':min(s[name] for s in selected),'max':max(s[name] for s in selected)}
              for name in ('full_ns','resident_core_cycles','resident_nominal_ns','matched_simulator_ns')}
        (a.out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    except Exception as error:
        (a.out/'failure.json').write_text(json.dumps({'error':str(error),'retry':False,'automatic_programming':False})+'\n')
        raise
    finally:
        serial.close()
        if any(hashlib.sha256(Path(path).read_bytes()).hexdigest()!=value for path,value in frozen.items()):
            raise ValueError('measurement artifacts changed')


if __name__=='__main__':main()
