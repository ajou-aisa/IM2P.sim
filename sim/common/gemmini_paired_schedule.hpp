#pragma once

#include <cstddef>
#include <cstdint>

#include "gemmini_schedule.hpp"

// Value-free pairing of one stripe's compact residual GEMM with Main's K32 loops
// (paired microtile, loop-tail order). Composes the public gemmini_schedule API
// only; gemmini_schedule.{hpp,cpp} stay as they are. No values or timing here.
namespace im2p::gemmini::paired {

// Per-slot resources Main and residual share. P2 drives the compat (1x) bounds
// that geometry_fits applies; the physical (P1) table is used for invariants only.
struct PairCapacity {
  std::size_t acc_rows_per_slot = 0, work_ids_per_slot = 0;
  std::size_t sp_half_rows = 0, scale_rows_per_slot = 0;
};

// One stripe's compact residual. rows == 0 means no residual (Main unchanged).
struct Residual {
  std::size_t rows = 0, compact_k = 0;
  const im2p_compact_runs_t *runs = nullptr;
  std::uint64_t activation_stride = 0, output_stride = 0;
};

// Fit-rule terms: I_M, I_R, J, max_k and max_b chunks_b.
struct FitFacts {
  std::size_t main_groups = 0, residual_groups = 0;
  std::size_t j_tiles = 0, max_k = 0, max_chunks = 0;
};

struct PairPlan {
  bool paired = false;
  FitFacts fit{};
  PairCapacity capacity{};
  // tile_k is K32-snapped when paired; a fallback stripe keeps the caller's tile_k.
  ScheduleConfig main{};
  // Compact residual config (tile_i = I_R, Main's tile_j, tile_k = fpb); paired only.
  ScheduleConfig residual{};
  std::size_t row_begin = 0, row_end = 0;
};

struct PairCursor {
  LoopCursor main{}, residual{};
  bool residual_done = true;
};

// One Main loop and the residual loop of the same (j-tile, block), if that run exists.
struct PairedLoop {
  LoopPlan main{}, residual{};
  bool has_residual = false;
  std::size_t run_index = 0;
  std::size_t micro_count = 0;
};

enum class MicroKind : std::uint8_t { main, residual };

struct PairMicroContext {
  MicroKind kind = MicroKind::main;
  FragmentPlan fragment{};
  // fragment_id is the compact path's block * fpb + chunk for residual µTs (C4).
  std::size_t fragment_id = 0, output_index = 0, scale_index = 0;
  // Slot-relative: Main grows low->high from the slot base, residual high->low from the top.
  std::size_t acc_row = 0;
  // Main in [0, I_M*J), residual in [I_M*J, (I_M+I_R)*J).
  std::size_t work_id = 0;
  std::size_t j_tile = 0;
  // SP rows between consecutive K fragments of the resident W tile: the Main loop's jp.
  std::size_t w_fragment_stride = 0;
};

struct OutputExtent {
  bool valid = false;
  std::size_t rows = 0, columns = 0;
  std::uint64_t byte_offset = 0;
};

// Pair descriptor fields of one loop as the HP1 top consumes them (Hp1LoopMetadata).
struct PairFields {
  bool paired = false;
  std::uint32_t mask = 0, compact_begin = 0, groups = 0, pad_i = 0;
  std::uint32_t acc_top = 0, work_offset = 0;
  bool first_run = false, final_run = false;
};

// max(fpb, floor(tile_k / fpb) * fpb) with fpb = max(1, 32 / dim).
std::size_t snapped_tile_k(std::size_t tile_k, std::size_t dim);
// false: invalid Main stripe or residual. Otherwise plan.paired says pair or fallback.
bool plan_stripe(const ScheduleConfig &main, std::size_t row_begin, std::size_t row_count,
                 const Residual &residual, const PairCapacity &capacity, PairPlan &plan);
PairCursor first_cursor(const PairPlan &plan);
PairedLoop plan_paired_loop(const PairPlan &plan, const PairCursor &cursor);
void advance_paired_loop(const PairPlan &plan, const PairedLoop &loop, PairCursor &cursor);
// Loop-tail order (C3): ordinals [0, main.fragment_count) are Main µTs in
// plan_fragment order, the rest are the residual µTs.
PairMicroContext micro_context(const PairPlan &plan, const PairedLoop &loop, std::size_t ordinal);
// SP row of reduction row `row` relative to the Main loop's W base:
// (local_k / D) * w_fragment_stride + j_tile * D + local_k % D, where a residual
// local_k is the original bit position inside the run's block.
std::size_t w_row(const PairPlan &plan, const PairedLoop &loop, const PairMicroContext &micro,
                  std::size_t row);
ReadExtent residual_activation_read(const PairPlan &plan, const PairedLoop &loop,
                                    std::uint64_t packed_offset);
// M_R x js int32 on the run that completes the j-tile; invalid otherwise.
OutputExtent residual_output_extent(const PairPlan &plan, const PairedLoop &loop);
// Per-slot capacity from the top's physical ACC rows and work entries and the SP rows.
PairCapacity capacity_from_hardware(std::size_t accumulator_rows, std::size_t work_entries,
                                    std::size_t sp_rows);
PairFields pair_fields(const PairPlan &plan, const PairedLoop &loop);

} // namespace im2p::gemmini::paired
