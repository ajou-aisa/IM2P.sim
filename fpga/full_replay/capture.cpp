#define GGML_GEMMINI_MATMUL_IMPLEMENTATION 1
#ifndef IM2P_FULL_REPLAY_MAX_ROWS
#define IM2P_FULL_REPLAY_MAX_ROWS 32
#endif
#include "ggml-gemmini-matmul.hpp"
#include "ggml-quants.h"
#include "quants/act/quantize.hpp"
#include "im2p_gemmini_frontend.hpp"
#include <gemmini.h>
#include <bit>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <cstring>
#include <atomic>

static std::atomic<unsigned> simulator_creates{0}, simulator_executes{0};
extern "C" im2p_sim_t *__real_im2p_sim_create();
extern "C" int __real_im2p_execute_matmul_extended(im2p_sim_t *,const im2p_matmul_desc_t *,im2p_work_stats_extended_t *);
extern "C" im2p_sim_t *__wrap_im2p_sim_create() { ++simulator_creates; return __real_im2p_sim_create(); }
extern "C" int __wrap_im2p_execute_matmul_extended(im2p_sim_t *s,const im2p_matmul_desc_t *d,im2p_work_stats_extended_t *t) {
    ++simulator_executes; return __real_im2p_execute_matmul_extended(s,d,t);
}

namespace fs = std::filesystem;
using namespace ggml::gemmini;
using Bytes = std::vector<uint8_t>;
static void require(bool value, const char *message) { if (!value) throw std::runtime_error(message); }
static void put(Bytes &b, uint64_t x, size_t count) {
    for (size_t i=0; i<count; ++i) b.push_back(uint8_t(x >> (8*i)));
}
static uint64_t get(const Bytes &b, size_t &at, size_t count) {
    require(at <= b.size() && count <= b.size()-at, "truncated fixture");
    uint64_t x=0; for (size_t i=0;i<count;++i) x |= uint64_t(b[at++]) << (8*i); return x;
}
static Bytes read(const fs::path &path) {
    std::ifstream f(path,std::ios::binary); require(bool(f),"missing artifact");
    return Bytes(std::istreambuf_iterator<char>(f),{});
}
static void write(const fs::path &path,const Bytes &bytes) {
    std::ofstream f(path,std::ios::binary); require(bool(f),"cannot write artifact");
    f.write(reinterpret_cast<const char *>(bytes.data()),bytes.size()); require(bool(f),"artifact write failed");
}
static void write_floats(const fs::path &path,const std::vector<float> &v) {
    Bytes b; for (auto x:v) put(b,std::bit_cast<uint32_t>(x),4); write(path,b);
}

