#pragma once

#include "uart.hpp"

#include <cstdint>
#include <vector>

namespace im2p::gemmini_hp1 {

struct FrontendRtlFragment {
  std::uint32_t work_id;
  std::uint32_t valid_rows;
  std::uint32_t valid_columns;
  WorkPlanV1 plan;
  Fragment fragment;
  std::vector<std::int8_t> activations;
  std::vector<std::int8_t> weights;
  std::vector<std::uint32_t> carriers;
};

using FrontendRtlExecute = int (*)(void *, const FrontendRtlFragment &,
                                 std::vector<std::int32_t> &, std::uint64_t &);

void run_frontend_rtl_fixture(const Capability &, void *, FrontendRtlExecute);

}
