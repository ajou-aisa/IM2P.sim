// ABI5 frontend integration. Expected arithmetic lives only in golden.py.
#include "ggml-gemmini-args.h"
#include "im2p_gemmini_frontend.hpp"
#include "quants/act/exsia/exsia.hpp"

#include <algorithm>
#include <array>
#include <bit>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using namespace im2p::gemmini;

static void require(bool condition, const char *message) {
    if (!condition) throw std::runtime_error(message);
}

static std::vector<int> integers(std::string text) {
    std::replace(text.begin(), text.end(), ',', ' ');
    std::istringstream input(text);
    std::vector<int> values;
    for (int value; input >> value;) values.push_back(value);
    require(input.eof(), "invalid input list");
    return values;
}

struct Fixture {
    ggml_gemmini_args_t args{};
    std::array<block_q8_h1, 2> h1{};
    std::array<block_q8_hp1, 2> hp1{};
    std::array<float, 3> output{17, 17, 17};
    bool shift = false;
    bool mutate = false;

    Fixture(const std::string &route, const std::vector<int> &codes, unsigned residual,
            float scale, int theta, const std::vector<int> &activation,
            const std::vector<int> &weight, bool change) : shift(route == "hp1"), mutate(change) {
        require(route == "h1" || shift, "unknown test route");
        require(codes.size() == 2 && activation.size() == 4 && weight.size() == 4, "case length");
        args.I = args.J = 1;
        args.K = 64;
        args.activation_rows_per_stripe = 16;
        args.tile_I = args.tile_J = 1;
        args.tile_K = 4;
        require(args.A.allocate(1, 64, 8), "activation allocation failed");
        for (size_t fragment = 0; fragment < 4; ++fragment) {
            require(activation[fragment] >= -128 && activation[fragment] <= 127 &&
                    weight[fragment] >= -128 && weight[fragment] <= 127, "code range");
            for (size_t lane = 0; lane < 16; ++lane) {
                (*args.A.bytes)[fragment * 16 + lane] = uint8_t(activation[fragment]);
                h1[fragment / 2].qs[(fragment % 2) * 16 + lane] = int8_t(weight[fragment]);
                hp1[fragment / 2].qs[(fragment % 2) * 16 + lane] = int8_t(weight[fragment]);
            }
        }
        for (size_t block = 0; block < 2; ++block) {
            h1[block].c_b = uint8_t(codes[block]);
            h1[block].R = uint16_t(residual);
            h1[block].s_rf = scale;
            hp1[block].m = int16_t(codes[block]);
            hp1[block].channel_scale = scale;
        }
        args.sA = 64;
        args.f_out = output.data();
        args.stride_f_out = 3;
        args.col_stride_f_out = 1;
        args.weight_format = shift ? ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1
                                   : ggml_gemmini_args_t::im2p_weight_format_t::q8_h1;
        if (shift) {
            args.q8_hp1_blocks = hp1.data();
            args.q8_hp1_block_count = hp1.size();
            args.q8_hp1_blocks_per_row = hp1.size();
            args.native_weight_bytes = sizeof(hp1);
        } else {
            args.q8_h1_blocks = h1.data();
            args.q8_h1_block_count = h1.size();
            args.q8_h1_rows = 1;
            args.blocks_per_row = h1.size();
            args.native_weight_bytes = sizeof(h1);
        }
        args.act_quant.storage().emplace<ggml::gemmini::quants::act::exsia::Meta>().theta = {int16_t(theta)};
    }
};

struct Observation {
    Fixture &fixture;
    im2p_provider_t downstream{};
    unsigned executes = 0, callbacks = 0;
    uint8_t operation = 0;
    uint32_t domain = UINT32_MAX;
    int64_t raw = 0;
    std::vector<size_t> scale_blocks;
    std::vector<uint32_t> scales;

