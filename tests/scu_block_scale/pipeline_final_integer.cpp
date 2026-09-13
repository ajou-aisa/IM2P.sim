// Deterministic post-fold publication through the real frontend and RTL simulator.
// This does not run the activation quantizer or claim CPU/device overlap.
// Link with --wrap=im2p_execute_matmul_extended and --wrap=im2p_begin_striped_matmul.
#include "ggml-gemmini-args.h"
#include "im2p_gemmini_frontend.hpp"
#include "quants/act/exsia/exsia.hpp"
#include <gemmini.h>

#include <algorithm>
#include <array>
#include <bit>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <iostream>
#include <limits>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

using namespace im2p::gemmini;
namespace exsia = ggml::gemmini::quants::act::exsia;

namespace {
constexpr size_t M = 321, N = 48, K = 64;
constexpr float Sentinel = 17.0f;
constexpr uint64_t RunId = 0x53435503;
constexpr std::array<int16_t, 3> Theta{-2, 0, 2};

void require(bool ok, const char *message) {
    if (!ok) throw std::runtime_error(message);
}

// Independent scalar fixture formulas. They do not call the DUT's readers,
// scale decoder, accumulator, or reconstruction helpers.
int activation_code(size_t row, size_t fragment) {
    return int((row + 2 * fragment + row / 160) % 5) - 2;
}
int weight_code(size_t column, size_t fragment) {
    return int((column + 3 * fragment) % 3) - 1;
}
int hp1_exponent(size_t column, size_t block) {
    return block == 1 && column % 7 == 0 ? INT16_MIN : int(1 + column % 3 + block);
}
float channel_scale(size_t column) {
    return std::ldexp(1.0f, -16 + int(column % 2));
}
int64_t clamp32(int64_t value) {
    return std::clamp(value, int64_t(INT32_MIN), int64_t(INT32_MAX));
}
int64_t expected_integer(bool hp1, size_t row, size_t column) {
    int64_t sum = 0;
    for (size_t fragment = 0; fragment < 4; ++fragment) {
        const size_t block = fragment / 2;
        const int64_t partial = 16 * activation_code(row, fragment) * weight_code(column, fragment);
        const int exponent = hp1_exponent(column, block);
        const int64_t factor = hp1 ? (exponent == INT16_MIN ? 0 : (INT64_C(1) << exponent))
                                   : 65535 + (block == 0 ? 0 : 255);
        // All fixture products fit INT64 and are overflow-free at signed32.
        const int64_t contribution = partial * factor;
        require(contribution == clamp32(contribution), "fixture must remain S1 overflow-free");
        require(sum + contribution == clamp32(sum + contribution), "fixture accumulator overflow");
        sum = clamp32(sum + clamp32(contribution));
    }
    return sum;
}
float expected_float(bool hp1, size_t row, size_t column) {
    return float(double(expected_integer(hp1, row, column)) * double(channel_scale(column)) *
                 double(std::ldexp(1.0f, Theta[row / 160])));
}

struct Fixture {
    ggml_gemmini_args_t args{};
    std::vector<block_q8_h1> h1{N * 2};
    std::vector<block_q8_hp1> hp1{N * 2};
    std::vector<float> output = std::vector<float>(M * (2 * N + 3), Sentinel);
    bool shift;
    explicit Fixture(bool hp) : shift(hp) {
        args.I = M; args.J = N; args.K = K;
        args.tiled_matmul_type = CPU;
        args.matmul_layer = "scu-pipeline-geometry";
        ggml::gemmini::gemmini_set_tile_ws(&args);
        const auto geometry = args.activation_geometry();
        require(geometry.ok() && geometry.geometry.stripe_rows == 160 &&
                geometry.geometry.stripe_count == 3 && geometry.geometry.final_rows == 1,
                "pinned automatic geometry is not 160/160/1");
        args.activation_rows_per_stripe = geometry.geometry.stripe_rows;
        require(args.A.allocate(M, K, 8), "activation allocation");
        args.sA = K;
        for (size_t row = 0; row < M; ++row)
            for (size_t k = 0; k < K; ++k)
                (*args.A.bytes)[row * K + k] = uint8_t(activation_code(row, k / 16));
        for (size_t column = 0; column < N; ++column) {
            for (size_t block = 0; block < 2; ++block) {
                auto &a = h1[column * 2 + block];
                auto &b = hp1[column * 2 + block];
                a.c_b = block == 0 ? 0 : 255;
                a.R = 65535;
                a.s_rf = b.channel_scale = channel_scale(column);
                b.m = int16_t(hp1_exponent(column, block));
                for (size_t lane = 0; lane < 32; ++lane)
                    a.qs[lane] = b.qs[lane] = int8_t(weight_code(column, block * 2 + lane / 16));
            }
        }
        args.weight_format = shift ? ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1
                                   : ggml_gemmini_args_t::im2p_weight_format_t::q8_h1;
        if (shift) {
            args.q8_hp1_blocks = hp1.data();
            args.q8_hp1_block_count = hp1.size();
            args.q8_hp1_blocks_per_row = 2;
            args.native_weight_bytes = hp1.size() * sizeof(block_q8_hp1);
        } else {
            args.q8_h1_blocks = h1.data();
            args.q8_h1_block_count = h1.size();
            args.q8_h1_rows = N;
            args.blocks_per_row = 2;
            args.native_weight_bytes = h1.size() * sizeof(block_q8_h1);
        }
        args.f_out = output.data();
        args.stride_f_out = 2 * N + 3;
        args.col_stride_f_out = 2;
        args.act_quant.storage().emplace<exsia::Meta>().theta.assign(Theta.begin(), Theta.end());
    }
    bool untouched() const {
        return std::all_of(output.begin(), output.end(), [](float value) { return value == Sentinel; });
    }
    void mutate_source_metadata() {
        for (auto &w : h1) { w.c_b = 3; w.R = 4; w.s_rf = 0.5f; }
        for (auto &w : hp1) { w.m = 7; w.channel_scale = 0.5f; }
    }
};

struct Observation {
    im2p_provider_t downstream{};
    uint8_t operation;
    unsigned starts = 0, full_starts = 0, stream_starts = 0;
    std::vector<int64_t> raw = std::vector<int64_t>(M * N);
    std::vector<uint8_t> seen = std::vector<uint8_t>(M * N);
    size_t raw_count = 0, callbacks = 0, scale_lanes = 0;
    std::mutex mutex;
    std::condition_variable changed;

