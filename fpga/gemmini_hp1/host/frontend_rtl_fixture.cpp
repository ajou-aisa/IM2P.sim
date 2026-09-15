#include "frontend_rtl_fixture.hpp"

#include "im2p_gemmini_frontend.hpp"
#include "quants/act/exsia/exsia.hpp"

#include <gemmini.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <deque>
#include <stdexcept>
#include <vector>

extern "C" void gemmini_log_debug_layer(const char *, const char *, ...) noexcept {}

namespace im2p::gemmini_hp1 {
namespace {

namespace frontend = im2p::gemmini;
namespace exsia = ggml::gemmini::quants::act::exsia;

void require(bool condition, const char *message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

struct Executor {
  const Capability &capability;
  void *context;
  FrontendRtlExecute execute;
  WorkPlanV1 plan;
  std::uint32_t next_work_id = 0;
  std::uint64_t cycles = 0;
  std::size_t completed_rows = 0;

  int run(const im2p_provider_t &provider, const void *activations,
          std::size_t activation_row_stride, std::uint32_t activation_bits,
          std::size_t row_begin, std::size_t rows, std::size_t columns,
          std::size_t k, im2p_work_stats_extended_t *stats) {
    if (!activations || !provider.read_weight_i8 || !provider.read_scale ||
        !provider.write_output || activation_bits != capability.activation_bits ||
        rows == 0 || rows > capability.dim || columns == 0 ||
        columns > capability.dim || k == 0) {
      return IM2P_INVALID_LAYOUT;
    }

    WorkPlanV1 work = plan;
    work.m = rows;
    work.n = columns;
    work.k = k;
    const auto fragments = fragment_work(capability, work);
    if (fragments.empty()) {
      return IM2P_INVALID_LAYOUT;
    }

    const auto work_id = next_work_id++;
    std::vector<std::int32_t> final_output;
    std::uint64_t elapsed = 0;
    for (const auto &fragment : fragments) {
      FrontendRtlFragment request{};
      request.work_id = work_id;
      request.valid_rows = static_cast<std::uint32_t>(rows);
      request.valid_columns = static_cast<std::uint32_t>(columns);
      request.plan = work;
      request.fragment = fragment;
      request.activations.assign(capability.dim * capability.dim, 0);
      request.weights.assign(capability.dim * capability.dim, 0);
      request.carriers.assign(capability.dim, 0);

      const auto *activation_bytes = static_cast<const std::uint8_t *>(activations);
      for (std::size_t row = 0; row < rows; ++row) {
        for (std::size_t lane = 0; lane < fragment.valid_k; ++lane) {
          request.activations[row * capability.dim + lane] =
              static_cast<std::int8_t>(activation_bytes[
                  row * activation_row_stride + fragment.k_begin + lane]);
        }
      }
      for (std::size_t lane = 0; lane < fragment.valid_k; ++lane) {
        if (provider.read_weight_i8(
                provider.context, fragment.k_begin + lane, 0, columns,
                request.weights.data() + lane * capability.dim) != IM2P_OK) {
          return IM2P_ERROR;
        }
      }
      if (provider.read_scale(provider.context, fragment.block, 0, columns,
                              request.carriers.data()) != IM2P_OK) {
        return IM2P_ERROR;
      }
      try {
        for (std::size_t column = 0; column < columns; ++column) {
          (void)normalize_scale(request.carriers[column]);
        }
      } catch (const std::exception &) {
        return IM2P_INVALID_LAYOUT;
      }

      final_output.clear();
      elapsed = 0;
      const int status = execute(context, request, final_output, elapsed);
      if (status != IM2P_OK) {
        return status;
      }
      if ((!fragment.final && !final_output.empty()) ||
          (fragment.final && final_output.size() != rows * columns)) {
        return IM2P_ERROR;
      }
    }

    std::vector<std::int64_t> widened(columns);
    for (std::size_t row = 0; row < rows; ++row) {
      std::copy_n(final_output.data() + row * columns, columns, widened.begin());
      if (provider.write_output(provider.context, 0, row_begin + row, 0, columns,
                                widened.data(), IM2P_OUTPUT_SCU_FINAL) != IM2P_OK) {
        return IM2P_ERROR;
      }
    }
    cycles += elapsed;
    completed_rows += rows;
    if (stats) {
      *stats = {};
      stats->base.work_total_cycles = elapsed;
      stats->base.completed_fragments = fragments.size();
      stats->base.completed_output_tiles = rows * columns;
      stats->base.completed_stripes = 1;
    }
    return IM2P_OK;
  }
};

int full_execute(void *opaque, const im2p_matmul_desc_t *descriptor,
                 im2p_work_stats_extended_t *stats) {
  if (!opaque || !descriptor || descriptor->vector_op != IM2P_VECTOR_LEFT_SHIFT ||
      descriptor->output_domain != IM2P_OUTPUT_SCU_FINAL ||
      descriptor->block_size != 32) {
    return IM2P_INVALID_LAYOUT;
  }
  auto &executor = *static_cast<Executor *>(opaque);
  return executor.run(
      descriptor->provider, descriptor->activations,
      descriptor->activation_row_stride_bytes, descriptor->activation_bits, 0,
      descriptor->m, descriptor->n, descriptor->k, stats);
}

struct Stream {
  Executor executor;
  im2p_stripe_work_desc_t descriptor{};
  std::deque<im2p_stripe_completion_extended_t> completions;
  std::size_t accepted = 0;
  std::size_t returned = 0;
  std::uint64_t endpoint = 0;

  static int begin(void *opaque, const im2p_stripe_work_desc_t *descriptor) {
    if (!opaque || !descriptor || descriptor->vector_op != IM2P_VECTOR_LEFT_SHIFT ||
        descriptor->output_domain != IM2P_OUTPUT_SCU_FINAL ||
        descriptor->block_size != 32) {
      return IM2P_INVALID_LAYOUT;
    }
    auto &stream = *static_cast<Stream *>(opaque);
    stream.descriptor = *descriptor;
    return IM2P_OK;
  }

  static int publish(void *opaque, const im2p_activation_stripe_t *stripe) {
    if (!opaque || !stripe) {
      return IM2P_INVALID_LAYOUT;
    }
    auto &stream = *static_cast<Stream *>(opaque);
    if (stripe->stripe_id != stream.accepted ||
        stripe->i_start + stripe->rows > stream.descriptor.m) {
      return IM2P_INVALID_LAYOUT;
    }
    im2p_work_stats_extended_t stats{};
    const auto publish_cycle = stream.endpoint;
    const int status = stream.executor.run(
        stream.descriptor.provider, stripe->activations,
        stripe->activation_row_stride_bytes, stripe->activation_bits,
        stripe->i_start, stripe->rows, stream.descriptor.n, stream.descriptor.k,
        &stats);
    if (status != IM2P_OK) {
      return status;
    }
    stream.endpoint += stats.base.work_total_cycles;
    stream.completions.push_back({
        {stripe->stripe_id, stripe->i_start, stripe->rows, stripe->context},
        publish_cycle, stream.endpoint, stream.endpoint - publish_cycle});
    ++stream.accepted;
    return IM2P_OK;
  }

  static int poll(void *opaque, im2p_stripe_completion_extended_t *completion) {
    if (!opaque || !completion) {
      return IM2P_INVALID_LAYOUT;
    }
    auto &stream = *static_cast<Stream *>(opaque);
    if (stream.completions.empty()) {
      return 0;
    }
    *completion = stream.completions.front();
    stream.completions.pop_front();
    ++stream.returned;
    return 1;
  }

  static int finish(void *opaque, im2p_work_stats_extended_t *stats) {
    if (!opaque || !stats) {
      return IM2P_INVALID_LAYOUT;
    }
    auto &stream = *static_cast<Stream *>(opaque);
    if (stream.returned != stream.descriptor.stripe_count ||
        !stream.completions.empty()) {
      return IM2P_ERROR;
    }
    *stats = {};
    stats->base.work_total_cycles = stream.endpoint;
    stats->base.completed_stripes = stream.returned;
    stats->base.completed_output_tiles =
        stream.descriptor.m * stream.descriptor.n;
    return IM2P_OK;
  }

  frontend::StreamExecutor table() {
    return {this, begin, publish, poll, finish};
  }
};

template <typename Block>
void set_code(Block &block, std::size_t lane, std::int8_t value) {
  if constexpr (std::is_same_v<Block, block_q4_hp1>) {
    const auto nibble = static_cast<std::uint8_t>(value + 8);
    auto &byte = block.qs[lane % 16];
    byte = lane < 16 ? static_cast<std::uint8_t>((byte & 0xf0U) | nibble)
                     : static_cast<std::uint8_t>((byte & 0x0fU) | (nibble << 4U));
  } else {
    block.qs[lane] = value;
  }
}

template <typename Block>
void initialize_blocks(std::vector<Block> &blocks) {
  for (auto &block : blocks) {
    if constexpr (std::is_same_v<Block, block_q4_hp1>) {
      std::fill(std::begin(block.qs), std::end(block.qs), std::uint8_t{0x88});
    }
    block.channel_scale = 0.5F;
    block.m = 0;
  }
  const auto at = [&](std::size_t column, std::size_t block) -> Block & {
    return blocks[column * 2 + block];
  };
  set_code(at(0, 0), 0, 1);
  set_code(at(0, 0), 3, -1);
  at(0, 0).m = 1;
  set_code(at(1, 0), 0, 2);
  set_code(at(1, 0), 3, 1);
  at(1, 0).m = 0;
  set_code(at(2, 0), 0, -3);
  set_code(at(2, 0), 3, 1);
  at(2, 0).m = 1;
  set_code(at(0, 1), 0, 2);
  at(0, 1).m = INT16_MIN;
  set_code(at(1, 1), 0, -1);
  at(1, 1).m = 2;
  set_code(at(2, 1), 0, 3);
  at(2, 1).m = 0;
}

void require_output(const std::array<float, 6> &output) {
  constexpr std::array<float, 6> expected = {
      -0.5F, 0.0F, 0.25F, -1.0F, -2.25F, 3.5F};
  require(output == expected, "frontend float restoration mismatch");
}

}

void run_frontend_rtl_fixture(const Capability &capability, void *context,
                              FrontendRtlExecute callback) {
  require(callback != nullptr && capability.hp1_shift_only && capability.ws &&
              !capability.rmd && capability.activation_bits == GGML_GEMMINI_ACTIVATION_BITS &&
              capability.weight_bits == GGML_GEMMINI_WEIGHT_BITS &&
              capability.dim == DIM,
          "frontend RTL capability mismatch");

  constexpr std::size_t rows = 2;
  constexpr std::size_t columns = 3;
  constexpr std::size_t k = 64;
  ggml_gemmini_args_t args{};
  args.I = rows;
  args.J = columns;
  args.K = k;
  require(args.A.allocate(rows, k, GGML_GEMMINI_ACTIVATION_BITS),
          "activation allocation failed");
  require(args.A.set(0, 0, 1) && args.A.set(0, 3, 2) && args.A.set(0, 32, 1) &&
              args.A.set(1, 0, -1) && args.A.set(1, 3, 1) && args.A.set(1, 32, 2),
          "activation fixture encoding failed");
  args.sA = k;
  args.activation_rows_per_stripe = 1;
  args.stride_f_out = columns;
  args.col_stride_f_out = 1;
  args.matmul_layer = "gemmini_hp1_frontend_rtl_fixture";
  args.act_quant.storage()
      .emplace<ggml::gemmini::quants::act::tensor::Meta>()
      .scale = 0.5F;

  std::vector<block_q4_hp1> q4_blocks(columns * 2);
  std::vector<block_q8_hp1> q8_blocks(columns * 2);
  if constexpr (GGML_GEMMINI_WEIGHT_BITS == 4) {
    initialize_blocks(q4_blocks);
    args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q4_hp1;
    args.q4_hp1_blocks = q4_blocks.data();
    args.native_block_count = q4_blocks.size();
    args.native_blocks_per_row = 2;
    args.native_weight_bytes = q4_blocks.size() * sizeof(block_q4_hp1);
  } else {
    initialize_blocks(q8_blocks);
    args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1;
    args.q8_hp1_blocks = q8_blocks.data();
    args.q8_hp1_block_count = q8_blocks.size();
    args.q8_hp1_blocks_per_row = 2;
    args.native_weight_bytes = q8_blocks.size() * sizeof(block_q8_hp1);
  }

  ggml::gemmini::gemmini_set_tile_ws(&args);
  require(args.tile_I != 0 && args.tile_J != 0 && args.tile_K != 0,
          "existing Gemmini tiler produced an empty tile");
  const WorkPlanV1 plan{rows, columns, k, args.tile_I, args.tile_J, args.tile_K,
                        1, Mode::full, WorkKind::dense_hp1_final};

  std::array<float, 6> full_output{};
  args.f_out = full_output.data();
  Executor full{capability, context, callback, plan};
  frontend::Options full_options{};
  full_options.full_executor_context = &full;
  full_options.full_executor = full_execute;
  auto full_run = frontend::execute(&args, frontend::Mode::full, full_options);
  require(full_run.status.ok() && full_run.run, "frontend FULL did not start");
  const auto full_done = frontend::fence(*full_run.run);
  require(full_done.status.ok() && full.completed_rows == rows &&
              full_done.stats.base.completed_output_tiles == rows * columns,
          "frontend FULL did not complete exact output coverage");
  require_output(full_output);

  std::array<float, 6> pipeline_output{};
  pipeline_output.fill(91.0F);
  args.f_out = pipeline_output.data();
  WorkPlanV1 pipeline_plan = plan;
  pipeline_plan.mode = Mode::pipeline;
  Stream stream{{capability, context, callback, pipeline_plan}};
  auto table = stream.table();
  frontend::Options pipeline_options{};
  pipeline_options.stream_executor = &table;
  auto pipeline = frontend::execute(
      &args, frontend::Mode::stripe_pipeline, pipeline_options);
  require(pipeline.status.ok() && pipeline.run, "frontend PIPELINE did not start");
  for (std::size_t row = 0; row < rows; ++row) {
    exsia::StripeReadyEvent event{};
    event.run_id = 77;
    event.stripe_id = row;
    event.slot = row % 2;
    event.row_begin = row;
    event.row_end = row + 1;
    require(frontend::submit_stripe(*pipeline.run, event).ok(),
            "frontend PIPELINE stripe rejected");
  }
  const auto pipeline_done = frontend::fence(*pipeline.run);
  require(pipeline_done.status.ok() && stream.accepted == rows &&
              stream.returned == rows &&
              pipeline_done.stripe_rtl_timings.size == rows,
          "frontend PIPELINE coverage incomplete");
  require(std::all_of(pipeline_output.begin(), pipeline_output.end(),
                      [](float value) { return value == 91.0F; }),
          "frontend PIPELINE published before authorization");
  require(frontend::authorize_output_commit(*pipeline.run, true).ok(),
          "frontend PIPELINE commit authorization failed");
  require_output(pipeline_output);
}

}
