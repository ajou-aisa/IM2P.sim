#!/usr/bin/env python3
"""IFR2 PTY protocol/ownership tests; mock data is not numerical FPGA evidence."""
import argparse, contextlib, hashlib, io, json, os, pty, select, struct, subprocess, tempfile, time, zlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
CPP=r'''
#include "uart.hpp"
#include <algorithm>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <tuple>
#include <vector>
using Tuple=std::tuple<size_t,size_t,size_t,size_t>;
struct P {std::vector<Tuple> expected;size_t writes=0;};
void ck(bool v,const char *why){if(!v)throw std::runtime_error(why);}
int w(void*,size_t row,size_t col,size_t count,int8_t*out){for(size_t l=0;l<count;++l)out[l]=int8_t((row*3+col+l)%127);return 0;}
int scale(void*,size_t,size_t,size_t count,int8_t*out){std::fill_n(out,count,1);return 0;}
int out(void *p,size_t b,size_t r,size_t c,size_t count,const int64_t *v){auto&o=*static_cast<P*>(p);if(o.writes>=o.expected.size()||o.expected[o.writes++]!=Tuple(b,r,c,count))return -1;for(size_t l=0;l<count;++l)if(v[l]!=int64_t(b*100000+r*100+c+l)-5000)return -1;return 0;}
int main(int argc,char**argv){try {
 std::string mode=argv[2];size_t m=mode=="large"?321:33,n=mode=="large"?48:19,k=mode=="large"?96:64,sr=mode=="large"?160:16;
 P p;for(size_t i=0;i<m;i+=16)for(size_t j=0;j<n;j+=16)for(size_t b=0;b<k/32;++b)for(size_t r=i;r<std::min(i+16,m);++r)p.expected.emplace_back(b,r,j,std::min(size_t(16),n-j));
 std::vector<uint8_t>a(m*k);for(size_t i=0;i<a.size();++i)a[i]=i%251;
 im2p_stripe_work_desc_t d{};d.abi_version=4;d.activation_bits=d.weight_bits=8;d.activation_storage_bytes=d.weight_storage_bytes=1;d.dim=16;d.m=m;d.n=n;d.k=k;d.tile_i_rows=d.tile_j_columns=16;d.block_size=32;d.vector_op=3;d.scale_total_k=k;d.scale_row_stride=d.scale_valid_columns=d.weight_row_stride_bytes=d.output_row_stride=n;d.stripe_count=3;d.provider={&p,w,nullptr,scale,out};
 im2p::fpga::UART u(argv[1],3.0,2);im2p_work_stats_extended_t stats{};
 if(mode=="invalid"){d.m=337;ck(u.begin(&d,sr)<0,"invalid accepted");ck(u.metrics().transactions==0,"invalid transmitted");std::cout<<"IFR2_PTY PASS invalid mock_framing_only=1\n";return 0;}
 ck(u.begin(&d,sr)==0,"begin");auto fresh=u.telemetry();ck(fresh.streaming&&!fresh.completed&&!fresh.released&&!fresh.statistics&&!fresh.first_activation&&fresh.stripes.empty(),"fresh telemetry unavailable");
 auto stripe=[&](int id){im2p_activation_stripe_t s{};s.abi_version=4;s.activation_bits=s.weight_bits=8;s.activation_storage_bytes=s.weight_storage_bytes=1;s.dim=16;s.stripe_id=id;s.i_start=id*sr;s.rows=std::min(sr,m-s.i_start);s.activations=a.data()+s.i_start*k;s.activation_row_stride_bytes=k;s.context=0x12345678;return s;};
 auto s0=stripe(0),s1=stripe(1),s2=stripe(2);
 ck(u.publish(&s0)==0&&u.publish(&s1)==0,"publish first two");auto tx=u.metrics().transactions;ck(u.publish(&s2)==IM2P_BACKPRESSURE&&u.metrics().transactions==tx&&u.published_count==2,"backpressure accepted event");
 im2p_stripe_completion_extended_t c{};ck(u.poll(&c)==0,"empty poll");int r=u.poll(&c);
 if(mode!="success"&&mode!="large"){ck(r<0&&p.writes==0,"fault leaked callback");ck(u.publish(&s2)<0&&u.release()<0&&!u.error().empty(),"fault not sticky");auto partial=u.telemetry();ck(!partial.completed&&!partial.released&&!partial.statistics&&partial.publications==2&&partial.completions==0,"partial telemetry reported complete");std::cout<<"IFR2_PTY PASS "<<mode<<" mock_framing_only=1\n";return 0;}
 ck(r==1&&c.base.stripe_id==0&&c.base.context==0x12345678&&c.publish_to_completion_cycles==900,"first completion");ck(u.publish(&s2)==0&&u.published_count==3,"slot0 reuse");
 for(unsigned id=1;id<3;++id){ck(u.poll(&c)==1&&c.base.stripe_id==id&&c.base.i_start==id*sr&&c.base.context==0x12345678,"later completion");}
 ck(u.finish(&stats)==0&&p.writes==p.expected.size()&&u.last_raw.size()==m*n*(k/32),"finish/raw callbacks");ck(stats.base.completed_stripes==3&&stats.base.stripes_published==3&&stats.base.stripe_rows_published==m,"stripe counts");ck(u.last_first_activation_cycle==101&&u.last_first_activation_published_rows==sr,"observations");
 auto owned=u.telemetry();ck(owned.completed&&!owned.released&&owned.streaming&&owned.publications==3&&owned.completions==3&&owned.stripes.size()==3&&owned.statistics&&owned.statistics->elapsed_cycles==9999&&owned.first_activation&&owned.first_activation->cycle==101&&owned.first_activation->published_rows==sr,"owned telemetry");
 auto timing=[](const im2p::fpga::ExchangeObservation&t){return t.send_begin_ns&&t.send_end_ns&&t.receive_end_ns&&t.validated_ns&&t.begin_ns<=*t.send_begin_ns&&*t.send_begin_ns<=*t.send_end_ns&&*t.send_end_ns<=*t.receive_end_ns&&*t.receive_end_ns<=*t.validated_ns;};
 for(size_t id=0;id<3;++id){const auto&t=owned.stripes[id];ck(t.stripe_id==id&&t.slot==id%2&&t.row_begin==id*sr&&t.rows==std::min(sr,m-id*sr)&&t.context==0x12345678&&t.publication_cycle==100+id*100&&t.completion_cycle==1000+id*100&&t.completion&&timing(t.publication)&&timing(*t.completion),"stripe snapshot identity/time");}
 ck(u.release()==0&&u.generation()==6&&u.telemetry().released&&!owned.released,"release and owned snapshot");
 im2p_matmul_desc_t f{};f.abi_version=4;f.activation_bits=f.weight_bits=8;f.activation_storage_bytes=f.weight_storage_bytes=1;f.dim=16;f.m=m;f.n=n;f.k=k;f.tile_i_rows=f.tile_j_columns=16;f.block_size=32;f.vector_op=3;f.scale_total_k=k;f.scale_row_stride=f.scale_valid_columns=f.weight_row_stride_bytes=f.output_row_stride=n;f.activations=a.data();f.activation_row_stride_bytes=k;f.provider=d.provider;p.writes=0;
 ck(u.full(&f,&stats)==0&&p.writes==p.expected.size()&&u.release()==0&&u.generation()==7,"same process FULL");
 auto newer=u.telemetry();ck(newer.run_id==2&&newer.generation==7&&newer.completed&&newer.released&&!newer.streaming&&newer.stripes.empty()&&newer.publications==0&&newer.completions==0&&newer.full&&timing(*newer.full)&&!newer.begin&&newer.first_activation&&newer.first_activation->published_rows==m&&owned.run_id==1&&owned.generation==6&&owned.stripes.size()==3&&owned.first_activation->published_rows==sr,"retired snapshot and no stale telemetry");
 auto transactions=u.metrics().transactions;d.m=337;ck(u.begin(&d,sr)<0&&u.metrics().transactions==transactions,"invalid next invocation sent");auto invalid=u.telemetry();ck(!invalid.completed&&!invalid.released&&!invalid.statistics&&!invalid.first_activation&&invalid.stripes.empty(),"invalid invocation reused snapshot");
 std::cout<<"IFR2_PTY PASS "<<mode<<" raw="<<m*n*(k/32)<<" callbacks="<<p.writes<<" mock_framing_only=1\n";
 }catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
'''
def rd(fd,n):
    data=bytearray();end=time.monotonic()+10
    while len(data)<n:
        if not select.select([fd],[],[],max(0,end-time.monotonic()))[0]:raise TimeoutError('peer')
        data.extend(os.read(fd,min(n-len(data),7)))
    return bytes(data)
