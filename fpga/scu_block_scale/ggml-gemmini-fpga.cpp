// IFR3 target copied explicitly from fpga/dense_pipeline/ggml-gemmini-fpga.cpp; legacy source is preserved.
#include "ggml-gemmini-fpga.hpp"
#include "ggml-gemmini-args.h"
#include "ggml-gemmini-config.hpp"
#include "im2p_gemmini_frontend.hpp"
#include "uart.hpp"
#include "telemetry.hpp"
#include "quants/act/exsia/exsia.hpp"
#include "residual/direct/direct-executor.hpp"
#include "residual/rmd/rmd-im2p-executor.hpp"
#include "residual/rmd/rmd-compose.hpp"
#include <gemmini/log.hpp>
#include <algorithm>
#include <array>
#include <bit>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string_view>
#include <thread>

#ifndef IM2P_FPGA_PROTOCOL_VERSION
#define IM2P_FPGA_PROTOCOL_VERSION 3
#endif

static_assert(IM2P_FPGA_PROTOCOL_VERSION == 3);
static_assert(IM2P_ABI_VERSION == 5);

namespace {
namespace rmd = ggml::gemmini::rmd;
namespace residual = ggml::gemmini::residual;
namespace exsia = ggml::gemmini::quants::act::exsia;
constexpr bool exsia_activation = ggml::gemmini::config::CURRENT_ACTIVATION_QUANT ==
    ggml::gemmini::config::ActivationQuantAlgo::EXSIA;
// ponytail: one physical engine, one invocation at a time. No batch queue.
std::mutex engine_mutex;
std::unique_ptr<im2p::fpga::UART> device;
std::string opened_device;
std::optional<im2p::fpga::FullCycleReference> full_reference;
std::atomic<bool> producer_checkpoint_started{false};
std::atomic<uint64_t> diagnostic_checksum{0};
std::vector<uint64_t> quantization_starts;
thread_local std::string last_error;
void (*observer)(const ggml_gemmini_args_t &, const std::vector<int32_t> &, void *) = nullptr;
void *observer_context = nullptr;
ggml_gemmini_fpga_block_observer block_observer = nullptr;
void *block_observer_context = nullptr;
ggml_gemmini_fpga_result_observer result_observer = nullptr;
void *result_observer_context = nullptr;
ggml_gemmini_fpga_boundary_observer boundary_observer = nullptr;
void *boundary_observer_context = nullptr;
void observe_boundary(const char *stage, const ggml_gemmini_args_t &args,
                      bool pipeline, const exsia::StripeReadyEvent *event = nullptr) {
    if (boundary_observer) boundary_observer(stage, args, pipeline, event, boundary_observer_context);
}
void require(bool good, const char *why) { if (!good) throw std::runtime_error(why); }
void validate_dense_metadata(const ggml_gemmini_args_t &args, bool ready, bool residual_enabled = false) {
    if constexpr (!exsia_activation) {
        require(!residual_enabled, "main non-ExSIA routes require RMD OFF");
        // The original frontend validates the selected metadata's extent and
        // positive finite scales after the original quantizer has completed.
        return;
    }
    const auto *meta = std::get_if<ggml::gemmini::quants::act::exsia::Meta>(&args.act_quant.storage());
    require((!ready && args.act_quant.kind() == ggml::gemmini::quants::act::MetaKind::none) ||
            (meta && (residual_enabled || (meta->rmd_packets.empty() && meta->direct_residuals.empty()))),
            residual_enabled ? "FPGA requires EXSIA metadata matching its residual mode" :
                               "FPGA requires EXSIA RMD OFF metadata");
}

void add_stats(im2p_work_stats_extended_t &total, const im2p_work_stats_extended_t &part) {
    // ABI5 statistics contain 41 uint64_t fields. Each FULL job has no
    // cross-stripe lookahead timestamps; the retained counters are additive.
    using Fields = std::array<uint64_t, 41>;
    auto values = std::bit_cast<Fields>(total);
    const auto incoming = std::bit_cast<Fields>(part);
    for (size_t i = 0; i < values.size(); ++i) {
        require(values[i] <= UINT64_MAX - incoming[i], "FPGA statistics overflow");
        values[i] += incoming[i];
    }
    total = std::bit_cast<im2p_work_stats_extended_t>(values);
}

int execute_residual(void *context, const im2p_matmul_desc_t *desc,
                     im2p_work_stats_extended_t *stats) {
    auto &uart = *static_cast<im2p::fpga::UART *>(context);
    if (!desc || desc->vector_op != IM2P_VECTOR_BYPASS || desc->output_domain != 0)
        return IM2P_ERROR;
    uart.set_block_observer(nullptr, nullptr);
    const int status = uart.full(desc, stats);
    if (status != IM2P_OK) return status;
    return uart.release();
}

void apply_residual(im2p::fpga::UART &uart, const ggml_gemmini_args_t &args,
                    const exsia::StripeReadyEvent &event, float *stage,
                    im2p::gemmini::ResidualStripeStats &stats) {
    require(event.activation_metadata && event.row_begin < event.row_end &&
            event.row_end <= args.I && !(event.rmd_packet && event.direct_residual),
            "invalid FPGA residual event");
    require((args.residual_route == residual::ResidualRoute::ws_packet || !event.rmd_packet) &&
            (args.residual_route == residual::ResidualRoute::cpu_direct || !event.direct_residual),
            "FPGA residual route changed after quantization");
    const auto matches_event = [&](const auto &payload) {
        return payload.stripe_id == event.stripe_id && payload.row_begin == event.row_begin &&
               payload.row_count == event.row_end - event.row_begin &&
               payload.logical_k == args.K && payload.logical_j == args.J;
    };
    require((!event.rmd_packet || matches_event(*event.rmd_packet)) &&
            (!event.direct_residual || matches_event(*event.direct_residual)),
            "FPGA residual packet identity mismatch");
    if (!event.rmd_packet && !event.direct_residual) return;
    auto stripe_args = args;
    auto &meta = stripe_args.act_quant.storage().emplace<exsia::Meta>();
    meta.e_s = event.activation_metadata->e_s;
    meta.rho = event.activation_metadata->rho;
    meta.sigma = event.activation_metadata->sigma;
    meta.run_id = event.run_id;
    meta.theta.assign(event.stripe_id + 1, std::numeric_limits<int16_t>::min());
    meta.theta[event.stripe_id] = event.activation_metadata->theta;
    rmd::Correction correction = rmd::BlockScaledInt64Correction{};
    rmd::RmdStatus status;
    if (event.rmd_packet) {
        rmd::CompressedOutput compressed;
        rmd::RmdExecutionMetrics metrics;
        const rmd::Im2pFullExecutor executor{&uart, execute_residual};
        status = rmd::execute_rmd_stripe_im2p(nullptr, stripe_args, *event.rmd_packet,
                                              compressed, &metrics, &executor);
        require(status == rmd::RmdStatus::success, rmd::rmd_status_message(status));
        stats.rmd_dot_calls = metrics.im2p_dot_calls;
        rmd::detail::expand_im2p_provider_stats(metrics.im2p_stats, stats.rmd_stats);
        status = rmd::compose_rmd_output(*event.rmd_packet, compressed, correction);
    } else {
        status = residual::execute_direct_stripe(stripe_args, *event.direct_residual, correction);
    }
    require(status == rmd::RmdStatus::success, rmd::rmd_status_message(status));
    status = rmd::merge_rmd_correction_to(stripe_args, stage, event.row_begin, event.row_end, correction);
    require(status == rmd::RmdStatus::success, rmd::rmd_status_message(status));
}

struct FullCapture {
    const ggml_gemmini_args_t *args = nullptr;
    std::mutex mutex;
    std::vector<exsia::StripeReadyEvent> events;
    static bool ready(void *opaque, const exsia::StripeReadyEvent &event) noexcept {
        try {
            auto &self = *static_cast<FullCapture *>(opaque);
            std::lock_guard lock(self.mutex);
            if (self.args) observe_boundary("post_fold", *self.args, false, &event);
            self.events.push_back(event);
            return true;
        } catch (...) { return false; }
    }
};

int execute_full(void *context, const im2p_matmul_desc_t *desc,
                 im2p_work_stats_extended_t *stats) {
    return static_cast<im2p::fpga::UART *>(context)->full(desc, stats);
}

struct Pipeline {
    im2p::fpga::UART *uart;
    ggml_gemmini_args_t &args;
    ggml_gemmini_args_t &staged_args;
    Pipeline(im2p::fpga::UART *device, ggml_gemmini_args_t &source, ggml_gemmini_args_t &staged)
        : uart(device), args(source), staged_args(staged) {}
    im2p::gemmini::Run *run = nullptr;
    size_t accepted = 0;
    std::string failure;
    struct Event { size_t id, slot, begin, end; int16_t theta; int64_t ready, accepted;
                   uint64_t quantization_start, quantization_end; };
    std::vector<Event> events;
    bool residual_enabled = false;
    im2p_stripe_work_desc_t descriptor{};
    im2p_work_stats_extended_t dense_stats{};
    std::optional<im2p_stripe_completion_extended_t> completion;
    size_t current_row = 0, dense_jobs = 0, merged = 0;
    uint64_t elapsed_cycles = 0;
    std::string worker_failure;
    static int64_t now() {
        return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
    }
    static int begin(void *opaque, const im2p_stripe_work_desc_t *desc) {
        auto &p = *static_cast<Pipeline *>(opaque);
        if (p.residual_enabled) { p.descriptor = *desc; return IM2P_OK; }
        return p.uart->begin(desc, p.args.activation_rows_per_stripe);
    }
    static int weight(void *opaque, size_t row, size_t column, size_t count, int8_t *out) {
        const auto &source = static_cast<Pipeline *>(opaque)->descriptor.provider;
        return source.read_weight_i8(source.context, row, column, count, out);
    }
    static int scale(void *opaque, size_t row, size_t column, size_t count, uint32_t *out) {
        const auto &source = static_cast<Pipeline *>(opaque)->descriptor.provider;
        return source.read_scale(source.context, row, column, count, out);
    }
    static int output(void *opaque, size_t block, size_t row, size_t column, size_t count,
                      const int64_t *values, uint32_t domain) {
        const auto &p = *static_cast<Pipeline *>(opaque);
        const auto &source = p.descriptor.provider;
        return source.write_output(source.context, block, row + p.current_row,
                                   column, count, values, domain);
    }
    static void observe_block(void *opaque, size_t block, size_t row, size_t column,
                              size_t count, const int64_t *values) {
        const auto &p = *static_cast<Pipeline *>(opaque);
        if (block_observer) block_observer(block_observer_context, block, row + p.current_row,
                                           column, count, values);
    }
    int dense_stripe(const im2p_activation_stripe_t &stripe) noexcept {
        try {
            require(!completion && dense_jobs == merged && stripe.stripe_id == dense_jobs,
                    "dense stripe reused a residual-owned core");
            current_row = stripe.i_start;
            im2p_matmul_desc_t d{};
#define COPY_FIELD(name) d.name = descriptor.name
            COPY_FIELD(abi_version); COPY_FIELD(activation_bits); COPY_FIELD(activation_storage_bytes);
            COPY_FIELD(weight_bits); COPY_FIELD(weight_storage_bytes); COPY_FIELD(dim);
            COPY_FIELD(n); COPY_FIELD(k); COPY_FIELD(weight_row_stride_bytes); COPY_FIELD(output_row_stride);
            COPY_FIELD(tile_i_rows); COPY_FIELD(tile_j_columns); COPY_FIELD(block_size);
            COPY_FIELD(scale_total_k); COPY_FIELD(scale_row_stride); COPY_FIELD(scale_column_offset);
            COPY_FIELD(scale_valid_columns); COPY_FIELD(scale_values_len);
            COPY_FIELD(vector_op); COPY_FIELD(output_domain); COPY_FIELD(work_context);
#undef COPY_FIELD
            d.m = stripe.rows;
            d.tile_i_rows = std::min(d.tile_i_rows, stripe.rows);
            d.activations = stripe.activations;
            d.activation_row_stride_bytes = stripe.activation_row_stride_bytes;
            d.provider = {this, weight, nullptr, descriptor.provider.read_scale ? scale : nullptr, output};
            uart->set_block_observer(observe_block, this);
            im2p_work_stats_extended_t stats{};
            require(uart->full(&d, &stats) == IM2P_OK, "FPGA dense stripe execution failed");
            require(uart->release() == IM2P_OK, "FPGA dense stripe release failed");
            add_stats(dense_stats, stats);
            require(elapsed_cycles <= UINT64_MAX - stats.base.work_total_cycles, "FPGA cycle overflow");
            completion = {{stripe.stripe_id, stripe.i_start, stripe.rows, stripe.context},
                          elapsed_cycles, elapsed_cycles + stats.base.work_total_cycles,
                          stats.base.work_total_cycles};
            elapsed_cycles += stats.base.work_total_cycles;
            ++dense_jobs;
            return IM2P_OK;
        } catch (const std::exception &error) { worker_failure = error.what(); return IM2P_ERROR; }
    }
    static int publish(void *opaque, const im2p_activation_stripe_t *stripe) {
        auto &p = *static_cast<Pipeline *>(opaque);
        if (p.residual_enabled) return p.dense_stripe(*stripe);
        if (stripe->stripe_id == 0 && std::getenv("IM2P_FPGA_TEST_PRODUCER_OVERLAP")) {
            const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(300);
            while (!producer_checkpoint_started.load()) {
                if (std::chrono::steady_clock::now() >= deadline) return IM2P_ERROR;
                std::this_thread::yield();
            }
        }
        return static_cast<Pipeline *>(opaque)->uart->publish(stripe);
    }
    static int poll(void *opaque, im2p_stripe_completion_extended_t *completion) {
        auto &p = *static_cast<Pipeline *>(opaque);
        if (!p.residual_enabled) return p.uart->poll(completion);
        if (!p.completion) return 0;
        *completion = *p.completion;
        p.completion.reset();
        return 1;
    }
    static int finish(void *opaque, im2p_work_stats_extended_t *stats) {
        auto &p = *static_cast<Pipeline *>(opaque);
        if (!p.residual_enabled) return p.uart->finish(stats);
        if (p.completion || p.merged != p.dense_jobs || p.dense_jobs != p.descriptor.stripe_count)
            return IM2P_ERROR;
        *stats = p.dense_stats;
        return IM2P_OK;
    }
    static im2p::gemmini::Status residual_stage(void *opaque, im2p_sim_t *sim,
            const exsia::StripeReadyEvent &event, im2p::gemmini::ResidualStageView stage,
            im2p::gemmini::ResidualStripeStats &stats) noexcept {
        auto &p = *static_cast<Pipeline *>(opaque);
        try {
            require(!sim && event.stripe_id == p.merged && p.dense_jobs == p.merged + 1,
                    "invalid physical residual ownership");
            apply_residual(*p.uart, p.staged_args, event, stage.data, stats);
            require(p.elapsed_cycles <= UINT64_MAX - stats.rmd_stats.base.work_total_cycles,
                    "FPGA residual cycle overflow");
            p.elapsed_cycles += stats.rmd_stats.base.work_total_cycles;
            ++p.merged;
            return {};
        } catch (const std::exception &error) {
            p.worker_failure = error.what();
            return {im2p::gemmini::StatusCode::execution_failure, im2p::gemmini::Route::unknown,
                    true, p.worker_failure.c_str()};
        }
    }
    static bool ready(void *opaque, const ggml::gemmini::quants::act::exsia::StripeReadyEvent &event) noexcept {
        auto &p = *static_cast<Pipeline *>(opaque);
        try {
            const auto timestamp = now();
            require(event.stripe_id == p.accepted && event.slot == event.stripe_id % 2 &&
                    event.activation_metadata && (p.residual_enabled || (!event.rmd_packet && !event.direct_residual)),
                    "invalid dense post-fold event identity/metadata/residual");
            require(event.row_begin == p.accepted * p.args.activation_rows_per_stripe &&
                    event.row_begin < p.args.I && event.row_end ==
                    std::min(event.row_begin + p.args.activation_rows_per_stripe, p.args.I),
                    "invalid dense post-fold row range");
            const auto theta = event.activation_metadata->theta;
            const auto &meta = std::get<ggml::gemmini::quants::act::exsia::Meta>(p.args.act_quant.storage());
            require(event.stripe_id < meta.theta.size() && meta.theta[event.stripe_id] == theta &&
                    theta != std::numeric_limits<int16_t>::min(), "post-fold immutable theta mismatch");
            for (size_t i = event.stripe_id + 1; i < meta.theta.size(); ++i)
                require(meta.theta[i] == std::numeric_limits<int16_t>::min(), "producer is not at the live post-fold boundary");
            // ExSIA failure clears its producer backing. Retain accepted bytes
            // independently so that failure cannot race an in-flight transfer.
            for (size_t row = event.row_begin; row < event.row_end; ++row)
                std::memcpy(p.staged_args.A.bytes->data() + row * p.staged_args.A.row_stride_bytes,
                             static_cast<const uint8_t *>(p.args.A.raw_data()) + row * p.args.A.row_stride_bytes, p.args.K);
            observe_boundary("post_fold", p.args, true, &event);
            observe_boundary("submit", p.args, true, &event);
            auto status = im2p::gemmini::submit_stripe(*p.run, event, {true, theta});
            // Existing frontend submit blocks for its two event credits. A false
            // ExSIA sink result means fatal quantization failure, never retry.
            require(status.ok(), status.message);
            observe_boundary("accepted", p.args, true, &event);
            ++p.accepted;
            p.events.push_back({event.stripe_id, event.slot, event.row_begin, event.row_end, theta, timestamp, now(),
                                quantization_starts.at(event.stripe_id), uint64_t(timestamp)});
            // Deliberate diagnostic only: hold future producer publication until
            // the real device observes its first A read. Performance leaves this off.
            if (event.stripe_id == 0 && std::getenv("IM2P_FPGA_TEST_WAIT_FIRST_READ")) {
                const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(300);
                while (p.uart->last_first_activation_cycle.load() == 0) {
                    require(std::chrono::steady_clock::now() < deadline, "diagnostic first-A observation timeout");
                    std::this_thread::sleep_for(std::chrono::milliseconds(1));
                }
                require(p.uart->last_first_activation_published_rows.load() == event.row_end,
                        "device consumed unpublished future rows");
            }
            if (const char *value = std::getenv("IM2P_FPGA_TEST_FAIL_AFTER_STRIPE"))
                require(event.stripe_id != std::stoul(value), "injected post-stripe producer failure");
            return true;
        } catch (const std::exception &error) { p.failure = error.what(); return false; }
    }
};
}

