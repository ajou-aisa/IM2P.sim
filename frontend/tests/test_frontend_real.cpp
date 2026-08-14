#define IM2P_GEMMINI_FRONTEND_TESTING 1
#include "im2p_gemmini_frontend.hpp"

#include "ggml-gemmini-args.h"
#include "quants/act/exsia/exsia.hpp"

#include <cstdio>
#include <vector>

using namespace im2p::gemmini;
namespace exsia = ggml::gemmini::quants::act::exsia;

#ifndef IM2P_GEMMINI_FRONTEND_EXPECTED_DIM
#error "real frontend test requires an explicit authoritative DIM config"
#endif
static_assert(DIM == IM2P_GEMMINI_FRONTEND_EXPECTED_DIM);

namespace {
ggml_gemmini_args_t make_args(std::vector<int8_t> &a, std::vector<int8_t> &b,
                              std::vector<int32_t> &c) {
  ggml_gemmini_args_t x{};
  x.I = 2;
  x.J = 2;
  x.K = 3;
  x.A = a.data();
  x.B = b.data();
  x.C = c.data();
  x.sA = 3;
  x.sB = 2;
  x.sC = 2;
  x.full_C = true;
  x.transpose_B = false;
  x.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_h0;
  x.activation_rows_per_stripe = 1;
  return x;
}

exsia::StripeReadyEvent stripe(size_t id) {
  exsia::StripeReadyEvent e{};
  e.run_id = 41;
  e.stripe_id = id;
  e.row_begin = id;
  e.row_end = id + 1;
  return e;
}

bool golden(const std::vector<int32_t> &c) {
  return c == std::vector<int32_t>({4, 5, 10, 11});
}
} // namespace

int main() {
  if (DIM != IM2P_GEMMINI_FRONTEND_EXPECTED_DIM) {
    std::fprintf(stderr, "FAIL: frontend DIM mismatch\n");
    return 4;
  }
  std::vector<int8_t> a = {1, 2, 3, 4, 5, 6};
  std::vector<int8_t> b = {1, 0, 0, 1, 1, 1};
  std::vector<int32_t> full_c(4);
  auto full_args = make_args(a, b, full_c);
  auto full = execute(&full_args);
  if (!full.status.ok() || !fence(*full.run).status.ok() || !golden(full_c)) {
    std::fprintf(stderr, "FAIL: real full golden\n");
    return 1;
  }

  std::vector<int32_t> stripe_c(4);
  auto stripe_args = make_args(a, b, stripe_c);
  auto run = execute(&stripe_args, Mode::stripe_pipeline, {2, 1000000});
  if (!run.status.ok() || !submit_stripe(*run.run, stripe(0)).ok() ||
      !submit_stripe(*run.run, stripe(1)).ok()) {
    std::fprintf(stderr, "FAIL: real stripe submission\n");
    return 2;
  }
  const auto done = fence(*run.run);
  const auto &s = done.stats;
  if (!done.status.ok() || !golden(stripe_c) || s.lookahead_prepared == 0 ||
      s.lookahead_publish_cycle == 0 ||
      s.lookahead_publish_cycle > s.lookahead_first_activation_cycle ||
      s.lookahead_publish_cycle > s.lookahead_first_weight_cycle ||
      s.lookahead_first_activation_cycle >= s.current_stripe_completion_cycle ||
      s.lookahead_first_weight_cycle >= s.current_stripe_completion_cycle) {
    std::fprintf(stderr, "FAIL: real stripe golden/lookahead: %s\n",
                 done.status.message);
    return 3;
  }
  // Regression for the proven long-running shape (~57572 RTL cycles). It uses
  // default frontend options and must not trip the logical-stall watchdog.
  constexpr size_t large_n = 64;
  constexpr size_t large_k = 4096;
  std::vector<int8_t> large_a(large_k, 0);
  std::vector<int8_t> large_b(large_k * large_n, 0);
  std::vector<int32_t> large_c(large_n);
  ggml_gemmini_args_t large{};
  large.I = 1;
  large.J = large_n;
  large.K = large_k;
  large.A = large_a.data();
  large.B = large_b.data();
  large.C = large_c.data();
  large.sA = large_k;
  large.sB = large_n;
  large.sC = large_n;
  large.full_C = true;
  large.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_h0;
  large.activation_rows_per_stripe = 1;
  auto long_run = execute(&large, Mode::stripe_pipeline);
  if (!long_run.status.ok() || !submit_stripe(*long_run.run, stripe(0)).ok() ||
      !fence(*long_run.run).status.ok()) {
    std::fprintf(stderr,
                 "FAIL: real long-running default-options regression\n");
    return 5;
  }

  std::printf("IM2P Gemmini frontend real RTL DIM=%d: PASS\n", DIM);
  return 0;
}
