#include "ggml-gemmini-args.h"
#include "ggml-gemmini-fpga.hpp"
#include "im2p_gemmini_frontend.hpp"
#include "quants/act/dispatch.hpp"
#include "uart.hpp"

#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

extern "C" void gemmini_log_debug_layer(const char *, const char *,
                                        ...) noexcept {}

namespace {

void check(bool condition, const char *message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

void clear_device_binding() {
#if defined(_WIN32)
  _putenv_s("IM2P_FPGA_DEVICE", "");
#else
  unsetenv("IM2P_FPGA_DEVICE");
#endif
}

struct Hp1Args {
  ggml_gemmini_args_t args{};
#if GGML_GEMMINI_WEIGHT_BITS == 4
  std::vector<block_q4_hp1> blocks;
#else
  std::vector<block_q8_hp1> blocks;
#endif

  Hp1Args() : blocks(3) {
    args.I = 129;
    args.J = 1;
    args.K = 96;
    args.tile_I = 2;
    args.tile_J = 3;
    args.tile_K = 4;
    args.activation_rows_per_stripe = 65;
#if GGML_GEMMINI_WEIGHT_BITS == 4
    args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q4_hp1;
    args.q4_hp1_blocks = blocks.data();
    args.native_block_count = blocks.size();
    args.native_blocks_per_row = blocks.size();
#else
    args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1;
    args.q8_hp1_blocks = blocks.data();
    args.q8_hp1_block_count = blocks.size();
    args.q8_hp1_blocks_per_row = blocks.size();
#endif
    args.native_weight_bytes = blocks.size() * sizeof(blocks.front());
  }
};

} // namespace

int main(int argc, char **argv) {
  using namespace im2p::gemmini_hp1;
  check(argc == 3, "host orchestration requires profile and build ID");
  ggml_gemmini_args_t metadata_args{};
  metadata_args.I = 9;
  metadata_args.tile_I = 1;
  metadata_args.activation_rows_per_stripe = 3;
  auto &metadata = metadata_args.act_quant.storage().emplace<
      ggml::gemmini::quants::act::exsia::Meta>();
  metadata.theta = {0, 1, 2};
  const ggml::gemmini::quants::act::ActivationMetadataView scales(metadata_args, 0, 9);
  float value = 0;
  check(scales.valid() && scales.scale(0, value) && value == 1.0F &&
            scales.scale(3, value) && value == 2.0F &&
            scales.scale(6, value) && value == 4.0F,
        "CPU reconstruction did not use the explicit host stripe H");
  Hp1Args fixture;
  const Capability selected{argv[1],
                            argv[2],
                            "hp1-fragment-sat32-v1",
                            GGML_GEMMINI_ACTIVATION_BITS,
                            GGML_GEMMINI_WEIGHT_BITS,
                            DIM,
                            32,
                            32,
                            GGML_GEMMINI_WEIGHT_BITS == 4
                                ? Packing::signed_int4_low_nibble_first
                                : Packing::signed_int8,
                            true,
                            true,
                            false,
                            BANK_NUM,
                            BANK_ROWS,
                            ACC_ROWS,
                            DIM * GGML_GEMMINI_WEIGHT_BITS / 8,
                            DIM * sizeof(std::int32_t)};
  const WorkPlanV1 plan{fixture.args.I,
                        fixture.args.J,
                        fixture.args.K,
                        fixture.args.tile_I,
                        fixture.args.tile_J,
                        fixture.args.tile_K,
                        fixture.args.activation_rows_per_stripe,
                        Mode::pipeline,
                        WorkKind::dense_hp1_final};
  const auto decoded = decode_work_plan_v1(encode_work_plan_v1(plan));
  const auto fragments = fragment_work(selected, decoded);
  check(decoded.m == 129 && decoded.k == 96 && !fragments.empty(),
        "selected profile WorkPlan failed");
  check(ggml_gemmini_fpga_supports(decoded.m, decoded.n, decoded.k, true, true),
        "selected HP1 WorkPlan rejected");

  const auto frontend =
      im2p::gemmini::execute(&fixture.args, im2p::gemmini::Mode::full);
  check(frontend.status.code == im2p::gemmini::StatusCode::invalid_argument &&
            !frontend.run &&
            std::string(frontend.status.message) ==
                "external executor required",
        "common frontend admitted simulator fallback");

  clear_device_binding();
  bool quantized = false;
  check(!ggml_gemmini_fpga_execute(
            fixture.args, true, [&] { quantized = true; }, "host-no-device"),
        "unbound physical work unexpectedly executed");
  check(!quantized &&
            ggml_gemmini_fpga_last_error() ==
                "IM2P_FPGA_DEVICE must explicitly bind a GEMMINI_HP1 board",
        "unbound physical work did not fail before transport");
  std::cout << "GEMMINI_HP1_HOST_COMMON_ORCHESTRATION_PASS profile="
            << selected.profile << " fragments=" << fragments.size() << '\n';
}
