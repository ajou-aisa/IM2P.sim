#include "gemmini_paired_schedule.hpp"

#include <algorithm>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <map>
#include <set>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

using namespace im2p::gemmini;
using namespace im2p::gemmini::paired;

namespace {

// Per-slot capacity, a8w8 profiles. Compat (1x) = geometry_fits: ACC rows / 2,
// 64 work IDs (workBase = slot * 64), SP banks * rows / 2, 128 scale rows.
// Physical = the P1 table (a8w8 d16 3x ACC, 256 work entries; d64 2x, 128).
PairCapacity compat(std::size_t dim) {
  return dim == 16 ? PairCapacity{512, 64, 8192, 128} : PairCapacity{128, 64, 2048, 128};
}
PairCapacity physical(std::size_t dim) {
  return dim == 16 ? PairCapacity{1536, 128, 8192, 128} : PairCapacity{256, 64, 2048, 128};
}

struct Tile {
  std::size_t i, j, k;
};

struct Runs {
  std::vector<im2p_compact_run_t> runs;
  im2p_compact_runs_t view{};
  std::size_t compact_k = 0;
};

// masks[b] is block b's surviving-K mask; zero blocks have no run.
Runs make_runs(std::size_t k, const std::vector<std::uint32_t> &masks) {
  Runs result;
  for (std::size_t block = 0; block < masks.size(); ++block) {
    if (!masks[block]) continue;
    std::uint32_t count = 0;
    for (unsigned bit = 0; bit < 32; ++bit) count += masks[block] >> bit & 1U;
    result.runs.push_back({static_cast<std::uint32_t>(block), masks[block],
                           static_cast<std::uint32_t>(result.compact_k), count});
    result.compact_k += count;
  }
  result.view = {IM2P_COMPACT_RUNS_VERSION, sizeof(im2p_compact_runs_t),
                 static_cast<std::uint32_t>(k), result.runs.size(), result.runs.data()};
  return result;
}

ScheduleConfig main_config(std::size_t dim, std::size_t m, std::size_t n, std::size_t k, Tile tile) {
  return {{m, n, k}, {dim, 8}, {tile.i, tile.j, tile.k, m}, {k, n, n * 4, n * 4, 0}};
}

std::size_t ceil_div(std::size_t a, std::size_t b) { return (a + b - 1) / b; }

// The fit rule restated independently of the planner.
bool fit_rule(std::size_t dim, std::size_t m, std::size_t n, std::size_t k, Tile tile,
              std::size_t m_r, const Runs &runs, const PairCapacity &c) {
  const auto j = std::min(tile.j, ceil_div(n, dim));
  const auto i_m = ceil_div(std::min(m, tile.i * dim), dim);
  const auto i_r = ceil_div(m_r, dim);
  const auto max_k = ceil_div(std::min<std::size_t>(k, 32), dim);
  std::size_t chunks = 0;
  for (const auto &run : runs.runs)
    chunks = std::max(chunks, ceil_div(run.compact_k_count, std::min<std::size_t>(dim, 32)));
  return tile.i * dim >= m && (i_m + i_r) * j * dim <= c.acc_rows_per_slot &&
         (i_m + i_r) * j <= c.work_ids_per_slot &&
         i_r * chunks * dim + (i_m + j) * max_k * dim <= c.sp_half_rows &&
         2 * j <= c.scale_rows_per_slot;
}

std::size_t bit_of(std::uint32_t mask, std::size_t position) {
  for (std::size_t bit = 0; bit < 32; ++bit)
    if ((mask >> bit & 1U) && position-- == 0) return bit;
  assert(false);
  return 32;
}

// Walks one paired stripe and checks every invariant. Returns the W strides seen
// per j-tile origin.
std::map<std::size_t, std::set<std::size_t>> check_invariants(const PairPlan &plan,
                                                              std::size_t m_r, const Runs &runs) {
  const auto dim = plan.main.hardware.dim;
  const auto n = plan.main.shape.n, k = plan.main.shape.k;
  const auto fpb = std::max<std::size_t>(1, 32 / dim);
  std::set<std::tuple<std::size_t, std::size_t, std::size_t>> main_seen, residual_seen;
  std::size_t main_work = 0, residual_work = 0;
  std::map<std::size_t, std::set<std::size_t>> strides;
  auto cursor = first_cursor(plan);
  bool last = false;
  do {
    const auto loop = plan_paired_loop(plan, cursor);
    assert(loop.main.k % 32 == 0 && loop.main.ks == std::min<std::size_t>(32, k - loop.main.k));
    strides[loop.main.j].insert(loop.main.jp);
    std::set<std::size_t> main_rows, residual_rows;
    std::map<std::pair<MicroKind, std::size_t>, std::size_t> work_ids;
    for (std::size_t ordinal = 0; ordinal < loop.micro_count; ++ordinal) {
      const auto micro = micro_context(plan, loop, ordinal);
      const auto &f = micro.fragment;
      assert((micro.kind == MicroKind::main) == (ordinal < loop.main.fragment_count));
      assert(micro.w_fragment_stride == loop.main.jp);
      assert(micro.acc_row + dim <= plan.capacity.acc_rows_per_slot);
      assert(micro.work_id < plan.capacity.work_ids_per_slot);
      const auto key = std::make_pair(micro.kind, micro.output_index);
      const auto [it, inserted] = work_ids.emplace(key, micro.work_id);
      assert(it->second == micro.work_id);
      (void)inserted;
      auto &rows = micro.kind == MicroKind::main ? main_rows : residual_rows;
      for (std::size_t row = 0; row < dim; ++row) rows.insert(micro.acc_row + row);
      const auto max_i = loop.main.ip / dim, max_j = loop.main.jp / dim;
      if (micro.kind == MicroKind::main) {
        assert(main_seen.emplace(f.i, f.j, f.k).second);
        main_work += f.rows * f.columns * f.reduction;
        assert(micro.fragment_id == f.k / std::min<std::size_t>(dim, 32));
        for (std::size_t row = 0; row < f.reduction; ++row)
          assert(w_row(plan, loop, micro, row) ==
                 (ordinal / (max_i * max_j) * max_j + micro.j_tile) * dim + row);
        continue;
      }
      assert(residual_seen.emplace(f.i, f.j, f.k).second);
      residual_work += f.rows * f.columns * f.reduction;
      const auto &run = runs.runs[loop.run_index];
      assert(run.original_block_id == loop.main.k / 32);
      // C4: the compact path's fragment ID.
      assert(micro.fragment_id == run.original_block_id * fpb +
                                      (f.k - run.compact_k_begin) / std::min<std::size_t>(dim, 32));
      for (std::size_t row = 0; row < f.reduction; ++row) {
        // llama gathers compact W row e from original W row block * 32 + bit.
        const auto original =
            std::size_t{run.original_block_id} * 32 + bit_of(run.original_k_mask, f.k - run.compact_k_begin + row);
        assert(original >= loop.main.k && original < loop.main.k + loop.main.ks);
        // Main's fragment holding that W row, and its LoopMatmul SP row (k * max_j + j) * D + r.
        bool found = false;
        for (std::size_t o = 0; o < loop.main.fragment_count; ++o) {
          const auto mf = plan_fragment(plan.main, loop.main, o);
          if (mf.j != f.j || original < mf.k || original >= mf.k + mf.reduction) continue;
          const auto kf = o / (max_i * max_j);
          assert(w_row(plan, loop, micro, row) == (kf * max_j + micro.j_tile) * dim + original - mf.k);
          assert(micro.scale_index == mf.scale_index);
          found = true;
          break;
        }
        assert(found);
      }
    }
    for (auto row : residual_rows) assert(!main_rows.count(row));
    if (loop.has_residual) {
      // Compact A rows of each chunk, as activation_read gives them for the compact path.
      std::size_t elements = 0;
      for (std::uint64_t offset = 0; offset < loop.residual.activation_packed_bytes; offset += dim) {
        const auto read = residual_activation_read(plan, loop, offset);
        assert(read.valid && read.element_count <= dim);
        if (read.element_count) {
          assert(read.byte_offset / plan.residual.layout.activation_stride < m_r);
          const auto column = read.byte_offset % plan.residual.layout.activation_stride;
          assert(column >= loop.residual.k &&
                 column + read.element_count <= loop.residual.k + loop.residual.ks);
        }
        elements += read.element_count;
      }
      assert(elements == m_r * loop.residual.ks);
      assert(!residual_activation_read(plan, loop, loop.residual.activation_packed_bytes).valid);
    } else {
      assert(!residual_activation_read(plan, loop, 0).valid);
    }
    std::set<std::size_t> ids;
    for (const auto &entry : work_ids) assert(ids.insert(entry.second).second);
    const auto output = residual_output_extent(plan, loop);
    assert(output.valid == (loop.has_residual && loop.residual.final_contribution));
    if (output.valid) assert(output.rows == m_r && output.byte_offset == loop.residual.j * 4);
    last = loop.main.last;
    advance_paired_loop(plan, loop, cursor);
  } while (!last);
  assert(cursor.residual_done);
  assert(main_work == (plan.row_end - plan.row_begin) * n * k);
  assert(residual_work == m_r * n * runs.compact_k);
  return strides;
}

struct Case {
  std::string name;
  std::size_t dim, m, n, k;
  Tile tile;
  std::vector<std::uint32_t> masks;
  std::vector<std::pair<std::size_t, bool>> expect;  // (M_R, pairs at 1x)
};

void run_case(const Case &c) {
  const auto runs = make_runs(c.k, c.masks);
  const auto config = main_config(c.dim, c.m, c.n, c.k, c.tile);
  std::cout << "paired " << c.name;
  for (const auto &[m_r, expected] : c.expect) {
    for (const auto &[capacity, is_compat] :
         {std::pair{compat(c.dim), true}, std::pair{physical(c.dim), false}}) {
      const Residual residual{m_r, runs.compact_k, &runs.view, runs.compact_k, c.n * 4};
      PairPlan plan;
      assert(plan_stripe(config, 0, c.m, residual, capacity, plan));
      const bool rule = fit_rule(c.dim, c.m, c.n, c.k, c.tile, m_r, runs, capacity);
      assert(plan.paired == rule);
      if (is_compat) assert(plan.paired == expected);
      if (!plan.paired) {
        assert(plan.main.tile.tile_k == c.tile.k);
        continue;
      }
      assert(plan.main.tile.tile_k == snapped_tile_k(c.tile.k, c.dim));
      check_invariants(plan, m_r, runs);
    }
    std::cout << " MR" << m_r << (expected ? ":pair" : ":fallback");
  }
  std::cout << " PASS\n";
}

// Partial last j-tile: indexed-W stride is the loop's jp, not tile_j * D.
void partial_j_tile() {
  const auto runs = make_runs(96, {0, 0x00f0f000u, 0xffff0fffu});
  const auto config = main_config(16, 80, 96, 96, {5, 5, 6});
  PairPlan plan;
  assert(plan_stripe(config, 0, 80, {16, runs.compact_k, &runs.view, runs.compact_k, 96 * 4},
                     compat(16), plan) && plan.paired);
  const auto strides = check_invariants(plan, 16, runs);
  assert(strides.size() == 2 && strides.at(0) == std::set<std::size_t>{80} &&
         strides.at(80) == std::set<std::size_t>{16});
  std::cout << "paired A8D16 M80 N96 K96 tile(5,5,6) w_fragment_stride 80/16 PASS\n";
}

// One inactive block, one run with <= 16 active K, one with > 16.
void block_chunks() {
  const auto runs = make_runs(96, {0, 0x00f0f000u, 0xffff0fffu});
  for (std::size_t dim : {16, 64}) {
    const auto config = main_config(dim, dim, dim, 96, {1, 1, dim == 16 ? std::size_t{6} : std::size_t{2}});
    PairPlan plan;
    assert(plan_stripe(config, 0, dim, {1, runs.compact_k, &runs.view, runs.compact_k, dim * 4},
                       compat(dim), plan) && plan.paired);
    check_invariants(plan, 1, runs);
    std::vector<std::pair<std::uint32_t, std::size_t>> residual_loops;
    auto cursor = first_cursor(plan);
    bool last = false;
    do {
      const auto loop = plan_paired_loop(plan, cursor);
      if (loop.has_residual)
        residual_loops.emplace_back(loop.residual.original_block_id, loop.residual.fragment_count);
      last = loop.main.last;
      advance_paired_loop(plan, loop, cursor);
    } while (!last);
    // At D16 the 28-bit run needs two 16-row chunks; at D64 every run is one chunk.
    assert((residual_loops == std::vector<std::pair<std::uint32_t, std::size_t>>{
                                  {1, 1}, {2, dim == 16 ? std::size_t{2} : std::size_t{1}}}));
  }
  std::cout << "paired blocks K96 inactive/1-chunk/2-chunk D16 D64 PASS\n";
}

std::size_t main_loops(const ScheduleConfig &config) {
  LoopCursor cursor{};
  std::size_t loops = 0;
  bool last = false;
  do {
    const auto loop = plan_loop(config, config.shape.m, cursor);
    advance_loop(config, loop, cursor);
    ++loops;
    last = loop.last;
  } while (!last);
  return loops;
}

void k32_snap() {
  assert(snapped_tile_k(51, 16) == 50 && snapped_tile_k(1, 16) == 2 && snapped_tile_k(2, 16) == 2);
  assert(snapped_tile_k(51, 32) == 51 && snapped_tile_k(1, 64) == 1);
  // Pair-off splits the K32 block at every odd 816-row tile boundary.
  for (const auto &[k, unsnapped, snapped] :
       {std::tuple<std::size_t, std::size_t, std::size_t>{2048, 65, 64}, {3072, 98, 96}, {8192, 261, 256}}) {
    assert(main_loops(main_config(16, 16, 16, k, {1, 1, 51})) == unsnapped);
    assert(main_loops(main_config(16, 16, 16, k, {1, 1, 50})) == snapped);
  }
  std::vector<std::uint32_t> masks(96, 0);
  masks[0] = 1;
  masks[25] = 0xffffffffu;  // the block pair-off splits at 816
  masks[95] = 0x80000001u;
  const auto runs = make_runs(3072, masks);
  for (std::size_t tile_k : {51, 1}) {
    PairPlan plan;
    assert(plan_stripe(main_config(16, 16, 16, 3072, {1, 1, tile_k}), 0, 16,
                       {1, runs.compact_k, &runs.view, runs.compact_k, 64}, compat(16), plan) &&
           plan.paired && plan.main.tile.tile_k == (tile_k == 51 ? 50 : 2));
    check_invariants(plan, 1, runs);
  }
  std::cout << "paired K32 snap A8D16 tile_k 51->50 1->2 loops 65/98/261 -> 64/96/256 PASS\n";
}

// Descriptor fields per loop for the RTL: the N=96 case's first and last j-tile loops.
void descriptor_fields() {
  assert(capacity_from_hardware(3072, 256, 16384).acc_rows_per_slot == 1536);
  assert(capacity_from_hardware(3072, 256, 16384).work_ids_per_slot == 128);
  assert(capacity_from_hardware(512, 128, 4096).sp_half_rows == 2048);
  const auto runs = make_runs(96, {0, 0x00f0f000u, 0xffff0fffu});
  PairPlan plan;
  assert(plan_stripe(main_config(16, 80, 96, 96, {5, 5, 6}), 0, 80,
                     {17, runs.compact_k, &runs.view, runs.compact_k, 96 * 4}, physical(16), plan) &&
         plan.paired);
  std::vector<PairFields> seen;
  auto cursor = first_cursor(plan);
  bool last = false;
  do {
    const auto loop = plan_paired_loop(plan, cursor);
    const auto fields = pair_fields(plan, loop);
    assert(fields.paired == loop.has_residual);
    if (fields.paired) seen.push_back(fields);
    last = loop.main.last;
    advance_paired_loop(plan, loop, cursor);
  } while (!last);
  // Two j-tiles x two runs; M_R 17 = two groups, the last missing 15 rows.
  assert(seen.size() == 4);
  for (std::size_t index = 0; index < seen.size(); ++index) {
    const auto &f = seen[index];
    const bool first = index % 2 == 0;
    assert(f.mask == (first ? 0x00f0f000u : 0xffff0fffu));
    assert(f.compact_begin == (first ? 0u : 8u));
    assert(f.groups == 2 && f.pad_i == 15 && f.acc_top == 1536 && f.work_offset == 25);
    assert(f.first_run == first && f.final_run == !first);
  }
  PairPlan off;
  assert(plan_stripe(main_config(16, 80, 96, 96, {5, 5, 6}), 0, 80, {}, physical(16), off));
  assert(!pair_fields(off, plan_paired_loop(off, first_cursor(off))).paired);
  std::cout << "paired descriptor fields and hardware capacity PASS\n";
}

void invalid_inputs() {
  const auto runs = make_runs(96, {0, 0x00f0f000u, 0xffff0fffu});
  const auto config = main_config(16, 80, 16, 96, {5, 1, 6});
  PairPlan plan;
  // No residual: Main unchanged, nothing paired.
  assert(plan_stripe(config, 0, 80, {}, compat(16), plan) && !plan.paired &&
         plan.main.tile.tile_k == 6);
  auto wrong_k = runs;
  wrong_k.view.original_k = 64;
  assert(!plan_stripe(config, 0, 80, {1, runs.compact_k, &wrong_k.view, runs.compact_k, 64},
                      compat(16), plan));
  assert(!plan_stripe(config, 0, 80, {1, runs.compact_k + 1, &runs.view, runs.compact_k, 64},
                      compat(16), plan));
  assert(!plan_stripe(config, 0, 80, {1, runs.compact_k, nullptr, runs.compact_k, 64},
                      compat(16), plan));
  auto raw = config;
  raw.backing_scales = false;
  assert(!plan_stripe(raw, 0, 80, {1, runs.compact_k, &runs.view, runs.compact_k, 64},
                      compat(16), plan));
  assert(!plan_stripe(config, 0, 81, {}, compat(16), plan));
  std::cout << "paired invalid residual and stripe rejected PASS\n";
}

} // namespace

