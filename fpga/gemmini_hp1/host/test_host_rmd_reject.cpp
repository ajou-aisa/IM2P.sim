#include "ggml-gemmini-args.h"
#include "ggml-gemmini-fpga.hpp"

#include <algorithm>
#include <array>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <vector>

extern "C" void gemmini_log_debug_layer(const char *, const char *, ...) noexcept {}

namespace {
using namespace im2p::gemmini_hp1;

struct RunProbe {
  const im2p_compact_run_t *borrowed_runs = nullptr;
  std::vector<std::size_t> scale_rows;
  std::size_t weight_reads = 0;
  std::size_t run_calls = 0;
  std::size_t scu_calls = 0;
  std::size_t output_writes = 0;
  bool owned_runs = false;
  bool run_payload = false;
  bool full_output = false;
};

int read_weight(void *opaque, std::size_t, std::size_t, std::size_t count,
                std::int8_t *out) {
  auto &probe = *static_cast<RunProbe *>(opaque);
  ++probe.weight_reads;
  std::fill_n(out, count, std::int8_t{1});
  return IM2P_OK;
}

int read_scale(void *opaque, std::size_t row, std::size_t,
               std::size_t count, std::uint32_t *out) {
  auto &probe = *static_cast<RunProbe *>(opaque);
  probe.scale_rows.push_back(row);
  std::fill_n(out, count, static_cast<std::uint32_t>(row + 3));
  return IM2P_OK;
}

int write_output(void *opaque, std::size_t block, std::size_t row,
                 std::size_t column, std::size_t count,
                 const std::int64_t *values, std::uint32_t domain) {
  auto &probe = *static_cast<RunProbe *>(opaque);
  ++probe.output_writes;
  const std::array<std::int64_t, 4> expected{11, 12, 13, 14};
  probe.full_output = block == 0 && row == 0 && column == 0 && count == 4 &&
                      domain == IM2P_OUTPUT_SCU_FINAL &&
                      std::equal(expected.begin(), expected.end(), values);
  return probe.full_output ? IM2P_OK : IM2P_ERROR;
}

int execute_runs(void *opaque, const RmdRunWork &work,
                 std::vector<std::int32_t> &values, std::uint64_t &cycles) {
  auto &probe = *static_cast<RunProbe *>(opaque);
  ++probe.run_calls;
  probe.owned_runs = work.runs.data() != probe.borrowed_runs;
  probe.run_payload =
      work.plan.m == 2 && work.plan.n == 2 && work.plan.k == 40 &&
      work.plan.kind == WorkKind::dense_hp1_final && work.original_k == 80 &&
      work.runs.size() == 2 && work.runs[0].original_block_id == 0 &&
      work.runs[1].original_block_id == 2 &&
      work.carriers == std::vector<std::uint32_t>({3, 3, 4, 4});
  values = {11, 12, 13, 14};
  cycles = 17;
  return probe.owned_runs && probe.run_payload ? IM2P_OK : IM2P_ERROR;
}

int execute_scu(void *opaque, const RmdScuWork &,
                std::vector<std::int32_t> &, std::uint64_t &) {
  ++static_cast<RunProbe *>(opaque)->scu_calls;
  return IM2P_OK;
}

void run_descriptor_contract() {
  const auto capability = ggml_gemmini_hp1_capability();
  std::vector<std::int8_t> activations(2 * 40, 1);
  const std::array<im2p_compact_run_t, 2> run_entries{{
      {0, 0x00ffffffU, 0, 24},
      {2, 0x0000ffffU, 24, 16},
  }};
  const im2p_compact_runs_t runs{IM2P_COMPACT_RUNS_VERSION, sizeof(runs), 80,
                                 run_entries.size(), run_entries.data()};
  RunProbe probe;
  probe.borrowed_runs = run_entries.data();
  im2p_matmul_desc_t descriptor{};
  descriptor.abi_version = IM2P_ABI_VERSION;
  descriptor.activation_bits = capability.activation_bits;
  descriptor.activation_storage_bytes = 1;
  descriptor.weight_bits = capability.weight_bits;
  descriptor.weight_storage_bytes = 1;
  descriptor.dim = capability.dim;
  descriptor.activations = activations.data();
  descriptor.m = 2;
  descriptor.n = 2;
  descriptor.k = 40;
  descriptor.activation_row_stride_bytes = descriptor.k;
  descriptor.weight_row_stride_bytes = descriptor.n;
  descriptor.output_row_stride = descriptor.n;
  descriptor.tile_i_rows = descriptor.m;
  descriptor.tile_j_columns = descriptor.n;
  descriptor.block_size = 32;
  descriptor.scale_total_k = runs.original_k;
  descriptor.scale_row_stride = descriptor.n;
  descriptor.scale_valid_columns = descriptor.n;
  descriptor.scale_values_len = runs.run_count * descriptor.n;
  descriptor.vector_op = IM2P_VECTOR_LEFT_SHIFT;
  descriptor.output_domain = IM2P_OUTPUT_SCU_FINAL;
  descriptor.provider = {&probe, read_weight, nullptr, read_scale,
                         write_output};
  const im2p_production_geometry_v1_t geometry{
      IM2P_PRODUCTION_GEOMETRY_VERSION,
      sizeof(geometry),
      capability.activation_bits,
      capability.weight_bits,
      capability.dim,
      IM2P_GEOMETRY_FULL,
      descriptor.m,
      descriptor.n,
      descriptor.k,
      1,
      1,
      3,
      descriptor.m,
      0,
      descriptor.m,
      0};
  RmdExecutorContext executor{capability, &probe, nullptr, 0, 1, execute_scu,
                              execute_runs};
  im2p_work_stats_extended_t stats{};
  if (execute_rmd_runs_descriptor(&executor, &descriptor, &geometry, &runs,
                                  &stats) != IM2P_OK ||
      !probe.owned_runs || !probe.run_payload || !probe.full_output ||
      probe.scale_rows != std::vector<std::size_t>({0, 1}) ||
      probe.weight_reads != descriptor.k || probe.run_calls != 1 ||
      probe.output_writes != 1 || stats.base.work_total_cycles != 17 ||
      stats.base.output_write_requests != 1 ||
      stats.base.output_write_responses != 1)
    throw std::runtime_error("run-aware descriptor contract failed");

  probe = {};
  executor.context = &probe;
  executor.execute_runs = nullptr;
  if (execute_rmd_runs_descriptor(&executor, &descriptor, &geometry, &runs,
                                  nullptr) != IM2P_INVALID_LAYOUT ||
      probe.weight_reads || probe.run_calls || probe.output_writes)
    throw std::runtime_error("missing run capability reached provider output");

  probe = {};
  executor.context = &probe;
  auto legacy = descriptor;
  legacy.scale_total_k = legacy.k;
  legacy.scale_values_len = legacy.n;
  if (execute_rmd_scu_descriptor(&executor, &legacy, &geometry, nullptr) !=
          IM2P_INVALID_LAYOUT ||
      probe.scu_calls || probe.output_writes)
    throw std::runtime_error("legacy SCU accepted K greater than 32");
}
}

int main() {
#if defined(_WIN32)
  _putenv_s("IM2P_FPGA_DEVICE", "");
#else
  unsetenv("IM2P_FPGA_DEVICE");
#endif
  ggml_gemmini_args_t args{};
  const auto capability = ggml_gemmini_hp1_capability();
  run_descriptor_contract();
  if (!capability.rmd) throw std::runtime_error("RMD capability missing");
  ggml_gemmini_hp1_executor incomplete{};
  incomplete.capability = capability;
  if (ggml_gemmini_hp1_bind_executor(incomplete))
    throw std::runtime_error("incomplete RMD executor accepted");
  bool quantized = false;
  if (ggml_gemmini_fpga_execute(
          args, false, [&] { quantized = true; }, "host-rmd-reject") ||
      quantized ||
      ggml_gemmini_fpga_last_error() != "GEMMINI_HP1 requires a native HP1 block-scaled work item") {
    throw std::runtime_error("invalid RMD work reached dispatch");
  }
  std::cout << "GEMMINI_HP1_RMD_INVALID_DISPATCH_REJECT_PASS\n";
}
