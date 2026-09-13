// Independent hand-computed radix anchors; no simulator or DUT values.
#include "ggml-gemmini-args.h"
#include "quants/act/exsia/exsia.hpp"
#include "model_capture.hpp"
#include <array>
#include <bit>
#include <iostream>

int main() {
    try {
        namespace rmd = ggml::gemmini::rmd;
        namespace exsia = ggml::gemmini::quants::act::exsia;
        ggml_gemmini_args_t args{};
        args.I = 1; args.J = 1; args.K = 96;
        args.tile_I = args.tile_J = args.tile_K = 1;
        args.activation_rows_per_stripe = 16;
        model_capture::require(args.A.allocate(1, 96, 8), "activation allocation");
        args.sA = args.A.row_stride_bytes; args.sB = args.K;
        std::array<block_q8_h1, 3> h1{};
        std::array<block_q8_hp1, 3> hp1{};
        for (auto &w : h1) { w.c_b = 2; w.R = 3; w.s_rf = 0.25f; }
        for (auto &w : hp1) { w.m = 2; w.channel_scale = 0.5f; }
        h1[2].qs[1] = hp1[2].qs[1] = 2;
        h1[2].qs[7] = hp1[2].qs[7] = -3;
        args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_h1;
        args.q8_h1_blocks = h1.data(); args.q8_h1_block_count = 3;
        args.q8_h1_rows = 1; args.blocks_per_row = 3;
        args.native_weight_bytes = sizeof(h1);
        auto packet = std::make_shared<rmd::StripePacket>();
        packet->row_count = 1; packet->logical_k = 96; packet->logical_j = 1;
        packet->j_padded = 16; packet->activation_value_count = 512;
        packet->residual_event_count = 2; packet->total_output_values = 512;
        packet->k_indices = {1, 7}; packet->stacked_activation.signed_int8.resize(512);
        packet->stacked_activation.signed_int8[0] = packet->stacked_activation.signed_int8[256] = 1;
        packet->stacked_activation.signed_int8[1] = packet->stacked_activation.signed_int8[257] = -1;
        rmd::BlockDescriptor block{};
        block.block_id = 2; block.global_k_begin = 64;
        block.compact_k_count = 2; block.padded_k_count = block.rows_padded = 16;
        block.active_lane_mask = 3; block.active_lane_count = 2; block.lane_ids[1] = 1;
        block.activation_byte_count = 512; block.lane_stride_values = 256;
        packet->blocks.push_back(block);
        const auto encoded = model_capture::packet_json(*packet);
        model_capture::validate_packet(encoded, 1, 1, 96, 0, 0, 1);
        const auto decoded = model_capture::packet_from_json<rmd::StripePacket>(encoded);
        model_capture::require(model_capture::packet_json(decoded) == encoded, "native packet roundtrip");
        auto &meta = args.act_quant.storage().emplace<exsia::Meta>();
        meta.theta = {2}; meta.rmd_packets.push_back(packet);
        std::vector<float> expected{10.0f};
        const auto nonzero = model_capture::residual_oracle(args, meta, expected);
        // (257 * 2 + -257 * -3) * (2+3) * 0.25 * 2^2 = 6425.
        model_capture::require(nonzero == 2 && expected[0] == 6435.0f, "H1 signed radix anchor");
        args.q8_h1_blocks = nullptr;
        args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1;
        args.q8_hp1_blocks = hp1.data(); args.q8_hp1_block_count = 3; args.q8_hp1_blocks_per_row = 3;
        args.native_weight_bytes = sizeof(hp1);
        expected[0] = 10.0f;
        model_capture::require(model_capture::residual_oracle(args, meta, expected) == 2 && expected[0] == 10290.0f,
                               "HP1 native integer scale anchor");
        hp1[2].m = INT16_MIN; expected[0] = 10.0f;
        model_capture::require(model_capture::residual_oracle(args, meta, expected) == 2 && expected[0] == 10.0f,
                               "HP1 sentinel anchor");
        std::cout << "MODEL_RESIDUAL_ORACLE_PASS H1=1 HP1=1 sentinel=1 signed_radix=1 native_roundtrip=1 numerical_DUT=NOT_RUN\n";
    } catch (const std::exception &error) {
        std::cerr << "MODEL_RESIDUAL_ORACLE_FAIL " << error.what() << '\n'; return 1;
    }
}
