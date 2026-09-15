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
#include <type_traits>
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

std::int32_t sat32(std::int64_t value) {
  return static_cast<std::int32_t>(std::clamp<std::int64_t>(value, INT32_MIN, INT32_MAX));
}

struct Executor {
  const Capability &capability;
  void *context;
  FrontendRtlExecute execute;
  WorkPlanV1 plan;
  std::uint32_t next_work_id = 0;
  std::uint64_t cycles = 0;
  std::size_t completed_rows = 0;
  FrontendRtlWorkExecute execute_work = nullptr;
  std::size_t completed_tiles = 0;
  const std::vector<std::int32_t> *expected_integer = nullptr;
  std::uint64_t first_start_cycle = 0;
  std::uint64_t final_done_cycle = 0;
  std::uint64_t last_start_cycle = 0;
  std::uint64_t last_done_cycle = 0;

  int run_work(const im2p_provider_t &provider, const void *activations,
               std::size_t stride, std::size_t row_begin, std::size_t rows,
               std::size_t columns, std::size_t k,
               std::uint32_t host_slot, std::vector<std::int32_t> &output,
               std::uint64_t &elapsed) {
    FrontendRtlWork request{};
    request.work_id = next_work_id++;
    request.host_slot = host_slot;
    request.row_begin = row_begin;
    request.rows = rows;
    request.columns = columns;
    request.k = k;
    request.plan = plan;
    request.plan.m = rows;
    request.plan.n = columns;
    request.plan.k = k;
    request.activations.resize(rows * k);
    request.weights.resize(k * columns);
    request.carriers.resize(((k + 31) / 32) * columns);
    const auto *bytes = static_cast<const std::int8_t *>(activations);
    for (std::size_t row = 0; row < rows; ++row) {
      std::copy_n(bytes + row * stride, k, request.activations.data() + row * k);
    }
    for (std::size_t column = 0; column < columns; column += capability.dim) {
      const auto count = std::min<std::size_t>(capability.dim, columns - column);
      for (std::size_t lane = 0; lane < k; ++lane) {
        if (provider.read_weight_i8(provider.context, lane, column, count,
                request.weights.data() + lane * columns + column) != IM2P_OK) {
          return IM2P_ERROR;
        }
      }
      for (std::size_t block = 0; block < (k + 31) / 32; ++block) {
        if (provider.read_scale(provider.context, block, column, count,
                request.carriers.data() + block * columns + column) != IM2P_OK) {
          return IM2P_ERROR;
        }
      }
    }
    FrontendRtlTiming timing{};
    const int status = execute_work(context, request, output, timing);
    if (status != IM2P_OK) return status;
    if (output.size() != rows * columns || timing.done_cycle <= timing.start_cycle ||
        !expected_integer ||
        (row_begin + rows) * columns > expected_integer->size()) return IM2P_ERROR;
    elapsed = timing.done_cycle - timing.start_cycle;
    last_start_cycle = timing.start_cycle;
    last_done_cycle = timing.done_cycle;
    require(std::equal(output.begin(), output.end(),
                       expected_integer->begin() + row_begin * columns),
            "frontend WS RTL integer oracle mismatch");
    return IM2P_OK;
  }

