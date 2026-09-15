#include "bound_rmd_rtl_fixture.hpp"
#include "ggml-gemmini-fpga.hpp"
#include "rmd_rtl_fixture.hpp"
#include "quants/act/exsia/exsia.hpp"
#include <gemmini.h>

#include <algorithm>
#include <array>
#include <cstdio>
#include <deque>
#include <stdexcept>

namespace im2p::gemmini_hp1 {
namespace {
namespace exsia = ggml::gemmini::quants::act::exsia;

void require(bool condition, const char *message) {
  if (!condition) throw std::runtime_error(message);
}

struct BoundExecutor {
  void *context;
  FrontendRtlWorkExecute dense;
  RmdRawExecute raw;
  WorkPlanV1 plan;
  im2p_stripe_work_desc_t stream{};
  std::deque<im2p_stripe_completion_extended_t> completions;
  size_t dense_calls = 0, raw_calls = 0, accepted = 0, returned = 0;
  uint64_t first_start = 0, last_done = 0;
  uint32_t work_id = 0;
  bool fail_raw = false;
  bool started = false;

  static int prepare(void *opaque, const WorkPlanV1 &work) {
    auto &self = *static_cast<BoundExecutor *>(opaque);
    if (work.kind != WorkKind::dense_hp1_final || !work.tile_i || !work.tile_j || !work.tile_k)
      return IM2P_INVALID_LAYOUT;
    self.plan = work;
    self.accepted = self.returned = 0;
    self.first_start = self.last_done = 0;
    self.started = false;
    self.completions.clear();
    return IM2P_OK;
  }

  int run(const im2p_provider_t &provider, const void *activations, size_t stride,
          size_t row_begin, size_t rows, uint32_t slot,
          im2p_work_stats_extended_t *stats) {
    if (!activations || !provider.read_weight_i8 || !provider.read_scale ||
        !provider.write_output || stride < plan.k) return IM2P_INVALID_LAYOUT;
    FrontendRtlWork work{};
    work.work_id = work_id++;
    work.host_slot = slot;
    work.row_begin = static_cast<uint32_t>(row_begin);
    work.rows = static_cast<uint32_t>(rows);
    work.columns = static_cast<uint32_t>(plan.n);
    work.k = static_cast<uint32_t>(plan.k);
    work.plan = plan;
    work.plan.m = rows;
    work.activations.resize(rows * plan.k);
    work.weights.resize(plan.k * plan.n);
    work.carriers.resize((plan.k / 32) * plan.n);
    for (size_t row = 0; row < rows; ++row)
      std::copy_n(static_cast<const int8_t *>(activations) + row * stride,
                  plan.k, work.activations.data() + row * plan.k);
    for (size_t lane = 0; lane < plan.k; ++lane)
      if (provider.read_weight_i8(provider.context, lane, 0, plan.n,
              work.weights.data() + lane * plan.n) != IM2P_OK) return IM2P_ERROR;
    for (size_t block = 0; block < plan.k / 32; ++block)
      if (provider.read_scale(provider.context, block, 0, plan.n,
              work.carriers.data() + block * plan.n) != IM2P_OK) return IM2P_ERROR;
    std::vector<int32_t> values;
    FrontendRtlTiming timing{};
    const int status = dense(context, work, values, timing);
    if (status != IM2P_OK) return status;
    if (values.size() != rows * plan.n || timing.done_cycle <= timing.start_cycle)
      return IM2P_ERROR;
    if (!started) first_start = timing.start_cycle;
    started = true;
    last_done = timing.done_cycle;
    std::vector<int64_t> row_values(plan.n);
    for (size_t row = 0; row < rows; ++row) {
      std::copy_n(values.data() + row * plan.n, plan.n, row_values.data());
      if (provider.write_output(provider.context, 0, row_begin + row, 0, plan.n,
              row_values.data(), IM2P_OUTPUT_SCU_FINAL) != IM2P_OK) return IM2P_ERROR;
    }
    ++dense_calls;
    if (stats) {
      *stats = {};
      stats->base.work_total_cycles = timing.done_cycle - timing.start_cycle;
      stats->base.completed_output_tiles = (rows + DIM - 1) / DIM;
    }
    return IM2P_OK;
  }

