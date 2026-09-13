// Explicit clock-driven transport for the synthesis provider/core. Weight and
// scale callbacks decode existing native data; only RTL supplies dot products.
#include "rtl_plugin.hpp"
#include "rtl_identity.hpp"
#include "VmkScuPipeline.h"
#include "verilated.h"
#include <algorithm>
#include <array>
#include <cstring>
#include <deque>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>

namespace {
void check(bool value, const char *message) {
    if (!value) throw std::runtime_error(message);
}
struct Window {
    CData &miss;
    IData &row, &word;
    SData &words;
    IData &generation, &load_generation;
    SData &load_index;
    VlWide<4> &values;
    CData &load_enable, &load_ready;
    IData &commit_generation;
    CData &commit_enable, &commit_ready;
};
#define WINDOW(name) Window{top.name##_miss, top.name##_originRow, top.name##_originWord, \
    top.name##_words, top.name##_generation, top.name##_load_generation, \
    top.name##_load_index, top.name##_load_values, top.EN_##name##_load, \
    top.RDY_##name##_load, top.name##_commit_generation, \
    top.EN_##name##_commit, top.RDY_##name##_commit}

struct Device {
    VerilatedContext context;
    VmkScuPipeline top{&context};
    std::string failure;
    im2p_matmul_desc_t desc{};
    im2p_scu_rtl_observation_v1 observed{};
    im2p_scu_rtl_output_observer_v1 observer = nullptr;
    void *observer_context = nullptr;
    std::deque<im2p_activation_stripe_t> stripes;
    std::deque<im2p_stripe_completion_extended_t> completions;
    uint64_t ticks = 0, progress_tick = 0, progress_mark = 0;
    size_t published = 0, retired = 0, expected_stripes = 0;
    size_t output_i = 0, output_j = 0, output_block = 0, output_row = 0;
    size_t output_count = 0;
    unsigned n_log = 4;
    bool active = false, striped = false, failed = false;
    static constexpr unsigned window_k_log = 6, window_n_log = 6;

