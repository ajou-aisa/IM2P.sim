// Q8RPLY01 schema 1 from graph_dispatch: replay identical numerical inputs.
// Build separately with immutable R0 ABI4 or current R1 ABI5 native archives.
#include "ggml-gemmini-args.h"
#include "im2p_gemmini_frontend.hpp"
#include "quants/act/exsia/exsia.hpp"
#include "quants/weight/quantize_Q8_H1.hpp"
#include "residual/direct/direct-builder.hpp"
#include "residual/direct/direct-executor.hpp"
#include "residual/rmd/rmd-compose.hpp"
#include <algorithm>
#include <bit>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <vector>

static_assert(IM2P_MODEL_REPLAY_MAIN_EXTERNAL == 0 || IM2P_MODEL_REPLAY_MAIN_EXTERNAL == 1);
static_assert(IM2P_ABI_VERSION == (IM2P_MODEL_REPLAY_MAIN_EXTERNAL ? 5 : 4));
static_assert(std::endian::native == std::endian::little && sizeof(float) == 4);
using namespace ggml::gemmini;
using namespace im2p::gemmini;
namespace exsia = quants::act::exsia;
static void require(bool ok, const char *why) { if (!ok) throw std::runtime_error(why); }
static bool exact(float a, float b) { return std::bit_cast<uint32_t>(a) == std::bit_cast<uint32_t>(b); }
template<class T> static std::vector<T> read(std::ifstream &file, size_t count) {
    std::vector<T> result(count);
    file.read(reinterpret_cast<char *>(result.data()), count * sizeof(T));
    require(bool(file), "truncated Q8_0 capture");
    return result;
}