  static int full(void *opaque, const im2p_matmul_desc_t *desc,
                   im2p_work_stats_extended_t *stats) {
    if (!desc || desc->vector_op != IM2P_VECTOR_LEFT_SHIFT ||
        desc->output_domain != IM2P_OUTPUT_SCU_FINAL) return IM2P_INVALID_LAYOUT;
    auto &self = *static_cast<BoundExecutor *>(opaque);
    return self.run(desc->provider, desc->activations, desc->activation_row_stride_bytes,
                    0, desc->m, 0, stats);
  }

  static int begin(void *opaque, const im2p_stripe_work_desc_t *desc) {
    if (!desc || desc->vector_op != IM2P_VECTOR_LEFT_SHIFT ||
        desc->output_domain != IM2P_OUTPUT_SCU_FINAL) return IM2P_INVALID_LAYOUT;
    static_cast<BoundExecutor *>(opaque)->stream = *desc;
    return IM2P_OK;
  }

  static int publish(void *opaque, const im2p_activation_stripe_t *stripe) {
    auto &self = *static_cast<BoundExecutor *>(opaque);
    if (!stripe || stripe->stripe_id != self.accepted) return IM2P_INVALID_LAYOUT;
    im2p_work_stats_extended_t stats{};
    const int status = self.run(self.stream.provider, stripe->activations,
        stripe->activation_row_stride_bytes, stripe->i_start, stripe->rows,
        static_cast<uint32_t>(stripe->stripe_id % 2), &stats);
    if (status != IM2P_OK) return status;
    self.completions.push_back({{stripe->stripe_id, stripe->i_start, stripe->rows, stripe->context},
        self.last_done - stats.base.work_total_cycles, self.last_done, stats.base.work_total_cycles});
    ++self.accepted;
    return IM2P_OK;
  }

  static int poll(void *opaque, im2p_stripe_completion_extended_t *completion) {
    auto &self = *static_cast<BoundExecutor *>(opaque);
    if (self.completions.empty()) return 0;
    *completion = self.completions.front();
    self.completions.pop_front();
    ++self.returned;
    return 1;
  }

  static int finish(void *opaque, im2p_work_stats_extended_t *stats) {
    auto &self = *static_cast<BoundExecutor *>(opaque);
    if (self.returned != self.stream.stripe_count || !self.completions.empty()) return IM2P_ERROR;
    *stats = {};
    stats->base.work_total_cycles = self.last_done - self.first_start;
    stats->base.completed_stripes = self.returned;
    return IM2P_OK;
  }

