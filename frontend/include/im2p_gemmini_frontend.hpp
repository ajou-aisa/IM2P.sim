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
  q4_h0,
  q4_h1,
  q4_hp1,
  q16_h0,
  q16_h1,
  q16_hp1,
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

enum class ResidualStageMode : uint8_t {
  none,
  host_direct,
  im2p_compact,
};

// Mutable private output staging borrowed for one residual callback invocation.
// The callback may mutate elements in range but must not retain this view.
struct ResidualStageView {
  float *data = nullptr;
  size_t element_count = 0;

  [[nodiscard]] bool empty() const noexcept { return element_count == 0; }
  [[nodiscard]] float &operator[](size_t index) const noexcept {
    return data[index];
  }
  [[nodiscard]] float *begin() const noexcept { return data; }
  [[nodiscard]] float *end() const noexcept {
    return data == nullptr ? nullptr : data + element_count;
  }
};

struct ResidualStripeStats {
  uint64_t rmd_dot_calls = 0;
  im2p_work_stats_extended_t rmd_stats{};
};

// All pointer and reference arguments are call-borrowed. The simulator is
// nullable and, when present, remains owned by the invoking frontend worker.
using ResidualStageFn =
    Status (*)(void *context, im2p_sim_t *simulator,
               const ggml::gemmini::quants::act::exsia::StripeReadyEvent &event,
               ResidualStageView stage, ResidualStripeStats &stats) noexcept;

// Optional autonomous physical execution. The callback table and context are
// borrowed, immutable, and remain valid through fence (or Run destruction).
// Descriptor/stripe/output arguments are borrowed for each callback. begin
// copies any descriptor/provider views needed until finish. publish returning
// IM2P_BACKPRESSURE accepts nothing. poll returns 0 when no completion is ready,
// 1 with one ordered completion, or a negative error. It observes hardware and
// must never treat a host poll as an RTL cycle. The adapter owns transport and
// device watchdogs; waiting for an unpublished producer stripe is legal.
struct StreamExecutor {
  void *context = nullptr;
  int (*begin)(void *, const im2p_stripe_work_desc_t *) = nullptr;
  int (*publish)(void *, const im2p_activation_stripe_t *) = nullptr;
  int (*poll)(void *, im2p_stripe_completion_extended_t *) = nullptr;
  int (*finish)(void *, im2p_work_stats_extended_t *) = nullptr;
};

struct Options {
  // Simulator RTL cycles allowed without a completed K fragment or stripe.
  // Runtime applies a 65536-cycle minimum; UINT64_MAX disables the watchdog.
  // Physical StreamExecutor callbacks own their transport/device timeouts.
  uint64_t max_stalled_cycles = 65536;
  ResidualStageMode residual_stage_mode = ResidualStageMode::none;
  void *residual_stage_context = nullptr;
  ResidualStageFn residual_stage_fn = nullptr;
  // Optional FULL-only execution adapter. Called synchronously by the worker;
  // descriptor and provider views are borrowed for this call. The context must
  // remain valid until fence or Run destruction. Return IM2P_OK only after all
  // output callbacks and hardware completion; failures never retry in simulator.
  void *full_executor_context = nullptr;
  int (*full_executor)(void *, const im2p_matmul_desc_t *,
                       im2p_work_stats_extended_t *) = nullptr;
  // PIPELINE only, mutually exclusive with FULL and residual execution hooks.
  // Failure never destroys/resets the borrowed physical executor or retries in
  // the simulator. The caller retains device ownership for diagnosis.
  const StreamExecutor *stream_executor = nullptr;
};

struct StripeMetadata {
  bool has_exsia_theta = false;
  int16_t exsia_theta = 0;
};

struct StripeRtlTiming {
  uint64_t run_id = 0;
  size_t stripe_id = 0;
  size_t slot = 0;
  size_t row_begin = 0;
  size_t row_end = 0;
  uint64_t publish_cycle = 0;
  uint64_t completion_cycle = 0;
  uint64_t publish_to_completion_cycles = 0;
};

// Immutable borrowed storage owned by Run. A successful pipeline fence freezes
// the pointer and size until Run destruction; FULL and failed fences are empty.
struct StripeRtlTimingView {
  const StripeRtlTiming *data = nullptr;
  size_t size = 0;

  [[nodiscard]] bool empty() const noexcept { return size == 0; }
  [[nodiscard]] const StripeRtlTiming &operator[](size_t index) const noexcept {
    return data[index];
  }
  [[nodiscard]] const StripeRtlTiming *begin() const noexcept { return data; }
  [[nodiscard]] const StripeRtlTiming *end() const noexcept {
    return data == nullptr ? nullptr : data + size;
  }
};

struct SemanticStripe {
  uint64_t run_id = 0;
  size_t stripe_id = 0;
  size_t slot = 0;
  size_t row_begin = 0;
  size_t row_end = 0;
};

struct SemanticStripeView {
  const SemanticStripe *data = nullptr;
  size_t size = 0;

