#include "gemmini_schedule.hpp"

#include <algorithm>
#include <cassert>
#include <iterator>
#include <limits>
#include <utility>

namespace im2p::gemmini {

namespace {

bool valid_run_map(std::uint32_t original_k, const im2p_compact_run_t *runs,
                   std::size_t count, std::size_t compact_k, std::size_t dim) {
  if (!original_k || !runs || !count || count > compact_k ||
      count > std::numeric_limits<std::size_t>::max() / sizeof(*runs)) return false;
  const auto fragments_per_block = std::max<std::size_t>(1, HardwareShape::block_k / dim);
  std::uint64_t end = 0;
  std::uint32_t previous_block = 0;
  for (std::size_t i = 0; i < count; ++i) {
    const auto &run = runs[i];
    if (!run.compact_k_count || run.compact_k_count > HardwareShape::block_k ||
        run.compact_k_begin != end ||
        (i && run.original_block_id <= previous_block) ||
        run.original_block_id > UINT16_MAX / fragments_per_block) return false;
    unsigned bits = 0;
    for (unsigned bit = 0; bit < HardwareShape::block_k; ++bit) {
      if (!(run.original_k_mask & (std::uint32_t{1} << bit))) continue;
      if (std::uint64_t{run.original_block_id} * HardwareShape::block_k + bit >= original_k)
        return false;
      ++bits;
    }
    if (bits != run.compact_k_count) return false;
    end += run.compact_k_count;
    if (end > compact_k || end > UINT32_MAX) return false;
    previous_block = run.original_block_id;
  }
  return end == compact_k;
}

}

bool valid_config(const ScheduleConfig &c) {
  const auto dim = c.hardware.dim;
  return (dim == 16 || dim == 32 || dim == 64) &&
      (c.hardware.operand_bits == 4 || c.hardware.operand_bits == 8) &&
      c.shape.m && c.shape.n && c.shape.k && c.tile.tile_i &&
      c.tile.tile_j && c.tile.tile_k && c.tile.stripe_rows &&
      c.tile.tile_i <= UINT16_MAX / dim && c.tile.tile_j <= UINT16_MAX / dim &&
      c.tile.tile_k <= UINT32_MAX / dim &&
      c.shape.m <= UINT32_MAX && c.shape.n <= UINT32_MAX && c.shape.k <= UINT32_MAX &&
      (c.split_at_block || c.shape.k <= HardwareShape::block_k) &&
      (c.runs.empty() ? c.original_k == 0 :
          valid_run_map(c.original_k, c.runs.data(), c.runs.size(), c.shape.k, dim));
}

bool set_compact_runs(ScheduleConfig &config, const im2p_compact_runs_t *view) {
  if (!view || view->version != IM2P_COMPACT_RUNS_VERSION ||
      view->struct_size != sizeof(*view) || !valid_config(config) ||
      !valid_run_map(view->original_k, view->runs, view->run_count,
                     config.shape.k, config.hardware.dim)) return false;
  std::vector<im2p_compact_run_t> owned(view->runs, view->runs + view->run_count);
  config.original_k = view->original_k;
  config.runs = std::move(owned);
  return true;
}

std::size_t padded(std::size_t value, std::size_t dim) {
  assert(dim && value <= std::numeric_limits<std::size_t>::max() - (dim - 1));
  return (value + dim - 1) / dim * dim;
}

GemmShape padded_shape(const ScheduleConfig &c) {
  assert(valid_config(c));
  return {padded(c.shape.m, c.hardware.dim), padded(c.shape.n, c.hardware.dim),
          padded(c.shape.k, c.hardware.dim)};
}

LoopPlan plan_loop(const ScheduleConfig &c, std::size_t stripe_end, const LoopCursor &cursor) {
  assert(valid_config(c));
  assert(cursor.i < stripe_end && stripe_end <= c.shape.m);
  assert(cursor.j < c.shape.n && cursor.k < c.shape.k);
  const auto dim = c.hardware.dim;
  const auto block_k = HardwareShape::block_k;
  LoopPlan loop{};
  loop.i = cursor.i;
  loop.j = cursor.j;
  loop.k = cursor.k;
  loop.order = cursor.order;
  loop.is = std::min(c.tile.tile_i * dim, stripe_end - loop.i);
  loop.js = std::min(c.tile.tile_j * dim, c.shape.n - loop.j);
  if (c.runs.empty()) {
    const auto tile_end = !c.split_at_block ? c.shape.k :
        std::min(c.shape.k, (loop.k / (c.tile.tile_k * dim) + 1) * c.tile.tile_k * dim);
    const auto block_end = !c.split_at_block ? tile_end :
        std::min(tile_end, (loop.k / block_k + 1) * block_k);
    loop.ks = std::min(block_end - loop.k, block_k);
    loop.fragment_index = loop.k / std::min(dim, block_k);
  } else {
    const auto run = std::prev(std::upper_bound(
        c.runs.begin(), c.runs.end(), loop.k,
        [](std::size_t k, const im2p_compact_run_t &r) { return k < r.compact_k_begin; }));
    const auto position = loop.k - run->compact_k_begin;
    const auto tile_span = c.tile.tile_k * dim;
    loop.ks = std::min<std::size_t>(run->compact_k_count - position,
                                    tile_span - position % tile_span);
    loop.original_block_id = run->original_block_id;
    loop.fragment_index = run->original_block_id * std::max<std::size_t>(1, block_k / dim) +
        position / std::min(dim, block_k);
  }
  loop.ip = padded(loop.is, dim);
  loop.jp = padded(loop.js, dim);
  loop.kp = padded(loop.ks, dim);
  loop.fragment_base = static_cast<std::uint16_t>(loop.fragment_index);
  loop.first = cursor.first;
  loop.final_contribution = loop.k + loop.ks == c.shape.k;
  loop.accumulate = c.accumulate_first || loop.k != 0;
  loop.first_contribution = !loop.accumulate;
  loop.last = loop.i + loop.is == stripe_end && loop.j + loop.js == c.shape.n &&
      loop.final_contribution;
  const auto fragments_per_block = std::max<std::size_t>(1, block_k / dim);
  loop.scale_first_block = loop.fragment_base / fragments_per_block;
  const auto last_block = (loop.fragment_base + loop.kp / dim - 1) / fragments_per_block;
  loop.scale_rows_per_block = loop.jp / dim;
  const auto scale_blocks = last_block - loop.scale_first_block + 1;
  loop.scale_rows = scale_blocks * loop.scale_rows_per_block;
  loop.scale_release_count = loop.scale_rows * dim;
  loop.fragment_count = (loop.ip / dim) * (loop.jp / dim) * (loop.kp / dim);
  loop.activation_packed_bytes = loop.ip * loop.kp * c.hardware.operand_bits / 8;
  loop.weight_packed_bytes = loop.kp * loop.jp * c.hardware.operand_bits / 8;
  loop.activation_host_bytes = loop.is * loop.ks;
  loop.weight_host_bytes = loop.ks * loop.js;
  loop.scale_packed_bytes = loop.scale_rows * dim * sizeof(std::uint32_t);
  loop.scale_host_bytes = c.backing_scales ? scale_blocks * loop.js * sizeof(std::uint32_t) : 0;
  loop.final_output_bytes = loop.final_contribution ? loop.is * loop.js * sizeof(std::int32_t) : 0;
  return loop;
}

void advance_loop(const ScheduleConfig &c, const LoopPlan &loop, LoopCursor &cursor) {
  cursor.first = false;
  ++cursor.order;
  cursor.k += loop.ks;
  if (cursor.k == c.shape.k) {
    cursor.k = 0;
    cursor.j += loop.js;
    if (cursor.j == c.shape.n) {
      cursor.j = 0;
      cursor.i += loop.is;
    }
  }
}

FragmentPlan plan_fragment(const ScheduleConfig &c, const LoopPlan &loop, std::size_t ordinal) {
  assert(ordinal < loop.fragment_count);
  const auto dim = c.hardware.dim;
  const auto max_i = loop.ip / dim, max_j = loop.jp / dim;
  const auto i = ordinal % max_i, j = ordinal / max_i % max_j, k = ordinal / (max_i * max_j);
  FragmentPlan result{};
  result.i = loop.i + i * dim;
  result.j = loop.j + j * dim;
  result.k = loop.k + k * dim;
  result.rows = std::min(dim, loop.is - i * dim);
  result.columns = std::min(dim, loop.js - j * dim);
  result.reduction = std::min(dim, loop.ks - k * dim);
  result.fragment_index = loop.fragment_index + k;
  result.output_index = i * max_j + j;
  result.scale_index = (loop.fragment_base + k) /
      std::max<std::size_t>(1, HardwareShape::block_k / dim) * max_j + j;
  result.accumulate = loop.accumulate || k != 0;
  result.first_contribution = !result.accumulate;
  result.final_contribution = loop.final_contribution && k + 1 == loop.kp / dim;
  return result;
}

ReadExtent activation_read(const ScheduleConfig &c, const LoopPlan &loop, std::uint64_t offset) {
  if (offset >= loop.activation_packed_bytes) return {false, 0, 0};
  const auto element = offset * 8 / c.hardware.operand_bits;
  const auto i = element / loop.kp, k = element % loop.kp;
  if (i >= loop.is || k >= loop.ks) return {};
  return {true, static_cast<std::uint32_t>(std::min<std::size_t>(c.hardware.dim, loop.ks - k)),
          (loop.i + i) * c.layout.activation_stride + loop.k + k};
}

ReadExtent weight_read(const ScheduleConfig &c, const LoopPlan &loop, std::uint64_t offset) {
  if (offset >= loop.weight_packed_bytes) return {false, 0, 0};
  const auto element = offset * 8 / c.hardware.operand_bits;
  const auto k = element / loop.jp, j = element % loop.jp;
  if (k >= loop.ks || j >= loop.js) return {};
  return {true, static_cast<std::uint32_t>(std::min<std::size_t>(c.hardware.dim, loop.js - j)),
          (loop.k + k) * c.layout.weight_stride + loop.j + j};
}

ReadExtent scale_read(const ScheduleConfig &c, const LoopPlan &loop, std::uint64_t offset) {
  // Preserve synthesized-scale handling: no host request or range check in this mode.
  if (!c.backing_scales) return {};
  const auto row_bytes = c.hardware.dim * sizeof(std::uint32_t);
  if (offset % row_bytes || offset / row_bytes >= loop.scale_rows) return {false, 0, 0};
  const auto row = offset / row_bytes;
  const auto block_offset = row / loop.scale_rows_per_block;
  const auto source_column = loop.j + row % loop.scale_rows_per_block * c.hardware.dim;
  const auto global_block = c.runs.empty()
      ? (c.layout.k_origin + loop.k) / HardwareShape::block_k + block_offset
      : loop.original_block_id;
  return {true, static_cast<std::uint32_t>(std::min<std::size_t>(c.hardware.dim, c.shape.n - source_column)),
          global_block * c.layout.scale_stride + source_column * sizeof(std::uint32_t)};
}

} // namespace im2p::gemmini
