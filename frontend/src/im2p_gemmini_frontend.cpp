#include "im2p_gemmini_frontend.hpp"

#include "ggml-gemmini-args.h"
#include "quants/act/exsia/exsia.hpp"
#include "quants/common/weight_route.hpp"

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <limits>
#include <mutex>
#include <new>
#include <thread>
#include <unordered_map>
#include <utility>

#if defined(IM2P_GEMMINI_FRONTEND_EXPECTED_DIM)
static_assert(DIM == IM2P_GEMMINI_FRONTEND_EXPECTED_DIM,
              "Gemmini parameter DIM does not match the selected IM2P RTL");
#endif

namespace im2p::gemmini {
namespace {
namespace wroute = ggml::gemmini::quants::wroute;
namespace exsia = ggml::gemmini::quants::act::exsia;

Status make_status(StatusCode code, Route route, bool native,
                   const char *message) noexcept {
  return {code, route, native, message};
}

Status from_c_status(int value, Route route, const char *operation) noexcept {
  switch (value) {
  case IM2P_OK:
    return make_status(StatusCode::success, route, false, "success");
  case IM2P_BACKPRESSURE:
    return make_status(StatusCode::backpressure, route, false,
                       "IM2P raw queue is full");
  case IM2P_INVALID_LAYOUT:
    return make_status(StatusCode::invalid_contract, route, false,
                       "invalid IM2P operand layout");
  case IM2P_UNFINISHED_STREAM:
    return make_status(StatusCode::invalid_state, route, false,
                       "IM2P simulator already owns a stream");
  case IM2P_DUPLICATE_STRIPE:
    return make_status(StatusCode::invalid_contract, route, false,
                       "duplicate IM2P stripe");
  case IM2P_LATE_STRIPE:
    return make_status(StatusCode::invalid_contract, route, false,
                       "late IM2P stripe");
  default:
    return make_status(StatusCode::execution_failure, route, false, operation);
  }
}

bool checked_mul(size_t left, size_t right, size_t &result) noexcept {
  if (right != 0 && left > std::numeric_limits<size_t>::max() / right)
    return false;
  result = left * right;
  return true;
}

bool normalize_tile_count(size_t count, size_t problem_extent,
                          size_t &rtl_extent) noexcept {
  size_t element_extent = 0;
  const size_t effective_count = count == 0 ? 1 : count;
  if (!checked_mul(effective_count, static_cast<size_t>(DIM), element_extent))
    return false;
  rtl_extent =
      std::min({element_extent, problem_extent, static_cast<size_t>(DIM)});
  return rtl_extent != 0;
}

Route classify_format(const ggml_gemmini_args_t &args) noexcept {
  using F = ggml_gemmini_args_t::im2p_weight_format_t;
  switch (args.weight_format) {
  case F::q8_0_unpacked_to_h1:
    return Route::q8_0_unpacked_to_h1;
  case F::q8_h0:
    return Route::q8_h0;
  case F::q8_h2:
    return Route::q8_h2;
  case F::q8_h1:
    return Route::q8_h1;
  case F::q8_hp1:
    return Route::q8_hp1;
  case F::q8_hp2:
    return Route::q8_hp2;
  case F::q8_channel:
    return Route::q8_channel;
  case F::q8_channel_dense_sidecar:
    return Route::q8_channel_dense_sidecar;
  }
  return Route::unknown;
}

bool native_contract(const ggml_gemmini_args_t &args, Route route) noexcept {
  switch (route) {
  case Route::q8_h1:
    return wroute::is_q8_h1_args(args) && args.has_q8_h1_im2p_contract();
  case Route::q8_h2:
    return wroute::is_q8_h2_args(args) && args.has_q8_h2_im2p_contract();
  case Route::q8_hp1:
    return wroute::has_q8_hp1_native_contract(args);
  case Route::q8_hp2:
    return wroute::has_q8_hp2_native_contract(args);
  case Route::q8_channel:
    return wroute::is_q8_channel_direct_read_args(args) &&
           args.has_q8_channel_direct_read_contract();
  case Route::q8_channel_dense_sidecar:
    return wroute::is_q8_channel_dense_sidecar_args(args);
  default:
    return false;
  }
}

struct ScalarSnapshot {
  size_t i = 0, j = 0, k = 0;
  size_t sa = 0, sb = 0, sc = 0, sd = 0;
  size_t activation_row_offset = 0;
  size_t activation_rows_per_stripe = 0;
  size_t block_size = 0;
  size_t tile_i = 0, tile_j = 0, tile_k = 0;
  size_t blocks_k = 0, blocks_j = 0, blocks_i = 0;
  size_t stripe_j = 0;
  size_t q8_h1_count = 0, q8_h1_rows = 0, blocks_per_row = 0;
  size_t q8_h2_count = 0, q8_h2_blocks_per_row = 0;
  size_t q8_hp1_count = 0, q8_hp1_blocks_per_row = 0;
  size_t q8_hp2_count = 0, q8_hp2_blocks_per_row = 0;
  size_t channel_scale_count = 0, channel_row_stride = 0, channel_row_count = 0;
  size_t col_stride_f_out = 0, stride_f_out = 0;
  ggml_gemmini_args_t::im2p_weight_format_t weight_format{};
  scale_t scale_b = 1.0f;
  scale_acc_t scale_d = 1;
  acc_scale_t scale = 1.0f;
  acc_scale_t bert_scale = 1.0f;
  bool transpose_a = false, transpose_b = false, full_c = false, low_d = false;
  bool weight_i8_scale_active = false;
  int act = 0;
};

struct PointerSnapshot {
  const void *a = nullptr, *b = nullptr, *c = nullptr, *d = nullptr;
  const void *a_fp32 = nullptr, *b_fp32 = nullptr, *b_blocks = nullptr,
             *b_scales = nullptr;
  const void *channel_scales = nullptr, *channel_rows = nullptr;
  const void *q8_h1 = nullptr, *q8_h2 = nullptr, *q8_hp1 = nullptr,
             *q8_hp2 = nullptr;
  const void *c_b = nullptr, *s_rf = nullptr, *r = nullptr,
             *s_rf_stripe = nullptr, *r_stripe = nullptr;
  const void *f_out = nullptr, *model_arch = nullptr, *stripe_sink = nullptr,
             *unpacked_blocks = nullptr;
};

ScalarSnapshot snapshot_scalars(const ggml_gemmini_args_t &a) noexcept {
  return {a.I,
          a.J,
          a.K,
          a.sA,
          a.sB,
          a.sC,
          a.sD,
          a.activation_row_offset,
          a.activation_rows_per_stripe,
          a.block_size_k,
          a.tile_I,
          a.tile_J,
          a.tile_K,
          a.blocks_K,
          a.blocks_J,
          a.blocks_I,
          a.stripe_J,
          a.q8_h1_block_count,
          a.q8_h1_rows,
          a.blocks_per_row,
          a.q8_h2_block_count,
          a.q8_h2_blocks_per_row,
          a.q8_hp1_block_count,
          a.q8_hp1_blocks_per_row,
          a.q8_hp2_block_count,
          a.q8_hp2_blocks_per_row,
          a.weight_channel_scale_count,
          a.q8_channel_row_stride,
          a.q8_channel_row_count,
          a.col_stride_f_out,
          a.stride_f_out,
          a.weight_format,
          a.scale_B,
          a.scale_D,
          a.scale,
          a.bert_scale,
          a.transpose_A,
          a.transpose_B,
          a.full_C,
          a.low_D,
          a.weight_i8_scale_active,
          a.act};
}

PointerSnapshot snapshot_pointers(const ggml_gemmini_args_t &a) noexcept {
  return {a.A,
          a.B,
          a.C,
          a.D,
          a.A_fp32,
          a.B_fp32,
          a.B_blocks,
          a.B_scales,
          a.weight_channel_scales,
          a.q8_channel_row_base,
          a.q8_h1_blocks,
          a.q8_h2_blocks,
          a.q8_hp1_blocks,
          a.q8_hp2_blocks,
          a.c_b,
          a.s_rf,
          a.R,
          a.s_rf_stripe,
          a.R_stripe,
          a.f_out,
          a.model_arch,
          a.exsia_stripe_ready_sink,
          a.unpacked.blocks};
}

} // namespace

struct Run::Impl {
  enum class Lifecycle : uint8_t { idle, starting, running, closing, terminal };

