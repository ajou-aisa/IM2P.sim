#include "../sim/common/gemmini_schedule.hpp"
#include "../sim/include/im2p_compact_runs.h"

#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <iostream>

using namespace im2p::gemmini;

static ScheduleConfig two_runs(std::size_t dim) {
  ScheduleConfig config{{1, 1, 22}, {dim, 8}, {1, 1, 2, 1}, {22, 1, 64, 4}};
  config.original_k = 64;
  config.runs = {{0, 0x00000fff, 0, 12}, {1, 0x000003ff, 12, 10}};
  return config;
}

static void two_carriers(std::size_t dim) {
  auto config = two_runs(dim);
  assert(valid_config(config));
  constexpr std::array<std::int32_t, 2> carrier{3, -5};
  LoopCursor cursor{};
  std::int32_t output = 0;
  for (std::size_t run = 0; run < 2; ++run) {
    const auto loop = plan_loop(config, config.shape.m, cursor);
    assert(loop.original_block_id == run);
    assert(loop.k == (run == 0 ? 0 : 12));
    assert(loop.ks == (run == 0 ? 12 : 10));
    assert(loop.fragment_base == run * (dim == 16 ? 2 : 1));
    assert(loop.accumulate == (run != 0));
    assert(loop.final_contribution == (run == 1));
    const auto fragment = plan_fragment(config, loop, 0);
    assert(fragment.reduction == loop.ks);
    assert(fragment.scale_index == run);
    assert(fragment.accumulate == (run != 0));
    assert(fragment.final_contribution == (run == 1));
    assert(scale_read(config, loop, 0).byte_offset == run * config.layout.scale_stride);
    output += static_cast<std::int32_t>(loop.ks) * carrier[run];
    advance_loop(config, loop, cursor);
  }
  assert(cursor.i == config.shape.m && output == -14);
  std::cout << "run-aware A8D" << dim << " K12+10 unequal-carrier=-14 PASS\n";
}

static void run_boundaries() {
  ScheduleConfig config{{1, 1, 32}, {16, 8}, {1, 1, 2, 1}, {32, 1, 128, 4}};
  config.original_k = 128;
  config.runs = {{0, 0x7fffffffu, 0, 31}, {3, 1, 31, 1}};
  assert(valid_config(config));
  LoopCursor cursor{};
  const auto first = plan_loop(config, 1, cursor);
  assert(first.k == 0 && first.ks == 31 && first.fragment_count == 2);
  assert(first.original_block_id == 0 && first.fragment_base == 0);
  assert(plan_fragment(config, first, 0).reduction == 16);
  assert(plan_fragment(config, first, 1).reduction == 15);
  assert(!plan_fragment(config, first, 1).final_contribution);
  advance_loop(config, first, cursor);
  const auto last = plan_loop(config, 1, cursor);
  assert(last.k == 31 && last.ks == 1 && last.fragment_count == 1);
  assert(last.original_block_id == 3 && last.fragment_base == 6);
  assert(last.accumulate && last.final_contribution && last.last);
  assert(plan_fragment(config, last, 0).reduction == 1);
  assert(plan_fragment(config, last, 0).final_contribution);
  assert(scale_read(config, last, 0).byte_offset == 3 * config.layout.scale_stride);
  advance_loop(config, last, cursor);
  assert(cursor.i == 1 && cursor.j == 0 && cursor.k == 0);
  std::cout << "run-aware A8D16 K31+1 block0/3 gap PASS\n";
}

static void run_local_tile_position() {
  ScheduleConfig config{{1, 1, 33}, {16, 8}, {1, 1, 1, 1}, {33, 1, 64, 4}};
  config.original_k = 64;
  config.runs = {{0, 1, 0, 1}, {1, UINT32_MAX, 1, 32}};
  assert(valid_config(config));
  LoopCursor cursor{};
  const std::array<std::size_t, 3> expected_k{0, 1, 17};
  const std::array<std::size_t, 3> expected_ks{1, 16, 16};
  const std::array<std::size_t, 3> expected_base{0, 2, 3};
  for (std::size_t i = 0; i < 3; ++i) {
    const auto loop = plan_loop(config, 1, cursor);
    assert(loop.k == expected_k[i] && loop.ks == expected_ks[i]);
    assert(loop.fragment_base == expected_base[i]);
    assert(loop.original_block_id == (i ? 1u : 0u));
    assert(scale_read(config, loop, 0).byte_offset == (i ? 64u : 0u));
    assert(loop.final_contribution == (i == 2));
    advance_loop(config, loop, cursor);
  }
  assert(cursor.i == 1);
  std::cout << "run-aware A8D16 run-local tile position PASS\n";
}

static void logical_loop_order() {
  auto config = two_runs(16);
  config.shape.m = 17;
  config.shape.n = 17;
  LoopCursor cursor{};
  std::size_t loops = 0, first = 0, last = 0, final_contributions = 0;
  while (cursor.i < config.shape.m) {
    const auto loop = plan_loop(config, config.shape.m, cursor);
    assert(loop.i == (loops / 4 ? 16u : 0u));
    assert(loop.j == (loops / 2 % 2 ? 16u : 0u));
    assert(loop.k == (loops % 2 ? 12u : 0u));
    assert(loop.accumulate == (loops % 2 != 0));
    first += loop.first;
    last += loop.last;
    final_contributions += loop.final_contribution;
    advance_loop(config, loop, cursor);
    ++loops;
  }
  assert(loops == 8 && first == 1 && last == 1 && final_contributions == 4);
  std::cout << "run-aware A8D16 I/J logical loop flags PASS\n";
}

static void invalid_runs() {
  auto config = two_runs(16);
  config.runs[1].compact_k_begin = 13;
  assert(!valid_config(config));
  config.runs[1].compact_k_begin = 11;
  assert(!valid_config(config));
  config = two_runs(16);
  config.runs[1].original_k_mask = 0x1ff;
  assert(!valid_config(config));
  config = two_runs(16);
  config.runs[1].compact_k_count = 0;
  assert(!valid_config(config));
  config = two_runs(16);
  config.runs[1].original_block_id = 0;
  assert(!valid_config(config));
  config = two_runs(16);
  config.original_k = 40;
  assert(!valid_config(config));
  config = two_runs(16);
  config.runs[1].original_block_id = 32768;
  assert(!valid_config(config));
  config = two_runs(16);
  config.tile.tile_k = 0;
  assert(!valid_config(config));
  std::cout << "run-aware malformed runs rejected PASS\n";
}

int main(int argc, char **argv) {
  if (argc == 2 && std::strcmp(argv[1], "--invalid-runs") == 0) {
    invalid_runs();
    return 0;
  }
  assert(argc == 1);
  two_carriers(16);
  two_carriers(32);
  run_boundaries();
  run_local_tile_position();
  logical_loop_order();
  invalid_runs();
}
