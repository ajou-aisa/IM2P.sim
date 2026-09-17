#include "ggml-gemmini-fpga.hpp"

#include "ggml-gemmini-args.h"
#include "quants/act/exsia/exsia.hpp"
#include "residual/rmd/rmd-compose.hpp"
#include "uart.hpp"

#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <mutex>
#include <optional>
#include <stdexcept>

namespace {

std::mutex adapter_mutex;
thread_local std::string last_error;
std::optional<ggml_gemmini_hp1_executor> bound_executor;
ggml_gemmini_fpga_result_observer result_observer = nullptr;
void *result_context = nullptr;

namespace frontend = im2p::gemmini;
namespace hp1 = im2p::gemmini_hp1;
namespace exsia = ggml::gemmini::quants::act::exsia;
namespace rmd = ggml::gemmini::rmd;

void require(bool condition, const char *message) {
  if (!condition)
    throw std::runtime_error(message);
}

bool hp1_weights(const ggml_gemmini_args_t &args) {
  using Format = ggml_gemmini_args_t::im2p_weight_format_t;
#if GGML_GEMMINI_WEIGHT_BITS == 4
  return args.weight_format == Format::q4_hp1 &&
         args.has_native_matched_width_contract();
#elif GGML_GEMMINI_WEIGHT_BITS == 8
  return args.weight_format == Format::q8_hp1 &&
         args.has_q8_hp1_im2p_contract();
#else
  (void)args;
  return false;
#endif
}

void set_error(const char *message) { last_error = message; }

im2p::gemmini_hp1::Capability capability() {
  using namespace im2p::gemmini_hp1;
  Capability value;
#if defined(IM2P_FPGA_PROFILE_ID)
  value.profile = IM2P_FPGA_PROFILE_ID;
#endif
#if defined(IM2P_FPGA_BUILD_ID)
  value.build_id = IM2P_FPGA_BUILD_ID;
#endif
  value.numerical_revision = "hp1-fragment-sat32-v1";
  value.activation_bits = GGML_GEMMINI_ACTIVATION_BITS;
  value.weight_bits = GGML_GEMMINI_WEIGHT_BITS;
  value.dim = DIM;
  value.accumulator_bits = 32;
  value.block_size = 32;
  value.packing = GGML_GEMMINI_WEIGHT_BITS == 4
                      ? Packing::signed_int4_low_nibble_first
                      : Packing::signed_int8;
  value.hp1_shift_only = true;
  value.ws = true;
  value.rmd = true;
  value.bank_count = BANK_NUM;
  value.bank_rows = BANK_ROWS;
  value.accumulator_rows = ACC_ROWS;
  value.scratchpad_row_bytes = DIM * GGML_GEMMINI_WEIGHT_BITS / 8;
  value.accumulator_row_bytes = DIM * sizeof(std::int32_t);
  return value;
}

struct Invocation {
  ggml_gemmini_args_t &args;
  ggml_gemmini_args_t &runtime;
  hp1::RmdExecutorContext residual;
  frontend::Run *run = nullptr;
  std::vector<exsia::StripeReadyEvent> events;
  std::string producer_failure;
  std::string residual_failure;

  static bool ready(void *opaque,
                    const exsia::StripeReadyEvent &event) noexcept {
    auto &self = *static_cast<Invocation *>(opaque);
    try {
      require(event.stripe_id == self.events.size() &&
                  event.slot == event.stripe_id % 2 &&
                  event.row_begin ==
                      (self.events.empty() ? 0 : self.events.back().row_end) &&
                  event.row_end > event.row_begin &&
                  event.row_end <= self.args.I &&
                  event.activation_metadata.has_value(),
              "invalid HP1 stripe publication");
#if !defined(GGML_GEMMINI_ENABLE_RMD) || GGML_GEMMINI_ENABLE_RMD == 0
      require(!event.rmd_packet && !event.direct_residual,
              "HP1 host was built with RMD OFF");
#endif
      if (self.run) {
        require(self.args.A.raw_data() && self.runtime.A.bytes &&
                    self.args.A.bits == GGML_GEMMINI_ACTIVATION_BITS &&
                    self.args.A.rows >= event.row_end &&
                    self.args.A.cols == self.args.K &&
                    self.args.A.row_stride_bytes >= self.args.K &&
                    self.args.A.raw_size() >= self.args.K &&
                    event.row_end - 1 <=
                        (self.args.A.raw_size() - self.args.K) /
                            self.args.A.row_stride_bytes,
                "invalid HP1 activation publication");
        for (size_t row = event.row_begin; row < event.row_end; ++row)
          std::memcpy(self.runtime.A.bytes->data() +
                          row * self.runtime.A.row_stride_bytes,
                      static_cast<const uint8_t *>(self.args.A.raw_data()) +
                          row * self.args.A.row_stride_bytes,
                      self.args.K);
        const auto status = frontend::submit_stripe(
            *self.run, event, {true, event.activation_metadata->theta});
        require(status.ok(), status.message);
      }
      self.events.push_back(event);
      return true;
    } catch (const std::exception &error) {
      self.producer_failure = error.what();
      return false;
    }
  }

