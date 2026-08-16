#define IM2P_GEMMINI_FRONTEND_TESTING 1
#include "im2p_gemmini_frontend.hpp"

#include "ggml-gemmini-args.h"
#include "quants/act/exsia/exsia.hpp"

#include <cmath>
#include <cstdio>
#include <cstring>
#include <vector>

using namespace im2p::gemmini;
namespace exsia = ggml::gemmini::quants::act::exsia;

#ifndef IM2P_GEMMINI_FRONTEND_EXPECTED_DIM
#error "real frontend test requires an explicit authoritative DIM config"
#endif
static_assert(DIM == IM2P_GEMMINI_FRONTEND_EXPECTED_DIM);

namespace {
constexpr float kGuard = 12345.0f;

exsia::StripeReadyEvent stripe(size_t id, size_t begin, size_t end) {
  exsia::StripeReadyEvent e{};
  e.run_id = 41; e.stripe_id = id; e.row_begin = begin; e.row_end = end;
  return e;
}

struct NativeCase {
  size_t m = DIM + 3, n = DIM + 5, k, blocks_k;
  size_t row_stride = 0, col_stride = 2;
  std::vector<int8_t> a, logical_w, planar;
  std::vector<uint8_t> cb, channel_rows;
  std::vector<uint16_t> r;
  std::vector<float> srf, channel_scales, out, expected;
  std::vector<block_q8_h1> h1;
  std::vector<block_q8_hp1> hp1;
  ggml_gemmini_args_t args{};

