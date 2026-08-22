#define IM2P_GEMMINI_FRONTEND_TESTING 1
#include "im2p_gemmini_frontend.hpp"

#include "ggml-gemmini-args.h"
#include "quants/act/exsia/exsia.hpp"

#include <algorithm>
#include <cmath>
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

[[maybe_unused]] bool run_legacy(Mode mode) {
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

  std::printf(
      "REAL_EXECUTION bits=%d dim=%d route=q8_h0 mode=%s PASS "
      "M=%zu N=%zu K=%zu activation_byte_stride=%zu "
      "weight_origin=%zu output_origin=%zu stripes=%zu "
      "activation_reads=%llu weight_reads=%llu output_writes=%llu "
      "completed=%llu published=%llu\n",
      IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS, DIM,
      mode == Mode::full ? "full" : "stripe", test.m, test.n, test.k,
      test.activation_stride_bytes, kWeightOrigin, kOutputOrigin,
      mode == Mode::stripe_pipeline ? submitted : 0,
      static_cast<unsigned long long>(done.stats.base.activation_read_requests),
      static_cast<unsigned long long>(done.stats.base.weight_read_requests),
      static_cast<unsigned long long>(done.stats.base.output_write_requests),
      static_cast<unsigned long long>(done.stats.base.completed_stripes),
      static_cast<unsigned long long>(done.stats.base.stripes_published));
  return true;
}

struct ProviderCase {
  const size_t m = DIM + 3;
  const size_t n = DIM + 5;
  const size_t k = DIM + 7;
  const size_t stripe_rows = (m + 2) / 3;
  const size_t blocks = (k + QK8_0 - 1) / QK8_0;
  ggml::gemmini::quants::act::QuantizedActivationBuffer activations;
  std::vector<block_q8_h1> weights;
  std::vector<float> output;
  std::vector<float> expected;
  ggml_gemmini_args_t args{};

  ProviderCase()
      : weights(n * blocks), output(m * n, 17.0f), expected(m * n, 0.0f) {
    if (!activations.allocate(m, k, IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS))
      std::abort();
    for (size_t i = 0; i < m; ++i)
      for (size_t x = 0; x < k; ++x)
        if (!activations.set(i, x,
                             static_cast<int32_t>((i * 7 + x * 3) % 11) - 5))
          std::abort();
    for (size_t j = 0; j < n; ++j) {
      for (size_t block = 0; block < blocks; ++block) {
        auto &weight = weights[j * blocks + block];
        weight.s_rf = block % 2 == 0 ? 0.25f : 0.5f;
        weight.c_b = static_cast<uint8_t>(1 + block);
        weight.R = 1;
        for (size_t lane = 0; lane < QK8_0; ++lane)
          weight.qs[lane] =
              static_cast<int8_t>((block * QK8_0 + lane + j * 5) % 13) - 6;
      }
    }
    for (size_t i = 0; i < m; ++i) {
      for (size_t j = 0; j < n; ++j) {
        double sum = 0.0;
        for (size_t x = 0; x < k; ++x) {
          const auto &weight = weights[j * blocks + x / QK8_0];
          const double factor = static_cast<double>(weight.s_rf) *
                                static_cast<double>(weight.c_b + weight.R);
          sum += static_cast<double>(activations.get(i, x)) *
                 static_cast<double>(weight.qs[x % QK8_0]) * factor * 0.5;
        }
        expected[i * n + j] = static_cast<float>(sum);
      }
    }
    args.I = m;
    args.J = n;
    args.K = k;
    args.A = activations;
    args.activation_rows_per_stripe = stripe_rows;
    args.f_out = output.data();
    args.stride_f_out = n;
    args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_h1;
    args.q8_h1_blocks = weights.data();
    args.q8_h1_block_count = weights.size();
    args.q8_h1_rows = n;
    args.blocks_per_row = blocks;
    auto &meta = args.act_quant.storage().emplace<exsia::Meta>();
    meta.theta.assign(3, -1);
  }
};

