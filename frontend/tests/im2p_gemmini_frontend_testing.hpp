#pragma once

#include "im2p_gemmini_frontend.hpp"

namespace im2p::gemmini {

struct RunTestAccess {
  struct Snapshot {
    size_t i = 0, j = 0, k = 0;
    size_t sa = 0, sb = 0, sc = 0, sd = 0;
    size_t activation_row_offset = 0;
    size_t activation_rows_per_stripe = 0;
    size_t block_size_k = 0;
    size_t tile_i = 0, tile_j = 0, tile_k = 0;
    uint8_t weight_format = 0;
    const void *a = nullptr, *b = nullptr, *c = nullptr, *d = nullptr;
    const void *a_fp32 = nullptr, *b_fp32 = nullptr;
    const void *b_blocks = nullptr, *b_scales = nullptr;
    size_t blocks_k = 0, blocks_j = 0, blocks_i = 0;
    const void *c_b = nullptr, *s_rf = nullptr, *r = nullptr;
    const void *s_rf_stripe = nullptr, *r_stripe = nullptr;
    size_t stripe_j = 0;
    const void *q8_h1 = nullptr;
    size_t q8_h1_count = 0, q8_h1_rows = 0, blocks_per_row = 0;
    const void *q8_h2 = nullptr;
    size_t q8_h2_count = 0, q8_h2_blocks_per_row = 0;
    const void *q8_hp1 = nullptr;
    size_t q8_hp1_count = 0, q8_hp1_blocks_per_row = 0;
    const void *q8_hp2 = nullptr;
    size_t q8_hp2_count = 0, q8_hp2_blocks_per_row = 0;
    const void *channel_scales = nullptr;
    size_t channel_scale_count = 0;
    const void *channel_rows = nullptr;
    size_t channel_row_stride = 0, channel_row_count = 0;
    const void *f_out = nullptr;
    size_t col_stride_f_out = 0, stride_f_out = 0;
    const void *model_arch = nullptr, *stripe_sink = nullptr;
    const void *unpacked_blocks = nullptr;
    float scale_b = 1.0f;
    uint32_t scale_d = 1;
    float scale = 1.0f;
    float bert_scale = 1.0f;
    uint64_t completion_generation = 0;
    uint8_t activation_bits = 0;
    size_t activation_raw_size = 0, activation_row_stride_bytes = 0;
    size_t queued = 0, in_flight = 0, outstanding = 0;
  };

  [[nodiscard]] static Snapshot inspect(const Run &) noexcept;
  [[nodiscard]] static bool wait_for_completion(Run &,
                                                uint64_t target) noexcept;
  [[nodiscard]] static bool wait_for_closing(Run &) noexcept;
  [[nodiscard]] static bool wait_for_blocked_submit(Run &,
                                                    size_t target) noexcept;
  static void hold_progress(Run &) noexcept;
  static void inject_execution_failure(Run &) noexcept;
  static void inject_progress_failure(Run &) noexcept;
  static void inject_poll_failure(Run &) noexcept;
  static void enable_completion_gate(Run &) noexcept;
  static void release_completion_gate(Run &) noexcept;
  static void disable_completion_gate(Run &) noexcept;
};

} // namespace im2p::gemmini
