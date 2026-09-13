// NEXSRP01 schema 1: original channel rows and captured non-ExSIA activation.
// Each reference uses its unchanged native frontend and real RTL archive.
#include "ggml-gemmini-args.h"
#include "im2p_gemmini_frontend.hpp"
#include "quants/act/exsia/exsia.hpp"
#include <gemmini.h>
#include <algorithm>
#include <bit>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <vector>
using namespace ggml::gemmini;
using namespace im2p::gemmini;
static_assert(IM2P_ABI_VERSION == (IM2P_MODEL_REPLAY_MAIN_EXTERNAL ? 5 : 4));
static_assert(std::endian::native == std::endian::little && sizeof(float) == 4);
static void require(bool ok, const char *why) { if (!ok) throw std::runtime_error(why); }
static bool exact(float a, float b) {return std::bit_cast<uint32_t>(a) == std::bit_cast<uint32_t>(b);}
template<class T> static std::vector<T> read(std::ifstream &file, size_t count) {
    std::vector<T> result(count);file.read(reinterpret_cast<char *>(result.data()), count * sizeof(T));
    require(bool(file), "truncated channel capture");return result;
}
struct Capture {
    ggml_gemmini_args_t args{};
    std::vector<int8_t> weights;
    std::vector<float> weight_scales, frozen, scalar, output;
    std::vector<int64_t> scalar_raw;
    size_t family = 0;
    explicit Capture(const char *path) {
        require(std::filesystem::file_size(path) <= 32*1024*1024, "capture size bound");
        std::ifstream file(path,std::ios::binary);
        const auto h=read<uint64_t>(file,11);
        require(h[0]==0x313050525358454eULL && h[1]==1, "channel capture schema");
        args.I=h[2];args.J=h[3];args.K=h[4];args.tile_I=h[5];args.tile_J=h[6];args.tile_K=h[7];
        args.activation_rows_per_stripe=h[8];family=h[10];
        require(args.I && args.I<=1024 && args.J && args.J<=128 && args.K && args.K<=1024 &&
                h[8] && h[8]==h[5]*DIM && h[9]>=args.K && h[9]<=2048 && family>=1 && family<=4, "bounded capture geometry/family");
        const auto original_float=read<float>(file,args.I*args.K);
        require(std::all_of(original_float.begin(),original_float.end(),[](float x){return std::isfinite(x);}), "original F32 provenance");
        require(args.A.allocate(args.I,args.K,8), "activation allocation");
        const auto activation=read<uint8_t>(file,args.I*h[9]);args.A.bytes->assign(activation.begin(),activation.end());
        args.A.row_stride_bytes=h[9];args.sA=h[9];args.sB=args.K;
        const auto scales=read<float>(file,args.I);
        require(std::all_of(scales.begin(),scales.end(),[](float x){return std::isfinite(x)&&x>0;}), "activation scale values");
        if(family==1) {
            require(std::all_of(scales.begin(),scales.end(),[&](float x){return exact(x,scales[0]);}),"tensor scale identity");
            args.act_quant.storage().emplace<quants::act::tensor::Meta>().scale=scales[0];
        } else if(family==2) args.act_quant.storage().emplace<quants::act::token::Meta>().scales=scales;
        else if(family==3) args.act_quant.storage().emplace<quants::act::block::Meta>().scales=scales;
        else {
            auto &m=args.act_quant.storage().emplace<quants::act::stripe::Meta>();
            for(size_t i=0;i<args.I;i+=h[8]) {
                m.scales.push_back(scales[i]);
                for(size_t row=i;row<std::min(args.I,i+h[8]);++row)require(exact(scales[row],scales[i]),"stripe scale identity");
            }
        }
        const auto rows=read<uint8_t>(file,args.J*(sizeof(float)+args.K));
        weights.resize(args.J*args.K);weight_scales.resize(args.J);
        for(size_t j=0;j<args.J;++j) {
            const auto *row=rows.data()+j*(sizeof(float)+args.K);
            std::memcpy(&weight_scales[j],row,sizeof(float));
            std::memcpy(weights.data()+j*args.K,row+sizeof(float),args.K);
            require(std::isfinite(weight_scales[j]),"original channel scale");
        }
        args.weight_format=ggml_gemmini_args_t::im2p_weight_format_t::q8_channel_dense_sidecar;
        args.B=weights.data();args.weight_channel_scales=weight_scales.data();args.weight_channel_scale_count=args.J;
        args.transpose_B=true;args.full_C=true;args.tiled_matmul_type=WS;
        const auto frozen_raw=read<int64_t>(file,args.I*args.J);frozen=read<float>(file,args.I*args.J);
        require(file.peek()==std::ifstream::traits_type::eof(),"capture trailing data");
        scalar.resize(frozen.size());scalar_raw.resize(frozen.size());output.assign(frozen.size(),17.0f);
        args.f_out=output.data();args.stride_f_out=args.J;args.col_stride_f_out=1;
        require(args.has_q8_channel_dense_sidecar_contract(),"original native sidecar contract");
        for(size_t i=0;i<args.I;++i)for(size_t j=0;j<args.J;++j) {
            int64_t sum=0;for(size_t k=0;k<args.K;++k)sum+=int64_t(args.A.get(i,k))*weights[j*args.K+k];
            const size_t at=i*args.J+j;scalar_raw[at]=sum;scalar[at]=float(double(sum)*double(weight_scales[j])*double(scales[i]));
            require(sum==frozen_raw[at] && exact(scalar[at],frozen[at]),"independent input oracle differs from frozen R2");
        }
    }
};

