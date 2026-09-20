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
  std::cout << "CPU_FUNCTIONAL_CONTRACT_PASS\n";
}
