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
#include <functional>
#include <limits>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>
#include <thread>
#include <type_traits>
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
bool malformed_completion_id = false;
bool malformed_completion_rows = false;
bool malformed_completion_context = false;
bool malformed_completion_duration = false;
bool malformed_completion_order = false;
bool throw_create = false;
size_t create_attempts = 0;
size_t fail_create_attempt = 0;
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
size_t raw_in_flight = 0;
size_t max_raw_in_flight = 0;
size_t sim_count_at_first_publish = 0;
std::vector<std::string> semantic_trace;
std::vector<im2p_sim_t *> sim_handles;
std::vector<std::thread::id> sim_create_threads;
std::vector<std::thread::id> sim_destroy_threads;

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
  malformed_completion_id = false;
  malformed_completion_rows = false;
  malformed_completion_context = false;
  malformed_completion_duration = false;
  malformed_completion_order = false;
  throw_create = false;
  create_attempts = 0;
  fail_create_attempt = 0;
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
  raw_in_flight = 0;
  max_raw_in_flight = 0;
  sim_count_at_first_publish = 0;
  semantic_trace.clear();
  sim_handles.clear();
  sim_create_threads.clear();
  sim_destroy_threads.clear();
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
  std::deque<im2p_stripe_completion_extended_t> done;
  size_t serviced = 0;
  uint64_t progress_count = 0;
};