  [[nodiscard]] bool empty() const noexcept { return size == 0; }
  [[nodiscard]] const SemanticStripe &operator[](size_t index) const noexcept {
    return data[index];
  }
  [[nodiscard]] const SemanticStripe *begin() const noexcept { return data; }
  [[nodiscard]] const SemanticStripe *end() const noexcept {
    return data == nullptr ? nullptr : data + size;
  }
};

struct ResidualStripeStatsView {
  const ResidualStripeStats *data = nullptr;
  size_t size = 0;

  [[nodiscard]] bool empty() const noexcept { return size == 0; }
  [[nodiscard]] const ResidualStripeStats &
  operator[](size_t index) const noexcept {
    return data[index];
  }
  [[nodiscard]] const ResidualStripeStats *begin() const noexcept {
    return data;
  }
  [[nodiscard]] const ResidualStripeStats *end() const noexcept {
    return data == nullptr ? nullptr : data + size;
  }
};

// Per-semantic-stripe statistics from the independent residual simulator.
// Its cycle counters are durations in a separate RTL clock domain; they are
// never endpoints on, or additive with, StripeRtlTiming.
struct ResidualStripeTiming {
  uint64_t run_id = 0;
  size_t stripe_id = 0;
  size_t slot = 0;
  size_t row_begin = 0;
  size_t row_end = 0;
  uint64_t rmd_dot_calls = 0;
  im2p_work_stats_extended_t rmd_stats{};
};

struct ResidualStripeTimingView {
  const ResidualStripeTiming *data = nullptr;
  size_t size = 0;

  [[nodiscard]] bool empty() const noexcept { return size == 0; }
  [[nodiscard]] const ResidualStripeTiming &
  operator[](size_t index) const noexcept {
    return data[index];
  }
  [[nodiscard]] const ResidualStripeTiming *begin() const noexcept {
    return data;
  }
  [[nodiscard]] const ResidualStripeTiming *end() const noexcept {
    return data == nullptr ? nullptr : data + size;
  }
};

class Run;
struct ExecuteResult;
struct FenceResult;
struct PipelineOutputStage;

// execute() snapshots the activation backing store and copies the weight/scale
// inputs needed by the selected route. Full-mode output commits on a successful
// fence; pipeline output remains staged until explicit authorization. Accepted
// pipeline publication retains copies of event-owned residual handles through
// semantic completion; no reference to the producer event is retained. Calls on
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
  friend Status
  submit_stripe(Run &,
                const ggml::gemmini::quants::act::exsia::StripeReadyEvent &,
                StripeMetadata) noexcept;
  friend struct FenceResult;
  friend FenceResult fence(Run &) noexcept;
  friend PipelineOutputStage acquire_pipeline_output_stage(Run &) noexcept;
  friend Status authorize_output_commit(Run &, bool) noexcept;
  friend struct RunTestAccess;
};

struct ExecuteResult {
  Status status{};
  std::unique_ptr<Run> run;
};

struct FenceResult {
  Status status{};
  // Dense RTL statistics supplied by the selected executor, never host polls.
  im2p_work_stats_extended_t stats{};
  StripeRtlTimingView stripe_rtl_timings{};
  ResidualStripeStatsView residual_stripe_stats{};
  SemanticStripeView semantic_stripes{};
  ResidualStripeTimingView residual_stripe_timings{};
  uint64_t semantic_completion_count = 0;
  uint64_t rmd_dot_calls = 0;
  // Independent residual-simulator aggregate; never included in stats.
  im2p_work_stats_extended_t rmd_stats{};
};

struct PipelineOutputStage {
  Status status{};
  float *data = nullptr;
  size_t element_count = 0;
};

struct ArgsLayoutFingerprint {
  uint64_t size = 0;
  uint64_t native_weight_bytes = 0;
  uint64_t col_stride_f_out = 0;
  uint64_t stride_f_out = 0;
  uint64_t tile_i = 0;
};

[[nodiscard]] uint32_t compiled_activation_bits() noexcept;
[[nodiscard]] uint32_t compiled_weight_bits() noexcept;
[[nodiscard]] uint32_t compiled_dim() noexcept;
[[nodiscard]] ArgsLayoutFingerprint compiled_args_layout_fingerprint() noexcept;

[[nodiscard]] ExecuteResult execute(const ggml_gemmini_args_t *args,
                                    Mode mode = Mode::full,
                                    Options options = {}) noexcept;

[[nodiscard]] Status
submit_stripe(Run &run,
              const ggml::gemmini::quants::act::exsia::StripeReadyEvent &event,
              StripeMetadata metadata = {}) noexcept;

[[nodiscard]] FenceResult fence(Run &run) noexcept;

// Returns mutable frontend-owned float staging only after a successful pipeline
// fence. Residual-enabled runs have already completed their per-stripe checked
// merges; compatibility callers may perform other terminal work here. The
// borrowed destination remains untouched until explicit authorization.
[[nodiscard]] PipelineOutputStage
acquire_pipeline_output_stage(Run &run) noexcept;

// Pipeline output remains staged after a successful fence. Call this only after
// every configured semantic/residual stage reaches its terminal result. A false
// terminal result permanently prevents destination mutation.
[[nodiscard]] Status authorize_output_commit(Run &run,
                                             bool rmd_succeeded) noexcept;

} // namespace im2p::gemmini