    explicit Observation(bool hp1) : operation(hp1 ? 5 : 4) {}
    template<class Descriptor> bool capture(const Descriptor &descriptor) {
        if (descriptor.abi_version != 5 || descriptor.output_domain != 2 ||
            descriptor.vector_op != operation || descriptor.m != M || descriptor.n != N ||
            descriptor.k != K || descriptor.dim != 16) return false;
        downstream = descriptor.provider;
        ++starts;
        return starts == 1;
    }
    static int weight(void *context, size_t row, size_t col, size_t count, int8_t *values) {
        auto &self = *static_cast<Observation *>(context);
        return self.downstream.read_weight_i8(self.downstream.context, row, col, count, values);
    }
    static int scale(void *context, size_t block, size_t col, size_t count, uint32_t *values) {
        auto &self = *static_cast<Observation *>(context);
        if (block >= 2 || col + count > N) return IM2P_ERROR;
        const int result = self.downstream.read_scale(self.downstream.context, block, col, count, values);
        if (result != IM2P_OK) return result;
        for (size_t lane = 0; lane < count; ++lane) {
            const int exponent = hp1_exponent(col + lane, block);
            const uint32_t expected = self.operation == 4 ? 65535 + (block == 0 ? 0 : 255)
                : exponent == INT16_MIN ? 0x80000000U : uint32_t(exponent);
            if (values[lane] != expected) return IM2P_ERROR;
        }
        self.scale_lanes += count;
        return IM2P_OK;
    }
    static int output(void *context, size_t block, size_t row, size_t col, size_t count,
                      const int64_t *values, uint32_t domain) {
        auto &self = *static_cast<Observation *>(context);
        if (domain != 2 || block != 0 || row >= M || col + count > N || count == 0) return IM2P_ERROR;
        {
            std::lock_guard lock(self.mutex);
            for (size_t lane = 0; lane < count; ++lane) {
                const size_t index = row * N + col + lane;
                if (self.seen[index]) return IM2P_ERROR;
                self.seen[index] = 1;
                self.raw[index] = values[lane];
            }
            self.raw_count += count;
            ++self.callbacks;
        }
        self.changed.notify_all();
        return self.downstream.write_output(self.downstream.context, block, row, col, count, values, domain);
    }
    im2p_provider_t provider() { return {this, weight, nullptr, scale, output}; }
};
Observation *active = nullptr; // Main owns it from execute through worker join/fence.

exsia::StripeReadyEvent event(size_t stripe) {
    exsia::StripeReadyEvent result{};
    result.run_id = RunId;
    result.stripe_id = stripe;
    result.slot = stripe % 2;
    result.row_begin = stripe * 160;
    result.row_end = std::min(M, result.row_begin + 160);
    exsia::StripeMetadataSnapshot metadata{};
    metadata.theta = Theta[stripe];
    result.activation_metadata = metadata;
    return result;
}

void verify_output(const Fixture &fixture, const Observation &observed) {
    require(observed.raw_count == M * N && observed.callbacks > 0 && observed.scale_lanes > 0,
            "missing actual final scalar/scale callbacks");
    size_t padding = 0, nonzero = 0;
    for (size_t row = 0; row < M; ++row) {
        for (size_t col = 0; col < N; ++col) {
            require(observed.seen[row * N + col] == 1 &&
                    observed.raw[row * N + col] == expected_integer(fixture.shift, row, col),
                    "raw final integer mismatch");
            const float expected = expected_float(fixture.shift, row, col);
            require(std::bit_cast<uint32_t>(fixture.output[row * (2 * N + 3) + 2 * col]) ==
                    std::bit_cast<uint32_t>(expected), "f_out bit mismatch");
            nonzero += expected != 0;
        }
        for (size_t position = 0; position < 2 * N + 3; ++position) {
            if (position < 2 * N && position % 2 == 0) continue;
            require(fixture.output[row * (2 * N + 3) + position] == Sentinel, "padding changed");
            ++padding;
        }
    }
    require(nonzero > 0, "vacuous zero fixture");
    std::cout << " raw=" << observed.raw_count << " logical_fout=" << M * N
              << " padding=" << padding << " output_callbacks=" << observed.callbacks
              << " scale_lanes=" << observed.scale_lanes;
}

std::vector<float> run_success(bool hp1, Mode mode) {
    Fixture fixture(hp1);
    Observation observed(hp1);
    active = &observed;
    if (mode == Mode::stripe_pipeline)
        std::get<exsia::Meta>(fixture.args.act_quant.storage()).theta.assign(3, INT16_MIN);
    auto started = execute(&fixture.args, mode);
    require(started.status.ok() && started.run, "frontend start failed");
    fixture.mutate_source_metadata();
    if (mode == Mode::stripe_pipeline) {
        for (size_t stripe = 0; stripe < 3; ++stripe) {
            auto ready = event(stripe);
            require(submit_stripe(*started.run, ready, {true, Theta[stripe]}).ok(), "stripe submit failed");
            ready.activation_metadata->theta = 12; // Accepted event must have an owned snapshot.
            ready.row_begin = M;
            std::get<exsia::Meta>(fixture.args.act_quant.storage()).theta.assign(3, 11);
        }
    }
    const auto done = fence(*started.run);
    require(done.status.ok(), "fence failed");
    require(observed.starts == 1 && done.stats.base.completed_output_tiles == 63 &&
            done.stats.base.completed_fragments == 252, "logical work/fragment counts");
    if (mode == Mode::stripe_pipeline) {
        require(fixture.untouched(), "caller output published before authorization");
        // SemanticStripe counts the optional residual stage. Dense retirement
        // is observed through RTL completion/timing plus final authorization.
        require(done.semantic_completion_count == 0 && done.semantic_stripes.empty() &&
                done.stats.base.completed_stripes == 3 && done.stats.base.stripes_published == 3 &&
                done.stripe_rtl_timings.size == 3, "dense stripe completion count");
        for (size_t stripe = 0; stripe < 3; ++stripe) {
            const auto &timing = done.stripe_rtl_timings[stripe];
            const auto expected = event(stripe);
            require(timing.run_id == RunId && timing.stripe_id == stripe && timing.slot == stripe % 2 &&
                    timing.row_begin == expected.row_begin && timing.row_end == expected.row_end &&
                    timing.completion_cycle >= timing.publish_cycle &&
                    timing.publish_to_completion_cycles == timing.completion_cycle - timing.publish_cycle,
                    "stripe timing identity");
        }
        require(authorize_output_commit(*started.run, true).ok(), "output authorization failed");
    }
    std::cout << "SCU_PIPELINE_RESULT route=" << (hp1 ? "hp1" : "h1")
              << " mode=" << (mode == Mode::full ? "FULL" : "PIPELINE")
              << " tiles=" << fixture.args.tile_I << ',' << fixture.args.tile_J << ',' << fixture.args.tile_K
              << " geometry=160,160,1 slots=0,1,0 works=63 fragments=252";
    verify_output(fixture, observed);
    std::cout << " backend=actual_rtl_simulator logical_invocations=1 elapsed="
              << done.stats.base.work_total_cycles << '\n';
    active = nullptr;
    return fixture.output;
}

void rejected_and_missing_publication() {
    Fixture fixture(false);
    Observation observed(false);
    active = &observed;
    std::get<exsia::Meta>(fixture.args.act_quant.storage()).theta.assign(3, INT16_MIN);
    auto started = execute(&fixture.args, Mode::stripe_pipeline);
    require(started.status.ok() && started.run, "failure fixture start");
    require(!submit_stripe(*started.run, event(1), {true, Theta[1]}).ok(), "out-of-order event accepted");
    require(!submit_stripe(*started.run, event(0)).ok(), "missing post-fold theta accepted");
    require(submit_stripe(*started.run, event(0), {true, Theta[0]}).ok(), "valid retry did not retain ownership");
    {
        std::unique_lock lock(observed.mutex);
        require(observed.changed.wait_for(lock, std::chrono::seconds(60), [&] {return observed.raw_count > 0;}),
                "first stripe did not execute while later stripes remained unpublished");
    }
    require(fixture.untouched(), "partial raw output escaped private staging");
    const auto failed = fence(*started.run);
    require(!failed.status.ok() && !fence(*started.run).status.ok() &&
            failed.stripe_rtl_timings.empty() && !authorize_output_commit(*started.run, true).ok() &&
            fixture.untouched(), "missing publication exposed partial caller output");
    std::cout << "SCU_PIPELINE_REJECTION order=1 missing_theta=1 missing_publications=2"
              << " partial_raw_observed=" << observed.raw_count
              << " caller_output_preserved=1 partial_invocations=1 successful_invocations=0\n";
    active = nullptr;
}
} // namespace

