// Direct synthesis-provider RTL test. UART is bypassed; no native simulator,
// backend, floating-point merge, or physical device is involved.
#include "VmkScuPipeline.h"
#include "verilated.h"
#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

static void require(bool value, const std::string &message) {
    if (!value) throw std::runtime_error(message);
}
static int32_t clamp32(int64_t value) {
    return int32_t(std::clamp(value, int64_t(INT32_MIN), int64_t(INT32_MAX)));
}
static unsigned stride_log(unsigned extent) {
    unsigned value = 4;
    while ((uint64_t(1) << value) < extent) ++value;
    return value;
}
struct Shape { unsigned m, n, k; bool saturation = false; unsigned op = 4; unsigned pattern = 0; };

// Generated inputs are shared by the transport and a separate scalar oracle.
// Only RTL output is collected; these functions never supply an output value.
static int8_t activation(const Shape &s, unsigned row, unsigned k) {
    if (row >= s.m || k >= s.k) return 0;
    if (s.saturation) return (row + k / 16) % 3 ? 127 : -128;
    return int8_t(int((row * 13 + k * 7 + k / 32 * 11 + 3) % 15) - 7);
}
static int8_t weight(const Shape &s, unsigned k, unsigned column) {
    if (k >= s.k || column >= s.n) return 0;
    if (s.saturation) return (column + k / 16) % 4 ? 127 : -128;
    return int8_t(int((k * 3 + column * 11 + k / 32 * 7 + 5) % 13) - 6);
}
static uint32_t scale(const Shape &s, unsigned block, unsigned column) {
    if (block >= (s.k + 31) / 32 || column >= s.n) return 0;
    // First-block-only and all-zero jobs expose stale accumulation across jobs.
    if (s.pattern) {
        const bool nonzero = s.pattern == 1 && block == 0;
        return s.op == 5 ? (nonzero ? 0 : 0x80000000U) : unsigned(nonzero);
    }
    if (s.op == 5) {
        static constexpr uint32_t shifts[] = {0x80000000U, 0, 1, 7, 15, 30, 31, 32, 127, 32767};
        return shifts[(block * 7 + column * 3) % 10];
    }
    static constexpr uint32_t factors[] = {0, 1, 2, 3, 5, 17, 255, 256, 513, 65790};
    return s.saturation ? 65790 : factors[(block * 7 + column * 3) % 10];
}
static std::vector<int32_t> oracle(const Shape &s) {
    std::vector<int32_t> result(s.m * s.n);
    for (unsigned row = 0; row < s.m; ++row)
        for (unsigned column = 0; column < s.n; ++column) {
            int32_t accumulator = 0;
            for (unsigned start = 0; start < s.k; start += 16) {
                int64_t dot = 0;
                for (unsigned k = start; k < std::min(start + 16, s.k); ++k)
                    dot += int(activation(s, row, k)) * int(weight(s, k, column));
                const uint32_t metadata = scale(s, start / 32, column);
                int32_t contribution;
                if (s.op == 5) {
                    if (!dot || metadata == 0x80000000U) contribution = 0;
                    else if (metadata >= 32) contribution = dot < 0 ? INT32_MIN : INT32_MAX;
                    else contribution = clamp32(dot * (int64_t(1) << metadata));
                } else contribution = clamp32(dot * metadata);
                accumulator = clamp32(int64_t(accumulator) + contribution);
            }
            result[row * s.n + column] = accumulator;
        }
    return result;
}

struct Window {
    CData &miss;
    IData &origin_row, &origin_word;
    SData &words;
    IData &generation, &load_generation;
    SData &load_index;
    VlWide<4> &load_values;
    CData &load_enable, &load_ready;
    IData &commit_generation;
    CData &commit_enable, &commit_ready;
};
#define WINDOW(name) Window{top.name##_miss, top.name##_originRow, top.name##_originWord, \
    top.name##_words, top.name##_generation, top.name##_load_generation, \
    top.name##_load_index, top.name##_load_values, top.EN_##name##_load, \
    top.RDY_##name##_load, top.name##_commit_generation, \
    top.EN_##name##_commit, top.RDY_##name##_commit}

