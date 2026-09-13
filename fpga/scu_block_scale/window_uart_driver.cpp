// UART-bit simulation only. No serial device, CPU dot, or expected result.
#include "Vscu_window_uart_shell.h"
#include "Vscu_window_uart_shell___024root.h"
#include "verilated.h"
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

struct Driver {
    VerilatedContext context;
    Vscu_window_uart_shell top{&context};
    std::vector<uint8_t> received;
    uint64_t ticks=0, loads=0, launches=0, output_acks=0, stripe_acks=0;
    int rx_bit=-1, rx_count=0;
    uint8_t rx_byte=0;
    bool previous=true;
    void tick() {
        top.clk=0; top.eval(); context.timeInc(20000);
        if (top.reset_n) {
            const auto &r=*top.rootp;
            loads+=r.scu_window_uart_shell__DOT__do_load!=0;
            launches+=r.scu_window_uart_shell__DOT__do_start;
            output_acks+=r.scu_window_uart_shell__DOT__do_consume;
            stripe_acks+=r.scu_window_uart_shell__DOT__do_stripe_ack;
        }
        top.clk=1; top.eval(); context.timeInc(20000); ++ticks;
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
                std::cout<<"{\"ticks\":"<<driver.ticks<<",\"loads\":"<<driver.loads
                         <<",\"launches\":"<<driver.launches<<",\"output_acks\":"<<driver.output_acks
                         <<",\"stripe_acks\":"<<driver.stripe_acks<<"}"<<std::endl;
            } else std::cout<<hex_bytes(driver.exchange(line))<<std::endl;
        }
    } catch (const std::exception &error) {
        std::cerr<<error.what()<<std::endl; return 1;
    }
}
