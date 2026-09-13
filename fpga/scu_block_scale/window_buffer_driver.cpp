// Standalone actual WindowBuffer RTL test. The oracle uses global word identity,
// independent of the DUT's local address implementation. No physical device.
#include "VmkWindowBufferTest.h"
#include "verilated.h"
#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>
#include <stdexcept>

static void check(bool condition, const char *message) {
    if (!condition) throw std::runtime_error(message);
}

struct Test {
    VerilatedContext context;
    VmkWindowBufferTest top{&context};
    uint64_t cycles = 0, requests = 0, refills = 0;
    unsigned row_log = 0, word_log = 0;

    void eval() { top.eval(); check(!context.gotFinish(), "unexpected RTL finish"); }
    void tick() {
        top.CLK = 0; eval(); context.timeInc(1);
        top.CLK = 1; eval(); context.timeInc(1);
        check(++cycles < 3000000, "watchdog");
    }
    void reset() {
        top.EN_configure = top.EN_request = top.EN_consume = 0;
        top.EN_refill_load = top.EN_refill_commit = 0;
        top.RST_N = 0;
        for (unsigned i = 0; i < 5; ++i) tick();
        top.RST_N = 1; tick(); eval();
        check(!top.error && !top.responseValid, "reset");
    }
    void configure(unsigned r, unsigned w) {
        row_log = r; word_log = w;
        check(top.RDY_configure, "configure ready");
        top.configure_rowLog = r; top.configure_wordLog = w;
        top.EN_configure = 1; tick(); top.EN_configure = 0; eval();
    }
    void request(uint32_t row, uint32_t word, uint32_t limit, uint64_t tag) {
        check(top.RDY_request, "request ready");
        top.request_row = row; top.request_word = word;
        top.request_rowLimit = limit; top.request_tag = tag;
        top.EN_request = 1; tick(); top.EN_request = 0; eval();
        ++requests;
    }
    void load(uint32_t generation, unsigned index) {
        check(top.RDY_refill_load, "load ready");
        const uint32_t row = top.refill_originRow + (index >> word_log);
        const uint32_t word = top.refill_originWord + (index % (1U << word_log));
        top.refill_load_values[0] = row; top.refill_load_values[1] = word;
        top.refill_load_values[2] = ~row; top.refill_load_values[3] = ~word;
        top.refill_load_generation = generation; top.refill_load_index = index;
        top.EN_refill_load = 1; tick(); top.EN_refill_load = 0; eval();
    }
    void commit(uint32_t generation) {
        check(top.RDY_refill_commit, "commit ready");
        top.refill_commit_generation = generation;
        top.EN_refill_commit = 1; tick(); top.EN_refill_commit = 0; eval();
    }
    void access(uint32_t row, uint32_t word, uint32_t limit, uint64_t tag) {
        request(row, word, limit, tag);
        check(!top.error, "valid request rejected");
        if (top.refill_miss) {
            const uint32_t base_row = (row >> row_log) << row_log;
            const uint32_t base_word = (word >> word_log) << word_log;
            const unsigned words = 1U << (row_log + word_log);
            const uint32_t valid_rows = std::min(1U << row_log, limit - base_row);
            const uint32_t generation = top.refill_generation;
            check(top.refill_originRow == base_row && top.refill_originWord == base_word,
                  "full-width origin");
            check(top.refill_words == words && top.refill_validRows == valid_rows, "extent");
            for (unsigned i = 0; i < 4; ++i) {
                tick();
                check(top.refill_miss && top.refill_generation == generation &&
                      top.refill_originRow == base_row && top.refill_originWord == base_word &&
                      !top.responseValid && !top.RDY_request && !top.RDY_configure,
                      "pending refill ownership");
            }
            for (unsigned i = 0; i < words; ++i) load(generation, i);
            check(!top.responseValid, "uncommitted data published");
            commit(generation); ++refills;
        }
        for (unsigned wait = 0; !top.responseValid && wait < 8; ++wait) tick();
        check(top.responseValid && !top.error, "response missing");
        for (unsigned wait = 0; wait < 4; ++wait) {
            check(top.response[0] == row && top.response[1] == word &&
                  top.response[2] == ~row && top.response[3] == ~word,
                  "global data/address identity");
            check(top.responseTag == tag && !top.RDY_request && !top.RDY_configure,
                  "response tag/ownership under backpressure");
            tick();
        }
        top.EN_consume = 1; tick(); top.EN_consume = 0; eval();
        check(!top.responseValid && top.RDY_request, "consumption retirement");
    }
};

int main() {
    try {
        Test test;
        unsigned configurations = 0, negatives = 0;
        uint64_t tag = UINT64_MAX - 100;
        for (unsigned r = 0; r <= 6; ++r) for (unsigned w = 0; w <= 4; ++w) {
            if (r + w > 8) continue;
            test.reset(); test.configure(r, w); ++configurations;
            // Every local word, with high-bit origins and UINT32_MAX row limit.
            for (uint32_t high : {0U, 0x7fffff00U, 0xfffffe00U}) {
                for (unsigned row = 0; row < (1U << r); ++row)
                    for (unsigned word = 0; word < (1U << w); ++word)
                        test.access(high + row, high + word, UINT32_MAX, tag++);
                // Cross both window edges; then a short final row extent.
                test.access(high + (1U << r), high + (1U << w), UINT32_MAX, tag++);
                test.access(high + (2U << r), high, high + (2U << r) + 1, tag++);
            }
            // Highest addressable row and word preserve their global bits.
            test.access(UINT32_MAX - 1, UINT32_MAX, UINT32_MAX, tag++);
        }
        for (auto config : {std::array<unsigned, 2>{7, 0}, {0, 5}, {6, 4}}) {
            test.reset(); test.configure(config[0], config[1]);
            check(test.top.error && !test.top.RDY_request, "bad configuration accepted"); ++negatives;
        }
        for (unsigned kind = 0; kind < 4; ++kind) {
            test.reset(); test.configure(2, 2);
            test.request(kind == 3 ? 8 : 0, 0, 8, UINT64_MAX);
            if (kind == 0) test.load(test.top.refill_generation + 1, 0);
            if (kind == 1) test.load(test.top.refill_generation, 1);
            if (kind == 2) test.commit(test.top.refill_generation);
            check(test.top.error && !test.top.responseValid && !test.top.RDY_request,
                  "bad generation/index/commit/row bounds accepted"); ++negatives;
        }
        std::cout << "WINDOW_BUFFER_RTL_PASS configurations=" << configurations
                  << " requests=" << test.requests << " refills=" << test.refills
                  << " negatives=" << negatives << " physical_access=0\n";
    } catch (const std::exception &error) {
        std::cerr << "WINDOW_BUFFER_RTL_FAIL " << error.what() << '\n'; return 1;
    }
}
