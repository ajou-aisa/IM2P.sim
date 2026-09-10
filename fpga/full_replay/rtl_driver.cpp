#include "Vfull_uart_shell.h"
#include "Vfull_uart_shell___024root.h"
#include "verilated.h"
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <vector>

struct Driver {
    VerilatedContext context;
    Vfull_uart_shell top{&context};
    std::vector<uint8_t> received;
    int rx_count=0,rx_bit=-1; uint8_t rx_byte=0; bool previous=true;
    uint64_t ticks=0, launches=0;
    uint64_t scale_reads=0, block_transitions=0;
    std::vector<uint64_t> output_addresses, fragment_blocks;
    void tick() {
        top.clk=0; top.eval(); context.timeInc(20000);
        if(context.gotFinish()) throw std::runtime_error("unexpected RTL $finish");
        if(top.reset_n) {
            auto &r=*top.rootp;
            if(r.full_uart_shell__DOT__do_start) {
                ++launches;scale_reads=0;block_transitions=0;output_addresses.clear();fragment_blocks.clear();
            }
            if(r.full_uart_shell__DOT__core__DOT__WILL_FIRE_RL_returnScale) ++scale_reads;
            if(r.full_uart_shell__DOT__core__DOT__WILL_FIRE_RL_core_core_core_finishMatrixExecution) {
                uint64_t block=r.full_uart_shell__DOT__core__DOT__core_core_core_matrixFragmentBlockIndexReg;
                if(!fragment_blocks.empty() && block>fragment_blocks.back()) ++block_transitions;
                fragment_blocks.push_back(block);
            }
            if(r.full_uart_shell__DOT__core__DOT__WILL_FIRE_RL_writeOutput)
                output_addresses.push_back(r.full_uart_shell__DOT__core__DOT__core_core_core_outputRequestAddressReg);
        }
        top.clk=1; top.eval(); context.timeInc(20000); ++ticks;
        if(context.gotFinish()) throw std::runtime_error("unexpected RTL $finish");
        bool value=top.uart_tx;
        if(rx_bit<0) {
            if(previous && !value) { rx_bit=0; rx_count=37; rx_byte=0; }
        } else if(--rx_count==0) {
            if(rx_bit<8) { rx_byte|=uint8_t(value)<<rx_bit++; rx_count=25; }
            else { if(!value) throw std::runtime_error("UART stop bit"); received.push_back(rx_byte); rx_bit=-1; }
        }
        previous=value;
    }
    void clocks(size_t n) { for(size_t i=0;i<n;++i) tick(); }
    void byte(uint8_t value) {
        top.uart_rx=0; clocks(25);
        for(int bit=0;bit<8;++bit) { top.uart_rx=(value>>bit)&1; clocks(25); }
        top.uart_rx=1; clocks(25);
    }
    Driver() { top.uart_rx=1;top.reset_n=0;clocks(16);top.reset_n=1;clocks(16); }
};
int main(int argc,char **argv) {
  try {
    if(argc!=2) throw std::runtime_error("usage: rtl_driver itinerary.txt");
    Driver driver; std::ifstream plan(argv[1]); if(!plan) throw std::runtime_error("itinerary missing");
    std::string request,response; size_t transactions=0;
    while(plan>>request>>response) {
        if(request=="@reset") {
            driver.top.reset_n=0;driver.clocks(16);driver.top.reset_n=1;driver.clocks(16);
            driver.received.clear(); continue;
        }
        std::ifstream in(request,std::ios::binary); if(!in) throw std::runtime_error("request missing");
        std::vector<uint8_t> bytes(std::istreambuf_iterator<char>(in),{});
        driver.received.clear();
        for(auto value:bytes) driver.byte(value);
        uint64_t deadline=driver.ticks+20000000; size_t length=100;
        while(driver.received.size()<length) {
            driver.tick();
            if(driver.ticks>deadline) throw std::runtime_error("RTL UART timeout");
            if(driver.received.size()>=96) {
                uint32_t payload=0; for(int i=0;i<4;++i) payload|=uint32_t(driver.received[20+i])<<(8*i);
                if(payload>18432) throw std::runtime_error("RTL output capacity"); length=100+payload;
            }
        }
        driver.clocks(300);
        if(driver.received.size()!=length) throw std::runtime_error("unexpected trailing response");
        std::ofstream out(response,std::ios::binary);
        out.write(reinterpret_cast<const char *>(driver.received.data()),driver.received.size());
        if(!out) throw std::runtime_error("response write");
        std::ofstream events(response+".events.json");
        events<<"{\"launches\":"<<driver.launches<<",\"scale_requests\":"<<driver.scale_reads
              <<",\"scale_block_transitions\":"<<driver.block_transitions<<",\"fragment_blocks\":[";
        for(size_t i=0;i<driver.fragment_blocks.size();++i) events<<(i?",":"")<<driver.fragment_blocks[i];
        events<<"],\"output_addresses\":[";
        for(size_t i=0;i<driver.output_addresses.size();++i) events<<(i?",":"")<<driver.output_addresses[i];
        events<<"]}\n";
        ++transactions;
        std::cout<<"RTL_PACKET transaction="<<transactions<<" bytes="<<length<<" ticks="<<driver.ticks
                 <<" launches="<<driver.launches<<std::endl;
    }
    if(!transactions) throw std::runtime_error("no transactions");
    std::cout<<"RTL_PACKET_COMPLETE transactions="<<transactions<<" launches="<<driver.launches<<std::endl;
  } catch(const std::exception &e) { std::cerr<<"RTL_FAIL: "<<e.what()<<std::endl;return 1; }
}
