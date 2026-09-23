#pragma once
#include "scheduled_work.hpp"
#include "timing_events.hpp"
#include "../include/im2p_cycle_service.h"

namespace im2p::cycle {
struct ModelResult {
  im2p_cycle_result_t counters{};
  Trace trace;
  im2p_cycle_service_result_t service{};
};
ModelResult estimate(const im2p_cycle_model_config_t &,
                     const im2p_cycle_request_t &,
                     const im2p_compact_runs_t *runs = nullptr,
                     bool drain_final_release = false);
} // namespace im2p::cycle