  explicit NativeCase(ggml_gemmini_args_t::im2p_weight_format_t format)
      : k(format == ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1 ? 64 : 65),
        blocks_k((k + 31) / 32) {
    row_stride = n * col_stride + 3;
    a.resize(m * k); logical_w.resize(n * k); planar.resize(n * k);
    cb.resize(n * blocks_k); r.resize(n); srf.resize(n);
    channel_scales.resize(n); channel_rows.resize(n * (sizeof(float) + k));
    h1.resize(n * blocks_k); hp1.resize(n * blocks_k);
    out.assign(m * row_stride + 7, kGuard);
    expected.assign(m * n, 0.0f);
    for (size_t i = 0; i < m; ++i)
      for (size_t x = 0; x < k; ++x) a[i * k + x] = int8_t((i + 2 * x) % 7 - 3);
    for (size_t j = 0; j < n; ++j) {
      r[j] = uint16_t(2 + j % 3); srf[j] = 0.125f * float(1 + j % 4);
      channel_scales[j] = 0.25f * float(1 + j % 3);
      std::memcpy(channel_rows.data() + j * (sizeof(float) + k), &channel_scales[j], sizeof(float));
      for (size_t x = 0; x < k; ++x) {
        const int8_t q = int8_t((3 * j + x) % 9 - 4);
        logical_w[j * k + x] = q; planar[j * k + x] = q;
        channel_rows[j * (sizeof(float) + k) + sizeof(float) + x] = uint8_t(q);
      }
      for (size_t b = 0; b < blocks_k; ++b) {
        cb[j * blocks_k + b] = uint8_t(1 + b + j % 2);
        auto &hb = h1[j * blocks_k + b];
        auto &pb = hp1[j * blocks_k + b];
        const size_t begin = b * 32;
        const size_t count = std::min<size_t>(32, k - begin);
        std::memcpy(hb.qs, logical_w.data() + j * k + begin, count);
        std::memcpy(pb.qs, logical_w.data() + j * k + begin, count);
        hb.c_b = cb[j * blocks_k + b]; hb.R = r[j]; hb.s_rf = srf[j];
        pb.channel_scale = 0.125f * float(1 + j % 4); pb.m = int16_t(b + 1);
      }
    }
    args.I = m; args.J = n; args.K = k; args.A = a.data(); args.sA = k;
    args.f_out = out.data(); args.stride_f_out = row_stride;
    args.col_stride_f_out = col_stride; args.activation_rows_per_stripe = DIM / 2;
    args.weight_format = format;
    args.act_quant.storage().emplace<ggml::gemmini::quants::act::tensor::Meta>().scale = 0.5f;
    switch (format) {
    case ggml_gemmini_args_t::im2p_weight_format_t::q8_0_unpacked_to_h1:
      args.B = planar.data(); args.sB = k; args.c_b = cb.data(); args.s_rf = srf.data();
      args.R = r.data(); args.blocks_per_row = blocks_k; break;
    case ggml_gemmini_args_t::im2p_weight_format_t::q8_h1:
      args.q8_h1_blocks = h1.data(); args.q8_h1_block_count = h1.size();
      args.q8_h1_rows = n; args.blocks_per_row = blocks_k; break;
    case ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1:
      args.q8_hp1_blocks = hp1.data(); args.q8_hp1_block_count = hp1.size();
      args.q8_hp1_blocks_per_row = blocks_k; break;
    case ggml_gemmini_args_t::im2p_weight_format_t::q8_channel:
      args.q8_channel_row_base = channel_rows.data();
      args.q8_channel_row_stride = sizeof(float) + k; args.q8_channel_row_count = n;
      args.B = reinterpret_cast<elem_t *>(channel_rows.data() + sizeof(float));
      args.sB = sizeof(float) + k; break;
    case ggml_gemmini_args_t::im2p_weight_format_t::q8_channel_dense_sidecar:
      args.B = logical_w.data(); args.sB = k; args.weight_channel_scales = channel_scales.data();
      args.weight_channel_scale_count = n; break;
    default: break;
    }
    for (size_t i = 0; i < m; ++i) for (size_t j = 0; j < n; ++j) {
      double sum = 0.0;
      for (size_t b = 0; b < blocks_k; ++b) {
        int32_t dot = 0;
        for (size_t x = b * 32; x < std::min(k, (b + 1) * 32); ++x)
          dot += int32_t(a[i * k + x]) * int32_t(logical_w[j * k + x]);
        double factor = channel_scales[j];
        if (format == ggml_gemmini_args_t::im2p_weight_format_t::q8_0_unpacked_to_h1 ||
            format == ggml_gemmini_args_t::im2p_weight_format_t::q8_h1)
          factor = double(srf[j]) * double(uint32_t(cb[j * blocks_k + b]) + uint32_t(r[j]));
        else if (format == ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1)
          factor = double(gemmini_ldexp_fast_pos(hp1[j * blocks_k + b].channel_scale,
                                                hp1[j * blocks_k + b].m));
        sum += double(dot) * factor;
      }
      expected[i * n + j] = float(sum * 0.5);
    }
  }
};

struct NativeResult {
  std::vector<float> output;
  im2p_work_stats_extended_t stats{};
};

bool check_native(ggml_gemmini_args_t::im2p_weight_format_t format, Mode mode,
                  NativeResult *result = nullptr) {
  NativeCase c(format);
  const size_t expected_stripes =
      (c.m + size_t(DIM / 2) - 1) / size_t(DIM / 2);
  auto run = execute(&c.args, mode, {expected_stripes, 1000000});
  if (!run.status.ok()) {
    std::fprintf(stderr, "native start failed route=%d mode=%d: %s\n",
                 int(format), int(mode), run.status.message);
    return false;
  }
  if (mode == Mode::stripe_pipeline) {
    size_t id = 0;
    for (size_t row = 0; row < c.m; row += DIM / 2, ++id)
      if (const auto submitted =
              submit_stripe(*run.run,
                            stripe(id, row, std::min(c.m, row + size_t(DIM / 2))));
          !submitted.ok()) {
        std::fprintf(stderr, "native submit failed route=%d stripe=%zu: %s\n",
                     int(format), id, submitted.message);
        return false;
      }
  }
  auto done = fence(*run.run);
  if (!done.status.ok()) { std::fprintf(stderr, "native fence failed route=%u mode=%u: %s\n", unsigned(format), unsigned(mode), done.status.message); return false; }
  for (size_t i = 0; i < c.m; ++i) for (size_t j = 0; j < c.n; ++j)
    if (std::fabs(c.out[i * c.row_stride + j * c.col_stride] - c.expected[i * c.n + j]) > 1e-5f) {
      std::fprintf(stderr, "native mismatch route=%u mode=%u i=%zu j=%zu got=%g want=%g\n",
                   unsigned(format), unsigned(mode), i, j,
                   c.out[i * c.row_stride + j * c.col_stride], c.expected[i * c.n + j]); return false;
    }
  for (size_t i = 0; i < c.m; ++i) for (size_t x = 0; x < c.row_stride; ++x)
    if (x % c.col_stride != 0 && c.out[i * c.row_stride + x] != kGuard) return false;
  if (mode == Mode::stripe_pipeline &&
      (done.stats.lookahead_prepared == 0 ||
       done.stats.base.completed_stripes != expected_stripes ||
       done.stats.lookahead_first_activation_cycle >=
           done.stats.current_stripe_completion_cycle ||
       done.stats.lookahead_first_weight_cycle >=
           done.stats.current_stripe_completion_cycle ||
       done.stats.lookahead_weight_preload_cycle >=
           done.stats.current_stripe_completion_cycle ||
       done.stats.lookahead_ready_cycle >=
           done.stats.current_stripe_completion_cycle ||
       done.stats.lookahead_start_cycle <
           done.stats.current_stripe_completion_cycle)) return false;
  const bool channel =
      format == ggml_gemmini_args_t::im2p_weight_format_t::q8_channel ||
      format == ggml_gemmini_args_t::im2p_weight_format_t::q8_channel_dense_sidecar;
  if (done.stats.base.weight_read_requests == 0 ||
      done.stats.base.output_write_requests == 0 ||
      (channel ? done.stats.base.scale_read_requests != 0
               : done.stats.base.scale_read_requests == 0))
    return false;
  if (result) {
    result->output.reserve(c.m * c.n);
    for (size_t i = 0; i < c.m; ++i)
      for (size_t j = 0; j < c.n; ++j)
        result->output.push_back(c.out[i * c.row_stride + j * c.col_stride]);
    result->stats = done.stats;
  }
  return true;
}

bool check_raw_h0(Mode mode) {
  const size_t m = DIM + 1, n = DIM + 3, k = DIM + 5;
  std::vector<int8_t> a(m * k), b(k * n);
  std::vector<int32_t> out(m * n), expected(m * n);
  for (size_t i = 0; i < m; ++i)
    for (size_t x = 0; x < k; ++x)
      a[i * k + x] = int8_t((i + 2 * x) % 7 - 3);
  for (size_t x = 0; x < k; ++x)
    for (size_t j = 0; j < n; ++j)
      b[x * n + j] = int8_t((x + 3 * j) % 9 - 4);
  for (size_t i = 0; i < m; ++i)
    for (size_t j = 0; j < n; ++j)
      for (size_t x = 0; x < k; ++x)
        expected[i * n + j] += int32_t(a[i * k + x]) * int32_t(b[x * n + j]);

  ggml_gemmini_args_t args{};
  args.I = m; args.J = n; args.K = k;
  args.A = a.data(); args.B = b.data(); args.C = out.data();
  args.sA = k; args.sB = n; args.sC = n; args.full_C = true;
  args.activation_rows_per_stripe = DIM / 2;
  args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_h0;
  const size_t expected_stripes =
      (m + size_t(DIM / 2) - 1) / size_t(DIM / 2);
  auto run = execute(&args, mode, {expected_stripes, 1000000});
  if (!run.status.ok()) {
    std::fprintf(stderr, "q8_h0 start failed mode=%d: %s\n", int(mode),
                 run.status.message);
    return false;
  }
  if (mode == Mode::stripe_pipeline) {
    size_t id = 0;
    for (size_t row = 0; row < m; row += DIM / 2, ++id)
      if (!submit_stripe(*run.run,
                         stripe(id, row, std::min(m, row + size_t(DIM / 2)))).ok())
        return false;
  }
  const auto done = fence(*run.run);
  if (!done.status.ok()) {
    std::fprintf(stderr, "q8_h0 fence failed mode=%d: %s\n", int(mode),
                 done.status.message);
    return false;
  }
  for (size_t index = 0; index < out.size(); ++index)
    if (out[index] != expected[index]) {
      std::fprintf(stderr,
                   "q8_h0 mismatch mode=%d i=%zu j=%zu got=%d want=%d\n",
                   int(mode), index / n, index % n, out[index], expected[index]);
      return false;
    }
  if (done.stats.base.weight_read_requests == 0 ||
      done.stats.base.output_write_requests == 0 ||
      done.stats.base.scale_read_requests != 0) {
    std::fprintf(stderr, "q8_h0 request stats invalid mode=%d W=%llu S=%llu C=%llu\n",
                 int(mode),
                 static_cast<unsigned long long>(
                     done.stats.base.weight_read_requests),
                 static_cast<unsigned long long>(
                     done.stats.base.scale_read_requests),
                 static_cast<unsigned long long>(
                     done.stats.base.output_write_requests));
    return false;
  }
  return true;
}
}

