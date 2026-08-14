#pragma once

#include "im2p_sim.h"

#include <cstddef>
#include <cstdint>
#include <memory>

struct ggml_gemmini_args_t;

namespace ggml::gemmini::quants::act::exsia {
struct StripeReadyEvent;
}

namespace im2p::gemmini {

enum class Mode : uint8_t {
  full,
  stripe_pipeline,
};

enum class Route : uint8_t {
  q8_0_unpacked_to_h1,
  q8_h0,
  q8_h2,
  q8_h1,
  q8_hp1,
  q8_hp2,
  q8_channel,
  q8_channel_dense_sidecar,
  unknown,
};

enum class StatusCode : uint8_t {
  success,
  invalid_argument,
  invalid_contract,
  unsupported_route,
  invalid_state,
  backpressure,
  out_of_memory,
  execution_failure,
};

struct Status {
  StatusCode code = StatusCode::success;
  Route route = Route::unknown;
  bool native_contract = false;
  const char *message = "success";

  [[nodiscard]] bool ok() const noexcept { return code == StatusCode::success; }
  explicit operator bool() const noexcept { return ok(); }
};

struct Options {
  size_t queue_capacity = 2;
  // Conservative minimum number of one-logical-cycle progress iterations
  // allowed without a matched completion. This is not a wall-clock timeout.
  uint64_t max_stalled_cycles = 65536;
};

class Run;
struct ExecuteResult;
struct FenceResult;
#if defined(IM2P_GEMMINI_FRONTEND_TESTING)
namespace testing {
struct Snapshot;
Snapshot inspect(const Run &) noexcept;
bool wait_for_completion(Run &, uint64_t target) noexcept;
bool wait_for_closing(Run &) noexcept;
void enable_completion_gate(Run &) noexcept;
void release_completion_gate(Run &) noexcept;
void disable_completion_gate(Run &) noexcept;
} // namespace testing
#endif

// execute() copies the selected scalar values and pointer identities documented
// in frontend/README.md; it does not copy the whole args object or any backing
// storage. For executable q8_h0 work, B and submitted A bytes remain borrowed,
// valid, and immutable, while C remains borrowed, valid, and exclusively
// writable by this Run until fence() returns or the Run is destroyed. Pipeline
// A rows may be produced before their successful submit_stripe() call. Calls on
// one Run are internally synchronized.
class Run {
public:
  ~Run() noexcept;
  Run(const Run &) = delete;
  Run &operator=(const Run &) = delete;
  Run(Run &&) = delete;
  Run &operator=(Run &&) = delete;

private:
  struct Impl;
  explicit Run(std::unique_ptr<Impl>) noexcept;
  std::unique_ptr<Impl> impl_;

  friend struct ExecuteResult;
  friend ExecuteResult execute(const ggml_gemmini_args_t *, Mode,
                               Options) noexcept;
  friend Status submit_stripe(
      Run &,
      const ggml::gemmini::quants::act::exsia::StripeReadyEvent &) noexcept;
  friend struct FenceResult;
  friend FenceResult fence(Run &) noexcept;
#if defined(IM2P_GEMMINI_FRONTEND_TESTING)
  friend testing::Snapshot testing::inspect(const Run &) noexcept;
  friend bool testing::wait_for_completion(Run &, uint64_t) noexcept;
  friend bool testing::wait_for_closing(Run &) noexcept;
  friend void testing::enable_completion_gate(Run &) noexcept;
  friend void testing::release_completion_gate(Run &) noexcept;
  friend void testing::disable_completion_gate(Run &) noexcept;
#endif
};

struct ExecuteResult {
  Status status{};
  std::unique_ptr<Run> run;
};

struct FenceResult {
  Status status{};
  im2p_work_stats_extended_t stats{};
};

[[nodiscard]] ExecuteResult execute(const ggml_gemmini_args_t *args,
                                    Mode mode = Mode::full,
                                    Options options = {}) noexcept;

[[nodiscard]] Status submit_stripe(
    Run &run,
    const ggml::gemmini::quants::act::exsia::StripeReadyEvent &event) noexcept;

[[nodiscard]] FenceResult fence(Run &run) noexcept;

#if defined(IM2P_GEMMINI_FRONTEND_TESTING)
namespace testing {
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
};
[[nodiscard]] Snapshot inspect(const Run &) noexcept;
[[nodiscard]] bool wait_for_completion(Run &, uint64_t target) noexcept;
[[nodiscard]] bool wait_for_closing(Run &) noexcept;
void enable_completion_gate(Run &) noexcept;
void release_completion_gate(Run &) noexcept;
void disable_completion_gate(Run &) noexcept;
} // namespace testing
#endif

} // namespace im2p::gemmini