struct Fixture {
    ggml_gemmini_args_t args{};
    std::vector<block_q8_h1> weights;
    std::vector<float> output;
    uint64_t identity=0;
    void bind() {
        args.weight_format=ggml_gemmini_args_t::im2p_weight_format_t::q8_h1;
        args.q8_h1_blocks=weights.data(); args.q8_h1_block_count=weights.size();
        args.q8_h1_rows=args.J; args.blocks_per_row=args.K/32;
        args.native_weight_bytes=weights.size()*sizeof(block_q8_h1);
        args.f_out=output.data(); args.sA=args.K; args.sB=args.K;
        args.tiled_matmul_type=CPU;
        args.matmul_layer="full-fpga-replay-dense-rmd-off";
    }
    void capture(const fs::path &dir) {
        auto &meta=std::get<quants::act::exsia::Meta>(args.act_quant.storage());
        require(meta.rmd_packets.empty() && meta.direct_residuals.empty(),"residual capture unsupported");
        Bytes b; put(b,0x31584649,4); // IFX1
        for (auto x:{args.I,args.J,args.K,args.tile_I,args.tile_J,args.tile_K,
             args.activation_rows_per_stripe,args.activation_row_offset,args.A.row_stride_bytes,
             args.stride_f_out,args.col_stride_f_out,size_t(identity),meta.theta.size()}) put(b,x,8);
        put(b,uint16_t(meta.e_s),2); put(b,uint16_t(meta.rho),2); put(b,uint32_t(meta.sigma),4);
        for (auto x:meta.theta) put(b,uint16_t(x),2);
        const auto *a=static_cast<const uint8_t *>(args.A.raw_data());
        b.insert(b.end(),a,a+args.I*args.A.row_stride_bytes);
        for (const auto &w:weights) {
            for (auto q:w.qs) put(b,uint8_t(q),1);
            put(b,w.c_b,1); put(b,std::bit_cast<uint32_t>(w.s_rf),4); put(b,w.R,2);
        }
        write(dir/"fixture.bin",b);
        const auto g=args.activation_geometry(); require(g.ok(),"host geometry failed");
        std::ofstream manifest(dir/"manifest.json");
        manifest << "{\n\"schema\":1,\"host_commit\":\"7a74ac29c9c8fa8fbc5d47cf073b8b9266e3e7a9\","
          "\"profile\":\"A8/W8/D16\",\"route\":\"q8_h1\",\"residual\":\"OFF (dense ablation)\","
          "\"mode\":\"FULL\",\"block_size_k\":32,\"comparison\":\"exact bits, padding unchanged\",\n"
          "\"I\":" << args.I << ",\"J\":" << args.J << ",\"K\":" << args.K << ",\"run_id\":" << identity
          << ",\"tile_factors\":[" << args.tile_I << ',' << args.tile_J << ',' << args.tile_K
          << "],\"outer_counts\":[" << g.geometry.outer.i << ',' << g.geometry.outer.j << ',' << g.geometry.outer.k
          << "],\"ws_inner_calls\":" << g.geometry.ws_inner_calls << ",\"logical_invocations\":1,"
          "\"output_stride\":" << args.stride_f_out << ",\"output_column_stride\":" << args.col_stride_f_out
          << ",\"activation_rows_per_stripe\":" << args.activation_rows_per_stripe << "}\n";
    }
    void restore(const fs::path &dir) {
        const auto b=read(dir/"fixture.bin"); size_t p=0;
        require(get(b,p,4)==0x31584649,"fixture version");
        size_t v[13]; for (auto &x:v) x=get(b,p,8);
        require(v[0]>0 && v[0]<=IM2P_FULL_REPLAY_MAX_ROWS && v[1]>0 && v[1]<=48 && v[2]>0 && v[2]<=96 && v[2]%32==0,"fixture capacity/alignment");
        args.I=v[0]; args.J=v[1]; args.K=v[2]; args.tile_I=v[3]; args.tile_J=v[4]; args.tile_K=v[5];
        args.activation_rows_per_stripe=v[6]; args.activation_row_offset=v[7];
        require(v[7]==0 && v[8]==args.K && v[9]<=128 && v[10]>0 && v[10]<=2 && v[9]>=args.J*v[10],"fixture layout");
        args.stride_f_out=v[9]; args.col_stride_f_out=v[10]; identity=v[11];
        require(v[12]>0 && v[12]<=32,"metadata capacity");
        auto &meta=args.act_quant.storage().emplace<quants::act::exsia::Meta>();
        meta.e_s=int16_t(get(b,p,2)); meta.rho=int16_t(get(b,p,2)); meta.sigma=int32_t(get(b,p,4));
        meta.run_id=identity; for(size_t i=0;i<v[12];++i) meta.theta.push_back(int16_t(get(b,p,2)));
        require(args.A.allocate(args.I,args.K,8),"activation allocation");
        auto *a=args.A.bytes->data();
        for(size_t i=0;i<args.I*args.K;++i) a[i]=get(b,p,1);
        weights.resize(args.J*args.K/32);
        for(auto &w:weights) {
            for(auto &q:w.qs) q=int8_t(get(b,p,1)); w.c_b=get(b,p,1);
            w.s_rf=std::bit_cast<float>(uint32_t(get(b,p,4))); w.R=get(b,p,2);
        }
        require(p==b.size(),"trailing fixture bytes"); output.assign(args.I*args.stride_f_out,17.0f); bind();
    }
};

