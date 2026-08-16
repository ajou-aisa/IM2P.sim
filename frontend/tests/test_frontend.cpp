#define IM2P_GEMMINI_FRONTEND_TESTING 1
#include "im2p_gemmini_frontend.hpp"

#include "ggml-gemmini-args.h"
#include "quants/act/exsia/exsia.hpp"
#include "residual/rmd/rmd-types.hpp"

#include <im2p_sim.h>

#include <array>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <deque>
#include <limits>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>

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
bool fail_progress = false;
bool fail_poll = false;
bool throw_create = false;
bool hold_publish = false;
bool publish_entered = false;
size_t required_progress_cycles = 1;
size_t progress_calls = 0;
size_t publish_attempts = 0;
size_t publish_count = 0;
size_t finish_count = 0;
std::thread::id owner;
bool one_owner = true;
im2p_matmul_desc_t full_desc{};
im2p_stripe_work_desc_t work_desc{};
im2p_matmul_desc_v1_t provider_full_desc{};
im2p_stripe_work_desc_v1_t provider_work_desc{};
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
  fail_progress = false;
  fail_poll = false;
  throw_create = false;
  hold_publish = false;
  publish_entered = false;
  required_progress_cycles = 1;
  progress_calls = 0;
  publish_attempts = 0;
  publish_count = 0;
  finish_count = 0;
  owner = {};
  one_owner = true;
  full_desc = {};
  work_desc = {};
  provider_full_desc = {};
  provider_work_desc = {};
  provider_full_count = 0;
  provider_work_count = 0;
  published.clear();
}

template <class P> bool wait(P predicate) {
  std::unique_lock lock(mutex);
  return changed.wait_for(lock, std::chrono::seconds(5), predicate);
}

void multiply(const int8_t *a, size_t sa, const int8_t *b, size_t sb,
              int32_t *c, size_t sc, size_t rows, size_t n, size_t k,
              size_t row0 = 0) {
  for (size_t i = 0; i < rows; ++i)
    for (size_t j = 0; j < n; ++j) {
      int32_t sum = 0;
      for (size_t x = 0; x < k; ++x)
        sum += a[i * sa + x] * b[x * sb + j];
      c[(row0 + i) * sc + j] = sum;
    }
}
} // namespace fake

struct im2p_sim {};
struct im2p_stream {
  std::deque<im2p_stripe_completion_t> done;
  size_t serviced = 0;
};

