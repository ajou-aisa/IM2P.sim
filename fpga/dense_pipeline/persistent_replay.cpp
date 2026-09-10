// Reuse the portable fixture reader and existing reference domains verbatim.
#define main frozen_capture_main
#include "../full_replay/capture.cpp"
#undef main
#include "uart.hpp"
#include "telemetry.hpp"
#include <chrono>
#include <cstdlib>
#include <thread>

struct Persistent {
    im2p_sim_t *sim = nullptr;
    im2p::fpga::UART *uart = nullptr;
    im2p_stream_t *stream = nullptr;
    size_t stripe_rows = 0;
    size_t stream_begins = 0;
    uint64_t last_fragment_count = 0, last_progress_cycle = 0;
    Execution capture;
    ~Persistent() {
        if (stream) im2p_destroy_stream(stream);
        if (sim) im2p_sim_destroy(sim);
    }
    static int execute(void *opaque, const im2p_matmul_desc_t *desc, im2p_work_stats_extended_t *stats) {
        auto &self = *static_cast<Persistent *>(opaque);
        if (self.uart) return self.uart->full(desc, stats);
        self.capture.m = desc->m; self.capture.n = desc->n; self.capture.k = desc->k;
        self.capture.downstream = desc->provider;
        self.capture.raw.assign(desc->m * desc->n * (desc->k / 32) * 4, 0);
        auto d = *desc;
        d.provider = {&self.capture, Execution::weight, nullptr, Execution::scale, Execution::output};
        return im2p_execute_matmul_extended(self.sim, &d, stats);
    }
    static int begin(void *opaque, const im2p_stripe_work_desc_t *desc) {
        auto &self = *static_cast<Persistent *>(opaque);
        if (self.uart) return self.uart->begin(desc, self.stripe_rows);
        if (self.stream) return IM2P_ERROR;
        self.capture.m = desc->m; self.capture.n = desc->n; self.capture.k = desc->k;
        self.capture.downstream = desc->provider;
        self.capture.raw.assign(desc->m * desc->n * (desc->k / 32) * 4, 0);
        auto d = *desc;
        d.provider = {&self.capture, Execution::weight, nullptr, Execution::scale, Execution::output};
        ++self.stream_begins;
        self.last_fragment_count = self.last_progress_cycle = 0;
        return im2p_begin_striped_matmul(self.sim, &d, &self.stream);
    }
    static int publish(void *opaque, const im2p_activation_stripe_t *stripe) {
        auto &self = *static_cast<Persistent *>(opaque);
        return self.uart ? self.uart->publish(stripe) : im2p_publish_stripe(self.stream, stripe);
    }
    static int poll(void *opaque, im2p_stripe_completion_extended_t *completion) {
        auto &self = *static_cast<Persistent *>(opaque);
        if (self.uart) return self.uart->poll(completion);
        // This backend explicitly advances the simulator by one RTL cycle.
        // The UART backend only observes its independently clocked device.
        const int progressed = im2p_progress_stream(self.stream, 1);
        if (progressed < 0) return progressed;
        const int result = im2p_poll_completed_extended(self.stream, completion);
        const auto cycles = im2p_stream_cycle_count(self.stream);
        const auto fragments = im2p_stream_progress_count(self.stream);
        if (result == 1 || fragments != self.last_fragment_count) {
            self.last_fragment_count = fragments; self.last_progress_cycle = cycles;
        } else if (cycles - self.last_progress_cycle > 65536) return IM2P_ERROR;
        return result;
    }
    static int finish(void *opaque, im2p_work_stats_extended_t *stats) {
        auto &self = *static_cast<Persistent *>(opaque);
        if (self.uart) return self.uart->finish(stats);
        const int result = im2p_finish_stream_extended(self.stream, stats);
        im2p_destroy_stream(self.stream); self.stream = nullptr;
        return result;
    }
};