[[maybe_unused]] bool run_provider(Mode mode) {
  ProviderCase test;
  auto started = execute(&test.args, mode, Options{1000000});
  if (!started.status.ok()) {
    std::fprintf(stderr, "provider execute failed bits=%d dim=%d mode=%d: %s\n",
                 IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS, DIM, int(mode),
                 started.status.message);
    return false;
  }
  size_t submitted = 0;
  if (mode == Mode::stripe_pipeline) {
    for (size_t row = 0; row < test.m; row += test.stripe_rows, ++submitted) {
      const auto status = submit_stripe(
          *started.run,
          stripe(submitted, row, std::min(test.m, row + test.stripe_rows)),
          StripeMetadata{true, -1});
      if (!status.ok()) {
        std::fprintf(stderr, "provider stripe %zu failed: %s\n", submitted,
                     status.message);
        return false;
      }
    }
  }
  const auto done = fence(*started.run);
  if (!done.status.ok() ||
      (mode == Mode::stripe_pipeline &&
       !authorize_output_commit(*started.run, true).ok()) ||
      test.output != test.expected ||
      done.stats.base.activation_read_requests == 0 ||
      done.stats.base.weight_read_requests == 0 ||
      done.stats.base.output_write_requests == 0 ||
      done.stats.base.scale_read_requests == 0 ||
      (mode == Mode::stripe_pipeline &&
       (done.stats.base.completed_stripes != 3 ||
        done.stats.base.stripes_published != 3))) {
    std::fprintf(stderr,
                 "provider verification failed bits=%d dim=%d mode=%d "
                 "status=%s stripes=%zu\n",
                 IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS, DIM, int(mode),
                 done.status.message, submitted);
    return false;
  }
  std::printf(
      "REAL_EXECUTION bits=%d dim=%d route=q8_h1 mode=%s PASS "
      "M=%zu N=%zu K=%zu stripes=%zu activation_reads=%llu "
      "weight_reads=%llu scale_reads=%llu output_writes=%llu "
      "completed=%llu published=%llu\n",
      IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS, DIM,
      mode == Mode::full ? "full" : "stripe", test.m, test.n, test.k,
      mode == Mode::stripe_pipeline ? submitted : 0,
      static_cast<unsigned long long>(done.stats.base.activation_read_requests),
      static_cast<unsigned long long>(done.stats.base.weight_read_requests),
      static_cast<unsigned long long>(done.stats.base.scale_read_requests),
      static_cast<unsigned long long>(done.stats.base.output_write_requests),
      static_cast<unsigned long long>(done.stats.base.completed_stripes),
      static_cast<unsigned long long>(done.stats.base.stripes_published));
  return true;
}

#if GGML_GEMMINI_WEIGHT_BITS == 4 || GGML_GEMMINI_WEIGHT_BITS == 16
enum class MatchedFormat { h0, h1, hp1 };

#if GGML_GEMMINI_WEIGHT_BITS == 4
using NativeH0 = block_q4_h0;
using NativeH1 = block_q4_h1;
using NativeHp1 = block_q4_hp1;
#else
using NativeH0 = block_q16_h0;
using NativeH1 = block_q16_h1;
using NativeHp1 = block_q16_hp1;
#endif

const char *matched_format_name(MatchedFormat format) {
  switch (format) {
  case MatchedFormat::h0: return GGML_GEMMINI_WEIGHT_BITS == 4 ? "q4_h0" : "q16_h0";
  case MatchedFormat::h1: return GGML_GEMMINI_WEIGHT_BITS == 4 ? "q4_h1" : "q16_h1";
  case MatchedFormat::hp1: return GGML_GEMMINI_WEIGHT_BITS == 4 ? "q4_hp1" : "q16_hp1";
  }
  return "unknown";
}