  int run(const im2p_provider_t &provider, const void *activations,
          std::size_t activation_row_stride, std::uint32_t activation_bits,
          std::size_t row_begin, std::size_t rows, std::size_t columns,
          std::size_t k, im2p_work_stats_extended_t *stats,
          std::uint32_t host_slot = 0) {
    if (!activations || !provider.read_weight_i8 || !provider.read_scale ||
        !provider.write_output || activation_bits != capability.activation_bits ||
        rows == 0 || columns == 0 || k == 0 ||
        (!execute_work && (rows > capability.dim || columns > capability.dim))) {
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

    const auto work_id = execute_work ? 0 : next_work_id++;
    std::vector<std::int32_t> final_output;
    std::uint64_t elapsed = 0;
    if (execute_work) {
      const int status = run_work(provider, activations, activation_row_stride,
                                  row_begin, rows, columns, k, host_slot,
                                  final_output, elapsed);
      if (status != IM2P_OK) return status;
    } else for (const auto &fragment : fragments) {
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
      for (std::size_t column = 0; column < columns; column += capability.dim) {
        const auto count = std::min<std::size_t>(capability.dim, columns - column);
        std::copy_n(final_output.data() + row * columns + column, count, widened.begin());
        if (provider.write_output(provider.context, 0, row_begin + row, column, count,
                                  widened.data(), IM2P_OUTPUT_SCU_FINAL) != IM2P_OK) {
          return IM2P_ERROR;
        }
      }
    }
    if (execute_work) {
      if (completed_rows == 0) first_start_cycle = last_start_cycle;
      final_done_cycle = last_done_cycle;
      cycles = final_done_cycle - first_start_cycle;
    } else {
      cycles += elapsed;
    }
    completed_rows += rows;
    const auto tiles = ((rows + capability.dim - 1) / capability.dim) *
                       ((columns + capability.dim - 1) / capability.dim);
    completed_tiles += tiles;
    if (stats) {
      *stats = {};
      stats->base.work_total_cycles = elapsed;
      stats->base.completed_fragments = fragments.size();
      stats->base.completed_output_tiles = tiles;
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
  std::uint64_t first_start_cycle = 0;
  std::uint64_t final_done_cycle = 0;
  std::uint64_t endpoint = 0;
  std::vector<std::uint32_t> host_slots;

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
        stripe->i_start + stripe->rows > stream.descriptor.m ||
        stripe->stripe_id >= stream.host_slots.size()) {
      return IM2P_INVALID_LAYOUT;
    }
    im2p_work_stats_extended_t stats{};
    const int status = stream.executor.run(
        stream.descriptor.provider, stripe->activations,
        stripe->activation_row_stride_bytes, stripe->activation_bits,
        stripe->i_start, stripe->rows, stream.descriptor.n, stream.descriptor.k,
        &stats, stream.host_slots[stripe->stripe_id]);
    if (status != IM2P_OK) {
      return status;
    }
    if (stream.executor.execute_work) {
      if (stream.accepted == 0) stream.first_start_cycle = stream.executor.last_start_cycle;
      stream.final_done_cycle = stream.executor.last_done_cycle;
      stream.completions.push_back({
          {stripe->stripe_id, stripe->i_start, stripe->rows, stripe->context},
          stream.executor.last_start_cycle, stream.executor.last_done_cycle,
          stream.executor.last_done_cycle - stream.executor.last_start_cycle});
    } else {
      const auto publish_cycle = stream.endpoint;
      stream.endpoint += stats.base.work_total_cycles;
      stream.completions.push_back({
          {stripe->stripe_id, stripe->i_start, stripe->rows, stripe->context},
          publish_cycle, stream.endpoint, stream.endpoint - publish_cycle});
    }
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
    stats->base.work_total_cycles = stream.executor.execute_work
        ? stream.final_done_cycle - stream.first_start_cycle
        : stream.endpoint;
    stats->base.completed_stripes = stream.returned;
    stats->base.completed_output_tiles = stream.executor.completed_tiles;
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
              full_done.stats.base.completed_output_tiles == 1,
          "frontend FULL did not complete exact output coverage");
  require_output(full_output);

  std::array<float, 6> pipeline_output{};
  pipeline_output.fill(91.0F);
  args.f_out = pipeline_output.data();
  WorkPlanV1 pipeline_plan = plan;
  pipeline_plan.mode = Mode::pipeline;
  Stream stream{{capability, context, callback, pipeline_plan}, {}, {},
                0, 0, 0, 0, 0, {}};
  stream.host_slots.resize(rows);
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
    stream.host_slots[row] = static_cast<std::uint32_t>(event.slot);
    require(frontend::submit_stripe(*pipeline.run, event).ok(),
            "frontend PIPELINE stripe rejected");
  }
  const auto pipeline_done = frontend::fence(*pipeline.run);
  require(pipeline_done.status.ok() && stream.accepted == rows &&
              stream.returned == rows &&
              pipeline_done.stats.base.completed_output_tiles == rows &&
              pipeline_done.stripe_rtl_timings.size == rows &&
              pipeline_done.stripe_rtl_timings[0].slot == 0 &&
              pipeline_done.stripe_rtl_timings[1].slot == 1,
          "frontend PIPELINE coverage incomplete");
  require(std::all_of(pipeline_output.begin(), pipeline_output.end(),
                      [](float value) { return value == 91.0F; }),
          "frontend PIPELINE published before authorization");
  require(frontend::authorize_output_commit(*pipeline.run, true).ok(),
          "frontend PIPELINE commit authorization failed");
  require_output(pipeline_output);
}

