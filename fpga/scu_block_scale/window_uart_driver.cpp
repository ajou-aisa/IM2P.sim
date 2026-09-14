// UART-bit simulation only. No serial device, CPU dot, or expected result.
#include "Vscu_window_uart_shell.h"
#include "Vscu_window_uart_shell___024root.h"
#include "verilated.h"
#include <algorithm>
#include <bit>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include <cerrno>
#include <fcntl.h>
#include <poll.h>
#include <unistd.h>

constexpr unsigned uart_divisor = Vscu_window_uart_shell___024root::scu_window_uart_shell__DOT__UART_DIV;
static_assert(uart_divisor >= 8, "UART-bit test requires at least eight clocks per bit");

// Passive reads of sealed production signals, sampled before the accepted edge.
// No monitor rule, register, or accessor is added to the generated RTL.
#define CORE(name) r.scu_window_uart_shell__DOT__core__DOT__##name
static void require(bool condition, const char *message) {
    if (!condition) throw std::runtime_error(message);
}
static int32_t clamp32(int64_t value) {
    return int32_t(std::clamp(value, int64_t(INT32_MIN), int64_t(INT32_MAX)));
}

struct Driver {
    VerilatedContext context;
    Vscu_window_uart_shell top{&context};
    std::vector<uint8_t> received;
    uint64_t ticks=0, loads=0, launches=0, output_acks=0, stripe_acks=0;
    uint64_t scu_fragments=0, replace_fragments=0, accumulate_fragments=0;
    uint64_t scu_commits=0, replace_commits=0, accumulate_commits=0, lane0_clamps=0, lane0_writes=0;
    uint64_t final_work_writes=0, intermediate_work_writes=0, final_scalars=0, releases=0;
    int rx_bit=-1, rx_count=0;
    uint8_t rx_byte=0;
    bool previous=true;
    void tick() {
        top.clk=0; top.eval(); context.timeInc(20000);
        bool capture_lane0=false;
        int32_t expected_contribution=0;
        if (top.reset_n) {
            const auto &r=*top.rootp;
            loads+=r.scu_window_uart_shell__DOT__do_load!=0;
            launches+=r.scu_window_uart_shell__DOT__do_start;
            output_acks+=r.scu_window_uart_shell__DOT__do_consume;
            stripe_acks+=r.scu_window_uart_shell__DOT__do_stripe_ack;
            releases+=r.scu_window_uart_shell__DOT__do_release;
            const unsigned op=CORE(logicalOp);
            if (op==4 || op==5) {
                if (CORE(WILL_FIRE_RL_core_core_core_beginMatrixFragment)) {
                    const bool first=CORE(core_core_core_workScheduler_firstFragmentReg);
                    require(!CORE(core_core_core_matrixBlockOutputReg) &&
                            !CORE(core_core_core_workScheduler_resetAtBlockBoundaryReg),
                            "SCU block reset/output mode is enabled");
                    require(first==(CORE(core_core_core_workScheduler_kStartReg)==0),
                            "SCU first fragment does not replace at K origin");
                    ++scu_fragments;
                    if (first) ++replace_fragments; else ++accumulate_fragments;
                }
                if (CORE(WILL_FIRE_RL_core_core_core_commitVectorResult)) {
                    const unsigned command=CORE(core_core_core_commandReg);
                    const bool accumulate=CORE(core_core_core_matrixFragmentKStartReg)!=0;
                    require((command&7)==op && ((command>>3)&1)==accumulate &&
                            CORE(core_core_core_matrixFragmentAccumulateReg)==accumulate,
                            "SCU accepted commit changed op or full-K accumulate control");
                    ++scu_commits;
                    if (accumulate) ++accumulate_commits; else ++replace_commits;
                    capture_lane0=(CORE(core_core_core_vectorUnit_validReg)&1)!=0;
                    if (capture_lane0) {
                        const int32_t partial=std::bit_cast<int32_t>(CORE(core_core_core_vectorUnit_partialReg)[0]);
                        const uint32_t metadata=CORE(core_core_core_vectorUnit_scaleReg)[0];
                        if (op==4) expected_contribution=clamp32(int64_t(partial)*metadata);
                        else if (!partial || metadata==0x80000000U) expected_contribution=0;
                        else if (metadata>=32) expected_contribution=partial<0 ? INT32_MIN : INT32_MAX;
                        else expected_contribution=clamp32(int64_t(partial)*(int64_t(1)<<metadata));
                    }
                }
                if (CORE(core_core_core_accumulator_banks_0__024EN) &&
                        CORE(core_core_core_accumulator_banks_0__024WE)) {
                    const int32_t contribution=std::bit_cast<int32_t>(CORE(core_core_core_accumulator_pendingContributions)[0]);
                    const int32_t previous=std::bit_cast<int32_t>(CORE(core_core_core_accumulator_banks_0__DOT__DO_R));
                    const int32_t expected=CORE(core_core_core_accumulator_pendingAccumulate)
                        ? clamp32(int64_t(previous)+contribution) : contribution;
                    require(CORE(core_core_core_accumulator_pendingSaturating) &&
                            std::bit_cast<int32_t>(CORE(core_core_core_accumulator_banks_0__024DI))==expected,
                            "SCU accepted accumulator write violates replace/clamp order");
                    ++lane0_writes;
                }
                if (CORE(WILL_FIRE_RL_core_core_core_beginIntermediateMatrixWriteback)) {
                    ++intermediate_work_writes;
                    throw std::runtime_error("SCU emitted intermediate block writeback");
                }
                if (CORE(WILL_FIRE_RL_core_core_core_beginFinalMatrixWriteback)) {
                    require(CORE(core_core_core_matrixFragmentKStartReg)+
                            CORE(core_core_core_matrixFragmentKCountReg)==CORE(reductionReg),
                            "SCU final writeback precedes full K completion");
                    ++final_work_writes;
                }
                if (r.scu_window_uart_shell__DOT__do_consume)
                    final_scalars+=r.scu_window_uart_shell__DOT__stream_count;
            }
        }
        top.clk=1; top.eval(); context.timeInc(20000); ++ticks;
        if (capture_lane0) {
            const auto &r=*top.rootp;
            require((CORE(core_core_core_accumulator_pendingValids)&1)!=0 &&
                    CORE(core_core_core_accumulator_pendingSaturating) &&
                    std::bit_cast<int32_t>(CORE(core_core_core_accumulator_pendingContributions)[0])==expected_contribution,
                    "SCU fragment clamp was not captured by accumulator");
            ++lane0_clamps;
        }
        if (context.gotFinish()) throw std::runtime_error("unexpected RTL finish");
        const bool value=top.uart_tx;
        if (rx_bit<0) {
            if (previous && !value) { rx_bit=0; rx_count=3*uart_divisor/2; rx_byte=0; }
        } else if (--rx_count==0) {
            if (rx_bit<8) { rx_byte|=uint8_t(value)<<rx_bit++; rx_count=uart_divisor; }
            else {
                if (!value) throw std::runtime_error("UART stop bit");
                received.push_back(rx_byte); rx_bit=-1;
                if (received.size()>8192) throw std::runtime_error("UART response extent");
            }
        }
        previous=value;
    }
    void stats(std::ostream &out) const {
        out<<"{\"ticks\":"<<ticks<<",\"loads\":"<<loads
           <<",\"launches\":"<<launches<<",\"output_acks\":"<<output_acks
           <<",\"stripe_acks\":"<<stripe_acks<<",\"releases\":"<<releases
           <<",\"scu_fragments\":"<<scu_fragments<<",\"replace_fragments\":"<<replace_fragments
           <<",\"accumulate_fragments\":"<<accumulate_fragments<<",\"scu_commits\":"<<scu_commits
           <<",\"replace_commits\":"<<replace_commits<<",\"accumulate_commits\":"<<accumulate_commits
           <<",\"lane0_fragment_clamp_checks\":"<<lane0_clamps<<",\"lane0_accumulator_write_checks\":"<<lane0_writes
           <<",\"final_work_writebacks\":"<<final_work_writes
           <<",\"intermediate_work_writebacks\":"<<intermediate_work_writes
           <<",\"final_integer_scalars\":"<<final_scalars<<",\"uart_divisor\":"<<uart_divisor<<"}";
    }
    void clocks(unsigned count) { while(count--) tick(); }
    void byte(uint8_t value) {
        top.uart_rx=0; clocks(uart_divisor);
        for (unsigned bit=0;bit<8;++bit) { top.uart_rx=(value>>bit)&1; clocks(uart_divisor); }
        top.uart_rx=1; clocks(uart_divisor);
    }
    Driver() {
        top.uart_rx=1; top.reset_n=0; clocks(16);
        top.reset_n=1; clocks(16);
    }
    std::vector<uint8_t> exchange(const std::string &hex) {
        if (hex.size()%2 || hex.size()>8296 || !received.empty())
            throw std::runtime_error("UART request/stale response extent");
        for (size_t at=0;at<hex.size();at+=2) {
            size_t used=0;
            const auto value=std::stoul(hex.substr(at,2),&used,16);
            if (used!=2) throw std::runtime_error("invalid request hex");
            byte(uint8_t(value));
        }
        size_t expected=52;
        const auto deadline=ticks+3000000;
        while (received.size()<expected) {
            tick();
            if (ticks>deadline) throw std::runtime_error("UART response timeout");
            if (received.size()>=48) {
                uint32_t size=0;
                for (unsigned i=0;i<4;++i) size|=uint32_t(received[24+i])<<(8*i);
                if (size>4096) throw std::runtime_error("UART response payload extent");
                expected=52+size;
            }
        }
        clocks(64);
        if (received.size()!=expected) throw std::runtime_error("UART trailing response bytes");
        auto result = received;
        received.clear();
        return result;
    }
};
#undef CORE