    static int weight(void *context, size_t row, size_t column, size_t count, int8_t *values) {
        auto &self = *static_cast<Observation *>(context);
        return self.downstream.read_weight_i8(self.downstream.context, row, column, count, values);
    }
    static int scale(void *context, size_t block, size_t column, size_t count, uint32_t *values) {
        auto &self = *static_cast<Observation *>(context);
        const int result = self.downstream.read_scale(self.downstream.context, block, column, count, values);
        if (result == IM2P_OK) {
            self.scale_blocks.push_back(block);
            self.scales.insert(self.scales.end(), values, values + count);
        }
        return result;
    }
    static int output(void *context, size_t block, size_t row, size_t column, size_t count,
                      const int64_t *values, uint32_t output_domain) {
        auto &self = *static_cast<Observation *>(context);
        if (block != 0 || row != 0 || column != 0 || count != 1 || output_domain != 2 || self.callbacks)
            return IM2P_ERROR;
        ++self.callbacks;
        self.domain = output_domain;
        self.raw = values[0];
        return self.downstream.write_output(self.downstream.context, block, row, column, count, values, output_domain);
    }
    static int execute(void *context, const im2p_matmul_desc_t *descriptor, im2p_work_stats_extended_t *stats) {
        auto &self = *static_cast<Observation *>(context);
        if (descriptor->abi_version != 5 || descriptor->output_domain != 2 ||
            descriptor->dim != 16 || descriptor->activation_bits != 8 || descriptor->weight_bits != 8 ||
            descriptor->vector_op != (self.fixture.shift ? 5 : 4)) return IM2P_ERROR;
        self.operation = descriptor->vector_op;
        self.downstream = descriptor->provider;
        if (self.fixture.mutate) {
            // Mutation occurs after frontend ownership/snapshot and before its provider reads.
            for (auto &weight : self.fixture.h1) { weight.c_b = 19; weight.R = 20; weight.s_rf = 3; }
            for (auto &weight : self.fixture.hp1) { weight.m = 17; weight.channel_scale = 3; }
            std::get<ggml::gemmini::quants::act::exsia::Meta>(self.fixture.args.act_quant.storage()).theta = {8};
        }
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

static void reject_metadata() {
    for (int invalid = 0; invalid < 6; ++invalid) {
        const bool shift = invalid >= 3;
        Fixture fixture(shift ? "hp1" : "h1", {0, 1}, 1, 1 / 256.0f, 0, {1, 1, 1, 1}, {1, 1, 1, 1}, false);
        if (invalid == 0) fixture.h1[1].s_rf = 0.25f;
        if (invalid == 1) fixture.h1[1].R = 2;
        if (invalid == 2) fixture.h1[0].s_rf = fixture.h1[1].s_rf = std::numeric_limits<float>::infinity();
        if (invalid == 3) fixture.hp1[1].m = -1;
        if (invalid == 4) fixture.hp1[1].channel_scale = 0.25f;
        if (invalid == 5) fixture.hp1[0].channel_scale = fixture.hp1[1].channel_scale = std::numeric_limits<float>::quiet_NaN();
        Observation observed{fixture};
        Options options;
        options.full_executor_context = &observed;
        options.full_executor = Observation::execute;
        auto started = execute(&fixture.args, Mode::full, options);
        require(!started.status.ok() && observed.executes == 0 &&
                (!started.run || !fence(*started.run).status.ok()) && fixture.output == std::array<float, 3>{17, 17, 17}, "invalid metadata reached execution or changed caller output");
        std::cout << "SCU_FRONTEND_REJECTION case=" << invalid << " executor_calls=0 caller_output_preserved=1\n";
    }
}

int main(int argc, char **argv) {
    try {
        require(argc == 2 && IM2P_ABI_VERSION == 5, "usage: frontend_final_integer CASES_TXT (ABI5 build)");
        require(im2p_compiled_accumulator_bits() == 32, "this fixture requires A8 signed32 accumulator");
        std::ifstream input(argv[1]);
        require(bool(input), "missing independent case inputs");
        size_t cases = 0;
        for (std::string line; std::getline(input, line);) {
            std::istringstream fields(line);
            std::string name, route, codes, activation, weight;
            unsigned residual;
            float scale;
            int theta, mutate;
            require(bool(fields >> name >> route >> codes >> residual >> scale >> theta >> activation >> weight >> mutate), "malformed case");
            Fixture fixture(route, integers(codes), residual, scale, theta, integers(activation), integers(weight), mutate != 0);
            Observation observed{fixture};
            Options options;
            options.full_executor_context = &observed;
            options.full_executor = Observation::execute;
            auto started = execute(&fixture.args, Mode::full, options);
            require(started.status.ok() && started.run, "frontend execute failed");
            const auto done = fence(*started.run);
            require(done.status.ok() && observed.executes == 1 && observed.callbacks == 1 && observed.domain == 2,
                    "SCU final callback/fence contract failed");
            require(fixture.output[1] == 17 && fixture.output[2] == 17, "caller padding changed");
            require(done.stats.base.completed_fragments == 4 && done.stats.base.completed_output_tiles == 1,
                    "fragment/work count mismatch");
            std::cout << "SCU_FRONTEND_RESULT case=" << name << " operation=" << unsigned(observed.operation)
                      << " raw=" << observed.raw << " domain=" << observed.domain << " callbacks=" << observed.callbacks
                      << " f_out_bits=" << std::bit_cast<uint32_t>(fixture.output[0])
                      << " metadata=";
            for (size_t index = 0; index < observed.scales.size(); ++index)
                std::cout << (index ? "," : "") << observed.scales[index];
            std::cout << " metadata_blocks=";
            for (size_t index = 0; index < observed.scale_blocks.size(); ++index)
                std::cout << (index ? "," : "") << observed.scale_blocks[index];
            std::cout << " padding_preserved=1 logical_invocations=1 backend=actual_rtl_simulator"
                      << " internal_scu_consumption=not_observed\n";
            ++cases;
        }
        require(cases > 0, "empty independent input set");
        reject_metadata();
        std::cout << "SCU_FRONTEND_PASS cases=" << cases << " rejected_metadata=6 numerical_revision="
                  << im2p_compiled_numerical_semantics_revision() << '\n';
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "SCU_FRONTEND_FAIL " << error.what() << '\n';
        return 1;
    }
}
