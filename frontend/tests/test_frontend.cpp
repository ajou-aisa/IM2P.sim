#define IM2P_GEMMINI_FRONTEND_TESTING 1
#include "im2p_gemmini_frontend.hpp"
#include "im2p_gemmini_frontend_testing.hpp"

#include "ggml-gemmini-args.h"
#include "quants/act/exsia/exsia.hpp"
#include "residual/rmd/rmd-types.hpp"

#include <im2p_sim.h>

#include <array>
#include <atomic>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <deque>
#include <limits>
#include <memory>
#include <mutex>
#include <string_view>
#include <thread>
#include <vector>

#if defined(__APPLE__)
#include <mach/mach.h>
#endif

using namespace im2p::gemmini;
namespace exsia = ggml::gemmini::quants::act::exsia;

namespace fake {
std::mutex mutex;
std::condition_variable changed;
bool hold_full = false;
bool full_entered = false;
bool allow_completion = true;
bool raw_pressure_once = false;
bool raw_pressure_forever = false;
bool fail_full = false;
bool fail_begin = false;
bool fail_publish = false;
bool fail_progress = false;
bool fail_poll = false;
bool fail_finish = false;
bool throw_create = false;
bool provider_force_callback_failure = false;
std::vector<int64_t> provider_exact_values;
std::vector<int64_t> provider_delivered_values;
std::atomic<size_t> sim_created{0};
std::atomic<size_t> sim_destroyed{0};
std::atomic<size_t> stream_created{0};
std::atomic<size_t> stream_destroyed{0};
bool hold_publish = false;
bool publish_entered = false;
size_t forward_progress_period = 0;
size_t required_progress_cycles = 1;
size_t progress_calls = 0;
size_t publish_attempts = 0;
size_t publish_count = 0;
size_t finish_count = 0;
std::thread::id owner;
bool one_owner = true;
im2p_matmul_desc_t full_desc{};
im2p_stripe_work_desc_t work_desc{};
size_t provider_full_count = 0;
size_t provider_work_count = 0;
std::vector<im2p_activation_stripe_t> published;

void abi_call() {
  const auto here = std::this_thread::get_id();
  one_owner = one_owner && (owner == std::thread::id{} || owner == here);
  owner = here;
}

void reset() {
  std::lock_guard lock(mutex);
  hold_full = false;
  full_entered = false;
  allow_completion = true;
  raw_pressure_once = false;
  raw_pressure_forever = false;
  fail_full = false;
  fail_begin = false;
  fail_publish = false;
  fail_progress = false;
  fail_poll = false;
  fail_finish = false;
  throw_create = false;
  provider_force_callback_failure = false;
  provider_exact_values.clear();
  provider_delivered_values.clear();
  sim_created = 0;
  sim_destroyed = 0;
  stream_created = 0;
  stream_destroyed = 0;
  hold_publish = false;
  publish_entered = false;
  forward_progress_period = 0;
  required_progress_cycles = 1;
  progress_calls = 0;
  publish_attempts = 0;
  publish_count = 0;
  finish_count = 0;
  owner = {};
  one_owner = true;
  full_desc = {};
  work_desc = {};
  provider_full_count = 0;
  provider_work_count = 0;
  published.clear();
}

template <class P> bool wait(P predicate) {
  std::unique_lock lock(mutex);
  return changed.wait_for(lock, std::chrono::seconds(5), predicate);
}

int32_t activation(const void *data, size_t stride, uint32_t bits, size_t row,
                   size_t column) {
  const auto *bytes = static_cast<const uint8_t *>(data) + row * stride;
  if (bits == 16) {
    int16_t value = 0;
    std::memcpy(&value, bytes + column * 2, sizeof(value));
    return value;
  }
  return static_cast<int8_t>(bytes[column]);
}

void multiply(const void *a, size_t sa, uint32_t bits, const int8_t *b,
              size_t sb, int32_t *c, size_t sc, size_t rows, size_t n, size_t k,
              size_t row0 = 0) {
  for (size_t i = 0; i < rows; ++i)
    for (size_t j = 0; j < n; ++j) {
      int32_t sum = 0;
      for (size_t x = 0; x < k; ++x)
        sum += activation(a, sa, bits, i, x) * b[x * sb + j];
      c[(row0 + i) * sc + j] = sum;
    }
}

template <class Descriptor>
int provider_outputs(const Descriptor &d, size_t row0, size_t rows) {
  const size_t blocks = (d.k + d.block_size - 1) / d.block_size;
  std::array<int8_t, DIM> scratch{};
  std::array<int64_t, DIM> exact{};
  for (size_t block = 0; block < blocks; ++block) {
    for (size_t row = row0; row < row0 + rows; ++row) {
      for (size_t column = 0; column < d.n; column += DIM) {
        const size_t count = std::min<size_t>(DIM, d.n - column);
        if (d.provider.read_scale(d.provider.context, block, column, count,
                                  scratch.data()) != IM2P_OK)
          return IM2P_ERROR;
        for (size_t lane = 0; lane < count; ++lane) {
          const size_t index = (block * d.m + row) * d.n + column + lane;
          exact[lane] = index < provider_exact_values.size()
                            ? provider_exact_values[index]
                            : 0;
        }
        provider_delivered_values.insert(provider_delivered_values.end(),
                                         exact.begin(), exact.begin() + count);
        const int status = d.provider.write_output(
            d.provider.context, block, row, column, count,
            provider_force_callback_failure ? nullptr : exact.data());
        if (status != IM2P_OK)
          return status;
      }
    }
  }
  return IM2P_OK;
}
} // namespace fake

struct im2p_sim {};
struct im2p_stream {
  std::deque<im2p_stripe_completion_t> done;
  size_t serviced = 0;
  uint64_t progress_count = 0;
};

extern "C" {
im2p_sim_t *im2p_sim_create(void) {
  fake::abi_call();
  if (fake::throw_create)
    throw std::bad_alloc();
  ++fake::sim_created;
  return new im2p_sim;
}
void im2p_sim_destroy(im2p_sim_t *p) {
  fake::abi_call();
  ++fake::sim_destroyed;
  delete p;
}
uint32_t im2p_sim_abi_version(void) { return IM2P_ABI_VERSION; }
uint32_t im2p_sim_activation_bits(void) {
  return IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS;
}
uint32_t im2p_sim_activation_storage_bytes(void) {
  return (IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS + 7) / 8;
}
uint32_t im2p_sim_weight_bits(void) { return GGML_GEMMINI_WEIGHT_BITS; }
uint32_t im2p_sim_weight_storage_bytes(void) {
  return (GGML_GEMMINI_WEIGHT_BITS + 7) / 8;
}
uint32_t im2p_sim_dim(void) { return DIM; }
int im2p_execute_matmul_extended(im2p_sim_t *,
                                 const im2p_matmul_desc_t *d,
                                 im2p_work_stats_extended_t *stats) {
  fake::abi_call();
  if (fake::fail_full)
    return IM2P_ERROR;
  if (d->provider.context == nullptr) {
    std::unique_lock lock(fake::mutex);
    fake::full_desc = *d;
    fake::full_entered = true;
    fake::changed.notify_all();
    fake::changed.wait(lock, [] { return !fake::hold_full; });
    lock.unlock();
    fake::multiply(
        d->activations, d->activation_row_stride_bytes, d->activation_bits,
        static_cast<const int8_t *>(d->weights),
        d->weight_row_stride_bytes / d->weight_storage_bytes, d->output,
        d->output_row_stride, d->m, d->n, d->k);
    if (stats)
      stats->base.completed_output_tiles = d->m * d->n;
    return IM2P_OK;
  }
  {
    std::lock_guard lock(fake::mutex);
    fake::full_desc = *d;
    ++fake::provider_full_count;
    fake::changed.notify_all();
  }
  const int status = fake::provider_outputs(*d, 0, d->m);
  if (status == IM2P_OK && stats)
    stats->base.completed_output_tiles = d->m * d->n;
  return status;
}
int im2p_begin_striped_matmul(im2p_sim_t *,
                              const im2p_stripe_work_desc_t *d,
                              im2p_stream_t **out) {
  fake::abi_call();
  std::lock_guard lock(fake::mutex);
  if (fake::fail_begin)
    return IM2P_ERROR;
  fake::work_desc = *d;
  if (d->provider.context != nullptr)
    ++fake::provider_work_count;
  *out = new im2p_stream;
  ++fake::stream_created;
  fake::changed.notify_all();
  return IM2P_OK;
}
int im2p_publish_stripe(im2p_stream_t *,
                        const im2p_activation_stripe_t *s) {
  fake::abi_call();
  std::unique_lock lock(fake::mutex);
  ++fake::publish_attempts;
  fake::publish_entered = true;
  fake::changed.notify_all();
  fake::changed.wait(lock, [] { return !fake::hold_publish; });
  if (fake::fail_publish)
    return IM2P_ERROR;
  if (fake::raw_pressure_forever)
    return IM2P_BACKPRESSURE;
  if (fake::raw_pressure_once) {
    fake::raw_pressure_once = false;
    return IM2P_BACKPRESSURE;
  }
  fake::published.push_back(*s);
  ++fake::publish_count;
  fake::changed.notify_all();
  return IM2P_OK;
}
int im2p_progress_stream(im2p_stream_t *stream, uint64_t) {
  fake::abi_call();
  std::lock_guard lock(fake::mutex);
  if (fake::fail_progress)
    return IM2P_ERROR;
  ++fake::progress_calls;
  if (fake::forward_progress_period != 0 &&
      fake::progress_calls % fake::forward_progress_period == 0)
    ++stream->progress_count;
  fake::changed.notify_all();
  while (fake::allow_completion &&
         fake::progress_calls >= fake::required_progress_cycles &&
         stream->serviced < fake::published.size()) {
    const auto &s = fake::published[stream->serviced++];
    if (fake::provider_work_count != 0) {
      const int status =
          fake::provider_outputs(fake::work_desc, s.i_start, s.rows);
      if (status != IM2P_OK)
        return IM2P_ERROR;
    } else {
      fake::multiply(s.activations, s.activation_row_stride_bytes,
                     s.activation_bits,
                     static_cast<const int8_t *>(fake::work_desc.weights),
                     fake::work_desc.weight_row_stride_bytes /
                         fake::work_desc.weight_storage_bytes,
                     fake::work_desc.output, fake::work_desc.output_row_stride,
                     s.rows, fake::work_desc.n, fake::work_desc.k, s.i_start);
    }
    stream->done.push_back({s.stripe_id, s.i_start, s.rows, s.context});
  }
  return IM2P_OK;
}
uint64_t im2p_stream_progress_count(const im2p_stream_t *stream) {
  return stream ? stream->progress_count : 0;
}
int im2p_poll_completed(im2p_stream_t *stream, im2p_stripe_completion_t *out) {
  fake::abi_call();
  std::lock_guard lock(fake::mutex);
  if (fake::fail_poll)
    return IM2P_ERROR;
  if (stream->done.empty())
    return 0;
  *out = stream->done.front();
  stream->done.pop_front();
  return 1;
}
int im2p_finish_stream_extended(im2p_stream_t *,
                                im2p_work_stats_extended_t *stats) {
  fake::abi_call();
  std::lock_guard lock(fake::mutex);
  ++fake::finish_count;
  if (fake::fail_finish)
    return IM2P_ERROR;
  stats->base.completed_stripes = fake::published.size();
  stats->lookahead_prepared = fake::published.size() > 1;
  stats->lookahead_publish_cycle = 10;
  stats->lookahead_first_activation_cycle = 11;
  stats->lookahead_first_weight_cycle = 12;
  stats->lookahead_weight_preload_cycle = 13;
  stats->lookahead_scale_cycle = 14;
  stats->lookahead_ready_cycle = 15;
  stats->current_stripe_completion_cycle = 20;
  stats->lookahead_start_cycle = 21;
  return IM2P_OK;
}
void im2p_destroy_stream(im2p_stream_t *p) {
  fake::abi_call();
  ++fake::stream_destroyed;
  delete p;
}
}