struct MatchedProviderCase {
  const size_t m = DIM + 3;
  const size_t n = DIM + 5;
  const size_t k = 2 * size_t{32};
  const size_t blocks = k / 32;
  MatchedFormat format;
  ggml::gemmini::quants::act::QuantizedActivationBuffer activations;
  std::vector<NativeH0> h0;
  std::vector<NativeH1> h1;
  std::vector<NativeHp1> hp1;
  std::vector<float> output;
  std::vector<float> expected;
  ggml_gemmini_args_t args{};

  explicit MatchedProviderCase(MatchedFormat requested)
      : format(requested), h0(n * blocks), h1(n * blocks), hp1(n * blocks),
        output(m * n, 17.0f), expected(m * n, 0.0f) {
    if (!activations.allocate(m, k, IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS))
      std::abort();
    for (size_t i = 0; i < m; ++i)
      for (size_t x = 0; x < k; ++x)
        if (!activations.set(i, x,
                             static_cast<int32_t>((i * 7 + x * 3) % 11) - 5))
          std::abort();

    for (size_t j = 0; j < n; ++j) {
      for (size_t block = 0; block < blocks; ++block) {
        const size_t index = j * blocks + block;
        h0[index].d = block == 0 ? ggml_half{0x3400} : ggml_half{0x3800};
        h1[index].s_rf = block == 0 ? 0.125f : 0.25f;
        h1[index].c_b = static_cast<uint8_t>(block + 1);
        h1[index].R = 1;
        hp1[index].channel_scale = 0.25f;
        hp1[index].m = static_cast<int16_t>(block);
        for (size_t lane = 0; lane < 32; ++lane) {
#if GGML_GEMMINI_WEIGHT_BITS == 4
          const int8_t code = static_cast<int8_t>((block * 3 + lane + j * 5) % 16) - 8;
          const uint8_t nibble = static_cast<uint8_t>(code + 8);
          auto set_nibble = [lane, nibble](uint8_t *qs) {
            uint8_t &byte = qs[lane % 16];
            byte = lane < 16 ? static_cast<uint8_t>((byte & 0xf0) | nibble)
                             : static_cast<uint8_t>((byte & 0x0f) | (nibble << 4));
          };
          set_nibble(h0[index].qs);
          set_nibble(h1[index].qs);
          set_nibble(hp1[index].qs);
#else
          const int16_t code = static_cast<int16_t>(
              static_cast<int>((block * 19 + lane * 7 + j * 5) % 47) - 23);
          h0[index].qs[lane] = code;
          h1[index].qs[lane] = code;
          hp1[index].qs[lane] = code;
#endif
        }
      }
    }

    for (size_t i = 0; i < m; ++i) {
      for (size_t j = 0; j < n; ++j) {
        double sum = 0.0;
        for (size_t x = 0; x < k; ++x) {
          const size_t block = x / 32;
          const size_t index = j * blocks + block;
#if GGML_GEMMINI_WEIGHT_BITS == 4
          const uint8_t byte = format == MatchedFormat::h0
              ? h0[index].qs[(x % 32) % 16]
              : format == MatchedFormat::h1
                  ? h1[index].qs[(x % 32) % 16]
                  : hp1[index].qs[(x % 32) % 16];
          const size_t lane = x % 32;
          const int code = int(lane < 16 ? byte & 0x0f : byte >> 4) - 8;
#else
          const size_t lane = x % 32;
          const int code = format == MatchedFormat::h0 ? h0[index].qs[lane]
                           : format == MatchedFormat::h1 ? h1[index].qs[lane]
                                                        : hp1[index].qs[lane];
#endif
          const double factor = format == MatchedFormat::h0
              ? (block == 0 ? 0.25 : 0.5)
              : format == MatchedFormat::h1
                  ? static_cast<double>(h1[index].s_rf) *
                        static_cast<double>(h1[index].c_b + h1[index].R)
                  : std::ldexp(static_cast<double>(hp1[index].channel_scale),
                               hp1[index].m);
          sum += static_cast<double>(activations.get(i, x)) * code * factor * 0.5;
        }
        expected[i * n + j] = static_cast<float>(sum);
      }
    }

    args.I = m;
    args.J = n;
    args.K = k;
    args.A = activations;
    args.f_out = output.data();
    args.stride_f_out = n;
    args.native_block_count = n * blocks;
    args.native_blocks_per_row = blocks;
    switch (format) {
    case MatchedFormat::h0:
#if GGML_GEMMINI_WEIGHT_BITS == 4
      args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q4_h0;
      args.q4_h0_blocks = h0.data();
#else
      args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q16_h0;
      args.q16_h0_blocks = h0.data();
#endif
      break;
    case MatchedFormat::h1:
#if GGML_GEMMINI_WEIGHT_BITS == 4
      args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q4_h1;
      args.q4_h1_blocks = h1.data();
#else
      args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q16_h1;
      args.q16_h1_blocks = h1.data();
#endif
      break;
    case MatchedFormat::hp1:
#if GGML_GEMMINI_WEIGHT_BITS == 4
      args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q4_hp1;
      args.q4_hp1_blocks = hp1.data();
#else
      args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q16_hp1;
      args.q16_hp1_blocks = hp1.data();
#endif
      break;
    }
    args.act_quant.storage()
        .emplace<ggml::gemmini::quants::act::tensor::Meta>()
        .scale = 0.5f;
  }
};