struct Capture {
    ggml_gemmini_args_t args{};
    std::vector<block_q8_h1> weights;
    std::vector<exsia::StripeReadyEvent> events;
    std::vector<int64_t> frozen_raw, scalar_raw;
    std::vector<float> frozen, scalar, output;
    size_t residual_events = 0, residual_callbacks = 0;
    explicit Capture(const char *path) {
        require(std::filesystem::file_size(path) <= 64 * 1024 * 1024, "capture size bound");
        std::ifstream file(path, std::ios::binary);
        const auto h = read<uint64_t>(file, 12);
        require(h[0] == 0x3130594c50523851ULL && h[1] == 1 && h[10] == sizeof(block_q8_h1), "capture schema/native ABI");
        args.I = h[2]; args.J = h[3]; args.K = h[4];
        require(args.I && args.I <= 4096 && args.J && args.J <= 4096 && args.K && args.K <= 4096 &&
                args.K % 32 == 0 && args.I * args.J * (args.K / 32) <= 4 * 1024 * 1024,
                "capture logical extent");
        args.tile_I = h[5]; args.tile_J = h[6]; args.tile_K = h[7];
        args.activation_rows_per_stripe = h[8];
        require(h[8] && h[8] == h[5] * DIM && h[9] >= args.K && h[9] <= 8192, "capture main stripe/stride");
        const size_t stripes = 1 + (args.I - 1) / h[8], blocks = args.K / 32, count = args.J * blocks;
        require(h[11] <= stripes, "capture residual stripe count");
        const auto scales = read<int32_t>(file, 3);
        auto &meta = args.act_quant.storage().emplace<exsia::Meta>();
        require(scales[0] >= INT16_MIN && scales[0] <= INT16_MAX && scales[1] >= INT16_MIN && scales[1] <= INT16_MAX,
                "activation metadata width");
        meta.e_s = scales[0]; meta.rho = scales[1]; meta.sigma = scales[2];
        meta.theta = read<int16_t>(file, stripes);
        for (auto theta : meta.theta) require(theta != INT16_MIN && std::isfinite(std::ldexp(1.0, theta)), "activation theta");
        const auto original = read<quants::BlockQ8_0>(file, count);
        const auto observed = read<block_q8_h1>(file, count);
        weights.resize(count);
        std::vector<uint8_t> codes(blocks);
        std::vector<int8_t> qs(args.K);
        // Each reference uses its existing row quantizer on original Q8_0 bytes.
        // The captured H1 result is comparison data, never an execution input.
        for (size_t col = 0; col < args.J; ++col) {
            quants::BlockQ8_H1 row{0, 0, codes.data(), qs.data()};
            for (size_t b = 0; b < blocks; ++b)
                require((original[col * blocks + b].d & 0x7c00) != 0x7c00, "nonfinite original weight scale");
            require(quants::quantize_row_q8_h1(original.data() + col * blocks, int(blocks), &row), "original row quantizer failed");
            for (size_t b = 0; b < blocks; ++b) {
                auto &w = weights[col * blocks + b];
                w.s_rf = row.s_rf; w.R = row.R; w.c_b = codes[b];
                std::memcpy(w.qs, qs.data() + b * 32, 32);
                const auto &r2 = observed[col * blocks + b];
                require(exact(w.s_rf, r2.s_rf) && w.R == r2.R && w.c_b == r2.c_b &&
                        std::memcmp(w.qs, r2.qs, 32) == 0, "original Q8_0 reprocessing differs from R2");
            }
        }
        require(args.A.allocate(args.I, args.K, 8), "activation allocation");
        args.A.row_stride_bytes = h[9];
        const auto activation = read<uint8_t>(file, args.I * h[9]);
        args.A.bytes->assign(activation.begin(), activation.end());
        args.sA = h[9]; args.sB = args.K;
        args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_h1;
        args.q8_h1_blocks = weights.data(); args.q8_h1_block_count = count;
        args.q8_h1_rows = args.J; args.blocks_per_row = blocks;
        args.block_size_k = 32; args.native_weight_bytes = count * sizeof(block_q8_h1);
        args.residual_route = residual::ResidualRoute::cpu_direct;
        for (size_t id = 0; id < stripes; ++id) {
            exsia::StripeReadyEvent e{};
            e.run_id = 1; e.stripe_id = id; e.slot = id % 2;
            e.row_begin = id * h[8]; e.row_end = std::min(args.I, e.row_begin + h[8]);
            e.activation_metadata = exsia::StripeMetadataSnapshot{meta.e_s, meta.rho, meta.sigma, meta.theta[id]};
            events.push_back(e);
        }
        for (size_t i = 0; i < h[11]; ++i) {
            const auto extent = read<uint64_t>(file, 6);
            require(extent[0] < stripes && extent[1] == events[extent[0]].row_begin &&
                    extent[2] == events[extent[0]].row_end - extent[1] && extent[3] == args.K && extent[4] == args.J &&
                    extent[5] && extent[5] <= extent[2] * args.K && !events[extent[0]].direct_residual,
                    "direct payload extent/identity");
            residual::DirectStripeBuilder builder;
            builder.reset(extent[0], extent[1], extent[2], args.K, args.J);
            for (size_t j = 0; j < extent[5]; ++j) {
                const auto v = read<int64_t>(file, 3);
                require(v[0] >= 0 && uint64_t(v[0]) < extent[2] && v[1] >= 0 && uint64_t(v[1]) < args.K &&
                        v[2] >= INT32_MIN && v[2] <= INT32_MAX && v[2] != 0 &&
                        builder.add_residual(size_t(v[0]), size_t(v[1]), int32_t(v[2])), "direct residual event");
            }
            auto payload = builder.finish();
            require(bool(payload), "direct residual payload validation");
            events[extent[0]].direct_residual = payload;
            meta.direct_residuals.push_back(payload);
            residual_events += payload->events.size();
        }
        frozen_raw = read<int64_t>(file, args.I * args.J * blocks);
        frozen = read<float>(file, args.I * args.J);
        require(file.peek() == std::ifstream::traits_type::eof(), "capture trailing data");
        output.assign(frozen.size(), 17.0f); scalar.assign(frozen.size(), 0.0f); scalar_raw.resize(frozen_raw.size());
        args.f_out = output.data(); args.stride_f_out = args.J; args.col_stride_f_out = 1;
        require(args.has_q8_h1_im2p_contract() && residual_events, "Q8_0 native/direct coverage");
        for (size_t row = 0; row < args.I; ++row) for (size_t col = 0; col < args.J; ++col) {
            double sum = 0;
            for (size_t b = 0; b < blocks; ++b) {
                const auto &w = weights[col * blocks + b];
                int64_t dot = 0;
                for (size_t k = 0; k < 32; ++k) dot += int64_t(args.A.get(row, b * 32 + k)) * w.qs[k];
                const size_t at = (b * args.I + row) * args.J + col;
                scalar_raw[at] = dot;
                require(dot == frozen_raw[at], "independent original-input raw differs from R2");
                sum += double(dot) * (double(w.s_rf) * double(uint32_t(w.c_b) + w.R));
            }
            scalar[row * args.J + col] += float(sum * std::ldexp(1.0, meta.theta[row / h[8]]));
        }
        for (const auto &p : meta.direct_residuals) {
            std::vector<__int128> correction(p->row_count * args.J, 0);
            for (const auto &e : p->events) for (size_t col = 0; col < args.J; ++col) {
                const auto &w = weights[col * blocks + e.original_k / 32];
                correction[e.local_row * args.J + col] += __int128(e.residual) * w.qs[e.original_k % 32] * (uint32_t(w.c_b) + w.R);
            }
            for (size_t row = 0; row < p->row_count; ++row) for (size_t col = 0; col < args.J; ++col) {
                const auto value = correction[row * args.J + col];
                require(value >= INT64_MIN && value <= INT64_MAX, "independent residual INT64 bound");
                scalar[(p->row_begin + row) * args.J + col] += float(double(int64_t(value)) *
                    double(weights[col * blocks].s_rf) * std::ldexp(1.0, meta.theta[p->stripe_id]));
            }
        }
        for (size_t i = 0; i < scalar.size(); ++i) require(exact(scalar[i], frozen[i]), "independent original-input f_out differs from R2");
    }
    static Status direct(void *opaque, im2p_sim_t *, const exsia::StripeReadyEvent &event,
                         ResidualStageView stage, ResidualStripeStats &) noexcept {
        auto &self = *static_cast<Capture *>(opaque);
        if (event.stripe_id != self.residual_callbacks || stage.element_count != self.output.size())
            return {StatusCode::invalid_contract, Route::unknown, false, "direct callback identity"};
        if (event.direct_residual) {
            rmd::Correction correction = rmd::BlockScaledInt64Correction{};
            if (residual::execute_direct_stripe(self.args, *event.direct_residual, correction) != rmd::RmdStatus::success ||
                rmd::merge_rmd_correction_to(self.args, stage.data, event.row_begin, event.row_end, correction) != rmd::RmdStatus::success)
                return {StatusCode::execution_failure, Route::unknown, false, "original CPU-direct residual failed"};
        }
        ++self.residual_callbacks;
        return {};
    }
};