struct Execution {
    fs::path dir;
    bool board=false;
    im2p_provider_t downstream{};
    Bytes raw;
    size_t m=0,n=0,k=0;
    uint64_t identity=0;
    static int output(void *context,size_t block,size_t row,size_t col,size_t count,const int64_t *values) {
        auto &x=*static_cast<Execution *>(context);
        for(size_t lane=0;lane<count;++lane) {
            const size_t offset=((block*x.m+row)*x.n+col+lane)*4;
            if(offset+4>x.raw.size() || values[lane]<INT32_MIN || values[lane]>INT32_MAX) return IM2P_ERROR;
            for(size_t byte=0;byte<4;++byte) x.raw[offset+byte]=uint32_t(values[lane])>>(byte*8);
        }
        return x.downstream.write_output(x.downstream.context,block,row,col,count,values);
    }
    static int weight(void *context,size_t row,size_t col,size_t count,int8_t *values) {
        auto &x=*static_cast<Execution *>(context);
        return x.downstream.read_weight_i8(x.downstream.context,row,col,count,values);
    }
    static int scale(void *context,size_t row,size_t col,size_t count,int8_t *values) {
        auto &x=*static_cast<Execution *>(context);
        return x.downstream.read_scale(x.downstream.context,row,col,count,values);
    }
    static int execute(void *context,const im2p_matmul_desc_t *d,im2p_work_stats_extended_t *stats) noexcept {
      try {
        auto &x=*static_cast<Execution *>(context);
        require(d->abi_version==4 && d->activation_bits==8 && d->weight_bits==8 && d->dim==16 &&
          d->activation_storage_bytes==1 && d->weight_storage_bytes==1 && d->vector_op==IM2P_VECTOR_EXTERNAL &&
          d->m>0 && d->m<=IM2P_FULL_REPLAY_MAX_ROWS && d->n>0 && d->n<=48 && d->k>0 && d->k<=96 && d->k%32==0 && d->block_size==32 &&
          d->tile_i_rows==std::min(size_t(16),d->m) && d->tile_j_columns==std::min(size_t(16),d->n) &&
          d->provider.read_weight_i8 && d->provider.write_output,"FPGA replay unsupported descriptor");
        x.m=d->m; x.n=d->n; x.k=d->k; x.downstream=d->provider;
        x.raw.assign(x.m*x.n*(x.k/32)*4,0);
        Bytes staging(x.m*128+x.k*64,0);
        for(size_t i=0;i<x.m;++i) std::memcpy(staging.data()+i*128,
          static_cast<const uint8_t *>(d->activations)+i*d->activation_row_stride_bytes,x.k);
        for(size_t row=0;row<x.k;++row) for(size_t col=0;col<x.n;col+=16)
          require(d->provider.read_weight_i8(d->provider.context,row,col,std::min(size_t(16),x.n-col),
            reinterpret_cast<int8_t *>(staging.data()+x.m*128+row*64+col))==IM2P_OK,"weight provider failed");
        if(!x.board) {
          write(x.dir/"staging.bin",staging);
          im2p_matmul_desc_t desc=*d;
          desc.provider={&x,weight,nullptr,scale,output};
          im2p_sim_t *sim=im2p_sim_create(); require(sim!=nullptr,"simulator create");
          int result=im2p_execute_matmul_extended(sim,&desc,stats); im2p_sim_destroy(sim);
          require(result==IM2P_OK,"simulator execution");
        } else {
          require(staging==read(x.dir/"staging.bin"),"replayed host inputs differ from transported inputs");
          // decoded.bin is produced only by protocol.py after CRC, identity,
          // profile, shape, counts and exact packet-length validation.
          auto result=read(x.dir/"decoded.bin"); size_t p=0;
          require(get(result,p,8)==x.identity,"decoded run identity");
          stats->base.work_total_cycles=get(result,p,8);
          stats->base.completed_fragments=get(result,p,8); stats->base.completed_output_tiles=get(result,p,8);
          stats->base.activation_read_requests=get(result,p,8); stats->base.weight_read_requests=get(result,p,8);
          stats->base.output_write_requests=get(result,p,8); stats->base.output_write_responses=get(result,p,8);
          const size_t t=(x.n+15)/16;
          require(result.size()==p+(x.k/32)*x.m*t*64,"decoded output length");
          for(size_t i=0;i<x.m;i+=16) for(size_t j=0;j<x.n;j+=16)
            for(size_t block=0;block<x.k/32;++block) for(size_t row=i;row<std::min(i+16,x.m);++row) {
              int64_t lanes[16]; size_t at=p+((block*x.m+row)*t+j/16)*64;
              for(size_t l=0;l<std::min(size_t(16),x.n-j);++l) lanes[l]=int32_t(get(result,at,4));
              require(output(&x,block,row,j,std::min(size_t(16),x.n-j),lanes)==IM2P_OK,"host reconstruction");
            }
        }
        require(x.raw==read(x.dir/"expected-raw.bin"),"raw reference mismatch");
        write(x.dir/(x.board?"r2-raw.bin":"r1-raw.bin"),x.raw);
        std::ofstream telemetry(x.dir/(x.board?"r2-stats.json":"r1-stats.json"));
        auto &s=stats->base;
        telemetry << "{\"cycles\":"<<s.work_total_cycles<<",\"fragments\":"<<s.completed_fragments
          <<",\"works\":"<<s.completed_output_tiles<<",\"A_requests\":"<<s.activation_read_requests
          <<",\"W_requests\":"<<s.weight_read_requests<<",\"output_writes\":"<<s.output_write_requests
          <<",\"output_acks\":"<<s.output_write_responses<<"}\n";
        return IM2P_OK;
      } catch(const std::exception &e) { std::cerr<<e.what()<<'\n'; return IM2P_ERROR; }
    }
};