extern "C" {
im2p_sim_t *im2p_sim_create(void) {
  fake::abi_call();
  bool fail = false;
  {
    std::lock_guard lock(fake::mutex);
    ++fake::create_attempts;
    fail = fake::throw_create ||
           fake::create_attempts == fake::fail_create_attempt;
  }
  if (fail)
    throw std::bad_alloc();
  auto *sim = new im2p_sim;
  {
    std::lock_guard lock(fake::mutex);
    fake::sim_handles.push_back(sim);
    fake::sim_create_threads.push_back(std::this_thread::get_id());
  }
  ++fake::sim_created;
  return sim;
}
void im2p_sim_destroy(im2p_sim_t *p) {
  fake::abi_call();
  {
    std::lock_guard lock(fake::mutex);
    fake::sim_destroy_threads.push_back(std::this_thread::get_id());
  }
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
  ++fake::raw_in_flight;
  fake::max_raw_in_flight =
      std::max(fake::max_raw_in_flight, fake::raw_in_flight);
  if (fake::publish_count == 1)
    fake::sim_count_at_first_publish = fake::sim_created.load();
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
    const uint64_t publish_cycle = UINT64_C(0x1100000000000000) +
                                   s.stripe_id * UINT64_C(0x101);
    const uint64_t completion_cycle = UINT64_C(0x2200000000000000) +
                                      s.stripe_id * UINT64_C(0x1001);
    stream->done.push_back({{s.stripe_id, s.i_start, s.rows, s.context},
                            publish_cycle, completion_cycle,
                            completion_cycle - publish_cycle});
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
  *out = stream->done.front().base;
  stream->done.pop_front();
  return 1;
}
int im2p_poll_completed_extended(
    im2p_stream_t *stream, im2p_stripe_completion_extended_t *out) {
  fake::abi_call();
  std::lock_guard lock(fake::mutex);
  if (fake::fail_poll)
    return IM2P_ERROR;
  if (stream->done.empty())
    return 0;
  if (fake::malformed_completion_order && stream->done.size() > 1) {
    *out = stream->done[1];
    stream->done.erase(stream->done.begin() + 1);
  } else {
    *out = stream->done.front();
    stream->done.pop_front();
  }
  if (fake::malformed_completion_id)
    ++out->base.stripe_id;
  if (fake::malformed_completion_rows)
    ++out->base.rows;
  if (fake::malformed_completion_context)
    ++out->base.context;
  if (fake::malformed_completion_duration)
    ++out->publish_to_completion_cycles;
  if (fake::raw_in_flight != 0)
    --fake::raw_in_flight;
  fake::semantic_trace.push_back("D" +
                                 std::to_string(out->base.stripe_id));
  fake::changed.notify_all();
  return 1;
}
int im2p_finish_stream_extended(im2p_stream_t *,
                                im2p_work_stats_extended_t *stats) {
  fake::abi_call();
  std::lock_guard lock(fake::mutex);
  ++fake::finish_count;
  if (fake::fail_finish)
    return IM2P_ERROR;
  const size_t tile_rows = fake::work_desc.tile_i_rows;
  const size_t tile_columns = fake::work_desc.tile_j_columns;
  const uint64_t output_works =
      ((fake::work_desc.m + tile_rows - 1) / tile_rows) *
      ((fake::work_desc.n + tile_columns - 1) / tile_columns);
  const uint64_t fragments_per_work =
      (fake::work_desc.k + DIM - 1) / DIM;
  stats->base.completed_fragments = output_works * fragments_per_work;
  stats->base.completed_output_tiles = output_works;
  stats->base.completed_stripes = fake::published.size();
  stats->base.stripes_published = fake::published.size();
  for (const auto &publication : fake::published)
    stats->base.stripe_rows_published += publication.rows;
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

bool test_q8_hp1_native_extent_contract() {
  fake::reset();
  const std::array<float, 1> untouched = {123.0f};
  std::array<float, 1> output = untouched;
  std::vector<block_q8_hp1> hp1(1);
  hp1[0].qs[0] = 7;
  hp1[0].m = 0;
  hp1[0].channel_scale = 1.0f;
  fake::provider_exact_values = {7};
  ggml_gemmini_args_t base{};
  base.I = 1;
  base.J = 1;
  base.K = 32;
  if (!base.A.allocate(1, 32, IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS))
    return false;
  base.f_out = output.data();
  base.stride_f_out = 1;
  base.col_stride_f_out = 1;
  base.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1;
  base.q8_hp1_blocks = hp1.data();
  base.q8_hp1_block_count = hp1.size();
  base.q8_hp1_blocks_per_row = 1;
  base.native_weight_bytes = hp1.size() * sizeof(block_q8_hp1);
  base.act_quant.storage()
      .emplace<ggml::gemmini::quants::act::tensor::Meta>()
      .scale = 1.0f;
  if (!expect(base.has_q8_hp1_im2p_contract(),
              "Q8_HP1 provider fixture satisfies the native contract"))
    return false;

  auto started = execute(&base);
  if (!expect(started.status.ok(), "Q8_HP1 provider route starts") ||
      !expect(started.run != nullptr, "Q8_HP1 provider route returns a run") ||
      !expect(fence(*started.run).status.ok(), "Q8_HP1 provider route fences"))
    return false;

  const auto &descriptor = fake::full_desc;
  if (!expect(fake::sim_created == 1 && fake::provider_full_count == 1 &&
                  fake::provider_work_count == 0 &&
                  fake::provider_delivered_values == fake::provider_exact_values &&
                  output == std::array<float, 1>{7.0f} &&
                  descriptor.abi_version == IM2P_ABI_VERSION &&
                  descriptor.activation_bits ==
                      IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS &&
                  descriptor.weight_bits == GGML_GEMMINI_WEIGHT_BITS &&
                  descriptor.weight_storage_bytes == 1 &&
                  descriptor.weight_row_stride_bytes == 1 &&
                  descriptor.output_row_stride == 1 &&
                  descriptor.block_size == 32 &&
                  descriptor.vector_op == IM2P_VECTOR_EXTERNAL &&
                  descriptor.provider.context != nullptr &&
                  descriptor.provider.read_weight_i8 != nullptr &&
                  descriptor.provider.read_weight_i16 == nullptr &&
                  descriptor.provider.read_scale != nullptr &&
                  descriptor.provider.write_output != nullptr,
              "Q8_HP1 provider fixture produces the exact output and descriptor"))
    return false;

  auto reject = [&](auto mutate, const char *message) {
    auto mutated = base;
    output = untouched;
    mutate(mutated);
    fake::reset();
    const auto result = execute(&mutated);
    return expect(!mutated.has_q8_hp1_im2p_contract() &&
                      result.status.code == StatusCode::invalid_contract &&
                      result.status.route == Route::q8_hp1 &&
                      fake::sim_created == 0 && fake::stream_created == 0 &&
                      fake::provider_full_count == 0 &&
                      fake::provider_work_count == 0 && output == untouched,
                  message);
  };

  if (!reject([](auto &x) { x.q8_hp1_block_count = 0; },
              "Q8_HP1 block-count underflow rejects before execution") ||
      !reject([](auto &x) { x.native_weight_bytes = 0; },
              "Q8_HP1 native extent underflow rejects before execution"))
    return false;

  return expect(output == untouched,
                "Q8_HP1 invalid contracts leave the destination untouched");
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
    args.native_weight_bytes = args.native_block_count * sizeof(*block);
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
    args.native_weight_bytes = args.native_block_count * sizeof(*block);
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
  args.native_weight_bytes = args.native_block_count * sizeof(q4_h0);
#else
  args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q16_h0;
  args.q16_h0_blocks = &q16_h0;
  args.native_weight_bytes = args.native_block_count * sizeof(q16_h0);
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
  x.native_weight_bytes = sizeof(blocks);
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
    args.native_weight_bytes = weights.size() * sizeof(block_q8_h1);
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
      return done.status.code == StatusCode::execution_failure &&
             done.stripe_rtl_timings.empty();
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
  one_row_args.native_weight_bytes =
      one_row_weights.size() * sizeof(block_q8_h1);
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
  const auto *stable_data = done.stripe_rtl_timings.data;
  const bool exact_timings =
      done.stripe_rtl_timings.size == 2 &&
      done.stripe_rtl_timings[0].run_id == 77 &&
      done.stripe_rtl_timings[0].stripe_id == 0 &&
      done.stripe_rtl_timings[0].slot == 9 &&
      done.stripe_rtl_timings[0].row_begin == 0 &&
      done.stripe_rtl_timings[0].row_end == 1 &&
      done.stripe_rtl_timings[0].publish_cycle == UINT64_C(0x1100000000000000) &&
      done.stripe_rtl_timings[0].completion_cycle == UINT64_C(0x2200000000000000) &&
      done.stripe_rtl_timings[0].publish_to_completion_cycles ==
          UINT64_C(0x1100000000000000) &&
      done.stripe_rtl_timings[1].run_id == 77 &&
      done.stripe_rtl_timings[1].stripe_id == 1 &&
      done.stripe_rtl_timings[1].slot == 10 &&
      done.stripe_rtl_timings[1].row_begin == 1 &&
      done.stripe_rtl_timings[1].row_end == 2 &&
      done.stripe_rtl_timings[1].publish_cycle == UINT64_C(0x1100000000000101) &&
      done.stripe_rtl_timings[1].completion_cycle == UINT64_C(0x2200000000001001) &&
      done.stripe_rtl_timings[1].publish_to_completion_cycles ==
          UINT64_C(0x1100000000000f00);
  return expect(done.status.ok() && again.status.code == done.status.code,
                "sticky idempotent fence") &&
         expect(exact_timings,
                "ordered timing view preserves run, stripe, slot, rows, and cycles") &&
         expect(again.stripe_rtl_timings.data == stable_data &&
                    again.stripe_rtl_timings.size == 2,
                "repeated fence returns the same immutable timing view") &&
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
  std::vector<int8_t> a(15), b(6);
  std::vector<int32_t> c(10, 99);
  auto args = raw_args(a, b, c, 5);
  args.activation_rows_per_stripe = 2;
  auto one = execute(&args, Mode::stripe_pipeline, {32});
  const auto initial = RunTestAccess::inspect(*one.run);
  if (!expect(initial.timing_size == 0 && initial.timing_capacity == 3,
              "pipeline reserves exactly the canonical stripe count") ||
      !expect(submit_stripe(*one.run, event(0, 0, 2, 4)).ok() &&
                  submit_stripe(*one.run, event(1, 2, 4, 4)).ok(),
              "exactly two producer slots accept without blocking") ||
      !expect(submit_stripe(*one.run, event(2, 4, 5, 5)).code ==
                  StatusCode::invalid_argument,
              "run id validation precedes the capacity wait"))
    return false;
  Status third_status{};
  std::thread producer(
      [&] { third_status = submit_stripe(*one.run, event(2, 4, 5, 4)); });
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
  const bool ordered_tail_timings =
      completed.stripe_rtl_timings.size == 3 &&
      completed.stripe_rtl_timings[0].run_id == 4 &&
      completed.stripe_rtl_timings[0].stripe_id == 0 &&
      completed.stripe_rtl_timings[0].row_begin == 0 &&
      completed.stripe_rtl_timings[0].row_end == 2 &&
      completed.stripe_rtl_timings[1].run_id == 4 &&
      completed.stripe_rtl_timings[1].stripe_id == 1 &&
      completed.stripe_rtl_timings[1].row_begin == 2 &&
      completed.stripe_rtl_timings[1].row_end == 4 &&
      completed.stripe_rtl_timings[2].run_id == 4 &&
      completed.stripe_rtl_timings[2].stripe_id == 2 &&
      completed.stripe_rtl_timings[2].row_begin == 4 &&
      completed.stripe_rtl_timings[2].row_end == 5;
  if (!expect(third_status.ok() && completed.status.ok(),
              "completion wakes and accepts the third producer") ||
      !expect(ordered_tail_timings,
              "accepted-only timings stay ordered through raw backpressure and one-row tail") ||
      !expect(c == std::vector<int32_t>(10, 99),
              "pipeline fence leaves output staged before RMD authorization") ||
      !expect(authorize_output_commit(*one.run, true).ok(),
              "RMD authorization commits the fixed-slot run") ||
      !expect(submit_stripe(*one.run, event(3, 5, 5, 4)).code ==
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
  return expect(x.status.ok() && y.status.ok() &&
                    x.stripe_rtl_timings.empty() &&
                    y.stripe_rtl_timings.empty(),
                "concurrent FULL lifecycle returns one stable empty view");
}

bool test_timing_setup_and_malformed_completions() {
  fake::reset();
  std::vector<int8_t> activation = {1, 2, 3, 4, 5, 6};
  std::vector<int8_t> weights = {1, 0, 0, 1, 1, 1};
  std::vector<int32_t> destination(4, 0x24681357);
  auto args = raw_args(activation, weights, destination);

  RunTestAccess::fail_next_timing_reserve();
  auto allocation = execute(&args, Mode::stripe_pipeline);
  if (!expect(allocation.status.code == StatusCode::out_of_memory &&
                  fake::sim_created == 0 && fake::stream_created == 0 &&
                  destination == std::vector<int32_t>(4, 0x24681357),
              "timing reserve failure returns OOM before RTL execution"))
    return false;

  enum class Malformed { id, rows, context, duration, order, capacity };
  for (const auto malformed : {Malformed::id, Malformed::rows,
                               Malformed::context, Malformed::duration,
                               Malformed::order, Malformed::capacity}) {
    fake::reset();
    fake::allow_completion = false;
    destination.assign(4, 0x24681357);
    auto started = execute(&args, Mode::stripe_pipeline);
    if (!started.status.ok() ||
        !submit_stripe(*started.run, event(0, 0, 1)).ok() ||
        !submit_stripe(*started.run, event(1, 1, 2)).ok() ||
        !fake::wait([] { return fake::publish_count == 2; }))
      return false;
    if (malformed == Malformed::capacity)
      RunTestAccess::invalidate_timing_capacity(*started.run);
    {
      std::lock_guard lock(fake::mutex);
      fake::malformed_completion_id = malformed == Malformed::id;
      fake::malformed_completion_rows = malformed == Malformed::rows;
      fake::malformed_completion_context = malformed == Malformed::context;
      fake::malformed_completion_duration = malformed == Malformed::duration;
      fake::malformed_completion_order = malformed == Malformed::order;
      fake::allow_completion = true;
      fake::changed.notify_all();
    }
    const auto done = fence(*started.run);
    const bool output_unchanged =
        destination == std::vector<int32_t>(4, 0x24681357);
    std::printf("TIMING_FAILURE_EMPTY malformed=%d view_size=%zu "
                "output_sentinel=%s\n",
                static_cast<int>(malformed), done.stripe_rtl_timings.size,
                output_unchanged ? "unchanged" : "changed");
    if (!expect(done.status.code == StatusCode::execution_failure &&
                    done.stripe_rtl_timings.empty() && output_unchanged,
                "malformed completion fails with an empty transactional view"))
      return false;
  }
  return true;
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
  if (!expect(first.status.ok() && second.status.ok() &&
                  first.stripe_rtl_timings.size == 1 &&
                  first.stripe_rtl_timings.data ==
                      second.stripe_rtl_timings.data &&
                  first.stripe_rtl_timings.size ==
                      second.stripe_rtl_timings.size,
              "truly overlapping fences share one stable timing view") ||
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
    args.native_weight_bytes = blocks.size() * sizeof(block_q8_h1);
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
  args.native_weight_bytes = blocks.size() * sizeof(block_q8_h1);
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

enum class RawInjectedFailure { progress, poll };

bool test_raw_failure_wakes_every_waiter() {
  for (const auto failure :
       {RawInjectedFailure::progress, RawInjectedFailure::poll}) {
    fake::reset();
    fake::allow_completion = false;
    std::vector<int8_t> activation(9, 1);
    std::vector<int8_t> weights = {1, 0, 0, 1, 1, 1};
    std::vector<int32_t> destination(6, 0x13572468);
    auto args = raw_args(activation, weights, destination, 3);
    auto started = execute(
        &args, Mode::stripe_pipeline,
        {std::numeric_limits<uint64_t>::max()});
    if (!started.status.ok())
      return false;
    RunTestAccess::hold_progress(*started.run);
    if (!submit_stripe(*started.run, event(0, 0, 1)).ok() ||
        !submit_stripe(*started.run, event(1, 1, 2)).ok()) {
      RunTestAccess::inject_execution_failure(*started.run);
      (void)fence(*started.run);
      return false;
    }

    std::mutex waiter_mutex;
    std::condition_variable waiter_changed;
    size_t completed_waiters = 0;
    const auto waiter_completed = [&] {
      std::lock_guard lock(waiter_mutex);
      ++completed_waiters;
      waiter_changed.notify_all();
    };
    Status producer_status{};
    std::thread producer([&] {
      producer_status = submit_stripe(*started.run, event(2, 2, 3));
      waiter_completed();
    });
    const bool producer_blocked =
        RunTestAccess::wait_for_blocked_submit(*started.run, 1);
    const bool worker_held =
        RunTestAccess::wait_for_held_progress(*started.run);
    if (!producer_blocked || !worker_held) {
      RunTestAccess::inject_execution_failure(*started.run);
      producer.join();
      (void)fence(*started.run);
      return false;
    }

    FenceResult first{}, second{};
    std::thread fence_one([&] {
      first = fence(*started.run);
      waiter_completed();
    });
    std::thread fence_two([&] {
      second = fence(*started.run);
      waiter_completed();
    });
    if (!RunTestAccess::wait_for_closing(*started.run)) {
      RunTestAccess::inject_execution_failure(*started.run);
      producer.join();
      fence_one.join();
      fence_two.join();
      return false;
    }
    if (failure == RawInjectedFailure::progress)
      RunTestAccess::inject_progress_failure(*started.run);
    else
      RunTestAccess::inject_poll_failure(*started.run);

    bool all_waiters_woke = false;
    {
      std::unique_lock lock(waiter_mutex);
      all_waiters_woke = waiter_changed.wait_for(
          lock, std::chrono::seconds(5), [&] { return completed_waiters == 3; });
    }
    if (!all_waiters_woke)
      RunTestAccess::inject_execution_failure(*started.run);
    producer.join();
    fence_one.join();
    fence_two.join();
    if (!expect(all_waiters_woke,
                "raw injection wakes every waiter within the bounded call"))
      return false;
    const auto repeated = fence(*started.run);
    const auto stage = acquire_pipeline_output_stage(*started.run);
    const auto authorization = authorize_output_commit(*started.run, true);
    const char *expected_message =
        failure == RawInjectedFailure::progress
            ? "injected IM2P progress failure"
            : "injected IM2P poll failure";
    const bool views_empty =
        first.stripe_rtl_timings.empty() &&
        first.semantic_stripes.empty() &&
        first.residual_stripe_stats.empty() &&
        first.residual_stripe_timings.empty() &&
        first.semantic_completion_count == 0 && first.rmd_dot_calls == 0;
    const bool resources =
        fake::sim_created.load() == 1 && fake::sim_destroyed.load() == 1 &&
        fake::stream_created.load() == 1 &&
        fake::stream_destroyed.load() == 1;
    std::printf(
        "RAW_FAILURE point=%s producer=%u fence1=%u fence2=%u repeat=%u "
        "message=%s views=%s sim=%zu/%zu stream=%zu/%zu sentinel=%s\n",
        failure == RawInjectedFailure::progress ? "progress" : "poll",
        static_cast<unsigned>(producer_status.code),
        static_cast<unsigned>(first.status.code),
        static_cast<unsigned>(second.status.code),
        static_cast<unsigned>(repeated.status.code), first.status.message,
        views_empty ? "empty" : "nonempty", fake::sim_created.load(),
        fake::sim_destroyed.load(), fake::stream_created.load(),
        fake::stream_destroyed.load(),
        destination == std::vector<int32_t>(6, 0x13572468) ? "unchanged"
                                                            : "changed");
    if (!expect(producer_status.code == StatusCode::execution_failure &&
                    first.status.code == producer_status.code &&
                    second.status.code == producer_status.code &&
                    repeated.status.code == producer_status.code &&
                    std::strcmp(producer_status.message, expected_message) == 0 &&
                    std::strcmp(first.status.message, expected_message) == 0 &&
                    std::strcmp(second.status.message, expected_message) == 0 &&
                    std::strcmp(repeated.status.message, expected_message) == 0,
                "raw boundary preserves the first status for every waiter") ||
        !expect(views_empty && stage.data == nullptr &&
                    authorization.code == StatusCode::execution_failure,
                "raw failure exposes no successful semantic view or stage") ||
        !expect(destination == std::vector<int32_t>(6, 0x13572468),
                "raw failure preserves the caller output sentinel") ||
        !expect(resources,
                "raw failure joins all waiters and destroys every handle"))
      return false;
  }
  return true;
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
    if (!expect(done.status.code == expected &&
                    done.stripe_rtl_timings.empty(),
                "full boundary returns its typed sticky error and empty view") ||
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
      const auto done = fence(*started.run);
      code = done.status.code;
      const auto repeated = fence(*started.run);
      if (!expect(done.stripe_rtl_timings.empty() &&
                      repeated.stripe_rtl_timings.empty() &&
                      repeated.status.code == code,
                  "stripe boundary error has a stable empty repeated view"))
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

bool test_publication_geometry_and_counter_separation() {
  struct Result {
    std::vector<size_t> publication_rows;
    im2p_work_stats_t stats{};
  };
  auto run = [](size_t reduction) {
    fake::reset();
    constexpr size_t rows = 16 * DIM;
    constexpr size_t rows_per_publication = 5 * DIM;
    std::vector<int8_t> activation(rows * reduction, 1);
    std::vector<int8_t> weights(reduction, 1);
    std::vector<int32_t> output(rows, 0x12345678);
    ggml_gemmini_args_t args{};
    args.I = rows;
    args.J = 1;
    args.K = reduction;
    if (!args.A.allocate(rows, reduction,
                         IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS))
      return Result{};
    for (size_t row = 0; row < rows; ++row)
      for (size_t column = 0; column < reduction; ++column)
        if (!args.A.set(row, column, activation[row * reduction + column]))
          return Result{};
    args.B = weights.data();
    args.C = output.data();
    args.sA = reduction;
    args.sB = 1;
    args.sC = 1;
    args.full_C = true;
    args.tile_I = 5;
    args.tile_J = 1;
    args.tile_K = 1;
    args.activation_rows_per_stripe = rows_per_publication;
    args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_h0;

    auto started = execute(&args, Mode::stripe_pipeline, {64});
    if (!started.status.ok())
      return Result{};
    size_t stripe_id = 0;
    for (size_t begin = 0; begin < rows;
         begin += rows_per_publication, ++stripe_id) {
      if (!submit_stripe(*started.run,
                         event(stripe_id, begin,
                               std::min(rows, begin + rows_per_publication)))
               .ok())
        return Result{};
    }
    const auto done = fence(*started.run);
    if (!done.status.ok())
      return Result{};
    Result result;
    for (const auto &published : fake::published)
      result.publication_rows.push_back(published.rows);
    result.stats = done.stats.base;
    return result;
  };

  const auto one_k_fragment = run(DIM);
  const auto three_k_fragments = run(2 * DIM + 1);
  if (!expect(one_k_fragment.publication_rows ==
                  std::vector<size_t>({5 * DIM, 5 * DIM, 5 * DIM, DIM}) &&
                  three_k_fragments.publication_rows ==
                      one_k_fragment.publication_rows,
              "tile_I=5 publishes exact 5*DIM/5*DIM/5*DIM/DIM row stripes"))
    return false;
  if (!expect(one_k_fragment.stats.completed_output_tiles == 16 &&
                  one_k_fragment.stats.completed_fragments == 16 &&
                  one_k_fragment.stats.completed_stripes == 4 &&
                  one_k_fragment.stats.stripes_published == 4 &&
                  one_k_fragment.stats.stripe_rows_published == 16 * DIM,
              "one K fragment keeps RTL works distinct from publications"))
    return false;
  if (!expect(three_k_fragments.stats.completed_output_tiles == 16 &&
                  three_k_fragments.stats.completed_fragments == 48 &&
                  three_k_fragments.stats.completed_stripes == 4 &&
                  three_k_fragments.stats.stripes_published == 4 &&
                  three_k_fragments.stats.stripe_rows_published == 16 * DIM,
              "extra K fragments change internal progress but not publications"))
    return false;
  std::printf("STRIPE_COUNTER_QA rows=%d,%d,%d,%d works=%llu "
              "k1_fragments=%llu k3_fragments=%llu publications=%llu "
              "published_rows=%llu\n",
              5 * DIM, 5 * DIM, 5 * DIM, DIM,
              static_cast<unsigned long long>(
                  three_k_fragments.stats.completed_output_tiles),
              static_cast<unsigned long long>(
                  one_k_fragment.stats.completed_fragments),
              static_cast<unsigned long long>(
                  three_k_fragments.stats.completed_fragments),
              static_cast<unsigned long long>(
                  three_k_fragments.stats.stripes_published),
              static_cast<unsigned long long>(
                  three_k_fragments.stats.stripe_rows_published));

  fake::reset();
  std::vector<int8_t> activation(16 * DIM * DIM, 1), weights(DIM, 1);
  std::vector<int32_t> output(16 * DIM);
  ggml_gemmini_args_t args{};
  args.I = 16 * DIM;
  args.J = 1;
  args.K = DIM;
  if (!args.A.allocate(args.I, args.K,
                       IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS))
    return false;
  args.B = weights.data();
  args.C = output.data();
  args.sA = DIM;
  args.sB = args.sC = 1;
  args.full_C = true;
  args.tile_I = 5;
  args.tile_J = args.tile_K = 1;
  args.activation_rows_per_stripe = 5 * DIM;
  args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_h0;
  auto malformed = execute(&args, Mode::stripe_pipeline, {64});
  if (!malformed.status.ok())
    return false;
  return expect(submit_stripe(*malformed.run, event(1, 0, 5 * DIM)).code ==
                    StatusCode::invalid_argument,
                "out-of-order publication rejects deterministically") &&
         expect(submit_stripe(*malformed.run, event(0, 0, 5 * DIM)).ok(),
                "valid first publication remains acceptable") &&
         expect(submit_stripe(*malformed.run,
                              event(1, 5 * DIM - 1, 10 * DIM - 1)).code ==
                    StatusCode::invalid_argument,
                "overlapping publication rejects deterministically") &&
         expect(fence(*malformed.run).status.code ==
                    StatusCode::invalid_contract,
                "incomplete publication sequence rejects at fence");
}

bool prepare_semantic_pipeline_args(ggml_gemmini_args_t &args,
                                    std::vector<block_q8_h1> &weights,
                                    std::vector<float> &destination,
                                    size_t rows) {
  constexpr size_t columns = 2;
  args.I = rows;
  args.J = columns;
  args.K = 32;
  if (!args.A.allocate(rows, args.K,
                       IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS))
    return false;
  args.activation_rows_per_stripe = 1;
  args.f_out = destination.data();
  args.stride_f_out = columns;
  args.col_stride_f_out = 1;
  args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_h1;
  weights.resize(columns);
  for (auto &weight : weights) {
    weight.s_rf = 1.0f;
    weight.c_b = 1;
    weight.R = 0;
  }
  args.q8_h1_blocks = weights.data();
  args.q8_h1_block_count = weights.size();
  args.q8_h1_rows = columns;
  args.blocks_per_row = 1;
  args.native_weight_bytes = weights.size() * sizeof(block_q8_h1);
  args.act_quant.storage().emplace<exsia::Meta>();
  return true;
}

exsia::StripeReadyEvent semantic_event(size_t id) {
  auto ready = event(id, id, id + 1, 83);
  ready.activation_metadata.emplace();
  ready.activation_metadata->e_s = -4;
  ready.activation_metadata->rho = 5;
  ready.activation_metadata->sigma = 17;
  ready.activation_metadata->theta = 0;
  ready.quantization_start = 1000 + id;
  ready.quantization_end = 1100 + id;
  ready.local_start_cycle = 1200 + id;
  ready.local_end_cycle = 1300 + id;
  ready.folding_commit_ns = 1400 + id;
  return ready;
}

struct SemanticSchedulerProbe {
  std::mutex mutex;
  std::condition_variable changed;
  Run *run = nullptr;
  float *caller_destination = nullptr;
  size_t stage_elements = 0;
  im2p_sim_t *expected_simulator = nullptr;
  bool gate_first = true;
  bool release_first = false;
  bool fail_first = false;
  bool first_entered = false;
  bool producer_done = false;
  bool callback_outside_lock = true;
  bool simulator_ok = true;
  bool event_ok = true;
  bool stage_ok = true;
  size_t calls = 0;
  std::thread::id callback_thread;
  std::vector<std::thread::id> packet_release_threads;
};

template <class Predicate>
bool wait_for_probe(SemanticSchedulerProbe &probe, Predicate predicate) {
  std::unique_lock lock(probe.mutex);
  return probe.changed.wait_for(lock, std::chrono::seconds(5), predicate);
}

Status semantic_scheduler_callback(void *opaque, im2p_sim_t *simulator,
                                   const exsia::StripeReadyEvent &ready,
                                   ResidualStageView stage,
                                   ResidualStripeStats &stats) noexcept {
  auto &probe = *static_cast<SemanticSchedulerProbe *>(opaque);
  const bool scheduler_unlocked =
      probe.run != nullptr && RunTestAccess::try_lock_scheduler(*probe.run);
  {
    std::lock_guard lock(fake::mutex);
    fake::semantic_trace.push_back("R" + std::to_string(ready.stripe_id));
    fake::changed.notify_all();
  }
  bool fail = false;
  {
    std::unique_lock lock(probe.mutex);
    probe.callback_outside_lock &= scheduler_unlocked;
    probe.simulator_ok &= simulator == probe.expected_simulator;
    probe.event_ok &=
        ready.run_id == 83 && ready.slot == ready.stripe_id + 9 &&
        ready.row_begin == ready.stripe_id &&
        ready.row_end == ready.stripe_id + 1 &&
        ready.activation_metadata.has_value() &&
        ready.activation_metadata->e_s == -4 &&
        ready.activation_metadata->rho == 5 &&
        ready.activation_metadata->sigma == 17 &&
        ready.activation_metadata->theta == 0 &&
        ready.quantization_start == 1000 + ready.stripe_id &&
        ready.quantization_end == 1100 + ready.stripe_id &&
        ready.local_start_cycle == 1200 + ready.stripe_id &&
        ready.local_end_cycle == 1300 + ready.stripe_id &&
        ready.folding_commit_ns == 1400 + ready.stripe_id;
    probe.stage_ok &= stage.data != nullptr &&
                      stage.data != probe.caller_destination &&
                      stage.element_count == probe.stage_elements;
    if (probe.callback_thread == std::thread::id{})
      probe.callback_thread = std::this_thread::get_id();
    else
      probe.simulator_ok &=
          probe.callback_thread == std::this_thread::get_id();
    ++probe.calls;
    if (ready.stripe_id == 0) {
      probe.first_entered = true;
      probe.changed.notify_all();
      if (probe.gate_first) {
        probe.changed.wait(lock, [&] { return probe.release_first; });
      }
      fail = probe.fail_first;
    }
  }
  if (fail)
    return {StatusCode::execution_failure, Route::q8_h1, true,
            "injected residual callback failure"};
  stage[ready.row_begin * 2] += 100.0f;
  stats.rmd_dot_calls = ready.stripe_id + 1;
  stats.rmd_stats.base.work_total_cycles = 10 + ready.stripe_id;
  stats.rmd_stats.base.completed_output_tiles = 1;
  {
    std::lock_guard lock(fake::mutex);
    fake::semantic_trace.push_back("C" + std::to_string(ready.stripe_id));
    fake::changed.notify_all();
  }
  return {};
}

std::string joined_trace(const std::vector<std::string> &events) {
  std::string result;
  for (const auto &entry : events) {
    if (!result.empty())
      result += ',';
    result += entry;
  }
  return result;
}

bool test_semantic_dense_rmd_order() {
#if GGML_GEMMINI_WEIGHT_BITS != 8
  return true;
#else
  fake::reset();
  fake::allow_completion = false;
  fake::provider_exact_values = {1, 2, 3, 4, 5, 6};
  std::vector<float> destination(6, 91.0f);
  std::vector<block_q8_h1> weights;
  ggml_gemmini_args_t args{};
  if (!prepare_semantic_pipeline_args(args, weights, destination, 3))
    return false;
  SemanticSchedulerProbe probe;
  probe.caller_destination = destination.data();
  probe.stage_elements = destination.size();
  auto started = execute(
      &args, Mode::stripe_pipeline,
      {std::numeric_limits<uint64_t>::max(),
       ResidualStageMode::im2p_compact, &probe,
       semantic_scheduler_callback});
  if (!expect(started.status.ok(), "residual pipeline starts"))
    return false;
  probe.run = started.run.get();
  {
    std::lock_guard lock(fake::mutex);
    probe.expected_simulator =
        fake::sim_handles.size() == 2 ? fake::sim_handles[1] : nullptr;
  }
  if (!expect(submit_stripe(*started.run, semantic_event(0), {true, 0}).ok() &&
                  submit_stripe(*started.run, semantic_event(1), {true, 0}).ok(),
              "two semantic slots accept before raw completion"))
    return false;
  Status third_status{};
  std::thread producer([&] {
    third_status =
        submit_stripe(*started.run, semantic_event(2), {true, 0});
    std::lock_guard lock(probe.mutex);
    probe.producer_done = true;
    probe.changed.notify_all();
  });
  if (!expect(RunTestAccess::wait_for_blocked_submit(*started.run, 1),
              "third semantic producer reaches capacity wait") ||
      !expect(fake::wait([] { return fake::publish_count >= 1; }),
              "first raw publication reaches the fake ABI")) {
    RunTestAccess::inject_execution_failure(*started.run);
    producer.join();
    (void)fence(*started.run);
    return false;
  }
  {
    std::lock_guard lock(fake::mutex);
    fake::allow_completion = true;
    fake::changed.notify_all();
  }
  const bool reached_first_boundary = wait_for_probe(probe, [&] {
    return probe.first_entered || probe.producer_done;
  });
  const auto pending = RunTestAccess::inspect(*started.run);
  bool producer_released_early = false;
  {
    std::lock_guard lock(probe.mutex);
    producer_released_early = probe.producer_done;
    probe.release_first = true;
    probe.changed.notify_all();
  }
  producer.join();
  const auto done = fence(*started.run);
  const auto terminal = RunTestAccess::inspect(*started.run);
  const bool destination_staged =
      destination == std::vector<float>(6, 91.0f);
  const auto authorized = authorize_output_commit(*started.run, true);
  std::vector<std::string> trace;
  size_t max_raw = 0;
  size_t sim_count_at_publish = 0;
  bool owner_threads = false;
  {
    std::lock_guard lock(fake::mutex);
    trace = fake::semantic_trace;
    max_raw = fake::max_raw_in_flight;
    sim_count_at_publish = fake::sim_count_at_first_publish;
    owner_threads = fake::one_owner && fake::sim_created == 2 &&
                    fake::sim_destroyed == 2 && fake::stream_created == 1 &&
                    fake::stream_destroyed == 1 &&
                    fake::sim_create_threads.size() == 2 &&
                    fake::sim_destroy_threads.size() == 2 &&
                    std::all_of(fake::sim_create_threads.begin(),
                                fake::sim_create_threads.end(),
                                [&](std::thread::id id) {
                                  return id == probe.callback_thread;
                                }) &&
                    std::all_of(fake::sim_destroy_threads.begin(),
                                fake::sim_destroy_threads.end(),
                                [&](std::thread::id id) {
                                  return id == probe.callback_thread;
                                });
  }
  const std::vector<std::string> exact_trace = {
      "D0", "R0", "C0", "D1", "R1", "C1", "D2", "R2", "C2"};
  const bool semantic_views =
      done.semantic_completion_count == 3 &&
      done.semantic_stripes.size == 3 &&
      done.residual_stripe_stats.size == 3 &&
      done.residual_stripe_timings.size == 3 &&
      done.rmd_dot_calls == 6 && done.rmd_stats.base.work_total_cycles == 33 &&
      done.semantic_stripes[0].stripe_id == 0 &&
      done.semantic_stripes[1].stripe_id == 1 &&
      done.semantic_stripes[2].stripe_id == 2 &&
      done.residual_stripe_timings[1].rmd_dot_calls == 2 &&
      done.residual_stripe_timings[1].rmd_stats.base.work_total_cycles == 11;
  std::printf(
      "SEMANTIC_ORDER trace=%s raw_max=%zu semantic_max=2 "
      "pending_raw=%zu pending_outstanding=%zu raw_generation=%llu "
      "semantic_generation=%llu third_released_before_c0=%d "
      "sim_created_before_d0=%zu callback_thread=%zu owner_thread=%zu\n",
      joined_trace(trace).c_str(), max_raw, pending.in_flight,
      pending.outstanding,
      static_cast<unsigned long long>(terminal.completion_generation),
      static_cast<unsigned long long>(terminal.semantic_generation),
      producer_released_early ? 1 : 0, sim_count_at_publish,
      std::hash<std::thread::id>{}(probe.callback_thread),
      std::hash<std::thread::id>{}(fake::owner));
  const bool compact_ok =
      expect(reached_first_boundary && probe.first_entered,
             "raw completion enters residual callback") &&
      expect(!producer_released_early && pending.queued == 1 &&
                 pending.in_flight == 0 && pending.outstanding == 2 &&
                 pending.timing_size == 1,
             "raw-complete residual-pending state retains both semantic slots") &&
      expect(third_status.ok(), "third producer releases after C0") &&
      expect(done.status.ok() && done.stripe_rtl_timings.size == 3 &&
                 semantic_views,
             "fence freezes complete raw and semantic coverage") &&
      expect(trace == exact_trace && max_raw == 1,
             "dense, residual, and semantic stages are strictly serialized") &&
      expect(probe.calls == 3 && probe.callback_outside_lock &&
                 probe.simulator_ok && probe.event_ok && probe.stage_ok,
             "callback runs outside scheduler lock with copied event and stage") &&
      expect(owner_threads && sim_count_at_publish == 2,
             "dense and residual simulators share one worker owner") &&
      expect(destination_staged && authorized.ok() &&
                 destination ==
                     std::vector<float>({101, 2, 103, 4, 105, 6}),
             "caller output remains staged until successful authorization") &&
      expect(terminal.completion_generation == 3 &&
                 terminal.semantic_generation == 3,
             "raw and semantic generations advance independently");

  fake::reset();
  fake::provider_exact_values = {7, 8};
  std::vector<float> direct_destination(2, 73.0f);
  std::vector<block_q8_h1> direct_weights;
  ggml_gemmini_args_t direct_args{};
  if (!prepare_semantic_pipeline_args(direct_args, direct_weights,
                                      direct_destination, 1))
    return false;
  SemanticSchedulerProbe direct_probe;
  direct_probe.caller_destination = direct_destination.data();
  direct_probe.stage_elements = direct_destination.size();
  auto direct = execute(
      &direct_args, Mode::stripe_pipeline,
      {65536, ResidualStageMode::host_direct, &direct_probe,
       semantic_scheduler_callback});
  if (!direct.status.ok())
    return false;
  direct_probe.run = direct.run.get();
  direct_probe.expected_simulator = nullptr;
  if (!submit_stripe(*direct.run, semantic_event(0), {true, 0}).ok() ||
      !wait_for_probe(direct_probe,
                      [&] { return direct_probe.first_entered; })) {
    std::lock_guard lock(direct_probe.mutex);
    direct_probe.release_first = true;
    direct_probe.changed.notify_all();
    return false;
  }
  FenceResult direct_done{};
  bool direct_fence_done = false;
  std::thread direct_fence([&] {
    direct_done = fence(*direct.run);
    std::lock_guard lock(direct_probe.mutex);
    direct_fence_done = true;
    direct_probe.changed.notify_all();
  });
  const bool direct_closing = RunTestAccess::wait_for_closing(*direct.run);
  bool direct_fence_done_before_release = false;
  {
    std::lock_guard lock(direct_probe.mutex);
    direct_fence_done_before_release = direct_fence_done;
    direct_probe.release_first = true;
    direct_probe.changed.notify_all();
  }
  direct_fence.join();
  std::vector<std::string> direct_trace;
  size_t direct_created = 0, direct_destroyed = 0, direct_finish = 0;
  {
    std::lock_guard lock(fake::mutex);
    direct_trace = fake::semantic_trace;
    direct_created = fake::sim_created;
    direct_destroyed = fake::sim_destroyed;
    direct_finish = fake::finish_count;
  }
  std::printf("SEMANTIC_HOST_DIRECT trace=%s sim_created=%zu sim_destroyed=%zu "
              "callback_simulator=null fence_waited_for_c0=%d finish=%zu\n",
              joined_trace(direct_trace).c_str(), direct_created,
              direct_destroyed, direct_fence_done_before_release ? 0 : 1,
              direct_finish);
  const bool direct_ok =
      expect(direct_closing && !direct_fence_done_before_release &&
                 direct_done.status.ok() &&
                 direct_trace ==
                     std::vector<std::string>({"D0", "R0", "C0"}) &&
                 direct_probe.calls == 1 && direct_probe.simulator_ok &&
                 direct_created == 1 && direct_destroyed == 1 &&
                 direct_finish == 1,
             "host-direct fence waits semantic completion without a residual "
             "simulator");
  return compact_ok && direct_ok;
#endif
}

bool test_semantic_blocked_producer_failure() {
#if GGML_GEMMINI_WEIGHT_BITS != 8
  return true;
#else
  fake::reset();
  fake::allow_completion = false;
  fake::provider_exact_values = {1, 2, 3, 4, 5, 6};
  std::vector<float> destination(6, 67.0f);
  std::vector<block_q8_h1> weights;
  ggml_gemmini_args_t args{};
  if (!prepare_semantic_pipeline_args(args, weights, destination, 3))
    return false;
  SemanticSchedulerProbe probe;
  probe.fail_first = true;
  probe.caller_destination = destination.data();
  probe.stage_elements = destination.size();
  auto started = execute(
      &args, Mode::stripe_pipeline,
      {std::numeric_limits<uint64_t>::max(),
       ResidualStageMode::im2p_compact, &probe,
       semantic_scheduler_callback});
  if (!started.status.ok())
    return false;
  probe.run = started.run.get();
  {
    std::lock_guard lock(fake::mutex);
    probe.expected_simulator =
        fake::sim_handles.size() == 2 ? fake::sim_handles[1] : nullptr;
  }
  auto owned_event = [&](size_t id) {
    auto ready = semantic_event(id);
    auto packet = std::shared_ptr<ggml::gemmini::rmd::StripePacket>(
        new ggml::gemmini::rmd::StripePacket, [&](auto *value) {
          {
            std::lock_guard lock(probe.mutex);
            probe.packet_release_threads.push_back(
                std::this_thread::get_id());
            probe.changed.notify_all();
          }
          delete value;
        });
    ready.rmd_packet = packet;
    return ready;
  };
  {
    auto first = owned_event(0);
    auto second = owned_event(1);
    if (!submit_stripe(*started.run, first, {true, 0}).ok() ||
        !submit_stripe(*started.run, second, {true, 0}).ok())
      return false;
  }
  Status producer_status{};
  std::thread producer([&] {
    producer_status =
        submit_stripe(*started.run, semantic_event(2), {true, 0});
    std::lock_guard lock(probe.mutex);
    probe.producer_done = true;
    probe.changed.notify_all();
  });
  if (!RunTestAccess::wait_for_blocked_submit(*started.run, 1) ||
      !fake::wait([] { return fake::publish_count >= 1; })) {
    RunTestAccess::inject_execution_failure(*started.run);
    producer.join();
    (void)fence(*started.run);
    return false;
  }
  {
    std::lock_guard lock(fake::mutex);
    fake::allow_completion = true;
    fake::changed.notify_all();
  }
  const bool callback_or_early_release = wait_for_probe(probe, [&] {
    return probe.first_entered || probe.producer_done;
  });
  FenceResult fence_one{}, fence_two{};
  std::thread first_fence([&] { fence_one = fence(*started.run); });
  std::thread second_fence([&] { fence_two = fence(*started.run); });
  const bool closing = RunTestAccess::wait_for_closing(*started.run);
  bool producer_done_before_failure = false;
  {
    std::lock_guard lock(probe.mutex);
    producer_done_before_failure = probe.producer_done;
    probe.release_first = true;
    probe.changed.notify_all();
  }
  producer.join();
  first_fence.join();
  second_fence.join();
  const auto repeated = fence(*started.run);
  const auto stage = acquire_pipeline_output_stage(*started.run);
  const auto authorization = authorize_output_commit(*started.run, true);
  std::vector<std::string> trace;
  size_t created = 0, destroyed = 0, streams_created = 0,
         streams_destroyed = 0, max_raw = 0;
  bool same_owner = false;
  std::thread::id abi_owner;
  {
    std::lock_guard lock(fake::mutex);
    trace = fake::semantic_trace;
    created = fake::sim_created;
    destroyed = fake::sim_destroyed;
    streams_created = fake::stream_created;
    streams_destroyed = fake::stream_destroyed;
    max_raw = fake::max_raw_in_flight;
    same_owner = fake::one_owner;
    abi_owner = fake::owner;
  }
  std::vector<std::thread::id> release_threads;
  {
    std::lock_guard lock(probe.mutex);
    release_threads = probe.packet_release_threads;
  }
  const bool handles_on_owner =
      release_threads.size() == 2 &&
      std::all_of(release_threads.begin(), release_threads.end(),
                  [&](std::thread::id id) { return id == abi_owner; });
  std::printf(
      "SEMANTIC_FAILURE trace=%s producer_status=%u fence1=%u fence2=%u "
      "repeat=%u raw_max=%zu sim=%zu/%zu stream=%zu/%zu handles=%zu "
      "callback_thread=%zu owner_thread=%zu sentinel=%s\n",
      joined_trace(trace).c_str(),
      static_cast<unsigned>(producer_status.code),
      static_cast<unsigned>(fence_one.status.code),
      static_cast<unsigned>(fence_two.status.code),
      static_cast<unsigned>(repeated.status.code), max_raw, created, destroyed,
      streams_created, streams_destroyed, release_threads.size(),
      std::hash<std::thread::id>{}(probe.callback_thread),
      std::hash<std::thread::id>{}(abi_owner),
      destination == std::vector<float>(6, 67.0f) ? "unchanged" : "changed");
  const bool callback_failure_ok =
      expect(callback_or_early_release && probe.first_entered && closing &&
                 !producer_done_before_failure,
             "gated R0 holds producer and both fences before failure") &&
      expect(producer_status.code == StatusCode::execution_failure &&
                 fence_one.status.code == producer_status.code &&
                 fence_two.status.code == producer_status.code &&
                 repeated.status.code == producer_status.code &&
                 std::strcmp(producer_status.message,
                             "injected residual callback failure") == 0 &&
                 std::strcmp(fence_one.status.message,
                             producer_status.message) == 0,
             "first callback error is sticky for every waiter") &&
      expect(trace == std::vector<std::string>({"D0", "R0"}) &&
                 max_raw == 1,
             "failure stops later dense publication before semantic commit") &&
      expect(fence_one.stripe_rtl_timings.empty() &&
                 fence_one.semantic_stripes.empty() &&
                 fence_one.residual_stripe_stats.empty() &&
                 fence_one.residual_stripe_timings.empty() &&
                 fence_one.semantic_completion_count == 0 &&
                 fence_one.rmd_dot_calls == 0 &&
                 stage.data == nullptr && authorization.code ==
                                              StatusCode::execution_failure,
             "failed run exposes no raw or semantic success view") &&
      expect(destination == std::vector<float>(6, 67.0f),
             "failed semantic merge preserves caller sentinel") &&
      expect(created == 2 && destroyed == 2 && streams_created == 1 &&
                 streams_destroyed == 1 && same_owner && handles_on_owner &&
                 probe.callback_outside_lock && probe.simulator_ok,
             "failure tears down both simulators and handles on worker owner");

  fake::reset();
  fake::fail_create_attempt = 2;
  std::vector<float> create_destination(2, 59.0f);
  std::vector<block_q8_h1> create_weights;
  ggml_gemmini_args_t create_args{};
  if (!prepare_semantic_pipeline_args(create_args, create_weights,
                                      create_destination, 1))
    return false;
  SemanticSchedulerProbe create_probe;
  create_probe.gate_first = false;
  create_probe.caller_destination = create_destination.data();
  create_probe.stage_elements = create_destination.size();
  auto create_failed = execute(
      &create_args, Mode::stripe_pipeline,
      {65536, ResidualStageMode::im2p_compact, &create_probe,
       semantic_scheduler_callback});
  FenceResult create_fence{};
  if (create_failed.run)
    create_fence = fence(*create_failed.run);
  const bool second_create_ok =
      expect(create_failed.status.code == StatusCode::out_of_memory &&
                 create_fence.status.code == create_failed.status.code &&
                 fake::create_attempts == 2 && fake::sim_created == 1 &&
                 fake::sim_destroyed == 1 && fake::stream_created == 0 &&
                 create_probe.calls == 0 &&
                 create_destination == std::vector<float>(2, 59.0f),
             "second simulator create failure is transactional before raw start");
  std::printf("SEMANTIC_SECOND_CREATE status=%u attempts=%zu sim=%zu/%zu "
              "stream_created=%zu sentinel=%s\n",
              static_cast<unsigned>(create_failed.status.code),
              fake::create_attempts, fake::sim_created.load(),
              fake::sim_destroyed.load(), fake::stream_created.load(),
              create_destination == std::vector<float>(2, 59.0f)
                  ? "unchanged"
                  : "changed");
  return callback_failure_ok && second_create_ok;
#endif
}

struct ResidualCallbackProbe {
  const exsia::StripeReadyEvent *expected_event = nullptr;
  float *expected_stage = nullptr;
  size_t expected_element_count = 0;
  im2p_sim_t *expected_simulator = nullptr;
  bool exact_borrowed_arguments = false;
  size_t calls = 0;
};

Status residual_callback_probe(void *opaque, im2p_sim_t *simulator,
                               const exsia::StripeReadyEvent &ready,
                               ResidualStageView stage,
                               ResidualStripeStats &stats) noexcept {
  auto &probe = *static_cast<ResidualCallbackProbe *>(opaque);
  probe.exact_borrowed_arguments =
      &ready == probe.expected_event && stage.data == probe.expected_stage &&
      stage.element_count == probe.expected_element_count &&
      simulator == probe.expected_simulator;
  ++probe.calls;
  stage[ready.row_begin] += 4.0f;
  stats.rmd_dot_calls = 3;
  stats.rmd_stats.base.completed_output_tiles = 2;
  return {};
}

bool test_legacy_options_aggregate() {
  fake::reset();
  std::vector<int8_t> activation = {1, 2, 3};
  std::vector<int8_t> weights = {1, 0, 0, 1, 1, 1};
  std::vector<int32_t> output(2, 41);
  auto args = raw_args(activation, weights, output, 1);
  auto started = execute(&args, Mode::full, Options{65536});
  return expect(started.status.ok() && started.run != nullptr &&
                    fence(*started.run).status.ok() &&
                    output == std::vector<int32_t>({4, 5}),
                "legacy single-field Options aggregate retains full execution");
}

bool test_residual_stage_contract() {
  constexpr Options legacy{65536};
  static_assert(legacy.max_stalled_cycles == 65536);
  static_assert(legacy.residual_stage_mode == ResidualStageMode::none);
  static_assert(legacy.residual_stage_context == nullptr);
  static_assert(legacy.residual_stage_fn == nullptr);
  static_assert(std::is_nothrow_invocable_r_v<
                Status, ResidualStageFn, void *, im2p_sim_t *,
                const exsia::StripeReadyEvent &, ResidualStageView,
                ResidualStripeStats &>);

  std::array<float, 4> private_stage = {1, 2, 3, 4};
  const auto ready = event(5, 1, 3, 91);
  ResidualCallbackProbe probe{&ready, private_stage.data(),
                              private_stage.size(), nullptr};
  ResidualStripeStats stats{};
  const ResidualStageView stage{private_stage.data(), private_stage.size()};
  const auto callback = residual_callback_probe;
  const auto status = callback(&probe, nullptr, ready, stage, stats);

  const std::array<ResidualStageMode, 3> modes = {
      ResidualStageMode::none, ResidualStageMode::host_direct,
      ResidualStageMode::im2p_compact};
  Options host{65536, modes[1], &probe, callback};
  Options compact{65536, modes[2], &probe, callback};
  const FenceResult empty_result{};
  return expect(status.ok() && probe.calls == 1 &&
                    probe.exact_borrowed_arguments &&
                    private_stage[1] == 6.0f &&
                    stage.begin() == private_stage.data() &&
                    stage.end() == private_stage.data() + private_stage.size() &&
                    stats.rmd_dot_calls == 3 &&
                    stats.rmd_stats.base.completed_output_tiles == 2,
                "residual callback receives exact borrowed event and stage") &&
         expect(host.residual_stage_mode == ResidualStageMode::host_direct &&
                    compact.residual_stage_mode ==
                        ResidualStageMode::im2p_compact &&
                    empty_result.residual_stripe_stats.empty() &&
                    empty_result.semantic_stripes.empty(),
                "all residual modes and default result views are additive");
}

bool test_residual_stage_invalid_options() {
  ResidualCallbackProbe probe;
  const auto callback = residual_callback_probe;
  const std::array invalid_options = {
      Options{65536, ResidualStageMode::host_direct, nullptr, nullptr},
      Options{65536, ResidualStageMode::im2p_compact, nullptr, nullptr},
      Options{65536, ResidualStageMode::none, &probe, callback},
      Options{65536, static_cast<ResidualStageMode>(0xff), nullptr, callback},
  };
  for (const auto options : invalid_options) {
    fake::reset();
    std::vector<int8_t> activation = {1, 2, 3};
    std::vector<int8_t> weights = {1, 0, 0, 1, 1, 1};
    std::vector<int32_t> destination(2, 41);
    auto args = raw_args(activation, weights, destination, 1);
    const auto rejected = execute(&args, Mode::full, options);
    if (!expect(rejected.status.code == StatusCode::invalid_argument &&
                    rejected.run == nullptr && fake::sim_created == 0 &&
                    fake::stream_created == 0 &&
                    destination == std::vector<int32_t>({41, 41}),
                "invalid residual options reject before execution"))
      return false;
  }
  return true;
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
        : selected == "fixed_two_slots" ||
                  selected == "backpressure_runid_incomplete_and_concurrent"
            ? test_backpressure_runid_incomplete_and_concurrent()
        : selected == "pipeline_lifecycle" ? test_pipeline_lifecycle()
        : selected == "timing_malformed"
            ? test_timing_setup_and_malformed_completions()
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
        : selected == "q8_hp1_extent_contract"
            ? test_q8_hp1_native_extent_contract()
        : selected == "native_q4_q16_provider"
            ? test_native_q4_q16_provider_golden()
        : selected == "provider_int64_scaling" ||
                  selected == "cross_mode_oracle"
            ? test_provider_int64_scaling_full_pipeline()
        : selected == "publication_counter_separation"
            ? test_publication_geometry_and_counter_separation()
        : selected == "legacy_options_aggregate"
            ? test_legacy_options_aggregate()
        : selected == "residual_stage_contract"
            ? test_residual_stage_contract()
        : selected == "residual_stage_invalid_options"
            ? test_residual_stage_invalid_options()
        : selected == "semantic_dense_rmd_order"
            ? test_semantic_dense_rmd_order()
        : selected == "semantic_blocked_producer_failure"
            ? test_semantic_blocked_producer_failure()
        : selected == "raw_failure_wakeup"
            ? test_raw_failure_wakes_every_waiter()
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
      test_publication_geometry_and_counter_separation() &&
      test_rejected_routes_do_not_execute() &&
      test_legacy_options_aggregate() && test_mode_and_raw_scale_contract() &&
      test_full_golden_and_scalar_snapshot() &&
      test_multiwidth_activation_snapshot_validation() &&
      test_tile_normalization_validation() && test_pipeline_lifecycle() &&
      test_backpressure_runid_incomplete_and_concurrent() &&
      test_timing_setup_and_malformed_completions() &&
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
      test_semantic_dense_rmd_order() &&
      test_semantic_blocked_producer_failure() &&
      test_raw_failure_wakes_every_waiter() &&
      test_full_failure_matrix() && test_stripe_failure_matrix() &&
      test_invalid_reuse_is_bounded() &&
      test_forward_progress_is_not_stall() &&
      test_max_stall_limit_disables_watchdog() &&
      test_logical_stall_bound());
  if (ok)
    std::puts("IM2P Gemmini frontend: PASS");
  return ok ? 0 : 1;
}