struct Observation {
    Capture &capture;
    im2p_provider_t downstream{};
    size_t starts = 0;
    std::vector<uint8_t> seen;
    explicit Observation(Capture &c) : capture(c), seen(c.scalar_raw.size(), 0) {}
    template<class T> bool begin(const T &d) {
        if (starts++ || d.vector_op != IM2P_VECTOR_EXTERNAL || d.m != capture.args.I ||
            d.n != capture.args.J || d.k != capture.args.K || !d.provider.write_output) return false;
        downstream = d.provider;
        return true;
    }
    static int weight(void *p, size_t row, size_t col, size_t n, int8_t *out) {
        auto &s = *static_cast<Observation *>(p);
        return s.downstream.read_weight_i8(s.downstream.context, row, col, n, out);
    }
#if IM2P_MODEL_REPLAY_MAIN_EXTERNAL
    using Scale = uint32_t;
#else
    using Scale = int8_t;
#endif
    static int scale(void *p, size_t block, size_t col, size_t n, Scale *out) {
        auto &s = *static_cast<Observation *>(p);
        return s.downstream.read_scale(s.downstream.context, block, col, n, out);
    }
    static int output(void *p, size_t block, size_t row, size_t col, size_t n, const int64_t *values
#if IM2P_MODEL_REPLAY_MAIN_EXTERNAL
                      , uint32_t domain
#endif
    ) {
        auto &s = *static_cast<Observation *>(p);
        const auto &a = s.capture.args;
#if IM2P_MODEL_REPLAY_MAIN_EXTERNAL
        if (domain != IM2P_OUTPUT_LEGACY_BLOCK) return IM2P_ERROR;
#endif
        if (!values || !n || block >= a.K / 32 || row >= a.I || col >= a.J || n > a.J - col) return IM2P_ERROR;
        for (size_t i = 0; i < n; ++i) {
            const size_t at = (block * a.I + row) * a.J + col + i;
            if (s.seen[at]++ || values[i] != s.capture.scalar_raw[at]) return IM2P_ERROR;
        }
        return s.downstream.write_output(s.downstream.context, block, row, col, n, values
#if IM2P_MODEL_REPLAY_MAIN_EXTERNAL
                                         , domain
#endif
        );
    }
    im2p_provider_t provider() { return {this, weight, nullptr, scale, output}; }
};
static Observation *active;
extern "C" int __real_im2p_execute_matmul_extended(im2p_sim_t *, const im2p_matmul_desc_t *, im2p_work_stats_extended_t *);
extern "C" int __wrap_im2p_execute_matmul_extended(im2p_sim_t *s, const im2p_matmul_desc_t *d, im2p_work_stats_extended_t *stats) {
    if (!active || !d || !active->begin(*d)) return IM2P_ERROR;
    auto copy = *d; copy.provider = active->provider();
    return __real_im2p_execute_matmul_extended(s, &copy, stats);
}
extern "C" int __real_im2p_begin_striped_matmul(im2p_sim_t *, const im2p_stripe_work_desc_t *, im2p_stream_t **);
extern "C" int __wrap_im2p_begin_striped_matmul(im2p_sim_t *s, const im2p_stripe_work_desc_t *d, im2p_stream_t **stream) {
    if (!active || !d || !active->begin(*d)) return IM2P_ERROR;
    auto copy = *d; copy.provider = active->provider();
    return __real_im2p_begin_striped_matmul(s, &copy, stream);
}