  void merge(const exsia::StripeReadyEvent &event, float *destination,
             frontend::ResidualStripeStats &stats) {
#if defined(GGML_GEMMINI_ENABLE_RMD) && GGML_GEMMINI_ENABLE_RMD != 0
    require(event.activation_metadata && !event.direct_residual,
            "HP1 RMD requires a width-native packet");
    if (!event.rmd_packet)
      return;
    const auto &packet = *event.rmd_packet;
    require(packet.stripe_id == event.stripe_id &&
                packet.row_begin == event.row_begin &&
                packet.row_count == event.row_end - event.row_begin &&
                packet.logical_k == args.K && packet.logical_j == args.J,
            "HP1 RMD packet identity mismatch");
    auto stripe = runtime;
    auto &metadata = stripe.act_quant.storage().emplace<exsia::Meta>();
    metadata.e_s = event.activation_metadata->e_s;
    metadata.rho = event.activation_metadata->rho;
    metadata.sigma = event.activation_metadata->sigma;
    metadata.run_id = event.run_id;
    metadata.theta.assign(event.stripe_id + 1,
                          std::numeric_limits<int16_t>::min());
    metadata.theta[event.stripe_id] = event.activation_metadata->theta;
    residual.host_slot = static_cast<uint32_t>(event.slot);
    rmd::Correction correction;
    rmd::RmdExecutionMetrics metrics;
    auto status =
        hp1::execute_rmd_packet(residual, stripe, packet, correction, &metrics);
    require(status == rmd::RmdStatus::success, rmd::rmd_status_message(status));
    status =
        rmd::merge_rmd_correction_to(stripe, destination, packet, correction);
    require(status == rmd::RmdStatus::success, rmd::rmd_status_message(status));
    stats.rmd_dot_calls = metrics.im2p_dot_calls;
    rmd::detail::expand_im2p_provider_stats(metrics.im2p_stats,
                                            stats.rmd_stats);
#else
    (void)destination;
    (void)stats;
    require(!event.rmd_packet && !event.direct_residual,
            "HP1 host was built with RMD OFF");
#endif
  }

  static frontend::Status
  merge_stage(void *opaque, im2p_sim_t *simulator,
              const exsia::StripeReadyEvent &event,
              frontend::ResidualStageView stage,
              frontend::ResidualStripeStats &stats) noexcept {
    auto &self = *static_cast<Invocation *>(opaque);
    try {
      require(simulator == nullptr, "HP1 residual requires its bound executor");
      self.merge(event, stage.data, stats);
      return {};
    } catch (const std::exception &error) {
      self.residual_failure = error.what();
      return {frontend::StatusCode::execution_failure, frontend::Route::unknown,
              true, self.residual_failure.c_str()};
    }
  }
};

struct SinkBinding {
  ggml_gemmini_args_t &args;
  explicit SinkBinding(ggml_gemmini_args_t &value,
                       const exsia::StripeReadySink &sink)
      : args(value) {
    require(!args.exsia_stripe_ready_sink,
            "HP1 invocation already has a stripe producer sink");
    args.exsia_stripe_ready_sink = &sink;
  }
  ~SinkBinding() { args.exsia_stripe_ready_sink = nullptr; }
};

} // namespace