def recv(fd,op,run=1,gen=6):
    head=rd(fd,32);p=rd(fd,struct.unpack_from('<I',head,28)[0]+4);b=head+p
    assert head[:8]==b'IFR2'+bytes([2,op,16,8]),head
    assert struct.unpack_from('<QI',head,8)==(run,gen)
    assert zlib.crc32(b[:-4])==struct.unpack('<I',b[-4:])[0]
    return b

def reply(op,shape,run=1,gen=6,payload=b'',counts=(0,)*7,idrow=None,first=0,firstrows=0):
    b=bytearray(144);struct.pack_into('<4sBBHQIIHHHH',b,0,b'OFR2',2,0,0x810,run,gen,len(payload),*shape,0)
    struct.pack_into('<7Q',b,32,*counts);b[88]=op
    if idrow:
        id,row,rows=idrow;b[89]=1 if op==6 else 0;struct.pack_into('<HHHQQ',b,90,id,row,rows,100+id*100,1000+id*100 if op==6 else 0)
    struct.pack_into('<QQQQ',b,112,first,firstrows,0,0)
    b+=payload;return bytes(b)+struct.pack('<I',zlib.crc32(b))
def send(fd,b):
    for off in range(0,len(b),13):
        part=b[off:off+13]
        while part:part=part[os.write(fd,part):]