bool run_matched_provider(MatchedFormat format, Mode mode) {
  MatchedProviderCase test(format);
  if (!test.args.has_native_matched_width_contract()) {
    std::fprintf(stderr, "invalid native fixture route=%s\n",
                 matched_format_name(format));
    return false;
  }
  test.args.activation_rows_per_stripe = (test.m + 2) / 3;
  auto started = execute(&test.args, mode, Options{1000000});
  if (!started.status.ok()) {
    std::fprintf(stderr, "matched execute failed route=%s mode=%s: %s\n",
                 matched_format_name(format),
                 mode == Mode::full ? "full" : "stripe",
                 started.status.message);
    return false;
  }
  size_t submitted = 0;
  if (mode == Mode::stripe_pipeline) {
    for (size_t row = 0; row < test.m;
         row += test.args.activation_rows_per_stripe, ++submitted) {
      const auto status = submit_stripe(
          *started.run,
          stripe(submitted, row,
                 std::min(test.m,
                          row + test.args.activation_rows_per_stripe)));
      if (!status.ok()) {
        std::fprintf(stderr, "matched stripe failed route=%s stripe=%zu: %s\n",
                     matched_format_name(format), submitted, status.message);
        return false;
      }
    }
  }
  const auto done = fence(*started.run);
  const bool staged = mode == Mode::stripe_pipeline &&
                      std::all_of(test.output.begin(), test.output.end(),
                                  [](float value) { return value == 17.0f; });
  if (mode == Mode::stripe_pipeline &&
      (!staged || !authorize_output_commit(*started.run, true).ok())) {
    std::fprintf(stderr, "matched pipeline authorization failed route=%s\n",
                 matched_format_name(format));
    return false;
  }
  if (!done.status.ok() || test.output != test.expected ||
      done.stats.base.activation_read_requests == 0 ||
      done.stats.base.weight_read_requests == 0 ||
      done.stats.base.scale_read_requests == 0 ||
      done.stats.base.output_write_requests == 0 ||
      (mode == Mode::stripe_pipeline &&
       (done.stats.base.completed_stripes != submitted ||
        done.stats.base.stripes_published != submitted))) {
    std::fprintf(stderr,
                 "matched verification failed route=%s mode=%s status=%s reads=%llu/%llu/%llu writes=%llu\n",
                 matched_format_name(format),
                 mode == Mode::full ? "full" : "stripe", done.status.message,
                 static_cast<unsigned long long>(done.stats.base.activation_read_requests),
                 static_cast<unsigned long long>(done.stats.base.weight_read_requests),
                 static_cast<unsigned long long>(done.stats.base.scale_read_requests),
                 static_cast<unsigned long long>(done.stats.base.output_write_requests));
    return false;
  }
  std::printf("REAL_EXECUTION activation_bits=%d weight_bits=%d dim=%d route=%s mode=%s PASS M=%zu N=%zu K=%zu blocks=%zu stripes=%zu\n",
              IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS, GGML_GEMMINI_WEIGHT_BITS,
              DIM, matched_format_name(format),
              mode == Mode::full ? "full" : "stripe", test.m, test.n, test.k,
              test.blocks, submitted);
  return true;
}
#endif