struct Test {
    VerilatedContext context;
    VmkScuPipeline top{&context};
    Shape shape;
    unsigned window_k_log, window_n_log, delay, n_log;
    uint64_t ticks = 0, output_requests = 0;
    std::array<unsigned, 3> refills{};
    std::vector<int32_t> observed;
    std::vector<bool> seen;
    bool live = false;
    unsigned publications = 0, completions = 0;

    Test(Shape value, unsigned k_log, unsigned columns_log, unsigned wait, bool striped = false)
        : shape(value), window_k_log(k_log), window_n_log(columns_log), delay(wait),
          n_log(stride_log(value.n)), observed(value.m * value.n), seen(value.m * value.n), live(striped) {}

    Window window(unsigned kind) {
        if (kind == 0) return WINDOW(aWindow);
        if (kind == 1) return WINDOW(wWindow);
        return WINDOW(sWindow);
    }
    void eval() {
        top.eval();
        require(!context.gotFinish(), "unexpected RTL assertion/$finish");
    }
    void tick() {
        top.CLK = 0; eval(); context.timeInc(20000);
        top.CLK = 1; eval(); context.timeInc(20000);
        require(++ticks < 3000000, "provider watchdog");
    }
    void clocks(unsigned count) { while (count--) tick(); }
    template<class Predicate> void until(Predicate ready) {
        while (!ready()) tick();
    }
    void reset() {
        top.EN_start = top.EN_startLogical = top.EN_publish = top.EN_publishLogical = 0;
        top.EN_loadActivation = top.EN_loadWeight = top.EN_loadScale = 0;
        top.EN_requestOutput = top.EN_consumeOutput = top.EN_acknowledge = 0;
        top.EN_acknowledgeStripe = top.EN_consumeStreamOutput = 0;
        for (unsigned kind = 0; kind < 3; ++kind) {
            auto port = window(kind);
            port.load_enable = port.commit_enable = 0;
        }
        top.RST_N = 0; clocks(16); top.RST_N = 1; clocks(16); eval();
        require(!top.protocolError && !top.done, "reset status");
    }
    void start() {
        until([&] { return top.RDY_startLogical; });
        output_requests = publications = completions = 0;
        refills.fill(0);
        std::fill(observed.begin(), observed.end(), 0);
        std::fill(seen.begin(), seen.end(), false);
        top.startLogical_striped = live;
        top.startLogical_m = shape.m; top.startLogical_n = shape.n; top.startLogical_k = shape.k;
        top.startLogical_kStrideLog = stride_log(shape.k); top.startLogical_nStrideLog = n_log;
        top.startLogical_windowKLog = window_k_log; top.startLogical_windowNLog = window_n_log;
        top.startLogical_op = shape.op; // Typed SCU domain2, signed-scu-sat-v2.
        top.startLogical_job = 1; top.startLogical_contextId = 0x123456789ULL;
        top.EN_startLogical = 1; tick(); top.EN_startLogical = 0; eval();
    }
    std::array<uint32_t, 4> data(unsigned kind, unsigned row_origin,
                                 unsigned word_origin, unsigned index) const {
        const unsigned words_per_row = 1U << (kind == 0 ? window_k_log - 4 :
                                              kind == 1 ? window_n_log - 4 : window_n_log - 2);
        const unsigned row = row_origin + index / words_per_row;
        const unsigned word = word_origin + index % words_per_row;
        std::array<uint32_t, 4> values{};
        if (kind == 2) {
            for (unsigned lane = 0; lane < 4; ++lane)
                values[lane] = scale(shape, row, word * 4 + lane);
        } else {
            for (unsigned lane = 0; lane < 16; ++lane) {
                const auto value = kind == 0 ? (row < top.publishedRows ? activation(shape, row, word * 16 + lane) : 0)
                                             : weight(shape, row, word * 16 + lane);
                values[lane / 4] |= uint32_t(uint8_t(value)) << (8 * (lane % 4));
            }
        }
        return values;
    }
    void load(unsigned kind, uint32_t generation, unsigned index,
              const std::array<uint32_t, 4> &values) {
        auto port = window(kind);
        until([&] { return port.load_ready; });
        port.load_generation = generation; port.load_index = index;
        for (unsigned lane = 0; lane < 4; ++lane) port.load_values[lane] = values[lane];
        port.load_enable = 1; tick(); port.load_enable = 0; eval();
    }
    void commit(unsigned kind, uint32_t generation) {
        auto port = window(kind);
        until([&] { return port.commit_ready; });
        port.commit_generation = generation; port.commit_enable = 1;
        tick(); port.commit_enable = 0; eval();
    }
    void refill(unsigned kind) {
        auto port = window(kind);
        const unsigned row = port.origin_row, word = port.origin_word, words = port.words;
        const uint32_t generation = port.generation;
        const unsigned expected_words = kind == 0 ? 1U << window_k_log :
            kind == 1 ? 1U << (window_k_log + window_n_log - 4) :
                        1U << (window_k_log - 5 + window_n_log - 2);
        require(words == expected_words && words <= 256, "resident window exceeds configured capacity");
        for (unsigned index = 0; index < words; ++index) {
            if (delay && index % 7 == 0) clocks(delay);
            require(port.miss && port.generation == generation && port.origin_row == row &&
                    port.origin_word == word && port.words == words, "window request changed before commit");
            load(kind, generation, index, data(kind, row, word, index));
            require(!top.protocolError, "valid refill rejected");
        }
        clocks(delay + 1);
        commit(kind, generation);
        ++refills[kind];
        require(!top.protocolError, "valid window commit rejected");
    }
    void output() {
        const uint64_t address = top.streamOutputAddress, tag = top.streamOutputTag;
        const unsigned count = top.streamOutputCount;
        std::array<uint32_t, 16> values{};
        for (unsigned lane = 0; lane < 16; ++lane) values[lane] = top.streamOutputValues[lane];
        clocks(delay + 3);
        require(top.streamOutputValid && top.streamOutputAddress == address &&
                top.streamOutputTag == tag && top.streamOutputCount == count,
                "output descriptor changed under backpressure");
        const unsigned row = address >> (n_log + 2);
        const unsigned column = (address & ((uint64_t(4) << n_log) - 1)) / 4;
        require(address % 64 == 0 && row < shape.m && column < shape.n &&
                count == std::min(16U, shape.n - column), "output logical address/count");
        for (unsigned lane = 0; lane < 16; ++lane) {
            require(top.streamOutputValues[lane] == values[lane], "output values changed under backpressure");
            if (lane < count) {
                const unsigned at = row * shape.n + column + lane;
                require(!seen[at], "duplicate logical output");
                observed[at] = int32_t(values[lane]); seen[at] = true;
            }
        }
        require(top.RDY_consumeStreamOutput, "stream consume not ready");
        top.consumeStreamOutput_tag = tag; top.EN_consumeStreamOutput = 1;
        tick(); top.EN_consumeStreamOutput = 0; eval();
        ++output_requests;
    }
    void finish() {
        while (!top.done) {
            if (live && top.stripeCompletionValid) {
                require(top.stripeId == completions && top.stripeRowBegin == completions * 7 &&
                        top.stripeRowCount == std::min(7U, shape.m - completions * 7),
                        "SCU live semantic completion identity");
                top.EN_acknowledgeStripe = 1; tick(); top.EN_acknowledgeStripe = 0; eval();
                ++completions;
                clocks(delay + 11);
            }
            if (live && publications == completions && top.publishedRows < shape.m && top.RDY_publishLogical) {
                top.publishLogical_rowBegin = top.publishedRows;
                top.publishLogical_rowCount = std::min(7U, shape.m - top.publishedRows);
                top.EN_publishLogical = 1; tick(); top.EN_publishLogical = 0; eval();
                ++publications;
            }
            for (unsigned kind = 0; kind < 3; ++kind)
                if (window(kind).miss) refill(kind);
            if (top.streamOutputValid) output();
            require(!top.protocolError, "provider protocol error");
            tick();
        }
        const auto expected = oracle(shape);
        for (unsigned at = 0; at < expected.size(); ++at)
            require(seen[at] && observed[at] == expected[at],
                    "independent integer mismatch at " + std::to_string(at) +
                    " actual=" + std::to_string(observed[at]) + " expected=" + std::to_string(expected[at]));
        const uint64_t works = uint64_t(live ? (shape.m + 6) / 7 : (shape.m + 15) / 16) * ((shape.n + 15) / 16);
        const uint64_t writes = uint64_t(shape.m) * ((shape.n + 15) / 16);
        require(top.works == works && top.fragments == works * ((shape.k + 15) / 16), "work/fragment counters");
        require(top.outputWrites == writes && top.outputAcks == writes && output_requests == writes,
                "final output write/ack counters");
        require(!top.streamOutputValid, "done before final output drain");
        if (live) require(publications == (shape.m + 6) / 7 && completions == publications &&
                          top.firstActivationPublishedRows == std::min(7U, shape.m),
                          "SCU live publication/first-A coverage");
        uint64_t checksum = 1469598103934665603ULL;
        for (int32_t value : observed) checksum = (checksum ^ uint32_t(value)) * 1099511628211ULL;
        std::cout << "{\"kind\":\"positive\",\"op\":" << shape.op << ",\"mode\":\""
                  << (live ? "STRIPE_PIPELINE" : "FULL") << "\",\"stripes\":" << publications
                  << ",\"M\":" << shape.m << ",\"N\":" << shape.n
                  << ",\"K\":" << shape.k << ",\"saturation\":" << (shape.saturation ? "true" : "false")
                  << ",\"pattern\":" << shape.pattern
                  << ",\"window_K\":" << (1U << window_k_log) << ",\"window_N\":" << (1U << window_n_log)
                  << ",\"delay\":" << delay << ",\"cycles\":" << top.cycles
                  << ",\"works\":" << top.works << ",\"fragments\":" << top.fragments
                  << ",\"writes\":" << output_requests << ",\"comparisons\":" << expected.size()
                  << ",\"A_refills\":" << refills[0] << ",\"W_refills\":" << refills[1]
                  << ",\"S_refills\":" << refills[2] << ",\"checksum\":" << checksum
                  << ",\"status\":\"PASS\"}" << std::endl;
        require(top.RDY_acknowledge, "final acknowledge not ready");
        top.EN_acknowledge = 1; tick(); top.EN_acknowledge = 0; eval();
        require(!top.done, "acknowledge did not release job");
    }
    void negative(const std::string &kind) {
        reset(); start();
        until([&] { return top.aWindow_miss; });
        auto port = window(0);
        const uint32_t generation = port.generation;
        const auto first = data(0, port.origin_row, port.origin_word, 0);
        if (kind == "stale_generation") load(0, generation - 1, 0, first);
        else if (kind == "duplicate_index") {
            load(0, generation, 0, first); load(0, generation, 0, first);
        } else if (kind == "incomplete_commit") {
            load(0, generation, 0, first); commit(0, generation);
        } else throw std::runtime_error("unknown negative");
        clocks(4);
        require(top.protocolError, "malformed refill did not fail closed: " + kind);
        clocks(100);
        require(!top.done && !top.streamOutputValid, "malformed refill produced completion/output");
        std::cout << "{\"kind\":\"negative\",\"case\":\"" << kind << "\",\"status\":\"PASS\"}" << std::endl;
    }
    void invalid_carrier(uint32_t carrier, unsigned column) {
        reset(); start();
        while (!top.sWindow_miss || !refills[0] || !refills[1]) {
            for (unsigned kind = 0; kind < 2; ++kind)
                if (window(kind).miss) refill(kind);
            require(!top.protocolError, "valid inputs rejected before carrier injection");
            tick();
        }
        auto port = window(2);
        const uint32_t generation = port.generation;
        require(port.origin_row == 0 && port.origin_word == 0 && port.words == 4,
                "invalid-carrier scale window coverage");
        for (unsigned index = 0; index < port.words; ++index) {
            auto values = data(2, 0, 0, index);
            if (index == column / 4) values[column % 4] = carrier;
            load(2, generation, index, values);
        }
        require(!top.protocolError, "valid scale upload rejected before consumption");
        commit(2, generation);
        clocks(64);
        for (unsigned cycle = 0; cycle < 257; ++cycle) {
            require(top.protocolError, "invalid carrier did not fail closed or error cleared");
            require(!top.done && !top.streamOutputValid && !top.stripeCompletionValid &&
                    !top.fragments && !top.works && !top.outputWrites && !top.outputAcks,
                    "invalid carrier produced work/completion/output");
            tick();
        }
        std::cout << "{\"kind\":\"negative\",\"case\":\"invalid_carrier\",\"op\":" << shape.op
                  << ",\"carrier\":" << carrier << ",\"column\":" << column
                  << ",\"status\":\"PASS\"}" << std::endl;
    }
    void omitted_commit() {
        reset(); start();
        until([&] { return top.aWindow_miss; });
        auto port = window(0);
        const uint32_t generation = port.generation;
        const unsigned row = port.origin_row, word = port.origin_word, words = port.words;
        for (unsigned index = 0; index < words; ++index)
            load(0, generation, index, data(0, row, word, index));
        for (unsigned kind = 1; kind < 3; ++kind)
            if (window(kind).miss) refill(kind);
        clocks(257);
        require(port.miss && port.generation == generation && !top.done &&
                !top.streamOutputValid && !top.protocolError, "uncommitted A window was consumed");
        commit(0, generation); ++refills[0]; finish();
        std::cout << "{\"kind\":\"negative\",\"case\":\"omitted_commit_then_resume\",\"status\":\"PASS\"}" << std::endl;
    }
    void extent_start(unsigned m, unsigned n, unsigned k, unsigned kl, unsigned nl,
                      unsigned op, bool valid) {
        reset();
        top.startLogical_striped = 1; // Descriptor-only: no stripe/A publication.
        top.startLogical_m = m; top.startLogical_n = n; top.startLogical_k = k;
        top.startLogical_kStrideLog = kl; top.startLogical_nStrideLog = nl;
        top.startLogical_windowKLog = 6; top.startLogical_windowNLog = 6;
        top.startLogical_op = op; top.startLogical_job = 1; top.startLogical_contextId = 1;
        require(top.RDY_startLogical, "extent probe not ready");
        top.EN_startLogical = 1; tick(); top.EN_startLogical = 0; clocks(32);
        require(bool(top.protocolError) == !valid && !top.done && !top.streamOutputValid &&
                !top.activationRequests && !top.weightRequests && !top.outputWrites,
                "logical extent acceptance/overflow guard");
        std::cout << "{\"kind\":\"extent\",\"M\":" << m << ",\"N\":" << n
                  << ",\"K\":" << k << ",\"op\":" << op << ",\"accepted\":" << valid
                  << ",\"status\":\"PASS\"}" << std::endl;
    }
};
#undef WINDOW

