#include "im2p_cpu_functional_internal.hpp"
#include "im2p_cpu_functional.hpp"
#include <algorithm>
#include <cstdint>
#include <deque>
#include <iterator>
#include <memory>
#include <new>
#if defined(IM2P_CPU_FUNCTIONAL_TEST_HOOKS)
#include <mutex>
namespace im2p::cpu_functional {
namespace {
std::mutex observation_mutex;
DispatchCallback dispatch_callback = nullptr;
void *dispatch_context = nullptr;
}
void set_dispatch_observer(DispatchCallback callback, void *context) noexcept {
  std::lock_guard lock(observation_mutex);
  dispatch_callback = callback;
  dispatch_context = context;
}
void observe(const im2p_matmul_desc_t &d,
             const im2p_production_geometry_v1_t &g,
             const im2p_compact_runs_t *runs = nullptr) noexcept {
  std::lock_guard lock(observation_mutex);
  if (dispatch_callback) dispatch_callback(dispatch_context, d, g, runs);
}
}
#endif

namespace cf = im2p::cpu_functional;

namespace {
// Paired residual operands are the stream's own, indexed through the runs:
// compact W row k is original row block * 32 + bit, its scale row is block.
// This is the mapping the RTL indexed preload must reproduce.
struct PairedGather {
  const cf::Operands *stream;
  const std::vector<im2p_compact_run_t> *runs;
};
int gather_weight(void *context, size_t k, size_t column, size_t count,
                  int8_t *out) {
  const auto &g = *static_cast<const PairedGather *>(context);
  const auto run = std::prev(std::upper_bound(
      g.runs->begin(), g.runs->end(), k,
      [](size_t value, const im2p_compact_run_t &r) {
        return value < r.compact_k_begin;
      }));
  uint32_t mask = run->original_k_mask;
  for (size_t skip = k - run->compact_k_begin; skip; --skip)
    mask &= mask - 1;
  const size_t row = size_t(run->original_block_id) * 32 + __builtin_ctz(mask);
  const size_t n = g.stream->descriptor.n;
  std::copy_n(g.stream->weights.data() + row * n + column, count, out);
  return IM2P_OK;
}
int gather_scale(void *context, size_t index, size_t column, size_t count,
                 uint32_t *out) {
  const auto &g = *static_cast<const PairedGather *>(context);
  const size_t n = g.stream->descriptor.n;
  std::copy_n(g.stream->scales.data() +
                  size_t((*g.runs)[index].original_block_id) * n + column,
              count, out);
  return IM2P_OK;
}
} // namespace
static_assert(IM2P_ACTIVATION_BITS == IM2P_WEIGHT_BITS &&
              (IM2P_ACTIVATION_BITS == 4 || IM2P_ACTIVATION_BITS == 8));
static_assert(IM2P_DIM == 16 || IM2P_DIM == 32 || IM2P_DIM == 64);

struct im2p_sim {
  std::shared_ptr<bool> busy = std::make_shared<bool>(false);
};
struct im2p_stream {
  cf::Operands operands;
  im2p_production_geometry_v1_t geometry{};
  std::shared_ptr<bool> busy;
  std::deque<im2p_stripe_completion_extended_t> completed;
  im2p_work_stats_extended_t stats{};
  size_t expected_stripes = 0;
  bool finished = false;
  bool failed = false;
  ~im2p_stream() {
    if (busy)
      *busy = false;
  }
};

