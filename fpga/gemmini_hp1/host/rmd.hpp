#pragma once

#include "residual/rmd/rmd-im2p-executor.hpp"
#include "uart.hpp"
#include <im2p_sim.h>
#include <im2p_compact_runs.h>

#include <cstdint>
#include <vector>

namespace im2p::gemmini_hp1 {

// Each compact dot belongs to one weight block and contains at most 32 K codes.
// Diagnostic legacy raw path only. Production residual uses RmdScuWork below.
struct RmdRawWork {
  WorkPlanV1 plan;
  std::uint32_t work_id = 0;
  std::uint32_t host_slot = 0;
  std::vector<std::int8_t>
      activations;                  // Canonical scalar bytes, plan.m * plan.k.
  std::vector<std::int8_t> weights; // Canonical scalar bytes, plan.k * plan.n.
};

using RmdRawExecute = int (*)(void *, const RmdRawWork &,
                              std::vector<std::int32_t> &, std::uint64_t &);

// Production residual is ordinary dense HP1 work. The label is host provenance;
// it never selects the diagnostic rmdRaw datapath.
struct RmdScuWork : RmdRawWork {
  im2p_production_geometry_v1_t geometry{};
  std::uint32_t original_block_id = 0;
  std::vector<std::uint32_t> carriers;
};
using RmdScuExecute = int (*)(void *, const RmdScuWork &,
                              std::vector<std::int32_t> &, std::uint64_t &);

// Fixture-only compact K work. Carriers are indexed by run ordinal, then N.
struct RmdRunWork : RmdRawWork {
  im2p_production_geometry_v1_t geometry{};
  std::uint32_t original_k = 0;
  std::vector<im2p_compact_run_t> runs;
  std::vector<std::uint32_t> carriers;
};
using RmdRunExecute = int (*)(void *, const RmdRunWork &,
                              std::vector<std::int32_t> &, std::uint64_t &);

struct RmdExecutorContext {
  Capability capability;
  void *context = nullptr;
  RmdRawExecute execute = nullptr;
  std::uint32_t next_work_id = 0;
  std::uint32_t host_slot = 0;
  RmdScuExecute execute_scu = nullptr;
};

int execute_rmd_scu_descriptor(void *, const im2p_matmul_desc_t *,
                               const im2p_production_geometry_v1_t *,
                               im2p_work_stats_extended_t *);

int execute_rmd_raw_descriptor(void *, const im2p_matmul_desc_t *,
                               im2p_work_stats_extended_t *);

ggml::gemmini::rmd::RmdStatus
execute_rmd_packet(RmdExecutorContext &, const ggml_gemmini_args_t &,
                   const ggml::gemmini::rmd::StripePacket &,
                   ggml::gemmini::rmd::Correction &,
                   ggml::gemmini::rmd::RmdExecutionMetrics * = nullptr);

} // namespace im2p::gemmini_hp1