int main(int argc, char **argv) {
    try {
        require(argc == 1, "bounded_driver takes no arguments; run the fixed acceptance matrix");
        (void)argv;
        // Three positive saturated fragments followed by one negative fragment.
        require(oracle({1,1,64,true})[0] == -1, "scalar saturation-order anchor");
        const Shape shapes[] = {{16,48,192}, {16,96,96}, {16,96,192}, {13,79,191},
                                {1,1,1}, {17,33,65}, {16,96,192,true}};
        unsigned jobs = 0;
        for (const Shape shape : shapes) {
            std::vector<int32_t> first;
            for (const auto configuration : {std::array<unsigned,3>{5,4,0}, {6,5,2}, {5,6,5}, {6,6,1}}) {
                Test test(shape, configuration[0], configuration[1], configuration[2]);
                test.reset(); test.start(); test.finish();
                if (first.empty()) first = test.observed;
                else require(test.observed == first, "chunk/delay invariance mismatch");
                ++jobs;
            }
        }
        for (const Shape shape : {Shape{16,96,192,false,5}, Shape{13,79,191,true,5},
                                  Shape{35,79,192,false,5}}) {
            std::vector<int32_t> first;
            for (const auto configuration : {std::array<unsigned,3>{5,4,0}, {6,6,3}}) {
                for (bool live : {false, true}) {
                    Test test(shape, configuration[0], configuration[1], configuration[2], live);
                    test.reset(); test.start(); test.finish();
                    if (first.empty()) first = test.observed;
                    else require(first == test.observed, "HP1 full/live/window invariance mismatch");
                    ++jobs;
                }
            }
        }
        for (unsigned op : {5U, 4U}) {
            Test consecutive({17,19,96,false,op}, 5, 4, 2);
            consecutive.reset(); consecutive.start(); consecutive.finish();
            consecutive.shape.pattern = 1;
            consecutive.live = true;
            consecutive.start(); consecutive.finish();
            require(std::any_of(consecutive.observed.begin(), consecutive.observed.end(),
                               [](int32_t value) { return value != 0; }), "first block anchor is zero");
            consecutive.shape.pattern = 2;
            consecutive.live = false;
            consecutive.start(); consecutive.finish();
            jobs += 3;
            std::cout << "{\"kind\":\"consecutive_invocations\",\"op\":" << op
                      << ",\"jobs\":3,\"resets\":1,\"status\":\"PASS\"}" << std::endl;
        }
        for (const char *kind : {"stale_generation", "duplicate_index", "incomplete_commit"}) {
            Test test({16,48,192}, 5, 4, 0);
            test.negative(kind);
        }
        Test resumed({16,48,192}, 5, 4, 0);
        resumed.omitted_commit();
        // First/final quartet cover latched and immediate carrier rejection.
        for (const auto invalid : {std::array<unsigned,3>{4,65791,0}, {4,0x80000000U,15},
                                   {5,32768,0}, {5,0x80000001U,15}}) {
            Test test({1,16,16,false,invalid[0]}, 5, 4, 0);
            test.invalid_carrier(invalid[1], invalid[2]);
        }
        for (const auto e : {std::array<unsigned,7>{1,128256,2048,11,17,3,1},
                 {UINT32_MAX,128256,2048,11,17,3,1}, {1,65537,192,8,17,3,1},
                 {16,96,65568,17,7,3,1}, {31,1U<<31,1U<<31,31,31,3,1},
                 {32,1U<<31,1U<<31,31,31,3,0}, {32,1U<<31,1U<<31,31,31,0,1},
                 {1,(1U<<31)+1,32,5,31,3,0}, {1,16,(1U<<31)+1,31,4,3,0},
                 {1,128256,32,5,16,3,0}, {0,16,32,5,4,3,0}}) {
            Test test({1,1,1}, 5,4,0);
            test.extent_start(e[0],e[1],e[2],e[3],e[4],e[5],e[6]);
        }
        std::cout << "BOUNDED_PROVIDER_RTL_PASS jobs=" << jobs
                  << " negatives=8 extent_checks=11 uart_bypassed=1 physical_access=0" << std::endl;
    } catch (const std::exception &error) {
        std::cerr << "BOUNDED_PROVIDER_RTL_FAIL " << error.what() << std::endl;
        return 1;
    }
}
