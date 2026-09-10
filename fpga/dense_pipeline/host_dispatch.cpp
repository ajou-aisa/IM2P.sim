#define GGML_GEMMINI_MATMUL_IMPLEMENTATION 1
#include "ggml-gemmini-fpga.hpp"
#include "ggml-gemmini-matmul.hpp"
#include "ggml-gemmini.h"
#include "ggml-backend.h"
#include "ggml-alloc.h"
#include "ggml-quants.h"
#include "im2p_sim.h"
#include <gemmini.h>
#include <algorithm>
#include <atomic>
#include <bit>
#include <chrono>
#include <cstring>
#include <iostream>
#include <stdexcept>

using namespace ggml::gemmini;
static std::atomic<unsigned> simulator_creates{0}, simulator_executes{0}, simulator_streams{0};
extern "C" im2p_sim_t *__real_im2p_sim_create();
extern "C" int __real_im2p_execute_matmul_extended(im2p_sim_t *, const im2p_matmul_desc_t *, im2p_work_stats_extended_t *);
extern "C" im2p_sim_t *__wrap_im2p_sim_create() {
    ++simulator_creates; return __real_im2p_sim_create();
}
extern "C" int __wrap_im2p_execute_matmul_extended(im2p_sim_t *sim, const im2p_matmul_desc_t *desc, im2p_work_stats_extended_t *stats) {
    ++simulator_executes; return __real_im2p_execute_matmul_extended(sim, desc, stats);
}
extern "C" int __real_im2p_begin_striped_matmul(im2p_sim_t *, const im2p_stripe_work_desc_t *, im2p_stream_t **);
extern "C" int __wrap_im2p_begin_striped_matmul(im2p_sim_t *sim, const im2p_stripe_work_desc_t *desc, im2p_stream_t **stream) {
    ++simulator_streams; return __real_im2p_begin_striped_matmul(sim, desc, stream);
}
static void require(bool value, const char *message) {
    if (!value) throw std::runtime_error(message);
}

struct Observation {
    std::vector<float> expected;
    std::vector<int32_t> expected_raw;
    size_t calls = 0, raw_count = 0;
    size_t reference_matmul_calls = 0, reference_dot_calls = 0;
    bool benchmark = false;
    static void complete(const ggml_gemmini_args_t &args, const std::vector<int32_t> &raw, void *opaque) {
        auto &self = *static_cast<Observation *>(opaque);
        require(simulator_creates == 0 && simulator_executes == 0 && simulator_streams == 0, "FPGA dispatch used simulator fallback");
        require(raw.size() == args.I * args.J * (args.K / 32), "raw block count");
        require(args.has_q8_h1_im2p_contract(), "completed native contract");
        const auto &meta = std::get<quants::act::exsia::Meta>(args.act_quant.storage());
        require(meta.direct_residuals.empty() && meta.rmd_packets.empty(), "residual unexpectedly enabled");
        require(std::any_of(raw.begin(), raw.end(), [](int32_t value) { return value != 0; }),
                "vacuous all-zero raw control");
        if (args.I > 32) {
            const auto geometry = args.activation_geometry();
            require(geometry.ok() && geometry.geometry.stripe_count == 3 && meta.theta.size() == 3,
                    "long invocation requires automatic three-stripe geometry");
            require(std::none_of(meta.theta.begin(), meta.theta.end(), [](int16_t theta) {
                        return theta == std::numeric_limits<int16_t>::min(); }) &&
                    meta.theta[0] != meta.theta[1] && meta.theta[0] != meta.theta[2] &&
                    meta.theta[1] != meta.theta[2], "long invocation requires distinct valid stripe theta");
        }
        if (self.benchmark && self.calls != 0) {
            // Warm-up verified this reference independently. Timed invocations
            // retain full raw checks without running a CPU numerical backend.
            require(raw == self.expected_raw, "benchmark raw signed32 exact mismatch");
            ++self.calls; self.raw_count += raw.size();
            return;
        }
        auto reference = args;
        reference.tiled_matmul_type = CPU;
        reference.exsia_stripe_ready_sink = nullptr;
        const size_t row_stride = args.stride_f_out ? args.stride_f_out : args.J;
        self.expected.assign(args.I * row_stride, 17.0f);
        reference.f_out = self.expected.data();
        // This independent comparison runs only after actual FPGA completion.
        const auto result = MatMul(reference).run_full();
        ++self.reference_matmul_calls;
        require(result.status == MatMulStatus::success, "existing host CPU reference failed");
        if (self.benchmark) self.expected_raw.resize(raw.size());
        size_t index = 0;
        for (size_t block = 0; block < args.K / 32; ++block)
            for (size_t row = 0; row < args.I; ++row)
                for (size_t col = 0; col < args.J; ++col) {
                    acc_t expected = 0;
                    const auto *a = reinterpret_cast<const elem_t *>(args.A.raw_data()) +
                        row * args.A.row_stride_bytes + block * 32;
                    const auto &w = args.q8_h1_blocks[col * (args.K / 32) + block];
                    matmul_cpu_int32(false, false, 1, 1, 32, a, w.qs, nullptr, &expected,
                        32, 1, 0, 1, 1, 1, 1, NO_ACTIVATION, 0, 0, false, true);
                    ++self.reference_dot_calls;
                    if (self.benchmark) self.expected_raw[index] = expected;
                    require(raw[index++] == expected, "raw signed32 exact mismatch");
                }
        require(std::any_of(self.expected.begin(), self.expected.end(), [](float x) { return x != 0.0f; }),
                "vacuous all-zero f_out control");
        ++self.calls;
        self.raw_count += raw.size();
    }
};

