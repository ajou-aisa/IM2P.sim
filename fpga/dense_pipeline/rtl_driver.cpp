// Real UART bits and an autonomous 25 MHz core clock. No expected results.
#include "Vdense_uart_shell.h"
#include "Vdense_uart_shell___024root.h"
#include "verilated.h"
#include <cerrno>
#include <csignal>
#include <cstring>
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <unistd.h>
#include <vector>

namespace {
constexpr size_t response_header = 144;
constexpr size_t maximum_payload = 4 * 336 * 48 * 3;
constexpr size_t maximum_response = response_header + maximum_payload + 4;
volatile sig_atomic_t stopped = 0;
void require(bool condition, const char *message) {
    if (!condition) throw std::runtime_error(message);
}
uint64_t number(const std::string &text) {
    size_t end = 0;
    const auto value = std::stoull(text, &end);
    require(!text.empty() && text.front() != '-' && end == text.size(), "invalid integer");
    return value;
}

struct Driver {
    VerilatedContext context;
    Vdense_uart_shell top{&context};
    std::vector<uint8_t> received;
    uint64_t ticks = 0, launches = 0, publications = 0, stripe_acks = 0;
    int rx_count = 0, rx_bit = -1;
    uint8_t rx_byte = 0;
    bool previous = true;
    std::ofstream host_trace, device_trace, events;

    void record(const char *event) {
        if (!events.is_open()) return;
        const auto &root = *top.rootp;
        events << "{\"event\":\"" << event << "\",\"tick\":" << ticks
               << ",\"run_id\":" << root.dense_uart_shell__DOT__run_id
               << ",\"generation\":" << root.dense_uart_shell__DOT__generation
               << ",\"launches\":" << launches << ",\"publications\":" << publications
               << ",\"stripe_acks\":" << stripe_acks << "}\n";
        require(bool(events), "event trace write failed");
    }
    void tick() {
        top.clk = 0; top.eval(); context.timeInc(20000);
        if (context.gotFinish()) throw std::runtime_error("unexpected RTL $finish");
        if (top.reset_n) {
            const auto &root = *top.rootp;
            if (root.dense_uart_shell__DOT__do_start) { ++launches; record("start"); }
            if (root.dense_uart_shell__DOT__do_publish) { ++publications; record("publish"); }
            if (root.dense_uart_shell__DOT__do_stripe_ack) { ++stripe_acks; record("stripe_ack"); }
        }
        top.clk = 1; top.eval(); context.timeInc(20000); ++ticks;
        if (context.gotFinish()) throw std::runtime_error("unexpected RTL $finish");
        const bool value = top.uart_tx;
        if (rx_bit < 0) {
            if (previous && !value) { rx_bit = 0; rx_count = 37; rx_byte = 0; }
        } else if (--rx_count == 0) {
            if (rx_bit < 8) { rx_byte |= uint8_t(value) << rx_bit++; rx_count = 25; }
            else {
                require(value, "UART stop bit");
                require(received.size() < 2 * maximum_response, "bounded UART receive storage exhausted");
                received.push_back(rx_byte);
                if (device_trace.is_open()) {
                    device_trace.put(char(rx_byte));
                    require(bool(device_trace), "device byte trace failed");
                }
                rx_bit = -1;
            }
        }
        previous = value;
    }
    void clocks(uint64_t count) { for (uint64_t i = 0; i < count; ++i) tick(); }
    void byte(uint8_t value) {
        if (host_trace.is_open()) {
            host_trace.put(char(value));
            require(bool(host_trace), "host byte trace failed");
        }
        top.uart_rx = 0; clocks(25);
        for (unsigned bit = 0; bit < 8; ++bit) { top.uart_rx = (value >> bit) & 1; clocks(25); }
        top.uart_rx = 1; clocks(25);
    }
    void reset() {
        // This is called only by initial simulation setup or explicit @reset.
        received.clear(); rx_bit = -1; rx_count = 0; previous = true;
        top.uart_rx = 1; top.reset_n = 0; clocks(16);
        top.reset_n = 1; clocks(16);
        record("reset");
    }
    Driver() { reset(); }
    void trace(const std::filesystem::path &directory) {
        require(std::filesystem::create_directory(directory), "trace directory must be fresh");
        host_trace.open(directory / "host-uart.bin", std::ios::binary);
        device_trace.open(directory / "device-uart.bin", std::ios::binary);
        events.open(directory / "events.jsonl");
        require(bool(host_trace) && bool(device_trace) && bool(events), "trace open failed");
    }
};

void pty(Driver &driver, int fd) {
    const int flags = fcntl(fd, F_GETFL);
    require(flags >= 0 && fcntl(fd, F_SETFL, flags | O_NONBLOCK) >= 0, "PTY flags");
    signal(SIGTERM, [](int) { stopped = 1; });
    signal(SIGINT, [](int) { stopped = 1; });
    size_t emitted = 0;
    uint64_t input_bytes = 0, output_bytes = 0;
    while (!stopped) {
        uint8_t bytes[256];
        const auto count = read(fd, bytes, sizeof(bytes));
        if (count > 0) {
            for (ssize_t i = 0; i < count; ++i) driver.byte(bytes[i]);
            input_bytes += uint64_t(count);
        } else if (count < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EIO && errno != EINTR) {
            throw std::runtime_error(std::strerror(errno));
        }
        // Normal producer delay never pauses the physical clock model.
        driver.clocks(256);
        while (emitted < driver.received.size()) {
            const auto sent = write(fd, driver.received.data() + emitted, driver.received.size() - emitted);
            if (sent < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EIO || errno == EINTR)) break;
            require(sent > 0, "PTY write failed");
            emitted += size_t(sent); output_bytes += uint64_t(sent);
        }
        if (emitted == driver.received.size()) { driver.received.clear(); emitted = 0; }
    }
    std::cout << "RTL_PTY_COMPLETE input_bytes=" << input_bytes << " output_bytes=" << output_bytes
              << " launches=" << driver.launches << " publications=" << driver.publications
              << " stripe_acks=" << driver.stripe_acks << " ticks=" << driver.ticks
              << " pending_output_bytes=" << driver.received.size() - emitted << std::endl;
}