void run_frontend_ws_rtl_fixture(const Capability &capability, void *context,
                                 FrontendRtlWorkExecute callback) {
  require(callback && capability.hp1_shift_only && capability.ws &&
              capability.block_size == 32 &&
              capability.activation_bits == GGML_GEMMINI_ACTIVATION_BITS &&
              capability.weight_bits == GGML_GEMMINI_WEIGHT_BITS &&
              capability.dim == DIM,
          "frontend WS RTL capability mismatch");
  constexpr std::size_t rows = 129, columns = 129, k = 96, blocks = k / 32;
  constexpr std::size_t stripe_rows = 64, stripes = 3;
  const auto activation = [](std::size_t row, std::size_t lane) {
    return static_cast<std::int8_t>(int((row * 7 + lane * 3 + row * lane) % 7) - 3);
  };
  const auto weight = [](std::size_t lane, std::size_t column) {
    return static_cast<std::int8_t>(int((lane * 5 + column * 3 + lane * column) % 7) - 3);
  };
  const auto exponent = [](std::size_t block, std::size_t column) {
    return (block + column) % 5 == 0 ? INT16_MIN : int((block * 2 + column) % 4);
  };
  const auto channel_scale = [](std::size_t column) {
    return std::ldexp(1.0F, -int(column % 3 + 1));
  };
  ggml_gemmini_args_t args{};
  args.I = rows;
  args.J = columns;
  args.K = k;
  require(args.A.allocate(rows, k, GGML_GEMMINI_ACTIVATION_BITS),
          "WS activation allocation failed");
  for (std::size_t row = 0; row < rows; ++row) {
    for (std::size_t lane = 0; lane < k; ++lane) {
      require(args.A.set(row, lane, activation(row, lane)),
              "WS activation encoding failed");
    }
  }
  args.sA = k;
  args.activation_rows_per_stripe = stripe_rows;
  args.stride_f_out = columns;
  args.col_stride_f_out = 1;
  args.matmul_layer = "gemmini_hp1_frontend_ws_rtl_fixture";
  args.act_quant.storage()
      .emplace<ggml::gemmini::quants::act::tensor::Meta>().scale = 0.5F;
  using Block = std::conditional_t<GGML_GEMMINI_WEIGHT_BITS == 4,
                                  block_q4_hp1, block_q8_hp1>;
  std::vector<Block> weights(columns * blocks);
  for (std::size_t column = 0; column < columns; ++column) {
    for (std::size_t block = 0; block < blocks; ++block) {
      auto &packed = weights[column * blocks + block];
      packed.channel_scale = channel_scale(column);
      packed.m = exponent(block, column);
      for (std::size_t lane = 0; lane < 32; ++lane) {
        set_code(packed, lane, weight(block * 32 + lane, column));
      }
    }
  }
#if GGML_GEMMINI_WEIGHT_BITS == 4
  args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q4_hp1;
  args.q4_hp1_blocks = weights.data();
  args.native_block_count = weights.size();
  args.native_blocks_per_row = blocks;
#else
  args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1;
  args.q8_hp1_blocks = weights.data();
  args.q8_hp1_block_count = weights.size();
  args.q8_hp1_blocks_per_row = blocks;
#endif
  args.native_weight_bytes = weights.size() * sizeof(Block);
  std::vector<std::int32_t> expected_integer(rows * columns);
  std::vector<float> expected_float(rows * columns);
  for (std::size_t row = 0; row < rows; ++row) {
    for (std::size_t column = 0; column < columns; ++column) {
      std::int32_t integer = 0;
      bool first = true;
      double restored = 0;
      for (std::size_t block = 0; block < blocks; ++block) {
        const int shift = exponent(block, column);
        if (shift == INT16_MIN) continue;
        for (std::size_t begin = block * 32; begin < (block + 1) * 32;
             begin += std::min<std::size_t>(DIM, 32)) {
          std::int64_t partial = 0;
          const auto end = std::min((block + 1) * 32,
                                    begin + std::min<std::size_t>(DIM, 32));
          for (std::size_t lane = begin; lane < end; ++lane) {
            const auto a = activation(row, lane), w = weight(lane, column);
            partial += std::int64_t(a) * w;
            restored += (double(a) * 0.5) *
                        (double(w) * std::ldexp(double(channel_scale(column)), shift));
          }
          const auto contribution = sat32(partial * (std::int64_t{1} << shift));
          integer = first ? contribution : sat32(std::int64_t{integer} + contribution);
          first = false;
        }
      }
      expected_integer[row * columns + column] = integer;
      expected_float[row * columns + column] = static_cast<float>(restored);
    }
  }
  ggml::gemmini::gemmini_set_tile_ws(&args);
  require(args.tile_I && args.tile_J && args.tile_K,
          "WS Gemmini tiler produced an empty tile");
  const WorkPlanV1 plan{rows, columns, k, args.tile_I, args.tile_J, args.tile_K,
                        stripe_rows, Mode::full, WorkKind::dense_hp1_final};
  const auto expected_tiles = ((rows + DIM - 1) / DIM) * ((columns + DIM - 1) / DIM);
  std::vector<float> output(rows * columns, 91.0F);
  args.f_out = output.data();
  Executor full{capability, context, nullptr, plan};
  full.execute_work = callback;
  full.expected_integer = &expected_integer;
  frontend::Options options{};
  options.full_executor_context = &full;
  options.full_executor = full_execute;
  auto run = frontend::execute(&args, frontend::Mode::full, options);
  require(run.status.ok() && run.run, "frontend WS FULL did not start");
  const auto done = frontend::fence(*run.run);
  require(done.status.ok() && full.next_work_id == 1 && full.completed_rows == rows &&
              done.stats.base.completed_output_tiles == expected_tiles &&
              done.stats.base.work_total_cycles == full.cycles &&
              output == expected_float,
          "frontend WS FULL oracle or exact coverage mismatch");

  std::fill(output.begin(), output.end(), 91.0F);
  WorkPlanV1 pipeline_plan = plan;
  pipeline_plan.mode = Mode::pipeline;
  Stream stream{{capability, context, nullptr, pipeline_plan}, {}, {},
                0, 0, 0, 0, 0, {}};
  stream.executor.execute_work = callback;
  stream.executor.expected_integer = &expected_integer;
  stream.host_slots.resize(stripes);
  auto table = stream.table();
  frontend::Options pipeline_options{};
  pipeline_options.stream_executor = &table;
  auto pipeline = frontend::execute(&args, frontend::Mode::stripe_pipeline, pipeline_options);
  require(pipeline.status.ok() && pipeline.run, "frontend WS PIPELINE did not start");
  for (std::size_t stripe = 0; stripe < stripes; ++stripe) {
    exsia::StripeReadyEvent event{};
    event.run_id = 12996;
    event.stripe_id = stripe;
    event.slot = stripe % 2;
    event.row_begin = stripe * stripe_rows;
    event.row_end = std::min(rows, event.row_begin + stripe_rows);
    stream.host_slots[stripe] = static_cast<std::uint32_t>(event.slot);
    require(frontend::submit_stripe(*pipeline.run, event).ok(),
            "frontend WS PIPELINE stripe rejected");
  }
  const auto pipeline_done = frontend::fence(*pipeline.run);
  require(pipeline_done.status.ok() && stream.accepted == stripes &&
              stream.returned == stripes && stream.executor.completed_rows == rows &&
              pipeline_done.stats.base.completed_output_tiles == expected_tiles &&
              pipeline_done.stats.base.completed_stripes == stripes &&
              pipeline_done.stats.base.work_total_cycles == stream.executor.cycles &&
              pipeline_done.stripe_rtl_timings.size == stripes,
          "frontend WS PIPELINE exact coverage mismatch");
  std::uint64_t first_start = 0;
  std::uint64_t previous_done = 0;
  for (std::size_t stripe = 0; stripe < stripes; ++stripe) {
    const auto &timing = pipeline_done.stripe_rtl_timings[stripe];
    if (stripe == 0) first_start = timing.publish_cycle;
    require(timing.stripe_id == stripe && timing.row_begin == stripe * stripe_rows &&
                timing.row_end == std::min(rows, (stripe + 1) * stripe_rows) &&
                timing.slot == stream.host_slots[stripe] &&
                timing.publish_cycle >= previous_done &&
                timing.completion_cycle > timing.publish_cycle &&
                timing.publish_to_completion_cycles ==
                    timing.completion_cycle - timing.publish_cycle,
            "frontend WS PIPELINE endpoint timing mismatch");
    previous_done = timing.completion_cycle;
  }
  require(previous_done - first_start == stream.executor.cycles,
          "frontend WS PIPELINE endpoint total mismatch");
  require(std::all_of(output.begin(), output.end(), [](float value) { return value == 91.0F; }),
          "frontend WS PIPELINE published before authorization");
  require(frontend::authorize_output_commit(*pipeline.run, true).ok(),
          "frontend WS PIPELINE output authorization failed");
  require(output == expected_float, "frontend WS PIPELINE double oracle mismatch");

  constexpr std::size_t outer_k = 8256;
  constexpr std::size_t outer_blocks = outer_k / 32;
  ggml_gemmini_args_t outer_args{};
  outer_args.I = 1;
  outer_args.J = 1;
  outer_args.K = outer_k;
  require(outer_args.A.allocate(1, outer_k, GGML_GEMMINI_ACTIVATION_BITS),
          "outer-K activation allocation failed");
  outer_args.sA = outer_k;
  outer_args.activation_rows_per_stripe = 1;
  outer_args.stride_f_out = 1;
  outer_args.col_stride_f_out = 1;
  outer_args.matmul_layer = "gemmini_hp1_frontend_outer_k_rtl_fixture";
  outer_args.act_quant.storage()
      .emplace<ggml::gemmini::quants::act::tensor::Meta>().scale = 0.5F;
  std::vector<Block> outer_weights(outer_blocks);
  for (auto &packed : outer_weights) {
    if constexpr (GGML_GEMMINI_WEIGHT_BITS == 4) {
      std::fill(std::begin(packed.qs), std::end(packed.qs), std::uint8_t{0x88});
    }
    packed.channel_scale = 0.5F;
    packed.m = 0;
  }
#if GGML_GEMMINI_WEIGHT_BITS == 4
  outer_args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q4_hp1;
  outer_args.q4_hp1_blocks = outer_weights.data();
  outer_args.native_block_count = outer_weights.size();
  outer_args.native_blocks_per_row = outer_blocks;
#else
  outer_args.weight_format = ggml_gemmini_args_t::im2p_weight_format_t::q8_hp1;
  outer_args.q8_hp1_blocks = outer_weights.data();
  outer_args.q8_hp1_block_count = outer_weights.size();
  outer_args.q8_hp1_blocks_per_row = outer_blocks;
#endif
  outer_args.native_weight_bytes = outer_weights.size() * sizeof(Block);
  ggml::gemmini::gemmini_set_tile_ws(&outer_args);
  const auto outer_geometry = outer_args.activation_geometry();
  require(outer_args.tile_I && outer_args.tile_J && outer_args.tile_K &&
              outer_geometry.ok() && outer_geometry.geometry.outer.k >= 2,
          "real Gemmini tiler did not produce multiple outer-K tiles");
  const std::array<std::size_t, 3> positions = {
      0, outer_args.tile_K * DIM, outer_k - 1};
  constexpr std::array<std::int8_t, 3> activation_values = {2, -3, 1};
  constexpr std::array<std::int8_t, 3> weight_values = {3, 2, -4};
  constexpr std::array<std::int16_t, 3> shifts = {0, 1, 2};
  std::vector<std::int8_t> outer_activation(outer_k);
  std::vector<std::int8_t> outer_weight(outer_k);
  std::vector<std::int16_t> outer_shift(outer_blocks);
  for (std::size_t index = 0; index < positions.size(); ++index) {
    const auto position = positions[index];
    require(position < outer_k && outer_args.A.set(0, position, activation_values[index]),
            "outer-K activation encoding failed");
    auto &packed = outer_weights[position / 32];
    set_code(packed, position % 32, weight_values[index]);
    packed.m = shifts[index];
    outer_activation[position] = activation_values[index];
    outer_weight[position] = weight_values[index];
    outer_shift[position / 32] = shifts[index];
  }
  std::int32_t outer_integer = 0;
  bool outer_first = true;
  for (std::size_t begin = 0; begin < outer_k;
       begin += std::min<std::size_t>(DIM, 32)) {
    std::int64_t partial = 0;
    const auto end = std::min(outer_k, begin + std::min<std::size_t>(DIM, 32));
    for (auto lane = begin; lane < end; ++lane) {
      partial += std::int64_t{outer_activation[lane]} * outer_weight[lane];
    }
    const auto contribution = sat32(
        partial * (std::int64_t{1} << outer_shift[begin / 32]));
    outer_integer = outer_first ? contribution
                                : sat32(std::int64_t{outer_integer} + contribution);
    outer_first = false;
  }
  const std::vector<std::int32_t> outer_expected_integer = {
      outer_integer};
  std::array<float, 1> outer_output{91.0F};
  outer_args.f_out = outer_output.data();
  const WorkPlanV1 outer_plan{1, 1, outer_k, outer_args.tile_I, outer_args.tile_J,
      outer_args.tile_K, 1, Mode::full, WorkKind::dense_hp1_final};
  Executor outer{capability, context, nullptr, outer_plan};
  outer.execute_work = callback;
  outer.expected_integer = &outer_expected_integer;
  frontend::Options outer_options{};
  outer_options.full_executor_context = &outer;
  outer_options.full_executor = full_execute;
  auto outer_run = frontend::execute(&outer_args, frontend::Mode::full, outer_options);
  require(outer_run.status.ok() && outer_run.run,
          "frontend WS outer-K FULL did not start");
  const auto outer_done = frontend::fence(*outer_run.run);
  require(outer_done.status.ok() && outer.next_work_id == 1 &&
              outer_done.stats.base.completed_output_tiles == 1 &&
              outer_done.stats.base.work_total_cycles == outer.cycles &&
              outer_output[0] == static_cast<float>(double(outer_integer) * 0.25),
          "frontend WS outer-K oracle or coverage mismatch");
}

}
