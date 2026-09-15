#include "ggml-gemmini-fpga.hpp"

#include "ggml-gemmini-args.h"
#include "uart.hpp"

#include <cstdlib>
#include <limits>
#include <mutex>

namespace {

std::mutex adapter_mutex;
thread_local std::string last_error;

bool hp1_weights(const ggml_gemmini_args_t &args) {
  using Format = ggml_gemmini_args_t::im2p_weight_format_t;
#if GGML_GEMMINI_WEIGHT_BITS == 4
  return args.weight_format == Format::q4_hp1 &&
         args.has_native_matched_width_contract();
#elif GGML_GEMMINI_WEIGHT_BITS == 8
  return args.weight_format == Format::q8_hp1 &&
         args.has_q8_hp1_im2p_contract();
#else
  (void)args;
  return false;
#endif
}

void set_error(const char *message) { last_error = message; }

im2p::gemmini_hp1::Capability capability() {
  using namespace im2p::gemmini_hp1;
  Capability value;
#if defined(IM2P_FPGA_PROFILE_ID)
  value.profile = IM2P_FPGA_PROFILE_ID;
#endif
#if defined(IM2P_FPGA_BUILD_ID)
  value.build_id = IM2P_FPGA_BUILD_ID;
#endif
  value.numerical_revision = "hp1-fragment-sat32-v1";
  value.activation_bits = GGML_GEMMINI_ACTIVATION_BITS;
  value.weight_bits = GGML_GEMMINI_WEIGHT_BITS;
  value.dim = DIM;
  value.accumulator_bits = 32;
  value.block_size = 32;
  value.packing = GGML_GEMMINI_WEIGHT_BITS == 4
                      ? Packing::signed_int4_low_nibble_first
                      : Packing::signed_int8;
  value.hp1_shift_only = true;
  value.ws = true;
  value.bank_count = BANK_NUM;
  value.bank_rows = BANK_ROWS;
  value.accumulator_rows = ACC_ROWS;
  value.scratchpad_row_bytes = DIM * GGML_GEMMINI_WEIGHT_BITS / 8;
  value.accumulator_row_bytes = DIM * sizeof(std::int32_t);
  return value;
}

}

bool ggml_gemmini_fpga_supports(std::size_t rows, std::size_t columns,
                                std::size_t reduction, bool pipeline) {
  return ggml_gemmini_fpga_supports(rows, columns, reduction, pipeline, true);
}

bool ggml_gemmini_fpga_supports(std::size_t rows, std::size_t columns,
                                std::size_t reduction, bool pipeline,
                                bool block_scaled) {
  (void)pipeline;
  return block_scaled && rows != 0 && columns != 0 && reduction != 0 &&
         reduction % 32 == 0 &&
         rows <= std::numeric_limits<std::size_t>::max() / columns &&
         rows <= std::numeric_limits<std::size_t>::max() / reduction;
}

bool ggml_gemmini_fpga_uses_rtl() { return false; }

bool ggml_gemmini_fpga_uses_bounded() { return true; }

bool ggml_gemmini_fpga_execute(ggml_gemmini_args_t &args, bool pipeline,
                               const std::function<void()> &quantize,
                               const char *layer_name, bool native_scu) {
  std::lock_guard<std::mutex> lock(adapter_mutex);
  (void)quantize;
  (void)layer_name;
  last_error.clear();

#if defined(GGML_GEMMINI_ENABLE_RMD) && GGML_GEMMINI_ENABLE_RMD != 0
  set_error("GEMMINI_HP1 does not support RMD");
  return false;
#endif
  if (!native_scu ||
      !ggml_gemmini_fpga_supports(args.I, args.J, args.K, pipeline, true) ||
      !hp1_weights(args) || args.tile_I == 0 || args.tile_J == 0 ||
      args.tile_K == 0 || (pipeline && args.activation_rows_per_stripe == 0)) {
    set_error("GEMMINI_HP1 requires a native HP1 block-scaled work item");
    return false;
  }
  using namespace im2p::gemmini_hp1;
  const WorkPlanV1 plan{args.I,
                        args.J,
                        args.K,
                        args.tile_I,
                        args.tile_J,
                        args.tile_K,
                        args.activation_rows_per_stripe,
                        pipeline ? Mode::pipeline : Mode::full,
                        WorkKind::dense_hp1_final};
  static_cast<void>(fragment_work(capability(), plan));
  const auto work_frame = encode_frame(
      {Opcode::work, 0, encode_work_plan_v1(plan)});
  static_cast<void>(work_frame);
  const char *device = std::getenv("IM2P_FPGA_DEVICE");
  if (!device || !*device) {
    set_error("IM2P_FPGA_DEVICE must explicitly bind a GEMMINI_HP1 board");
    return false;
  }
  set_error("GEMMINI_HP1 physical transport is not implemented");
  return false;
}

std::string ggml_gemmini_fpga_last_error() { return last_error; }

void ggml_gemmini_fpga_test_producer_checkpoint(std::size_t stripe_index) {
  (void)stripe_index;
}

void ggml_gemmini_fpga_set_observer(ggml_gemmini_fpga_observer observer,
                                    void *context) {
  (void)observer;
  (void)context;
}

void ggml_gemmini_fpga_set_block_observer(
    ggml_gemmini_fpga_block_observer observer, void *context) {
  (void)observer;
  (void)context;
}

void ggml_gemmini_fpga_set_result_observer(
    ggml_gemmini_fpga_result_observer observer, void *context) {
  (void)observer;
  (void)context;
}

void ggml_gemmini_fpga_set_boundary_observer(
    ggml_gemmini_fpga_boundary_observer observer, void *context) {
  (void)observer;
  (void)context;
}