struct Observation {
    Capture &capture;
    im2p_provider_t downstream{};
    size_t starts = 0;
    std::vector<uint8_t> seen;
    explicit Observation(Capture &c) : capture(c), seen(c.scalar_raw.size(), 0) {}
    template<class T> bool begin(const T &d) {
        if (starts++ || d.vector_op != IM2P_VECTOR_BYPASS || d.m != capture.args.I ||
            d.n != capture.args.J || d.k != capture.args.K || !d.provider.write_output) return false;
        downstream = d.provider;
        return true;
    }
    static int weight(void *p, size_t row, size_t col, size_t n, int8_t *out) {
        auto &s = *static_cast<Observation *>(p);
        return s.downstream.read_weight_i8(s.downstream.context, row, col, n, out);
    }
#if IM2P_MODEL_REPLAY_MAIN_EXTERNAL
    using Scale = uint32_t;
#else
    using Scale = int8_t;
#endif
    static int scale(void *p, size_t block, size_t col, size_t n, Scale *out) {
        auto &s = *static_cast<Observation *>(p);
        return s.downstream.read_scale(s.downstream.context, block, col, n, out);
    }
    static int output(void *p, size_t block, size_t row, size_t col, size_t n, const int64_t *values
#if IM2P_MODEL_REPLAY_MAIN_EXTERNAL
                      , uint32_t domain
#endif
    ) {
        auto &s = *static_cast<Observation *>(p);
        const auto &a = s.capture.args;
#if IM2P_MODEL_REPLAY_MAIN_EXTERNAL
        if (domain != IM2P_OUTPUT_LEGACY_FINAL) return IM2P_ERROR;
#endif
        if (!values || !n || block != 0 || row >= a.I || col >= a.J || n > a.J - col) return IM2P_ERROR;
        for (size_t i = 0; i < n; ++i) {
            const size_t at = (block * a.I + row) * a.J + col + i;
            if (s.seen[at]++ || values[i] != s.capture.scalar_raw[at]) return IM2P_ERROR;
        }
        return s.downstream.write_output(s.downstream.context, block, row, col, n, values
#if IM2P_MODEL_REPLAY_MAIN_EXTERNAL
                                         , domain
#endif
        );
    }
    im2p_provider_t provider() { return {this, weight, nullptr, scale, output}; }
};
static Observation *active;
extern "C" int __real_im2p_execute_matmul_extended(im2p_sim_t *, const im2p_matmul_desc_t *, im2p_work_stats_extended_t *);
extern "C" int __wrap_im2p_execute_matmul_extended(im2p_sim_t *s, const im2p_matmul_desc_t *d, im2p_work_stats_extended_t *stats) {
    if (!active || !d || !active->begin(*d)) return IM2P_ERROR;
    auto copy = *d; copy.provider = active->provider();
    return __real_im2p_execute_matmul_extended(s, &copy, stats);
}
extern "C" int __real_im2p_begin_striped_matmul(im2p_sim_t *, const im2p_stripe_work_desc_t *, im2p_stream_t **);
extern "C" int __wrap_im2p_begin_striped_matmul(im2p_sim_t *s, const im2p_stripe_work_desc_t *d, im2p_stream_t **stream) {
    if (!active || !d || !active->begin(*d)) return IM2P_ERROR;
    auto copy = *d; copy.provider = active->provider();
    return __real_im2p_begin_striped_matmul(s, &copy, stream);
}

