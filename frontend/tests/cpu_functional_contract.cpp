#include "im2p_cpu_functional.hpp"
#include "im2p_sim.h"
#include <array>
#include <cassert>
#include <cstring>
#include <iostream>
#include <memory>
#include <vector>

struct Timing {
  std::array<unsigned, 3> begin{}, end{};
  static void observe(void *p, const char *stage, bool start) noexcept {
    auto &self = *static_cast<Timing *>(p);
    const size_t index = std::strcmp(stage, "functional.materialize") == 0 ? 0
                         : std::strcmp(stage, "functional.matmul") == 0    ? 1
                                                                           : 2;
    ++(start ? self.begin : self.end)[index];
  }
};

struct RunFixture {
  std::array<uint32_t, 2> carriers{0, 1};
  std::array<int64_t, 2> output{-99, -99};
  std::array<unsigned, 2> reads{};
  unsigned writes = 0;
  bool fail_scale = false;
  bool weight_mode = false;
};

static int run_weight(void *context, size_t row, size_t column, size_t count,
                      int8_t *out) {
  if (row >= 22 || column || count != 1) return -1;
  const int8_t weights[7] = {-3, 0, 3, -1, 2, -2, 1};
  *out = static_cast<RunFixture *>(context)->weight_mode ? weights[row % 7]
                                                         : 1;
  return 0;
}
static int run_scale(void *context, size_t run, size_t column, size_t count,
                     uint32_t *out) {
  auto &fixture = *static_cast<RunFixture *>(context);
  if (run >= 2 || column || count != 1 ||
      (fixture.fail_scale && run == 1)) return -1;
  ++fixture.reads[run];
  *out = fixture.carriers[run];
  return 0;
}
static int run_output(void *context, size_t block, size_t row, size_t column,
                      size_t count, const int64_t *values, uint32_t domain) {
  auto &fixture = *static_cast<RunFixture *>(context);
  if (block || row || column || count != 2 || domain != IM2P_OUTPUT_SCU_FINAL)
    return -1;
  std::copy_n(values, count, fixture.output.begin());
  ++fixture.writes;
  return 0;
}

