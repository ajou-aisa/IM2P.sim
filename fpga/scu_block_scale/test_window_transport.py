#!/usr/bin/env python3
"""Native IFR4 codec/ownership tests against a PTY mock, never RTL numerical proof."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import tempfile
import zlib

import window_packets as wire
from test_uart import rd, send
from test_window_packets import layout_test

ROOT = Path(__file__).resolve().parents[2]
CPP = r'''
#include "window_uart.hpp"
#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
void ck(bool b,const char *s){if(!b)throw std::runtime_error(s);}
int64_t value(size_t b,size_t r,size_t c){return c==0?INT32_MIN:c==1?INT32_MAX:int64_t(r*100+b*10+c)-1000;}
struct State {size_t m=1,writes=0,planes=2;uint32_t op=3,domain=1;std::string fault;};
int weight(void*,size_t r,size_t c,size_t n,int8_t*out){ck(r<64&&c+n<=17,"weight extent");for(size_t i=0;i<n;++i)out[i]=int((r*3+(c+i)*5)%127)-63;return 0;}
int scale(void*p,size_t b,size_t c,size_t n,uint32_t*out){auto&s=*static_cast<State*>(p);ck(b<2&&c+n<=17,"scale extent");const std::array<uint32_t,6>h1{0,1,65535,65536,65789,65790},hp1{0,1,30,31,32767,0x80000000u};for(size_t i=0;i<n;++i)out[i]=s.op==4?h1[(b*17+c+i)%6]:s.op==5?hp1[(b*17+c+i)%6]:b*101+c+i;if(s.fault=="invalid_carrier")out[0]=s.op==4?65791:32768;if(s.fault=="negative_carrier")out[0]=0xffffffffu;return 0;}
int output(void *p,size_t b,size_t r,size_t c,size_t n,const int64_t*v,uint32_t domain){auto&s=*static_cast<State*>(p);++s.writes;ck(r<s.m&&b<s.planes&&c+n<=17&&domain==s.domain,"callback outside output");for(size_t i=0;i<n;++i)ck(v[i]==value(b,r,c+i),"MOCK value decode");return 0;}
int main(int argc,char**argv){try{
 ck(argc==3,"args");std::string label=argv[2],mode=label;State s;if(mode.starts_with("h1_")||mode.starts_with("hp1_")){s.op=mode.starts_with("h1_")?4:5;s.domain=2;s.planes=1;mode=mode.substr(mode.find('_')+1);}s.fault=mode;auto api=im2p::fpga::window_uart_api();void*d=nullptr;
 try{d=im2p::fpga::open_window_uart(argv[1],2);}catch(const std::exception&e){if(mode.starts_with("cap_")){std::cout<<"MOCK_PASS "<<mode<<" rejected="<<e.what()<<"\n";return 0;}throw;}
 std::array<int8_t,320>a{};for(size_t i=0;i<a.size();++i)a[i]=int(i%101)-50;
 im2p_matmul_desc_t f{};f.abi_version=IM2P_ABI_VERSION;f.activation_bits=f.weight_bits=8;f.activation_storage_bytes=f.weight_storage_bytes=1;f.dim=16;f.m=1;f.n=17;f.k=64;f.tile_i_rows=1;f.tile_j_columns=16;f.activations=a.data();f.activation_row_stride_bytes=64;f.weight_row_stride_bytes=f.output_row_stride=f.scale_row_stride=f.scale_valid_columns=17;f.block_size=32;f.scale_total_k=64;f.vector_op=IM2P_VECTOR_EXTERNAL;f.output_domain=IM2P_OUTPUT_LEGACY_BLOCK;f.provider={&s,weight,nullptr,scale,output};
 f.vector_op=s.op;f.output_domain=s.domain;
 if(mode=="descriptor_domain")f.output_domain=s.domain==2?1:2;
 if(mode=="descriptor_op")f.vector_op=6;
 if(mode=="descriptor_block")f.block_size=16;
 if(mode=="descriptor_k")f.k=63;
 if(mode=="descriptor_stride")f.weight_row_stride_bytes=16;
 if(mode=="missing_scale")f.provider.read_scale=nullptr;
 if(mode=="missing_weight")f.provider.read_weight_i8=nullptr;
 if(mode=="missing_output")f.provider.write_output=nullptr;
 if(mode=="bypass_scale"){f.vector_op=IM2P_VECTOR_BYPASS;f.output_domain=IM2P_OUTPUT_LEGACY_FINAL;f.block_size=1;f.provider.read_scale=nullptr;}
 im2p_work_stats_extended_t stats{};int status=api->full(d,&f,&stats);
 if(mode!="success"){
  ck(status<0,"fault accepted");ck(!std::string(api->error(d)).empty(),"failure detail missing");
  ck(api->release(d)<0&&api->full(d,&f,&stats)<0,"failure not sticky");
  if(mode=="extra_output"||mode=="completion_count")ck(s.writes==2*s.planes,"extra output reached callback");
  else if(mode=="duplicate_tag"||mode=="skip_tag")ck(s.writes==1,"bad tag reached callback");
  else if(mode=="output_padding")ck(s.writes==s.planes,"bad padding reached callback");
  else ck(s.writes==0,"fault leaked callback");
  std::cout<<"MOCK_PASS "<<label<<" raw_callbacks="<<s.writes<<" rejected="<<api->error(d)<<"\n";api->destroy(d);return 0;
 }
 ck(status==0&&s.writes==2*s.planes&&api->release(d)==0,"FULL/release");
 s.writes=0;ck(api->full(d,&f,&stats)==0&&s.writes==2*s.planes&&api->release(d)==0,"repeated FULL");
 im2p_stripe_work_desc_t w{};
#define COPY(x) w.x=f.x
 COPY(abi_version);COPY(activation_bits);COPY(weight_bits);COPY(activation_storage_bytes);COPY(weight_storage_bytes);COPY(dim);COPY(n);COPY(k);COPY(tile_i_rows);COPY(tile_j_columns);COPY(weight_row_stride_bytes);COPY(output_row_stride);COPY(scale_row_stride);COPY(scale_valid_columns);COPY(block_size);COPY(scale_total_k);COPY(vector_op);COPY(output_domain);COPY(provider);
#undef COPY
 w.m=s.m=5;w.tile_i_rows=5;w.stripe_count=5;s.writes=0;ck(api->begin(d,&w,1)==0,"begin");
 for(unsigned id=0;id<5;++id){im2p_activation_stripe_t stripe{};stripe.abi_version=IM2P_ABI_VERSION;stripe.activation_bits=stripe.weight_bits=8;stripe.activation_storage_bytes=stripe.weight_storage_bytes=1;stripe.dim=16;stripe.stripe_id=id;stripe.i_start=id;stripe.rows=1;stripe.activations=a.data()+64*id;stripe.activation_row_stride_bytes=64;stripe.context=0x987000+id;
  ck(api->publish(d,&stripe)==0,"publish");
  if(id<4){auto next=stripe;next.stripe_id++;next.i_start++;next.activations=a.data()+64*(id+1);ck(api->publish(d,&next)==IM2P_BACKPRESSURE,"pending A backing not retained");}
  im2p_stripe_completion_extended_t completed{};int got=0;for(unsigned polls=0;!got&&polls<12;++polls)got=api->poll(d,&completed);
  ck(got==1&&completed.base.stripe_id==id&&completed.base.i_start==id&&completed.base.rows==1&&completed.base.context==stripe.context,"semantic completion");
 }
 ck(api->finish(d,&stats)==0&&s.writes==10*s.planes&&stats.base.completed_stripes==5&&stats.base.stripe_rows_published==5,"live coverage");
 ck(api->release(d)==0,"live release");api->destroy(d);std::cout<<"MOCK_PASS "<<label<<" FULL=2 live_stripes=5 op="<<s.op<<" host_descriptor_domain="<<s.domain<<" planes="<<s.planes<<"\n";
 }catch(const std::exception&e){std::cerr<<"MOCK_FAIL "<<e.what()<<'\n';return 1;}}
'''


def response(request, payload=b'', flags=0):
    _, version, op, _, _, run, generation, sequence, _, *_ = wire.HEADER.unpack_from(request)
    body = wire.HEADER.pack(b'OFR4',version,op,0,flags,run,generation,sequence,len(payload),0x08100420,0,0,0,0)+payload
    return body+struct.pack('<I',zlib.crc32(body))


def raw_value(block,row,column):
    return -(1<<31) if column==0 else (1<<31)-1 if column==1 else row*100+block*10+column-1000


def mutate(data, mode):
    data=bytearray(data)
    offsets={'cap_version':4,'cap_profile':28,'version':4,'operation':5,'sequence':20,
             'generation':16,'run':8,'profile':28,'padding':32,'flags':7,'crc':-1}
    if mode in offsets:
        data[offsets[mode]] ^= 0x80 if mode=='flags' else 1
    if mode!='crc': data[-4:]=struct.pack('<I',zlib.crc32(data[:-4]))
    return bytes(data)


def run(exe,mode):
    label=mode
    contract,mode=mode.split('_',1) if mode.startswith(('h1_','hp1_')) else ('external',mode)
    vector_op={'external':3,'h1':4,'hp1':5}[contract]
    planes=2 if contract=='external' else 1
    master,slave=pty.openpty()
    process=subprocess.Popen([str(exe),os.ttyname(slave),label],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    state={'run':0,'gen':0,'seq':0,'m':1,'row':0,'live':False,'published':0,'stage':'refill','refills':0,'batch':1,'outputs':0,'tag':(1<<32)-2}
    transactions=[]
    try:
        while process.poll() is None:
            if not select.select([master],[],[],0.05)[0]: continue
            head=rd(master,wire.HEADER_BYTES)
            fields=wire.HEADER.unpack(head)
            magic,version,op,status,flags,run_id,generation,sequence,length,*args=fields
            payload=rd(master,length) if length else b''
            checksum=rd(master,4);request=head+payload+checksum
            assert request==wire.packet(op,run_id,generation,sequence,args,payload,flags)
            assert magic==b'IFR4' and version==4 and status==0
            transactions.append(dict(op=op,run=run_id,generation=generation,sequence=sequence,args=args,payload_bytes=length))
            if op==wire.CAP:
                assert (run_id,generation,sequence,flags,length,*args)==(0,)*10
                reply=response(request)
                if mode.startswith('cap_'): reply=mutate(reply,mode)
            elif op==wire.START:
                assert run_id==state['run']+1 and generation==state['gen']+1 and sequence==1
                assert args[1:3]==[17,64] and args[4]==0
                assert args[3]==6|(5<<5)|(6<<10)|(6<<13)|((0 if mode=='bypass_scale' else vector_op)<<16)
                state.update(run=run_id,gen=generation,seq=sequence,m=args[0],row=0,live=bool(flags),published=0 if flags else args[0],stage='refill' if not flags else 'waiting',refills=0,batch=1,outputs=0)
                reply=response(request)
            else:
                assert (run_id,generation,sequence,flags)==(state['run'],state['gen'],state['seq']+1,0)
                state['seq']=sequence
                if op==wire.PUBLISH:
                    assert args==[state['row'],1,state['row'],state['row']%2,0]
                    state.update(published=state['row']+1,stage='refill',refills=0)
                    reply=response(request)
                elif op==wire.REFILL:
                    kind,gen,first,words,final=args
                    assert first==0 and final==1 and words==(64,256,32)[kind] and gen==state['row']+1
                    expected=bytearray()
                    for index in range(words):
                        per_row=(4,4,16)[kind];row=index//per_row;column=index%per_row*(4 if kind==2 else 16)
                        if kind==2:
                            carriers={'h1':(0,1,65535,65536,65789,65790),'hp1':(0,1,30,31,32767,0x80000000)}
                            expected+=struct.pack('<4I',*[(row*101+column+i if contract=='external' else carriers[contract][(row*17+column+i)%6]) if row<2 and column+i<17 else 0 for i in range(4)])
                        else:
                            values=[]
                            for lane in range(16):
                                c=column+lane
                                v=((row*64+c)%101)-50 if kind==0 and row==state['row'] else (row*3+c*5)%127-63 if kind==1 and c<17 else 0
                                values.append(v)
                            expected+=struct.pack('<16b',*values)
                    assert payload==expected,(kind,len(payload),len(expected))
                    state['refills']+=1
                    if state['refills']==3:state['stage']='output'
                    reply=response(request)
                elif op==wire.POLL:
                    assert length==0 and args==[0]*5
                    data=bytearray(wire.POLL_BYTES); records=b'';reply_flags=0
                    writes=state['outputs']
                    struct.pack_into('<7Q',data,0,100+len(transactions),writes*2,writes,1,1,writes,writes)
                    if mode=='completion_count' and state['stage']=='done':struct.pack_into('<Q',data,48,writes+1)
                    struct.pack_into('<II',data,56,state['published'],state['row'])
                    struct.pack_into('<II',data,168,state['batch'],0)
                    stage=state['stage']
                    if stage=='refill':
                        reply_flags=7
                        for kind in range(3):
                            struct.pack_into('<6I',data,64+24*kind,0,0,(state['published'],64,2)[kind],state['row']+1,(64,256,32)[kind],0)
                        if mode=='window_origin':struct.pack_into('<I',data,88,64)
                        if mode=='window_alignment':struct.pack_into('<I',data,92,1)
                        if mode=='window_generation':struct.pack_into('<I',data,76,0)
                        if mode=='scale_origin':struct.pack_into('<I',data,112,2)
                    elif stage=='output':
                        reply_flags=8|(16 if state['live'] else 32)
                        for column in (0,16):
                            for block in range(planes):
                                count=min(16,17-column);address=block*state['m']*128+state['row']*128+column*4
                                tag=state['gen']<<32|(state['tag']&0xffffffff);state['tag']+=1
                                values=[raw_value(block,state['row'],column+i) if i<count else 0 for i in range(16)]
                                records+=wire.RECORD.pack(address,tag,count,0,*values)
                        if mode=='extra_output':records+=wire.RECORD.pack(state['m']*128,state['gen']<<32|(state['tag']&0xffffffff),16,0,*[raw_value(0,state['m'],i) for i in range(16)])
                        records=bytearray(records)
                        if mode=='output_address':struct.pack_into('<Q',records,0,4)
                        if mode=='output_tag':records[12]^=1
                        if mode=='duplicate_tag':records[wire.RECORD_BYTES+8:wire.RECORD_BYTES+16]=records[8:16]
                        if mode=='skip_tag':records[wire.RECORD_BYTES+8]^=2
                        if mode=='output_padding':struct.pack_into('<i',records,planes*wire.RECORD_BYTES+28,1)
                        if mode=='coverage':records=b'';reply_flags=32
                        struct.pack_into('<I',data,172,len(records)//wire.RECORD_BYTES)
                        if mode=='batch_identity':struct.pack_into('<I',data,168,state['batch']+1)
                    elif stage=='stripe':
                        reply_flags=16;struct.pack_into('<III',data,136,state['row'],state['row'],1);struct.pack_into('<QQ',data,152,1,99)
                    elif stage=='done':reply_flags=32
                    reply=response(request,bytes(data)+records,reply_flags)
                    if mode in ('version','operation','sequence','generation','run','profile','padding','flags','crc'):reply=mutate(reply,mode)
                elif op==wire.OUTPUT_ACK:
                    assert args==[state['batch'],2*planes,0,0,0]
                    state['outputs']+=2*planes;state['batch']+=1;state['stage']='stripe' if state['live'] else 'done'
                    reply=response(request)
                elif op==wire.STRIPE_ACK:
                    assert args==[state['row'],state['row'],1,0,0]
                    state['row']+=1;state['stage']='done' if state['row']==state['m'] else 'waiting'
                    reply=response(request)
                elif op==wire.RELEASE:
                    assert state['stage']=='done';reply=response(request)
                else:raise AssertionError(('unexpected operation',op))
            send(master,reply)
        output=process.communicate(timeout=3)[0]
        assert process.returncode==0 and f'MOCK_PASS {label}' in output,output
        if mode.startswith(('descriptor_','missing_')):assert [t['op'] for t in transactions]==[wire.CAP]
        assert not select.select([master],[],[],0)[0],'unexpected packet after completion/failure'
        return dict(mode=label,status='PASS',output=output.strip(),transactions=transactions)
    finally:
        if process.poll() is None:process.kill();process.wait()
        os.close(master);os.close(slave)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--mode',action='append')
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=False);layout_test()
    source=args.out/'client.cpp';source.write_text(CPP);exe=args.out/'client'
    command=['c++','-std=c++20','-O2','-Wall','-Wextra','-Werror','-I'+str(ROOT/'sim/include'),'-I'+str(ROOT/'fpga/scu_block_scale'),str(source),str(ROOT/'fpga/scu_block_scale/window_uart.cpp'),'-o',str(exe)]
    result=dict(status='RUNNING',classification='MOCK_CODEC_OWNERSHIP',RTL_numerical='NOT_RUN',physical_access_count=0,command=command,cases=[])
    paths=[Path(__file__),ROOT/'fpga/scu_block_scale/window_uart.cpp',ROOT/'fpga/scu_block_scale/window_uart.hpp',ROOT/'fpga/scu_block_scale/rtl_plugin.hpp',ROOT/'fpga/scu_block_scale/window_protocol.hpp',ROOT/'fpga/scu_block_scale/window_packets.py']
    result['source_sha256']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    try:
        build=subprocess.run(command,capture_output=True);(args.out/'compile.stdout').write_bytes(build.stdout);(args.out/'compile.stderr').write_bytes(build.stderr)
        assert build.returncode==0,build.stderr.decode()
        legacy_modes=('success','cap_version','cap_profile','version','operation','sequence','generation','run','profile','padding','flags','crc','output_address','output_tag','duplicate_tag','skip_tag','output_padding','coverage','extra_output','window_origin','window_alignment','window_generation','scale_origin','bypass_scale','batch_identity','completion_count')
        scu_modes=('success','descriptor_domain','descriptor_op','descriptor_block','descriptor_k','descriptor_stride','missing_scale','missing_weight','missing_output','invalid_carrier','negative_carrier','output_address','output_tag','duplicate_tag','skip_tag','output_padding','coverage','extra_output','completion_count','scale_origin','batch_identity')
        for mode in args.mode or (*legacy_modes,*(f'{contract}_{fault}' for contract in ('h1','hp1') for fault in scu_modes)):
            case=run(exe,mode);result['cases'].append(case);print(case['output'],flush=True)
            (args.out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
        result['status']='PASS'
    except Exception as error:
        result.update(status='FAIL',first_error=str(error));raise
    finally:(args.out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':main()
