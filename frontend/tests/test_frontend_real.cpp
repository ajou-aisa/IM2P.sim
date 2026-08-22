#define IM2P_GEMMINI_FRONTEND_TESTING 1
#include "im2p_gemmini_frontend.hpp"

#include "ggml-gemmini-args.h"
#include "quants/act/exsia/exsia.hpp"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <memory>
#include <string_view>
#include <vector>

using namespace im2p::gemmini;
namespace exsia = ggml::gemmini::quants::act::exsia;

#ifndef IM2P_GEMMINI_FRONTEND_EXPECTED_DIM
#error "real frontend test requires an explicit authoritative DIM config"
#endif
#ifndef IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS
#error "real frontend test requires an explicit activation width"
#endif
static_assert(DIM == IM2P_GEMMINI_FRONTEND_EXPECTED_DIM);
static_assert(IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS == 4 ||
              IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS == 8 ||
              IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS == 16);

namespace {
constexpr int32_t kGuard = 0x5a5a5a5a;
constexpr size_t kWeightOrigin = 7;
constexpr size_t kOutputOrigin = 5;
constexpr uint64_t kRunId = 41;

size_t activation_storage_bytes() {
  return (IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS + 7) / 8;
}

int32_t activation_min() {
  return -(int32_t{1} << (IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS - 1));
}

int32_t activation_max() {
  return (int32_t{1} << (IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS - 1)) - 1;
}

exsia::StripeReadyEvent stripe(size_t id, size_t begin, size_t end) {
  exsia::StripeReadyEvent event{};
  event.run_id = kRunId;
  event.stripe_id = id;
  event.slot = id % 2;
  event.row_begin = begin;
  event.row_end = end;
  return event;
}

struct RealCase {
  const size_t m = DIM + 3;
  const size_t n = DIM + 5;
  const size_t k = DIM + 7;
  const size_t stripe_rows = (m + 2) / 3;
  const size_t activation_stride_bytes = (k + 3) * activation_storage_bytes();
  const size_t weight_stride = n + 5;
  const size_t output_stride = n + 4;

  ggml::gemmini::quants::act::QuantizedActivationBuffer activations;
  std::vector<int8_t> weight_storage;
  std::vector<int32_t> output_storage;
  std::vector<int32_t> expected;
  ggml_gemmini_args_t args{};

  RealCase()
      : weight_storage(kWeightOrigin + k * weight_stride + 9, int8_t{0x33}),
        output_storage(kOutputOrigin + m * output_stride + 11, kGuard),
        expected(m * n, 0) {
    activations.bits = IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS;
    activations.rows = m;
    activations.cols = k;
    activations.row_stride_bytes = activation_stride_bytes;
    activations.bytes = std::make_shared<std::vector<uint8_t>>(
        m * activation_stride_bytes + 13, uint8_t{0xa5});

    for (size_t i = 0; i < m; ++i) {
      for (size_t x = 0; x < k; ++x) {
        int32_t value = static_cast<int32_t>((i * 11 + x * 7) % 13) - 6;
        if (i == 0 && x == 0)
          value = activation_min();
        else if (i == 0 && x == 1)
          value = activation_max();
        if (!activations.set(i, x, value)) {
          std::fprintf(stderr,
                       "failed to set activation i=%zu k=%zu value=%d\n", i, x,
                       value);
          std::abort();
        }
      }
    }

    auto *weights = weight_storage.data() + kWeightOrigin;
    for (size_t x = 0; x < k; ++x) {
      for (size_t j = 0; j < n; ++j) {
        int32_t value = static_cast<int32_t>((x * 5 + j * 3) % 17) - 8;
        if (x == 0 && j == 0)
          value = -128;
        else if (x == 1 && j == 0)
          value = 127;
        weights[x * weight_stride + j] = static_cast<int8_t>(value);
      }
    }

    for (size_t i = 0; i < m; ++i) {
      for (size_t j = 0; j < n; ++j) {
        int64_t sum = 0;
        for (size_t x = 0; x < k; ++x)
          sum += int64_t(activations.get(i, x)) *
                 int64_t(weights[x * weight_stride + j]);
        if (sum < std::numeric_limits<int32_t>::min() ||
            sum > std::numeric_limits<int32_t>::max())
          std::abort();
        expected[i * n + j] = static_cast<int32_t>(sum);
      }
    }

    args.I = m;
    args.J = n;
    args.K = k;
    args.A = activations;
    args.B = weights;
    args.C = output_storage.data() + kOutputOrigin;
    args.sA = k;
    args.sB = weight_stride;
    args.sC = output_stride;
    args.full_C = true;
    args.activation_rows_per_stripe = stripe_rows;
    args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_h0;
  }

  bool verify_layout_contract() const {
    const size_t bytes = activation_storage_bytes();
    if (activations.row_stride_bytes != (k + 3) * bytes)
      return false;
    if (IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS == 4 && bytes != 1) {
      std::fprintf(stderr, "A4 must use one host byte per value\n");
      return false;
    }
    if (IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS == 16 && bytes != 2) {
      std::fprintf(stderr, "A16 must use two bytes per value\n");
      return false;
    }
    return activations.get(0, 0) == activation_min() &&
           activations.get(0, 1) == activation_max();
  }

