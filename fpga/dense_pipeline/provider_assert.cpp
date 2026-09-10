// Direct provider assertion test: existing fixture/reference, no UART or new math.
#include "VmkDensePipeline.h"
#include "verilated.h"
#include <algorithm>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <vector>

static void check(bool ok, const char *why) { if (!ok) throw std::runtime_error(why); }
static std::vector<uint8_t> read(const char *path) {
    std::ifstream input(path, std::ios::binary); check(bool(input), "fixture file");
    return std::vector<uint8_t>(std::istreambuf_iterator<char>(input), {});
}
struct Test {
    VerilatedContext context;
    VmkDensePipeline top{&context};
    uint64_t ticks=0, comparisons=0, stripe_count=0;
    size_t m,n,k,sr;
    std::vector<uint8_t> input, expected;
    void eval() { top.eval(); check(!context.gotFinish(), "unexpected assertion/$finish"); }
    void tick() {
        top.CLK=0;eval();context.timeInc(20000);
        top.CLK=1;eval();context.timeInc(20000);++ticks;
        check(!top.RST_N || !top.protocolError, "provider address/count failure");
    }
    void clocks(unsigned count) { while(count--)tick(); }
    template<class Predicate> void until(Predicate ready) {
        const auto deadline=ticks+100000;
        while(!ready()) {check(ticks<deadline,"provider watchdog");tick();}
    }
    void load(bool weight,size_t first,size_t rows) {
        const size_t bytes=weight?k*64:rows*128;
        const size_t offset=weight?m*128:first*128;
        for(size_t at=0;at<bytes;at+=16) {
            until([&]{return weight?top.RDY_loadWeight:top.RDY_loadActivation;});
            auto &value=weight?top.loadWeight_values:top.loadActivation_values;
            for(size_t w=0;w<4;++w) {
                uint32_t bits=0;
                for(size_t b=0;b<4;++b)bits|=uint32_t(input[offset+at+w*4+b])<<(8*b);
                value[w]=bits;
            }
            if(weight){top.loadWeight_word=at/16;top.EN_loadWeight=1;}
            else{top.loadActivation_word=(offset+at)/16;top.EN_loadActivation=1;}
            tick();top.EN_loadWeight=top.EN_loadActivation=0;eval();
        }
    }
    void publish(size_t first,size_t rows) {
        until([&]{return top.RDY_publish;});
        top.publish_rowBegin=first;top.publish_rowCount=rows;top.EN_publish=1;
        tick();top.EN_publish=0;eval();
        check(top.publishedRows==first+rows,"publication count");
    }
    void raw(size_t first,size_t rows) {
        for(size_t block=0;block<k/32;++block)for(size_t row=first;row<first+rows;++row)
            for(size_t col=0;col<n;col+=16) {
                until([&]{return top.RDY_requestOutput;});
                top.requestOutput_word=((block*m+row)<<2)+col/16;
                top.EN_requestOutput=1;tick();top.EN_requestOutput=0;eval();
                until([&]{return top.outputValid&&top.RDY_outputResponse;});
                // Delay consumption while autonomous next-stripe work may run.
                uint32_t saved[16];for(size_t l=0;l<16;++l)saved[l]=top.outputResponse[l];
                clocks(3);
                for(size_t lane=0;lane<16;++lane) {
                    check(top.outputResponse[lane]==saved[lane],"output changed under backpressure");
                    uint32_t bits=0;
                    if(col+lane<n) {
                        const size_t offset=((block*m+row)*n+col+lane)*4;
                        for(size_t b=0;b<4;++b)bits|=uint32_t(expected[offset+b])<<(8*b);
                        ++comparisons;
                    }
                    check(saved[lane]==bits,"raw signed32/wire-padding mismatch");
                }
                check(top.RDY_consumeOutput,"consume readiness");
                top.EN_consumeOutput=1;tick();top.EN_consumeOutput=0;eval();
            }
    }
    void stripe(size_t id) {
        const size_t first=id*sr,rows=std::min(sr,m-first);
        until([&]{return top.stripeCompletionValid;});
        check(top.stripeId==id&&top.stripeRowBegin==first&&top.stripeRowCount==rows,"stripe identity/range");
        check(top.stripeCompletionCycle>=top.stripePublishCycle,"stripe cycle order");
        clocks(17);raw(first,rows);
        check(top.RDY_acknowledgeStripe,"stripe acknowledgement readiness");
        top.EN_acknowledgeStripe=1;tick();top.EN_acknowledgeStripe=0;eval();++stripe_count;
    }
    void run(bool striped,unsigned job) {
        load(true,0,0);if(!striped)load(false,0,m);
        until([&]{return top.RDY_start;});
        top.start_striped=striped;top.start_m=m;top.start_n=n;top.start_k=k;
        top.start_job=job;top.start_contextId=job;top.EN_start=1;tick();top.EN_start=0;eval();
        if(striped) {
            clocks(67);check(top.activationRequests==0&&top.firstActivationCycle==0,"unpublished A consumed");
            load(false,0,sr);publish(0,sr);
            load(false,sr,sr);publish(sr,sr);
            stripe(0);
            load(false,2*sr,m-2*sr);publish(2*sr,m-2*sr);
            stripe(1);stripe(2);
        }
        until([&]{return top.done;});
        if(!striped)raw(0,m);
        const size_t works=((m+15)/16)*((n+15)/16),writes=m*((n+15)/16)*(k/32);
        check(top.fragments==works*(k/16)&&top.works==works,"fragment/output-work count");
        check(top.outputWrites==writes&&top.outputAcks==writes,"writeback count");
        if(striped)check(top.firstActivationPublishedRows==sr,"first-A publication window");
        std::cout<<"PROVIDER_JOB_PASS mode="<<(striped?"PIPELINE":"FULL")<<" cycles="<<top.cycles
                 <<" fragments="<<top.fragments<<" works="<<top.works<<" writes="<<top.outputWrites
                 <<" first_A_rows="<<top.firstActivationPublishedRows<<" comparisons="<<comparisons<<'\n';
        until([&]{return top.RDY_acknowledge;});top.EN_acknowledge=1;tick();top.EN_acknowledge=0;eval();
    }
};
int main(int argc,char **argv) {
    try {
        check(argc==7,"provider_assert STAGING RAW M N K STRIPE_ROWS");
        Test test;test.input=read(argv[1]);test.expected=read(argv[2]);
        test.m=std::stoul(argv[3]);test.n=std::stoul(argv[4]);test.k=std::stoul(argv[5]);test.sr=std::stoul(argv[6]);
        check(test.m>2*test.sr&&test.m<=3*test.sr&&test.sr%16==0,"three-stripe fixture geometry");
        check(test.input.size()==test.m*128+test.k*64&&test.expected.size()==test.m*test.n*(test.k/32)*4,"fixture lengths");
        test.top.RST_N=0;test.clocks(16);test.top.RST_N=1;test.clocks(16);
        test.run(false,1);test.run(true,2);
        check(test.comparisons==2*test.m*test.n*(test.k/32)&&test.stripe_count==3,"completed count");
        std::cout<<"PROVIDER_ASSERT_PASS jobs=2 stripes=3 comparisons="<<test.comparisons<<" ticks="<<test.ticks<<'\n';
    } catch(const std::exception &error) {std::cerr<<"PROVIDER_ASSERT_FAIL "<<error.what()<<'\n';return 1;}
}
