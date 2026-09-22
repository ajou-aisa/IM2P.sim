#include "rmd_rtl_fixture.hpp"

#include "quants/act/meta.hpp"
#include "quants/common/hp1_scu.hpp"
#include "residual/rmd/rmd-compose.hpp"
#include "residual/rmd/rmd-run-aware.hpp"

#include <algorithm>
#include <array>
#include <cstdio>
#include <cstring>
#include <limits>
#include <stdexcept>

namespace im2p::gemmini_hp1 {
namespace {
namespace rmd = ggml::gemmini::rmd;

void require(bool condition, const char *message) {
  if (!condition)
    throw std::runtime_error(message);
}

int code(std::size_t column, std::size_t k) {
  return column == 2 ? 7 : static_cast<int>((column * 11 + k * 3) % 15) - 7;
}

float channel_scale(std::size_t column) {
  return static_cast<float>(column + 1) * 0.0625F;
}

enum class Fault { none, missing, excess, execution };

struct ObservedExecutor {
  void *context;
  RmdRunExecute execute;
  std::size_t calls = 0;
  std::size_t raw_exact = 0;
  bool odd_k = false;
  bool dense_clamp = false;
  Fault fault = Fault::none;
};

int observed_execute(void *opaque, const RmdRunWork &work,
                     std::vector<std::int32_t> &values, std::uint64_t &cycles) {
  auto &observed = *static_cast<ObservedExecutor *>(opaque);
  require(work.plan.kind == WorkKind::dense_hp1_final && !work.runs.empty() &&
              work.carriers.size() == work.runs.size() * work.plan.n,
          "RMD callback lost compact-run work contract");
  std::vector<std::int32_t> expected(work.plan.m * work.plan.n);
  for (std::size_t row = 0; row < work.plan.m; ++row) {
    for (std::size_t column = 0; column < work.plan.n; ++column) {
      std::int32_t acc = 0;
      for (std::size_t ordinal = 0; ordinal < work.runs.size(); ++ordinal) {
        const auto &run = work.runs[ordinal];
        std::int32_t dot = 0;
        std::size_t local = 0;
        for (std::uint32_t bit = 0; bit < 32; ++bit) {
          if (!(run.original_k_mask & (std::uint32_t{1} << bit)))
            continue;
          const auto original_k = std::uint64_t{run.original_block_id} * 32 + bit;
          const auto compact_k = run.compact_k_begin + local;
          require(original_k < work.original_k && compact_k < work.plan.k,
                  "RMD original K owner exceeds run bounds");
          dot += std::int32_t{work.activations[row * work.plan.k + compact_k]} *
                 work.weights[compact_k * work.plan.n + column];
          ++local;
          if (local % DIM == 0 || local == run.compact_k_count) {
            const auto q = ggml::gemmini::quants::hp1::apply_validated(
                dot, work.carriers[ordinal * work.plan.n + column]);
            acc = ggml::gemmini::quants::hp1::accumulate(acc, q);
            observed.dense_clamp |= q == INT32_MIN || q == INT32_MAX;
            dot = 0;
          }
        }
        require(local == run.compact_k_count,
                "RMD original K mask count differs from compact count");
      }
      expected[row * work.plan.n + column] = acc;
    }
  }
  const int status = observed.execute(observed.context, work, values, cycles);
  if (status != IM2P_OK)
    return status;
  require(values == expected,
          "RMD run-aware RTL output differs from compact-request diagnostic");
  require(cycles > 0, "RMD callback returned no measured RTL cycles");
  ++observed.calls;
  observed.raw_exact += values.size();
  observed.odd_k |= work.plan.k % 2 != 0;
  switch (observed.fault) {
  case Fault::missing:
    values.pop_back();
    break;
  case Fault::excess:
    values.push_back(0);
    break;
  case Fault::execution:
    return IM2P_ERROR;
  case Fault::none:
    break;
  }
  return IM2P_OK;
}

void assembler_negatives() {
  rmd::RmdStripeBuilder builder;
  builder.reset(0, 0, 1, 32, 1, GGML_GEMMINI_ACTIVATION_BITS);
  require(builder.add_residual(0, 0, 1), "RMD assembler fixture build failed");
  const auto packet = builder.finish();
  require(packet != nullptr, "RMD assembler fixture packet missing");
  rmd::Correction correction;
  rmd::RmdOutputAssembler assembler;
  require(assembler.begin(*packet, correction) == rmd::RmdStatus::success &&
              assembler.finish() == rmd::RmdStatus::invalid_packet,
          "RMD missing tile accepted");
  require(assembler.begin(*packet, correction) == rmd::RmdStatus::success,
          "RMD duplicate fixture begin failed");
  std::array<std::int64_t, DIM * DIM> values{};
  const rmd::PhysicalTile tile{0, 0, 0, 0, 0, 1, 1, values.data()};
  require(assembler.submit(tile) == rmd::RmdStatus::success &&
              assembler.submit(tile) == rmd::RmdStatus::invalid_arguments,
          "RMD duplicate tile accepted");
}

rmd::RmdStatus compact_request_expected(
    const ggml_gemmini_args_t &args, const rmd::StripePacket &packet,
    std::vector<std::int64_t> &output) {
  rmd::RunAwareRequest request;
  const auto status = rmd::build_run_aware_request(args, packet, request);
  if (status != rmd::RmdStatus::success)
    return status;
  const auto radix = rmd::balanced_radix_contract(packet.digit_bits);
  std::vector<__int128> sums(packet.row_count * request.n);
  for (std::size_t row = 0; row < request.rows.size(); ++row) {
    const auto &source = request.rows[row];
    __int128 place = 1;
    for (std::uint8_t lane = 0; lane < source.original_lane_id; ++lane)
      place *= radix.radix;
    for (std::size_t column = 0; column < request.n; ++column) {
      std::int32_t accumulated = 0;
      for (std::size_t ordinal = 0; ordinal < request.runs.size(); ++ordinal) {
        const auto &run = request.runs[ordinal];
        std::int32_t partial = 0;
        std::size_t local = 0;
        for (std::uint32_t bit = 0; bit < 32; ++bit) {
          if (!(run.union_k_mask & (std::uint32_t{1} << bit)))
            continue;
          const auto compact_k = run.compact_k_begin + local;
          partial +=
              std::int32_t{request.activations[row * request.k + compact_k]} *
              request.weights[compact_k * request.n + column];
          ++local;
          if (local % DIM == 0 || local == run.compact_k_count) {
            const auto scaled = ggml::gemmini::quants::hp1::apply_validated(
                partial, request.carriers[ordinal * request.n + column]);
            accumulated = ggml::gemmini::quants::hp1::accumulate(accumulated,
                                                                  scaled);
            partial = 0;
          }
        }
        require(local == run.compact_k_count,
                "RMD original-coordinate run mask is inconsistent");
      }
      sums[source.source_row * request.n + column] +=
          static_cast<__int128>(accumulated) * place;
    }
  }
  output.clear();
  for (const auto value : sums) {
    if (value < INT64_MIN || value > INT64_MAX)
      return rmd::RmdStatus::overflow;
    output.push_back(static_cast<std::int64_t>(value));
  }
  return rmd::RmdStatus::success;
}

} // namespace

RmdRtlFixture::RmdRtlFixture()
    : output(rows * columns, 0.0F), q4_(columns * 2), q8_(columns * 2),
      residuals_(rows * k, 0), exponents_{INT16_MIN, 0, 2, 7, 0, 24} {
  args.I = rows;
  args.J = columns;
  args.K = k;
  args.block_size_k = 32;
  args.native_block_count = columns * 2;
  args.native_blocks_per_row = 2;
  args.f_out = output.data();
  args.stride_f_out = columns;
  args.col_stride_f_out = 1;
  args.matmul_layer = "gemmini_hp1_rmd_rtl_fixture";
  require(args.A.allocate(rows, k, GGML_GEMMINI_ACTIVATION_BITS),
          "RMD activation fixture allocation failed");
  args.act_quant.storage()
      .emplace<ggml::gemmini::quants::act::tensor::Meta>()
      .scale = 0.5F;
  for (std::size_t column = 0; column < columns; ++column) {
    for (std::size_t block = 0; block < 2; ++block) {
      const auto index = column * 2 + block;
      q4_[index].channel_scale = q8_[index].channel_scale =
          channel_scale(column);
      set_exponent(column, block, exponents_[index]);
      for (std::size_t lane = 0; lane < 32; ++lane) {
        const auto value = code(column, block * 32 + lane);
        q8_[index].qs[lane] = static_cast<std::int8_t>(value);
        const auto nibble = static_cast<std::uint8_t>(value + 8);
        auto &packed = q4_[index].qs[lane % 16];
        packed =
            lane < 16
                ? static_cast<std::uint8_t>((packed & 0xf0U) | nibble)
                : static_cast<std::uint8_t>((packed & 0x0fU) | (nibble << 4));
      }
    }
  }
  using Format = ggml_gemmini_args_t::im2p_weight_format_t;
  if constexpr (GGML_GEMMINI_WEIGHT_BITS == 4) {
    args.weight_format = Format::q4_hp1;
    args.q4_hp1_blocks = q4_.data();
    args.native_weight_bytes = q4_.size() * sizeof(block_q4_hp1);
  } else {
    args.weight_format = Format::q8_hp1;
    args.q8_hp1_blocks = q8_.data();
    args.q8_hp1_block_count = q8_.size();
    args.q8_hp1_blocks_per_row = 2;
    args.native_weight_bytes = q8_.size() * sizeof(block_q8_hp1);
  }
  for (std::size_t row = 0; row < rows; ++row) {
    auto *residual = residuals_.data() + row * k;
    residual[0] = INT32_MAX;
    residual[1] = INT32_MIN;
    residual[3] = 0x12345678;
    residual[11] = -0x12345678;
    residual[31] = 1 << (GGML_GEMMINI_ACTIVATION_BITS * 2);
    for (std::size_t lane = 0; lane < 5; ++lane)
      residual[32 + lane] = 7;
    if (row % 3 == 1) {
      residual[32] = INT32_MAX;
      residual[33] = INT32_MIN;
    }
    if (row % 3 == 2) {
      for (std::size_t lane = 0; lane < 32; ++lane)
        residual[lane] = (lane % 2 == 0 ? 1 : -1) * static_cast<int>(lane + 1);
    }
  }
}

void RmdRtlFixture::set_exponent(std::size_t column, std::size_t block,
                                 std::int16_t exponent) {
  require(column < columns && block < 2, "RMD fixture exponent index invalid");
  const auto index = column * 2 + block;
  exponents_[index] = q4_[index].m = q8_[index].m = exponent;
}

rmd::StripePacketHandle RmdRtlFixture::packet(std::size_t stripe_id,
                                              std::size_t row_begin,
                                              std::size_t row_count) const {
  require(row_begin < rows && row_count <= rows - row_begin,
          "RMD fixture stripe range invalid");
  rmd::RmdStripeBuilder builder;
  builder.reset(stripe_id, row_begin, row_count, k, columns,
                GGML_GEMMINI_ACTIVATION_BITS);
  for (std::size_t row = 0; row < row_count; ++row) {
    for (std::size_t lane = 0; lane < k; ++lane) {
      require(builder.add_residual(row, lane,
                                   residuals_[(row_begin + row) * k + lane]),
              "RMD full INT32 builder rejected residual");
    }
  }
  const auto result = builder.finish();
  require(result != nullptr &&
              rmd::validate_packet(*result) == rmd::RmdStatus::success,
          "RMD full INT32 packet invalid");
  return result;
}

std::vector<std::int64_t>
RmdRtlFixture::expected_correction(std::size_t row_begin,
                                   std::size_t row_count) const {
  std::vector<std::int64_t> expected;
  const auto input = packet(0, row_begin, row_count);
  require(compact_request_expected(args, *input, expected) ==
              rmd::RmdStatus::success,
          "RMD compact-request diagnostic overflow");
  return expected;
}

std::vector<float> RmdRtlFixture::expected_merge(std::size_t row_begin,
                                                 std::size_t row_count) const {
  const auto integers = expected_correction(row_begin, row_count);
  std::vector<float> expected(integers.size());
  for (std::size_t row = 0; row < row_count; ++row)
    for (std::size_t column = 0; column < columns; ++column)
      expected[row * columns + column] =
          static_cast<float>(double(integers[row * columns + column]) *
                             double(channel_scale(column)) * 0.5);
  return expected;
}

void run_rmd_ws_rtl_fixture(const Capability &capability, void *context,
                            RmdScuExecute scu, RmdRunExecute runs) {
  require(capability.rmd && scu && runs,
          "RMD RTL capability/callback missing");
  RmdRtlFixture fixture;
  ObservedExecutor observed{context, runs};
  RmdExecutorContext executor{capability, &observed, nullptr,
                              0,          0,         scu, observed_execute};
  const auto full = fixture.packet(0, 0, fixture.rows);
  const auto lane_count = GGML_GEMMINI_ACTIVATION_BITS == 4 ? 9U : 5U;
  require(full->lane_capacity == lane_count,
          "RMD full INT32 lane capacity changed");
  bool high_carry = false;
  bool all_lanes = false;
  for (const auto &block : full->blocks) {
    all_lanes |= block.active_lane_count == lane_count;
    for (std::size_t position = 0; position < block.active_lane_count;
         ++position)
      high_carry |= block.lane_ids[position] == lane_count - 1;
  }
  require(high_carry && all_lanes,
          "RMD full INT32 lane/high carry coverage missing");
  auto missing_runs = executor;
  missing_runs.execute_runs = nullptr;
  rmd::Correction rejected = rmd::PreScaledFloat64Correction{{91.0}};
  require(execute_rmd_packet(missing_runs, fixture.args, *full, rejected) ==
                  rmd::RmdStatus::unsupported_route &&
              std::get<rmd::PreScaledFloat64Correction>(rejected).values ==
                  std::vector<double>{91.0},
          "RMD packet accepted a missing planned-runs callback");

  std::size_t composed = 0;
  std::size_t merged = 0;
  for (std::size_t pass = 0; pass < 4; ++pass) {
    const auto begin = pass == 0 ? 0 : (pass - 1) * 3;
    const auto rows = pass == 0 ? fixture.rows : 3;
    executor.host_slot = pass == 0 ? 0 : (pass - 1) % 2;
    const auto packet = fixture.packet(pass, begin, rows);
    rmd::Correction correction;
    rmd::RmdExecutionMetrics metrics{};
    const auto status =
        execute_rmd_packet(executor, fixture.args, *packet, correction, &metrics);
    if (status != rmd::RmdStatus::success)
      throw std::runtime_error(std::string{"RMD external packet execution failed: "} +
                               rmd::rmd_status_message(status));
    const auto *integer =
        std::get_if<rmd::BlockScaledInt64Correction>(&correction);
    require(
        integer && integer->values == fixture.expected_correction(begin, rows),
        "RMD SCU/radix composition differs from hardware-equivalent reference");
    require(metrics.im2p_dot_calls > 0 &&
                metrics.im2p_stats.work_total_cycles() > 0,
            "RMD provider execution metrics missing");
    composed += integer->values.size();
    std::fill(fixture.output.begin(), fixture.output.end(), 0.0F);
    require(rmd::merge_rmd_correction(fixture.args, *packet, correction) ==
                rmd::RmdStatus::success,
            "RMD CPU float merge failed");
    const auto expected = fixture.expected_merge(begin, rows);
    require(std::equal(expected.begin(), expected.end(),
                       fixture.output.begin() + begin * fixture.columns),
            "RMD CPU float merge differs from independent golden");
    merged += expected.size();
  }
  assembler_negatives();
  for (const auto fault : {Fault::missing, Fault::excess, Fault::execution}) {
    observed.fault = fault;
    rmd::Correction correction = rmd::PreScaledFloat64Correction{{91.0}};
    rmd::RmdExecutionMetrics metrics{};
    metrics.packet_call_count = 73;
    require(execute_rmd_packet(executor, fixture.args, *full, correction,
                               &metrics) != rmd::RmdStatus::success &&
                std::get<rmd::PreScaledFloat64Correction>(correction).values ==
                    std::vector<double>{91.0} &&
                metrics.packet_call_count == 73,
            "RMD invalid hardware completion published output/metrics");
  }
  observed.fault = Fault::none;
  fixture.set_exponent(2, 0, 62);
  rmd::Correction unchanged = rmd::PreScaledFloat64Correction{{19.0}};
  std::vector<std::int64_t> high_expected;
  const auto expected_status =
      compact_request_expected(fixture.args, *full, high_expected);
  const auto actual_status =
      execute_rmd_packet(executor, fixture.args, *full, unchanged);
  require(
      actual_status == expected_status &&
          (actual_status == rmd::RmdStatus::success
               ? std::get<rmd::BlockScaledInt64Correction>(unchanged).values ==
                     high_expected
               : std::get<rmd::PreScaledFloat64Correction>(unchanged).values ==
                     std::vector<double>{19.0}),
      "RMD high exponent SCU/radix status or transactionality mismatch");
  require(observed.odd_k && observed.dense_clamp,
          "RMD sparse odd K/dense clamp distinction not exercised");
  std::printf(
      "WS_RMD_RUNS_DIAGNOSTIC A%uW%uD%u run_callbacks=%zu compact_exact=%zu lanes=%u "
      "high_carry=1 "
      "compose_exact=%zu merge_exact=%zu negative_tests=7 missing_reject=1 "
      "duplicate_reject=1 missing_runs_reject=1 high_exponent_run=1 sparse_k=1 odd_k=1 stripes=3 "
      "slots=0,1,0\n",
      capability.activation_bits, capability.weight_bits, capability.dim,
      observed.calls, observed.raw_exact, lane_count, composed, merged);
}

} // namespace im2p::gemmini_hp1
