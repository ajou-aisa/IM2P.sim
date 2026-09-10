#include "ggml-gemmini-fpga.hpp"
#include "ggml-gemmini-args.h"
#include "im2p_gemmini_frontend.hpp"
#include "uart.hpp"
#include "telemetry.hpp"
#include "quants/act/exsia/exsia.hpp"
#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <thread>

#ifndef IM2P_FPGA_PROTOCOL_VERSION
#define IM2P_FPGA_PROTOCOL_VERSION 1
#endif

namespace {
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
void require(bool good, const char *why) { if (!good) throw std::runtime_error(why); }
void validate_dense_metadata(const ggml_gemmini_args_t &args, bool ready) {
    const auto *meta = std::get_if<ggml::gemmini::quants::act::exsia::Meta>(&args.act_quant.storage());
    require((!ready && args.act_quant.kind() == ggml::gemmini::quants::act::MetaKind::none) ||
            (meta && meta->rmd_packets.empty() && meta->direct_residuals.empty()),
            "FPGA requires EXSIA RMD OFF metadata");
}

int execute_full(void *context, const im2p_matmul_desc_t *desc,
                 im2p_work_stats_extended_t *stats) {
    return static_cast<im2p::fpga::UART *>(context)->full(desc, stats);
}

struct Pipeline {
    im2p::fpga::UART *uart;
    ggml_gemmini_args_t &args;
    ggml_gemmini_args_t &staged_args;
    im2p::gemmini::Run *run = nullptr;
    size_t accepted = 0;
    std::string failure;
    struct Event { size_t id, slot, begin, end; int16_t theta; int64_t ready, accepted;
                   uint64_t quantization_start, quantization_end; };
    std::vector<Event> events;
    static int64_t now() {
        return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
    }
    static int begin(void *opaque, const im2p_stripe_work_desc_t *desc) {
        auto &p = *static_cast<Pipeline *>(opaque);
        return p.uart->begin(desc, p.args.activation_rows_per_stripe);
    }
    static int publish(void *opaque, const im2p_activation_stripe_t *stripe) {
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
        return static_cast<Pipeline *>(opaque)->uart->poll(completion);
    }
    static int finish(void *opaque, im2p_work_stats_extended_t *stats) {
        return static_cast<Pipeline *>(opaque)->uart->finish(stats);
    }
    static bool ready(void *opaque, const ggml::gemmini::quants::act::exsia::StripeReadyEvent &event) noexcept {
        auto &p = *static_cast<Pipeline *>(opaque);
        try {
            const auto timestamp = now();
            require(event.stripe_id == p.accepted && event.slot == event.stripe_id % 2 &&
                    event.activation_metadata && !event.rmd_packet && !event.direct_residual,
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
            auto status = im2p::gemmini::submit_stripe(*p.run, event, {true, theta});
            // Existing frontend submit blocks for its two event credits. A false
            // ExSIA sink result means fatal quantization failure, never retry.
            require(status.ok(), status.message);
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

bool ggml_gemmini_fpga_supports(size_t rows, size_t columns, size_t reduction, bool pipeline) {
    return (!pipeline || IM2P_FPGA_PROTOCOL_VERSION == 2) && rows > 0 &&
           rows <= (IM2P_FPGA_PROTOCOL_VERSION == 2 ? 336 : 32) && columns > 0 && columns <= 48 &&
           (reduction == 32 || reduction == 64 || reduction == 96);
}
std::string ggml_gemmini_fpga_last_error() { return last_error; }
void ggml_gemmini_fpga_set_observer(
        void (*callback)(const ggml_gemmini_args_t &, const std::vector<int32_t> &, void *), void *context) {
    std::lock_guard lock(engine_mutex);
    observer = callback; observer_context = context;
}

bool ggml_gemmini_fpga_execute(ggml_gemmini_args_t &args, bool pipeline,
        const std::function<void()> &quantize, const char *layer_name) {
    std::lock_guard lock(engine_mutex);
    last_error.clear();
    try {
        require(!(std::getenv("IM2P_FPGA_TEST_PRODUCER_OVERLAP") &&
                  std::getenv("IM2P_FPGA_TEST_WAIT_FIRST_READ")), "incompatible diagnostic producer waits");
        require(ggml_gemmini_fpga_supports(args.I, args.J, args.K, pipeline), "unsupported FPGA shape/mode");
        require(args.weight_format == ggml_gemmini_args_t::im2p_weight_format_t::q8_h1 &&
                args.has_q8_h1_im2p_contract(), "FPGA requires native Q8_H1");
        require(args.A.bytes && args.A.bits == 8 && args.A.row_offset == 0 &&
                args.A.rows == args.I && args.A.cols == args.K &&
                args.A.row_stride_bytes >= args.K && args.A.row_stride_bytes <= 4096 &&
                (args.I - 1) * args.A.row_stride_bytes + args.K <= args.A.bytes->size(),
                "FPGA requires a bounded A8 activation view");
        validate_dense_metadata(args, false);
        require(args.activation_row_offset == 0 && args.f_out && args.col_stride_f_out > 0 &&
                args.col_stride_f_out <= 128 && args.stride_f_out >= args.J * args.col_stride_f_out &&
                args.stride_f_out <= 4096, "unsupported FPGA output layout");
        if (args.D) {
            require(!args.low_D, "FPGA low-width bias unsupported");
            require(args.repeating_bias, "FPGA only supports absent or repeating zero bias");
            const auto *bias = static_cast<const int32_t *>(args.D);
            require(std::all_of(bias, bias + args.J, [](int32_t x) { return x == 0; }), "FPGA nonzero bias unsupported");
        }
        if (!full_reference) {
            const char *reference_path = std::getenv("IM2P_FPGA_FULL_REFERENCE");
            require(reference_path && *reference_path, "missing IM2P_FPGA_FULL_REFERENCE before device open");
            full_reference = im2p::fpga::FullCycleReference::load(reference_path);
        }
        std::optional<im2p::fpga::FullCycleExpectation> expectation;
        if (!pipeline) expectation = full_reference->expect(
            "m" + std::to_string(args.I) + "n" + std::to_string(args.J) + "k" + std::to_string(args.K),
            args.I, args.J, args.K);
        // The explicit selector alone never opens a physical device. This call
        // requires the caller to choose a path (PTY for pre-approval testing).
        const char *path = std::getenv("IM2P_FPGA_DEVICE");
        require(path && *path, "IM2P_FPGA_DEVICE must explicitly select the transport");
        if (!device) {
            double timeout = 30;
            if (const char *value = std::getenv("IM2P_FPGA_TIMEOUT_SECONDS")) timeout = std::stod(value);
            device = std::make_unique<im2p::fpga::UART>(path, timeout, IM2P_FPGA_PROTOCOL_VERSION);
            opened_device = path;
            std::cout << "FPGA_UART_IDENTITY protocol=" << IM2P_FPGA_PROTOCOL_VERSION
                      << " profile=A8/W8/D16 source=" << IM2P_FPGA_BUILD_ID
                      << " backend=FPGA_UART native=Q8_H1 activation=EXSIA residual=OFF\n";
        }
        require(opened_device == path, "persistent FPGA device cannot change during the process");
        if (expectation) device->expect_full(*expectation);
        auto start = std::chrono::steady_clock::now();
        producer_checkpoint_started.store(false);
        quantization_starts.clear();
        if (pipeline) {
            require(args.activation_rows_per_stripe > 0 && args.activation_rows_per_stripe <= 336 &&
                    args.activation_rows_per_stripe % 16 == 0, "FPGA requires canonical aligned stripe geometry");
            require(!args.exsia_stripe_ready_sink, "existing ExSIA sink already owns this invocation");
            quantization_starts.assign((args.I + args.activation_rows_per_stripe - 1) /
                                       args.activation_rows_per_stripe, 0);
            args.act_quant.storage().emplace<ggml::gemmini::quants::act::exsia::Meta>();
        } else {
            quantize();
            validate_dense_metadata(args, true);
        }
        ggml_gemmini_args_t runtime = args;
        if (pipeline) {
            require(runtime.A.allocate(args.I, args.K, 8), "accepted activation staging allocation failed");
            runtime.A.zero_fill();
        }
        runtime.D = nullptr; runtime.repeating_bias = false;
        // Private output remains unpublished if transport, reducer or reference
        // observation fails. Caller padding is never copied over.
        std::vector<float> staged(args.I * args.stride_f_out, 0.0f);
        runtime.f_out = staged.data();
        im2p::gemmini::Options options;
        Pipeline producer{device.get(), args, runtime, nullptr, 0, {}, {}};
        im2p::gemmini::StreamExecutor executor{&producer, Pipeline::begin, Pipeline::publish, Pipeline::poll, Pipeline::finish};
        if (pipeline) options.stream_executor = &executor;
        else { options.full_executor_context = device.get(); options.full_executor = execute_full; }
        auto launched = im2p::gemmini::execute(&runtime, pipeline ? im2p::gemmini::Mode::stripe_pipeline : im2p::gemmini::Mode::full, options);
        require(launched.status.ok() && launched.run, launched.status.message);
        if (pipeline) {
            producer.run = launched.run.get();
            ggml::gemmini::quants::act::exsia::StripeReadySink sink{&producer, Pipeline::ready};
            args.exsia_stripe_ready_sink = &sink;
            try { quantize(); }
            catch (...) { args.exsia_stripe_ready_sink = nullptr; throw; }
            args.exsia_stripe_ready_sink = nullptr;
            require(producer.failure.empty(), producer.failure.c_str());
        }
        validate_dense_metadata(args, true);
        auto result = im2p::gemmini::fence(*launched.run);
        require(result.status.ok(), result.status.message);
        if (pipeline) {
            require(result.stripe_rtl_timings.size == producer.accepted && producer.accepted ==
                    (args.I + args.activation_rows_per_stripe - 1) / args.activation_rows_per_stripe,
                    "incomplete logical PIPELINE stripes");
            auto authorization = im2p::gemmini::authorize_output_commit(*launched.run, true);
            require(authorization.ok(), authorization.message);

        }
        if (observer) observer(args, device->last_raw, observer_context);
        std::vector<float> previous(args.I * args.J);
        for (size_t row = 0; row < args.I; ++row)
            for (size_t column = 0; column < args.J; ++column) {
                previous[row * args.J + column] = args.f_out[row * args.stride_f_out + column * args.col_stride_f_out];
                args.f_out[row * args.stride_f_out + column * args.col_stride_f_out] =
                    staged[row * args.stride_f_out + column * args.col_stride_f_out];
            }
        auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now() - start).count();
        if (device->release() != IM2P_OK) {
            for (size_t row = 0; row < args.I; ++row)
                for (size_t column = 0; column < args.J; ++column)
                    args.f_out[row * args.stride_f_out + column * args.col_stride_f_out] = previous[row * args.J + column];
            throw std::runtime_error("FPGA RELEASE failed");
        }
        const im2p::fpga::RunTelemetry owned(result);
        const auto device_telemetry = device->telemetry();
        const auto cycles = owned.stats.base.work_total_cycles;
        launched.run.reset();
        const auto sustained = std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now() - start).count();
        if (pipeline) {
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
        }
        im2p::fpga::print_telemetry(std::cout, owned, device_telemetry);
        const auto &metrics = device->metrics();
        std::cout << (pipeline ? "FPGA_UART_PIPELINE_PASS layer=" : "FPGA_UART_FULL_PASS layer=") << (layer_name ? layer_name : "")
                  << " cycles=" << cycles << " host_call_ns=" << elapsed
                  << " sustained_ns=" << sustained << " observer=" << bool(observer)
                  << " request_bytes=" << metrics.request_bytes << " response_bytes=" << metrics.response_bytes
                  << " transactions=" << metrics.transactions << " transfer_seconds=" << metrics.transfer_seconds
                  << " reconstruction_seconds=" << metrics.reconstruction_seconds
                  << " release_seconds=" << metrics.release_seconds
                  << " first_A_cycle=" << device->last_first_activation_cycle.load()
                  << " first_A_published_rows=" << device->last_first_activation_published_rows.load()
                  << " residual=OFF commit=1\n";
        return true;
    } catch (const std::exception &error) {
        last_error = error.what();
        if (device && !device->error().empty()) last_error += ": " + device->error();
        std::cerr << "FPGA_UART_FAIL " << last_error << '\n';
        return false;
    }
}