  bool verify_output() const {
    const auto *output = output_storage.data() + kOutputOrigin;
    for (size_t i = 0; i < m; ++i) {
      for (size_t j = 0; j < n; ++j) {
        if (output[i * output_stride + j] != expected[i * n + j]) {
          std::fprintf(stderr,
                       "oracle mismatch bits=%d dim=%d i=%zu j=%zu got=%d "
                       "want=%d\n",
                       IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS, DIM, i, j,
                       output[i * output_stride + j], expected[i * n + j]);
          return false;
        }
      }
      for (size_t j = n; j < output_stride; ++j)
        if (output[i * output_stride + j] != kGuard)
          return false;
    }
    return std::all_of(output_storage.begin(),
                       output_storage.begin() + kOutputOrigin,
                       [](int32_t value) { return value == kGuard; }) &&
           std::all_of(output_storage.begin() + kOutputOrigin +
                           m * output_stride,
                       output_storage.end(),
                       [](int32_t value) { return value == kGuard; });
  }
};

bool run_real(Mode mode) {
  RealCase test;
  if (!test.verify_layout_contract())
    return false;

  auto started = execute(&test.args, mode, Options{1000000});
  if (!started.status.ok()) {
    std::fprintf(stderr, "execute failed bits=%d dim=%d mode=%d: %s\n",
                 IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS, DIM, int(mode),
                 started.status.message);
    return false;
  }

  size_t submitted = 0;
  if (mode == Mode::stripe_pipeline) {
    for (size_t row = 0; row < test.m; row += test.stripe_rows, ++submitted) {
      const auto status = submit_stripe(
          *started.run,
          stripe(submitted, row, std::min(test.m, row + test.stripe_rows)));
      if (!status.ok()) {
        std::fprintf(stderr, "stripe %zu failed: %s\n", submitted,
                     status.message);
        return false;
      }
    }
    if (submitted != 3) {
      std::fprintf(stderr, "expected three stripes, got %zu\n", submitted);
      return false;
    }
  }

  const auto done = fence(*started.run);
  if (!done.status.ok()) {
    std::fprintf(stderr, "fence failed bits=%d dim=%d mode=%d: %s\n",
                 IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS, DIM, int(mode),
                 done.status.message);
    return false;
  }
  if (mode == Mode::stripe_pipeline &&
      !authorize_output_commit(*started.run, true).ok())
    return false;
  if (!test.verify_output())
    return false;
  if (done.stats.base.activation_read_requests == 0 ||
      done.stats.base.weight_read_requests == 0 ||
      done.stats.base.output_write_requests == 0 ||
      done.stats.base.scale_read_requests != 0)
    return false;
  if (mode == Mode::stripe_pipeline &&
      (done.stats.base.completed_stripes != 3 ||
       done.stats.base.stripes_published != 3))
    return false;

  std::printf("REAL_EXECUTION bits=%d dim=%d mode=%s PASS "
              "M=%zu N=%zu K=%zu activation_byte_stride=%zu "
              "weight_origin=%zu output_origin=%zu stripes=%zu\n",
              IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS, DIM,
              mode == Mode::full ? "full" : "stripe", test.m, test.n, test.k,
              test.activation_stride_bytes, kWeightOrigin, kOutputOrigin,
              mode == Mode::stripe_pipeline ? submitted : 0);
  return true;
}

bool expect_configuration_mismatch() {
  RealCase test;
  const auto before = test.output_storage;
  auto started = execute(&test.args, Mode::full, Options{1000000});
  if (!started.status.ok()) {
    std::fprintf(stderr, "mismatch execute setup unexpectedly failed: %s\n",
                 started.status.message);
    return false;
  }
  const auto done = fence(*started.run);
  const auto &stats = done.stats.base;
  const bool no_work =
      stats.work_total_cycles == 0 && stats.activation_read_requests == 0 &&
      stats.weight_read_requests == 0 && stats.output_write_requests == 0;
  const bool pass =
      done.status.code == StatusCode::invalid_contract &&
      im2p_sim_activation_bits() != IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS &&
      test.output_storage == before && no_work;
  std::printf("CONFIGURATION_MISMATCH frontend_bits=%d simulator_bits=%u "
              "dim=%d no_rtl_work=%s output_unchanged=%s %s\n",
              IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS, im2p_sim_activation_bits(),
              DIM, no_work ? "yes" : "no",
              test.output_storage == before ? "yes" : "no",
              pass ? "PASS" : "FAIL");
  return pass;
}
} // namespace

int main(int argc, char **argv) {
  if (argc == 2 &&
      std::string_view(argv[1]) == "--expect-configuration-mismatch")
    return expect_configuration_mismatch() ? 0 : 3;
  if (argc != 1) {
    std::fprintf(stderr, "usage: %s [--expect-configuration-mismatch]\n",
                 argv[0]);
    return 64;
  }
  if (!run_real(Mode::full) || !run_real(Mode::stripe_pipeline))
    return 1;
  std::printf("IM2P Gemmini frontend real RTL bits=%d DIM=%d: PASS\n",
              IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS, DIM);
  return 0;
}