bool verify_compiled_identity() {
  const uint32_t expected_activation_storage =
      (IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS + 7) / 8;
  const uint32_t expected_weight_storage = (GGML_GEMMINI_WEIGHT_BITS + 7) / 8;
  const bool valid =
        im2p_sim_abi_version() == IM2P_ABI_VERSION &&
      im2p_sim_activation_bits() == IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS &&
      im2p_sim_activation_storage_bytes() == expected_activation_storage &&
      im2p_sim_weight_bits() == GGML_GEMMINI_WEIGHT_BITS &&
      im2p_sim_weight_storage_bytes() == expected_weight_storage &&
      im2p_sim_dim() == DIM;
  if (!valid) {
    std::fprintf(stderr,
                 "identity mismatch frontend=A%d/W%d/D%d simulator=ABI%u/A%u(%u)/W%u(%u)/D%u\n",
                 IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS,
                 GGML_GEMMINI_WEIGHT_BITS, DIM, im2p_sim_abi_version(),
                 im2p_sim_activation_bits(), im2p_sim_activation_storage_bytes(),
                 im2p_sim_weight_bits(), im2p_sim_weight_storage_bytes(),
                 im2p_sim_dim());
  }
  return valid;
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
  if (!verify_compiled_identity())
    return 2;
#if GGML_GEMMINI_WEIGHT_BITS == 4 || GGML_GEMMINI_WEIGHT_BITS == 16
  if (argc != 1) {
    std::fprintf(stderr, "matched-width real test takes no route override\n");
    return 64;
  }
  const bool passed =
      run_matched_provider(MatchedFormat::h0, Mode::full) &&
      run_matched_provider(MatchedFormat::h0, Mode::stripe_pipeline) &&
      run_matched_provider(MatchedFormat::h1, Mode::full) &&
      run_matched_provider(MatchedFormat::h1, Mode::stripe_pipeline) &&
      run_matched_provider(MatchedFormat::hp1, Mode::full) &&
      run_matched_provider(MatchedFormat::hp1, Mode::stripe_pipeline);
  if (!passed)
    return 1;
  std::printf("IM2P Gemmini frontend real RTL A%d/W%d/D%d matched routes: PASS\n",
              IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS,
              GGML_GEMMINI_WEIGHT_BITS, DIM);
  return 0;
#else
  bool provider = IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS == 8;
  if (argc == 3 && std::string_view(argv[1]) == "--route") {
    if (std::string_view(argv[2]) == "q8_h0")
      provider = false;
    else if (std::string_view(argv[2]) == "q8_h1")
      provider = true;
    else {
      std::fprintf(stderr, "unsupported route: %s\n", argv[2]);
      return 64;
    }
  } else if (argc != 1) {
    std::fprintf(
        stderr,
        "usage: %s [--route q8_h0|q8_h1|--expect-configuration-mismatch]\n",
        argv[0]);
    return 64;
  }
  const bool passed =
      provider ? run_provider(Mode::full) && run_provider(Mode::stripe_pipeline)
               : run_legacy(Mode::full) && run_legacy(Mode::stripe_pipeline);
  if (!passed)
    return 1;
  std::printf("IM2P Gemmini frontend real RTL bits=%d DIM=%d route=%s: PASS\n",
              IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS, DIM,
              provider ? "q8_h1" : "q8_h0");
  return 0;
#endif
}