struct Graph {
    ggml_backend_t backend = nullptr;
    ggml_context *context = nullptr;
    ggml_backend_buffer_t buffer = nullptr;
    ggml_cgraph *graph = nullptr;
    ggml_tensor *weight = nullptr, *activation = nullptr, *output = nullptr;
    size_t m, n, k;
    Graph(size_t rows, size_t cols, size_t reduction, bool native = true) : m(rows), n(cols), k(reduction) {
        backend = ggml_backend_gemmini_init(); require(backend != nullptr, "ggml Gemmini backend init");
        // Initialize ggml's quantizer/FP16 tables before creating packed weights.
        context = ggml_init({ggml_tensor_overhead() * 16 + ggml_graph_overhead(), nullptr, true});
        require(context != nullptr, "ggml context init");
        weight = ggml_new_tensor_2d(context, native ? GGML_TYPE_Q8_H1 : GGML_TYPE_F32, k, n);
        activation = ggml_new_tensor_2d(context, GGML_TYPE_F32, k, m);
        output = ggml_mul_mat(context, weight, activation);
        ggml_set_name(weight, "blk.0.fpga_dense.weight");
        ggml_set_name(activation, "blk.0.fpga_dense.input");
        ggml_set_name(output, "blk.0.fpga_dense.output");
        buffer = ggml_backend_alloc_ctx_tensors(context, backend);
        require(buffer != nullptr, "ggml graph buffer");
        graph = ggml_new_graph(context);
        ggml_build_forward_expand(graph, output);
    }
    ~Graph() {
        if (buffer) ggml_backend_buffer_free(buffer);
        if (context) ggml_free(context);
        if (backend) ggml_backend_free(backend);
    }
    void prepare(size_t seed) {
        std::vector<float> a(m * k), w(n * k);
        for (size_t i = 0; i < a.size(); ++i)
            a[i] = float(int((i * 17 + seed * 13) % 251) - 125) / 32;
        if (m > 32) {
            ggml_gemmini_args_t args{};
            args.I = m; args.J = n; args.K = k;
            args.tiled_matmul_type = CPU;
            args.matmul_layer = "dense-pipeline-input-geometry";
            gemmini_set_tile_ws(&args);
            const auto geometry = args.activation_geometry();
            require(geometry.ok() && geometry.geometry.stripe_count == 3,
                    "long input requires automatic three-stripe geometry");
            for (size_t row = 0; row < m; ++row)
                for (size_t column = 0; column < k; ++column)
                    a[row * k + column] = std::ldexp(a[row * k + column],
                        int(row / geometry.geometry.stripe_rows) * 3);
        }
        for (size_t j = 0; j < n; ++j) for (size_t l = 0; l < k; ++l)
            w[j * k + l] = float(int((j * 11 + l * 7 + seed * 5) % 253) - 126) * float(1 + (l / 32) * 3) / 128;
        if (weight->type == GGML_TYPE_Q8_H1) {
            std::vector<block_q8_h1> packed(n * k / 32);
            for (size_t j = 0; j < n; ++j)
                quantize_row_q8_h1_ref(w.data() + j * k, packed.data() + j * k / 32, k);
            for (const auto &block : packed)
                require(block.s_rf > 0 && block.c_b + block.R > 0, "native nonzero scale control");
            for (size_t block = 1; block < k / 32; ++block)
                require(packed[block].s_rf * (packed[block].c_b + packed[block].R) !=
                        packed[block - 1].s_rf * (packed[block - 1].c_b + packed[block - 1].R),
                        "native block scales must differ");
            ggml_backend_tensor_set(weight, packed.data(), 0, packed.size() * sizeof(block_q8_h1));
        } else ggml_backend_tensor_set(weight, w.data(), 0, w.size() * sizeof(float));
        ggml_backend_tensor_set(activation, a.data(), 0, a.size() * sizeof(float));
        std::vector<float> sentinel(m * n, 17.0f);
        ggml_backend_tensor_set(output, sentinel.data(), 0, sentinel.size() * sizeof(float));
    }
    std::vector<float> values() const {
        std::vector<float> result(m * n);
        ggml_backend_tensor_get(output, result.data(), 0, result.size() * sizeof(float));
        return result;
    }
};