im2p::gemmini_hp1::Capability ggml_gemmini_hp1_capability() {
  return capability();
}

bool ggml_gemmini_hp1_bind_executor(const ggml_gemmini_hp1_executor &executor) {
  std::lock_guard<std::mutex> lock(adapter_mutex);
  last_error.clear();
  if (!hp1::compatible(capability(), executor.capability) ||
      !executor.prepare || !executor.full || !executor.stream.begin ||
      !executor.stream.publish || !executor.stream.poll ||
      !executor.stream.finish || !executor.scu) {
    bound_executor.reset();
    set_error("GEMMINI_HP1 executor capability or callbacks are incomplete");
    return false;
  }
  bound_executor = executor;
  return true;
}

void ggml_gemmini_hp1_unbind_executor() {
  std::lock_guard<std::mutex> lock(adapter_mutex);
  bound_executor.reset();
}

bool ggml_gemmini_fpga_supports(std::size_t rows, std::size_t columns,
                                std::size_t reduction, bool pipeline) {
  return ggml_gemmini_fpga_supports(rows, columns, reduction, pipeline, true);
}

bool ggml_gemmini_fpga_supports(std::size_t rows, std::size_t columns,
                                std::size_t reduction, bool pipeline,
                                bool block_scaled) {
  (void)pipeline;
  return block_scaled && rows != 0 && columns != 0 && reduction != 0 &&
         reduction % 32 == 0 &&
         rows <= std::numeric_limits<std::size_t>::max() / columns &&
         rows <= std::numeric_limits<std::size_t>::max() / reduction;
}

bool ggml_gemmini_fpga_uses_rtl() { return false; }

bool ggml_gemmini_fpga_uses_bounded() { return true; }