def raw(m,n,k,first,rows):
    return b''.join(struct.pack('<i',b*100000+r*100+c-5000 if c<n else 0) for b in range(k//32) for r in range(first,first+rows) for c in range(((n+15)//16)*16))
def run(exe,mode):
    m,n,k,sr=(321,48,96,160) if mode=='large' else (33,19,64,16);sh=(m,n,k)
    master,slave=pty.openpty();p=subprocess.Popen([str(exe),os.ttyname(slave),mode],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    try:
        recv(master,0,0,0);send(master,reply(0,(336,48,96),0,5))
        if mode!='invalid':
            b=recv(master,4);assert struct.unpack_from('<4H',b,20)==(m,n,k,sr)
            expected=bytearray(k*64)
            for r in range(k):expected[r*64:r*64+n]=bytes((r*3+c)%127 for c in range(n))
            assert b[32:-4]==expected;send(master,reply(4,sh))
            def publish(id):
                b=recv(master,5);row=id*sr;rows=min(sr,m-row);assert struct.unpack_from('<4H',b,20)==(row,rows,id,id%2)
                expected=bytearray(rows*128)
                for r in range(rows):expected[r*128:r*128+k]=bytes(((row+r)*k+c)%251 for c in range(k))
                assert b[32:-4]==expected
                send(master,reply(5,sh,idrow=(id,row,rows),first=101 if id else 0,firstrows=sr if id else 0))
            publish(0);publish(1)
            recv(master,6);send(master,reply(6,sh,first=101,firstrows=sr))
            recv(master,6);data=bytearray(reply(6,sh,payload=raw(m,n,k,0,sr),idrow=(0,0,sr),first=101,firstrows=sr))
            if mode not in ('success','large'):
                offsets={'crc':-1,'identity':8,'profile':6,'count':94,'generation':16,'operation':88,'flags':89,'order':90,'cycle':96,'padding':144+19*4,'length':20}
                data[offsets[mode]]^=1
                if mode!='crc':data[-4:]=struct.pack('<I',zlib.crc32(data[:-4]))
            send(master,data)
            if mode in ('success','large'):
                publish(2)
                for id in (1,2):
                    recv(master,6);r=id*sr;rows=min(sr,m-r);send(master,reply(6,sh,payload=raw(m,n,k,r,rows),idrow=(id,r,rows),first=101,firstrows=sr))
                tiles=(n+15)//16;works=((m+15)//16)*tiles;writes=m*tiles*(k//32);counts=(9999,works*(k//16),works,1,1,writes,writes)
                recv(master,7);send(master,reply(7,sh,counts=counts,first=101,firstrows=sr))
                recv(master,2);send(master,reply(2,sh,first=101,firstrows=sr))
                recv(master,1,2,7);send(master,reply(1,sh,2,7,payload=raw(m,n,k,0,m),counts=counts,first=101,firstrows=m))
                recv(master,2,2,7);send(master,reply(2,sh,2,7,first=101,firstrows=m))
        out=p.communicate(timeout=6)[0];assert p.returncode==0,out
        assert f'IFR2_PTY PASS {mode}' in out,out
        if mode=='invalid':assert not select.select([master],[],[],0)[0]
        print(out.strip(),flush=True)
    finally:
        if p.poll() is None:p.kill();p.wait()
        os.close(master);os.close(slave)
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='im2p-ifr2-pty-') as temporary:
        out = args.out.resolve() if args.out else Path(temporary)
        if args.out:
            out.mkdir(parents=True, exist_ok=False)
        source = out / 'test.cpp'
        source.write_text(CPP)
        exe = out / 'test'
        command = ['c++', '-std=c++20', '-O2', '-I'+str(ROOT/'sim/include'),
                   '-I'+str(ROOT/'fpga/dense_pipeline'), str(source),
                   str(ROOT/'fpga/dense_pipeline/uart.cpp'), '-o', str(exe), '-pthread']
        (out/'command.json').write_text(json.dumps(command, indent=2)+'\n')
        identities = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in (Path(__file__).resolve(), ROOT/'fpga/dense_pipeline/uart.cpp',
                                ROOT/'fpga/dense_pipeline/uart.hpp', ROOT/'sim/include/im2p_sim.h')}
        (out/'source-sha256.json').write_text(json.dumps(identities, indent=2)+'\n')
        subprocess.run(command, check=True)
        with (out/'results.log').open('x') as log:
            for mode in ('success','large','invalid','crc','identity','profile','count','generation',
                         'operation','flags','order','cycle','padding','length'):
                captured = io.StringIO()
                try:
                    with contextlib.redirect_stdout(captured):
                        run(exe, mode)
                finally:
                    log.write(captured.getvalue()); log.flush()
                    print(captured.getvalue(), end='', flush=True)
        assert all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == digest
                   for name, digest in identities.items()), 'source changed during test'
        (out/'status.json').write_text(json.dumps({'status':'PASS','tests':14,
            'mock_framing_only':True,'physical_device_access':False}, indent=2)+'\n')

if __name__=='__main__':
    main()