int main() {
  constexpr size_t m = 3, n = 2, k = 31;
  std::vector<int8_t> a(m * k, 1), weights(k * n, 1);
  std::vector<uint32_t> scale{0, 31};
  std::vector<int32_t> output(m * n, -99);
  im2p_matmul_desc_t d{};
  d.abi_version = 5;
  d.activation_bits = im2p_sim_activation_bits();
  d.weight_bits = im2p_sim_weight_bits();
  d.activation_storage_bytes = d.weight_storage_bytes = 1;
  d.dim = im2p_sim_dim();
  d.m = m;
  d.n = n;
  d.k = k;
  d.activations = a.data();
  d.weights = weights.data();
  d.scales = scale.data();
  d.output = output.data();
  d.activation_row_stride_bytes = k;
  d.weight_row_stride_bytes = n;
  d.output_row_stride = n;
  d.tile_i_rows = m;
  d.tile_j_columns = n;
  d.block_size = 32;
  d.scale_total_k = k;
  d.scale_row_stride = n;
  d.scale_valid_columns = n;
  d.scale_values_len = n;
  d.vector_op = IM2P_VECTOR_LEFT_SHIFT;
  d.output_domain = IM2P_OUTPUT_SCU_FINAL;
  im2p_production_geometry_v1_t g{1,
                                  sizeof(g),
                                  d.activation_bits,
                                  d.weight_bits,
                                  d.dim,
                                  IM2P_GEOMETRY_FULL,
                                  m,
                                  n,
                                  k,
                                  1,
                                  1,
                                  2,
                                  1,
                                  0,
                                  m,
                                  0};
  std::unique_ptr<im2p_sim_t, decltype(&im2p_sim_destroy)> sim(
      im2p_sim_create(), im2p_sim_destroy);
  assert(sim);
  assert(std::strcmp(im2p_sim_implementation(), "CPU_FUNCTIONAL") == 0);
  assert(im2p_sim_abi_version() == 5 && im2p_compiled_accumulator_bits() == 32);
  assert(im2p_compiled_partial_bits() == 32 &&
         im2p_compiled_accumulator_rows() > 0);
  assert(im2p_execute_matmul_extended(sim.get(), &d, nullptr) ==
         IM2P_INVALID_LAYOUT);
  auto malformed = g;
  malformed.tile_k_count = 0;
  assert(im2p_execute_matmul_planned(sim.get(), &d, &malformed, nullptr) ==
         IM2P_INVALID_LAYOUT);
  assert(output.front() == -99);
  scale[1] = 0x80000001u;
  assert(im2p_execute_matmul_planned(sim.get(), &d, &g, nullptr) ==
         IM2P_INVALID_LAYOUT);
  assert(output.front() == -99);
  scale[1] = 31;
  Timing timing;
  {
    im2p::cpu_functional::TimingRegistration registration(
        {Timing::observe, &timing});
    im2p_work_stats_extended_t stats{};
    assert(im2p_execute_matmul_planned(sim.get(), &d, &g, &stats) == IM2P_OK);
    assert(stats.base.work_total_cycles == 0);
  }
  assert((timing.begin == std::array<unsigned, 3>{1, 1, 1}) &&
         timing.end == timing.begin);
  for (size_t row = 0; row < m; ++row)
    assert(output[row * n] == 31 && output[row * n + 1] == INT32_MAX);
  assert(im2p_execute_matmul_planned(sim.get(), &d, &g, nullptr) == IM2P_OK);
  assert((timing.begin == std::array<unsigned, 3>{1, 1, 1}));
  for (size_t row = 0; row < m; ++row)
    std::fill(a.begin() + row * k + 16, a.begin() + (row + 1) * k, -1);
  assert(im2p_execute_matmul_planned(sim.get(), &d, &g, nullptr) == IM2P_OK);
  assert(output[0] == 1);
  assert(output[1] == (d.dim == 16 ? -1 : INT32_MAX));
  std::fill(a.begin(), a.end(), 1);

  im2p_stripe_work_desc_t s{};
#define COPY(field) s.field = d.field
  COPY(abi_version);
  COPY(activation_bits);
  COPY(activation_storage_bytes);
  COPY(weight_bits);
  COPY(weight_storage_bytes);
  COPY(dim);
  COPY(weights);
  COPY(scales);
  COPY(output);
  COPY(m);
  COPY(n);
  COPY(k);
  COPY(weight_row_stride_bytes);
  COPY(output_row_stride);
  COPY(tile_i_rows);
  COPY(tile_j_columns);
  COPY(block_size);
  COPY(scale_total_k);
  COPY(scale_row_stride);
  COPY(scale_valid_columns);
  COPY(scale_values_len);
  COPY(vector_op);
  COPY(output_domain);
#undef COPY
  s.stripe_count = m;
  g.scope = IM2P_GEOMETRY_STREAM;
  im2p_stream_t *raw = nullptr;
  assert(im2p_begin_striped_matmul_planned(sim.get(), &s, &g, &raw) == IM2P_OK);
  std::unique_ptr<im2p_stream_t, decltype(&im2p_destroy_stream)> stream(
      raw, im2p_destroy_stream);
  assert(im2p_begin_striped_matmul_planned(sim.get(), &s, &g, &raw) ==
         IM2P_UNFINISHED_STREAM);
  assert(im2p_finish_stream_extended(stream.get(), nullptr) ==
         IM2P_UNFINISHED_STREAM);
  sim.reset();
  std::fill(output.begin(), output.end(), -99);
  auto stripe_g = g;
  stripe_g.scope = IM2P_GEOMETRY_STRIPE;
  stripe_g.row_count = 1;
  im2p_activation_stripe_t stripe{
      5, d.activation_bits, 1, d.weight_bits, 1, d.dim, 0, 0, 1, a.data(), k,
      17};
  assert(im2p_publish_stripe_planned(stream.get(), &stripe, &stripe_g) ==
         IM2P_OK);
  assert(im2p_publish_stripe_planned(stream.get(), &stripe, &stripe_g) ==
         IM2P_DUPLICATE_STRIPE);
  stripe.stripe_id = stripe.i_start = stripe_g.stripe_id = stripe_g.row_begin =
      1;
  stripe.activations = a.data() + k;
  assert(im2p_publish_stripe_planned(stream.get(), &stripe, &stripe_g) ==
         IM2P_OK);
  stripe.stripe_id = stripe.i_start = stripe_g.stripe_id = stripe_g.row_begin =
      2;
  stripe.activations = a.data() + 2 * k;
  assert(im2p_publish_stripe_planned(stream.get(), &stripe, &stripe_g) ==
         IM2P_BACKPRESSURE);
  assert(output[4] == -99);
  im2p_stripe_completion_extended_t completion{};
  assert(im2p_poll_completed_extended(stream.get(), &completion) == 1);
  assert(completion.base.stripe_id == 0 && completion.base.context == 17 &&
         completion.completion_cycle == 0);
  assert(im2p_publish_stripe_planned(stream.get(), &stripe, &stripe_g) ==
         IM2P_OK);
  im2p_work_stats_extended_t stats{};
  assert(im2p_finish_stream_extended(stream.get(), &stats) == IM2P_OK);
  assert(stats.base.stripes_published == m &&
         stats.base.stripe_rows_published == m);
  assert(im2p_publish_stripe_planned(stream.get(), &stripe, &stripe_g) ==
         IM2P_LATE_STRIPE);
  assert(output[4] == 31 && output[5] == INT32_MAX);

  std::array<int8_t, 64> compact_a{};
  compact_a.fill(1);
  RunFixture run_fixture;
  auto run_d = d;
  run_d.m = 2;
  run_d.n = 1;
  run_d.k = 22;
  run_d.activations = compact_a.data();
  run_d.weights = nullptr;
  run_d.scales = nullptr;
  run_d.output = nullptr;
  run_d.activation_row_stride_bytes = 22;
  run_d.weight_row_stride_bytes = 1;
  run_d.output_row_stride = 1;
  run_d.provider = {&run_fixture, run_weight, nullptr, run_scale, run_output};
  auto run_g = g;
  run_g.scope = IM2P_GEOMETRY_FULL;
  run_g.m = run_g.row_count = 2;
  run_g.n = 1;
  run_g.k = 22;
  const im2p_compact_run_t run_entries[] = {{0, 0xfff, 0, 12},
                                            {3, 0x3ff, 12, 10}};
  im2p_compact_runs_t run_view{1, sizeof(run_view), 128, 2, run_entries};
  auto run_sim = std::unique_ptr<im2p_sim_t, decltype(&im2p_sim_destroy)>(
      im2p_sim_create(), im2p_sim_destroy);
  assert(im2p_execute_matmul_planned_runs(run_sim.get(), &run_d, &run_g,
                                          &run_view, nullptr) == IM2P_OK);
  assert((run_fixture.output == std::array<int64_t, 2>{32, 32}));
  assert(run_fixture.writes == 1 && run_fixture.reads[0] &&
         run_fixture.reads[1]);
  int32_t oracle_raw[2] = {0, 0};
  for (size_t position = 0; position < 22; ++position) {
    const int8_t value = position % 3 == 0 ? 2 : -1;
    compact_a[position] = value;
    compact_a[22 + position] = -value;
    oracle_raw[position < 12 ? 0 : 1] +=
        value * (static_cast<int32_t>(position * 3 % 7) - 3);
  }
  run_fixture.weight_mode = true;
  assert(im2p_execute_matmul_planned_runs(run_sim.get(), &run_d, &run_g,
                                          &run_view, nullptr) == IM2P_OK);
  const int32_t asymmetric_expected = oracle_raw[0] + 2 * oracle_raw[1];
  assert(asymmetric_expected == -11);
  assert((run_fixture.output ==
          std::array<int64_t, 2>{asymmetric_expected, -asymmetric_expected}));
  std::cout << "CPU_FUNCTIONAL_ORACLE asymmetric expected="
            << asymmetric_expected << "," << -asymmetric_expected
            << " actual=" << run_fixture.output[0] << ","
            << run_fixture.output[1] << "\n";
  compact_a.fill(1);
  run_fixture.weight_mode = false;
  run_fixture.carriers[1] = 0x80000000U;
  assert(im2p_execute_matmul_planned_runs(run_sim.get(), &run_d, &run_g,
                                          &run_view, nullptr) == IM2P_OK);
  assert((run_fixture.output == std::array<int64_t, 2>{12, 12}));
  run_fixture.carriers = {31, 31};
  assert(im2p_execute_matmul_planned_runs(run_sim.get(), &run_d, &run_g,
                                          &run_view, nullptr) == IM2P_OK);
  assert((run_fixture.output ==
          std::array<int64_t, 2>{INT32_MAX, INT32_MAX}));
  compact_a.fill(-1);
  assert(im2p_execute_matmul_planned_runs(run_sim.get(), &run_d, &run_g,
                                          &run_view, nullptr) == IM2P_OK);
  assert((run_fixture.output ==
          std::array<int64_t, 2>{INT32_MIN, INT32_MIN}));
  compact_a.fill(1);
  run_fixture.carriers = {0, 1};
  std::array<int8_t, 32> compact_weights{};
  compact_weights.fill(1);
  run_d.k = run_g.k = 32;
  run_d.activation_row_stride_bytes = 32;
  run_d.weights = compact_weights.data();
  run_d.provider.read_weight_i8 = nullptr;
  const im2p_compact_run_t gap_entries[] = {{0, 0x7fffffff, 0, 31},
                                            {3, 1, 31, 1}};
  run_view.runs = gap_entries;
  run_view.original_k = 128;
  assert(im2p_execute_matmul_planned_runs(run_sim.get(), &run_d, &run_g,
                                          &run_view, nullptr) == IM2P_OK);
  assert((run_fixture.output == std::array<int64_t, 2>{33, 33}));
  run_view.runs = run_entries;
  run_d.k = run_g.k = 22;
  run_d.weights = nullptr;
  run_d.provider.read_weight_i8 = run_weight;
  run_d.activation_row_stride_bytes = 22;
  run_fixture.output = {-99, -99};
  run_fixture.writes = 0;
  run_fixture.fail_scale = true;
  assert(im2p_execute_matmul_planned_runs(run_sim.get(), &run_d, &run_g,
                                          &run_view, nullptr) == IM2P_ERROR);
  assert((run_fixture.output == std::array<int64_t, 2>{-99, -99}));
  assert(run_fixture.writes == 0);
  std::array<int32_t, 2> direct_run_output{-99, -99};
  run_d.provider.write_output = nullptr;
  run_d.output = direct_run_output.data();
  assert(im2p_execute_matmul_planned_runs(run_sim.get(), &run_d, &run_g,
                                          &run_view, nullptr) == IM2P_ERROR);
  assert((direct_run_output == std::array<int32_t, 2>{-99, -99}));
  run_fixture.fail_scale = false;
  assert(im2p_execute_matmul_planned_runs(run_sim.get(), &run_d, &run_g,
                                          &run_view, nullptr) == IM2P_OK);
  assert((direct_run_output == std::array<int32_t, 2>{32, 32}));
  const im2p_compact_run_t malformed_runs[] = {{0, 0xfff, 0, 12},
                                               {3, 0x3ff, 13, 10}};
  run_view.runs = malformed_runs;
  direct_run_output = {-99, -99};
  assert(im2p_execute_matmul_planned_runs(run_sim.get(), &run_d, &run_g,
                                          &run_view, nullptr) ==
         IM2P_INVALID_LAYOUT);
  assert((direct_run_output == std::array<int32_t, 2>{-99, -99}));
  std::cout << "CPU_FUNCTIONAL_CONTRACT_PASS\n";
}
