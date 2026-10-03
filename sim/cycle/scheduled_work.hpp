#pragma once
#include "../common/gemmini_schedule.hpp"
#include "timing_profile.hpp"
#include <vector>

namespace im2p::cycle {
struct ScheduledFragment {
  gemmini::FragmentPlan plan;
  uint64_t planner_loop;
};
struct ScheduledWork {
  gemmini::LoopPlan first_plan;
  unsigned max_i = 0, max_j = 0, max_k = 0;
  unsigned rows = 0, columns = 0, reduction = 0;
  unsigned scale_rows = 0, fragment_base = 0;
  bool final_store = false;
  std::vector<ScheduledFragment> fragments;
};
struct Schedule {
  gemmini::ScheduleConfig planner;
  std::vector<ScheduledWork> work;
  uint64_t planner_loops = 0, fragments = 0;
};
Schedule expand_work(const im2p_cycle_model_config_t &,
                     const im2p_cycle_request_t &,
                     const im2p_compact_runs_t *runs = nullptr);
} // namespace im2p::cycle
