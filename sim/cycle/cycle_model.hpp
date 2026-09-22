#pragma once
#include "scheduled_work.hpp"
#include "timing_events.hpp"

namespace im2p::cycle {
struct ModelResult {
  im2p_cycle_result_t counters{};
  Trace trace;
};
ModelResult estimate(const im2p_cycle_model_config_t &,
                     const im2p_cycle_request_t &,
                     const im2p_compact_runs_t *runs = nullptr);
} // namespace im2p::cycle
