// Replay model_observer captures through the original frontend and real RTL.
// Link with --wrap=im2p_execute_matmul_extended --wrap=im2p_begin_striped_matmul.
#include "ggml-gemmini-args.h"
#include "im2p_gemmini_frontend.hpp"
#include "quants/act/exsia/exsia.hpp"
#include "json.hpp"
#include "model_capture.hpp"
#include "residual/rmd/rmd-im2p-executor.hpp"
#include "residual/rmd/rmd-compose.hpp"
#include <memory>
#include <algorithm>
#include <bit>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

#ifndef IM2P_MODEL_REPLAY_MAIN_EXTERNAL
#error "select immutable R0=0 or current R1 main_external=1 explicitly"
#endif
static_assert(IM2P_MODEL_REPLAY_MAIN_EXTERNAL == 0 || IM2P_MODEL_REPLAY_MAIN_EXTERNAL == 1);
static_assert(IM2P_ABI_VERSION == (IM2P_MODEL_REPLAY_MAIN_EXTERNAL ? 5 : 4));
static_assert(std::endian::native == std::endian::little && sizeof(float) == 4 &&
              std::numeric_limits<float>::is_iec559);
static_assert(DIM == 16 && GGML_GEMMINI_ACTIVATION_BITS == 8 && GGML_GEMMINI_WEIGHT_BITS == 8);

namespace fs = std::filesystem;
namespace exsia = ggml::gemmini::quants::act::exsia;
using namespace im2p::gemmini;
using nlohmann::json;
namespace rmd = ggml::gemmini::rmd;

static void require(bool valid, const char *message) {
    if (!valid) throw std::runtime_error(message);
}
static size_t product(size_t a, size_t b) {
    require(b == 0 || a <= SIZE_MAX / b, "capture extent overflow");
    return a * b;
}
template<class T> static T integer(const json &value) {
    require(value.is_number_integer(), "metadata integer required");
    if (value.is_number_unsigned())
        require(value.get<uint64_t>() <= uint64_t(INT64_MAX), "metadata integer overflow");
    const auto number = value.get<int64_t>();
    require(number >= static_cast<int64_t>(std::numeric_limits<T>::lowest()) &&
            (number < 0 || uint64_t(number) <= uint64_t(std::numeric_limits<T>::max())),
            "metadata integer out of range");
    return static_cast<T>(number);
}
template<class T> static std::vector<T> read(const fs::path &path, size_t count) {
    const size_t bytes = product(count, sizeof(T));
    require(bytes <= size_t(std::numeric_limits<std::streamsize>::max()) &&
            fs::file_size(path) == bytes, "capture byte count mismatch");
    std::vector<T> values(count);
    std::ifstream input(path, std::ios::binary);
    input.read(reinterpret_cast<char *>(values.data()), static_cast<std::streamsize>(bytes));
    require(bool(input), "capture read failed");
    return values;
}
static bool same_float(float a, float b) {
    return std::bit_cast<uint32_t>(a) == std::bit_cast<uint32_t>(b);
}
static double hp1_factor(const block_q8_hp1 &weight) {
    if (weight.m == INT16_MIN) return 0.0;
    // Independent scalar form of immutable main's FP32 HP1 scale contract.
    const double scaled = std::ldexp(double(weight.channel_scale), weight.m);
    if (!std::isnormal(weight.channel_scale)) return double(float(scaled));
    if (scaled < std::numeric_limits<float>::min()) return 0.0;
    return double(float(std::min(scaled, double(std::numeric_limits<float>::max()))));
}

struct Capture {
    ggml_gemmini_args_t args{};
    std::vector<block_q8_h1> h1;
    std::vector<block_q8_hp1> hp1;
    std::vector<int64_t> raw;
    std::vector<float> frozen, output;
    bool publication_captured = false;
    size_t residual_nonzero = 0;
    std::string captured_mode;
    std::vector<exsia::StripeReadyEvent> events;

