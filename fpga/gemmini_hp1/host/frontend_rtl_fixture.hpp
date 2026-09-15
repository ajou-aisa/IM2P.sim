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

struct FrontendRtlWork {
  std::uint32_t work_id;
  std::uint32_t host_slot;
  std::uint32_t row_begin;
  std::uint32_t rows;
  std::uint32_t columns;
  std::uint32_t k;
  WorkPlanV1 plan;
  std::vector<std::int8_t> activations; // Decoded row-major rows * k scalar bytes.
  std::vector<std::int8_t> weights; // Decoded row-major k * columns signed codes.
  std::vector<std::uint32_t> carriers; // Block-major ceil(k / 32) * columns.
};

struct FrontendRtlTiming {
  std::uint64_t start_cycle;
  std::uint64_t done_cycle;
};

using FrontendRtlWorkExecute = int (*)(void *, const FrontendRtlWork &,
                                     std::vector<std::int32_t> &, FrontendRtlTiming &);

void run_frontend_ws_rtl_fixture(const Capability &, void *, FrontendRtlWorkExecute);

}