// Diagnostic CPU preparation delay at the existing producer's stripe1 start.
// The transport barrier above ensures no stripe0 transfer/publication precedes
// this window. A response containing first-A evidence closes the window.
void ggml_gemmini_fpga_test_producer_checkpoint(size_t stripe_index) {
    if (stripe_index < quantization_starts.size()) quantization_starts[stripe_index] = Pipeline::now();
    if (stripe_index != 1 || !std::getenv("IM2P_FPGA_TEST_PRODUCER_OVERLAP")) return;
    const auto begin = Pipeline::now();
    producer_checkpoint_started.store(true);
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(300);
    uint64_t value = 1;
    while (device->last_first_activation_cycle.load() == 0) {
        for (unsigned i = 0; i < 4096; ++i) value = value * 6364136223846793005ULL + 1;
        diagnostic_checksum.store(value, std::memory_order_relaxed);
        require(std::chrono::steady_clock::now() < deadline, "diagnostic producer/device overlap timeout");
    }
    std::cout << "INJECTED_PRODUCER_OVERLAP stripe=1 begin_ns=" << begin << " end_ns=" << Pipeline::now()
              << " first_A_cycle=" << device->last_first_activation_cycle.load()
              << " first_A_published_rows=" << device->last_first_activation_published_rows.load()
              << " cpu_work_checksum=" << value << " physical_clock_mapping=causal\n";
}