int main() {
  if (!check_raw_h0(Mode::full) || !check_raw_h0(Mode::stripe_pipeline)) return 1;
  NativeResult q8_0_full, q8_0_stripe, h1_full, h1_stripe;
  NativeResult channel_full, channel_stripe, sidecar_full, sidecar_stripe;
  const ggml_gemmini_args_t::im2p_weight_format_t routes[] = {
    ggml_gemmini_args_t::im2p_weight_format_t::q8_channel,
    ggml_gemmini_args_t::im2p_weight_format_t::q8_0_unpacked_to_h1,
    ggml_gemmini_args_t::im2p_weight_format_t::q8_h1,
    ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1,
    ggml_gemmini_args_t::im2p_weight_format_t::q8_channel_dense_sidecar,
  };
  for (auto route : routes) {
    NativeResult *full = nullptr, *stripe_result = nullptr;
    switch (route) {
    case ggml_gemmini_args_t::im2p_weight_format_t::q8_0_unpacked_to_h1:
      full = &q8_0_full; stripe_result = &q8_0_stripe; break;
    case ggml_gemmini_args_t::im2p_weight_format_t::q8_h1:
      full = &h1_full; stripe_result = &h1_stripe; break;
    case ggml_gemmini_args_t::im2p_weight_format_t::q8_channel:
      full = &channel_full; stripe_result = &channel_stripe; break;
    case ggml_gemmini_args_t::im2p_weight_format_t::q8_channel_dense_sidecar:
      full = &sidecar_full; stripe_result = &sidecar_stripe; break;
    default: break;
    }
    if (!check_native(route, Mode::full, full) ||
        !check_native(route, Mode::stripe_pipeline, stripe_result))
      return 2;
  }
  if (q8_0_full.output != h1_full.output ||
      q8_0_stripe.output != h1_stripe.output ||
      channel_full.output != sidecar_full.output ||
      channel_stripe.output != sidecar_stripe.output ||
      q8_0_full.stats.base.work_total_cycles !=
          h1_full.stats.base.work_total_cycles ||
      q8_0_stripe.stats.base.work_total_cycles !=
          h1_stripe.stats.base.work_total_cycles ||
      channel_full.stats.base.work_total_cycles !=
          sidecar_full.stats.base.work_total_cycles ||
      channel_stripe.stats.base.work_total_cycles !=
          sidecar_stripe.stats.base.work_total_cycles)
    return 5;
  ggml_gemmini_args_t rejected{};
  rejected.weight_format=ggml_gemmini_args_t::im2p_weight_format_t::q8_h2;
  if (execute(&rejected).status.code != StatusCode::unsupported_route) return 3;
  rejected.weight_format=ggml_gemmini_args_t::im2p_weight_format_t::q8_hp2;
  if (execute(&rejected).status.code != StatusCode::unsupported_route) return 4;
  std::printf("IM2P Gemmini frontend real RTL DIM=%d: PASS\n", DIM);
  return 0;
}
