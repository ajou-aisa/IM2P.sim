#!/usr/bin/env python3
"""PTY framing/ownership tests. Mock words are never FPGA numerical evidence."""
import argparse
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import tempfile
import time
import zlib

CPP = r'''
#include "uart.hpp"
#include <algorithm>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <tuple>
#include <vector>
using Tuple=std::tuple<size_t,size_t,size_t,size_t>;
struct Provider {std::vector<Tuple> expected;size_t writes=0;};
void check(bool v,const char *why){if(!v)throw std::runtime_error(why);}
int weight(void*,size_t row,size_t col,size_t count,int8_t*out){for(size_t l=0;l<count;++l)out[l]=int8_t((row*3+col+l)%127);return 0;}
int scale(void*,size_t,size_t,size_t count,int8_t*out){std::fill_n(out,count,1);return 0;}
int output(void *p,size_t block,size_t row,size_t col,size_t count,const int64_t *lanes){
 auto&o=*static_cast<Provider*>(p);if(o.writes>=o.expected.size()||o.expected[o.writes++]!=Tuple(block,row,col,count))return -1;
 for(size_t l=0;l<count;++l)if(lanes[l]!=int64_t(block*100000+row*100+col+l)-5000)return -1;return 0;
}
int main(int argc,char**argv){try{
 const std::string mode=argv[2];Provider p;for(size_t i=0;i<17;i+=16)for(size_t j=0;j<19;j+=16)for(size_t b=0;b<2;++b)for(size_t r=i;r<std::min(i+16,size_t(17));++r)p.expected.emplace_back(b,r,j,std::min(size_t(16),19-j));
 std::vector<uint8_t>a(17*64);for(size_t i=0;i<a.size();++i)a[i]=uint8_t(i%251);
 im2p_matmul_desc_t d{};d.abi_version=4;d.activation_bits=d.weight_bits=8;d.activation_storage_bytes=d.weight_storage_bytes=1;d.dim=16;d.activations=a.data();d.m=17;d.n=19;d.k=64;d.activation_row_stride_bytes=64;d.tile_i_rows=d.tile_j_columns=16;d.block_size=32;d.vector_op=IM2P_VECTOR_EXTERNAL;d.scale_total_k=64;d.scale_row_stride=d.scale_valid_columns=d.weight_row_stride_bytes=d.output_row_stride=19;d.provider={&p,weight,nullptr,scale,output};im2p_work_stats_extended_t stats{};
 im2p::fpga::UART u(argv[1],mode=="timeout"?0.15:3.0);
 if(mode=="invalid")d.k=33;
 const int result=u.full(&d,&stats);
 if(mode=="success"||mode=="release_duplicate"){
  check(result==0,"full failed");check(p.writes==68&&u.last_raw.size()==646,"callback/raw count");check(u.metrics().transactions==1,"release occurred before caller commit");
  const int released=u.release();
  if(mode=="release_duplicate"){check(released!=0,"duplicate RUN accepted as RELEASE");check(u.release()!=0,"sticky release");}
  else {check(released==0,"release failed");check(u.metrics().transactions==2&&u.metrics().request_bytes==6344&&u.metrics().response_bytes==4552,"metrics bytes");check(u.metrics().sustained_seconds>=u.metrics().service_seconds&&u.metrics().core_cycles==2021,"metric endpoints");p.writes=0;check(u.full(&d,&stats)==0&&p.writes==68,"second invocation");check(u.release()==0&&u.generation()==7,"second release identity");}
 }else{check(result!=0,"failure accepted");check(p.writes==0,"output leaked before validation");check(!u.error().empty(),"missing sticky error");check(u.full(&d,&stats)!=0&&u.release()!=0,"failed invocation recovered");}
 std::cout<<"UART_PTY PASS mode="<<mode<<" mock_framing_only=1\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;} }
'''


def read_exact(fd, count):
    data = bytearray()
    deadline = time.monotonic() + 5
    while len(data) < count:
        if not select.select([fd], [], [], max(0, deadline-time.monotonic()))[0]:
            raise TimeoutError('test peer request timeout')
        data.extend(os.read(fd, min(7, count-len(data))))
    return bytes(data)