    explicit Capture(const std::string &prefix) {
        std::ifstream input(prefix + ".json");
        const auto meta = json::parse(input);
        publication_captured = model_capture::validate_publication(meta);
        captured_mode = publication_captured ? meta.at("mode").get<std::string>() : "NOT_CAPTURED";
        args.I = integer<size_t>(meta.at("M"));
        args.J = integer<size_t>(meta.at("N"));
        args.K = integer<size_t>(meta.at("K"));
        require(args.I && args.J && args.K && args.K % 32 == 0 &&
                meta.at("I") == args.I && meta.at("J") == args.J &&
                meta.at("activation_bits") == 8 && meta.at("block_size") == 32 &&
                meta.at("direct_residuals") == 0 &&
                meta.at("numerical_contract") == "main_external" &&
                meta.at("raw_exact") == true && meta.at("fout_exact") == true,
                "capture requires completed main A8 native K32 invocation; CPU-direct is separate");
        args.tile_I = integer<size_t>(meta.at("tile_I"));
        args.tile_J = integer<size_t>(meta.at("tile_J"));
        args.tile_K = integer<size_t>(meta.at("tile_K"));
        args.activation_rows_per_stripe = integer<size_t>(meta.at("stripe_rows"));
        require(args.activation_rows_per_stripe != 0, "zero stripe rows");
        const size_t stripes = 1 + (args.I - 1) / args.activation_rows_per_stripe;
        require(meta.at("stripes") == stripes && meta.at("theta").is_array() &&
                meta.at("theta").size() == stripes, "capture stripe extent");
        auto &activation_meta = args.act_quant.storage().emplace<exsia::Meta>();
        activation_meta.e_s = integer<int16_t>(meta.at("e_s"));
        activation_meta.rho = integer<int16_t>(meta.at("rho"));
        activation_meta.sigma = integer<int32_t>(meta.at("sigma"));
        for (const auto &value : meta.at("theta")) {
            const auto theta = integer<int16_t>(value);
            require(theta != INT16_MIN && std::isfinite(std::ldexp(1.0f, theta)) &&
                    std::ldexp(1.0f, theta) > 0, "capture activation scale");
            activation_meta.theta.push_back(theta);
        }
        const auto activation = read<int8_t>(prefix + ".activation-i8.bin", product(args.I, args.K));
        require(args.A.allocate(args.I, args.K, 8) && args.A.row_stride_bytes == args.K,
                 "capture activation allocation");
        if (publication_captured) {
            args.A.row_stride_bytes = integer<size_t>(meta.at("activation_row_stride_bytes"));
            args.A.bytes->resize(product(args.I, args.A.row_stride_bytes));
        }
        for (size_t row = 0; row < args.I; ++row)
            std::memcpy(args.A.bytes->data() + row * args.A.row_stride_bytes,
                        activation.data() + row * args.K, args.K);
        args.sA = args.A.row_stride_bytes; args.sB = args.K;
        for (size_t id = 0, row = 0; row < args.I; ++id) {
            exsia::StripeReadyEvent event{};
            if (publication_captured) {
                const auto &saved = meta.at("events").at(id);
                event.run_id = model_capture::natural(saved.at("run_id"));
                event.stripe_id = integer<size_t>(saved.at("stripe_id"));
                event.slot = integer<size_t>(saved.at("slot"));
                event.row_begin = integer<size_t>(saved.at("row_begin"));
                event.row_end = integer<size_t>(saved.at("row_end"));
                event.folding_commit_ns = model_capture::natural(saved.at("folding_commit_ns"));
                if (saved.at("residual_route") == "ws_packet") {
                    auto packet = std::make_shared<const rmd::StripePacket>(
                        model_capture::packet_from_json<rmd::StripePacket>(saved.at("residual_packet")));
                    event.rmd_packet = packet;
                    activation_meta.rmd_packets.push_back(packet);
                }
                event.activation_metadata = exsia::StripeMetadataSnapshot{
                    integer<int16_t>(saved.at("e_s")), integer<int16_t>(saved.at("rho")),
                    integer<int32_t>(saved.at("sigma")), integer<int16_t>(saved.at("theta"))};
            } else {
                // Legacy captures contain no event identity. Keep numerical
                // replay available, without claiming original publication replay.
                event.run_id = 1; event.stripe_id = id; event.slot = id % 2;
                event.row_begin = row;
                event.row_end = row + std::min(args.activation_rows_per_stripe, args.I - row);
            }
            events.push_back(event);
            row = event.row_end;
        }
        require(meta.at("accelerator_residuals") == activation_meta.rmd_packets.size(), "captured residual packet coverage");
        const size_t blocks = args.K / 32, weights = product(args.J, blocks);
        const auto format = meta.at("format").get<std::string>();
        if (format == "Q8_H1") {
            require(meta.at("native_block_bytes") == sizeof(block_q8_h1), "H1 native ABI mismatch");
            h1 = read<block_q8_h1>(prefix + ".native-weight.bin", weights);
            args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_h1;
            args.q8_h1_blocks = h1.data(); args.q8_h1_block_count = weights;
            args.q8_h1_rows = args.J; args.blocks_per_row = blocks;
            args.native_weight_bytes = product(weights, sizeof(block_q8_h1));
        } else {
            require(format == "Q8_HP1" && meta.at("native_block_bytes") == sizeof(block_q8_hp1),
                    "HP1 native ABI mismatch");
            hp1 = read<block_q8_hp1>(prefix + ".native-weight.bin", weights);
            args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1;
            args.q8_hp1_blocks = hp1.data(); args.q8_hp1_block_count = weights;
            args.q8_hp1_blocks_per_row = blocks;
            args.native_weight_bytes = product(weights, sizeof(block_q8_hp1));
        }
        const size_t outputs = product(args.I, args.J);
        raw = read<int64_t>(prefix + ".raw-i64.bin", product(outputs, blocks));
        frozen = read<float>(prefix + ".fout-f32.bin", outputs);
        require(meta.at("raw_comparisons") == raw.size() && meta.at("fout_comparisons") == outputs,
                "capture comparison extent");
        output.assign(outputs, 17.0f);
        args.f_out = output.data(); args.stride_f_out = args.J; args.col_stride_f_out = 1;
        require(hp1.empty() ? args.has_q8_h1_im2p_contract() : args.has_q8_hp1_im2p_contract(),
                "native replay contract");
        const auto geometry = args.activation_geometry();
        require(geometry.ok() && geometry.geometry.stripe_rows == args.activation_rows_per_stripe &&
                geometry.geometry.stripe_count == stripes, "captured host geometry mismatch");
        std::vector<float> reconstructed(outputs, 0.0f);
        for (size_t row = 0; row < args.I; ++row) {
            const double scale = std::ldexp(1.0, activation_meta.theta[row / args.activation_rows_per_stripe]);
            for (size_t col = 0; col < args.J; ++col) {
                double sum = 0;
                for (size_t block = 0; block < blocks; ++block) {
                    const size_t at = col * blocks + block;
                    const auto *codes = hp1.empty() ? h1[at].qs : hp1[at].qs;
                    int64_t dot = 0;
                    for (size_t lane = 0; lane < 32; ++lane)
                        dot += int64_t(activation[row * args.K + block * 32 + lane]) * int64_t(codes[lane]);
                    require(dot == raw[(block * args.I + row) * args.J + col], "frozen raw differs from scalar dot");
                    const float channel = hp1.empty() ? h1[at].s_rf : hp1[at].channel_scale;
                    require(std::isfinite(channel) && channel >= 0, "invalid captured channel scale");
                    const double factor = hp1.empty()
                        ? double(channel) * double(uint32_t(h1[at].c_b) + h1[at].R) : hp1_factor(hp1[at]);
                    sum += double(dot) * factor * scale;
                }
                // R0 publishes with += into a stage initialized to positive zero.
                float expected = 0.0f;
                expected += float(sum);
                require(std::isfinite(sum), "frozen dense reconstruction overflow");
                reconstructed[row * args.J + col] = expected;
            }
        }
        residual_nonzero = model_capture::residual_oracle(args, activation_meta, reconstructed);
        if (meta.contains("residual_nonzero"))
            require(meta.at("residual_nonzero") == residual_nonzero, "captured residual nonzero count");
        for (size_t at = 0; at < outputs; ++at)
            require(same_float(reconstructed[at], frozen[at]), "frozen f_out differs from independent dense/residual oracle");
    }
};