struct LiveProducer {
    ggml_gemmini_args_t &args;
    im2p::gemmini::Run *run = nullptr;
    quants::act::QuantizedActivationBuffer *accepted_activation = nullptr;
    unsigned delay_ms = 0;
    size_t accepted = 0;
    std::optional<uint64_t> run_id;
    std::string error;
    bool quantizing = false;
    struct Event { uint64_t run; size_t stripe, slot, begin, end; int16_t theta; int64_t ready, accepted; };
    std::vector<Event> events;
    static int64_t now() {
        return std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count();
    }
    static bool ready(void *opaque, const quants::act::exsia::StripeReadyEvent &event) noexcept {
        auto &p = *static_cast<LiveProducer *>(opaque);
        try {
            const auto timestamp = now();
            require(p.quantizing && p.run && event.stripe_id == p.accepted &&
                    event.slot == event.stripe_id % 2 && event.activation_metadata &&
                    !event.rmd_packet && !event.direct_residual &&
                    (!p.run_id || *p.run_id == event.run_id), "live post-fold event identity");
            require(event.row_begin == p.accepted * p.args.activation_rows_per_stripe &&
                    event.row_begin < p.args.I && event.row_end ==
                        std::min(event.row_begin + p.args.activation_rows_per_stripe, p.args.I),
                    "live post-fold row bounds");
            const auto &meta = std::get<quants::act::exsia::Meta>(p.args.act_quant.storage());
            const auto theta = event.activation_metadata->theta;
            require(meta.run_id && *meta.run_id == event.run_id &&
                    event.stripe_id < meta.theta.size() && meta.theta[event.stripe_id] == theta &&
                    theta != std::numeric_limits<int16_t>::min(), "live immutable stripe metadata");
            for (size_t stripe = event.stripe_id + 1; stripe < meta.theta.size(); ++stripe)
                require(meta.theta[stripe] == std::numeric_limits<int16_t>::min(),
                        "live event arrived after future quantization");
            if (event.stripe_id && p.delay_ms)
                std::this_thread::sleep_for(std::chrono::milliseconds(p.delay_ms));
            // ExSIA clears its A buffer on producer failure. Keep accepted bytes
            // in separate bounded storage until the frontend worker is retired.
            require(p.accepted_activation && p.accepted_activation->row_stride_bytes == p.args.A.row_stride_bytes,
                    "live accepted activation storage");
            const size_t stride = p.args.A.row_stride_bytes;
            std::memcpy(p.accepted_activation->bytes->data() + event.row_begin * stride,
                        p.args.A.bytes->data() + event.row_begin * stride,
                        (event.row_end - event.row_begin) * stride);
            const auto status = im2p::gemmini::submit_stripe(*p.run, event, {true, theta});
            require(status.ok(), status.message);
            p.run_id = event.run_id;
            ++p.accepted;
            p.events.push_back({event.run_id, event.stripe_id, event.slot, event.row_begin,
                                event.row_end, theta, timestamp, now()});
            return true;
        } catch (const std::exception &e) { p.error = e.what(); return false; }
    }
};