  struct DenseEvent {
    uint64_t run_id = 0;
    size_t stripe_id = 0, slot = 0, row_begin = 0, row_end = 0;
  };

  Impl(const ggml_gemmini_args_t *source, Mode requested_mode,
       Options requested_options) noexcept
      : scalars(snapshot_scalars(*source)),
        pointers(snapshot_pointers(*source)), mode(requested_mode),
        options(requested_options), route(classify_format(*source)),
        native(native_contract(*source, route)),
        final_status(
            make_status(StatusCode::success, route, native, "success")) {}

  ScalarSnapshot scalars;
  PointerSnapshot pointers;
  Mode mode;
  Options options;
  Route route;
  bool native;
  mutable std::mutex mutex;
  std::condition_variable changed;
  std::thread worker;
  Lifecycle lifecycle = Lifecycle::idle;
  bool startup_done = false;
  bool join_in_progress = false;
  Status final_status;
  im2p_work_stats_extended_t stats{};
  std::deque<DenseEvent> ready;
  std::unordered_map<uint32_t, DenseEvent> in_flight;
  size_t outstanding = 0, next_row = 0, next_stripe = 0;
  uint64_t completion_generation = 0;
#if defined(IM2P_GEMMINI_FRONTEND_TESTING)
  bool completion_gate_enabled = false;
  uint64_t completion_gate_permits = 0;
#endif
  bool bound_run = false;
  uint64_t run_id = 0;
  size_t tile_i_rows = 0, tile_j_columns = 0;