namespace {
bool expect(bool ok, const char *what) {
  if (!ok)
    std::fprintf(stderr, "FAIL: %s\n", what);
  return ok;
}

ggml_gemmini_args_t raw_args(std::vector<int8_t> &a, std::vector<int8_t> &b,
                             std::vector<int32_t> &c, size_t m = 2) {
  ggml_gemmini_args_t x{};
  x.I = m;
  x.J = 2;
  x.K = 3;
  if (!x.A.allocate(m, 3, IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS))
    std::abort();
  for (size_t row = 0; row < m; ++row)
    for (size_t column = 0; column < 3; ++column)
      if (!x.A.set(row, column, a[row * 3 + column]))
        std::abort();
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

exsia::StripeReadyEvent event(size_t id, size_t begin, size_t end,
                              uint64_t run = 77) {
  exsia::StripeReadyEvent e{};
  e.run_id = run;
  e.stripe_id = id;
  e.slot = id + 9;
  e.row_begin = begin;
  e.row_end = end;
  e.rmd_pack_ns = 101 + id;
  return e;
}

[[maybe_unused]] bool test_routes_and_preservation() {
  fake::reset();
  std::vector<int8_t> a(96), b(96);
  std::vector<int32_t> c(64);
  auto base = raw_args(a, b, c);
  int32_t d[4]{};
  float f_out[4]{};
  base.D = d;
  base.sD = 17;
  base.A_fp32 = reinterpret_cast<const float *>(a.data());
  base.B_fp32 = reinterpret_cast<const float *>(b.data());
  base.B_blocks = reinterpret_cast<const block_q8_0 *>(b.data());
  base.blocks_K = 11;
  base.blocks_J = 12;
  base.blocks_I = 13;
  base.model_arch = "preserved-model";
  base.exsia_stripe_ready_sink =
      reinterpret_cast<const exsia::StripeReadySink *>(a.data());
  base.unpacked.blocks = reinterpret_cast<const block_q8_0 *>(a.data());
  base.activation_row_offset = 5;
  base.activation_rows_per_stripe = 2;
  base.block_size_k = 29;
  base.tile_I = 3;
  base.tile_J = 4;
  base.tile_K = 5;
  base.f_out = f_out;
  base.col_stride_f_out = 19;
  base.stride_f_out = 23;
  const float scales[2] = {1, 2};
  uint8_t cb[2]{};
  uint16_t rr[2]{};
  block_q8_h1 h1[2]{};
  block_q8_h2 h2[2]{};
  block_q8_hp1 hp1[2]{};
  block_q8_hp2 hp2[2]{};
  const std::array formats = {
      ggml_gemmini_args_t::im2p_weight_format_t::q8_0_unpacked_to_h1,
      ggml_gemmini_args_t::im2p_weight_format_t::q8_h1,
      ggml_gemmini_args_t::im2p_weight_format_t::q8_h2,
      ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1,
      ggml_gemmini_args_t::im2p_weight_format_t::q8_hp2,
      ggml_gemmini_args_t::im2p_weight_format_t::q8_channel,
      ggml_gemmini_args_t::im2p_weight_format_t::q8_channel_dense_sidecar,
  };
  const std::array expected = {Route::q8_0_unpacked_to_h1,
                               Route::q8_h1,
                               Route::q8_h2,
                               Route::q8_hp1,
                               Route::q8_hp2,
                               Route::q8_channel,
                               Route::q8_channel_dense_sidecar};
  for (size_t index = 0; index < formats.size(); ++index) {
    const auto format = formats[index];
    auto x = base;
    x.weight_format = format;
    x.B_scales = scales;
    x.c_b = cb;
    x.s_rf = scales;
    x.R = rr;
    x.s_rf_stripe = scales;
    x.R_stripe = rr;
    x.q8_h1_blocks = h1;
    x.q8_h1_block_count = 31;
    x.q8_h1_rows = 32;
    x.blocks_per_row = 33;
    x.q8_h2_blocks = h2;
    x.q8_h2_block_count = 34;
    x.q8_h2_blocks_per_row = 35;
    x.q8_hp1_blocks = hp1;
    x.q8_hp1_block_count = 36;
    x.q8_hp1_blocks_per_row = 37;
    x.q8_hp2_blocks = hp2;
    x.q8_hp2_block_count = 38;
    x.q8_hp2_blocks_per_row = 39;
    x.stripe_J = 40;
    x.weight_channel_scales = scales;
    x.weight_channel_scale_count = 41;
    x.q8_channel_row_base = reinterpret_cast<uint8_t *>(b.data());
    x.q8_channel_row_stride = 42;
    x.q8_channel_row_count = 43;
    auto started = execute(&x, Mode::full);
    const char *reason = expected[index] == Route::q8_h2
                             ? "q8_h2 is deprecated"
                             : "native Gemmini route is classified but not "
                               "raw-ABI compatible";
    if (!expect(started.run != nullptr,
                "unsupported route still returns an inspectable run") ||
        !expect(
            started.status.code == StatusCode::unsupported_route &&
                started.status.route == expected[index] &&
                std::strcmp(started.status.message, reason) == 0,
            "every Gemmini format is classified and explicitly unsupported"))
      return false;
    const auto snap = RunTestAccess::inspect(*started.run);
    if (!expect(
            snap.i == x.I && snap.j == x.J && snap.k == x.K &&
                snap.sa == x.sA && snap.sb == x.sB && snap.sc == x.sC &&
                snap.sd == x.sD &&
                snap.activation_row_offset == x.activation_row_offset &&
                snap.activation_rows_per_stripe ==
                    x.activation_rows_per_stripe &&
                snap.block_size_k == x.block_size_k &&
                snap.tile_i == x.tile_I && snap.tile_j == x.tile_J &&
                snap.tile_k == x.tile_K &&
                snap.weight_format == static_cast<uint8_t>(x.weight_format) &&
                snap.a == x.A.raw_data() && snap.b == x.B && snap.c == x.C &&
                snap.d == x.D && snap.a_fp32 == x.A_fp32 &&
                snap.b_fp32 == x.B_fp32 && snap.b_blocks == x.B_blocks &&
                snap.b_scales == x.B_scales && snap.blocks_k == x.blocks_K &&
                snap.blocks_j == x.blocks_J && snap.blocks_i == x.blocks_I &&
                snap.c_b == x.c_b && snap.s_rf == x.s_rf && snap.r == x.R &&
                snap.s_rf_stripe == x.s_rf_stripe &&
                snap.r_stripe == x.R_stripe && snap.stripe_j == x.stripe_J &&
                snap.q8_h1 == x.q8_h1_blocks &&
                snap.q8_h1_count == x.q8_h1_block_count &&
                snap.q8_h1_rows == x.q8_h1_rows &&
                snap.blocks_per_row == x.blocks_per_row &&
                snap.q8_h2 == x.q8_h2_blocks &&
                snap.q8_h2_count == x.q8_h2_block_count &&
                snap.q8_h2_blocks_per_row == x.q8_h2_blocks_per_row &&
                snap.q8_hp1 == x.q8_hp1_blocks &&
                snap.q8_hp1_count == x.q8_hp1_block_count &&
                snap.q8_hp1_blocks_per_row == x.q8_hp1_blocks_per_row &&
                snap.q8_hp2 == x.q8_hp2_blocks &&
                snap.q8_hp2_count == x.q8_hp2_block_count &&
                snap.q8_hp2_blocks_per_row == x.q8_hp2_blocks_per_row &&
                snap.channel_scales == x.weight_channel_scales &&
                snap.channel_scale_count == x.weight_channel_scale_count &&
                snap.channel_rows == x.q8_channel_row_base &&
                snap.channel_row_stride == x.q8_channel_row_stride &&
                snap.channel_row_count == x.q8_channel_row_count &&
                snap.f_out == x.f_out &&
                snap.col_stride_f_out == x.col_stride_f_out &&
                snap.stride_f_out == x.stride_f_out &&
                snap.model_arch == x.model_arch &&
                snap.stripe_sink == x.exsia_stripe_ready_sink &&
                snap.unpacked_blocks == x.unpacked.blocks,
            "complete route scalars, pointers, counts, and strides preserved"))
      return false;
    const auto fenced = fence(*started.run);
    if (!expect(fenced.status.code == StatusCode::unsupported_route &&
                    fenced.status.route == expected[index] &&
                    std::strcmp(fenced.status.message, reason) == 0,
                "unsupported and deprecated routes fence cleanly"))
      return false;
  }
  return expect(fake::owner == std::thread::id{},
                "unsupported and deprecated routes never start raw execution");
}

[[maybe_unused]] bool test_native_classification() {
  std::vector<int8_t> a(128), dense(128);
  std::vector<int32_t> c(8);
  auto base = raw_args(a, dense, c);
  base.I = 1;
  base.J = 2;
  base.K = 32;
  base.sA = 32;
  base.sC = 2;
  base.B = nullptr;
  auto check = [&](ggml_gemmini_args_t &x, Route expected) {
    auto result = execute(&x);
    const char *reason =
        expected == Route::q8_h2
            ? "q8_h2 is deprecated"
            : "native Gemmini route is classified but not raw-ABI compatible";
    return expect(
        result.status.code == StatusCode::unsupported_route &&
            result.status.route == expected && result.status.native_contract &&
            std::strcmp(result.status.message, reason) == 0,
        "authoritative native contract classified and explicitly unsupported");
  };

  std::vector<block_q8_h1> h1(2);
  auto x = base;
  x.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_h1;
  x.q8_h1_blocks = h1.data();
  x.q8_h1_block_count = h1.size();
  x.q8_h1_rows = 2;
  x.blocks_per_row = 1;
  if (!check(x, Route::q8_h1))
    return false;

  std::vector<block_q8_h2> h2(2);
  x = base;
  x.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_h2;
  x.q8_h2_blocks = h2.data();
  x.q8_h2_block_count = h2.size();
  x.q8_h2_blocks_per_row = 1;
  if (!check(x, Route::q8_h2))
    return false;

  std::vector<block_q8_hp1> hp1(2);
  x = base;
  x.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1;
  x.q8_hp1_blocks = hp1.data();
  x.q8_hp1_block_count = hp1.size();
  x.q8_hp1_blocks_per_row = 1;
  if (!check(x, Route::q8_hp1))
    return false;

  std::vector<block_q8_hp2> hp2(2);
  x = base;
  x.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_hp2;
  x.q8_hp2_blocks = hp2.data();
  x.q8_hp2_block_count = hp2.size();
  x.q8_hp2_blocks_per_row = 1;
  if (!check(x, Route::q8_hp2))
    return false;

  std::vector<uint8_t> rows(2 * (sizeof(float) + 3));
  const float one = 1.0f;
  std::memcpy(rows.data(), &one, sizeof(one));
  std::memcpy(rows.data() + sizeof(float) + 3, &one, sizeof(one));
  x = base;
  x.K = 3;
  x.sA = 3;
  x.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_channel;
  x.q8_channel_row_base = rows.data();
  x.q8_channel_row_stride = sizeof(float) + 3;
  x.q8_channel_row_count = 2;
  x.B = reinterpret_cast<elem_t *>(rows.data() + sizeof(float));
  x.sB = sizeof(float) + 3;
  if (!check(x, Route::q8_channel))
    return false;

  const float channel_scales[2] = {1.0f, 2.0f};
  x = base;
  x.K = 3;
  x.sA = 3;
  x.B = dense.data();
  x.sB = 3;
  x.weight_format =
      ggml_gemmini_args_t::im2p_weight_format_t::q8_channel_dense_sidecar;
  x.weight_channel_scales = channel_scales;
  x.weight_channel_scale_count = 2;
  return check(x, Route::q8_channel_dense_sidecar);
}
bool test_native_q4_q16_provider_golden() {
  auto base = [] {
    ggml_gemmini_args_t args{};
    args.I = 1;
    args.J = 1;
    args.K = 32;
    args.native_block_count = 1;
    args.native_blocks_per_row = 1;
    return args;
  };

  block_q4_h0 q4_h0{};
  block_q4_h1 q4_h1{};
  block_q4_hp1 q4_hp1{};
  q4_h0.d = 0x3c00; // IEEE binary16 1.0
  q4_h1.s_rf = 0.125f;
  q4_h1.c_b = 2;
  q4_h1.R = 6;
  q4_hp1.channel_scale = 0.25f;
  q4_hp1.m = 2;
  q4_h0.qs[15] = q4_h1.qs[15] = q4_hp1.qs[15] = 0x00;
  q4_h0.qs[0] = q4_h1.qs[0] = q4_hp1.qs[0] = 0xf0;

  auto check_q4 = [&](auto format, auto member, const auto *block,
                      const char *message) {
    auto args = base();
    args.weight_format = format;
    args.*member = block;
    std::array<int8_t, 1> lane15{}, lane16{};
    double factor = 0.0;
    return expect(args.has_native_matched_width_contract(), message) &&
           expect(RunTestAccess::read_selected_weight(
                      args, 15, 0, 1, lane15.data()) == IM2P_OK &&
                      RunTestAccess::read_selected_weight(
                          args, 16, 0, 1, lane16.data()) == IM2P_OK &&
                      lane15[0] == -8 && lane16[0] == 7,
                  "Q4 split-half lanes 15/16 decode as signed -8..7") &&
           expect(RunTestAccess::weight_factor(args, 0, 0, factor) &&
                      factor == 1.0,
                  "Q4 provider applies the exact per-block factor");
  };
  if (!check_q4(ggml_gemmini_args_t::im2p_weight_format_t::q4_h0,
                &ggml_gemmini_args_t::q4_h0_blocks, &q4_h0,
                "Q4_H0 native contract") ||
      !check_q4(ggml_gemmini_args_t::im2p_weight_format_t::q4_h1,
                &ggml_gemmini_args_t::q4_h1_blocks, &q4_h1,
                "Q4_H1 native contract") ||
      !check_q4(ggml_gemmini_args_t::im2p_weight_format_t::q4_hp1,
                &ggml_gemmini_args_t::q4_hp1_blocks, &q4_hp1,
                "Q4_HP1 native contract"))
    return false;

  block_q16_h0 q16_h0{};
  block_q16_h1 q16_h1{};
  block_q16_hp1 q16_hp1{};
  q16_h0.d = 0x3c00;
  q16_h1.s_rf = 0.125f;
  q16_h1.c_b = 2;
  q16_h1.R = 6;
  q16_hp1.channel_scale = 0.25f;
  q16_hp1.m = 2;
  q16_h0.qs[15] = q16_h1.qs[15] = q16_hp1.qs[15] = INT16_MIN;
  q16_h0.qs[16] = q16_h1.qs[16] = q16_hp1.qs[16] = INT16_MAX;
  auto check_q16 = [&](auto format, auto member, const auto *block,
                       const char *message) {
    auto args = base();
    args.weight_format = format;
    args.*member = block;
    std::array<int16_t, 1> lane15{}, lane16{};
    double factor = 0.0;
    return expect(args.has_native_matched_width_contract(), message) &&
           expect(RunTestAccess::read_selected_weight(
                      args, 15, 0, 1, lane15.data()) == IM2P_OK &&
                      RunTestAccess::read_selected_weight(
                          args, 16, 0, 1, lane16.data()) == IM2P_OK &&
                      lane15[0] == INT16_MIN && lane16[0] == INT16_MAX,
                  "Q16 provider preserves signed int16 codes") &&
           expect(RunTestAccess::weight_factor(args, 0, 0, factor) &&
                      factor == 1.0,
                  "Q16 provider applies the exact per-block factor");
  };
  if (!check_q16(ggml_gemmini_args_t::im2p_weight_format_t::q16_h0,
                   &ggml_gemmini_args_t::q16_h0_blocks, &q16_h0,
                   "Q16_H0 native contract") ||
      !check_q16(ggml_gemmini_args_t::im2p_weight_format_t::q16_h1,
                   &ggml_gemmini_args_t::q16_h1_blocks, &q16_h1,
                   "Q16_H1 native contract") ||
      !check_q16(ggml_gemmini_args_t::im2p_weight_format_t::q16_hp1,
                   &ggml_gemmini_args_t::q16_hp1_blocks, &q16_hp1,
                   "Q16_HP1 native contract"))
    return false;

#if GGML_GEMMINI_WEIGHT_BITS == 4 || GGML_GEMMINI_WEIGHT_BITS == 16
  fake::reset();
  fake::provider_exact_values = {3};
  float output = 91.0f;
  auto args = base();
  if (!args.A.allocate(1, 32, IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS))
    return false;
  args.f_out = &output;
  args.stride_f_out = 1;
  args.act_quant.storage()
      .emplace<ggml::gemmini::quants::act::tensor::Meta>()
      .scale = 0.5f;
#if GGML_GEMMINI_WEIGHT_BITS == 4
  args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q4_h0;
  args.q4_h0_blocks = &q4_h0;
#else
  args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q16_h0;
  args.q16_h0_blocks = &q16_h0;
#endif
  auto started = execute(&args, Mode::full);
  if (!expect(started.status.ok(), "matched native route starts") ||
      !expect(fence(*started.run).status.ok(),
              "matched native FULL route fences"))
    return false;
  const auto &descriptor = fake::full_desc;
  if (!expect(descriptor.abi_version == IM2P_ABI_VERSION &&
                  descriptor.activation_bits == GGML_GEMMINI_WEIGHT_BITS &&
                  descriptor.weight_bits == GGML_GEMMINI_WEIGHT_BITS &&
                  descriptor.weight_storage_bytes ==
                      (GGML_GEMMINI_WEIGHT_BITS + 7) / 8 &&
#if GGML_GEMMINI_WEIGHT_BITS == 4
                  descriptor.provider.read_weight_i8 != nullptr &&
                  descriptor.provider.read_weight_i16 == nullptr &&
#else
                  descriptor.provider.read_weight_i8 == nullptr &&
                  descriptor.provider.read_weight_i16 != nullptr &&
#endif
                  descriptor.provider.read_scale != nullptr &&
                  descriptor.provider.write_output != nullptr,
              "matched native FULL descriptor carries exact widths") ||
      !expect(output == 1.5f,
              "host reducer applies activation and block factors"))
    return false;

  fake::reset();
  fake::provider_exact_values = {3};
  output = 91.0f;
  args.activation_rows_per_stripe = 1;
  args.act_quant.storage().emplace<exsia::Meta>();
  auto pipeline = execute(&args, Mode::stripe_pipeline);
  if (!expect(pipeline.status.ok(),
              "matched native route starts PIPELINE") ||
      !expect(submit_stripe(*pipeline.run, event(0, 0, 1), {true, -1}).ok(),
              "matched native stripe publishes") ||
      !expect(fence(*pipeline.run).status.ok(),
              "matched native PIPELINE fences"))
    return false;
  const auto &striped = fake::work_desc;
  if (!expect(striped.abi_version == IM2P_ABI_VERSION &&
                  striped.activation_bits == GGML_GEMMINI_WEIGHT_BITS &&
                  striped.weight_bits == GGML_GEMMINI_WEIGHT_BITS &&
                  striped.weight_storage_bytes ==
                      (GGML_GEMMINI_WEIGHT_BITS + 7) / 8 &&
                  striped.weight_row_stride_bytes ==
                      (GGML_GEMMINI_WEIGHT_BITS + 7) / 8 &&
#if GGML_GEMMINI_WEIGHT_BITS == 4
                  striped.provider.read_weight_i8 != nullptr &&
                  striped.provider.read_weight_i16 == nullptr &&
#else
                  striped.provider.read_weight_i8 == nullptr &&
                  striped.provider.read_weight_i16 != nullptr &&
#endif
                  striped.provider.read_scale != nullptr &&
                  striped.provider.write_output != nullptr &&
                  fake::provider_work_count == 1 && fake::publish_count == 1,
              "matched native PIPELINE descriptor carries exact widths") ||
      !expect(output == 91.0f,
              "matched PIPELINE output remains staged before authorization") ||
      !expect(authorize_output_commit(*pipeline.run, true).ok() &&
                  output == 1.5f,
              "matched PIPELINE commits after authorization"))
    return false;
#endif
  return true;
}

bool test_native_h1_provider_start_contract() {
  fake::reset();
  float out[2]{};
  block_q8_h1 blocks[2]{};
  ggml_gemmini_args_t x{};
  x.I = 1;
  x.J = 2;
  x.K = 32;
  if (!x.A.allocate(1, 32, IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS))
    return false;
  x.sA = 32;
  x.f_out = out;
  x.stride_f_out = 2;
  x.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_h1;
  x.q8_h1_blocks = blocks;
  x.q8_h1_block_count = 2;
  x.q8_h1_rows = 2;
  x.blocks_per_row = 1;
  x.act_quant.storage()
      .emplace<ggml::gemmini::quants::act::tensor::Meta>()
      .scale = 1.0f;
  auto started = execute(&x);
  if (!expect(started.status.ok(), "native H1 starts through provider"))
    return false;
  const auto done = fence(*started.run);
  std::lock_guard lock(fake::mutex);
  const auto &d = fake::full_desc;
  return expect(
      done.status.ok() && fake::provider_full_count == 1 &&
          d.abi_version == IM2P_ABI_VERSION &&
          d.activation_bits == IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS &&
          d.weights == nullptr && d.output == nullptr && d.m == 1 && d.n == 2 &&
          d.k == 32 && d.weight_row_stride_bytes == 2 &&
          d.output_row_stride == 2 &&
          d.block_size == 32 && d.vector_op == IM2P_VECTOR_EXTERNAL &&
          d.provider.context != nullptr &&
          d.provider.read_weight_i8 != nullptr &&
          d.provider.read_weight_i16 == nullptr &&
          d.provider.read_scale != nullptr &&
          d.provider.write_output != nullptr,
      "native H1 provider descriptor is exact");
}

bool test_provider_int64_scaling_full_pipeline() {
  constexpr size_t rows = 3;
  constexpr size_t columns = 2;
  constexpr size_t blocks_per_row = 2;
  const std::vector<int64_t> exact = {
      100,                  -200,
      INT64_C(2147487743),  -INT64_C(2147495993),
      -INT64_C(4294979641), INT64_C(4295011617),
      300,                  400,
      INT64_C(2147504127),  -INT64_C(4294987775),
      INT64_C(4295000063),  -INT64_C(2147516415),
  };
  const std::array<double, 4> factors = {0.5, 0.75, 1.25, 1.5};
  constexpr double activation_scale = 0.5;
  std::array<float, rows * columns> oracle{};
  for (size_t row = 0; row < rows; ++row) {
    for (size_t column = 0; column < columns; ++column) {
      long double sum = 0.0L;
      for (size_t block = 0; block < blocks_per_row; ++block) {
        const size_t index = (block * rows + row) * columns + column;
        sum += static_cast<long double>(exact[index]) *
               static_cast<long double>(factors[block * columns + column]) *
               static_cast<long double>(activation_scale);
      }
      oracle[row * columns + column] = static_cast<float>(sum);
    }
  }

  auto run = [&](Mode mode, bool force_callback_failure,
                 std::array<float, rows * columns> &destination) {
    fake::reset();
    fake::provider_exact_values = exact;
    fake::provider_force_callback_failure = force_callback_failure;
    std::array<block_q8_h1, columns * blocks_per_row> weights{};
    for (size_t column = 0; column < columns; ++column) {
      weights[column * blocks_per_row].s_rf = column == 0 ? 0.25f : 0.125f;
      weights[column * blocks_per_row].c_b = column == 0 ? 1 : 2;
      weights[column * blocks_per_row].R = column == 0 ? 1 : 4;
      weights[column * blocks_per_row + 1].s_rf = column == 0 ? 0.25f : 0.25f;
      weights[column * blocks_per_row + 1].c_b = column == 0 ? 2 : 3;
      weights[column * blocks_per_row + 1].R = 3;
    }
    ggml_gemmini_args_t args{};
    args.I = rows;
    args.J = columns;
    args.K = 64;
    if (!args.A.allocate(rows, args.K, IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS))
      return false;
    args.activation_rows_per_stripe = 1;
    args.f_out = destination.data();
    args.stride_f_out = columns;
    args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_h1;
    args.q8_h1_blocks = weights.data();
    args.q8_h1_block_count = weights.size();
    args.q8_h1_rows = columns;
    args.blocks_per_row = blocks_per_row;
    args.act_quant.storage()
        .emplace<ggml::gemmini::quants::act::tensor::Meta>()
        .scale = static_cast<float>(activation_scale);
    auto started = execute(&args, mode, {64});
    if (!started.status.ok())
      return false;
    if (mode == Mode::stripe_pipeline) {
      // These frontend-only stripes deliberately have no residual handles;
      // llama's integrated oracle below owns multi-stripe RMD application.
      const auto first = submit_stripe(*started.run, event(0, 0, 1));
      if (!first.ok())
        return false;
      if (!force_callback_failure) {
        const auto second = submit_stripe(*started.run, event(1, 1, 2));
        const auto third = submit_stripe(*started.run, event(2, 2, 3));
        if (!second.ok() || !third.ok())
          return false;
      }
    }
    const auto done = fence(*started.run);
    if (force_callback_failure)
      return done.status.code == StatusCode::execution_failure;
    std::vector<int64_t> delivered_oracle;
    if (mode == Mode::full) {
      delivered_oracle = exact;
    } else {
      for (size_t row = 0; row < rows; ++row)
        for (size_t block = 0; block < blocks_per_row; ++block)
          for (size_t column = 0; column < columns; ++column)
            delivered_oracle.push_back(
                exact[(block * rows + row) * columns + column]);
    }
    if (!done.status.ok() ||
        fake::provider_delivered_values != delivered_oracle) {
      std::fprintf(stderr,
                   "cross-mode fence=%u delivered=%zu expected=%zu\n",
                   static_cast<unsigned>(done.status.code),
                   fake::provider_delivered_values.size(),
                   delivered_oracle.size());
      return false;
    }
    if (mode == Mode::full)
      return fake::provider_full_count == 1 && fake::provider_work_count == 0 &&
             fake::publish_count == 0;
    if (destination != std::array<float, rows * columns>{92, 92, 92, 92, 92,
                                                         92} ||
        fake::provider_full_count != 0 || fake::provider_work_count != 1 ||
        fake::publish_count != 3 || fake::finish_count != 1) {
      std::fprintf(stderr,
                   "cross-mode staged=%d full=%zu work=%zu publish=%zu finish=%zu\n",
                   destination == std::array<float, rows * columns>{
                                      92, 92, 92, 92, 92, 92},
                   fake::provider_full_count, fake::provider_work_count,
                   fake::publish_count, fake::finish_count);
      return false;
    }
    if (!authorize_output_commit(*started.run, true).ok())
      return false;
    const auto committed = destination;
    return authorize_output_commit(*started.run, true).ok() &&
           destination == committed;
  };

  std::array<float, rows * columns> full = {91, 91, 91, 91, 91, 91};
  std::array<float, rows * columns> pipeline = {92, 92, 92, 92, 92, 92};
  if (!expect(run(Mode::full, false, full), "int64 provider FULL completes") ||
      !expect(run(Mode::stripe_pipeline, false, pipeline),
              "int64 provider pipeline completes") ||
      !expect(full == oracle && pipeline == oracle,
              "FULL/pipeline match long-double int64 scaling oracle"))
    return false;

  std::printf("INT64_QA positive_input=%lld negative_input=%lld "
              "weight_scales=[0.25,0.125,0.25,0.25] "
              "activation_scale=0.5 positive_result=%.9g "
              "negative_result=%.9g\n",
              static_cast<long long>(exact[2]),
              static_cast<long long>(exact[3]), full[2], full[3]);

  std::array<float, rows * columns> failed_full = {73, 73, 73, 73, 73, 73};
  std::array<float, rows * columns> failed_pipeline = {74, 74, 74, 74, 74, 74};
  if (!expect(run(Mode::full, true, failed_full) &&
                  failed_full == std::array<float, rows * columns>{
                                     73, 73, 73, 73, 73, 73},
              "FULL callback failure preserves destination") ||
      !expect(run(Mode::stripe_pipeline, true, failed_pipeline) &&
                  failed_pipeline == std::array<float, rows * columns>{
                                         74, 74, 74, 74, 74, 74},
              "pipeline callback failure preserves destination"))
    return false;

  // A single-row request must retain the pipeline lifecycle and commit once.
  fake::reset();
  fake::provider_exact_values.assign(exact.begin(), exact.begin() + columns);
  std::array<block_q8_h1, columns> one_row_weights{};
  std::array<float, columns> one_row = {81, 81};
  ggml_gemmini_args_t one_row_args{};
  one_row_args.I = 1;
  one_row_args.J = columns;
  one_row_args.K = 32;
  if (!one_row_args.A.allocate(1, one_row_args.K,
                               IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS))
    return false;
  one_row_args.activation_rows_per_stripe = 1;
  one_row_args.f_out = one_row.data();
  one_row_args.stride_f_out = columns;
  one_row_args.weight_format =
      ggml_gemmini_args_t::im2p_weight_format_t::q8_h1;
  one_row_args.q8_h1_blocks = one_row_weights.data();
  one_row_args.q8_h1_block_count = one_row_weights.size();
  one_row_args.q8_h1_rows = columns;
  one_row_args.blocks_per_row = 1;
  one_row_args.act_quant.storage()
      .emplace<ggml::gemmini::quants::act::tensor::Meta>()
      .scale = 1.0f;
  auto one = execute(&one_row_args, Mode::stripe_pipeline, {64});
  if (!one.status.ok() || !submit_stripe(*one.run, event(0, 0, 1)).ok() ||
      !fence(*one.run).status.ok())
    return false;
  return expect(fake::provider_full_count == 0 &&
                    fake::provider_work_count == 1 &&
                    fake::publish_count == 1 && fake::finish_count == 1 &&
                    one_row == std::array<float, columns>{81, 81},
                "one-row request remains one staged pipeline stripe") &&
         expect(authorize_output_commit(*one.run, true).ok(),
                "one-row pipeline commits exactly once");
}

bool test_rejected_routes_do_not_execute() {
  fake::reset();
  ggml_gemmini_args_t x{};
  x.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_h2;
  auto h2 = execute(&x);
  x.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_hp2;
  auto hp2 = execute(&x);
  x.weight_format = static_cast<ggml_gemmini_args_t::im2p_weight_format_t>(255);
  auto unknown = execute(&x);
  return expect(
      h2.status.code == StatusCode::unsupported_route &&
          std::strcmp(h2.status.message, "q8_h2 is deprecated") == 0 &&
          hp2.status.code == StatusCode::unsupported_route &&
          std::strcmp(hp2.status.message, "q8_hp2 is unsupported") == 0 &&
          unknown.status.code == StatusCode::unsupported_route &&
          std::strcmp(unknown.status.message, "unknown Gemmini weight route") ==
              0 &&
          fake::provider_full_count == 0 && fake::provider_work_count == 0,
      "H2, HP2, and unknown routes reject without execution");
}

bool test_mode_and_raw_scale_contract() {
  fake::reset();
  std::vector<int8_t> a = {1, 2, 3}, b = {1, 0, 0, 1, 1, 1};
  std::vector<int32_t> c(2);
  auto args = raw_args(a, b, c, 1);
  const auto invalid = execute(&args, static_cast<Mode>(0xff));
  if (!expect(invalid.status.code == StatusCode::invalid_argument &&
                  invalid.run == nullptr,
              "invalid mode is rejected before run construction"))
    return false;

  auto reject = [&](auto mutate, const char *message) {
    auto changed = args;
    mutate(changed);
    const auto result = execute(&changed);
    return expect(result.status.code == StatusCode::unsupported_route, message);
  };
  int32_t bias[2] = {1, -1};
  if (!reject([](auto &x) { x.scale_B = 2.0f; },
              "nonidentity scale_B is rejected") ||
      !reject([](auto &x) { x.scale_D = 2; },
              "nonidentity scale_D is rejected") ||
      !reject([](auto &x) { x.scale = 0.5f; },
              "nonidentity output scale is rejected") ||
      !reject([](auto &x) { x.bert_scale = 3.0f; },
              "nonidentity bert_scale is rejected") ||
      !reject([](auto &x) { x.repeating_bias = true; },
              "repeating-bias semantics are rejected without a bias pointer") ||
      !reject(
          [&](auto &x) {
            x.D = bias;
            x.repeating_bias = true;
          },
          "nonzero repeating bias is rejected"))
    return false;

  fake::hold_full = true;
  auto valid = execute(&args);
  if (!valid.status.ok() || !fake::wait([] { return fake::full_entered; }))
    return false;
  args.scale_B = 9.0f;
  args.scale_D = 9;
  args.scale = 9.0f;
  args.bert_scale = 9.0f;
  const auto snapshot = RunTestAccess::inspect(*valid.run);
  {
    std::lock_guard lock(fake::mutex);
    fake::hold_full = false;
    fake::changed.notify_all();
  }
  return expect(snapshot.scale_b == 1.0f && snapshot.scale_d == 1 &&
                    snapshot.scale == 1.0f && snapshot.bert_scale == 1.0f &&
                    fence(*valid.run).status.ok(),
                "raw scale identities are snapshotted before execution");
}

bool test_full_golden_and_scalar_snapshot() {
  fake::reset();
  fake::hold_full = true;
  std::vector<int8_t> a = {1, 2, 3, 4, 5, 6}, b = {1, 0, 0, 1, 1, 1};
  std::vector<int32_t> c(4);
  auto args = raw_args(a, b, c);
  args.activation_row_offset = 19;
  args.tile_I = 99;
  args.tile_J = 99;
  args.tile_K = 101;
  std::fill(c.begin(), c.end(), 123456);
  auto started = execute(&args, Mode::full);
  if (!expect(started.status.ok() &&
                  fake::wait([] { return fake::full_entered; }),
              "full worker starts") ||
      !expect(c == std::vector<int32_t>(4, 123456),
              "destination remains untouched before successful fence"))
    return false;
  args.I = 999;
  args.A = {};
  std::fill(b.begin(), b.end(), 99);
  {
    std::lock_guard lock(fake::mutex);
    fake::hold_full = false;
    fake::changed.notify_all();
  }
  auto done = fence(*started.run);
  return expect(done.status.ok(), "full fence") &&
         expect(c == std::vector<int32_t>({4, 5, 10, 11}),
                "full real mapping golden") &&
         expect(fake::full_desc.m == 2 &&
                    fake::full_desc.activations != nullptr &&
                    fake::full_desc.activation_bits ==
                        IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS,
                "scalars and owned activation backing are snapshotted") &&
         expect(fake::full_desc.tile_i_rows == 2 &&
                    fake::full_desc.tile_j_columns == 2 &&
                    fake::full_desc.block_size == args.block_size_k,
                "tile counts multiply by DIM then clamp to tails; tile_K is "
                "metadata only") &&
         expect(fake::one_owner, "dedicated C ABI owner");
}

bool test_multiwidth_activation_snapshot_validation() {
  fake::reset();
  std::vector<int8_t> activation = {1, 2, 3};
  std::vector<int8_t> weights = {1, 0, 0, 1, 1, 1};
  std::vector<int32_t> output(2);
  auto args = raw_args(activation, weights, output, 1);
  auto malformed = args;
  malformed.A.row_stride_bytes = 1;
  if (!expect(
          execute(&malformed).status.code == StatusCode::invalid_argument,
          "malformed activation byte stride rejects before worker creation"))
    return false;
  malformed = args;
  malformed.A.bits = IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS == 16 ? 8 : 16;
  if (!expect(execute(&malformed).status.code == StatusCode::invalid_argument,
              "stale activation width rejects before worker creation"))
    return false;
  fake::hold_full = true;
  auto started = execute(&args);
  if (!started.status.ok() || !fake::wait([] { return fake::full_entered; }))
    return false;
  const auto snapshot = RunTestAccess::inspect(*started.run);
  {
    std::lock_guard lock(fake::mutex);
    fake::hold_full = false;
    fake::changed.notify_all();
  }
  return expect(
      snapshot.a != nullptr &&
          snapshot.activation_bits == IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS &&
          snapshot.activation_raw_size == args.A.raw_size() &&
          snapshot.activation_row_stride_bytes == args.A.row_stride_bytes &&
          fence(*started.run).status.ok(),
      "A4/A16 snapshot uses raw bytes instead of implicit elem_t conversion");
}

bool test_tile_normalization_validation() {
  fake::reset();
  std::vector<int8_t> a(3), b(6);
  std::vector<int32_t> c(2);
  auto args = raw_args(a, b, c, 1);
  args.tile_I = std::numeric_limits<size_t>::max() / DIM + 1;
  if (!expect(execute(&args).status.code == StatusCode::invalid_contract,
              "tile_I count multiplication overflow is explicit"))
    return false;
  args.tile_I = 1;
  args.tile_J = std::numeric_limits<size_t>::max() / DIM + 1;
  return expect(execute(&args).status.code == StatusCode::invalid_contract,
                "tile_J count multiplication overflow is explicit");
}

bool test_pipeline_lifecycle() {
  fake::reset();
  fake::allow_completion = false;
  fake::raw_pressure_once = true;
  std::vector<int8_t> a = {1, 2, 3, 4, 5, 6}, b = {1, 0, 0, 1, 1, 1};
  std::vector<int32_t> c(4);
  auto args = raw_args(a, b, c);
  auto started = execute(&args, Mode::stripe_pipeline, {64});
  if (!started.status.ok())
    return false;
  bool packet_released = false;
  {
    auto packet = std::shared_ptr<ggml::gemmini::rmd::StripePacket>(
        new ggml::gemmini::rmd::StripePacket, [&](auto *p) {
          packet_released = true;
          delete p;
        });
    auto first = event(0, 0, 1);
    first.rmd_packet = packet;
    if (!expect(submit_stripe(*started.run, first).ok(), "first accepted"))
      return false;
  }
  if (!expect(!packet_released, "submitted residual handle is retained"))
    return false;
  if (!expect(submit_stripe(*started.run, event(1, 1, 2)).ok(),
              "lookahead accepted") ||
      !expect(submit_stripe(*started.run, event(2, 2, 2)).code ==
                  StatusCode::invalid_argument,
              "bounds before pressure") ||
      !expect(fake::wait([] { return fake::publish_count == 2; }),
              "raw retry drains") ||
      !expect(fake::publish_attempts == 3,
              "raw pressure retried identical logical event"))
    return false;
  {
    std::lock_guard lock(fake::mutex);
    fake::allow_completion = true;
    fake::changed.notify_all();
  }
  auto done = fence(*started.run);
  auto again = fence(*started.run);
  const auto authorized = authorize_output_commit(*started.run, true);
  return expect(done.status.ok() && again.status.code == done.status.code,
                "sticky idempotent fence") &&
         expect(authorized.ok() && c == std::vector<int32_t>({4, 5, 10, 11}),
                "RMD authorization commits stripe golden") &&
         expect(done.stats.lookahead_publish_cycle == 10 &&
                    done.stats.lookahead_first_activation_cycle == 11 &&
                    done.stats.lookahead_first_weight_cycle == 12 &&
                    done.stats.lookahead_weight_preload_cycle == 13 &&
                    done.stats.lookahead_scale_cycle == 14 &&
                    done.stats.lookahead_ready_cycle == 15 &&
                    done.stats.current_stripe_completion_cycle == 20 &&
                    done.stats.lookahead_start_cycle == 21,
                "exact extended lookahead stats");
}

bool test_backpressure_runid_incomplete_and_concurrent() {
  fake::reset();
  fake::allow_completion = false;
  fake::hold_publish = true;
  std::vector<int8_t> a(9), b(6);
  std::vector<int32_t> c(6, 99);
  auto args = raw_args(a, b, c, 3);
  auto one = execute(&args, Mode::stripe_pipeline, {32});
  if (!expect(submit_stripe(*one.run, event(0, 0, 1, 4)).ok() &&
                  submit_stripe(*one.run, event(1, 1, 2, 4)).ok(),
              "exactly two producer slots accept without blocking") ||
      !expect(submit_stripe(*one.run, event(2, 2, 3, 5)).code ==
                  StatusCode::invalid_argument,
              "run id validation precedes the capacity wait"))
    return false;
  Status third_status{};
  std::thread producer(
      [&] { third_status = submit_stripe(*one.run, event(2, 2, 3, 4)); });
  if (!expect(RunTestAccess::wait_for_blocked_submit(*one.run, 1),
              "third producer blocks on the fixed two-slot contract") ||
      !expect(RunTestAccess::inspect(*one.run).outstanding == 2,
              "dequeue alone does not release either producer slot"))
    return false;
  {
    std::lock_guard lock(fake::mutex);
    fake::allow_completion = true;
    fake::hold_publish = false;
    fake::changed.notify_all();
  }
  producer.join();
  const auto completed = fence(*one.run);
  if (!expect(third_status.ok() && completed.status.ok(),
              "completion wakes and accepts the third producer") ||
      !expect(c == std::vector<int32_t>(6, 99),
              "pipeline fence leaves output staged before RMD authorization") ||
      !expect(authorize_output_commit(*one.run, true).ok(),
              "RMD authorization commits the fixed-slot run") ||
      !expect(submit_stripe(*one.run, event(3, 3, 3, 4)).code ==
                  StatusCode::invalid_state,
              "submission is linearized after fence"))
    return false;

  fake::reset();
  fake::hold_full = true;
  std::vector<int8_t> aa = {1, 2, 3}, bb = {1, 0, 0, 1, 1, 1};
  std::vector<int32_t> cc(2);
  auto full_args = raw_args(aa, bb, cc, 1);
  auto full = execute(&full_args, Mode::full);
  if (!fake::wait([] { return fake::full_entered; }))
    return false;
  FenceResult x{}, y{};
  std::thread t1([&] { x = fence(*full.run); });
  std::thread t2([&] { y = fence(*full.run); });
  {
    std::lock_guard lock(fake::mutex);
    fake::hold_full = false;
    fake::changed.notify_all();
  }
  t1.join();
  t2.join();
  return expect(x.status.ok() && y.status.ok(),
                "concurrent lifecycle linearization");
}

bool test_startup_failure_and_destruction() {
  fake::reset();
  fake::throw_create = true;
  std::vector<int8_t> a = {1, 2, 3}, b = {1, 0, 0, 1, 1, 1};
  std::vector<int32_t> c(2);
  auto args = raw_args(a, b, c, 1);
  const auto failed = execute(&args, Mode::stripe_pipeline);
  if (!expect(failed.status.code == StatusCode::out_of_memory,
              "startup wait terminates on sticky worker failure"))
    return false;

  fake::reset();
  fake::hold_publish = true;
  ExecuteResult active;
  {
    auto scoped_args = raw_args(a, b, c, 1);
    active = execute(&scoped_args, Mode::stripe_pipeline);
  }
  if (!active.status.ok())
    return false;
  {
    auto scoped_event = event(0, 0, 1);
    if (!submit_stripe(*active.run, scoped_event).ok())
      return false;
  }
  if (!fake::wait([] { return fake::publish_entered; }))
    return false;
  {
    std::lock_guard lock(fake::mutex);
    fake::hold_publish = false;
    fake::changed.notify_all();
  }
  // Args/event objects are gone; backing buffers and scalar snapshots remain.
  active.run.reset();
  return expect(fake::one_owner,
                "active destruction preserves worker-thread ABI ownership");
}

bool test_submit_fence_orderings_and_error_stickiness() {
  fake::reset();
  fake::hold_publish = true;
  std::vector<int8_t> a = {1, 2, 3}, b = {1, 0, 0, 1, 1, 1};
  std::vector<int32_t> c(2);
  auto args = raw_args(a, b, c, 1);
  auto submitted = execute(&args, Mode::stripe_pipeline);
  if (!submitted.status.ok() ||
      !submit_stripe(*submitted.run, event(0, 0, 1)).ok() ||
      !fake::wait([] { return fake::publish_entered; }))
    return false;
  FenceResult first{}, second{};
  std::mutex gate_mutex;
  std::condition_variable gate_changed;
  size_t fence_entered = 0;
  auto do_fence = [&](FenceResult &result) {
    {
      std::lock_guard lock(gate_mutex);
      ++fence_entered;
      gate_changed.notify_all();
    }
    result = fence(*submitted.run);
  };
  std::thread one(do_fence, std::ref(first));
  std::thread two(do_fence, std::ref(second));
  {
    std::unique_lock lock(gate_mutex);
    if (!gate_changed.wait_for(lock, std::chrono::seconds(5),
                               [&] { return fence_entered == 2; }))
      return false;
  }
  if (!expect(RunTestAccess::wait_for_closing(*submitted.run),
              "fence reaches the observed closing transition") ||
      !expect(submit_stripe(*submitted.run, event(1, 1, 1)).code ==
                  StatusCode::invalid_state,
              "fence-before-submit ordering rejects new submission"))
    return false;
  {
    std::lock_guard lock(fake::mutex);
    fake::hold_publish = false;
    fake::changed.notify_all();
  }
  one.join();
  two.join();
  if (!expect(first.status.ok() && second.status.ok(),
              "truly overlapping fences share one terminal result") ||
      !expect(authorize_output_commit(*submitted.run, true).ok(),
              "one post-fence authorization commits concurrent fence result"))
    return false;

  fake::reset();
  fake::fail_poll = true;
  auto errored = execute(&args, Mode::stripe_pipeline);
  if (!errored.status.ok() || !submit_stripe(*errored.run, event(0, 0, 1)).ok())
    return false;
  FenceResult error_one{}, error_two{};
  std::thread e1([&] { error_one = fence(*errored.run); });
  std::thread e2([&] { error_two = fence(*errored.run); });
  e1.join();
  e2.join();
  if (!expect(error_one.status.code == StatusCode::execution_failure &&
                  error_two.status.code == error_one.status.code,
              "poll error is sticky across concurrent fences"))
    return false;

  fake::reset();
  fake::fail_progress = true;
  auto progress_error = execute(&args, Mode::stripe_pipeline);
  if (!progress_error.status.ok() ||
      !submit_stripe(*progress_error.run, event(0, 0, 1)).ok())
    return false;
  FenceResult progress_one{}, progress_two{};
  std::thread p1([&] { progress_one = fence(*progress_error.run); });
  std::thread p2([&] { progress_two = fence(*progress_error.run); });
  p1.join();
  p2.join();
  return expect(progress_one.status.code == StatusCode::execution_failure &&
                    progress_two.status.code == progress_one.status.code,
                "progress error is sticky across concurrent fences");
}

bool test_inflight_progress_and_long_valid_completion() {
  fake::reset();
  fake::required_progress_cycles = 3;
  std::vector<int8_t> a = {1, 2, 3, 4, 5, 6}, b = {1, 0, 0, 1, 1, 1};
  std::vector<int32_t> c(4);
  auto args = raw_args(a, b, c);
  auto run = execute(&args, Mode::stripe_pipeline, {4096});
  if (!run.status.ok() || !submit_stripe(*run.run, event(0, 0, 1)).ok())
    return false;
  if (!expect(RunTestAccess::wait_for_completion(*run.run, 1),
              "capacity wait observes a matched completion poll") ||
      !expect(submit_stripe(*run.run, event(1, 1, 2)).ok(),
              "frontend capacity is released after the completion poll") ||
      !expect(fence(*run.run).status.ok(),
              "multi-cycle pipeline fences without deadlock") ||
      !expect(authorize_output_commit(*run.run, true).ok(),
              "successful RMD authorization commits completed pipeline"))
    return false;

  fake::reset();
  fake::required_progress_cycles = 5001;
  std::vector<int8_t> long_a = {1, 2, 3};
  std::vector<int32_t> long_c(2);
  auto long_args = raw_args(long_a, b, long_c, 1);
  auto long_run = execute(&long_args, Mode::stripe_pipeline, {4096});
  if (!long_run.status.ok() ||
      !submit_stripe(*long_run.run, event(0, 0, 1)).ok())
    return false;
  const auto long_fence = fence(*long_run.run);
  return expect(
      long_fence.status.ok() &&
          authorize_output_commit(*long_run.run, true).ok() &&
          RunTestAccess::inspect(*long_run.run).completion_generation == 1,
      "valid completion beyond 4096 logical cycles is not rejected");
}

bool test_continuous_refill_completion_generation() {
  fake::reset();
  constexpr size_t stripe_count = 65538;
  std::vector<int8_t> a(stripe_count * 3, 1);
  std::vector<int8_t> b = {1, 0, 0, 1, 1, 1};
  std::vector<int32_t> c(stripe_count * 2);
  auto args = raw_args(a, b, c, stripe_count);
  auto run = execute(&args, Mode::stripe_pipeline, {1});
  if (!run.status.ok())
    return false;
  RunTestAccess::enable_completion_gate(*run.run);
  if (!submit_stripe(*run.run, event(0, 0, 1)).ok()) {
    RunTestAccess::disable_completion_gate(*run.run);
    return false;
  }
  bool sequence_ok = true;
  for (size_t completed = 1; completed <= stripe_count; ++completed) {
    if (!expect(RunTestAccess::wait_for_completion(*run.run, completed),
                "bounded wait observes each matched completion")) {
      sequence_ok = false;
      break;
    }
    if (completed < stripe_count &&
        !expect(
            submit_stripe(*run.run, event(completed, completed, completed + 1))
                .ok(),
            "capacity-one refill follows the actual completion poll")) {
      sequence_ok = false;
      break;
    }
    RunTestAccess::release_completion_gate(*run.run);
  }
  RunTestAccess::disable_completion_gate(*run.run);
  const auto done = fence(*run.run);
  const auto authorized = authorize_output_commit(*run.run, true);
  return sequence_ok &&
         expect(done.status.ok() && authorized.ok(),
                "continuous complete-then-refill does not falsely stall") &&
         expect(RunTestAccess::inspect(*run.run).completion_generation ==
                    stripe_count,
                "worker counts every matched completion monotonically");
}

bool test_args_and_inputs_can_expire_after_execute() {
  fake::reset();
  fake::hold_full = true;
  std::vector<int32_t> destination(2, 777);
  ExecuteResult started;
  {
    std::vector<int8_t> activation = {1, 2, 3};
    std::vector<int8_t> weights = {1, 0, 0, 1, 1, 1};
    auto args = raw_args(activation, weights, destination, 1);
    started = execute(&args, Mode::full);
  }
  if (!expect(
          started.status.ok() && fake::wait([] { return fake::full_entered; }),
          "worker retains activation and copied weights after args expire") ||
      !expect(destination == std::vector<int32_t>(2, 777),
              "expired caller inputs do not expose partial output"))
    return false;
  {
    std::lock_guard lock(fake::mutex);
    fake::hold_full = false;
    fake::changed.notify_all();
  }
  return expect(fence(*started.run).status.ok() &&
                    destination == std::vector<int32_t>({4, 5}),
                "fence succeeds after caller args and input buffers expire");
}

bool test_pipeline_args_and_inputs_expire_before_submit() {
  fake::reset();
  std::vector<int32_t> destination(2, 0x23456789);
  ExecuteResult started;
  {
    std::vector<int8_t> activation = {1, 2, 3};
    std::vector<int8_t> weights = {1, 0, 0, 1, 1, 1};
    auto args = raw_args(activation, weights, destination, 1);
    started = execute(&args, Mode::stripe_pipeline, {64});
  }
  if (!expect(started.status.ok(),
              "pipeline execute snapshots every later-used args field") ||
      !expect(submit_stripe(*started.run, event(0, 0, 1)).ok(),
              "stripe submission does not dereference expired args"))
    return false;
  const auto done = fence(*started.run);
  return expect(
             done.status.ok() &&
                 destination == std::vector<int32_t>(2, 0x23456789),
             "pipeline fence succeeds with expired inputs and staged output") &&
         expect(authorize_output_commit(*started.run, true).ok() &&
                    destination == std::vector<int32_t>({4, 5}),
                "post-RMD authorization commits expired-input result");
}

bool test_exsia_metadata_is_published_explicitly_after_args_expire() {
  fake::reset();
  fake::hold_publish = true;
  float destination[2] = {73.0f, 73.0f};
  ExecuteResult started;
  {
    ggml_gemmini_args_t args{};
    args.I = 1;
    args.J = 2;
    args.K = 32;
    if (!args.A.allocate(1, 32, IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS))
      return false;
    args.activation_rows_per_stripe = 1;
    args.f_out = destination;
    args.stride_f_out = 2;
    args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_h1;
    std::vector<block_q8_h1> blocks(2);
    args.q8_h1_blocks = blocks.data();
    args.q8_h1_block_count = blocks.size();
    args.q8_h1_rows = 2;
    args.blocks_per_row = 1;
    args.act_quant.storage().emplace<exsia::Meta>();
    started = execute(&args, Mode::stripe_pipeline, {64});
  }
  if (!started.status.ok() ||
      !expect(submit_stripe(*started.run, event(0, 0, 1)).code ==
                  StatusCode::invalid_contract,
              "ExSIA publication rejects absent post-fold theta") ||
      !expect(submit_stripe(*started.run, event(0, 0, 1), {true, 0}).ok(),
              "ExSIA publication copies explicit theta after args expire") ||
      !fake::wait([] { return fake::publish_entered; }))
    return false;
  {
    std::lock_guard lock(fake::mutex);
    fake::fail_progress = true;
    fake::hold_publish = false;
    fake::changed.notify_all();
  }
  return expect(fence(*started.run).status.code ==
                        StatusCode::execution_failure &&
                    destination[0] == 73.0f && destination[1] == 73.0f,
                "later worker failure preserves ExSIA destination sentinel");
}

bool test_rmd_finalizes_frontend_stage_before_authorization() {
  fake::reset();
  float destination[2] = {91.0f, 91.0f};
  ggml_gemmini_args_t args{};
  args.I = 1;
  args.J = 2;
  args.K = 32;
  if (!args.A.allocate(1, 32, IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS))
    return false;
  args.activation_rows_per_stripe = 1;
  args.f_out = destination;
  args.stride_f_out = 2;
  args.col_stride_f_out = 1;
  args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_h1;
  std::vector<block_q8_h1> blocks(2);
  args.q8_h1_blocks = blocks.data();
  args.q8_h1_block_count = blocks.size();
  args.q8_h1_rows = 2;
  args.blocks_per_row = 1;
  args.act_quant.storage().emplace<exsia::Meta>();

  auto started = execute(&args, Mode::stripe_pipeline, {64});
  if (!started.status.ok() ||
      !expect(acquire_pipeline_output_stage(*started.run).status.code ==
                  StatusCode::invalid_state,
              "RMD staging is unavailable before fence") ||
      !submit_stripe(*started.run, event(0, 0, 1), {true, 0}).ok() ||
      !fence(*started.run).status.ok())
    return false;

  auto stage = acquire_pipeline_output_stage(*started.run);
  if (!expect(stage.status.ok() && stage.data != nullptr &&
                  stage.element_count >= 2,
              "successful fence exposes frontend-owned RMD staging") ||
      !expect(destination[0] == 91.0f && destination[1] == 91.0f,
              "RMD staging access preserves borrowed destination"))
    return false;
  stage.data[0] = 17.0f;
  stage.data[1] = 23.0f;
  return expect(authorize_output_commit(*started.run, true).ok() &&
                    destination[0] == 17.0f && destination[1] == 23.0f,
                "authorization commits finalized frontend staging") &&
         expect(acquire_pipeline_output_stage(*started.run).status.code ==
                    StatusCode::invalid_state,
                "committed staging cannot be acquired again");
}

bool test_rmd_commit_authorization_ordering() {
  auto make_run = [](std::vector<int32_t> &destination) {
    std::vector<int8_t> activation = {1, 2, 3};
    std::vector<int8_t> weights = {1, 0, 0, 1, 1, 1};
    auto args = raw_args(activation, weights, destination, 1);
    return execute(&args, Mode::stripe_pipeline, {64});
  };

  fake::reset();
  std::vector<int32_t> success_destination(2, 31337);
  auto success = make_run(success_destination);
  if (!success.status.ok() ||
      !submit_stripe(*success.run, event(0, 0, 1)).ok() ||
      !expect(authorize_output_commit(*success.run, true).code ==
                  StatusCode::invalid_state,
              "RMD cannot authorize output before simulator fence") ||
      !expect(success_destination == std::vector<int32_t>(2, 31337),
              "early authorization cannot mutate destination"))
    return false;
  if (!expect(fence(*success.run).status.ok() &&
                  success_destination == std::vector<int32_t>(2, 31337),
              "successful fence remains staged pending RMD"))
    return false;
  if (!expect(authorize_output_commit(*success.run, true).ok() &&
                  success_destination == std::vector<int32_t>({4, 5}),
              "successful RMD authorization commits after fence"))
    return false;

  fake::reset();
  std::vector<int32_t> failure_destination(2, 42424);
  auto failure = make_run(failure_destination);
  if (!failure.status.ok() ||
      !submit_stripe(*failure.run, event(0, 0, 1)).ok() ||
      !fence(*failure.run).status.ok())
    return false;
  const auto rejected = authorize_output_commit(*failure.run, false);
  const auto retry = authorize_output_commit(*failure.run, true);
  return expect(rejected.code == StatusCode::execution_failure &&
                    retry.code == rejected.code,
                "failed RMD authorization is sticky") &&
         expect(failure_destination == std::vector<int32_t>(2, 42424),
                "failed RMD permanently preserves destination sentinel");
}

bool test_blocked_producer_failure_is_transactional() {
  fake::reset();
  fake::hold_publish = true;
  std::vector<int8_t> activation(9, 1);
  std::vector<int8_t> weights = {1, 0, 0, 1, 1, 1};
  std::vector<int32_t> destination(6, 0x12345678);
  auto args = raw_args(activation, weights, destination, 3);
  auto started = execute(&args, Mode::stripe_pipeline, {64});
  if (!started.status.ok() ||
      !submit_stripe(*started.run, event(0, 0, 1)).ok() ||
      !submit_stripe(*started.run, event(1, 1, 2)).ok() ||
      !fake::wait([] { return fake::publish_entered; }))
    return false;
  Status producer_status{};
  std::thread producer(
      [&] { producer_status = submit_stripe(*started.run, event(2, 2, 3)); });
  if (!expect(RunTestAccess::wait_for_blocked_submit(*started.run, 1),
              "third capacity-two producer reaches deterministic wait"))
    return false;
  {
    std::lock_guard lock(fake::mutex);
    fake::fail_progress = true;
    fake::hold_publish = false;
    fake::changed.notify_all();
  }
  producer.join();
  const auto done = fence(*started.run);
  return expect(producer_status.code == StatusCode::execution_failure &&
                    done.status.code == producer_status.code,
                "worker failure wakes blocked producer with sticky failure") &&
         expect(destination == std::vector<int32_t>(6, 0x12345678),
                "failed run leaves destination sentinel intact");
}

enum class FullFailure { create, execute };
enum class StripeFailure { create, begin, publish, progress, poll, finish };

bool resources_balanced(bool expect_stream) {
  return fake::sim_created.load() == fake::sim_destroyed.load() &&
         fake::stream_created.load() == fake::stream_destroyed.load() &&
         (expect_stream ? fake::stream_created.load() == 1
                        : fake::stream_created.load() == 0);
}

bool test_full_failure_matrix() {
  for (const auto failure : {FullFailure::create, FullFailure::execute}) {
    fake::reset();
    fake::throw_create = failure == FullFailure::create;
    fake::fail_full = failure == FullFailure::execute;
    std::vector<int8_t> activation = {1, 2, 3};
    std::vector<int8_t> weights = {1, 0, 0, 1, 1, 1};
    std::vector<int32_t> destination(2, 0x34567812);
    auto args = raw_args(activation, weights, destination, 1);
    auto started = execute(&args, Mode::full);
    if (!expect(started.status.ok(), "full failure starts asynchronously"))
      return false;
    const auto done = fence(*started.run);
    const auto expected = failure == FullFailure::create
                              ? StatusCode::out_of_memory
                              : StatusCode::execution_failure;
    if (!expect(done.status.code == expected,
                "full boundary returns its typed sticky error") ||
        !expect(fence(*started.run).status.code == expected,
                "full boundary error is stable across repeated fence") ||
        !expect(destination == std::vector<int32_t>(2, 0x34567812),
                "full failure preserves caller sentinel") ||
        !expect(resources_balanced(false),
                "full failure destroys every simulator resource"))
      return false;
  }
  return true;
}

bool test_stripe_failure_matrix() {
  for (const auto failure :
       {StripeFailure::create, StripeFailure::begin, StripeFailure::publish,
        StripeFailure::progress, StripeFailure::poll, StripeFailure::finish}) {
    fake::reset();
    fake::throw_create = failure == StripeFailure::create;
    fake::fail_begin = failure == StripeFailure::begin;
    fake::fail_publish = failure == StripeFailure::publish;
    fake::fail_progress = failure == StripeFailure::progress;
    fake::fail_poll = failure == StripeFailure::poll;
    fake::fail_finish = failure == StripeFailure::finish;
    std::vector<int8_t> activation = {1, 2, 3};
    std::vector<int8_t> weights = {1, 0, 0, 1, 1, 1};
    std::vector<int32_t> destination(2, 0x45678123);
    auto args = raw_args(activation, weights, destination, 1);
    auto started = execute(&args, Mode::stripe_pipeline, {64});
    const bool startup_failure =
        failure == StripeFailure::create || failure == StripeFailure::begin;
    StatusCode code = started.status.code;
    if (!startup_failure) {
      if (!expect(started.status.ok(), "stripe boundary starts") ||
          !expect(submit_stripe(*started.run, event(0, 0, 1)).ok(),
                  "stripe boundary accepts publication"))
        return false;
      code = fence(*started.run).status.code;
      if (!expect(fence(*started.run).status.code == code,
                  "stripe boundary error is stable across repeated fence"))
        return false;
    }
    const auto expected = failure == StripeFailure::create
                              ? StatusCode::out_of_memory
                              : StatusCode::execution_failure;
    if (!expect(code == expected, "stripe boundary returns typed error") ||
        !expect(destination == std::vector<int32_t>(2, 0x45678123),
                "stripe failure preserves caller sentinel") ||
        !expect(resources_balanced(!startup_failure &&
                                   failure != StripeFailure::begin),
                "stripe failure destroys stream and simulator resources"))
      return false;
  }
  return true;
}

size_t resident_bytes() {
#if defined(__APPLE__)
  mach_task_basic_info_data_t info{};
  mach_msg_type_number_t count = MACH_TASK_BASIC_INFO_COUNT;
  if (task_info(mach_task_self(), MACH_TASK_BASIC_INFO,
                reinterpret_cast<task_info_t>(&info), &count) == KERN_SUCCESS)
    return static_cast<size_t>(info.resident_size);
#endif
  return 0;
}

bool test_invalid_reuse_is_bounded() {
  fake::reset();
  std::vector<int8_t> activation = {1, 2, 3};
  std::vector<int8_t> weights = {1, 0, 0, 1, 1, 1};
  std::vector<int32_t> destination(2, 0x56781234);
  auto args = raw_args(activation, weights, destination, 1);
  args.A.bits = IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS == 16 ? 8 : 16;
  const auto warmup = execute(&args);
  if (!expect(warmup.status.code == StatusCode::invalid_argument,
              "invalid width warmup rejects before worker"))
    return false;
  const size_t before = resident_bytes();
  for (size_t iteration = 0; iteration < 1000; ++iteration) {
    const auto rejected = execute(&args);
    if (rejected.status.code != StatusCode::invalid_argument)
      return expect(false, "reused invalid input returns the same typed error");
  }
  const size_t after = resident_bytes();
  return expect(fake::sim_created.load() == 0 &&
                    fake::stream_created.load() == 0,
                "invalid input creates no simulator, stream, or worker") &&
         expect(destination == std::vector<int32_t>(2, 0x56781234),
                "reused invalid input preserves caller sentinel") &&
         expect(before == 0 || after <= before + 8 * 1024 * 1024,
                "1000 invalid calls keep observed resident memory bounded");
}

bool test_logical_stall_bound() {
  fake::reset();
  fake::raw_pressure_forever = true;
  std::vector<int8_t> a = {1, 2, 3}, b = {1, 0, 0, 1, 1, 1};
  std::vector<int32_t> c(2);
  auto args = raw_args(a, b, c, 1);
  auto run = execute(&args, Mode::stripe_pipeline, {8});
  if (!run.status.ok() || !submit_stripe(*run.run, event(0, 0, 1)).ok())
    return false;
  const auto done = fence(*run.run);
  return expect(done.status.code == StatusCode::execution_failure,
                "deterministic logical stall limit") &&
         expect(fake::progress_calls == 65537,
                "small stalled work retains the 65536-cycle floor");
}

bool test_forward_progress_is_not_stall() {
  fake::reset();
  fake::forward_progress_period = 32'768;
  fake::required_progress_cycles = 1'000'000;
  std::vector<int8_t> activation = {1, 2, 3};
  std::vector<int8_t> weights = {1, 0, 0, 1, 1, 1};
  std::vector<int32_t> output(2);
  auto args = raw_args(activation, weights, output, 1);
  auto run = execute(&args, Mode::stripe_pipeline);
  if (!run.status.ok() ||
      !submit_stripe(*run.run, event(0, 0, 1)).ok())
    return false;
  const auto done = fence(*run.run);
  return expect(done.status.ok() &&
                    authorize_output_commit(*run.run, true).ok(),
                "forward progress is not mistaken for a logical stall") &&
         expect(fake::progress_calls == fake::required_progress_cycles,
                "completion follows one million progressing cycles");
}

bool test_max_stall_limit_disables_watchdog() {
  fake::reset();
  fake::required_progress_cycles = 70'000;
  std::vector<int8_t> activation = {1, 2, 3};
  std::vector<int8_t> weights = {1, 0, 0, 1, 1, 1};
  std::vector<int32_t> output(2);
  auto args = raw_args(activation, weights, output, 1);
  auto run = execute(&args, Mode::stripe_pipeline,
                     {std::numeric_limits<uint64_t>::max()});
  if (!run.status.ok() ||
      !submit_stripe(*run.run, event(0, 0, 1)).ok())
    return false;
  const auto done = fence(*run.run);
  return expect(done.status.ok(),
                "UINT64_MAX disables the logical stall watchdog") &&
         expect(fake::progress_calls == fake::required_progress_cycles,
                "disabled watchdog permits completion beyond ordinary limit");
}

bool test_compiled_identity() {
  return expect(compiled_activation_bits() ==
                    IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS,
                "frontend reports its activation width") &&
         expect(compiled_weight_bits() == GGML_GEMMINI_WEIGHT_BITS,
                "frontend reports its weight width") &&
         expect(compiled_dim() == DIM, "frontend reports its RTL DIM");
}
} // namespace

int main(int argc, char **argv) {
  if (argc == 2) {
    const std::string_view selected(argv[1]);
    const bool selected_ok =
        selected == "blocked_producer_fence_failure"
            ? test_blocked_producer_failure_is_transactional()
        : selected == "caller_args_expire"
            ? test_args_and_inputs_can_expire_after_execute()
        : selected == "pipeline_args_expire"
            ? test_pipeline_args_and_inputs_expire_before_submit()
        : selected == "fixed_two_slots"
            ? test_backpressure_runid_incomplete_and_concurrent()
        : selected == "exsia_postfold_metadata"
            ? test_exsia_metadata_is_published_explicitly_after_args_expire()
        : selected == "rmd_commit_authorization"
            ? test_rmd_commit_authorization_ordering()
        : selected == "rmd_terminal_staging"
            ? test_rmd_finalizes_frontend_stage_before_authorization()
        : selected == "full_failure_matrix"   ? test_full_failure_matrix()
        : selected == "stripe_failure_matrix" ? test_stripe_failure_matrix()
        : selected == "invalid_reuse"         ? test_invalid_reuse_is_bounded()
        : selected == "forward_progress_watchdog"
            ? test_forward_progress_is_not_stall()
        : selected == "disabled_progress_watchdog"
            ? test_max_stall_limit_disables_watchdog()
        : selected == "native_q4_q16_provider"
            ? test_native_q4_q16_provider_golden()
        : selected == "provider_int64_scaling" ||
                  selected == "cross_mode_oracle"
            ? test_provider_int64_scaling_full_pipeline()
            : false;
    if (selected_ok)
      std::printf("IM2P Gemmini frontend case %s: PASS\n", argv[1]);
    else
      std::fprintf(stderr, "IM2P Gemmini frontend case %s: FAIL\n", argv[1]);
    return selected_ok ? 0 : 1;
  }
  if (argc != 1)
    return 2;
  const bool ok = GGML_GEMMINI_WEIGHT_BITS != 8
      ? (test_compiled_identity() && test_native_q4_q16_provider_golden())
      : (test_compiled_identity() &&
         test_native_q4_q16_provider_golden() &&
         test_native_h1_provider_start_contract() &&
      test_provider_int64_scaling_full_pipeline() &&
      test_rejected_routes_do_not_execute() &&
      test_mode_and_raw_scale_contract() &&
      test_full_golden_and_scalar_snapshot() &&
      test_multiwidth_activation_snapshot_validation() &&
      test_tile_normalization_validation() && test_pipeline_lifecycle() &&
      test_backpressure_runid_incomplete_and_concurrent() &&
      test_startup_failure_and_destruction() &&
      test_submit_fence_orderings_and_error_stickiness() &&
      test_inflight_progress_and_long_valid_completion() &&
      test_continuous_refill_completion_generation() &&
      test_args_and_inputs_can_expire_after_execute() &&
      test_pipeline_args_and_inputs_expire_before_submit() &&
      test_exsia_metadata_is_published_explicitly_after_args_expire() &&
      test_rmd_finalizes_frontend_stage_before_authorization() &&
      test_rmd_commit_authorization_ordering() &&
      test_blocked_producer_failure_is_transactional() &&
      test_full_failure_matrix() && test_stripe_failure_matrix() &&
      test_invalid_reuse_is_bounded() &&
      test_forward_progress_is_not_stall() &&
      test_max_stall_limit_disables_watchdog() &&
      test_logical_stall_bound());
  if (ok)
    std::puts("IM2P Gemmini frontend: PASS");
  return ok ? 0 : 1;
}