int main(int argc,char **argv) {
 try {
    require(argc==3 || argc==7,"usage: full_replay capture DIR M N K SEED | replay DIR");
    fs::path dir=argv[2]; Fixture f;
    if(std::string(argv[1])=="capture") {
      require(argc==7,"capture arguments"); fs::create_directories(dir);
      auto &a=f.args; a.I=std::stoul(argv[3]); a.J=std::stoul(argv[4]); a.K=std::stoul(argv[5]); f.identity=std::stoull(argv[6]);
      require(a.I>0 && a.I<=IM2P_FULL_REPLAY_MAX_ROWS && a.J>0 && a.J<=48 && a.K>0 && a.K<=96 && a.K%32==0,"native capacity/alignment");
      std::vector<float> av(a.I*a.K),wv(a.J*a.K);
      for(size_t i=0;i<av.size();++i) av[i]=float(int((i*17+f.identity*13)%251)-125)/32;
      if(a.I>32) {
        // Automatic host geometry also defines this fixture's scale boundaries.
        gemmini_set_tile_ws(&a);
        const auto stripe_geometry=a.activation_geometry();
        require(stripe_geometry.ok() && stripe_geometry.geometry.stripe_count==3,
                "long fixture requires automatic three-stripe geometry");
        for(size_t row=0;row<a.I;++row) for(size_t col=0;col<a.K;++col)
          av[row*a.K+col]=std::ldexp(av[row*a.K+col],
              int(row/stripe_geometry.geometry.stripe_rows)*3);
      }
      for(size_t j=0;j<a.J;++j) for(size_t k=0;k<a.K;++k)
        wv[j*a.K+k]=float(int((j*11+k*7+f.identity*5)%253)-126)*float(1+(k/32)*3)/128;
      // ggml initializes the FP16 lookup tables used by the native quantizer.
      ggml_init_params params{av.size()*4+ggml_tensor_overhead()+1024,nullptr,false};
      auto *ctx=ggml_init(params); require(ctx!=nullptr,"ggml context");
      f.weights.resize(a.J*a.K/32);
      for(size_t j=0;j<a.J;++j) quantize_row_q8_h1_ref(wv.data()+j*a.K,f.weights.data()+j*a.K/32,a.K);
      require(f.weights[0].s_rf>0 && f.weights[0].c_b+f.weights[0].R>0,"host weight scales initialized");
      if(a.K>32) require(f.weights[0].s_rf*(f.weights[0].c_b+f.weights[0].R)!=
          f.weights[1].s_rf*(f.weights[1].c_b+f.weights[1].R),"fixture crosses distinct weight scales");
      a.col_stride_f_out=(a.I==17?2:1); a.stride_f_out=a.J*a.col_stride_f_out+3;
      f.output.assign(a.I*a.stride_f_out,17.0f); f.bind();
      gemmini_set_tile_ws(&a);
      auto geometry=a.activation_geometry(); require(geometry.ok(),"tile geometry");
      a.activation_rows_per_stripe=geometry.geometry.stripe_rows;
      require(a.A.allocate(a.I,a.K,8),"A allocation");
      auto *tensor=ggml_new_tensor_2d(ctx,GGML_TYPE_F32,a.K,a.I);
      std::memcpy(tensor->data,av.data(),av.size()*4);
      require(quants::quantize_activation(tensor,a),"existing host quantizer"); ggml_free(ctx);
      if(a.I>32) {
        const auto &meta=std::get<quants::act::exsia::Meta>(a.act_quant.storage());
        require(meta.theta.size()==3 && meta.theta[0]!=std::numeric_limits<int16_t>::min() &&
                meta.theta[1]!=std::numeric_limits<int16_t>::min() &&
                meta.theta[2]!=std::numeric_limits<int16_t>::min() &&
                meta.theta[0]!=meta.theta[1] && meta.theta[0]!=meta.theta[2] && meta.theta[1]!=meta.theta[2],
                "long fixture requires distinct valid stripe theta");
      }
      f.capture(dir);
      // Optional live-quantization input; existing IFX1 payload stays unchanged.
      write_floats(dir/"input-f32.bin",av);
      // Existing host CPU matmul supplies f_out; existing Gemmini integer
      // reference supplies each raw block. No new dot-product implementation.
      auto reference=a; std::vector<float> expected=f.output; reference.f_out=expected.data();
      auto result=MatMul(reference).run_full(); require(result.status==MatMulStatus::success,"host CPU reference");
      write_floats(dir/"expected-fout.bin",expected);
      Bytes raw;
      for(size_t block=0;block<a.K/32;++block) for(size_t i=0;i<a.I;++i) for(size_t j=0;j<a.J;++j) {
        acc_t value=0; const auto *row=reinterpret_cast<const elem_t *>(a.A.raw_data())+i*a.A.row_stride_bytes+block*32;
        matmul_cpu_int32(false,false,1,1,32,row,f.weights[j*(a.K/32)+block].qs,nullptr,&value,
          32,1,0,1,1,1,1,NO_ACTIVATION,0,0,false,true);
        put(raw,uint32_t(value),4);
      }
      write(dir/"expected-raw.bin",raw);
    } else {
      const std::string mode=argv[1];
      require(mode=="replay" || mode=="simulate" || mode=="control","unknown mode"); f.restore(dir);
      if(mode=="control") {
        unsigned calls=0;
        im2p::gemmini::Options control;
        control.full_executor_context=&calls;
        control.full_executor=[](void *p,const im2p_matmul_desc_t *,im2p_work_stats_extended_t *) -> int {
          ++*static_cast<unsigned *>(p); return IM2P_ERROR;
        };
        auto started=im2p::gemmini::execute(&f.args,im2p::gemmini::Mode::full,control);
        require(started.status.ok() && started.run,"control execute");
        auto first=im2p::gemmini::fence(*started.run),second=im2p::gemmini::fence(*started.run);
        require(!first.status.ok() && first.status.code==second.status.code && calls==1,"sticky fence");
        require(std::all_of(f.output.begin(),f.output.end(),[](float x){return x==17.0f;}),"failed output publication");
        auto pipeline=im2p::gemmini::execute(&f.args,im2p::gemmini::Mode::stripe_pipeline,control);
        require(pipeline.status.code==im2p::gemmini::StatusCode::unsupported_route && !pipeline.run,"pipeline rejection");
        control.residual_stage_mode=im2p::gemmini::ResidualStageMode::host_direct;
        auto residual=im2p::gemmini::execute(&f.args,im2p::gemmini::Mode::full,control);
        require(residual.status.code==im2p::gemmini::StatusCode::unsupported_route && !residual.run,"residual rejection");
        require(simulator_creates==0 && simulator_executes==0,"control simulator fallback");
        std::cout<<"FULL_EXECUTOR_CONTROL PASS failure_sticky=1 output_preserved=1 pipeline_rejected=1 residual_rejected=1 simulator_calls=0\n";
        return 0;
      }
    }
    Execution execution{dir,std::string(argv[1])=="replay"}; execution.identity=f.identity;
    im2p::gemmini::Options options; options.full_executor_context=&execution; options.full_executor=Execution::execute;
    auto started=im2p::gemmini::execute(&f.args,im2p::gemmini::Mode::full,options);
    require(started.status.ok() && started.run,"frontend execute rejected");
    auto done=im2p::gemmini::fence(*started.run);
    if(!done.status.ok()) std::cerr<<done.status.message<<'\n';
    require(done.status.ok(),"frontend fence failed");
    auto path=dir/(execution.board?"r2-fout.bin":"r1-fout.bin"); write_floats(path,f.output);
    require(read(path)==read(dir/"expected-fout.bin"),"f_out exact reference mismatch");
    require(done.stats.base.completed_fragments==((f.args.I+15)/16)*((f.args.J+15)/16)*(f.args.K/16),"fragment count");
    require(simulator_creates==(execution.board?0u:1u) && simulator_executes==(execution.board?0u:1u),"backend call identity");
    std::cout << (execution.board?"FPGA_REPLAY_V1":"IM2P_SIM") << " PASS logical=1 raw=" << execution.raw.size()/4
      << " f_out=" << f.args.I*f.args.J << " fragments=" << done.stats.base.completed_fragments
      << " simulator_creates=" << simulator_creates << " simulator_executes=" << simulator_executes << '\n';
 } catch(const std::exception &e) { std::cerr<<"FULL_REPLAY_FAIL: "<<e.what()<<'\n'; return 1; }
 return 0;
}