  void set_error(Status value) noexcept {
    std::lock_guard lock(mutex);
    if (final_status.ok())
      final_status = value;
    changed.notify_all();
  }

  void worker_failed(Status value) noexcept {
    std::lock_guard lock(mutex);
    if (final_status.ok())
      final_status = value;
    startup_done = true;
    changed.notify_all();
  }

  im2p_matmul_desc_t full_descriptor() const noexcept {
    im2p_matmul_desc_t d{};
    d.activations = static_cast<const int8_t *>(pointers.a);
    d.weights = static_cast<const int8_t *>(pointers.b);
    d.output = static_cast<int32_t *>(const_cast<void *>(pointers.c));
    d.m = scalars.i;
    d.n = scalars.j;
    d.k = scalars.k;
    d.activation_row_stride = scalars.sa == 0 ? scalars.k : scalars.sa;
    d.weight_row_stride = scalars.sb == 0 ? scalars.j : scalars.sb;
    d.output_row_stride = scalars.sc == 0 ? scalars.j : scalars.sc;
    d.tile_i_rows = tile_i_rows;
    d.tile_j_columns = tile_j_columns;
    d.block_size =
        scalars.block_size == 0 ? GGML_GEMMINI_BLOCK_SIZE : scalars.block_size;
    d.vector_op = IM2P_VECTOR_BYPASS;
    return d;
  }

  im2p_stripe_work_desc_t stripe_descriptor() const noexcept {
    const auto f = full_descriptor();
    im2p_stripe_work_desc_t d{};
    d.weights = f.weights;
    d.output = f.output;
    d.m = f.m;
    d.n = f.n;
    d.k = f.k;
    d.weight_row_stride = f.weight_row_stride;
    d.output_row_stride = f.output_row_stride;
    d.tile_i_rows = f.tile_i_rows;
    d.tile_j_columns = f.tile_j_columns;
    d.block_size = f.block_size;
    d.vector_op = f.vector_op;
    const size_t rows = scalars.activation_rows_per_stripe;
    d.stripe_count = (scalars.i + rows - 1) / rows;
    return d;
  }

  struct SimDelete {
    void operator()(im2p_sim_t *p) const noexcept {
      if (p)
        im2p_sim_destroy(p);
    }
  };
  struct StreamDelete {
    void operator()(im2p_stream_t *p) const noexcept {
      if (p)
        im2p_destroy_stream(p);
    }
  };