// Reuse portable_replay's provider interposition; all work reaches real RTL.
struct Observation {
    Capture &capture;
    im2p_provider_t downstream{};
    std::vector<uint8_t> seen;
    size_t starts = 0;
    explicit Observation(Capture &value) : capture(value), seen(value.raw.size(), 0) {}
    template<class Descriptor> bool begin(const Descriptor &d) {
        const auto &a = capture.args;
        if (d.abi_version != IM2P_ABI_VERSION || d.activation_bits != 8 || d.weight_bits != 8 ||
            d.dim != 16 || d.vector_op != IM2P_VECTOR_EXTERNAL || d.block_size != 32 ||
            d.m != a.I || d.n != a.J || d.k != a.K || !d.provider.read_weight_i8 ||
            !d.provider.read_scale || !d.provider.write_output || starts++) return false;
#if IM2P_MODEL_REPLAY_MAIN_EXTERNAL
        if (d.output_domain != IM2P_OUTPUT_LEGACY_BLOCK) return false;
#endif
        downstream = d.provider;
        return true;
    }
    static int weight(void *context, size_t row, size_t col, size_t count, int8_t *out) {
        auto &self = *static_cast<Observation *>(context);
        return self.downstream.read_weight_i8(self.downstream.context, row, col, count, out);
    }
#if IM2P_MODEL_REPLAY_MAIN_EXTERNAL
    using Scale = uint32_t;
#else
    using Scale = int8_t;
#endif
    static int scale(void *context, size_t block, size_t col, size_t count, Scale *out) {
        auto &self = *static_cast<Observation *>(context);
        return self.downstream.read_scale(self.downstream.context, block, col, count, out);
    }
    static int output(void *context, size_t block, size_t row, size_t col, size_t count,
                      const int64_t *values
#if IM2P_MODEL_REPLAY_MAIN_EXTERNAL
                      , uint32_t domain
#endif
    ) {
        auto &self = *static_cast<Observation *>(context);
        const auto &a = self.capture.args;
#if IM2P_MODEL_REPLAY_MAIN_EXTERNAL
        if (domain != IM2P_OUTPUT_LEGACY_BLOCK) return IM2P_ERROR;
#endif
        if (!values || !count || count > DIM || block >= a.K / 32 || row >= a.I ||
            col >= a.J || count > a.J - col) return IM2P_ERROR;
        for (size_t lane = 0; lane < count; ++lane) {
            const size_t at = (block * a.I + row) * a.J + col + lane;
            if (self.seen[at]++ || values[lane] != self.capture.raw[at]) return IM2P_ERROR;
        }
        return self.downstream.write_output(self.downstream.context, block, row, col, count, values
#if IM2P_MODEL_REPLAY_MAIN_EXTERNAL
                                             , domain
#endif
        );
    }
    im2p_provider_t provider() { return {this, weight, nullptr, scale, output}; }
};
static Observation *active = nullptr;
static thread_local bool residual_execution = false;
struct ResidualReplay {
    Capture &capture;
    size_t completed = 0;
    uint64_t dot_calls = 0;
    static Status execute(void *context, im2p_sim_t *sim, const exsia::StripeReadyEvent &event,
                          ResidualStageView stage, ResidualStripeStats &stats) noexcept {
        auto &self = *static_cast<ResidualReplay *>(context);
        try {
            require(event.stripe_id == self.completed && !event.direct_residual,
                    "residual replay completion identity");
            if (event.rmd_packet) {
                require(sim && stage.data && stage.element_count == self.capture.output.size(),
                        "residual replay simulator/private output missing");
                rmd::CompressedOutput compressed;
                rmd::RmdExecutionMetrics metrics{};
                residual_execution = true;
                const auto executed = rmd::execute_rmd_stripe_im2p(sim, self.capture.args,
                    *event.rmd_packet, compressed, &metrics);
                residual_execution = false;
                require(executed == rmd::RmdStatus::success && metrics.im2p_dot_calls > 0,
                        "residual real IM2P compact execution failed");
                rmd::Correction correction = rmd::BlockScaledInt64Correction{};
                require(rmd::compose_rmd_output(*event.rmd_packet, compressed, correction) == rmd::RmdStatus::success &&
                        rmd::merge_rmd_correction_to(self.capture.args, stage.data, event.row_begin, event.row_end,
                                                     correction) == rmd::RmdStatus::success,
                        "residual checked compose/merge failed");
                self.dot_calls += metrics.im2p_dot_calls;
                stats.rmd_dot_calls = metrics.im2p_dot_calls;
                rmd::detail::expand_im2p_provider_stats(metrics.im2p_stats, stats.rmd_stats);
            }
            ++self.completed;
            return {};
        } catch (...) {
            residual_execution = false;
            return {StatusCode::execution_failure, Route::unknown, false, "residual replay failed"};
        }
    }
};
extern "C" int __real_im2p_execute_matmul_extended(im2p_sim_t *, const im2p_matmul_desc_t *, im2p_work_stats_extended_t *);
extern "C" int __wrap_im2p_execute_matmul_extended(im2p_sim_t *sim, const im2p_matmul_desc_t *d,
                                                 im2p_work_stats_extended_t *stats) {
    if (residual_execution) return __real_im2p_execute_matmul_extended(sim, d, stats);
    if (!active || !d || !active->begin(*d)) return IM2P_ERROR;
    auto copy = *d; copy.provider = active->provider();
    return __real_im2p_execute_matmul_extended(sim, &copy, stats);
}
extern "C" int __real_im2p_begin_striped_matmul(im2p_sim_t *, const im2p_stripe_work_desc_t *, im2p_stream_t **);
extern "C" int __wrap_im2p_begin_striped_matmul(im2p_sim_t *sim, const im2p_stripe_work_desc_t *d,
                                             im2p_stream_t **stream) {
    if (!active || !d || !active->begin(*d)) return IM2P_ERROR;
    auto copy = *d; copy.provider = active->provider();
    return __real_im2p_begin_striped_matmul(sim, &copy, stream);
}

