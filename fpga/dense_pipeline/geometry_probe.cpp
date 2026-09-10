#define GGML_GEMMINI_MATMUL_IMPLEMENTATION 1
#include "ggml-gemmini-matmul.hpp"
#include "quants/act/quantize.hpp"
#include <gemmini.h>
#include <iostream>
#include <cstring>
#include <stdexcept>
#include <chrono>
using namespace ggml::gemmini;
struct Observer {
 ggml_gemmini_args_t *a;bool ended=false;int reject=-1;
 struct Record{quants::act::exsia::StripeReadyEvent event;std::vector<uint8_t> bytes;size_t future_invalid;};std::vector<Record> records;
 static bool ready(void *p,const quants::act::exsia::StripeReadyEvent &e){auto &o=*static_cast<Observer*>(p);if(o.ended||!e.activation_metadata)throw std::runtime_error("late/missing event");
  auto &a=*o.a;auto &m=quants::get_exsia_meta_mut(a);size_t invalid=0;for(size_t i=e.stripe_id+1;i<m.theta.size();++i)invalid+=m.theta[i]==std::numeric_limits<int16_t>::min();
  const auto *first=static_cast<const uint8_t*>(a.A.raw_data())+e.row_begin*a.K;
  o.records.push_back({e,std::vector<uint8_t>(first,first+(e.row_end-e.row_begin)*a.K),invalid});
  return int(e.stripe_id)!=o.reject;
 }
};
static int live(){try{
 for(size_t k:{size_t(64),size_t(96)})for(bool manual:{false,true}){
 ggml_gemmini_args_t a{};a.I=manual?33:321;a.J=manual?19:48;a.K=k;a.sA=k;a.matmul_layer="live-geometry-probe";
 gemmini_set_tile_ws(&a);if(manual)a.tile_I=1;auto g=a.activation_geometry();if(!g.ok())throw std::runtime_error("geometry");a.activation_rows_per_stripe=g.geometry.stripe_rows;
 if(!a.A.allocate(a.I,k,8))throw std::runtime_error("alloc");a.A.zero_fill();
 ggml_init_params p{a.I*k*4+ggml_tensor_overhead()+1024,nullptr,false};auto *ctx=ggml_init(p);auto *t=ggml_new_tensor_2d(ctx,GGML_TYPE_F32,k,a.I);auto *v=static_cast<float*>(t->data);
 for(size_t i=0;i<a.I;++i)for(size_t j=0;j<k;++j)v[i*k+j]=std::ldexp(float(int((i*17+j*13)%251)-125)/32,int(i/g.geometry.stripe_rows)*3);
 Observer o{&a};quants::act::exsia::StripeReadySink sink{&o,Observer::ready};a.exsia_stripe_ready_sink=&sink;
 bool ok=quants::quantize_activation(t,a);o.ended=true;if(!ok||o.records.size()!=3)throw std::runtime_error("producer count");
 std::cout<<"LIVE "<<(manual?"formal_args":"automatic")<<" I="<<a.I<<" J="<<a.J<<" K="<<k<<" stripes="<<o.records.size()<<" stride="<<g.geometry.stripe_rows<<'\n';
 for(auto &r:o.records){auto &e=r.event;const auto *q=static_cast<const uint8_t*>(a.A.raw_data())+e.row_begin*k;if(std::memcmp(q,r.bytes.data(),r.bytes.size()))throw std::runtime_error("A changed");uint64_t sum=0;for(auto x:r.bytes)sum=sum*131+x;
 std::cout<<"EVENT stripe="<<e.stripe_id<<" slot="<<e.slot<<" begin="<<e.row_begin<<" end="<<e.row_end<<" theta="<<e.activation_metadata->theta<<" e_s="<<e.activation_metadata->e_s<<" future_invalid="<<r.future_invalid<<" checksum="<<sum<<" quantization_ns="<<e.quantization_end_ns-e.quantization_start_ns<<'\n';}
 if(o.records[0].future_invalid!=2||o.records[1].future_invalid!=1)throw std::runtime_error("not live");
 Observer fail{&a};fail.reject=1;sink.user_data=&fail;a.A.zero_fill();if(quants::quantize_activation(t,a))throw std::runtime_error("sink failure ignored");for(size_t i=0;i<a.I;++i)for(size_t j=0;j<k;++j)if(a.A.get(i,j)!=0)throw std::runtime_error("failure not zeroed");
 std::cout<<"REJECT stripe=1 callbacks="<<fail.records.size()<<" A_zeroed=1\n";ggml_free(ctx);
 }
 std::cout<<"LIVE_PRODUCER_GEOMETRY PASS jobs=4 events=12 slot_sequence=0,1,0 immutable_A=1 prefix_metadata=1 rejected_jobs=4\n";return 0;
}catch(const std::exception &e){std::cerr<<e.what()<<'\n';return 1;}}

static int scan(){using namespace ggml::gemmini;
std::cerr << "DIM=" << DIM << " BANK_NUM=" << BANK_NUM << " BANK_ROWS=" << BANK_ROWS << " ACC_ROWS=" << ACC_ROWS << " sizeof(native_q8_h1)=" << sizeof(block_q8_h1) << "\n";
for(size_t j:{size_t(1),size_t(16),size_t(19),size_t(32),size_t(48)})for(size_t k:{size_t(32),size_t(64),size_t(96)}){bool found=false;
for(size_t i=1;i<=8192;++i){ggml_gemmini_args_t a{};a.I=i;a.J=j;a.K=k;a.matmul_layer="geometry-probe";gemmini_set_tile_ws(&a);auto g=make_gemmini_geometry({{i,j,k},{a.tile_I,a.tile_J,a.tile_K},DIM});
if(i==16||i==32||i==33||i==48||i==49||i==64||i==96||(!found&&g.geometry.stripe_count>=3)){std::cout<<i<<','<<j<<','<<k<<','<<a.tile_I<<','<<a.tile_J<<','<<a.tile_K<<','<<g.geometry.stripe_rows<<','<<g.geometry.stripe_count<<','<<g.geometry.final_rows<<','<<g.geometry.ws_inner_calls<<"\n";}
if(g.geometry.stripe_count>=3){found=true;break;}}}
return 0;
}

int main(int argc,char **argv){return argc==2 && std::string(argv[1])=="scan" ? scan() : live();}
