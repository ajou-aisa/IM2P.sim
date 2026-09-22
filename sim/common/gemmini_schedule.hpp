#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

#include "../include/im2p_compact_runs.h"

// Value-free decomposition of the integrated simulator's existing work order.
// No model, C ABI, host slots, numerical buffers, or timing policy belongs here.
namespace im2p::gemmini {

struct GemmShape {
  std::size_t m = 0, n = 0, k = 0;
};

struct HardwareShape {
  std::size_t dim = 0;
  std::size_t operand_bits = 0;
  static constexpr std::size_t block_k = 32;
};

struct TilePlan {
  std::size_t tile_i = 1, tile_j = 1, tile_k = 1;
  std::size_t stripe_rows = 1;
};

struct Layout {
  // Host operands occupy one signed byte, including A4/W4. Hardware rows are
  // packed separately. Offsets below never contain a backing-object address.
  std::uint64_t activation_stride = 0, weight_stride = 0;
  std::uint64_t scale_stride = 0, output_stride = 0;
  std::uint64_t k_origin = 0;
};

struct ScheduleConfig {
  GemmShape shape{};
  HardwareShape hardware{};
  TilePlan tile{};
  Layout layout{};
  bool split_at_block = true;
  bool backing_scales = true;
  bool accumulate_first = false;
  std::uint32_t original_k = 0;
  std::vector<im2p_compact_run_t> runs{};
};

struct LoopCursor {
  std::size_t i = 0, j = 0, k = 0;
  std::uint64_t order = 0;
  bool first = true;
};

struct LoopPlan {
  std::size_t i = 0, j = 0, k = 0;
  std::size_t is = 0, js = 0, ks = 0;
  std::size_t ip = 0, jp = 0, kp = 0;
  std::uint64_t order = 0;
  std::size_t fragment_index = 0;
  std::uint32_t original_block_id = 0;
  // Preserve the current 16-bit hardware encoding, including its narrowing.
  // Descriptor admission/representability is not redesigned by this planner.
  std::uint16_t fragment_base = 0;
  bool first = false, last = false;
  bool first_contribution = false, accumulate = false, final_contribution = false;
  std::size_t scale_first_block = 0, scale_rows_per_block = 0, scale_rows = 0;
  std::size_t scale_release_count = 0, fragment_count = 0;
  std::size_t activation_packed_bytes = 0, weight_packed_bytes = 0;
  std::size_t activation_host_bytes = 0, weight_host_bytes = 0;
  std::size_t scale_packed_bytes = 0, scale_host_bytes = 0;
  std::size_t final_output_bytes = 0;
};

struct FragmentPlan {
  std::size_t i = 0, j = 0, k = 0;
  std::size_t rows = 0, columns = 0, reduction = 0;
  std::size_t fragment_index = 0, output_index = 0, scale_index = 0;
  bool first_contribution = false, accumulate = false, final_contribution = false;
};

struct ReadExtent {
  bool valid = true;
  std::uint32_t element_count = 0;
  std::uint64_t byte_offset = 0;
  // A valid empty extent is synthesized padding, not a host memory request.
};

bool valid_config(const ScheduleConfig &config);
bool set_compact_runs(ScheduleConfig &config, const im2p_compact_runs_t *view);
std::size_t padded(std::size_t value, std::size_t dim);
GemmShape padded_shape(const ScheduleConfig &config);
LoopPlan plan_loop(const ScheduleConfig &config, std::size_t stripe_end,
                   const LoopCursor &cursor);
void advance_loop(const ScheduleConfig &config, const LoopPlan &loop, LoopCursor &cursor);
// ExecuteController output context order is I-fastest, then J, then K.
FragmentPlan plan_fragment(const ScheduleConfig &config, const LoopPlan &loop,
                           std::size_t ordinal);
ReadExtent activation_read(const ScheduleConfig &config, const LoopPlan &loop,
                           std::uint64_t packed_offset);
ReadExtent weight_read(const ScheduleConfig &config, const LoopPlan &loop,
                       std::uint64_t packed_offset);
ReadExtent scale_read(const ScheduleConfig &config, const LoopPlan &loop,
                      std::uint64_t packed_offset);

// The byte fields describe buffer/request extents, NOT measured traffic totals.
// Actual repeated DMA requests, stalls, overlap and start/done cycles remain RTL
// facts. A future timing implementation can consume these facts without values.
} // namespace im2p::gemmini