  void run_full() {
    std::unique_ptr<im2p_sim_t, SimDelete> sim(im2p_sim_create());
    if (!sim) {
      set_error(make_status(StatusCode::execution_failure, route, false,
                            "failed to create IM2P simulator"));
      return;
    }
    const auto d = full_descriptor();
    const int result = im2p_execute_matmul_extended(sim.get(), &d, &stats);
    if (result != IM2P_OK)
      set_error(from_c_status(result, route, "IM2P full execution failed"));
  }

  int publish(im2p_stream_t *stream, const DenseEvent &e) {
    size_t offset = 0;
    const size_t stride = scalars.sa == 0 ? scalars.k : scalars.sa;
    if (!checked_mul(e.row_begin, stride, offset))
      return IM2P_ERROR;
    im2p_activation_stripe_t s{};
    s.stripe_id = static_cast<uint32_t>(e.stripe_id);
    s.i_start = e.row_begin;
    s.rows = e.row_end - e.row_begin;
    s.activations = static_cast<const int8_t *>(pointers.a) + offset;
    s.activation_row_stride = stride;
    s.context = e.run_id;
    const int result = im2p_publish_stripe(stream, &s);
    if (result == IM2P_OK) {
      std::lock_guard lock(mutex);
      in_flight.emplace(s.stripe_id, e);
    }
    return result;
  }

  bool poll(im2p_stream_t *stream, size_t &completion_count) {
    completion_count = 0;
    for (;;) {
      im2p_stripe_completion_t c{};
      const int result = im2p_poll_completed(stream, &c);
      if (result < 0) {
        set_error(from_c_status(result, route, "IM2P completion poll failed"));
        return false;
      }
      if (result == 0) {
#if defined(IM2P_GEMMINI_FRONTEND_TESTING)
        if (completion_count != 0) {
          std::unique_lock lock(mutex);
          changed.wait(lock, [&] {
            return !completion_gate_enabled || completion_gate_permits != 0;
          });
          if (completion_gate_enabled)
            --completion_gate_permits;
        }
#endif
        return true;
      }
      std::lock_guard lock(mutex);
      const auto found = in_flight.find(c.stripe_id);
      if (found == in_flight.end() || found->second.row_begin != c.i_start ||
          found->second.row_end - found->second.row_begin != c.rows ||
          found->second.run_id != c.context) {
        if (final_status.ok())
          final_status = make_status(StatusCode::execution_failure, route,
                                     false, "invalid IM2P completion");
        changed.notify_all();
        return false;
      }
      in_flight.erase(found);
      --outstanding;
      ++completion_count;
      ++completion_generation;
      changed.notify_all();
    }
  }

  void observe_completions(uint64_t &stalled,
                           uint64_t &observed_generation) noexcept {
    std::lock_guard lock(mutex);
    if (completion_generation != observed_generation) {
      observed_generation = completion_generation;
      stalled = 0;
    }
  }

  bool progress(im2p_stream_t *stream, uint64_t &stalled,
                uint64_t &observed_generation, const char *message) {
    size_t completion_count = 0;
    if (im2p_progress_stream(stream, 1) != IM2P_OK ||
        !poll(stream, completion_count)) {
      set_error(
          make_status(StatusCode::execution_failure, route, false, message));
      return false;
    }
    {
      std::lock_guard lock(mutex);
      if (completion_generation != observed_generation) {
        observed_generation = completion_generation;
        stalled = 0;
      } else {
        ++stalled;
      }
    }
    if (stalled > std::max<uint64_t>(options.max_stalled_cycles, 65536)) {
      set_error(make_status(StatusCode::execution_failure, route, false,
                            "IM2P stream exceeded logical stall bound"));
      return false;
    }
    return true;
  }