int main() {
  const std::vector<std::uint32_t> k96 = {0, 0x00f0f000u, 0xffff0fffu};
  std::vector<std::uint32_t> k1024(32, 0), k128 = {0xffffffffu, 0, 0x00ff00ffu, 0};
  k1024[0] = 1;
  k1024[3] = 0xffffffffu;
  k1024[31] = 0x80000001u;
  // Main tiles not marked pinned are the selector's choice for that shape.
  const std::vector<Case> cases = {
      // (5+27)*16 = 512 <= 512; (5+28)*16 = 528 > 512.
      {"A8D16 M80 N16 K96 tile(5,1,6)", 16, 80, 16, 96, {5, 1, 6}, k96,
       {{1, true}, {16, true}, {17, true}, {432, true}, {433, false}}},
      // (5+2)*80 = 560 > 512.
      {"A8D16 M80 N80 K96 tile(5,5,6)", 16, 80, 80, 96, {5, 5, 6}, k96,
       {{1, true}, {16, true}, {17, false}}},
      // Pinned; the selector would pick (5,6,6). Last j-tile is partial (jp 16).
      {"A8D16 M80 N96 K96 tile(5,5,6)", 16, 80, 96, 96, {5, 5, 6}, k96,
       {{1, true}, {16, true}, {17, false}}},
      // Pinned; three Main i-tiles in the stripe.
      {"A8D16 M80 N80 K96 tile(2,5,6)", 16, 80, 80, 96, {2, 5, 6}, k96,
       {{1, false}, {16, false}, {432, false}}},
      {"A8D64 M64 N256 K1024 tile(1,1,16)", 64, 64, 256, 1024, {1, 1, 16}, k1024,
       {{1, true}, {64, true}, {65, false}}},
      // Main fills the 128-row 1x slot (2*64).
      {"A8D64 M64 N256 K128 tile(1,2,2)", 64, 64, 256, 128, {1, 2, 2}, k128,
       {{1, false}, {64, false}}},
      {"A8D64 M64 N64 K128 tile(1,1,2)", 64, 64, 64, 128, {1, 1, 2}, k128,
       {{1, true}, {64, true}}},
  };
  for (const auto &c : cases) run_case(c);
  partial_j_tile();
  block_chunks();
  k32_snap();
  descriptor_fields();
  invalid_inputs();
}