def receive(fd):
    header = read_exact(fd, 32)
    payload = read_exact(fd, struct.unpack_from('<I', header, 28)[0]+4)
    request = header+payload
    assert zlib.crc32(request[:-4]) == struct.unpack_from('<I', request, len(request)-4)[0]
    assert request[:8] in (b'IFR1\x01\x00\x10\x08', b'IFR1\x01\x01\x10\x08', b'IFR1\x01\x02\x10\x08')
    return request


def response(identity, generation, shape, payload=b'', counts=(0,)*7):
    out = bytearray(96)
    struct.pack_into('<4sBBHQIIHHHH', out, 0, b'OFR1',1,0,0x810,identity,generation,len(payload),*shape,0)
    struct.pack_into('<7Q',out,32,*counts)
    return bytes(out)+payload+struct.pack('<I',zlib.crc32(out+payload))


def send(fd, reply):
    for at in range(0,len(reply),13):
        part=reply[at:at+13]
        while part:
            count=os.write(fd,part)
            part=part[count:]


def run_case(executable, mode):
    master, slave = pty.openpty()
    process = subprocess.Popen([str(executable),os.ttyname(slave),mode],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    try:
        cap=receive(master)
        assert cap[5]==0
        send(master,response(0,5,(32,48,96)))
        if mode=='invalid':
            output=process.communicate(timeout=5)[0]
            assert not select.select([master],[],[],0)[0], 'invalid descriptor sent a RUN'
        else:
            for invocation in range(2 if mode=='success' else 1):
                request=receive(master)
                identity,generation=struct.unpack_from('<QI',request,8)
                assert request[5]==1 and (identity,generation)==(invocation+1,invocation+6)
                staging=bytearray(17*128+64*64)
                for row in range(17):
                    staging[row*128:row*128+64]=bytes((row*64+k)%251 for k in range(64))
                for row in range(64):
                    staging[17*128+row*64:17*128+row*64+19]=bytes((row*3+j)%127 for j in range(19))
                assert request[32:-4]==staging
                payload=bytearray()
                for block in range(2):
                    for row in range(17):
                        for col in range(32):
                            payload+=struct.pack('<i',block*100000+row*100+col-5000 if col<19 else 0)
                if mode=='wire_padding':struct.pack_into('<i',payload,19*4,1)
                reply=response(identity,generation,(17,19,64),payload,(2021,16,4,136,256,68,68))
                if mode in ('profile','identity','count'):
                    data=bytearray(reply[:-4]);offset={'profile':6,'identity':8,'count':40}[mode];data[offset]^=1;reply=bytes(data)+struct.pack('<I',zlib.crc32(data))
                if mode=='crc':reply=reply[:-1]+bytes([reply[-1]^1])
                if mode=='length':
                    data=bytearray(reply[:96]);struct.pack_into('<I',data,20,999999);reply=bytes(data)
                if mode!='timeout':send(master,reply)
                if mode in ('success','release_duplicate'):
                    release=receive(master)
                    assert release[5]==2 and struct.unpack_from('<QI',release,8)==(identity,generation)
                    send(master,reply if mode=='release_duplicate' else response(identity,generation,(17,19,64)))
            output=process.communicate(timeout=5)[0]
        assert process.returncode==0 and f'UART_PTY PASS mode={mode}' in output,output
        return output.strip()
    finally:
        if process.poll() is None:
            process.kill();process.wait()
        os.close(master);os.close(slave)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path);args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='im2p-uart-pty-') as temporary:
        out=args.out.resolve() if args.out else Path(temporary)
        if args.out:out.mkdir(parents=True,exist_ok=False)
        source=out/'uart_pty.cpp';source.write_text(CPP);executable=out/'uart_pty'
        command=['c++','-std=c++20','-O2','-I',str(root/'sim/include'),'-I',str(Path(__file__).parent),str(source),str(Path(__file__).with_name('uart.cpp')),'-o',str(executable),'-pthread']
        subprocess.run(command,check=True)
        results=[run_case(executable,mode) for mode in ('success','invalid','crc','profile','identity','count','length','wire_padding','timeout','release_duplicate')]
        (out/'results.log').write_text('\n'.join(results)+'\n')
        print('\n'.join(results))


if __name__=='__main__':main()
