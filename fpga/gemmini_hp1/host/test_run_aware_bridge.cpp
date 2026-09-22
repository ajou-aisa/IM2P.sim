#include "../../../sim/backends/gemmini_hp1/runtime.hpp"

#include <array>
#include <cstdint>
#include <cstdio>
#include <stdexcept>

using namespace im2p::gemmini_hp1;

double sc_time_stamp() { return 0.0; }

namespace {
void check(bool yes, const char *why) {
  if (!yes)
    throw std::runtime_error(why);
}

void run(std::uint32_t second_block, std::uint32_t first_count,
         std::uint32_t second_count) {
  Runtime runtime;
  reset_state(runtime);
  clear_inputs(runtime);
  runtime.top.reset = 1;
  for (unsigned n = 0; n < 5; ++n)
    raw_clock(runtime);
  runtime.top.reset = 0;
  raw_clock(runtime);
  const auto k = first_count + second_count;
  im2p_matmul_descriptor_t d{};
  d.job_id = 91;
  d.mode = 0;
  d.activation_base = 0x1000;
  d.weight_base = 0x2000;
  d.scale_base = 0x3000;
  d.output_base = 0x4000;
  d.activation_row_stride = k;
  d.weight_row_stride = 1;
  d.scale_row_stride = 4;
  d.output_row_stride = 4;
  d.row_count = 1;
  d.column_count = 1;
  d.reduction_count = k;
  d.tile_i_rows = 1;
  d.tile_j_columns = 1;
  d.scale_total_k = second_block * 32 + second_count;
  d.scale_block_size = 32;
  d.vector_op = 5;
  im2p_production_geometry_v1_t g{};
  g.version = IM2P_PRODUCTION_GEOMETRY_VERSION;
  g.struct_size = sizeof(g);
  g.scope = IM2P_GEOMETRY_FULL;
  g.activation_bits = IM2P_ACTIVATION_BITS;
  g.weight_bits = IM2P_WEIGHT_BITS;
  g.dim = IM2P_DIM;
  g.m = g.row_count = g.stripe_rows = 1;
  g.n = 1;
  g.k = k;
  g.tile_i_count = g.tile_j_count = 1;
  g.tile_k_count = 2;
  std::array<im2p_compact_run_t, 2> runs{{
      {0, (std::uint32_t{1} << first_count) - 1, 0, first_count},
      {second_block, (std::uint32_t{1} << second_count) - 1, first_count,
       second_count},
  }};
  const im2p_compact_runs_t view{IM2P_COMPACT_RUNS_VERSION, sizeof(view),
                                 d.scale_total_k, runs.size(), runs.data()};
  auto invalid = view;
  invalid.original_k = 32;
  check(im2p_start_matmul_geometry_runs(&runtime, &d, &g, &invalid) ==
                IM2P_REQUEST_INVALID_ARGUMENT &&
            !runtime.active,
        "invalid run owner reached RTL admission");
  check(im2p_start_matmul_geometry_runs(&runtime, &d, &g, &view) == 1,
        "run bridge admission failed");
  runs[1].original_block_id = 99;
  check(runtime.schedule.runs[1].original_block_id == second_block,
        "run metadata was borrowed after admission");
  std::uint32_t scale_owners = 0;
  std::uint32_t writes = 0;
  std::int32_t raw = 0;
  for (unsigned n = 0; n < 200000 && !runtime.matrix_done && !runtime.fault;
       ++n) {
    if (runtime.read.active && !runtime.read.response) {
      const auto &read = runtime.read;
      if (read.kind == ReadKind::scale) {
        check(read.count == 1 && read.address >= d.scale_base &&
                  (read.address - d.scale_base) % d.scale_row_stride == 0,
              "scale read layout invalid");
        const auto owner = (read.address - d.scale_base) / d.scale_row_stride;
        check(owner == 0 || owner == second_block,
              "scale read lost original block owner");
        scale_owners |= owner == 0 ? 1U : 2U;
        const std::uint32_t carrier = owner == 0 ? 0 : 1;
        check(stage_read<ReadKind::scale>(&runtime, read.tag, &carrier, 1) == 1,
              "scale read response failed");
      } else {
        std::array<std::int8_t, kDim> ones{};
        ones.fill(1);
        const auto status = read.kind == ReadKind::activation
                                ? stage_read<ReadKind::activation>(
                                      &runtime, read.tag, ones.data(), read.count)
                                : stage_read<ReadKind::weight>(
                                      &runtime, read.tag, ones.data(), read.count);
        check(status == 1, "operand read response failed");
      }
    }
    if (runtime.write.active && !runtime.write.response) {
      check(runtime.write.address == d.output_base &&
                runtime.write.count == 1 && writes == 0,
            "output published more than once");
      raw = static_cast<std::int32_t>(runtime.write.values[0]);
      ++writes;
      runtime.write.response = true;
    }
    tick(runtime);
  }
  check(!runtime.fault && runtime.matrix_done && writes == 1 &&
            scale_owners == 3 &&
            raw == static_cast<std::int32_t>(first_count + 2 * second_count) &&
            runtime.counters.works_completed == 1 &&
            runtime.last_done > runtime.last_start,
        "run bridge did not complete one exact RTL work");
  std::printf("FIXTURE_ONLY bridge owners=0,%u raw=%d start=%llu done=%llu\n",
              second_block, raw,
              static_cast<unsigned long long>(runtime.last_start),
              static_cast<unsigned long long>(runtime.last_done));
  runtime.top.final();
}
}

int main() {
  try {
    run(1, 12, 10);
    run(3, 12, 10);
    run(1, 31, 1);
    return 0;
  } catch (const std::exception &error) {
    std::fprintf(stderr, "run bridge failed: %s\n", error.what());
    return 1;
  }
}