extern "C" {
im2p_sim_t *im2p_sim_create() {
  try {
    return new im2p_sim;
  } catch (...) {
    return nullptr;
  }
}
void im2p_sim_destroy(im2p_sim_t *sim) { delete sim; }
uint32_t im2p_sim_abi_version() { return IM2P_ABI_VERSION; }
uint32_t im2p_sim_activation_bits() { return IM2P_ACTIVATION_BITS; }
uint32_t im2p_sim_weight_bits() { return IM2P_WEIGHT_BITS; }
uint32_t im2p_sim_dim() { return IM2P_DIM; }
uint32_t im2p_sim_activation_storage_bytes() { return 1; }
uint32_t im2p_sim_weight_storage_bytes() { return 1; }
uint32_t im2p_compiled_accumulator_bits() { return 32; }
uint32_t im2p_compiled_accumulator_rows() { return IM2P_ACCUMULATOR_ROWS; }
uint32_t im2p_compiled_partial_bits() { return IM2P_PARTIAL_BITS; }
const char *im2p_compiled_numerical_semantics_revision() {
  return IM2P_SCU_NUMERICAL_REVISION;
}
const char *im2p_sim_implementation() { return "CPU_FUNCTIONAL"; }
void im2p_set_rtl_log_callback(im2p_rtl_log_fn, void *) {}

int im2p_execute_matmul_planned(im2p_sim_t *sim, const im2p_matmul_desc_t *d,
                                const im2p_production_geometry_v1_t *g,
                                im2p_work_stats_extended_t *stats) {
  if (!sim || !d || !g)
    return IM2P_INVALID_LAYOUT;
  if (*sim->busy)
    return IM2P_UNFINISHED_STREAM;
  if (!cf::geometry(*g, *d, IM2P_GEOMETRY_FULL))
    return IM2P_INVALID_LAYOUT;
  try {
    cf::Operands operands;
    int status = cf::prepare(operands, *d);
    if (status == IM2P_OK)
      status = cf::execute(operands, d->activations,
                           d->activation_row_stride_bytes, 0, d->m);
    if (status == IM2P_OK && stats)
      *stats = {};
#if defined(IM2P_CPU_FUNCTIONAL_TEST_HOOKS)
    if (status == IM2P_OK) cf::observe(*d, *g);
#endif
    return status;
  } catch (...) {
    return IM2P_ERROR;
  }
}

int im2p_execute_matmul_planned_runs(im2p_sim_t *sim,
                                     const im2p_matmul_desc_t *d,
                                     const im2p_production_geometry_v1_t *g,
                                     const im2p_compact_runs_t *view,
                                     im2p_work_stats_extended_t *stats) {
  if (!sim || !d || !g || !view ||
      reinterpret_cast<std::uintptr_t>(view) %
          alignof(im2p_compact_runs_t) ||
      !view->runs ||
      reinterpret_cast<std::uintptr_t>(view->runs) %
          alignof(im2p_compact_run_t) ||
      view->run_count > d->k ||
      view->run_count > SIZE_MAX / sizeof(im2p_compact_run_t))
    return IM2P_INVALID_LAYOUT;
  if (*sim->busy) return IM2P_UNFINISHED_STREAM;
  if (!cf::geometry(*g, *d, IM2P_GEOMETRY_FULL) ||
      view->version != IM2P_COMPACT_RUNS_VERSION ||
      view->struct_size != sizeof(*view) || !view->original_k ||
      !view->run_count)
    return IM2P_INVALID_LAYOUT;
  try {
    const std::vector<im2p_compact_run_t> runs(view->runs,
                                               view->runs + view->run_count);
    cf::Operands operands;
    int status = cf::prepare_runs(operands, *d, runs, view->original_k);
    if (status == IM2P_OK) status = cf::execute_runs(operands, runs);
    if (status == IM2P_OK && stats) *stats = {};
#if defined(IM2P_CPU_FUNCTIONAL_TEST_HOOKS)
    if (status == IM2P_OK) cf::observe(*d, *g, view);
#endif
    return status;
  } catch (...) {
    return IM2P_ERROR;
  }
}

int im2p_begin_striped_matmul_planned(im2p_sim_t *sim,
                                      const im2p_stripe_work_desc_t *d,
                                      const im2p_production_geometry_v1_t *g,
                                      im2p_stream_t **out) {
  if (!sim || !d || !g || !out)
    return IM2P_INVALID_LAYOUT;
  if (*sim->busy)
    return IM2P_UNFINISHED_STREAM;
  const auto full = cf::descriptor(*d);
  if (!cf::geometry(*g, full, IM2P_GEOMETRY_STREAM) ||
      d->stripe_count != (d->m - 1) / g->stripe_rows + 1)
    return IM2P_INVALID_LAYOUT;
  try {
    auto stream = std::make_unique<im2p_stream>();
    const int status = cf::prepare(stream->operands, full);
    if (status != IM2P_OK)
      return status;
    stream->geometry = *g;
    stream->expected_stripes = d->stripe_count;
    stream->busy = sim->busy;
    *sim->busy = true;
    *out = stream.release();
    return IM2P_OK;
  } catch (...) {
    return IM2P_ERROR;
  }
}

int im2p_publish_stripe_planned(im2p_stream_t *stream,
                                const im2p_activation_stripe_t *stripe,
                                const im2p_production_geometry_v1_t *g) {
  if (!stream || !stripe || !g)
    return IM2P_INVALID_LAYOUT;
  if (stream->finished || stream->failed)
    return IM2P_LATE_STRIPE;
  if (!cf::identity(stripe->abi_version, stripe->activation_bits,
                    stripe->activation_storage_bytes, stripe->weight_bits,
                    stripe->weight_storage_bytes, stripe->dim))
    return IM2P_CONFIGURATION_MISMATCH;
  auto &stats = stream->stats.base;
  if (stripe->stripe_id < stats.stripes_published ||
      stripe->i_start < stats.stripe_rows_published)
    return IM2P_DUPLICATE_STRIPE;
  if (!cf::geometry(*g, stream->operands.descriptor, IM2P_GEOMETRY_STRIPE) ||
      stripe->stripe_id != stats.stripes_published ||
      stripe->i_start != stats.stripe_rows_published ||
      g->stripe_id != stripe->stripe_id || g->row_begin != stripe->i_start ||
      g->row_count != stripe->rows ||
      g->stripe_rows != stream->geometry.stripe_rows ||
      (stripe->i_start + stripe->rows < g->m && stripe->rows != g->stripe_rows))
    return IM2P_INVALID_LAYOUT;
  if (stream->completed.size() == 2)
    return IM2P_BACKPRESSURE;
  try {
    const im2p_stripe_completion_extended_t completion{
        {stripe->stripe_id, stripe->i_start, stripe->rows, stripe->context},
        0,
        0,
        0};
    stream->completed.push_back(completion);
    const int status = cf::execute(stream->operands, stripe->activations,
                                   stripe->activation_row_stride_bytes,
                                   stripe->i_start, stripe->rows);
    if (status != IM2P_OK) {
      stream->completed.pop_back();
      stream->failed = true;
      return status;
    }
    ++stats.stripes_published;
    stats.stripe_rows_published += stripe->rows;
    ++stats.completed_stripes;
#if defined(IM2P_CPU_FUNCTIONAL_TEST_HOOKS)
    auto observed = stream->operands.descriptor;
    observed.activations = stripe->activations;
    observed.activation_row_stride_bytes = stripe->activation_row_stride_bytes;
    cf::observe(observed, *g);
#endif
    return IM2P_OK;
  } catch (...) {
    stream->failed = true;
    return IM2P_ERROR;
  }
}

int im2p_publish_stripe_paired(im2p_stream_t *stream,
                               const im2p_activation_stripe_t *stripe,
                               const im2p_production_geometry_v1_t *g,
                               const im2p_paired_residual_v1_t *residual) {
  if (residual && (residual->version != IM2P_PAIRED_RESIDUAL_VERSION ||
                   residual->struct_size != sizeof(*residual)))
    return IM2P_INVALID_LAYOUT;
  if (!residual || !residual->rows)
    return im2p_publish_stripe_planned(stream, stripe, g);
  if (!stream)
    return IM2P_INVALID_LAYOUT;
  if (stream->finished || stream->failed)
    return IM2P_LATE_STRIPE;
  const auto &main = stream->operands.descriptor;
  const auto *view = residual->runs;
  if (!view ||
      reinterpret_cast<std::uintptr_t>(view) % alignof(im2p_compact_runs_t) ||
      view->version != IM2P_COMPACT_RUNS_VERSION ||
      view->struct_size != sizeof(*view) || !view->runs ||
      reinterpret_cast<std::uintptr_t>(view->runs) %
          alignof(im2p_compact_run_t) ||
      !view->run_count || view->run_count > residual->compact_k ||
      view->original_k != main.k || main.scale_total_k != main.k ||
      !residual->output || residual->output_row_stride < main.n ||
      residual->rows - 1 > (size_t(PTRDIFF_MAX) / sizeof(int32_t) - main.n) /
                               residual->output_row_stride)
    return IM2P_INVALID_LAYOUT;
  try {
    const std::vector<im2p_compact_run_t> runs(view->runs,
                                               view->runs + view->run_count);
    PairedGather gather{&stream->operands, &runs};
    // The residual lands in a staging buffer so a rejected Main publish writes nothing.
    std::vector<int32_t> staged(residual->rows * main.n);
    im2p_matmul_desc_t d = main;
    d.activations = residual->activations;
    d.weights = nullptr;
    d.scales = nullptr;
    d.output = staged.data();
    d.m = residual->rows;
    d.k = residual->compact_k;
    d.activation_row_stride_bytes = residual->activation_row_stride_bytes;
    d.weight_row_stride_bytes = main.n;
    d.output_row_stride = main.n;
    d.provider = {&gather, gather_weight, nullptr, gather_scale, nullptr};
    cf::Operands operands;
    int status = cf::prepare_runs(operands, d, runs, view->original_k);
    if (status == IM2P_OK)
      status = cf::execute_runs(operands, runs);
    if (status == IM2P_OK)
      status = im2p_publish_stripe_planned(stream, stripe, g);
    if (status != IM2P_OK)
      return status;
    for (size_t row = 0; row < residual->rows; ++row)
      std::copy_n(staged.data() + row * main.n, main.n,
                  residual->output + row * residual->output_row_stride);
    return IM2P_OK;
  } catch (...) {
    return IM2P_ERROR;
  }
}

int im2p_progress_stream(im2p_stream_t *stream, uint64_t) {
  return stream && !stream->failed ? IM2P_OK : IM2P_ERROR;
}
uint64_t im2p_stream_cycle_count(const im2p_stream_t *) { return 0; }
uint64_t im2p_stream_progress_count(const im2p_stream_t *) { return 0; }
int im2p_poll_completed_extended(im2p_stream_t *stream,
                                 im2p_stripe_completion_extended_t *out) {
  if (!stream || !out || stream->failed)
    return IM2P_ERROR;
  if (stream->completed.empty())
    return 0;
  *out = stream->completed.front();
  stream->completed.pop_front();
  return 1;
}
int im2p_poll_completed(im2p_stream_t *stream, im2p_stripe_completion_t *out) {
  if (!out)
    return IM2P_ERROR;
  im2p_stripe_completion_extended_t extended{};
  const int status = im2p_poll_completed_extended(stream, &extended);
  if (status == 1)
    *out = extended.base;
  return status;
}
int im2p_finish_stream_extended(im2p_stream_t *stream,
                                im2p_work_stats_extended_t *stats) {
  if (!stream || stream->failed)
    return IM2P_ERROR;
  if (stream->stats.base.stripe_rows_published != stream->geometry.m ||
      stream->stats.base.stripes_published != stream->expected_stripes)
    return IM2P_UNFINISHED_STREAM;
  stream->finished = true;
  if (stream->busy) {
    *stream->busy = false;
    stream->busy.reset();
  }
  if (stats)
    *stats = stream->stats;
  return IM2P_OK;
}
int im2p_finish_stream(im2p_stream_t *stream, im2p_work_stats_t *stats) {
  im2p_work_stats_extended_t extended{};
  const int status = im2p_finish_stream_extended(stream, &extended);
  if (status == IM2P_OK && stats)
    *stats = extended.base;
  return status;
}
void im2p_destroy_stream(im2p_stream_t *stream) { delete stream; }
int im2p_execute_matmul(im2p_sim_t *, const im2p_matmul_desc_t *,
                        im2p_work_stats_t *) {
  return IM2P_INVALID_LAYOUT;
}
int im2p_execute_matmul_extended(im2p_sim_t *, const im2p_matmul_desc_t *,
                                 im2p_work_stats_extended_t *) {
  return IM2P_INVALID_LAYOUT;
}
int im2p_begin_striped_matmul(im2p_sim_t *, const im2p_stripe_work_desc_t *,
                              im2p_stream_t **) {
  return IM2P_INVALID_LAYOUT;
}
int im2p_publish_stripe(im2p_stream_t *, const im2p_activation_stripe_t *) {
  return IM2P_INVALID_LAYOUT;
}
}