extern "C" {
im2p_sim_t *im2p_sim_create(void) {
  fake::abi_call();
  if (fake::throw_create)
    throw std::bad_alloc();
  return new im2p_sim;
}
void im2p_sim_destroy(im2p_sim_t *p) {
  fake::abi_call();
  delete p;
}
int im2p_execute_matmul_extended(im2p_sim_t *, const im2p_matmul_desc_t *d,
                                 im2p_work_stats_extended_t *stats) {
  fake::abi_call();
  std::unique_lock lock(fake::mutex);
  fake::full_desc = *d;
  fake::full_entered = true;
  fake::changed.notify_all();
  fake::changed.wait(lock, [] { return !fake::hold_full; });
  lock.unlock();
  fake::multiply(d->activations, d->activation_row_stride, d->weights,
                 d->weight_row_stride, d->output, d->output_row_stride, d->m,
                 d->n, d->k);
  if (stats)
    stats->base.completed_output_tiles = d->m * d->n;
  return IM2P_OK;
}
int im2p_execute_matmul_extended_ex(im2p_sim_t *,
                                    const im2p_matmul_desc_v1_t *d,
                                    im2p_work_stats_extended_t *) {
  fake::abi_call();
  std::lock_guard lock(fake::mutex);
  fake::provider_full_desc = *d;
  ++fake::provider_full_count;
  fake::changed.notify_all();
  return IM2P_OK;
}
int im2p_begin_striped_matmul_ex(im2p_sim_t *, const im2p_stripe_work_desc_t *d,
                                 im2p_stream_t **out) {
  fake::abi_call();
  std::lock_guard lock(fake::mutex);
  fake::work_desc = *d;
  *out = new im2p_stream;
  fake::changed.notify_all();
  return IM2P_OK;
}
int im2p_begin_striped_matmul_v1_ex(im2p_sim_t *,
                                    const im2p_stripe_work_desc_v1_t *d,
                                    im2p_stream_t **out) {
  fake::abi_call();
  std::lock_guard lock(fake::mutex);
  fake::provider_work_desc = *d;
  ++fake::provider_work_count;
  *out = new im2p_stream;
  fake::changed.notify_all();
  return IM2P_OK;
}
int im2p_publish_stripe(im2p_stream_t *, const im2p_activation_stripe_t *s) {
  fake::abi_call();
  std::unique_lock lock(fake::mutex);
  ++fake::publish_attempts;
  fake::publish_entered = true;
  fake::changed.notify_all();
  fake::changed.wait(lock, [] { return !fake::hold_publish; });
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
  fake::changed.notify_all();
  while (fake::allow_completion &&
         fake::progress_calls >= fake::required_progress_cycles &&
         stream->serviced < fake::published.size()) {
    const auto &s = fake::published[stream->serviced++];
    fake::multiply(s.activations, s.activation_row_stride,
                   fake::work_desc.weights, fake::work_desc.weight_row_stride,
                   fake::work_desc.output, fake::work_desc.output_row_stride,
                   s.rows, fake::work_desc.n, fake::work_desc.k, s.i_start);
    stream->done.push_back({s.stripe_id, s.i_start, s.rows, s.context});
  }
  return IM2P_OK;
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
    const auto snap = testing::inspect(*started.run);
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
                snap.a == x.A && snap.b == x.B && snap.c == x.C &&
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
bool test_native_h1_provider_start_contract() {
  fake::reset();
  int8_t a[32]{};
  float out[2]{};
  block_q8_h1 blocks[2]{};
  ggml_gemmini_args_t x{};
  x.I = 1; x.J = 2; x.K = 32;
  x.A = a; x.sA = 32;
  x.f_out = out; x.stride_f_out = 2;
  x.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_h1;
  x.q8_h1_blocks = blocks;
  x.q8_h1_block_count = 2;
  x.q8_h1_rows = 2;
  x.blocks_per_row = 1;
  x.act_quant.storage().emplace<ggml::gemmini::quants::act::tensor::Meta>().scale = 1.0f;
  auto started = execute(&x);
  if (!expect(started.status.ok(), "native H1 starts through provider v1")) return false;
  const auto done = fence(*started.run);
  std::lock_guard lock(fake::mutex);
  const auto &d = fake::provider_full_desc;
  return expect(done.status.ok() && fake::provider_full_count == 1 &&
                d.version == IM2P_PROVIDER_VERSION_1 &&
                d.legacy.weights == nullptr && d.legacy.output == nullptr &&
                d.legacy.m == 1 && d.legacy.n == 2 && d.legacy.k == 32 &&
                d.legacy.weight_row_stride == 2 &&
                d.legacy.output_row_stride == 2 && d.legacy.block_size == 32 &&
                d.legacy.vector_op == IM2P_VECTOR_EXTERNAL &&
                d.provider.context != nullptr && d.provider.read_weight != nullptr &&
                d.provider.read_scale != nullptr && d.provider.write_output != nullptr,
                "native H1 provider descriptor is exact");
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
  return expect(h2.status.code == StatusCode::unsupported_route &&
                    std::strcmp(h2.status.message, "q8_h2 is deprecated") == 0 &&
                    hp2.status.code == StatusCode::unsupported_route &&
                    std::strcmp(hp2.status.message, "q8_hp2 is unsupported") == 0 &&
                    unknown.status.code == StatusCode::unsupported_route &&
                    std::strcmp(unknown.status.message, "unknown Gemmini weight route") == 0 &&
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
      !reject([&](auto &x) {
        x.D = bias;
        x.repeating_bias = true;
      }, "nonzero repeating bias is rejected"))
    return false;

  fake::hold_full = true;
  auto valid = execute(&args);
  if (!valid.status.ok() || !fake::wait([] { return fake::full_entered; }))
    return false;
  args.scale_B = 9.0f;
  args.scale_D = 9;
  args.scale = 9.0f;
  args.bert_scale = 9.0f;
  const auto snapshot = testing::inspect(*valid.run);
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
  auto started = execute(&args, Mode::full);
  if (!expect(started.status.ok() &&
                  fake::wait([] { return fake::full_entered; }),
              "full worker starts"))
    return false;
  args.I = 999;
  args.A = nullptr;
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
                    fake::full_desc.activations == a.data(),
                "scalars snapshotted and pointer identity") &&
         expect(fake::full_desc.tile_i_rows == 2 &&
                    fake::full_desc.tile_j_columns == 2 &&
                    fake::full_desc.block_size == args.block_size_k,
                "tile counts multiply by DIM then clamp to tails; tile_K is "
                "metadata only") &&
         expect(fake::one_owner, "dedicated C ABI owner");
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
  auto started = execute(&args, Mode::stripe_pipeline, {2, 64});
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
  if (!expect(packet_released, "event/RMD ownership not retained"))
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
  return expect(done.status.ok() && again.status.code == done.status.code,
                "sticky idempotent fence") &&
         expect(c == std::vector<int32_t>({4, 5, 10, 11}), "stripe golden") &&
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
  std::vector<int8_t> a(9), b(6);
  std::vector<int32_t> c(6);
  auto args = raw_args(a, b, c, 3);
  auto one = execute(&args, Mode::stripe_pipeline, {1, 32});
  if (!submit_stripe(*one.run, event(0, 0, 1, 4)).ok())
    return false;
  if (!expect(submit_stripe(*one.run, event(1, 1, 2, 4)).code ==
                  StatusCode::backpressure,
              "frontend pressure") ||
      !expect(submit_stripe(*one.run, event(1, 1, 2, 5)).code ==
                  StatusCode::invalid_argument,
              "first run id binding checked before pressure"))
    return false;
  {
    std::lock_guard lock(fake::mutex);
    fake::allow_completion = true;
    fake::changed.notify_all();
  }
  const auto incomplete = fence(*one.run);
  const auto repeated = fence(*one.run);
  if (!expect(incomplete.status.code == StatusCode::invalid_contract &&
                  repeated.status.code == incomplete.status.code,
              "incomplete fence is sticky and idempotent") ||
      !expect(submit_stripe(*one.run, event(1, 1, 2, 4)).code ==
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
  if (!expect(testing::wait_for_closing(*submitted.run),
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
              "truly overlapping fences share one terminal result"))
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
  auto run = execute(&args, Mode::stripe_pipeline, {1, 4096});
  if (!run.status.ok() || !submit_stripe(*run.run, event(0, 0, 1)).ok())
    return false;
  if (!expect(testing::wait_for_completion(*run.run, 1),
              "capacity wait observes a matched completion poll") ||
      !expect(submit_stripe(*run.run, event(1, 1, 2)).ok(),
              "frontend capacity is released after the completion poll") ||
      !expect(fence(*run.run).status.ok(),
              "multi-cycle pipeline fences without deadlock"))
    return false;

  fake::reset();
  fake::required_progress_cycles = 5001;
  std::vector<int8_t> long_a = {1, 2, 3};
  std::vector<int32_t> long_c(2);
  auto long_args = raw_args(long_a, b, long_c, 1);
  auto long_run = execute(&long_args, Mode::stripe_pipeline, {1, 4096});
  if (!long_run.status.ok() ||
      !submit_stripe(*long_run.run, event(0, 0, 1)).ok())
    return false;
  return expect(fence(*long_run.run).status.ok() &&
                    testing::inspect(*long_run.run).completion_generation == 1,
                "valid completion beyond 4096 logical cycles is not rejected");
}

bool test_continuous_refill_completion_generation() {
  fake::reset();
  constexpr size_t stripe_count = 65538;
  std::vector<int8_t> a(stripe_count * 3, 1);
  std::vector<int8_t> b = {1, 0, 0, 1, 1, 1};
  std::vector<int32_t> c(stripe_count * 2);
  auto args = raw_args(a, b, c, stripe_count);
  auto run = execute(&args, Mode::stripe_pipeline, {1, 1});
  if (!run.status.ok())
    return false;
  testing::enable_completion_gate(*run.run);
  if (!submit_stripe(*run.run, event(0, 0, 1)).ok()) {
    testing::disable_completion_gate(*run.run);
    return false;
  }
  bool sequence_ok = true;
  for (size_t completed = 1; completed <= stripe_count; ++completed) {
    if (!expect(testing::wait_for_completion(*run.run, completed),
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
    testing::release_completion_gate(*run.run);
  }
  testing::disable_completion_gate(*run.run);
  const auto done = fence(*run.run);
  return sequence_ok &&
         expect(done.status.ok(),
                "continuous complete-then-refill does not falsely stall") &&
         expect(testing::inspect(*run.run).completion_generation ==
                    stripe_count,
                "worker counts every matched completion monotonically");
}

bool test_logical_stall_bound() {
  fake::reset();
  fake::raw_pressure_forever = true;
  std::vector<int8_t> a = {1, 2, 3}, b = {1, 0, 0, 1, 1, 1};
  std::vector<int32_t> c(2);
  auto args = raw_args(a, b, c, 1);
  auto run = execute(&args, Mode::stripe_pipeline, {1, 8});
  if (!run.status.ok() || !submit_stripe(*run.run, event(0, 0, 1)).ok())
    return false;
  return expect(fence(*run.run).status.code == StatusCode::execution_failure,
                "deterministic logical stall limit");
}
} // namespace

int main() {
  const bool ok =
      test_native_h1_provider_start_contract() &&
      test_rejected_routes_do_not_execute() &&
      test_mode_and_raw_scale_contract() &&
      test_full_golden_and_scalar_snapshot() &&
      test_tile_normalization_validation() && test_pipeline_lifecycle() &&
      test_backpressure_runid_incomplete_and_concurrent() &&
      test_startup_failure_and_destruction() &&
      test_submit_fence_orderings_and_error_stickiness() &&
      test_inflight_progress_and_long_valid_completion() &&
      test_continuous_refill_completion_generation() &&
      test_logical_stall_bound();
  if (ok)
    std::puts("IM2P Gemmini frontend: PASS");
  return ok ? 0 : 1;
}
