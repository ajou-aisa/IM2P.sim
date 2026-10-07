#include "gemmini_paired_schedule.hpp"

#include <algorithm>
#include <cassert>
#include <utility>

namespace im2p::gemmini::paired {

namespace {

constexpr std::size_t kBlockK = HardwareShape::block_k;

std::size_t ceil_div(std::size_t value, std::size_t divisor) {
  return (value + divisor - 1) / divisor;
}

std::size_t fragments_per_block(std::size_t dim) {
  return std::max<std::size_t>(1, kBlockK / dim);
}

std::size_t nth_set_bit(std::uint32_t mask, std::size_t position) {
  for (std::size_t bit = 0; bit < kBlockK; ++bit)
    if ((mask >> bit & 1U) && position-- == 0) return bit;
  assert(false);
  return kBlockK;
}

} // namespace

std::size_t snapped_tile_k(std::size_t tile_k, std::size_t dim) {
  const auto fpb = fragments_per_block(dim);
  return std::max(fpb, tile_k / fpb * fpb);
}

bool plan_stripe(const ScheduleConfig &main, std::size_t row_begin, std::size_t row_count,
                 const Residual &residual, const PairCapacity &capacity, PairPlan &plan) {
  if (!valid_config(main) || !main.runs.empty() || !row_count ||
      row_begin >= main.shape.m || row_count > main.shape.m - row_begin) return false;
  plan = PairPlan{};
  plan.main = main;
  plan.capacity = capacity;
  plan.row_begin = row_begin;
  plan.row_end = row_begin + row_count;
  if (!residual.rows) return true;
  // Residual scales are Main's block scales and its W is Main's resident tile.
  if (!main.split_at_block || !main.backing_scales || !residual.runs ||
      residual.runs->original_k != main.shape.k) return false;
  const auto dim = main.hardware.dim;
  ScheduleConfig compact{{residual.rows, main.shape.n, residual.compact_k},
                         main.hardware,
                         {1, main.tile.tile_j, fragments_per_block(dim), residual.rows},
                         {residual.activation_stride, main.layout.weight_stride,
                          main.layout.scale_stride, residual.output_stride, 0}};
  if (!set_compact_runs(compact, residual.runs)) return false;

  auto &fit = plan.fit;
  fit.main_groups = ceil_div(std::min(row_count, main.tile.tile_i * dim), dim);
  fit.residual_groups = ceil_div(residual.rows, dim);
  fit.j_tiles = std::min(main.tile.tile_j, ceil_div(main.shape.n, dim));
  fit.max_k = ceil_div(std::min(main.shape.k, kBlockK), dim);
  for (const auto &run : compact.runs)
    fit.max_chunks = std::max(fit.max_chunks,
                              ceil_div(run.compact_k_count, std::min(dim, kBlockK)));
  const auto groups = fit.main_groups + fit.residual_groups;
  plan.paired = main.tile.tile_i * dim >= row_count &&
      groups * fit.j_tiles * dim <= capacity.acc_rows_per_slot &&
      groups * fit.j_tiles <= capacity.work_ids_per_slot &&
      fit.residual_groups * fit.max_chunks * dim +
              (fit.main_groups + fit.j_tiles) * fit.max_k * dim <= capacity.sp_half_rows &&
      2 * fit.j_tiles <= capacity.scale_rows_per_slot;
  if (!plan.paired) return true;
  plan.main.tile.tile_k = snapped_tile_k(main.tile.tile_k, dim);
  compact.tile.tile_i = fit.residual_groups;
  plan.residual = std::move(compact);
  assert(valid_config(plan.main) && valid_config(plan.residual));
  return true;
}

PairCursor first_cursor(const PairPlan &plan) {
  PairCursor cursor{};
  cursor.main.i = plan.row_begin;
  cursor.residual_done = !plan.paired;
  return cursor;
}

PairedLoop plan_paired_loop(const PairPlan &plan, const PairCursor &cursor) {
  PairedLoop loop{};
  loop.main = plan_loop(plan.main, plan.row_end, cursor.main);
  loop.micro_count = loop.main.fragment_count;
  // The K32 snap makes every paired Main loop exactly one whole block.
  assert(!plan.paired || (loop.main.k % kBlockK == 0 &&
                          loop.main.ks == std::min(kBlockK, plan.main.shape.k - loop.main.k)));
  if (cursor.residual_done) return loop;
  const auto residual = plan_loop(plan.residual, plan.residual.shape.m, cursor.residual);
  if (residual.j != loop.main.j || residual.original_block_id != loop.main.k / kBlockK)
    return loop;
  const auto &runs = plan.residual.runs;
  loop.run_index = static_cast<std::size_t>(
      std::find_if(runs.begin(), runs.end(),
                   [&](const im2p_compact_run_t &run) {
                     return run.original_block_id == residual.original_block_id;
                   }) - runs.begin());
  loop.residual = residual;
  loop.has_residual = true;
  loop.micro_count += residual.fragment_count;
  return loop;
}

void advance_paired_loop(const PairPlan &plan, const PairedLoop &loop, PairCursor &cursor) {
  advance_loop(plan.main, loop.main, cursor.main);
  if (!loop.has_residual) return;
  advance_loop(plan.residual, loop.residual, cursor.residual);
  cursor.residual_done = cursor.residual.i == plan.residual.shape.m;
}

PairMicroContext micro_context(const PairPlan &plan, const PairedLoop &loop, std::size_t ordinal) {
  assert(ordinal < loop.micro_count);
  const auto dim = plan.main.hardware.dim;
  PairMicroContext micro{};
  micro.w_fragment_stride = loop.main.jp;
  if (ordinal < loop.main.fragment_count) {
    micro.fragment = plan_fragment(plan.main, loop.main, ordinal);
    micro.acc_row = micro.fragment.output_index * dim;
    micro.work_id = micro.fragment.output_index;
    micro.j_tile = (micro.fragment.j - loop.main.j) / dim;
  } else {
    micro.kind = MicroKind::residual;
    micro.fragment = plan_fragment(plan.residual, loop.residual, ordinal - loop.main.fragment_count);
    micro.acc_row = plan.capacity.acc_rows_per_slot - (micro.fragment.output_index + 1) * dim;
    micro.work_id = plan.fit.main_groups * plan.fit.j_tiles + micro.fragment.output_index;
    micro.j_tile = (micro.fragment.j - loop.residual.j) / dim;
  }
  micro.fragment_id = micro.fragment.fragment_index;
  micro.output_index = micro.fragment.output_index;
  micro.scale_index = micro.fragment.scale_index;
  return micro;
}

std::size_t w_row(const PairPlan &plan, const PairedLoop &loop, const PairMicroContext &micro,
                  std::size_t row) {
  assert(row < micro.fragment.reduction);
  const auto dim = plan.main.hardware.dim;
  std::size_t local_k = micro.fragment.k - loop.main.k + row;
  if (micro.kind == MicroKind::residual) {
    const auto &run = plan.residual.runs[loop.run_index];
    local_k = nth_set_bit(run.original_k_mask, micro.fragment.k - run.compact_k_begin + row);
  }
  return local_k / dim * micro.w_fragment_stride + micro.j_tile * dim + local_k % dim;
}

ReadExtent residual_activation_read(const PairPlan &plan, const PairedLoop &loop,
                                    std::uint64_t packed_offset) {
  if (!loop.has_residual) return {false, 0, 0};
  return activation_read(plan.residual, loop.residual, packed_offset);
}

OutputExtent residual_output_extent(const PairPlan &plan, const PairedLoop &loop) {
  if (!loop.has_residual || !loop.residual.final_contribution) return {};
  return {true, loop.residual.is, loop.residual.js,
          loop.residual.i * plan.residual.layout.output_stride +
              loop.residual.j * sizeof(std::int32_t)};
}

} // namespace im2p::gemmini::paired