std::string hex_bytes(const std::vector<uint8_t> &bytes) {
    constexpr char digits[]="0123456789abcdef";
    std::string result;
    for (auto value:bytes) { result += digits[value>>4]; result += digits[value&15]; }
    return result;
}

// The only descriptor accepted here is an inherited PTY master. No device path
// is opened. The same UART bit driver supplies every response byte.
void serve_pty(Driver &driver, int fd) {
    if (!isatty(fd) || fcntl(fd,F_SETFL,fcntl(fd,F_GETFL)|O_NONBLOCK)<0)
        throw std::runtime_error("invalid inherited PTY");
    uint64_t packets=0;
    std::vector<uint8_t> request;
    size_t expected=48;
    for (;;) {
        pollfd port{fd,POLLIN,0};
        const int ready=::poll(&port,1,1);
        if (ready<0 && errno==EINTR) continue;
        if (ready<0) throw std::runtime_error("PTY poll");
        if (!ready) { driver.clocks(1000); continue; }
        std::vector<uint8_t> part(expected-request.size());
        const auto count=::read(fd,part.data(),part.size());
        if (count<0 && (errno==EAGAIN || errno==EINTR)) continue;
        if (count==0 || (count<0 && errno==EIO)) {
            // llama may load weights before it opens its selected PTY.
            if (!packets) { driver.clocks(1000); continue; }
            break;
        }
        if (count<0) throw std::runtime_error("PTY read");
        request.insert(request.end(),part.begin(),part.begin()+count);
        if (request.size()==48 && expected==48) {
            uint32_t length=0;
            for (unsigned i=0;i<4;++i) length|=uint32_t(request[24+i])<<(8*i);
            if (length>4096) throw std::runtime_error("PTY request extent");
            expected=52+length;
        }
        if (request.size()!=expected) continue;
        const auto response=driver.exchange(hex_bytes(request));
        for (size_t at=0;at<response.size();) {
            pollfd output{fd,POLLOUT,0};
            const int writable=::poll(&output,1,30000);
            if (writable<0 && errno==EINTR) continue;
            if (writable<=0) throw std::runtime_error("PTY response timeout");
            const auto written=::write(fd,response.data()+at,response.size()-at);
            if (written<0 && (errno==EAGAIN || errno==EINTR)) continue;
            if (written<=0) throw std::runtime_error("PTY write");
            at+=size_t(written);
        }
        ++packets;
        request.clear(); expected=48;
    }
    if (!request.empty() || !packets) throw std::runtime_error("PTY incomplete request/session");
    std::cout<<"RTL_PTY_COMPLETE protocol=4 packets="<<packets<<" launches="<<driver.launches
             <<" output_acks="<<driver.output_acks<<" stripe_acks="<<driver.stripe_acks
             <<" physical_access=0 PHY=UART_bits\n";
    driver.stats(std::cout); std::cout<<std::endl;
}

int main(int argc,char **argv) {
    try {
        Driver driver;
        if (argc==3 && std::string(argv[1])=="--pty") {
            serve_pty(driver,std::stoi(argv[2]));
            return 0;
        }
        if (argc!=1) throw std::runtime_error("usage: window_uart_driver [--pty inherited-fd]");
        std::string line;
        while (std::getline(std::cin,line)) {
            if (line=="stats") {
                driver.stats(std::cout); std::cout<<std::endl;
            } else std::cout<<hex_bytes(driver.exchange(line))<<std::endl;
        }
    } catch (const std::exception &error) {
        std::cerr<<error.what()<<std::endl; return 1;
    }
}
