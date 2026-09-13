#!/usr/bin/env python3
"""IFR3 FULL reference/gate PTY tests. Synthetic pins/cycles are not board evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pty
import select
import subprocess
import tempfile

from test_uart import recv, reply, send, raw

ROOT = Path(__file__).resolve().parents[2]
HW, RTL = '1' * 64, '2' * 64
CASES = {'test16': (16, 16, 32, 12345), 'test64': (321, 48, 64, 23456),
         'test96': (321, 48, 96, 34567)}
CPP = r'''
#include "uart.hpp"
#include <algorithm>
#include <iostream>
#include <stdexcept>
#include <vector>
void ck(bool value,const char *why){if(!value)throw std::runtime_error(why);}
struct Output {size_t callbacks=0;};
int weight(void*,size_t,size_t,size_t n,int8_t*out){std::fill_n(out,n,1);return 0;}
int scale(void*,size_t b,size_t,size_t n,uint32_t*out){std::fill_n(out,n,b?256:1);return 0;}
int write(void*ctx,size_t block,size_t,size_t,size_t,const int64_t*values,uint32_t domain){
 if(block||domain!=2||!values)return -1;
 ++static_cast<Output*>(ctx)->callbacks;return 0;
}
int main(int argc,char**argv){
 if(argc!=9)return 2;
 const std::string mode=argv[2];
 try{
  const auto reference=im2p::fpga::FullCycleReference::load(argv[3],std::string(64,'1'),std::string(64,'2'));
  const size_t m=std::stoull(argv[5]),n=std::stoull(argv[6]),k=std::stoull(argv[7]);
  const auto expected=reference.expect(argv[4],m,n,k);
  ck(!mode.starts_with("reference_"),"invalid reference accepted");
  im2p::fpga::UART uart(argv[1],3.0);
  uart.expect_full(expected);
  Output output;
  std::vector<int8_t> a(m*k,1);
  im2p_matmul_desc_t d{};d.abi_version=5;
  d.activation_bits=d.weight_bits=8;d.activation_storage_bytes=d.weight_storage_bytes=1;
  d.dim=16;d.m=m;d.n=n;d.k=k;d.tile_i_rows=d.tile_j_columns=16;
  d.vector_op=4;d.output_domain=2;d.block_size=32;d.scale_total_k=k;
  d.scale_row_stride=d.scale_valid_columns=d.weight_row_stride_bytes=d.output_row_stride=n;
  d.activations=a.data();d.activation_row_stride_bytes=k;d.provider={&output,weight,nullptr,scale,write};
  im2p_work_stats_extended_t stats{};
  const auto status=uart.full(&d,&stats);
  if(mode=="positive"){
   ck(status==0&&output.callbacks==m*((n+15)/16),"valid FULL failed");
   const auto owned=uart.telemetry();
   ck(owned.completed&&owned.statistics&&owned.statistics->elapsed_cycles==expected.cycles,"FULL snapshot");
   ck(uart.release()==0,"normal RELEASE");
   const auto transactions=uart.metrics().transactions;
   ck(uart.full(&d,&stats)!=0&&uart.metrics().transactions==transactions,"missing next expectation sent RUN");
   ck(uart.release()!=0&&owned.completed&&!owned.released,"missing expectation lost sticky/owned state");
  }else{
   const std::string error=uart.error();
   ck(status!=0&&output.callbacks==0&&uart.last_raw.empty(),"cycle gate leaked output");
   ck(error.find("IFR3 FULL cycle mismatch expected=")!=std::string::npos&&
      error.find("response_header=")!=std::string::npos&&error.find("numerical_revision=2")!=std::string::npos,
      "cycle failure evidence missing");
   const auto partial=uart.telemetry();
   ck(!partial.completed&&!partial.released&&partial.statistics&&partial.final_response_header.size()==144,
      "partial failure snapshot lost");
   const auto transactions=uart.metrics().transactions;
   ck(uart.release()!=0&&uart.full(&d,&stats)!=0&&uart.metrics().transactions==transactions,
      "failure cleanup or next RUN sent a command");
   std::cout<<error<<'\n';
  }
  std::cout<<"IFR3_FULL_GATE PASS "<<mode<<" callbacks="<<output.callbacks<<" mock_only=1\n";
 }catch(const std::exception&e){
  if(mode.starts_with("reference_")&&std::string(e.what())!="invalid reference accepted"){
   std::cout<<"IFR3_FULL_GATE PASS "<<mode<<" rejected="<<e.what()<<" mock_only=1\n";return 0;
  }
  std::cerr<<e.what()<<'\n';return 1;
 }
}
'''


def reference_text():
    prefix = (f'IFR3_FULL_REFERENCE_V1\nhardware_sha256 {HW}\nproduction_rtl_sha256 {RTL}\n'
              'backend FPGA_UART\nprotocol 3\nprofile 0810\nsemantic_capability 0294\n'
              'numerical_revision 2\nmode FULL\n')
    return prefix + ''.join(f'fixture {name} {m} {n} {k} {cycles}\n'
                            for name, (m, n, k, cycles) in CASES.items())


def run(exe, out, mode, name, text):
    m, n, k, cycles = CASES[name]
    ref = out / f'{mode}-{name}.txt'
    ref.write_text(text)
    master, slave = pty.openpty()
    command = [str(exe), os.ttyname(slave), mode, str(ref), name, str(m), str(n), str(k), 'mock']
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        if not mode.startswith('reference_'):
            recv(master, 0, 0, 0)
            send(master, reply(0, (336, 48, 96), 0, 5))
            request = recv(master, 1)
            assert len(request) == 36 + m * 128 + k * 64 + (k // 32) * 256
            actual = cycles + (1 if mode == 'plus1' else -1 if mode == 'minus1' else 0)
            works, writes = ((m + 15) // 16) * ((n + 15) // 16), m * ((n + 15) // 16)
            counts = (actual, works * (k // 16), works, 1, 1, writes, writes)
            send(master, reply(1, (m, n, k), payload=raw(m, n, k, 0, m), counts=counts))
            if mode == 'positive':
                recv(master, 2)
                send(master, reply(2, (m, n, k)))
        output = process.communicate(timeout=6)[0]
        assert process.returncode == 0 and f'IFR3_FULL_GATE PASS {mode}' in output, output
        assert not select.select([master], [], [], 0)[0], 'command after rejection or failed FULL gate'
        (out / f'{mode}-{name}.log').write_text(output)
        return {'mode': mode, 'fixture': name, 'status': 'PASS', 'command': command,
                'commands_after_failure': 0, 'mock_only': True}
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
        os.close(slave)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='ifr3-full-gate-') as temporary:
        out = args.out.resolve() if args.out else Path(temporary)
        if args.out:
            out.mkdir(parents=True, exist_ok=False)
        source, exe = out / 'test.cpp', out / 'test'
        source.write_text(CPP)
        command = ['c++', '-std=c++20', '-O2', '-Wall', '-Wextra', '-Werror',
                   '-I'+str(ROOT/'sim/include'), '-I'+str(Path(__file__).parent), str(source),
                   str(Path(__file__).with_name('uart.cpp')), '-pthread', '-o', str(exe)]
        (out/'build-command.json').write_text(json.dumps(command, indent=2)+'\n')
        subprocess.run(command, check=True)
        results = [run(exe, out, mode, name, reference_text())
                   for name in CASES for mode in ('positive', 'minus1', 'plus1')]
        original = reference_text()
        changes = {'backend': original.replace('backend FPGA_UART', 'backend SIMULATOR'),
                   'profile': original.replace('profile 0810', 'profile 0410'),
                   'hardware': original.replace(HW, '3'*64),
                   'rtl': original.replace(RTL, '4'*64),
                   'revision': original.replace('numerical_revision 2', 'numerical_revision 1'),
                   'capability': original.replace('0294', '0214'),
                   'protocol': original.replace('protocol 3', 'protocol 2'),
                   'missing': original.replace('fixture test16 16 16 32 12345\n', ''),
                   'duplicate': original + 'fixture test16 16 16 32 12345\n',
                   'negative_cycle': original.replace('32 12345', '32 -1'),
                   'overflow_cycle': original.replace('32 12345', '32 18446744073709551616'),
                   'empty': ''}
        results += [run(exe, out, 'reference_'+mode, 'test16', text) for mode, text in changes.items()]
        identities = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in (Path(__file__).resolve(), Path(__file__).with_name('test_uart.py'),
                                Path(__file__).with_name('uart.cpp'), Path(__file__).with_name('uart.hpp'),
                                ROOT/'sim/include/im2p_sim.h')}
        (out/'results.json').write_text(json.dumps({'tests': len(results), 'results': results,
            'source_sha256': identities, 'physical_device_access': False,
            'reference_identity': 'synthetic PTY only; not measured cycle provenance'}, indent=2)+'\n')
        print(f'IFR3_FULL_GATE_SUITE PASS tests={len(results)} mock_only=1 physical_devices=0')


if __name__ == '__main__':
    main()