bool ggml_gemmini_fpga_execute(ggml_gemmini_args_t &args, bool pipeline,
                               const std::function<void()> &quantize,
                               const char *layer_name, bool native_scu) {
  std::lock_guard<std::mutex> lock(adapter_mutex);
  (void)layer_name;
  last_error.clear();
  try {
    if (!native_scu ||
        !ggml_gemmini_fpga_supports(args.I, args.J, args.K, pipeline, true) ||
        !hp1_weights(args) || args.tile_I == 0 || args.tile_J == 0 ||
        args.tile_K == 0 ||
        (pipeline && args.activation_rows_per_stripe == 0)) {
      set_error("GEMMINI_HP1 requires a native HP1 block-scaled work item");
      return false;
    }
    using namespace im2p::gemmini_hp1;
    const WorkPlanV1 plan{args.I,
                          args.J,
                          args.K,
                          args.tile_I,
                          args.tile_J,
                          args.tile_K,
                          args.activation_rows_per_stripe,
                          pipeline ? Mode::pipeline : Mode::full,
                          WorkKind::dense_hp1_final};
    static_cast<void>(fragment_work(capability(), plan));
    if (!bound_executor) {
      const char *device = std::getenv("IM2P_FPGA_DEVICE");
      if (!device || !*device) {
        set_error("IM2P_FPGA_DEVICE must explicitly bind a GEMMINI_HP1 board");
        return false;
      }
      set_error("GEMMINI_HP1 physical transport is not implemented");
      return false;
    }
    auto &executor = *bound_executor;
    require(executor.prepare(executor.context, plan) == IM2P_OK,
            "GEMMINI_HP1 executor rejected the host work plan");
    require(args.f_out && args.col_stride_f_out > 0 &&
                args.J <= SIZE_MAX / args.col_stride_f_out &&
                args.stride_f_out >= args.J * args.col_stride_f_out &&
                args.I <= SIZE_MAX / args.stride_f_out,
            "invalid HP1 output layout");
#if defined(GGML_GEMMINI_ENABLE_RMD) && GGML_GEMMINI_ENABLE_RMD != 0
    require(args.residual_route ==
                ggml::gemmini::residual::ResidualRoute::ws_packet,
            "HP1 RMD requires the systolic packet route");
#endif
    if (args.D) {
      require(!args.low_D && args.repeating_bias,
              "HP1 supports only absent or repeating zero bias");
      const auto *bias = static_cast<const int32_t *>(args.D);
      require(std::all_of(bias, bias + args.J,
                          [](int32_t value) { return value == 0; }),
              "HP1 does not support nonzero bias");
    }
    std::vector<float> staged(args.I * args.stride_f_out, 0.0f);
    auto runtime = args;
    runtime.f_out = staged.data();
    runtime.D = nullptr;
    runtime.repeating_bias = false;
    Invocation invocation{
        args,
        runtime,
        {executor.capability, executor.context, nullptr, 0, 0, executor.scu},
        nullptr,
        {},
        {},
        {}};
    const exsia::StripeReadySink sink{&invocation, Invocation::ready};
    frontend::Options options;
    options.numerical_contract = frontend::NumericalContract::scu_final_integer;
    if (pipeline) {
      args.act_quant.storage().emplace<exsia::Meta>();
      runtime.act_quant.storage().emplace<exsia::Meta>();
      require(runtime.A.allocate(args.I, args.K, GGML_GEMMINI_ACTIVATION_BITS),
              "HP1 accepted activation staging allocation failed");
      runtime.A.zero_fill();
      options.stream_executor = &executor.stream;
#if defined(GGML_GEMMINI_ENABLE_RMD) && GGML_GEMMINI_ENABLE_RMD != 0
      options.residual_stage_mode = frontend::ResidualStageMode::im2p_compact;
      options.residual_stage_context = &invocation;
      options.residual_stage_fn = Invocation::merge_stage;
#endif
    } else {
      SinkBinding binding(args, sink);
      quantize();
      require(invocation.producer_failure.empty(),
              invocation.producer_failure.c_str());
      runtime = args;
      runtime.f_out = staged.data();
      runtime.D = nullptr;
      runtime.repeating_bias = false;
      options.full_executor_context = executor.context;
      options.full_executor = executor.full;
    }
    auto launched = frontend::execute(&runtime,
                                      pipeline ? frontend::Mode::stripe_pipeline
                                               : frontend::Mode::full,
                                      options);
    require(launched.status.ok() && launched.run, launched.status.message);
    if (pipeline) {
      invocation.run = launched.run.get();
      SinkBinding binding(args, sink);
      quantize();
      require(invocation.producer_failure.empty(),
              invocation.producer_failure.c_str());
    }
    const auto result = frontend::fence(*launched.run);
    require(result.status.ok(), result.status.message);
    require(!invocation.events.empty() &&
                invocation.events.back().row_end == args.I,
            "incomplete HP1 quantized stripe coverage");
    if (pipeline) {
      const auto status =
          frontend::authorize_output_commit(*launched.run, true);
      require(status.ok(), status.message);
    } else {
      for (const auto &event : invocation.events) {
        frontend::ResidualStripeStats stats;
        invocation.merge(event, staged.data(), stats);
      }
    }
    if (result_observer)
      result_observer(runtime, result_context);
    for (size_t row = 0; row < args.I; ++row)
      for (size_t column = 0; column < args.J; ++column)
        args.f_out[row * args.stride_f_out + column * args.col_stride_f_out] =
            staged[row * args.stride_f_out + column * args.col_stride_f_out];
    return true;
  } catch (const std::exception &error) {
    last_error = error.what();
    return false;
  }
}

std::string ggml_gemmini_fpga_last_error() { return last_error; }

void ggml_gemmini_fpga_test_producer_checkpoint(std::size_t stripe_index) {
  (void)stripe_index;
}

void ggml_gemmini_fpga_set_observer(ggml_gemmini_fpga_observer observer,
                                    void *context) {
  (void)observer;
  (void)context;
}

void ggml_gemmini_fpga_set_block_observer(
    ggml_gemmini_fpga_block_observer observer, void *context) {
  (void)observer;
  (void)context;
}

void ggml_gemmini_fpga_set_result_observer(
    ggml_gemmini_fpga_result_observer observer, void *context) {
  std::lock_guard<std::mutex> lock(adapter_mutex);
  result_observer = observer;
  result_context = context;
}

void ggml_gemmini_fpga_set_boundary_observer(
    ggml_gemmini_fpga_boundary_observer observer, void *context) {
  (void)observer;
  (void)context;
}
