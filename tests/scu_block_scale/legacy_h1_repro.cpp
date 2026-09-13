// Synthetic legacy-contract reproduction, not a model or FPGA board test.
#include "ggml-gemmini-args.h"
#include "im2p_gemmini_frontend.hpp"
#include "quants/act/exsia/exsia.hpp"

#include <algorithm>
#include <array>
#include <bit>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string_view>
#include <vector>

using namespace im2p::gemmini;

static void require(bool condition, const char *message) {
    if (!condition) throw std::runtime_error(message);
}

struct Observation {
    im2p_provider_t downstream{};
    uint8_t vector_op = 0;
    size_t block_size = 0;
    std::vector<size_t> scale_blocks, output_blocks;
    std::vector<int> scales;
    std::vector<int64_t> raw;
    unsigned executes = 0;

    static int weight(void *context, size_t row, size_t column,
                      size_t count, int8_t *values) {
        auto &self = *static_cast<Observation *>(context);
        return self.downstream.read_weight_i8(self.downstream.context, row, column, count, values);
    }
    static int scale(void *context, size_t block, size_t column,
                     size_t count, int8_t *values) {
        auto &self = *static_cast<Observation *>(context);
        const int result = self.downstream.read_scale(self.downstream.context, block, column, count, values);
        if (result == IM2P_OK) {
            self.scale_blocks.push_back(block);
            for (size_t lane = 0; lane < count; ++lane) self.scales.push_back(values[lane]);
        }
        return result;
    }
    static int output(void *context, size_t block, size_t row, size_t column,
                      size_t count, const int64_t *values) {
        auto &self = *static_cast<Observation *>(context);
        if (row != 0 || column != 0 || count != 1) return IM2P_ERROR;
        self.output_blocks.push_back(block);
        self.raw.push_back(values[0]);
        return self.downstream.write_output(self.downstream.context, block, row, column, count, values);
    }
    static int execute(void *context, const im2p_matmul_desc_t *descriptor,
                       im2p_work_stats_extended_t *stats) {
        auto &self = *static_cast<Observation *>(context);
        self.vector_op = descriptor->vector_op;
        self.block_size = descriptor->block_size;
        self.downstream = descriptor->provider;
        auto copy = *descriptor;
        copy.provider = {&self, weight, nullptr, scale, output};
        auto *simulator = im2p_sim_create();
        if (!simulator) return IM2P_ERROR;
        ++self.executes;
        const int result = im2p_execute_matmul_extended(simulator, &copy, stats);
        im2p_sim_destroy(simulator);
        return result;
    }
};

int main(int argc, char **argv) {
    try {
        const bool legacy = argc == 2 && std::string_view(argv[1]) == "--legacy";
        require(argc == 1 || legacy, "usage: legacy_h1_repro [--legacy]");
        std::array<block_q8_h1, 2> weights{};
        for (size_t block = 0; block < weights.size(); ++block) {
            std::fill(std::begin(weights[block].qs), std::end(weights[block].qs), int8_t(1));
            weights[block].c_b = block == 0 ? 0 : 255;
            weights[block].R = 1;
            weights[block].s_rf = 1.0f / 256.0f;
        }
        float output = -17.0f;
        ggml_gemmini_args_t args{};
        args.I = args.J = 1;
        args.K = 64;
        args.activation_rows_per_stripe = 16;
        args.tile_I = args.tile_J = 1;
        args.tile_K = 4;
        require(args.A.allocate(1, 64, 8), "activation allocation failed");
        std::fill(args.A.bytes->begin(), args.A.bytes->end(), uint8_t(1));
        args.sA = 64;
        args.f_out = &output;
        args.stride_f_out = args.col_stride_f_out = 1;
        args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_h1;
        args.q8_h1_blocks = weights.data();
        args.q8_h1_block_count = weights.size();
        args.q8_h1_rows = 1;
        args.blocks_per_row = 2;
        args.native_weight_bytes = sizeof(weights);
        args.act_quant.storage().emplace<ggml::gemmini::quants::act::exsia::Meta>().theta = {0};
        Observation observed;
        Options options;
        options.full_executor_context = &observed;
        options.full_executor = Observation::execute;
        auto started = execute(&args, Mode::full, options);
        require(started.status.ok() && started.run, "frontend execute failed");
        const auto done = fence(*started.run);
        require(done.status.ok(), "frontend fence failed");
        require(observed.executes == 1 && observed.vector_op == IM2P_VECTOR_EXTERNAL && observed.block_size == 32,
                "legacy descriptor changed");
        require(observed.output_blocks == std::vector<size_t>{0, 1} && observed.raw == std::vector<int64_t>{32, 32},
                "legacy raw callback differs");
        require(!observed.scales.empty() && std::all_of(observed.scales.begin(), observed.scales.end(),
                [](int value) { return value == 1; }), "legacy provider scale differs");
        require(std::bit_cast<uint32_t>(output) == std::bit_cast<uint32_t>(32.125f), "legacy f_out differs");
        std::cout << "LEGACY_H1_PASS backend=actual_rtl_simulator logical_invocations=1"
                  << " shape=1/1/64 vector_op=" << unsigned(observed.vector_op)
                  << " vector_op_name=External block_size=32 provider_scale_reads=" << observed.scales.size()
                  << " provider_scale_values=1 output_blocks=0,1 raw_signed32=32,32"
                  << " canonical_callback=signed64 f_out=" << output
                  << " f_out_bits=" << std::bit_cast<uint32_t>(output)
                  << " fragments=" << done.stats.base.completed_fragments
                  << " output_works=" << done.stats.base.completed_output_tiles
                  << " scu_internal_consumption=not_observed final_integer_callback=absent\n";
        if (legacy) return 0;
        // Check the required final integer location, not the host float value.
        const bool final_integer_present = observed.output_blocks == std::vector<size_t>{0} &&
                                           observed.raw == std::vector<int64_t>{8224};
        if (!final_integer_present) {
            std::cerr << "SCU_TARGET_FAILURE expected_final_signed32=8224 actual_block_signed32="
                      << observed.raw[0] << ',' << observed.raw[1]
                      << " actual_host_f_out=" << output << " final_integer_callback=absent"
                      << " provider_only_evidence_is_not_internal_scu_observation\n";
            return 2;
        }
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "REPRO_ERROR " << error.what() << '\n';
        return 1;
    }
}