int main(int argc, char **argv) {
    using Clock = std::chrono::steady_clock;
    if (argc > 1 && std::string(argv[1]) == "capture") return frozen_capture_main(argc, argv);
    try {
        require(argc >= 5, "usage: persistent_replay uart[-live][-pipeline]|simulator[-live][-pipeline] DEVICE REPETITIONS fixture...");
        const std::string backend = argv[1];
        require(backend == "uart" || backend == "simulator" ||
                backend == "uart-pipeline" || backend == "simulator-pipeline" ||
                backend == "uart-live" || backend == "simulator-live" ||
                backend == "uart-live-pipeline" || backend == "simulator-live-pipeline", "explicit backend required");
        const bool pipeline = backend.ends_with("-pipeline");
        const bool physical = backend.starts_with("uart");
        const bool live = backend.find("-live") != std::string::npos;
        require(!live || !std::getenv("IM2P_FPGA_TEST_PRODUCER_OVERLAP"),
                "persistent live producer uses IM2P_REPLAY_TEST_STRIPE_DELAY_MS diagnostic only");
        require(!pipeline || !physical || IM2P_FPGA_PROTOCOL_VERSION == 2, "UART PIPELINE requires IFR2");
        const int repeats = std::stoi(argv[3]); require(repeats > 0 && repeats <= 100, "bounded repetitions");
        unsigned diagnostic_delay_ms = 0;
        if (const char *delay = std::getenv("IM2P_REPLAY_TEST_STRIPE_DELAY_MS")) {
            size_t consumed = 0;
            const auto value = std::stoul(delay, &consumed);
            require(pipeline && consumed == std::strlen(delay) && value <= 30000,
                    "invalid diagnostic stripe delay");
            diagnostic_delay_ms = unsigned(value);
        }
        std::optional<im2p::fpga::FullCycleReference> reference;
        std::vector<im2p::fpga::FullCycleExpectation> full_expectations;
        if (physical) {
            const char *path = std::getenv("IM2P_FPGA_FULL_REFERENCE");
            require(path && *path, "missing IM2P_FPGA_FULL_REFERENCE before device open");
            reference = im2p::fpga::FullCycleReference::load(path);
            // Check every planned FULL before OPEN/CAP, not after an earlier job.
            if (!pipeline) for (int file = 4; file < argc; ++file) {
                Fixture probe; probe.restore(argv[file]);
                full_expectations.push_back(reference->expect(fs::path(argv[file]).filename().string(),
                                                               probe.args.I, probe.args.J, probe.args.K));
            }
        }
        std::unique_ptr<im2p::fpga::UART> uart;
        Persistent executor;
        const auto initialize = Clock::now();
        if (physical) {
            double timeout = 30.0;
            if (const char *value = std::getenv("IM2P_FPGA_TIMEOUT_SECONDS")) timeout = std::stod(value);
            uart = std::make_unique<im2p::fpga::UART>(argv[2], timeout, IM2P_FPGA_PROTOCOL_VERSION); executor.uart = uart.get();
        } else { executor.sim = im2p_sim_create(); require(executor.sim, "persistent simulator create"); }
        std::cout << "INITIALIZATION backend=" << backend << " execution=" << (physical ? "FPGA_UART" : "SIMULATOR")
                  << " replay=" << (live ? "LIVE_QUANTIZATION" : "DETERMINISTIC")
                  << " prequantized=" << !live << " live_producer=" << (live && pipeline)
                  << " quantization_included=" << live << " diagnostic_delay_ms=" << diagnostic_delay_ms << " ns="
                  << std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - initialize).count() << '\n';
        size_t jobs = 0, raw_count = 0, logical_count = 0, padding_count = 0;
        for (int file = 4; file < argc; ++file) {
            Fixture original; original.restore(argv[file]);
            auto expected_raw = read(fs::path(argv[file]) / "expected-raw.bin");
            auto expected_fout = read(fs::path(argv[file]) / "expected-fout.bin");
            const auto geometry = original.args.activation_geometry();
            require(geometry.ok(), "persistent host geometry");
            const auto &original_meta = std::get<quants::act::exsia::Meta>(original.args.act_quant.storage());
            std::unique_ptr<ggml_context, decltype(&ggml_free)> context(nullptr, ggml_free);
            ggml_tensor *input = nullptr;
            if (live) {
                const auto source = read(fs::path(argv[file]) / "input-f32.bin");
                require(source.size() == original.args.I * original.args.K * sizeof(float),
                        "live float32 input length");
                context.reset(ggml_init({source.size() + ggml_tensor_overhead() + 1024, nullptr, false}));
                require(bool(context), "live ggml quantizer context");
                input = ggml_new_tensor_2d(context.get(), GGML_TYPE_F32, original.args.K, original.args.I);
                size_t offset = 0;
                auto *values = static_cast<float *>(input->data);
                for (size_t i = 0; i < source.size() / sizeof(float); ++i)
                    values[i] = std::bit_cast<float>(uint32_t(get(source, offset, 4)));
            }
            if (pipeline) {
                require(original.args.activation_rows_per_stripe == geometry.geometry.stripe_rows &&
                        original_meta.run_id && *original_meta.run_id == original.identity &&
                        original_meta.theta.size() == geometry.geometry.stripe_count &&
                        original_meta.rmd_packets.empty() && original_meta.direct_residuals.empty(),
                        "persistent dense stripe metadata/geometry");
                for (auto theta : original_meta.theta)
                    require(theta != std::numeric_limits<int16_t>::min(), "persistent invalid stripe theta");
            }
            for (int iteration = 0; iteration <= repeats; ++iteration) {
                if (uart && !pipeline) uart->expect_full(full_expectations.at(file - 4));
                const auto begin = Clock::now();
                Fixture f = original; f.bind(); // owned W/metadata, retained immutable A
                LiveProducer producer{f.args};
                producer.delay_ms = diagnostic_delay_ms;
                int64_t quantization_start_ns = 0, quantization_end_ns = 0;
                const auto quantize = [&] {
                    quantization_start_ns = LiveProducer::now();
                    producer.quantizing = true;
                    bool ok = false;
                    try { ok = quants::quantize_activation(input, f.args); }
                    catch (...) { producer.quantizing = false; f.args.exsia_stripe_ready_sink = nullptr; throw; }
                    producer.quantizing = false;
                    f.args.exsia_stripe_ready_sink = nullptr;
                    quantization_end_ns = LiveProducer::now();
                    require(producer.error.empty(), producer.error.c_str());
                    require(ok, "live existing quantizer failed");
                };
                if (live) {
                    // allocate always creates a fresh owned zero-filled buffer;
                    // the captured reference A is retained only for comparison.
                    require(f.args.A.allocate(f.args.I, f.args.K, 8), "live activation allocation");
                    f.args.act_quant.storage().emplace<quants::act::exsia::Meta>();
                    if (!pipeline) quantize();
                }
                ggml_gemmini_args_t runtime = f.args;
                if (live && pipeline) {
                    require(runtime.A.allocate(runtime.I, runtime.K, 8), "live accepted activation allocation");
                    producer.accepted_activation = &runtime.A;
                }
                im2p::gemmini::Options options;
                executor.stripe_rows = f.args.activation_rows_per_stripe;
                im2p::gemmini::StreamExecutor stream_executor{
                    &executor, Persistent::begin, Persistent::publish, Persistent::poll, Persistent::finish};
                if (pipeline) options.stream_executor = &stream_executor;
                else { options.full_executor_context = &executor; options.full_executor = Persistent::execute; }
                auto started = im2p::gemmini::execute(&runtime, pipeline ? im2p::gemmini::Mode::stripe_pipeline :
                                                     im2p::gemmini::Mode::full, options);
                if (!started.status.ok() || !started.run)
                    throw std::runtime_error("persistent execute: " +
                        (uart ? uart->error() : std::string(started.status.message)));
                if (live && pipeline) {
                    producer.run = started.run.get();
                    quants::act::exsia::StripeReadySink sink{&producer, LiveProducer::ready};
                    f.args.exsia_stripe_ready_sink = &sink;
                    quantize();
                    require(producer.accepted == geometry.geometry.stripe_count && producer.run_id,
                            "live accepted stripe coverage");
                } else if (pipeline) {
                    const auto &meta = std::get<quants::act::exsia::Meta>(f.args.act_quant.storage());
                    for (size_t stripe = 0; stripe < geometry.geometry.stripe_count; ++stripe) {
                        if (stripe && diagnostic_delay_ms)
                            std::this_thread::sleep_for(std::chrono::milliseconds(diagnostic_delay_ms));
                        quants::act::exsia::StripeReadyEvent event{};
                        event.run_id = *meta.run_id; event.stripe_id = stripe; event.slot = stripe % 2;
                        event.row_begin = stripe * executor.stripe_rows;
                        event.row_end = std::min(event.row_begin + executor.stripe_rows, f.args.I);
                        event.activation_metadata = quants::act::exsia::StripeMetadataSnapshot{
                            meta.e_s, meta.rho, meta.sigma, meta.theta[stripe]};
                        const auto accepted = im2p::gemmini::submit_stripe(*started.run, event, {true, meta.theta[stripe]});
                        require(accepted.ok(), "persistent stripe submit");
                    }
                }
                auto result = im2p::gemmini::fence(*started.run);
                if (!result.status.ok())
                    throw std::runtime_error("persistent fence: " +
                        (uart ? uart->error() : std::string(result.status.message)));
                if (pipeline) {
                    const uint64_t expected_run_id = live ? *producer.run_id : f.identity;
                    require(result.stripe_rtl_timings.size == geometry.geometry.stripe_count,
                            "persistent completion count");
                    for (size_t stripe = 0; stripe < result.stripe_rtl_timings.size; ++stripe) {
                        const auto &timing = result.stripe_rtl_timings[stripe];
                        require(timing.run_id == expected_run_id && timing.stripe_id == stripe && timing.slot == stripe % 2 &&
                                timing.row_begin == stripe * executor.stripe_rows &&
                                timing.row_end == std::min((stripe + 1) * executor.stripe_rows, f.args.I),
                                "persistent completion identity/row/slot");
                    }
                    require(im2p::gemmini::authorize_output_commit(*started.run, true).ok(), "persistent output authorization");
                }
                const auto committed = Clock::now();
                if (uart) require(uart->release() == IM2P_OK, "persistent RELEASE");
                const im2p::fpga::RunTelemetry owned(result);
                const auto device_telemetry = uart ? std::optional(uart->telemetry()) : std::nullopt;
                started.run.reset();
                const auto retired = Clock::now();
                if (live) {
                    const auto &meta = std::get<quants::act::exsia::Meta>(f.args.act_quant.storage());
                    require(f.args.A.row_stride_bytes == original.args.A.row_stride_bytes &&
                            *f.args.A.bytes == *original.args.A.bytes && meta.theta == original_meta.theta &&
                            meta.e_s == original_meta.e_s && meta.rho == original_meta.rho &&
                            meta.sigma == original_meta.sigma && meta.rmd_packets.empty() &&
                            meta.direct_residuals.empty() && meta.run_id &&
                            (!pipeline || *meta.run_id == *producer.run_id),
                            "live activation/theta/captured metadata exact mismatch");
                    for (const auto &event : producer.events)
                        std::cout << "PERSISTENT_LIVE_STRIPE run=" << event.run << " id=" << event.stripe
                                  << " slot=" << event.slot << " begin=" << event.begin << " end=" << event.end
                                  << " theta=" << event.theta << " ready_ns=" << event.ready
                                  << " accepted_ns=" << event.accepted << " quantization_end_ns=" << quantization_end_ns
                                  << " immediate_post_fold=1\n";
                }
                Bytes actual_raw;
                if (uart) for (int32_t x : uart->last_raw) put(actual_raw, uint32_t(x), 4);
                else actual_raw = executor.capture.raw;
                require(actual_raw == expected_raw, "persistent raw signed32 exact mismatch");
                Bytes actual_fout; for (float value : f.output) put(actual_fout, std::bit_cast<uint32_t>(value), 4);
                require(actual_fout == expected_fout, "persistent float32/padding bit-exact mismatch");
                if (uart) require(simulator_creates == 0 && simulator_executes == 0 && executor.stream_begins == 0,
                                  "FPGA simulator fallback");
                im2p::fpga::print_telemetry(std::cout, owned, device_telemetry);
                ++jobs; raw_count += actual_raw.size() / 4; logical_count += f.args.I * f.args.J;
                padding_count += f.output.size() - f.args.I * f.args.J;
                std::cout << "SAMPLE backend=" << backend << " fixture=" << fs::path(argv[file]).filename().string()
                          << " iteration=" << iteration << " warmup=" << (iteration == 0)
                          << " replay=" << (live ? "LIVE_QUANTIZATION" : "DETERMINISTIC")
                          << " prequantized=" << !live << " live_producer=" << (live && pipeline)
                          << " quantization_included=" << live << " stripes="
                          << (pipeline ? geometry.geometry.stripe_count : 0)
                          << " stripe_rows=" << geometry.geometry.stripe_rows
                          << " quantization_start_ns=" << quantization_start_ns
                          << " quantization_end_ns=" << quantization_end_ns
                          << " accepted_A_bytes=" << (live && pipeline ? runtime.I * runtime.K : 0)
                          << " diagnostic_delay_ms=" << diagnostic_delay_ms
                          << " service_ns=" << std::chrono::duration_cast<std::chrono::nanoseconds>(committed - begin).count()
                          << " sustained_ns=" << std::chrono::duration_cast<std::chrono::nanoseconds>(retired - begin).count()
                          << " request_bytes=" << (uart ? uart->metrics().request_bytes : 0)
                          << " response_bytes=" << (uart ? uart->metrics().response_bytes : 0)
                          << " transactions=" << (uart ? uart->metrics().transactions : 0)
                          << " run_id=" << (uart ? uart->metrics().run_id :
                              (live ? *std::get<quants::act::exsia::Meta>(f.args.act_quant.storage()).run_id : f.identity))
                          << " run_id_domain=" << (uart ? "DEVICE" : (live ? "HOST_QUANTIZATION" : "FIXTURE"))
                          << " generation=" << (uart ? uart->metrics().generation : 0)
                          << " prepare_seconds=" << (uart ? uart->metrics().prepare_seconds : 0)
                          << " transfer_seconds=" << (uart ? uart->metrics().transfer_seconds : 0)
                          << " reconstruction_seconds=" << (uart ? uart->metrics().reconstruction_seconds : 0)
                          << " release_seconds=" << (uart ? uart->metrics().release_seconds : 0)
                          << " cycles=" << owned.stats.base.work_total_cycles
                          << " validation=expected_files_exact cpu_reference_calls=not_instrumented"
                          << " cpu_reference_path=not_called_source_audit exact=1 commit=1\n" << std::flush;
            }
        }
        std::cout << (pipeline ? "PERSISTENT_PIPELINE_PASS" : "PERSISTENT_FULL_PASS")
                  << " jobs=" << jobs << " raw=" << raw_count << " logical=" << logical_count
                  << " padding=" << padding_count << " simulator_creates=" << simulator_creates
                  << " simulator_executes=" << simulator_executes << " simulator_stream_begins=" << executor.stream_begins << '\n';
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "PERSISTENT_REPLAY_FAIL " << error.what() << '\n'; return 1;
    }
}
