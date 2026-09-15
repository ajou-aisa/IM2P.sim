#pragma once

#include "uart.hpp"
#include "residual/rmd/rmd-im2p-executor.hpp"
#include <im2p_sim.h>

#include <cstdint>
#include <vector>

namespace im2p::gemmini_hp1 {

// Each compact dot belongs to one weight block and contains at most 32 K codes.
// Its returned INT32 values are unscaled; block/radix composition stays on CPU.
struct RmdRawWork {
  WorkPlanV1 plan;
  std::uint32_t work_id = 0;
  std::uint32_t host_slot = 0;
  std::vector<std::int8_t> activations; // Canonical scalar bytes, plan.m * plan.k.
  std::vector<std::int8_t> weights; // Canonical scalar bytes, plan.k * plan.n.
};

using RmdRawExecute = int (*)(void *, const RmdRawWork &,
                             std::vector<std::int32_t> &, std::uint64_t &);

struct RmdExecutorContext {
  Capability capability;
  void *context = nullptr;
  RmdRawExecute execute = nullptr;
  std::uint32_t next_work_id = 0;
  std::uint32_t host_slot = 0;
};

int execute_rmd_raw_descriptor(void *, const im2p_matmul_desc_t *,
                               im2p_work_stats_extended_t *);

ggml::gemmini::rmd::RmdStatus execute_rmd_packet(
    RmdExecutorContext &, const ggml_gemmini_args_t &,
    const ggml::gemmini::rmd::StripePacket &,
    ggml::gemmini::rmd::Correction &,
    ggml::gemmini::rmd::RmdExecutionMetrics * = nullptr);

}