int main(int argc, char **argv) {
    try {
        const float underflow = float(-std::ldexp(1.0, -150));
        float committed = 0.0f;
        committed += underflow;
        require(std::bit_cast<uint32_t>(underflow) == 0x80000000u &&
                std::bit_cast<uint32_t>(committed) == 0, "R0 underflow publication anchor failed");
        require(argc == 3, "usage: model_replay CAPTURE_PREFIX FULL|STRIPE_PIPELINE");
        const std::string mode_name = argv[2];
        require(mode_name == "FULL" || mode_name == "STRIPE_PIPELINE", "unsupported replay mode");
        require(im2p_sim_abi_version() == IM2P_ABI_VERSION && im2p_sim_activation_bits() == 8 &&
                im2p_sim_weight_bits() == 8 && im2p_sim_dim() == DIM, "linked real simulator identity mismatch");
        Capture capture(argv[1]);
        Observation observation(capture);
        active = &observation;
        Options options{1000000};
#if IM2P_MODEL_REPLAY_MAIN_EXTERNAL
        options.numerical_contract = NumericalContract::main_external;
#endif
        const auto mode = mode_name == "FULL" ? Mode::full : Mode::stripe_pipeline;
        const auto &activation_meta = std::get<exsia::Meta>(capture.args.act_quant.storage());
        const bool has_residual = !activation_meta.rmd_packets.empty();
        ResidualReplay residual{capture};
        if (has_residual && mode == Mode::stripe_pipeline) {
            options.residual_stage_mode = ResidualStageMode::im2p_compact;
            options.residual_stage_context = &residual;
            options.residual_stage_fn = ResidualReplay::execute;
        }
        auto started = execute(&capture.args, mode, options);
        require(started.status.ok() && bool(started.run), started.status.message);
        if (mode == Mode::stripe_pipeline) {
            const auto &meta = std::get<exsia::Meta>(capture.args.act_quant.storage());
            for (const auto &event : capture.events) {
                const auto status = submit_stripe(*started.run, event, StripeMetadata{true, meta.theta[event.stripe_id]});
                require(status.ok(), status.message);
            }
        }
        const auto done = fence(*started.run);
        require(done.status.ok(), done.status.message);
        if (mode == Mode::stripe_pipeline)
            require(authorize_output_commit(*started.run, true).ok(), "replay output authorization failed");
        if (has_residual && mode == Mode::full) {
            std::unique_ptr<im2p_sim_t, decltype(&im2p_sim_destroy)> simulator(im2p_sim_create(), im2p_sim_destroy);
            require(bool(simulator), "FULL residual simulator allocation failed");
            for (const auto &event : capture.events) {
                ResidualStripeStats stats{};
                const auto status = ResidualReplay::execute(&residual, simulator.get(), event,
                    {capture.output.data(), capture.output.size()}, stats);
                require(status.ok(), status.message);
            }
        }
        if (has_residual)
            require(residual.completed == capture.events.size() && residual.dot_calls > 0,
                    "residual semantic completion missing");
        require(observation.starts == 1 &&
                std::all_of(observation.seen.begin(), observation.seen.end(), [](uint8_t x) { return x == 1; }),
                "replay raw observation incomplete");
        for (size_t at = 0; at < capture.output.size(); ++at)
            require(same_float(capture.output[at], capture.frozen[at]), "replay f_out differs from frozen R2/scalar");
        require(done.stats.base.work_total_cycles && done.stats.base.activation_read_requests &&
                done.stats.base.weight_read_requests && done.stats.base.output_write_requests,
                "replay real RTL work counters missing");
        std::cout << "MODEL_REPLAY_PASS reference=" << (IM2P_MODEL_REPLAY_MAIN_EXTERNAL ? "R1" : "R0")
                  << " mode=" << mode_name << " M=" << capture.args.I << " N=" << capture.args.J
                  << " K=" << capture.args.K << " raw_exact=" << capture.raw.size()
                  << " fout_exact=" << capture.output.size() << " frozen_R2=equal independent_scalar=equal"
                  << " publication_identity=" << (capture.publication_captured ? "CAPTURED" : "NOT_CAPTURED")
                  << " call_order=" << (capture.captured_mode == mode_name ? "CAPTURED_MODE" : "MODE_REPLAY_ONLY")
                  << " residual_nonzero=" << capture.residual_nonzero << " residual_dot_calls=" << residual.dot_calls
                  << " rtl_cycles=" << done.stats.base.work_total_cycles << '\n';
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "MODEL_REPLAY_FAIL " << error.what() << '\n';
        return 1;
    }
}