bool ggml_gemmini_fpga_uses_rtl() {
    const char *path = std::getenv("IM2P_FPGA_DEVICE");
    return path && std::string_view(path).starts_with("rtl:/");
}
bool ggml_gemmini_fpga_uses_bounded() {
    const char *path = std::getenv("IM2P_FPGA_DEVICE");
    return ggml_gemmini_fpga_uses_rtl() || (path && std::string_view(path).starts_with("uart4:/"));
}
bool ggml_gemmini_fpga_supports(size_t rows, size_t columns, size_t reduction, bool pipeline) {
    return ggml_gemmini_fpga_supports(rows, columns, reduction, pipeline, true);
}
bool ggml_gemmini_fpga_supports(size_t rows, size_t columns, size_t reduction, bool pipeline, bool block_scaled) {
    (void)pipeline;
    if (ggml_gemmini_fpga_uses_bounded())
        return im2p_scu_logical_extent_valid(rows, columns, reduction, block_scaled) &&
               (!block_scaled || reduction % 32 == 0) &&
               rows <= SIZE_MAX / columns && rows <= SIZE_MAX / reduction;
    if (!block_scaled) return false;
    if (GGML_GEMMINI_ENABLE_RMD != 0) return false;
    return rows > 0 && rows <= 336 && columns > 0 && columns <= 48 &&
           (reduction == 32 || reduction == 64 || reduction == 96);
}
std::string ggml_gemmini_fpga_last_error() { return last_error; }
void ggml_gemmini_fpga_set_observer(
        void (*callback)(const ggml_gemmini_args_t &, const std::vector<int32_t> &, void *), void *context) {
    std::lock_guard lock(engine_mutex);
    observer = callback; observer_context = context;
}
void ggml_gemmini_fpga_set_block_observer(ggml_gemmini_fpga_block_observer callback, void *context) {
    std::lock_guard lock(engine_mutex);
    block_observer = callback; block_observer_context = context;
}
void ggml_gemmini_fpga_set_result_observer(ggml_gemmini_fpga_result_observer callback, void *context) {
    std::lock_guard lock(engine_mutex);
    result_observer = callback; result_observer_context = context;
}
void ggml_gemmini_fpga_set_boundary_observer(ggml_gemmini_fpga_boundary_observer callback, void *context) {
    std::lock_guard lock(engine_mutex);
    boundary_observer = callback; boundary_observer_context = context;
}

