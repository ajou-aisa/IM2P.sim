#pragma once

#include <gemmini/cycle_sim_log.hpp>

#if CYCLE_SIM
#include "im2p_cpu_functional.hpp"
#include "ggml-gemmini-matmul.hpp"
#include <gemmini/cpu_log_context.hpp>
#include <gemmini/log.hpp>
#include <im2p_sim.h>
#include <algorithm>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <vector>

namespace im2p::gemmini::cycle_sim {
namespace log = ggml::gemmini::cycle_sim;

template <class Descriptor>
log::Work project(const Descriptor &d,
                  const im2p_production_geometry_v1_t &g) {
  if (d.abi_version != IM2P_ABI_VERSION ||
      g.version != IM2P_PRODUCTION_GEOMETRY_VERSION ||
      g.struct_size != sizeof(g) || d.m != g.m || d.n != g.n || d.k != g.k ||
      d.activation_bits != g.activation_bits || d.weight_bits != g.weight_bits ||
      d.dim != g.dim || d.output_row_stride > UINT64_MAX / sizeof(int32_t))
    throw std::runtime_error("cycle-sim: final descriptor/geometry mismatch");
  log::Work work;
  work.geometry = g;
  work.m = g.row_count;
  work.row_begin = g.row_begin;
  work.row_count = g.row_count;
  work.weight_stride_bytes = d.weight_row_stride_bytes;
  work.output_stride_bytes = d.output_row_stride * sizeof(int32_t);
  work.scale_stride_elements = d.scale_row_stride;
  work.block_size = static_cast<uint32_t>(d.block_size);
  work.vector_op = d.vector_op;
  work.output_domain = d.output_domain;
  work.work_context = d.work_context;
  return work;
}

inline log::Work full(const im2p_matmul_desc_t &d,
                      const im2p_production_geometry_v1_t &g) {
  if (g.scope != IM2P_GEOMETRY_FULL || g.row_begin != 0 || g.row_count != d.m)
    throw std::runtime_error("cycle-sim: final FULL geometry mismatch");
  auto work = project(d, g);
  work.activation_stride_bytes = d.activation_row_stride_bytes;
  return work;
}

inline log::Work stripe(const im2p_stripe_work_desc_t &d,
                        const im2p_activation_stripe_t &s,
                        const im2p_production_geometry_v1_t &g, uint64_t slot) {
  if (g.scope != IM2P_GEOMETRY_STRIPE || g.row_begin != s.i_start ||
      g.row_count != s.rows || g.stripe_id != s.stripe_id ||
      s.activation_bits != g.activation_bits || s.weight_bits != g.weight_bits ||
      s.dim != g.dim)
    throw std::runtime_error("cycle-sim: final stripe geometry mismatch");
  auto work = project(d, g);
  work.scope = "stripe";
  work.activation_stride_bytes = s.activation_row_stride_bytes;
  work.stripe_id = s.stripe_id;
  work.host_slot = slot;
  work.work_context = s.context;
  return work;
}

inline void record_cpu_stage(const char *layer, const char *stage,
                             const ggml::gemmini::MatmulCpuSample &start,
                             const ggml::gemmini::MatmulCpuSample &end,
                             bool success) noexcept {
#if LOG_CYCLE
  try {
    ggml::gemmini::log::CycleRecord record{layer, stage, 0, 0, nullptr, 0, nullptr,
        ggml::gemmini::kNativeCycleSource, ggml::gemmini::kNativeCycleUnit};
    record.timing_interval_class = start.correlation.host_stage_id != UINT64_MAX
        ? ggml::gemmini::cycle::TimingIntervalClass::canonical_additive
        : ggml::gemmini::cycle::TimingIntervalClass::diagnostic;
    ggml::gemmini::log::cycle.write_json(
        ggml::gemmini::serialize_matmul_cpu_interval(record, start, end, success));
  } catch (...) {
    ggml::gemmini::log::cycle.report_failure("CPU-functional host stage");
  }
#else
  (void)layer; (void)stage; (void)start; (void)end; (void)success;
#endif
}

class HostStageScope {
public:
  HostStageScope(log::Context source, const char *name, const char *execution_class,
                  const char *owner, const char *location, const char *layer = nullptr,
                  const std::vector<uint64_t> &required_work = {},
                  const std::vector<uint64_t> &required_host = {},
                  bool measure_cpu = false) noexcept
      : source_(std::move(source)), name_(name), layer_(layer), measure_(measure_cpu) {
    if (!source_) return;
    try {
      stage_ = source_.session->host_stage_begin(source_,
          {name, execution_class, owner, location, required_work, required_host});
      scope_ = std::make_unique<log::ScopedContext>(stage_);
      if (measure_ && std::strcmp(execution_class, "POTAL_HOST") == 0)
        start_ = ggml::gemmini::read_matmul_cpu_sample();
    } catch (...) { source_.session->record_failure("host stage declaration failed"); }
  }
  ~HostStageScope() { if (!finished_) finish(false); }
  std::optional<uint64_t> id() const noexcept { return stage_.host_stage_id; }
  void finish(bool success = true) noexcept {
    if (finished_) return;
    finished_ = true;
    if (!stage_) return;
    if (measure_ && start_.collected)
      record_cpu_stage(layer_, name_, start_, ggml::gemmini::read_matmul_cpu_sample(), success);
    try { stage_.session->host_stage_end(stage_, success); }
    catch (...) { stage_.session->record_failure("host stage completion failed"); }
    scope_.reset();
  }
private:
  log::Context source_, stage_;
  const char *name_, *layer_;
  bool measure_, finished_ = false;
  ggml::gemmini::MatmulCpuSample start_;
  std::unique_ptr<log::ScopedContext> scope_;
};

inline void append_host_dependencies(std::vector<uint64_t> &destination,
                                     const std::vector<uint64_t> &source) {
  for (const auto id : source)
    if (std::find(destination.begin(), destination.end(), id) == destination.end())
      destination.push_back(id);
}

#if defined(IM2P_CPU_FUNCTIONAL_TEST_HOOKS)
inline thread_local void (*publication_observer)(void *) = nullptr;
inline thread_local void *publication_observer_context = nullptr;
inline void set_publication_observer(void (*observer)(void *), void *context) noexcept {
  publication_observer = observer;
  publication_observer_context = context;
}
#endif

template <typename T, typename Copy>
bool publish_output(const log::Context &context, const char *name, const char *owner,
                    const char *location, const char *layer, T *destination,
                    size_t rows, size_t columns, size_t row_stride, size_t column_stride,
                    const std::vector<uint64_t> &required_work,
                    const std::vector<uint64_t> &required_host, Copy copy) noexcept {
  if (!context) { copy(); return true; }
  std::vector<unsigned char> original;
  bool copied = false;
  try {
    {
      ggml::gemmini::log::ScopedFunctionalEmulationSuppression collection_only_backup;
      if (columns && rows > SIZE_MAX / columns / sizeof(T))
        throw std::runtime_error("output rollback extent overflow");
      original.resize(rows * columns * sizeof(T));
      for (size_t row = 0; row < rows; ++row)
        for (size_t column = 0; column < columns; ++column)
          std::memcpy(original.data() + (row * columns + column) * sizeof(T),
                      destination + row * row_stride + column * column_stride, sizeof(T));
    }
    HostStageScope stage(context, name, "POTAL_HOST", owner, location, layer,
                         required_work, required_host, true);
    context.session->ensure_healthy();
    copied = true;
    copy();
#if defined(IM2P_CPU_FUNCTIONAL_TEST_HOOKS)
    if (publication_observer) publication_observer(publication_observer_context);
#endif
    stage.finish();
    context.session->ensure_healthy();
    return true;
  } catch (...) {
    if (copied) {
      ggml::gemmini::log::ScopedFunctionalEmulationSuppression collection_only_restore;
      for (size_t row = 0; row < rows; ++row)
        for (size_t column = 0; column < columns; ++column)
          std::memcpy(destination + row * row_stride + column * column_stride,
                      original.data() + (row * columns + column) * sizeof(T), sizeof(T));
    }
    context.session->record_failure("output publication provenance failed; original bytes restored");
    return false;
  }
}

inline thread_local std::vector<uint64_t> *active_work_collector = nullptr;
inline thread_local std::optional<uint64_t> continuation_work_id;
inline std::vector<uint64_t> continuation_work_ids() {
  return continuation_work_id ? std::vector<uint64_t>{*continuation_work_id} : std::vector<uint64_t>{};
}
class WorkCollector {
public:
  explicit WorkCollector(std::vector<uint64_t> &owned, bool only_if_absent = false)
      : previous_(active_work_collector) {
    if (!only_if_absent || !active_work_collector) active_work_collector = &owned;
  }
  ~WorkCollector() { active_work_collector = previous_; }
  WorkCollector(const WorkCollector &) = delete;
  WorkCollector &operator=(const WorkCollector &) = delete;
private:
  std::vector<uint64_t> *previous_;
};
inline size_t work_count() noexcept {
  return active_work_collector ? active_work_collector->size() : 0;
}
inline std::vector<uint64_t> work_ids_since(size_t begin) {
  return active_work_collector
      ? std::vector<uint64_t>(active_work_collector->begin() + begin, active_work_collector->end())
      : std::vector<uint64_t>{};
}

class StageCall {
public:
  StageCall(log::Context context, log::CallKind kind,
            size_t required_begin = SIZE_MAX) noexcept : source_(std::move(context)) {
    if (!source_) return;
    try {
      call_ = source_.session->call_begin(source_, kind);
      call_.session->call_event(call_, log::CallStage::Invoke);
      if (required_begin != SIZE_MAX) {
        const auto required = work_ids_since(required_begin);
        if (!required.empty())
          call_.session->call_event(call_, log::CallStage::CompleteRequired, required);
      }
      scope_ = std::make_unique<log::ScopedContext>(call_);
    } catch (...) { source_.session->record_failure("cycle-sim: host call declaration failed"); }
  }
  ~StageCall() {
    if (source_ && !finished_) source_.session->record_failure("cycle-sim: unfinished host call");
  }
  void finish() noexcept {
    finished_ = true;
    if (!call_) return;
    try { call_.session->call_event(call_, log::CallStage::Continuation); }
    catch (...) { call_.session->record_failure("cycle-sim: host call continuation failed"); }
  }
private:
  log::Context source_, call_;
  std::unique_ptr<log::ScopedContext> scope_;
  bool finished_ = false;
};

class DispatchEvents {
public:
  explicit DispatchEvents(log::Context context, const log::Work *work = nullptr,
                          log::CallKind kind = log::CallKind::Full)
      : context_(std::move(context)), work_(work), kind_(kind),
        collector_(active_work_collector) {}
  cpu_functional::TimingObserver observer() noexcept { return {observe, this}; }
  void complete() {
    if (!call_) return;
    call_.session->ensure_healthy();
  }
  const std::vector<uint64_t> &required_work() const noexcept { return selected_work_ids_; }
private:
  static void observe(void *opaque, const char *stage, bool begin) noexcept {
    auto &self = *static_cast<DispatchEvents *>(opaque);
    try {
      const bool output = std::strcmp(stage, "functional.output") == 0;
      if (!output) {
        if (begin) {
          self.suppression_ = std::make_unique<ggml::gemmini::log::ScopedFunctionalEmulationSuppression>();
          if (self.context_ && self.work_ && !self.work_id_) {
            self.call_ = self.context_.session->call_begin(self.context_, self.kind_);
            self.call_.session->call_event(self.call_, log::CallStage::Invoke);
            self.work_id_ = self.call_.session->work(self.call_, *self.work_);
            self.selected_work_ids_.push_back(*self.work_id_);
            if (self.collector_) self.collector_->push_back(*self.work_id_);
            if (self.kind_ == log::CallKind::Stripe)
              self.call_.session->call_event(self.call_, log::CallStage::Publish);
          }
          if (self.context_)
            self.emulation_stage_ = std::make_unique<HostStageScope>(
                self.call_ ? self.call_ : self.context_, stage, "FUNCTIONAL_EMULATION", "IM2P.sim",
                std::strcmp(stage, "functional.materialize") == 0
                    ? "frontend/src/im2p_cpu_functional_compute.cpp:prepare"
                    : "frontend/src/im2p_cpu_functional_compute.cpp:execute");
        } else {
          if (self.emulation_stage_) self.emulation_stage_->finish();
          self.emulation_stage_.reset();
          self.suppression_.reset();
        }
      } else if (self.call_ && self.work_id_) {
        if (begin) {
          self.call_.session->call_event(self.call_, log::CallStage::CompleteRequired, {*self.work_id_});
          self.call_.session->call_event(self.call_, log::CallStage::Continuation);
          self.cpu_context_ = std::make_unique<log::ScopedContext>(self.call_);
          self.previous_continuation_ = continuation_work_id;
          continuation_work_id = self.work_id_;
        } else {
          continuation_work_id = self.previous_continuation_;
          self.cpu_context_.reset();
        }
      }
    } catch (...) {
      if (self.context_)
        self.context_.session->record_failure("cycle-sim: dispatch event failed");
    }
  }
  log::Context context_;
  const log::Work *work_;
  log::CallKind kind_;
  std::vector<uint64_t> *collector_;
  log::Context call_;
  std::optional<uint64_t> work_id_;
  std::vector<uint64_t> selected_work_ids_;
  std::optional<uint64_t> previous_continuation_;
  std::unique_ptr<HostStageScope> emulation_stage_;
  std::unique_ptr<ggml::gemmini::log::ScopedFunctionalEmulationSuppression> suppression_;
  std::unique_ptr<log::ScopedContext> cpu_context_;
};
} // namespace im2p::gemmini::cycle_sim
#endif