  void run_pipeline() {
    std::unique_ptr<im2p_sim_t, SimDelete> sim(im2p_sim_create());
    im2p_stream_t *raw = nullptr;
    if (sim) {
      const auto d = stripe_descriptor();
      const int result = im2p_begin_striped_matmul_ex(sim.get(), &d, &raw);
      if (result != IM2P_OK)
        set_error(from_c_status(result, route, "failed to start IM2P stream"));
    } else
      set_error(make_status(StatusCode::execution_failure, route, false,
                            "failed to create IM2P simulator"));
    {
      std::lock_guard lock(mutex);
      startup_done = true;
      changed.notify_all();
    }
    std::unique_ptr<im2p_stream_t, StreamDelete> stream(raw);
    if (!stream)
      return;
    uint64_t stalled = 0;
    uint64_t observed_generation = 0;
    for (;;) {
      DenseEvent event{};
      bool have = false;
      {
        std::unique_lock lock(mutex);
        if (ready.empty() && in_flight.empty() &&
            lifecycle != Lifecycle::closing && final_status.ok()) {
          changed.wait(lock, [&] {
            return !ready.empty() || !in_flight.empty() ||
                   lifecycle == Lifecycle::closing || !final_status.ok();
          });
        }
        if (!final_status.ok())
          break;
        if (!ready.empty()) {
          event = ready.front();
          ready.pop_front();
          have = true;
        } else if (lifecycle == Lifecycle::closing) {
          if (next_row != scalars.i)
            final_status =
                make_status(StatusCode::invalid_contract, route, false,
                            "fence called before all stripes were submitted");
          if (outstanding == 0 || !final_status.ok())
            break;
        }
      }
      if (have) {
        for (;;) {
          const int result = publish(stream.get(), event);
          if (result == IM2P_OK)
            break;
          if (result != IM2P_BACKPRESSURE) {
            set_error(
                from_c_status(result, route, "IM2P stripe publish failed"));
            break;
          }
          if (!progress(stream.get(), stalled, observed_generation,
                        "IM2P progress failed during raw retry"))
            break;
        }
      }
      {
        std::lock_guard lock(mutex);
        if (!final_status.ok())
          break;
      }
      size_t completion_count = 0;
      if (!poll(stream.get(), completion_count))
        break;
      observe_completions(stalled, observed_generation);
      bool complete = false;
      {
        std::unique_lock lock(mutex);
        complete = lifecycle == Lifecycle::closing && ready.empty() &&
                   in_flight.empty() && next_row == scalars.i;
      }
      if (complete)
        break;
      if (!progress(stream.get(), stalled, observed_generation,
                    "IM2P stream progress failed"))
        break;
    }
    bool complete;
    {
      std::lock_guard lock(mutex);
      complete = final_status.ok() && lifecycle == Lifecycle::closing &&
                 next_row == scalars.i && outstanding == 0 && in_flight.empty();
    }
    if (complete) {
      const int result = im2p_finish_stream_extended(stream.get(), &stats);
      if (result != IM2P_OK)
        set_error(from_c_status(result, route, "IM2P stream fence failed"));
    }
  }
};

Run::Run(std::unique_ptr<Impl> impl) noexcept : impl_(std::move(impl)) {}
Run::~Run() noexcept {
  if (impl_)
    (void)fence(*this);
}

ExecuteResult execute(const ggml_gemmini_args_t *args, Mode mode,
                      Options options) noexcept {
  switch (mode) {
  case Mode::full:
  case Mode::stripe_pipeline:
    break;
  default:
    return {make_status(StatusCode::invalid_argument, Route::unknown, false,
                        "invalid IM2P invocation mode"),
            {}};
  }
  if (!args)
    return {make_status(StatusCode::invalid_argument, Route::unknown, false,
                        "null Gemmini args"),
            {}};
  std::unique_ptr<Run::Impl> impl;
  try {
    impl = std::make_unique<Run::Impl>(args, mode, options);
  } catch (const std::bad_alloc &) {
    return {make_status(StatusCode::out_of_memory, Route::unknown, false,
                        "failed to allocate IM2P run"),
            {}};
  } catch (...) {
    return {make_status(StatusCode::execution_failure, Route::unknown, false,
                        "failed to snapshot IM2P run"),
            {}};
  }
  std::unique_ptr<Run> run(new (std::nothrow) Run(std::move(impl)));
  if (!run)
    return {make_status(StatusCode::out_of_memory, Route::unknown, false,
                        "failed to allocate IM2P run"),
            {}};
  auto &x = *run->impl_;
  if (x.route != Route::q8_h0) {
    x.final_status = make_status(
        StatusCode::unsupported_route, x.route, x.native,
        "native Gemmini route is classified but not raw-ABI compatible");
    x.lifecycle = Run::Impl::Lifecycle::terminal;
    return {x.final_status, std::move(run)};
  }
  if (!normalize_tile_count(x.scalars.tile_i, x.scalars.i, x.tile_i_rows) ||
      !normalize_tile_count(x.scalars.tile_j, x.scalars.j, x.tile_j_columns)) {
    x.final_status = make_status(StatusCode::invalid_contract, x.route, false,
                                 "Gemmini tile count overflows element extent");
    x.lifecycle = Run::Impl::Lifecycle::terminal;
    return {x.final_status, std::move(run)};
  }
  const size_t sa = x.scalars.sa == 0 ? x.scalars.k : x.scalars.sa;
  const size_t sb = x.scalars.sb == 0 ? x.scalars.j : x.scalars.sb;
  const size_t sc = x.scalars.sc == 0 ? x.scalars.j : x.scalars.sc;
  if (options.queue_capacity == 0 || options.max_stalled_cycles == 0 ||
      x.scalars.i == 0 || x.scalars.j == 0 || x.scalars.k == 0 ||
      !x.pointers.a || !x.pointers.b || !x.pointers.c)
    x.final_status = make_status(StatusCode::invalid_argument, x.route, false,
                                 "missing IM2P operand, dimension, or option");
  else if (x.scalars.transpose_a || x.scalars.transpose_b ||
           x.scalars.weight_i8_scale_active || !x.scalars.full_c ||
           x.scalars.low_d || x.pointers.d || x.scalars.act != 0 ||
           x.scalars.scale_b != static_cast<scale_t>(1) ||
           x.scalars.scale_d != static_cast<scale_acc_t>(1) ||
           x.scalars.scale != static_cast<acc_scale_t>(1) ||
           x.scalars.bert_scale != static_cast<acc_scale_t>(1))
    x.final_status = make_status(
        StatusCode::unsupported_route, x.route, false,
        "q8_h0 operands or scalar scales are not raw-ABI compatible");
  else if (sa < x.scalars.k || sb < x.scalars.j || sc < x.scalars.j ||
           (mode == Mode::stripe_pipeline &&
            x.scalars.activation_rows_per_stripe == 0))
    x.final_status = make_status(StatusCode::invalid_contract, x.route, false,
                                 "invalid IM2P stride or stripe layout");
  if (!x.final_status.ok()) {
    x.lifecycle = Run::Impl::Lifecycle::terminal;
    return {x.final_status, std::move(run)};
  }
  {
    std::lock_guard lock(x.mutex);
    x.lifecycle = Run::Impl::Lifecycle::starting;
    try {
      auto *impl_ptr = &x;
      x.worker = std::thread([impl_ptr] {
        auto &state = *impl_ptr;
        try {
          if (state.mode == Mode::full)
            state.run_full();
          else
            state.run_pipeline();
        } catch (const std::bad_alloc &) {
          state.worker_failed(make_status(StatusCode::out_of_memory,
                                          state.route, false,
                                          "IM2P worker allocation failed"));
        } catch (...) {
          state.worker_failed(make_status(StatusCode::execution_failure,
                                          state.route, false,
                                          "IM2P worker exception"));
        }
      });
      x.lifecycle = Run::Impl::Lifecycle::running;
    } catch (...) {
      x.lifecycle = Run::Impl::Lifecycle::terminal;
      x.final_status = make_status(StatusCode::execution_failure, x.route,
                                   false, "failed to start IM2P worker");
      return {x.final_status, std::move(run)};
    }
  }
  if (mode == Mode::stripe_pipeline) {
    std::unique_lock lock(x.mutex);
    x.changed.wait(lock,
                   [&] { return x.startup_done || !x.final_status.ok(); });
    if (!x.final_status.ok())
      return {x.final_status, std::move(run)};
  }
  return {make_status(StatusCode::success, x.route, false, "success"),
          std::move(run)};
}

Status submit_stripe(Run &run, const exsia::StripeReadyEvent &e) noexcept {
  auto &x = *run.impl_;
  std::lock_guard lock(x.mutex);
  if (x.lifecycle != Run::Impl::Lifecycle::running ||
      x.mode != Mode::stripe_pipeline)
    return make_status(StatusCode::invalid_state, x.route, false,
                       "run is not accepting stripes");
  if (!x.final_status.ok())
    return x.final_status;
  if (e.stripe_id > std::numeric_limits<uint32_t>::max() ||
      e.stripe_id != x.next_stripe || e.row_begin != x.next_row ||
      e.row_begin >= e.row_end || e.row_end > x.scalars.i ||
      (x.bound_run && e.run_id != x.run_id))
    return make_status(StatusCode::invalid_argument, x.route, false,
                       "invalid stripe run, order, or bounds");
  const size_t rows = e.row_end - e.row_begin,
               expected = x.scalars.activation_rows_per_stripe;
  if ((e.row_end != x.scalars.i && rows != expected) ||
      (e.row_end == x.scalars.i && rows > expected))
    return make_status(StatusCode::invalid_argument, x.route, false,
                       "invalid stripe row count");
  if (x.outstanding >= x.options.queue_capacity)
    return make_status(StatusCode::backpressure, x.route, false,
                       "frontend stripe queue is full");
  Run::Impl::DenseEvent dense{e.run_id, e.stripe_id, e.slot, e.row_begin,
                              e.row_end};
  try {
    x.ready.push_back(dense);
  } catch (const std::bad_alloc &) {
    return make_status(StatusCode::out_of_memory, x.route, false,
                       "failed to queue stripe metadata");
  } catch (...) {
    return make_status(StatusCode::execution_failure, x.route, false,
                       "failed to queue stripe metadata");
  }
  if (!x.bound_run) {
    x.bound_run = true;
    x.run_id = e.run_id;
  }
  ++x.outstanding;
  ++x.next_stripe;
  x.next_row = e.row_end;
  x.changed.notify_all();
  return make_status(StatusCode::success, x.route, false, "success");
}

FenceResult fence(Run &run) noexcept {
  auto &x = *run.impl_;
  std::thread worker;
  {
    std::unique_lock lock(x.mutex);
    if (x.lifecycle == Run::Impl::Lifecycle::idle ||
        x.lifecycle == Run::Impl::Lifecycle::terminal)
      return {x.final_status, x.stats};
    if (x.lifecycle == Run::Impl::Lifecycle::starting ||
        x.lifecycle == Run::Impl::Lifecycle::running) {
      x.lifecycle = Run::Impl::Lifecycle::closing;
      x.changed.notify_all();
    }
    if (x.join_in_progress) {
      x.changed.wait(
          lock, [&] { return x.lifecycle == Run::Impl::Lifecycle::terminal; });
      return {x.final_status, x.stats};
    }
    x.join_in_progress = true;
    worker = std::move(x.worker);
  }
  if (worker.joinable())
    worker.join();
  {
    std::lock_guard lock(x.mutex);
    x.join_in_progress = false;
    x.lifecycle = Run::Impl::Lifecycle::terminal;
    x.changed.notify_all();
    return {x.final_status, x.stats};
  }
}

#if defined(IM2P_GEMMINI_FRONTEND_TESTING)
testing::Snapshot testing::inspect(const Run &run) noexcept {
  const auto &x = *run.impl_;
  std::lock_guard lock(x.mutex);
  testing::Snapshot view{};
  view.i = x.scalars.i;
  view.j = x.scalars.j;
  view.k = x.scalars.k;
  view.sa = x.scalars.sa;
  view.sb = x.scalars.sb;
  view.sc = x.scalars.sc;
  view.sd = x.scalars.sd;
  view.activation_row_offset = x.scalars.activation_row_offset;
  view.activation_rows_per_stripe = x.scalars.activation_rows_per_stripe;
  view.block_size_k = x.scalars.block_size;
  view.tile_i = x.scalars.tile_i;
  view.tile_j = x.scalars.tile_j;
  view.tile_k = x.scalars.tile_k;
  view.weight_format = static_cast<uint8_t>(x.scalars.weight_format);
  view.a = x.pointers.a;
  view.b = x.pointers.b;
  view.c = x.pointers.c;
  view.d = x.pointers.d;
  view.a_fp32 = x.pointers.a_fp32;
  view.b_fp32 = x.pointers.b_fp32;
  view.b_blocks = x.pointers.b_blocks;
  view.b_scales = x.pointers.b_scales;
  view.blocks_k = x.scalars.blocks_k;
  view.blocks_j = x.scalars.blocks_j;
  view.blocks_i = x.scalars.blocks_i;
  view.c_b = x.pointers.c_b;
  view.s_rf = x.pointers.s_rf;
  view.r = x.pointers.r;
  view.s_rf_stripe = x.pointers.s_rf_stripe;
  view.r_stripe = x.pointers.r_stripe;
  view.stripe_j = x.scalars.stripe_j;
  view.q8_h1 = x.pointers.q8_h1;
  view.q8_h1_count = x.scalars.q8_h1_count;
  view.q8_h1_rows = x.scalars.q8_h1_rows;
  view.blocks_per_row = x.scalars.blocks_per_row;
  view.q8_h2 = x.pointers.q8_h2;
  view.q8_h2_count = x.scalars.q8_h2_count;
  view.q8_h2_blocks_per_row = x.scalars.q8_h2_blocks_per_row;
  view.q8_hp1 = x.pointers.q8_hp1;
  view.q8_hp1_count = x.scalars.q8_hp1_count;
  view.q8_hp1_blocks_per_row = x.scalars.q8_hp1_blocks_per_row;
  view.q8_hp2 = x.pointers.q8_hp2;
  view.q8_hp2_count = x.scalars.q8_hp2_count;
  view.q8_hp2_blocks_per_row = x.scalars.q8_hp2_blocks_per_row;
  view.channel_scales = x.pointers.channel_scales;
  view.channel_scale_count = x.scalars.channel_scale_count;
  view.channel_rows = x.pointers.channel_rows;
  view.channel_row_stride = x.scalars.channel_row_stride;
  view.channel_row_count = x.scalars.channel_row_count;
  view.f_out = x.pointers.f_out;
  view.col_stride_f_out = x.scalars.col_stride_f_out;
  view.stride_f_out = x.scalars.stride_f_out;
  view.model_arch = x.pointers.model_arch;
  view.stripe_sink = x.pointers.stripe_sink;
  view.unpacked_blocks = x.pointers.unpacked_blocks;
  view.scale_b = static_cast<float>(x.scalars.scale_b);
  view.scale_d = static_cast<uint32_t>(x.scalars.scale_d);
  view.scale = static_cast<float>(x.scalars.scale);
  view.bert_scale = static_cast<float>(x.scalars.bert_scale);
  view.completion_generation = x.completion_generation;
  return view;
}

bool testing::wait_for_completion(Run &run, uint64_t target) noexcept {
  auto &x = *run.impl_;
  std::unique_lock lock(x.mutex);
  return x.changed.wait_for(lock, std::chrono::seconds(5), [&] {
    return x.completion_generation >= target || !x.final_status.ok();
  }) && x.completion_generation >= target;
}

bool testing::wait_for_closing(Run &run) noexcept {
  auto &x = *run.impl_;
  std::unique_lock lock(x.mutex);
  return x.changed.wait_for(lock, std::chrono::seconds(5), [&] {
    return x.lifecycle == Run::Impl::Lifecycle::closing ||
           x.lifecycle == Run::Impl::Lifecycle::terminal;
  });
}

void testing::enable_completion_gate(Run &run) noexcept {
  auto &x = *run.impl_;
  std::lock_guard lock(x.mutex);
  x.completion_gate_enabled = true;
}

void testing::release_completion_gate(Run &run) noexcept {
  auto &x = *run.impl_;
  std::lock_guard lock(x.mutex);
  ++x.completion_gate_permits;
  x.changed.notify_all();
}

void testing::disable_completion_gate(Run &run) noexcept {
  auto &x = *run.impl_;
  std::lock_guard lock(x.mutex);
  x.completion_gate_enabled = false;
  x.changed.notify_all();
}
#endif

} // namespace im2p::gemmini