extern "C" int __real_im2p_execute_matmul_extended(im2p_sim_t *, const im2p_matmul_desc_t *, im2p_work_stats_extended_t *);
extern "C" int __wrap_im2p_execute_matmul_extended(im2p_sim_t *sim, const im2p_matmul_desc_t *descriptor,
                                                   im2p_work_stats_extended_t *stats) {
    if (!active || !descriptor || !active->capture(*descriptor)) return IM2P_ERROR;
    ++active->full_starts;
    auto copy = *descriptor;
    copy.provider = active->provider();
    return __real_im2p_execute_matmul_extended(sim, &copy, stats);
}
extern "C" int __real_im2p_begin_striped_matmul(im2p_sim_t *, const im2p_stripe_work_desc_t *, im2p_stream_t **);
extern "C" int __wrap_im2p_begin_striped_matmul(im2p_sim_t *sim, const im2p_stripe_work_desc_t *descriptor,
                                               im2p_stream_t **stream) {
    if (!active || !descriptor || !active->capture(*descriptor)) return IM2P_ERROR;
    ++active->stream_starts;
    auto copy = *descriptor;
    copy.provider = active->provider();
    return __real_im2p_begin_striped_matmul(sim, &copy, stream);
}

int main() {
    try {
        require(IM2P_ABI_VERSION == 5 && im2p_compiled_accumulator_bits() == 32 &&
                compiled_activation_bits() == 8 && compiled_weight_bits() == 8 && compiled_dim() == 16,
                "this test requires ABI5/A8/W8/D16");
        for (bool hp1 : {false, true}) {
            const auto full = run_success(hp1, Mode::full);
            const auto pipeline = run_success(hp1, Mode::stripe_pipeline);
            require(full == pipeline, "FULL/PIPELINE final output differs");
        }
        rejected_and_missing_publication();
        std::cout << "SCU_PIPELINE_PASS complete_invocations=4 partial_invocations=1"
                  << " raw_comparisons=61632 logical_fout_comparisons=61632 padding_comparisons=65484"
                  << " quantizer=not_run overlap_claim=none physical_jobs=0\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "SCU_PIPELINE_FAIL " << error.what() << '\n';
        return 1;
    }
}