void itinerary(Driver &driver, const std::string &path) {
    std::ifstream plan(path); require(bool(plan), "itinerary missing");
    std::string request, response;
    size_t transactions = 0;
    while (plan >> request >> response) {
        if (request == "@reset") { driver.reset(); continue; }
        if (request == "@clocks") {
            const auto clocks = number(response);
            require(clocks <= 100000000, "explicit clock wait exceeds test bound");
            driver.clocks(clocks); continue;
        }
        require(driver.received.empty(), "stale unsolicited UART response before request");
        std::ifstream input(request, std::ios::binary); require(bool(input), "request missing");
        std::vector<uint8_t> bytes(std::istreambuf_iterator<char>(input), {});
        require(!bytes.empty() && bytes.size() <= 65536, "request file bounds");
        for (auto value : bytes) driver.byte(value);
        const uint64_t deadline = driver.ticks + 250 * maximum_response + 2000000;
        size_t length = response_header + 4;
        while (driver.received.size() < length) {
            driver.tick();
            require(driver.ticks <= deadline, "RTL UART timeout");
            if (driver.received.size() >= response_header) {
                uint32_t payload = 0;
                for (unsigned i = 0; i < 4; ++i) payload |= uint32_t(driver.received[20 + i]) << (8 * i);
                require(payload <= maximum_payload, "RTL output capacity");
                length = response_header + payload + 4;
            }
        }
        driver.clocks(300);
        require(driver.received.size() == length, "unexpected trailing response");
        require(!std::filesystem::exists(response), "response artifact already exists");
        std::ofstream output(response, std::ios::binary);
        output.write(reinterpret_cast<const char *>(driver.received.data()), driver.received.size());
        require(bool(output), "response write failed");
        require(!std::filesystem::exists(response + ".events.json"), "event artifact already exists");
        std::ofstream events(response + ".events.json");
        events << "{\"ticks\":" << driver.ticks << ",\"launches\":" << driver.launches
               << ",\"publications\":" << driver.publications << ",\"stripe_acks\":" << driver.stripe_acks << "}\n";
        require(bool(events), "event write failed");
        driver.received.clear(); ++transactions;
        std::cout << "RTL_PACKET transaction=" << transactions << " bytes=" << length
                  << " ticks=" << driver.ticks << " launches=" << driver.launches
                  << " publications=" << driver.publications << " stripe_acks=" << driver.stripe_acks << std::endl;
    }
    require(plan.eof(), "invalid itinerary");
    require(transactions != 0, "no transactions");
    std::cout << "RTL_PACKET_COMPLETE transactions=" << transactions << " launches=" << driver.launches
              << " publications=" << driver.publications << " stripe_acks=" << driver.stripe_acks << std::endl;
}
} // namespace

int main(int argc, char **argv) {
    bool is_pty = false;
    try {
        require(argc >= 2, "usage: rtl_driver itinerary.txt | inherited-master-fd | --pty fd [--trace directory]");
        std::string input = argv[1];
        int next = 2;
        if (input == "--pty") { require(argc >= 3, "missing PTY fd"); input = argv[2]; next = 3; is_pty = true; }
        else is_pty = input.find_first_not_of("0123456789") == std::string::npos;
        Driver driver;
        if (next < argc) {
            require(next + 2 == argc && std::string(argv[next]) == "--trace", "invalid trace arguments");
            driver.trace(argv[next + 1]);
        } else if (const char *trace = std::getenv("IM2P_RTL_TRACE_DIR")) driver.trace(trace);
        if (is_pty) {
            const auto descriptor = number(input);
            require(descriptor <= INT32_MAX, "PTY fd bounds");
            pty(driver, int(descriptor));
        } else itinerary(driver, input);
    } catch (const std::exception &error) {
        std::cerr << (is_pty ? "RTL_PTY_FAIL " : "RTL_FAIL: ") << error.what() << std::endl;
        return 1;
    }
}