int main(int argc, char **argv) {
    try {
        require(argc >= 2, "usage: host_dispatch reject | reject-activation | reject-metadata | failure M N K [MODE] | run M N K SEED REPS MODE | benchmark M N K SEED [REPS=5] FULL|STRIPE_PIPELINE");
        const std::string command = argv[1];
        Observation observation;
        ggml_gemmini_fpga_set_observer(Observation::complete, &observation);
        if (command == "reject-activation" || command == "reject-metadata" || command == "reject-bias") {
            require(argc == 2, "rejection arguments");
            const bool metadata = command == "reject-metadata";
            const bool bias = command == "reject-bias";
            // No device is selected. The exact error proves validation precedes
            // quantization and even the device-path requirement, for both modes.
            require(unsetenv("IM2P_FPGA_DEVICE") == 0, "clear rejection-test device");
            std::vector<block_q8_h1> weights(16);
            std::vector<float> output(16 * 19, 17.0f);
            std::vector<int8_t> low_bias(16, 0);
            ggml_gemmini_args_t base{};
            base.I = 16; base.J = 16; base.K = 32;
            base.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_h1;
            base.q8_h1_blocks = weights.data(); base.q8_h1_block_count = weights.size();
            base.q8_h1_rows = 16; base.blocks_per_row = 1;
            base.native_weight_bytes = weights.size() * sizeof(block_q8_h1);
            base.f_out = output.data(); base.stride_f_out = 19; base.col_stride_f_out = 1;
            require(base.has_q8_h1_im2p_contract(), "rejection requires valid native weights");
            size_t quantization_calls = 0, cases = 0;
            for (bool pipeline : {false, true}) for (unsigned invalid = 0; invalid < (bias ? 1u : 6u); ++invalid) {
                auto args = base;
                require(args.A.allocate(16, 32, !metadata && !bias && invalid == 0 ? 16 : 8), "rejection A allocation");
                if (metadata) {
                    auto &storage = args.act_quant.storage();
                    if (invalid == 0) storage.emplace<quants::act::block::Meta>();
                    if (invalid == 1) storage.emplace<quants::act::stripe::Meta>();
                    if (invalid == 2) storage.emplace<quants::act::tensor::Meta>();
                    if (invalid == 3) storage.emplace<quants::act::token::Meta>();
                    if (invalid == 4) {
                        auto payload = std::make_shared<residual::DirectStripePayload>();
                        payload->row_count = 16; payload->logical_k = 32; payload->logical_j = 16;
                        payload->events.push_back({0, 0, 1});
                        storage.emplace<quants::act::exsia::Meta>().direct_residuals.push_back(payload);
                    }
                    if (invalid == 5) {
                        auto packet = std::make_shared<rmd::StripePacket>();
                        packet->row_count = 16; packet->logical_k = 32; packet->logical_j = 16;
                        storage.emplace<quants::act::exsia::Meta>().rmd_packets.push_back(packet);
                    }
                } else if (bias) {
                    args.D = low_bias.data(); args.low_D = true; args.repeating_bias = true;
                } else {
                    if (invalid == 1) args.A.row_offset = 1;
                    if (invalid == 2) args.A.rows = 15;
                    if (invalid == 3) args.A.row_stride_bytes = 31;
                    if (invalid == 4) args.A.row_stride_bytes = std::numeric_limits<size_t>::max();
                    if (invalid == 5) args.A.bytes->resize(511);
                }
                const auto original_metadata = args.act_quant.storage();
                require(!ggml_gemmini_fpga_execute(args, pipeline, [&] { ++quantization_calls; },
                            "invalid-input-contract"), "invalid input reached FPGA execution");
                require(ggml_gemmini_fpga_last_error() == (bias ? "FPGA low-width bias unsupported" :
                            metadata ? "FPGA requires EXSIA RMD OFF metadata" :
                            "FPGA requires a bounded A8 activation view"),
                        "invalid input was not rejected before device selection");
                require(args.act_quant.storage().index() == original_metadata.index(), "rejection reset metadata kind");
                if (const auto *original = std::get_if<quants::act::exsia::Meta>(&original_metadata)) {
                    const auto &preserved = std::get<quants::act::exsia::Meta>(args.act_quant.storage());
                    require(preserved.rmd_packets == original->rmd_packets &&
                            preserved.direct_residuals == original->direct_residuals,
                            "rejection discarded residual handles");
                }
                require(std::all_of(output.begin(), output.end(), [](float x) { return x == 17.0f; }),
                        "invalid input changed output or padding");
                ++cases;
            }
            require(quantization_calls == 0 && observation.calls == 0 && simulator_creates == 0 &&
                    simulator_executes == 0 && simulator_streams == 0, "rejection executed a backend");
            std::cout << (bias ? "FPGA_UART_BIAS_REJECT" : metadata ? "FPGA_UART_METADATA_REJECT" : "FPGA_UART_ACTIVATION_REJECT")
                      << " PASS cases=" << cases
                      << " quantization_calls=0 simulator_calls=0 device_selected=0 output_preserved=1 metadata_preserved=1\n";
        } else if (command == "reject") {
            require(argc == 2, "reject arguments");
            setenv("GEMMINI_MATMUL_MODE", "FULL", 1);
            for (const bool native : {false, true}) {
                Graph test(16, native ? 49 : 16, 32, native);
                test.prepare(1);
                require(!ggml_backend_supports_op(test.backend, test.output), "unsupported route advertised");
                require(ggml_backend_graph_compute(test.backend, test.graph) == GGML_STATUS_FAILED,
                        "unsupported forced graph must fail closed");
                const auto values = test.values();
                require(std::all_of(values.begin(), values.end(), [](float x) { return x == 17.0f; }),
                        "rejected graph changed caller output");
            }
            require(observation.calls == 0 && simulator_creates == 0 && simulator_executes == 0 && simulator_streams == 0,
                    "unsupported route executed numerical backend");
            std::cout << "FPGA_UART_GGML_REJECT PASS cases=2 output_preserved=1 simulator_calls=0\n";
        } else {
            const bool benchmark = command == "benchmark";
            require((command == "run" && argc == 8) || (command == "failure" && (argc == 5 || argc == 6)) ||
                    (benchmark && (argc == 7 || argc == 8)), "run/failure/benchmark arguments");
            const size_t m = std::stoul(argv[2]), n = std::stoul(argv[3]), k = std::stoul(argv[4]);
            require(m > 0 && m <= 4096 && n > 0 && n <= 256 && k > 0 && k <= 256 && k % 32 == 0,
                    "test native shape bounds");
            const size_t seed = command == "failure" ? 1 : std::stoul(argv[5]);
            const size_t repetitions = benchmark ? (argc == 8 ? std::stoul(argv[6]) : 5) :
                                      command == "run" ? std::stoul(argv[6]) : 1;
            const char *mode = command == "failure" && argc == 5 ? "FULL" : argv[argc-1];
            require(std::string(mode) == "FULL" || std::string(mode) == "STRIPE_PIPELINE", "test mode");
            require(repetitions > 0 && repetitions <= (benchmark ? 10 : 100), "test repetition bound");
            setenv("GEMMINI_MATMUL_MODE", mode, 1);
            observation.benchmark = benchmark;
            Graph test(m, n, k);
            const size_t invocations = repetitions + size_t(benchmark);
            for (size_t repetition = 0; repetition < invocations; ++repetition) {
                const bool warmup = benchmark && repetition == 0;
                if (benchmark)
                    require(setenv("IM2P_FPGA_OBSERVER_REFERENCE", warmup ? "1" : "0", 1) == 0,
                            "benchmark observer label");
                const auto references_before = observation.reference_dot_calls;
                const auto matmuls_before = observation.reference_matmul_calls;
                const auto start = std::chrono::steady_clock::now();
                test.prepare(seed + (benchmark ? 0 : repetition));
                require(ggml_backend_supports_op(test.backend, test.output), "supported route missing from ggml probe");
                const auto status = ggml_backend_graph_compute(test.backend, test.graph);
                const auto values = test.values();
                if (command == "failure") {
                    require(status == GGML_STATUS_FAILED && observation.calls == 0,
                            "expected adapter failure did not propagate");
                    require(std::all_of(values.begin(), values.end(), [](float x) { return x == 17.0f; }),
                            "failed invocation changed caller output");
                } else {
                    require(status == GGML_STATUS_SUCCESS && observation.calls == repetition + 1,
                            "actual ggml dispatch failed or completion observer missing");
                    require(values.size() == observation.expected.size(), "reference output layout");
                    require(std::memcmp(values.data(), observation.expected.data(), values.size() * sizeof(float)) == 0,
                            "f_out float32 bit-exact mismatch");
                }
                require(simulator_creates == 0 && simulator_executes == 0 && simulator_streams == 0, "hidden simulator fallback");
                if (benchmark) {
                    const auto duration = std::chrono::duration_cast<std::chrono::nanoseconds>(
                        std::chrono::steady_clock::now() - start).count();
                    const auto dot_calls = observation.reference_dot_calls - references_before;
                    const auto matmul_calls = observation.reference_matmul_calls - matmuls_before;
                    require(warmup || (dot_calls == 0 && matmul_calls == 0), "timed invocation executed CPU reference");
                    // This outer endpoint includes preparation, tensor copies,
                    // actual quantization, output get/check and adapter RELEASE.
                    std::cout << "HOST_BENCH_SAMPLE backend=FPGA_UART mode=" << mode
                              << " iteration=" << repetition << " warmup=" << warmup
                              << " sustained_ns=" << duration << " graph_prepare_included=1 quantization_included=1"
                              << " output_get_included=1 release_included=1 exact=1"
                              << " validation=" << (warmup ? "reference" : "check")
                              << " reference_dot_calls=" << dot_calls << " reference_matmul_calls=" << matmul_calls
                              << " raw=" << m*n*(k/32) << " f_out=" << m*n << '\n' << std::flush;
                }
            }
            std::cout << "FPGA_UART_GGML_" << (command == "failure" ? "FAILURE" : "NUMERICAL")
                      << " PASS mode=" << mode << " logical=" << invocations
                      << " raw=" << observation.raw_count << " f_out=" << (command != "failure" ? invocations * m * n : 0)
                      << " output_preserved=" << (command == "failure")
                      << " simulator_creates=0 simulator_executes=0"
                      << " validation=" << (benchmark ? "reference_warmup_cached_check_measured" : "cpu_reference_after_fpga")
                      << " reference_dot_calls=" << observation.reference_dot_calls
                      << " reference_matmul_calls=" << observation.reference_matmul_calls << '\n';
            if (benchmark) {
                std::cout << "HOST_BENCH_PASS measured=" << repetitions << " warmups=1 unique_inputs=1"
                          << " raw=" << observation.raw_count << " f_out=" << invocations*m*n
                          << " timed_reference_dot_calls=0 timed_reference_matmul_calls=0 simulator_calls=0\n";
                unsetenv("IM2P_FPGA_OBSERVER_REFERENCE");
            }
        }
        ggml_gemmini_fpga_set_observer(nullptr, nullptr);
    } catch (const std::exception &error) {
        ggml_gemmini_fpga_set_observer(nullptr, nullptr);
        std::cerr << "FPGA_UART_GGML_FAIL: " << error.what() << '\n';
        return 1;
    }
}