bool ggml_gemmini_fpga_execute(ggml_gemmini_args_t &args, bool pipeline,
        const std::function<void()> &quantize, const char *layer_name) {
    std::lock_guard lock(engine_mutex);
    last_error.clear();
    try {
        const bool rtl = ggml_gemmini_fpga_uses_rtl();
        const bool bounded = ggml_gemmini_fpga_uses_bounded();
        const bool residual_enabled = GGML_GEMMINI_ENABLE_RMD != 0;
        const bool channel = args.has_q8_channel_direct_read_contract() ||
                             args.has_q8_channel_dense_sidecar_contract();
        require(exsia_activation || (bounded && !residual_enabled),
                "non-ExSIA requires bounded transport and main's RMD OFF policy");
        require(!residual_enabled || bounded, "legacy UART does not implement accelerator residual");
        require(!(std::getenv("IM2P_FPGA_TEST_PRODUCER_OVERLAP") &&
                  std::getenv("IM2P_FPGA_TEST_WAIT_FIRST_READ")), "incompatible diagnostic producer waits");
        require(ggml_gemmini_fpga_supports(args.I, args.J, args.K, pipeline, !channel), "unsupported FPGA shape/mode");
        require((args.weight_format == ggml_gemmini_args_t::im2p_weight_format_t::q8_h1 &&
                 args.has_q8_h1_im2p_contract()) ||
                (bounded && args.weight_format == ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1 &&
                 args.has_q8_hp1_im2p_contract()) ||
                (bounded && !exsia_activation && channel), bounded ? "FPGA requires a supported native weight contract" :
                                                             "FPGA requires native Q8_H1");
        require(args.A.bytes && args.A.bits == 8 && args.A.row_offset == 0 &&
                args.A.rows == args.I && args.A.cols == args.K &&
                args.A.row_stride_bytes >= args.K && (bounded || args.A.row_stride_bytes <= 4096) &&
                args.A.bytes->size() >= args.K &&
                args.I - 1 <= (args.A.bytes->size() - args.K) / args.A.row_stride_bytes,
                "FPGA requires a bounded A8 activation view");
        validate_dense_metadata(args, false, residual_enabled);
        require(args.activation_row_offset == 0 && args.f_out && args.col_stride_f_out > 0 &&
                args.col_stride_f_out <= 128 && args.stride_f_out >= args.J * args.col_stride_f_out &&
                (bounded || args.stride_f_out <= 4096) && args.I <= SIZE_MAX / args.stride_f_out,
                "unsupported FPGA output layout");
        if (args.D) {
            require(!args.low_D, "FPGA low-width bias unsupported");
            require(args.repeating_bias, "FPGA only supports absent or repeating zero bias");
            const auto *bias = static_cast<const int32_t *>(args.D);
            require(std::all_of(bias, bias + args.J, [](int32_t x) { return x == 0; }), "FPGA nonzero bias unsupported");
        }
        // Missing cycle provenance is allowed only in an explicitly selected
        // PTY RTL integration test. Physical FULL remains fail-closed.
        const char *path = std::getenv("IM2P_FPGA_DEVICE");
        require(path && *path, "IM2P_FPGA_DEVICE must explicitly select the transport");
        if (!bounded && !full_reference) {
            const char *reference_path = std::getenv("IM2P_FPGA_FULL_REFERENCE");
            if (reference_path && *reference_path) {
                const char *hardware = std::getenv("IM2P_FPGA_HARDWARE_SHA256");
                const char *rtl = std::getenv("IM2P_FPGA_PRODUCTION_RTL_SHA256");
                require(hardware && rtl, "missing IFR3 hardware/production RTL reference pins");
                full_reference = im2p::fpga::FullCycleReference::load(reference_path, hardware, rtl);
            } else {
                const char *test = std::getenv("IM2P_FPGA_ALLOW_UNPINNED_RTL_TEST");
                require(test && std::string(test) == "1" && std::string(path).starts_with("/dev/pts/"),
                        "missing IFR3 FULL reference outside explicit PTY RTL test");
            }
        }
        std::optional<im2p::fpga::FullCycleExpectation> expectation;
        if (!bounded && !pipeline && full_reference) expectation = full_reference->expect(
            "m" + std::to_string(args.I) + "n" + std::to_string(args.J) + "k" + std::to_string(args.K),
            args.I, args.J, args.K);
        if (!device) {
            double timeout = 30;
            if (const char *value = std::getenv("IM2P_FPGA_TIMEOUT_SECONDS")) timeout = std::stod(value);
            ggml::gemmini::log::debug("FPGA_UART",
                "connection=open_begin device=%s transport=%s", path, bounded ? "IFR4_UART" : "IFR3_UART");
            device = std::make_unique<im2p::fpga::UART>(path, timeout, IM2P_FPGA_PROTOCOL_VERSION);
            opened_device = path;
            ggml::gemmini::log::debug("FPGA_UART",
                "connection=verified device=%s protocol=%d capability=%s physical_fpga=1",
                path, bounded ? 4 : IM2P_FPGA_PROTOCOL_VERSION, bounded ? "08100420" : "0294");
            if (!bounded) std::cout << "FPGA_UART_IDENTITY protocol=" << IM2P_FPGA_PROTOCOL_VERSION
                      << " semantic_capability=0294 numerical_revision=signed-scu-sat-v2 output_domain=2"
                      << " full_cycle_reference=" << (full_reference ? "pinned" : "unavailable_rtl_test")
                      << " profile=A8/W8/D16 source=" << IM2P_FPGA_BUILD_ID
                      << " backend=FPGA_UART native=Q8_H1 activation=EXSIA residual=OFF\n";
        }
        require(opened_device == path, "persistent FPGA device cannot change during the process");
        require(!bounded || !observer, "final-domain observer is incompatible with External block output");
        require(bounded || !block_observer, "External block observer requires a bounded transport");
        if (bounded) device->set_block_observer(block_observer, block_observer_context);
        if (expectation) device->expect_full(*expectation);
        auto start = std::chrono::steady_clock::now();
        producer_checkpoint_started.store(false);
        quantization_starts.clear();
        FullCapture full_capture;
        full_capture.args = &args;
        observe_boundary("begin", args, pipeline);
        if (pipeline) {
            require(args.activation_rows_per_stripe > 0 && (bounded || args.activation_rows_per_stripe <= 336) &&
                    args.activation_rows_per_stripe % 16 == 0, "FPGA requires canonical aligned stripe geometry");
        }
        if (pipeline && exsia_activation) {
            require(!args.exsia_stripe_ready_sink, "existing ExSIA sink already owns this invocation");
            quantization_starts.assign((args.I + args.activation_rows_per_stripe - 1) /
                                       args.activation_rows_per_stripe, 0);
            args.act_quant.storage().emplace<ggml::gemmini::quants::act::exsia::Meta>();
        } else {
            if (exsia_activation && (residual_enabled || boundary_observer)) {
                require(!args.exsia_stripe_ready_sink, "existing ExSIA sink already owns this invocation");
                exsia::StripeReadySink sink{&full_capture, FullCapture::ready};
                args.exsia_stripe_ready_sink = &sink;
                try { quantize(); }
                catch (...) { args.exsia_stripe_ready_sink = nullptr; throw; }
                args.exsia_stripe_ready_sink = nullptr;
            } else { quantize(); }
            validate_dense_metadata(args, true, residual_enabled);
        }
        ggml_gemmini_args_t runtime = args;
        if (pipeline && exsia_activation) {
            require(runtime.A.allocate(args.I, args.K, 8), "accepted activation staging allocation failed");
            runtime.A.zero_fill();
        }
        runtime.D = nullptr; runtime.repeating_bias = false;
        // Private output remains unpublished if transport, reducer or reference
        // observation fails. Caller padding is never copied over.
        std::vector<float> staged(args.I * args.stride_f_out, 0.0f);
        runtime.f_out = staged.data();
        im2p::gemmini::Options options;
        if (bounded) options.numerical_contract = im2p::gemmini::NumericalContract::main_external;
        Pipeline producer{device.get(), args, runtime};
        producer.residual_enabled = residual_enabled;
        im2p::gemmini::StreamExecutor executor{&producer, Pipeline::begin, Pipeline::publish, Pipeline::poll, Pipeline::finish};
        if (pipeline) {
            options.stream_executor = &executor;
            if (residual_enabled) {
                options.residual_stage_mode = args.residual_route == residual::ResidualRoute::ws_packet
                    ? im2p::gemmini::ResidualStageMode::im2p_compact
                    : im2p::gemmini::ResidualStageMode::host_direct;
                options.residual_stage_context = &producer;
                options.residual_stage_fn = Pipeline::residual_stage;
            }
        }
        else { options.full_executor_context = device.get(); options.full_executor = execute_full; }
        observe_boundary("execute", args, pipeline);
        auto launched = im2p::gemmini::execute(&runtime, pipeline ? im2p::gemmini::Mode::stripe_pipeline : im2p::gemmini::Mode::full, options);
        require(launched.status.ok() && launched.run, launched.status.message);
        if (pipeline && exsia_activation) {
            producer.run = launched.run.get();
            ggml::gemmini::quants::act::exsia::StripeReadySink sink{&producer, Pipeline::ready};
            args.exsia_stripe_ready_sink = &sink;
            try { quantize(); }
            catch (...) { args.exsia_stripe_ready_sink = nullptr; throw; }
            args.exsia_stripe_ready_sink = nullptr;
            require(producer.failure.empty(), producer.failure.c_str());
        } else if (pipeline) {
            // Match main's prequantized non-ExSIA publication: one Run, original
            // H-row intervals and immutable A/scale backing, no new GEMM tiles.
            const auto run_id = exsia::next_exsia_run_id();
            for (size_t begin = 0; begin < args.I; begin += args.activation_rows_per_stripe) {
                exsia::StripeReadyEvent event{};
                event.run_id = run_id;
                event.stripe_id = producer.accepted;
                event.slot = event.stripe_id % 2;
                event.row_begin = begin;
                event.row_end = std::min(args.I, begin + args.activation_rows_per_stripe);
                observe_boundary("submit", args, true, &event);
                const auto status = im2p::gemmini::submit_stripe(*launched.run, event);
                require(status.ok(), status.message);
                observe_boundary("accepted", args, true, &event);
                ++producer.accepted;
            }
        }
        validate_dense_metadata(args, true, residual_enabled);
        observe_boundary("fence", args, pipeline);
        auto result = im2p::gemmini::fence(*launched.run);
        require(result.status.ok(), result.status.message);
        if (residual_enabled && !pipeline) {
            require(device->release() == IM2P_OK, "FPGA dense FULL release failed");
            std::sort(full_capture.events.begin(), full_capture.events.end(),
                      [](const auto &a, const auto &b) { return a.stripe_id < b.stripe_id; });
            size_t next_row = 0;
            for (size_t index = 0; index < full_capture.events.size(); ++index) {
                const auto &event = full_capture.events[index];
                require(event.stripe_id == index && event.row_begin == next_row &&
                        event.row_end > event.row_begin && event.row_end <= args.I &&
                        event.run_id == full_capture.events.front().run_id,
                        "invalid captured FULL residual stripe identity");
                im2p::gemmini::ResidualStripeStats stats;
                apply_residual(*device, runtime, event, staged.data(), stats);
                require(result.rmd_dot_calls <= UINT64_MAX - stats.rmd_dot_calls,
                        "FPGA residual count overflow");
                result.rmd_dot_calls += stats.rmd_dot_calls;
                add_stats(result.rmd_stats, stats.rmd_stats);
                next_row = event.row_end;
            }
            require(next_row == args.I, "incomplete captured FULL residual coverage");
        }
        if (pipeline) {
            require(result.stripe_rtl_timings.size == producer.accepted && producer.accepted ==
                    (args.I + args.activation_rows_per_stripe - 1) / args.activation_rows_per_stripe,
                    "incomplete logical PIPELINE stripes");
            auto authorization = im2p::gemmini::authorize_output_commit(*launched.run, true);
            require(authorization.ok(), authorization.message);

        }
        observe_boundary("fenced", args, pipeline);
        if (observer) {
            auto completed_args = args;
            completed_args.f_out = staged.data();
            observer(completed_args, device->last_raw, observer_context);
        }
        if (result_observer) {
            auto completed_args = args;
            completed_args.f_out = staged.data();
            result_observer(completed_args, result_observer_context);
        }
        std::vector<float> previous(args.I * args.J);
        for (size_t row = 0; row < args.I; ++row)
            for (size_t column = 0; column < args.J; ++column) {
                previous[row * args.J + column] = args.f_out[row * args.stride_f_out + column * args.col_stride_f_out];
                args.f_out[row * args.stride_f_out + column * args.col_stride_f_out] =
                    staged[row * args.stride_f_out + column * args.col_stride_f_out];
            }
        auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now() - start).count();
        if (!residual_enabled && device->release() != IM2P_OK) {
            for (size_t row = 0; row < args.I; ++row)
                for (size_t column = 0; column < args.J; ++column)
                    args.f_out[row * args.stride_f_out + column * args.col_stride_f_out] = previous[row * args.J + column];
            throw std::runtime_error("FPGA RELEASE failed");
        }
        observe_boundary("commit", args, pipeline);
        ggml::gemmini::log::debug("FPGA_UART",
            "execution=committed layer=%s mode=%s M=%zu N=%zu K=%zu physical_fpga=1",
            layer_name ? layer_name : "", pipeline ? "STRIPE_PIPELINE" : "FULL",
            args.I, args.J, args.K);
        const im2p::fpga::RunTelemetry owned(result);
        const auto device_telemetry = device->telemetry();
        const auto cycles = owned.stats.base.work_total_cycles;
        launched.run.reset();
        const auto sustained = std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now() - start).count();
        if (pipeline && exsia_activation) {
            for (size_t i = 0; i < producer.events.size(); ++i) {
                const auto &event = producer.events[i]; const auto &rtl = owned.stripes[i];
                std::cout << "LIVE_STRIPE id=" << event.id << " slot=" << event.slot << " begin=" << event.begin
                          << " end=" << event.end << " theta=" << event.theta << " ready_ns=" << event.ready
                          << " accepted_ns=" << event.accepted << " publish_cycle=" << rtl.publish_cycle
                          << " quantization_start_ns=" << event.quantization_start
                          << " quantization_end_ns=" << event.quantization_end
                          << " quantization_clock=host_checkpoint_to_postfold"
                          << " completion_cycle=" << rtl.completion_cycle << '\n';
            }
        } else if (pipeline) {
            std::cout << "PREPARED_STRIPES count=" << producer.accepted << " backing=immutable\n";
        }
        if (!residual_enabled) im2p::fpga::print_telemetry(std::cout, owned, device_telemetry);
        else std::cout << "FPGA_RESIDUAL_JOBS dense_jobs=" << (pipeline ? producer.dense_jobs : 1)
                       << " compact_jobs=" << result.rmd_dot_calls
                       << " dense_cycles=" << cycles
                       << " residual_cycles=" << result.rmd_stats.base.work_total_cycles
                       << " semantic_stripes=" << (pipeline ? producer.merged : full_capture.events.size())
                       << " physical_cores=1 metrics_scope=last_physical_job\n";
        const auto &metrics = device->metrics();
        std::cout << (rtl ? (pipeline ? "FPGA_RTL_PIPELINE_PASS layer=" : "FPGA_RTL_FULL_PASS layer=") :
                           (pipeline ? "FPGA_UART_PIPELINE_PASS layer=" : "FPGA_UART_FULL_PASS layer=")) << (layer_name ? layer_name : "")
                  << " cycles=" << cycles << " host_call_ns=" << elapsed
                  << " sustained_ns=" << sustained << " observer=" << bool(observer)
                  << " request_bytes=" << metrics.request_bytes << " response_bytes=" << metrics.response_bytes
                  << " transactions=" << metrics.transactions << " transfer_seconds=" << metrics.transfer_seconds
                  << " reconstruction_seconds=" << metrics.reconstruction_seconds
                  << " release_seconds=" << metrics.release_seconds
                  << (bounded && !rtl ? " first_A_observation=NOT_AVAILABLE_IFR4" :
                      " first_A_cycle=" + std::to_string(device->last_first_activation_cycle.load()) +
                      " first_A_published_rows=" + std::to_string(device->last_first_activation_published_rows.load()))
                  << (rtl ? " plugin_api=1 numerical_contract=main_external output_domain=" :
                      bounded ? " protocol=4 numerical_contract=main_external output_domain=" :
                            " protocol=3 semantic_capability=0294 numerical_revision=signed-scu-sat-v2 output_domain=2")
                  << (bounded ? std::string(channel ? "0" : "1") + (rtl ? " PHY=omitted" : " PHY=UART") : "")
                  << " residual=" << (residual_enabled ? "ON" : "OFF") << " commit=1\n";
        return true;
    } catch (const std::exception &error) {
        last_error = error.what();
        if (device && !device->error().empty()) last_error += ": " + device->error();
        std::cerr << "FPGA_UART_FAIL " << last_error << '\n';
        return false;
    }
}
