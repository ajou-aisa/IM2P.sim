#pragma once
#include "../include/im2p_cycle_model.h"
#include <cstdint>
#include <stdexcept>

namespace im2p::cycle {
struct Error : std::runtime_error {
  int status;
  Error(int code, const char *message)
      : std::runtime_error(message), status(code) {}
};

// Architectural control constants, not fitted GEMM latency coefficients.
// Source provenance and exact field names: docs/GEMMINI_CYCLE_MODEL.md.
struct RtlTimingProfile {
  // UpstreamWsConfig + pinned GemminiConfigs defaults.
  static constexpr unsigned load_queue = 8, store_queue = 2, execute_queue = 8;
  static constexpr unsigned load_entries = 8, store_entries = 4,
                            execute_entries = 16;
  static constexpr unsigned max_dma_requests = 16;
  // UpstreamWsControl / Top, HostCommandBridge, ScaleBackingLoader.
  static constexpr unsigned incoming_queue = 2, unrolled_queue = 2;
  static constexpr unsigned loop_queue = 2, transpose_queue = 2;
  static constexpr unsigned concurrent_loops = 2, read_slots = 8;
  static constexpr unsigned read_queue = 8, backing_queue = 1;
  static constexpr unsigned contexts = 16, scale_entries = 256;
  static constexpr unsigned completion_queue = 64, bridge_instructions = 11;
  // MeshWithDelays with tile_latency=0, mesh_output_delay=1.
  static constexpr unsigned tile_pipeline = 1, mesh_output_delay = 1;
  static constexpr unsigned mesh_ids = 5, tag_queue = mesh_ids + 1;
  // ExecuteController's non-transpose, garbage-D minimum row count.
  static constexpr unsigned minimum_compute_rows = 4;
  static constexpr unsigned synchronous_read = 1;
  im2p_cycle_hardware_t hardware{};
  im2p_cycle_timing_t memory{};
  unsigned dma_commands() const { return max_dma_requests / hardware.dim + 1; }
  unsigned array_latency() const {
    return hardware.dim * tile_pipeline + mesh_output_delay +
           (hardware.dim - 1) * tile_pipeline;
  }
};
void validate_config(const im2p_cycle_model_config_t &);
uint64_t checked_add(uint64_t, uint64_t);
uint64_t checked_mul(uint64_t, uint64_t);
} // namespace im2p::cycle