    Device() { reset(); }
    Window window(unsigned kind) {
        if (kind == 0) return WINDOW(aWindow);
        if (kind == 1) return WINDOW(wWindow);
        return WINDOW(sWindow);
    }
    void eval() {
        top.eval();
        check(!context.gotFinish(), "production RTL assertion or finish");
    }
    void tick() {
        top.CLK = 0; eval(); context.timeInc(20000);
        top.CLK = 1; eval(); context.timeInc(20000);
        ++ticks;
        check(!top.protocolError, "production provider protocol error");
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
        top.RST_N = 0;
        for (unsigned i = 0; i < 16; ++i) tick();
        top.RST_N = 1;
        for (unsigned i = 0; i < 16; ++i) tick();
        eval();
    }
    static bool valid(const im2p_matmul_desc_t &d) {
        return d.abi_version == IM2P_ABI_VERSION && d.activation_bits == 8 &&
            d.weight_bits == 8 && d.activation_storage_bytes == 1 &&
            d.weight_storage_bytes == 1 && d.dim == 16 &&
            im2p_scu_logical_extent_valid(d.m, d.n, d.k, d.vector_op == IM2P_VECTOR_EXTERNAL) &&
            d.tile_i_rows == std::min(size_t(16), d.m) &&
            d.tile_j_columns == std::min(size_t(16), d.n) &&
            d.weight_row_stride_bytes >= d.n && d.output_row_stride >= d.n &&
            ((d.vector_op == IM2P_VECTOR_EXTERNAL && d.output_domain == IM2P_OUTPUT_LEGACY_BLOCK &&
              d.block_size == 32 && d.k % 32 == 0 && d.provider.read_scale) ||
             (d.vector_op == IM2P_VECTOR_BYPASS && d.output_domain == IM2P_OUTPUT_LEGACY_FINAL)) &&
            d.provider.read_weight_i8 && !d.provider.read_weight_i16 && d.provider.write_output;
    }
    static bool activation_layout(size_t rows, size_t k, size_t stride, const void *values) {
        return values && rows && stride >= k &&
            rows - 1 <= (std::numeric_limits<size_t>::max() - k) / stride;
    }
    void start(const im2p_matmul_desc_t &value, bool live, size_t stripe_count) {
        check(!active && !failed && valid(value), "invalid or busy RTL descriptor");
        if (!live) check(activation_layout(value.m, value.k, value.activation_row_stride_bytes, value.activations),
                         "invalid activation extent");
        desc = value; striped = live; active = true;
        observed = {}; stripes.clear(); completions.clear();
        published = live ? 0 : desc.m; retired = 0; expected_stripes = stripe_count;
        output_i = output_j = output_block = output_row = output_count = 0;
        n_log = im2p_scu_stride_log(desc.n);
        for (unsigned wait = 0; !top.RDY_startLogical; ++wait) {
            check(wait < 1000, "RTL did not become idle"); tick();
        }
        top.startLogical_striped = live;
        top.startLogical_m = desc.m; top.startLogical_n = desc.n; top.startLogical_k = desc.k;
        top.startLogical_kStrideLog = im2p_scu_stride_log(desc.k); top.startLogical_nStrideLog = n_log;
        top.startLogical_windowKLog = window_k_log; top.startLogical_windowNLog = window_n_log;
        top.startLogical_op = desc.vector_op;
        top.startLogical_job = uint32_t(desc.work_context);
        top.startLogical_contextId = desc.work_context;
        top.EN_startLogical = 1; tick(); top.EN_startLogical = 0; eval();
        progress_tick = ticks; progress_mark = 0;
    }
    int8_t activation(size_t row, size_t column) const {
        if (row >= desc.m || column >= desc.k) return 0;
        if (!striped) return static_cast<const int8_t *>(desc.activations)
            [row * desc.activation_row_stride_bytes + column];
        for (const auto &stripe : stripes)
            if (row >= stripe.i_start && row - stripe.i_start < stripe.rows)
                return static_cast<const int8_t *>(stripe.activations)
                    [(row - stripe.i_start) * stripe.activation_row_stride_bytes + column];
        // Completed and unpublished rows cannot be requested by the core.
        // Their padding in a rectangular transfer does not authorize a read.
        return 0;
    }
    void refill(unsigned kind) {
        auto port = window(kind);
        const uint32_t generation = port.generation;
        const size_t row_origin = port.row, word_origin = port.word, words = port.words;
        const unsigned words_per_row = 1U << (kind == 0 ? window_k_log - 4 :
                                              kind == 1 ? window_n_log - 4 : window_n_log - 2);
        check(words && words <= 256, "RTL resident transfer extent");
        for (size_t index = 0; index < words; ++index) {
            const size_t row = row_origin + index / words_per_row;
            const size_t column = (word_origin + index % words_per_row) * (kind == 2 ? 4 : 16);
            std::array<uint32_t, 4> values{};
            if (kind == 2) {
                if (row < (desc.k + 31) / 32 && column < desc.n)
                    check(desc.provider.read_scale(desc.provider.context, row, column,
                          std::min(size_t(4), desc.n - column), values.data()) == IM2P_OK,
                          "scale provider rejected resident read");
            } else {
                std::array<int8_t, 16> bytes{};
                if (kind == 0) {
                    for (size_t lane = 0; lane < 16; ++lane)
                        bytes[lane] = activation(row, column + lane);
                } else if (row < desc.k && column < desc.n) {
                    check(desc.provider.read_weight_i8(desc.provider.context, row, column,
                          std::min(size_t(16), desc.n - column), bytes.data()) == IM2P_OK,
                          "weight provider rejected resident read");
                }
                for (size_t lane = 0; lane < 16; ++lane)
                    values[lane / 4] |= uint32_t(uint8_t(bytes[lane])) << (8 * (lane % 4));
            }
            check(port.miss && port.generation == generation && port.load_ready &&
                  port.row == row_origin && port.word == word_origin && port.words == words,
                  "RTL resident request changed during refill");
            port.load_generation = generation; port.load_index = index;
            for (unsigned lane = 0; lane < 4; ++lane) port.values[lane] = values[lane];
            port.load_enable = 1; tick(); port.load_enable = 0; eval();
        }
        check(port.commit_ready, "RTL resident commit not ready");
        port.commit_generation = generation; port.commit_enable = 1;
        tick(); port.commit_enable = 0; eval();
        if (kind == 0) ++observed.activation_refills;
        else if (kind == 1) ++observed.weight_refills;
        else ++observed.scale_refills;
    }
    size_t current_stripe_end() const {
        if (!striped) return desc.m;
        for (const auto &stripe : stripes)
            if (output_i >= stripe.i_start && output_i - stripe.i_start < stripe.rows)
                return stripe.i_start + stripe.rows;
        throw std::runtime_error("RTL output belongs to no published stripe");
    }
    void output() {
        const uint64_t address = top.streamOutputAddress;
        const uint64_t row_stride = uint64_t(4) << n_log;
        const uint64_t block_stride = desc.m * row_stride;
        const size_t block = desc.vector_op == IM2P_VECTOR_EXTERNAL ? address / block_stride : 0;
        const uint64_t within = desc.vector_op == IM2P_VECTOR_EXTERNAL ? address % block_stride : address;
        const size_t row = within / row_stride, column = within % row_stride / 4;
        const size_t count = top.streamOutputCount;
        check(address % 4 == 0 && block == output_block && row == output_i + output_row &&
              column == output_j && count == std::min(size_t(16), desc.n - output_j),
              "RTL output block/order/extent mismatch");
        std::array<int64_t, 16> values{};
        for (size_t lane = 0; lane < count; ++lane)
            values[lane] = int32_t(top.streamOutputValues[lane]);
        if (observer) observer(observer_context, block, row, column, count, values.data());
        check(desc.provider.write_output(desc.provider.context, block, row, column, count,
              values.data(), desc.output_domain) == IM2P_OK, "output provider rejected RTL values");
        top.consumeStreamOutput_tag = top.streamOutputTag;
        check(top.RDY_consumeStreamOutput, "RTL output acknowledgement not ready");
        top.EN_consumeStreamOutput = 1; tick(); top.EN_consumeStreamOutput = 0; eval();
        ++output_count;
        const size_t tile_rows = std::min(size_t(16), current_stripe_end() - output_i);
        if (++output_row == tile_rows) {
            output_row = 0;
            const size_t blocks = desc.vector_op == IM2P_VECTOR_EXTERNAL ? desc.k / 32 : 1;
            if (++output_block == blocks) {
                output_block = 0;
                output_j += std::min(size_t(16), desc.n - output_j);
                if (output_j == desc.n) { output_j = 0; output_i += tile_rows; }
            }
        }
    }
    void completion() {
        check(striped && !stripes.empty(), "unexpected RTL stripe completion");
        const auto &stripe = stripes.front();
        check(top.stripeId == stripe.stripe_id && top.stripeRowBegin == stripe.i_start &&
              top.stripeRowCount == stripe.rows && output_i >= stripe.i_start + stripe.rows,
              "RTL stripe completion identity or output coverage");
        im2p_stripe_completion_extended_t value{};
        value.base = {stripe.stripe_id, stripe.i_start, stripe.rows, stripe.context};
        value.publish_cycle = top.stripePublishCycle;
        value.completion_cycle = top.stripeCompletionCycle;
        check(value.completion_cycle >= value.publish_cycle, "RTL stripe cycle order");
        value.publish_to_completion_cycles = value.completion_cycle - value.publish_cycle;
        completions.push_back(value); stripes.pop_front();
        top.EN_acknowledgeStripe = 1; tick(); top.EN_acknowledgeStripe = 0; eval();
        ++observed.completions;
    }
    void step() {
        for (unsigned kind = 0; kind < 3; ++kind)
            if (window(kind).miss) refill(kind);
        if (top.streamOutputValid) output();
        if (top.stripeCompletionValid) completion();
        tick();
        observed.first_activation_cycle = top.firstActivationCycle;
        observed.first_activation_published_rows = top.firstActivationPublishedRows;
        const uint64_t mark = top.fragments + top.activationRequests + top.weightRequests +
            top.outputAcks + observed.activation_refills + observed.weight_refills +
            observed.scale_refills + observed.publications + observed.completions;
        if (mark != progress_mark) { progress_mark = mark; progress_tick = ticks; }
        // Waiting for the caller to publish is legal. An active work must still
        // make progress; transport stalls never disable this watchdog.
        check(striped && stripes.empty() && published < desc.m || ticks - progress_tick < 1000000,
              "RTL provider progress watchdog");
    }
    void stats(im2p_work_stats_extended_t *out) const {
        if (!out) return;
        *out = {};
        out->base.work_total_cycles = top.cycles;
        out->base.activation_read_requests = top.activationRequests;
        out->base.weight_read_requests = top.weightRequests;
        out->base.output_write_requests = top.outputWrites;
        out->base.output_write_responses = top.outputAcks;
        out->base.stripe_host_wait_cycles = top.hostWaitCycles;
        out->base.overlap_cycles = top.overlapCycles;
        out->base.completed_fragments = top.fragments;
        out->base.completed_output_tiles = top.works;
        out->base.completed_stripes = striped ? observed.completions : 1;
        out->base.stripes_published = striped ? observed.publications : 1;
        out->base.stripe_rows_published = published;
    }
    void finish(im2p_work_stats_extended_t *out) {
        check(top.done && !top.streamOutputValid && output_i == desc.m && output_j == 0 &&
              output_block == 0 && output_row == 0, "incomplete RTL output coverage");
        stats(out);
        check(top.RDY_acknowledge, "RTL final acknowledge not ready");
        top.EN_acknowledge = 1; tick(); top.EN_acknowledge = 0; eval();
        active = false;
    }
};
#undef WINDOW

template<class Function> int invoke(void *handle, Function function) noexcept {
    if (!handle) return IM2P_ERROR;
    auto &device = *static_cast<Device *>(handle);
    if (device.failed) return IM2P_ERROR;
    try { return function(device); }
    catch (const std::exception &error) { device.failure = error.what(); }
    catch (...) { device.failure = "unknown RTL transport failure"; }
    device.failed = true;
    return IM2P_ERROR;
}
void *create() noexcept { try { return new Device; } catch (...) { return nullptr; } }
void destroy(void *handle) noexcept { delete static_cast<Device *>(handle); }
const char *error(void *handle) noexcept {
    return handle ? static_cast<Device *>(handle)->failure.c_str() : "no RTL instance";
}
int full(void *handle, const im2p_matmul_desc_t *desc, im2p_work_stats_extended_t *stats) {
    if (!desc || !Device::valid(*desc) || !Device::activation_layout(desc->m, desc->k,
            desc->activation_row_stride_bytes, desc->activations)) return IM2P_INVALID_LAYOUT;
    return invoke(handle, [&](Device &device) {
        device.start(*desc, false, 1);
        while (!device.top.done) device.step();
        device.finish(stats);
        return IM2P_OK;
    });
}
int begin(void *handle, const im2p_stripe_work_desc_t *source, size_t stripe_rows) {
    if (!source || !stripe_rows || !source->stripe_count) return IM2P_INVALID_LAYOUT;
    im2p_matmul_desc_t desc{};
#define COPY(field) desc.field = source->field
    COPY(abi_version); COPY(activation_bits); COPY(activation_storage_bytes);
    COPY(weight_bits); COPY(weight_storage_bytes); COPY(dim); COPY(weights); COPY(scales);
    COPY(output); COPY(m); COPY(n); COPY(k); COPY(weight_row_stride_bytes);
    COPY(output_row_stride); COPY(tile_i_rows); COPY(tile_j_columns); COPY(block_size);
    COPY(scale_total_k); COPY(scale_row_stride); COPY(scale_column_offset);
    COPY(scale_valid_columns); COPY(scale_values_len); COPY(vector_op); COPY(output_domain);
    COPY(work_context); COPY(provider);
#undef COPY
    if (!Device::valid(desc)) return IM2P_INVALID_LAYOUT;
    return invoke(handle, [&](Device &device) {
        device.start(desc, true, source->stripe_count);
        return IM2P_OK;
    });
}
int publish(void *handle, const im2p_activation_stripe_t *stripe) {
    if (!stripe) return IM2P_INVALID_LAYOUT;
    return invoke(handle, [&](Device &device) {
        const auto &d = device.desc;
        if (!device.active || !device.striped || stripe->abi_version != IM2P_ABI_VERSION ||
            stripe->activation_bits != 8 || stripe->activation_storage_bytes != 1 ||
            stripe->weight_bits != 8 || stripe->weight_storage_bytes != 1 || stripe->dim != 16 ||
            !Device::activation_layout(stripe->rows, d.k, stripe->activation_row_stride_bytes, stripe->activations) ||
            stripe->i_start != device.published ||
            stripe->rows > d.m - device.published || stripe->stripe_id != device.observed.publications ||
            device.observed.publications >= device.expected_stripes)
            return IM2P_INVALID_LAYOUT;
        if (!device.top.RDY_publishLogical) return IM2P_BACKPRESSURE;
        device.stripes.push_back(*stripe);
        device.top.publishLogical_rowBegin = stripe->i_start;
        device.top.publishLogical_rowCount = stripe->rows;
        device.top.EN_publishLogical = 1; device.tick();
        device.top.EN_publishLogical = 0; device.eval();
        device.published += stripe->rows; ++device.observed.publications;
        return IM2P_OK;
    });
}
int poll(void *handle, im2p_stripe_completion_extended_t *completion) {
    if (!completion) return IM2P_INVALID_LAYOUT;
    return invoke(handle, [&](Device &device) -> int {
        if (!device.active || !device.striped) return IM2P_UNFINISHED_STREAM;
        for (unsigned budget = 0; device.completions.empty() && !device.top.done && budget < 1024; ++budget)
            device.step();
        if (device.completions.empty()) return 0;
        *completion = device.completions.front(); device.completions.pop_front();
        ++device.retired;
        return 1;
    });
}
int finish(void *handle, im2p_work_stats_extended_t *stats) {
    return invoke(handle, [&](Device &device) {
        if (!device.active || !device.striped || device.published != device.desc.m ||
            device.observed.publications != device.expected_stripes || device.retired != device.expected_stripes)
            return IM2P_UNFINISHED_STREAM;
        while (!device.top.done) device.step();
        device.finish(stats);
        return IM2P_OK;
    });
}
int release(void *handle) {
    return invoke(handle, [](Device &device) {
        return device.active ? IM2P_UNFINISHED_STREAM : IM2P_OK;
    });
}
void set_observer(void *handle, im2p_scu_rtl_output_observer_v1 observer, void *context) {
    if (!handle) return;
    auto &device = *static_cast<Device *>(handle);
    device.observer = observer; device.observer_context = context;
}
int observation(void *handle, im2p_scu_rtl_observation_v1 *out) {
    if (!handle || !out) return IM2P_INVALID_LAYOUT;
    *out = static_cast<Device *>(handle)->observed;
    return IM2P_OK;
}
const im2p_scu_rtl_api_v1 api = {sizeof(api), 1, IM2P_ABI_VERSION, 16, 8, 8,
    IM2P_VECTOR_EXTERNAL, IM2P_OUTPUT_LEGACY_BLOCK, IM2P_BSV_SHA256, IM2P_RTL_SHA256,
    create, destroy, error, full, begin, publish, poll, finish, release, set_observer, observation};
} // namespace

extern "C" const im2p_scu_rtl_api_v1 *im2p_scu_rtl_get_api_v1() { return &api; }