int main(int argc, char **argv) {
    try {
        require(argc == 3 && (std::string(argv[2]) == "FULL" || std::string(argv[2]) == "STRIPE_PIPELINE"),
                "usage: q8_0_replay CAPTURE.bin FULL|STRIPE_PIPELINE");
        require(im2p_sim_abi_version() == IM2P_ABI_VERSION && im2p_sim_activation_bits() == 8 &&
                im2p_sim_weight_bits() == 8 && im2p_sim_dim() == 16, "real RTL archive identity");
        Capture c(argv[1]); Observation observation(c); active = &observation;
        const auto mode = std::string(argv[2]) == "FULL" ? Mode::full : Mode::stripe_pipeline;
        Options options{1000000};
#if IM2P_MODEL_REPLAY_MAIN_EXTERNAL
        options.numerical_contract = NumericalContract::main_external;
#endif
        if (mode == Mode::stripe_pipeline) {
            options.residual_stage_mode = ResidualStageMode::host_direct;
            options.residual_stage_context = &c; options.residual_stage_fn = Capture::direct;
        }
        auto run = execute(&c.args, mode, options);
        require(run.status.ok() && bool(run.run), run.status.message);
        if (mode == Mode::stripe_pipeline)
            for (const auto &event : c.events) {
                const auto status = submit_stripe(*run.run, event, {true, event.activation_metadata->theta});
                require(status.ok(), status.message);
            }
        const auto done = fence(*run.run);
        require(done.status.ok(), done.status.message);
        if (mode == Mode::stripe_pipeline) require(authorize_output_commit(*run.run, true).ok(), "output authorization");
        else for (const auto &event : c.events) {
            ResidualStripeStats stats{};
            require(Capture::direct(&c, nullptr, event, {c.output.data(), c.output.size()}, stats).ok(), "FULL direct completion");
        }
        require(observation.starts == 1 && std::all_of(observation.seen.begin(), observation.seen.end(), [](uint8_t v) { return v == 1; }) &&
                c.residual_callbacks == c.events.size() && done.stats.base.work_total_cycles &&
                done.stats.base.activation_read_requests && done.stats.base.weight_read_requests && done.stats.base.output_write_requests,
                "real RTL/raw/direct completion conservation");
        for (size_t i = 0; i < c.output.size(); ++i)
            require(exact(c.output[i], c.scalar[i]) && exact(c.output[i], c.frozen[i]), "R0/R1 differs from independent scalar/R2 f_out");
        std::cout << "Q8_0_REPLAY_PASS reference=" << (IM2P_MODEL_REPLAY_MAIN_EXTERNAL ? "R1" : "R0")
                  << " mode=" << argv[2] << " M=" << c.args.I << " N=" << c.args.J << " K=" << c.args.K
                  << " raw_exact=" << c.scalar_raw.size() << " fout_exact=" << c.output.size()
                  << " stripes=" << c.events.size() << " direct_events=" << c.residual_events
                  << " weight_source=original_Q8_0 quantizer=existing_reference_row_helper"
                  << " frozen_R2=equal independent_scalar=equal schema=1 publication_order=NOT_CAPTURED"
                  << " rtl_cycles=" << done.stats.base.work_total_cycles << '\n';
        return 0;
    } catch (const std::exception &e) {
        std::cerr << "Q8_0_REPLAY_FAIL " << e.what() << '\n';
        return 1;
    }
}
