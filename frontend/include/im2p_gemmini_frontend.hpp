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
  // Conservative minimum number of one-logical-cycle progress iterations
  // allowed without a matched completion. This is not a wall-clock timeout.
  uint64_t max_stalled_cycles = 65536;
};

struct StripeMetadata {
  bool has_exsia_theta = false;
  int16_t exsia_theta = 0;
};

class Run;
struct ExecuteResult;
struct FenceResult;
struct PipelineOutputStage;

// execute() snapshots the activation backing store and copies the weight/scale
// inputs needed by the selected route. Full-mode output commits on a successful
// fence; pipeline output remains staged until explicit authorization. Pipeline
// publication copies event-owned residual handles; no reference to the producer
// event is retained. Calls on one Run are internally synchronized.
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
  im2p_work_stats_extended_t stats{};
};

struct PipelineOutputStage {
  Status status{};
  float *data = nullptr;
  size_t element_count = 0;
};

[[nodiscard]] ExecuteResult execute(const ggml_gemmini_args_t *args,
                                    Mode mode = Mode::full,
                                    Options options = {}) noexcept;

[[nodiscard]] Status
submit_stripe(Run &run,
              const ggml::gemmini::quants::act::exsia::StripeReadyEvent &event,
              StripeMetadata metadata = {}) noexcept;

[[nodiscard]] FenceResult fence(Run &run) noexcept;

// Returns mutable frontend-owned float staging only after a successful pipeline
// fence. The existing RMD path may finalize corrections there; the borrowed
// destination remains untouched until explicit authorization.
[[nodiscard]] PipelineOutputStage
acquire_pipeline_output_stage(Run &run) noexcept;

// Pipeline output remains staged after a successful fence. Call this only after
// the existing 8-bit RMD path reaches its terminal result. A failed RMD result
// permanently prevents destination mutation.
[[nodiscard]] Status authorize_output_commit(Run &run,
                                             bool rmd_succeeded) noexcept;

} // namespace im2p::gemmini