int main(int argc,char **argv) {
    try {
        require(argc==3 && (std::string(argv[2])=="FULL" || std::string(argv[2])=="STRIPE_PIPELINE"),"usage: channel_replay CAPTURE FULL|STRIPE_PIPELINE");
        require(im2p_sim_abi_version()==IM2P_ABI_VERSION && im2p_sim_activation_bits()==8 && im2p_sim_weight_bits()==8 && im2p_sim_dim()==16,"native archive identity");
        Capture c(argv[1]);Observation observation(c);active=&observation;
        const auto mode=std::string(argv[2])=="FULL" ? Mode::full : Mode::stripe_pipeline;
        Options options{1000000};
#if IM2P_MODEL_REPLAY_MAIN_EXTERNAL
        options.numerical_contract=NumericalContract::main_external;
#endif
        auto run=execute(&c.args,mode,options);require(run.status.ok() && bool(run.run),run.status.message);
        size_t stripes=0;
        if(mode==Mode::stripe_pipeline) {
            for(size_t begin=0;begin<c.args.I;begin+=c.args.activation_rows_per_stripe) {
                quants::act::exsia::StripeReadyEvent event{};event.run_id=1;event.stripe_id=stripes;event.slot=stripes%2;
                event.row_begin=begin;event.row_end=std::min(c.args.I,begin+c.args.activation_rows_per_stripe);
                const auto status=submit_stripe(*run.run,event);require(status.ok(),status.message);++stripes;
            }
        }
        const auto done=fence(*run.run);require(done.status.ok(),done.status.message);
        if(mode==Mode::stripe_pipeline) require(authorize_output_commit(*run.run,true).ok(),"output authorization");
        require(observation.starts==1 && std::all_of(observation.seen.begin(),observation.seen.end(),[](uint8_t v){return v==1;}) &&
                done.stats.base.work_total_cycles && done.stats.base.activation_read_requests && done.stats.base.weight_read_requests && done.stats.base.output_write_requests,"one real RTL job/output conservation");
        for(size_t i=0;i<c.output.size();++i)require(exact(c.output[i],c.scalar[i]) && exact(c.output[i],c.frozen[i]),"R0/R1 differs from independent scalar/R2 f_out");
        std::cout<<"CHANNEL_REPLAY_PASS reference="<<(IM2P_MODEL_REPLAY_MAIN_EXTERNAL?"R1":"R0")<<" mode="<<argv[2]<<" family="<<c.family
                 <<" M="<<c.args.I<<" N="<<c.args.J<<" K="<<c.args.K<<" stripes="<<stripes<<" raw_exact="<<c.scalar_raw.size()
                 <<" fout_exact="<<c.output.size()<<" domain=0 schema=1 frozen_R2=equal independent_scalar=equal rtl_cycles="<<done.stats.base.work_total_cycles<<'\n';
        return 0;
    }catch(const std::exception &e){std::cerr<<"CHANNEL_REPLAY_FAIL "<<e.what()<<'\n';return 1;}
}