  static int execute_raw(void *opaque, const RmdRawWork &work,
                         std::vector<int32_t> &values, uint64_t &cycles) {
    auto &self = *static_cast<BoundExecutor *>(opaque);
    const int status = self.raw(self.context, work, values, cycles);
    ++self.raw_calls;
    return self.fail_raw ? IM2P_ERROR : status;
  }
};

struct Unbind {
  ~Unbind() { ggml_gemmini_hp1_unbind_executor(); }
};
}

void run_bound_rmd_ws_rtl_fixture(const Capability &capability, void *context,
                                  FrontendRtlWorkExecute dense, RmdRawExecute raw) {
  const auto selected = ggml_gemmini_hp1_capability();
  require(selected.rmd && selected.dim == capability.dim &&
          selected.activation_bits == capability.activation_bits &&
          selected.weight_bits == capability.weight_bits,
          "bound host profile differs from generated RTL");
  BoundExecutor executor{context, dense, raw, {}, {}, {}};
  const ggml_gemmini_hp1_executor binding{selected, &executor, BoundExecutor::prepare,
      BoundExecutor::full, {&executor, BoundExecutor::begin, BoundExecutor::publish,
                            BoundExecutor::poll, BoundExecutor::finish}, BoundExecutor::execute_raw};
  require(ggml_gemmini_hp1_bind_executor(binding), ggml_gemmini_fpga_last_error().c_str());
  Unbind unbind;
  size_t exact_full = 0, exact_pipeline = 0, rollback = 0;
  for (bool fail : {false, true}) {
    for (bool pipeline : {false, true}) {
      RmdRtlFixture fixture;
      auto &args = fixture.args;
      std::array<int32_t, fixture.columns> bias{};
      args.D = bias.data();
      args.repeating_bias = true;
      args.activation_rows_per_stripe = 3;
      ggml::gemmini::gemmini_set_tile_ws(&args);
      executor.fail_raw = fail;
      std::fill(fixture.output.begin(), fixture.output.end(), 91.0F);
      const auto quantize = [&] {
        args.A.zero_fill();
        for (size_t row = 0; row < fixture.rows; ++row)
          (*args.A.bytes)[row * args.A.row_stride_bytes + 32] =
              static_cast<uint8_t>(row % 2 == 0 ? 1 : -1);
        auto &metadata = args.act_quant.storage().emplace<exsia::Meta>();
        metadata.run_id = 123;
        metadata.theta.assign(3, INT16_MIN);
        require(args.exsia_stripe_ready_sink, "public HP1 entry did not attach producer");
        for (size_t stripe = 0; stripe < 3; ++stripe) {
          metadata.theta[stripe] = -1;
          exsia::StripeReadyEvent event{};
          event.run_id = 123;
          event.stripe_id = stripe;
          event.slot = stripe % 2;
          event.row_begin = stripe * 3;
          event.row_end = event.row_begin + 3;
          event.activation_metadata = exsia::StripeMetadataSnapshot{-1, 6, 2, -1};
          event.rmd_packet = fixture.packet(stripe, event.row_begin, 3);
          if (!args.exsia_stripe_ready_sink->on_ready(args.exsia_stripe_ready_sink->user_data, event))
            throw std::runtime_error("bound HP1 producer failed");
        }
      };
      const bool success = ggml_gemmini_fpga_execute(args, pipeline, quantize, "bound-rmd-rtl", true);
      if (fail) {
        require(!success && std::all_of(fixture.output.begin(), fixture.output.end(),
                    [](float value) { return value == 91.0F; }),
                "public HP1 entry published failed RMD output");
        ++rollback;
      } else {
        require(success, ggml_gemmini_fpga_last_error().c_str());
        auto expected = fixture.expected_merge(0, fixture.rows);
        constexpr std::array<int32_t, 3> dense_integer{-1, -640, 117440512};
        for (size_t row = 0; row < fixture.rows; ++row)
          for (size_t column = 0; column < fixture.columns; ++column)
            expected[row * fixture.columns + column] += static_cast<float>(
                double(dense_integer[column]) * (row % 2 == 0 ? 1 : -1) *
                (double(column + 1) * 0.0625) * 0.5);
        require(fixture.output == expected,
                "public HP1 RMD result differs from independent oracle");
        (pipeline ? exact_pipeline : exact_full) += fixture.output.size();
      }
      require(args.exsia_stripe_ready_sink == nullptr, "HP1 producer binding leaked");
    }
  }
  RmdRtlFixture malformed;
  malformed.args.activation_rows_per_stripe = 3;
  ggml::gemmini::gemmini_set_tile_ws(&malformed.args);
  std::fill(malformed.output.begin(), malformed.output.end(), 91.0F);
  const auto dense_before = executor.dense_calls, raw_before = executor.raw_calls;
  const bool accepted = ggml_gemmini_fpga_execute(malformed.args, true, [&] {
    require(malformed.args.A.allocate(1, malformed.k, GGML_GEMMINI_ACTIVATION_BITS),
            "malformed publication fixture allocation failed");
    exsia::StripeReadyEvent event{};
    event.row_end = 3;
    event.activation_metadata = exsia::StripeMetadataSnapshot{-1, 6, 2, -1};
    const auto *sink = malformed.args.exsia_stripe_ready_sink;
    require(sink && !sink->on_ready(sink->user_data, event),
            "undersized activation publication accepted");
  }, "bound-rmd-bounds", true);
  require(!accepted && executor.dense_calls == dense_before && executor.raw_calls == raw_before &&
          std::all_of(malformed.output.begin(), malformed.output.end(),
              [](float value) { return value == 91.0F; }) &&
          malformed.args.exsia_stripe_ready_sink == nullptr,
          "malformed publication reached execution or modified output");
  std::printf("WS_RMD_PUBLICATION_BOUNDS rejected=1 dense_calls=0 raw_calls=0 output_unchanged=1\n");
  std::printf("WS_RMD_BOUND bits=%u DIM=%u full_exact=%zu pipeline_exact=%zu dense_calls=%zu raw_calls=%zu stripes=3 slots=0,1,0 rollback=%zu public_entry=1\n",
      GGML_GEMMINI_ACTIVATION_BITS, DIM, exact_full, exact_pipeline,
      executor.dense_calls, executor.raw_calls, rollback);
}
}
