#include "im2p_gemmini_frontend.hpp"

#include "ggml-gemmini-args.h"
#include "quants/act/exsia/exsia.hpp"
#include "quants/common/weight_route.hpp"
#if defined(IM2P_GEMMINI_FRONTEND_TESTING)
#include "../tests/im2p_gemmini_frontend_testing.hpp"
#endif

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstring>
#include <deque>
#include <limits>
#include <mutex>
#include <new>
#include <thread>
#include <type_traits>
#include <unordered_map>
#include <utility>
#include <vector>

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

Status from_c_status(int value, Route route, const char *operation,
                     bool native = false) noexcept {
  switch (value) {
  case IM2P_OK:
    return make_status(StatusCode::success, route, native, "success");
  case IM2P_BACKPRESSURE:
    return make_status(StatusCode::backpressure, route, native,
                       "IM2P raw queue is full");
  case IM2P_INVALID_LAYOUT:
    return make_status(StatusCode::invalid_contract, route, native,
                       "invalid IM2P operand layout");
  case IM2P_UNFINISHED_STREAM:
    return make_status(StatusCode::invalid_state, route, native,
                       "IM2P simulator already owns a stream");
  case IM2P_DUPLICATE_STRIPE:
    return make_status(StatusCode::invalid_contract, route, native,
                       "duplicate IM2P stripe");
  case IM2P_LATE_STRIPE:
    return make_status(StatusCode::invalid_contract, route, native,
                       "late IM2P stripe");
  case IM2P_CONFIGURATION_MISMATCH:
    return make_status(StatusCode::invalid_contract, route, native,
                       "IM2P ABI v2 configuration mismatch");
  default:
    return make_status(StatusCode::execution_failure, route, native, operation);
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
    return false;
  default:
    return false;
  }
}

enum class RoutePolicy : uint8_t {
  legacy,
  provider,
  deprecated,
  unsupported,
  unknown
};

RoutePolicy route_policy(Route route) noexcept {
  switch (route) {
  case Route::q8_h0:
    return RoutePolicy::legacy;
  case Route::q8_0_unpacked_to_h1:
  case Route::q8_h1:
  case Route::q8_hp1:
  case Route::q8_channel:
  case Route::q8_channel_dense_sidecar:
    return RoutePolicy::provider;
  case Route::q8_h2:
    return RoutePolicy::deprecated;
  case Route::q8_hp2:
    return RoutePolicy::unsupported;
  case Route::unknown:
    return RoutePolicy::unknown;
  }
  return RoutePolicy::unknown;
}

bool provider_route_contract(const ggml_gemmini_args_t &a,
                             Route route) noexcept {
  if (a.I == 0 || a.J == 0 || a.K == 0 || a.A.raw_data() == nullptr ||
      a.f_out == nullptr || a.A.row_stride_bytes < a.K * ((a.A.bits + 7) / 8) ||
      a.transpose_A || a.D != nullptr || a.repeating_bias || a.low_D ||
      a.weight_i8_scale_active || a.act != 0 ||
      a.scale_B != static_cast<scale_t>(1) ||
      a.scale_D != static_cast<scale_acc_t>(1) ||
      a.scale != static_cast<acc_scale_t>(1) ||
      a.bert_scale != static_cast<acc_scale_t>(1))
    return false;
  switch (route) {
  case Route::q8_0_unpacked_to_h1: {
    const size_t blocks = (a.K + 31) / 32;
    const bool striped = a.stripe_J > 1;
    return a.B != nullptr && a.sB == a.K && a.blocks_per_row == blocks &&
           a.c_b != nullptr &&
           (striped ? (a.s_rf_stripe != nullptr && a.R_stripe != nullptr)
                    : (a.s_rf != nullptr && a.R != nullptr));
  }
  case Route::q8_h1:
    return wroute::is_q8_h1_args(a) && a.has_q8_h1_im2p_contract();
  case Route::q8_hp1:
    return wroute::has_q8_hp1_native_contract(a);
  case Route::q8_channel:
    return wroute::is_q8_channel_direct_read_args(a) &&
           a.has_q8_channel_direct_read_contract();
  case Route::q8_channel_dense_sidecar:
    return a.has_q8_channel_dense_sidecar_contract();
  default:
    return false;
  }
}

bool snapshot_activation_scales(const ggml_gemmini_args_t &a, size_t begin,
                                size_t end, std::vector<float> &out) {
  if (a.I > std::numeric_limits<size_t>::max() - a.activation_row_offset ||
      begin > end || end > a.I)
    return false;
  size_t rows_per_stripe =
      a.activation_rows_per_stripe != 0 ? a.activation_rows_per_stripe : a.I;
  if (rows_per_stripe == 0)
    return false;
  try {
    out.resize(a.I);
  } catch (...) {
    return false;
  }
  const auto &storage = a.act_quant.storage();
  for (size_t i = begin; i < end; ++i) {
    const size_t row = a.activation_row_offset + i;
    float scale = 0.0f;
    bool valid = true;
    std::visit(
        [&](const auto &meta) {
          using T = std::decay_t<decltype(meta)>;
          if constexpr (std::is_same_v<T, act::exsia::Meta>) {
            const size_t stripe = row / rows_per_stripe;
            if (stripe >= meta.theta.size() ||
                meta.theta[stripe] == std::numeric_limits<int16_t>::min())
              valid = false;
            else
              scale = std::ldexp(1.0f, meta.theta[stripe]);
          } else if constexpr (std::is_same_v<T, act::tensor::Meta>) {
            scale = meta.scale;
          } else if constexpr (std::is_same_v<T, act::token::Meta> ||
                               std::is_same_v<T, act::block::Meta>) {
            if (row >= meta.scales.size())
              valid = false;
            else
              scale = meta.scales[row];
          } else if constexpr (std::is_same_v<T, act::stripe::Meta>) {
            const size_t stripe = row / rows_per_stripe;
            if (stripe >= meta.scales.size())
              valid = false;
            else
              scale = meta.scales[stripe];
          } else {
            valid = false;
          }
        },
        storage);
    if (!valid || !std::isfinite(scale) || scale <= 0.0f)
      return false;
    out[i] = scale;
  }
  return true;
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
  bool repeating_bias = false, weight_i8_scale_active = false;
  int act = 0;
  uint8_t activation_bits = 0;
  size_t activation_raw_size = 0, activation_row_stride_bytes = 0;
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
          a.repeating_bias,
          a.weight_i8_scale_active,
          a.act,
          a.A.bits,
          a.A.raw_size(),
          a.A.row_stride_bytes};
}

PointerSnapshot snapshot_pointers(const ggml_gemmini_args_t &a) noexcept {
  return {a.A.raw_data(),
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
    ggml::gemmini::rmd::StripePacketHandle rmd_packet;
    ggml::gemmini::residual::DirectStripePayloadHandle direct_residual;
  };

  Impl(const ggml_gemmini_args_t *source, Mode requested_mode,
       Options requested_options)
      : scalars(snapshot_scalars(*source)),
        pointers(snapshot_pointers(*source)), mode(requested_mode),
        options(requested_options), route(classify_format(*source)),
        native(native_contract(*source, route)),
        final_status(
            make_status(StatusCode::success, route, native, "success")),
        activation_owner(source->A.bytes),
        pipeline_exsia(std::holds_alternative<act::exsia::Meta>(
            source->act_quant.storage())) {}

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
  std::shared_ptr<std::vector<uint8_t>> activation_owner;
  bool pipeline_exsia = false;
  std::shared_ptr<std::vector<int8_t>> weight_owner;
  std::shared_ptr<std::vector<uint8_t>> byte_metadata_owner;
  std::shared_ptr<std::vector<float>> scale_owner;
  std::shared_ptr<std::vector<uint16_t>> residual_owner;
  std::shared_ptr<std::vector<block_q8_h1>> h1_owner;
  std::shared_ptr<std::vector<block_q8_hp1>> hp1_owner;
  std::shared_ptr<std::vector<int32_t>> integer_output_stage;
  std::shared_ptr<std::vector<float>> float_output_stage;
  void *integer_output_destination = nullptr;
  float *float_output_destination = nullptr;
  im2p_work_stats_extended_t stats{};
  std::deque<DenseEvent> ready;
  std::unordered_map<uint32_t, DenseEvent> in_flight;
  static constexpr size_t producer_slot_count = 2;
  size_t outstanding = 0, next_row = 0, next_stripe = 0;
  size_t blocked_producers = 0;
  uint64_t completion_generation = 0;
#if defined(IM2P_GEMMINI_FRONTEND_TESTING)
  bool progress_held = false;
  bool progress_failure_injected = false;
  bool poll_failure_injected = false;
  bool completion_gate_enabled = false;
  uint64_t completion_gate_permits = 0;
#endif
  bool bound_run = false;
  uint64_t run_id = 0;
  size_t tile_i_rows = 0, tile_j_columns = 0;
  std::vector<float> activation_scales;
  bool provider_failed = false;
  bool output_committed = false;

  bool retain_legacy_operands() {
    size_t weight_count = 0, output_count = 0;
    const size_t sb = scalars.sb ? scalars.sb : scalars.j;
    const size_t sc = scalars.sc ? scalars.sc : scalars.j;
    if (!checked_mul(scalars.k, sb, weight_count) ||
        !checked_mul(scalars.i, sc, output_count))
      return false;
    try {
      weight_owner = std::make_shared<std::vector<int8_t>>(
          static_cast<const int8_t *>(pointers.b),
          static_cast<const int8_t *>(pointers.b) + weight_count);
      integer_output_stage =
          std::make_shared<std::vector<int32_t>>(output_count, 0);
    } catch (...) {
      return false;
    }
    integer_output_destination = const_cast<void *>(pointers.c);
    pointers.b = weight_owner->data();
    pointers.c = integer_output_stage->data();
    return true;
  }

  bool retain_provider_operands() {
    try {
      switch (route) {
      case Route::q8_0_unpacked_to_h1: {
        size_t weight_count = 0, code_count = 0;
        if (!checked_mul(scalars.j, scalars.k, weight_count) ||
            !checked_mul(scalars.j, scalars.blocks_per_row, code_count))
          return false;
        weight_owner = std::make_shared<std::vector<int8_t>>(
            static_cast<const int8_t *>(pointers.b),
            static_cast<const int8_t *>(pointers.b) + weight_count);
        byte_metadata_owner = std::make_shared<std::vector<uint8_t>>(
            static_cast<const uint8_t *>(pointers.c_b),
            static_cast<const uint8_t *>(pointers.c_b) + code_count);
        const bool striped = scalars.stripe_j > 1;
        const size_t scale_count =
            striped ? (scalars.j + scalars.stripe_j - 1) / scalars.stripe_j
                    : scalars.j;
        const auto *scales = static_cast<const float *>(
            striped ? pointers.s_rf_stripe : pointers.s_rf);
        const auto *residuals = static_cast<const uint16_t *>(
            striped ? pointers.r_stripe : pointers.r);
        scale_owner =
            std::make_shared<std::vector<float>>(scales, scales + scale_count);
        residual_owner = std::make_shared<std::vector<uint16_t>>(
            residuals, residuals + scale_count);
        pointers.b = weight_owner->data();
        pointers.c_b = byte_metadata_owner->data();
        if (striped) {
          pointers.s_rf_stripe = scale_owner->data();
          pointers.r_stripe = residual_owner->data();
        } else {
          pointers.s_rf = scale_owner->data();
          pointers.r = residual_owner->data();
        }
        break;
      }
      case Route::q8_h1:
        h1_owner = std::make_shared<std::vector<block_q8_h1>>(
            static_cast<const block_q8_h1 *>(pointers.q8_h1),
            static_cast<const block_q8_h1 *>(pointers.q8_h1) +
                scalars.q8_h1_count);
        pointers.q8_h1 = h1_owner->data();
        break;
      case Route::q8_hp1:
        hp1_owner = std::make_shared<std::vector<block_q8_hp1>>(
            static_cast<const block_q8_hp1 *>(pointers.q8_hp1),
            static_cast<const block_q8_hp1 *>(pointers.q8_hp1) +
                scalars.q8_hp1_count);
        pointers.q8_hp1 = hp1_owner->data();
        break;
      case Route::q8_channel: {
        size_t byte_count = 0;
        if (!checked_mul(scalars.channel_row_count, scalars.channel_row_stride,
                         byte_count))
          return false;
        byte_metadata_owner = std::make_shared<std::vector<uint8_t>>(
            static_cast<const uint8_t *>(pointers.channel_rows),
            static_cast<const uint8_t *>(pointers.channel_rows) + byte_count);
        pointers.channel_rows = byte_metadata_owner->data();
        break;
      }
      case Route::q8_channel_dense_sidecar: {
        size_t weight_count = 0;
        if (!checked_mul(scalars.j, scalars.k, weight_count))
          return false;
        weight_owner = std::make_shared<std::vector<int8_t>>(
            static_cast<const int8_t *>(pointers.b),
            static_cast<const int8_t *>(pointers.b) + weight_count);
        scale_owner = std::make_shared<std::vector<float>>(
            static_cast<const float *>(pointers.channel_scales),
            static_cast<const float *>(pointers.channel_scales) +
                scalars.channel_scale_count);
        pointers.b = weight_owner->data();
        pointers.channel_scales = scale_owner->data();
        break;
      }
      default:
        return false;
      }
    } catch (...) {
      return false;
    }
    return retain_provider_output();
  }

  bool retain_provider_output() {
    const size_t rs = scalars.stride_f_out ? scalars.stride_f_out : scalars.j;
    const size_t cs = scalars.col_stride_f_out ? scalars.col_stride_f_out : 1;
    size_t row_offset = 0, column_offset = 0;
    if (!checked_mul(scalars.i - 1, rs, row_offset) ||
        !checked_mul(scalars.j - 1, cs, column_offset) ||
        row_offset > std::numeric_limits<size_t>::max() - column_offset - 1)
      return false;
    const size_t count = row_offset + column_offset + 1;
    try {
      float_output_stage = std::make_shared<std::vector<float>>(count, 0.0f);
    } catch (...) {
      return false;
    }
    float_output_destination =
        static_cast<float *>(const_cast<void *>(pointers.f_out));
    pointers.f_out = float_output_stage->data();
    return true;
  }

  void commit_output() noexcept {
    if (integer_output_stage && integer_output_destination) {
      auto *destination = static_cast<int32_t *>(integer_output_destination);
      const size_t stride = scalars.sc ? scalars.sc : scalars.j;
      for (size_t row = 0; row < scalars.i; ++row)
        std::memcpy(destination + row * stride,
                    integer_output_stage->data() + row * stride,
                    scalars.j * sizeof(int32_t));
    } else if (float_output_stage && float_output_destination) {
      const size_t rs = scalars.stride_f_out ? scalars.stride_f_out : scalars.j;
      const size_t cs = scalars.col_stride_f_out ? scalars.col_stride_f_out : 1;
      for (size_t row = 0; row < scalars.i; ++row)
        for (size_t column = 0; column < scalars.j; ++column)
          float_output_destination[row * rs + column * cs] =
              (*float_output_stage)[row * rs + column * cs];
    }
  }

  struct FactorCache {
    bool valid = false;
    size_t block = 0, column = 0, count = 0;
    std::array<double, DIM> values{};
  };
  std::array<FactorCache, 2> factor_cache{};
  size_t next_factor_cache = 0;

  struct ReducerSlot {
    bool valid = false;
    size_t row = 0, tile_begin = 0, expected_block = 0;
    std::array<double, DIM> sums{};
    std::array<bool, DIM> seen{};
  };
  std::array<ReducerSlot, DIM> reducers{};

  size_t provider_block_size() const noexcept {
    switch (route) {
    case Route::q8_0_unpacked_to_h1:
    case Route::q8_h1:
    case Route::q8_hp1:
      return 32;
    default:
      return scalars.k;
    }
  }

  size_t provider_block_count() const noexcept {
    const size_t block = provider_block_size();
    return (scalars.k + block - 1) / block;
  }

  uint8_t provider_vector_op() const noexcept {
    switch (route) {
    case Route::q8_channel:
    case Route::q8_channel_dense_sidecar:
      return IM2P_VECTOR_BYPASS;
    default:
      return IM2P_VECTOR_EXTERNAL;
    }
  }

  bool factor(size_t block, size_t column, double &value) const noexcept {
    if (block >= provider_block_count() || column >= scalars.j)
      return false;
    switch (route) {
    case Route::q8_0_unpacked_to_h1: {
      const auto *cb = static_cast<const uint8_t *>(pointers.c_b);
      const bool striped = scalars.stripe_j > 1;
      const auto *sr = static_cast<const float *>(striped ? pointers.s_rf_stripe
                                                          : pointers.s_rf);
      const auto *rr = static_cast<const uint16_t *>(striped ? pointers.r_stripe
                                                             : pointers.r);
      const size_t scale_row = striped ? column / scalars.stripe_j : column;
      if (!cb || !sr || !rr)
        return false;
      value = static_cast<double>(sr[scale_row]) *
              static_cast<double>(
                  uint32_t(cb[column * scalars.blocks_per_row + block]) +
                  uint32_t(rr[scale_row]));
      break;
    }
    case Route::q8_h1: {
      const auto *blocks = static_cast<const block_q8_h1 *>(pointers.q8_h1);
      const auto &b = blocks[column * scalars.blocks_per_row + block];
      value = static_cast<double>(b.s_rf) *
              static_cast<double>(uint32_t(b.c_b) + uint32_t(b.R));
      break;
    }
    case Route::q8_hp1: {
      const auto *blocks = static_cast<const block_q8_hp1 *>(pointers.q8_hp1);
      const auto &b = blocks[column * scalars.q8_hp1_blocks_per_row + block];
      value = b.m == INT16_MIN ? 0.0
                               : static_cast<double>(gemmini_ldexp_fast_pos(
                                     b.channel_scale, int(b.m)));
      break;
    }
    case Route::q8_channel: {
      const auto *base = static_cast<const uint8_t *>(pointers.channel_rows);
      float scale = 0.0f;
      std::memcpy(&scale, base + column * scalars.channel_row_stride,
                  sizeof(scale));
      value = scale;
      break;
    }
    case Route::q8_channel_dense_sidecar:
      value = static_cast<const float *>(pointers.channel_scales)[column];
      break;
    default:
      return false;
    }
    return std::isfinite(value);
  }

  int read_weight(size_t row, size_t column, size_t count,
                  int8_t *out) noexcept {
    if (!out || count == 0 || count > DIM || row >= scalars.k ||
        column > scalars.j || count > scalars.j - column)
      return IM2P_ERROR;
    const size_t block = row / provider_block_size(),
                 lane = row % provider_block_size();
    for (size_t n = 0; n < count; ++n) {
      const size_t j = column + n;
      switch (route) {
      case Route::q8_0_unpacked_to_h1:
        out[n] = static_cast<const int8_t *>(pointers.b)[j * scalars.k + row];
        break;
      case Route::q8_h1:
        out[n] = static_cast<const block_q8_h1 *>(
                     pointers.q8_h1)[j * scalars.blocks_per_row + block]
                     .qs[lane];
        break;
      case Route::q8_hp1:
        out[n] = static_cast<const block_q8_hp1 *>(
                     pointers.q8_hp1)[j * scalars.q8_hp1_blocks_per_row + block]
                     .qs[lane];
        break;
      case Route::q8_channel: {
        const auto *base = static_cast<const uint8_t *>(pointers.channel_rows);
        out[n] = static_cast<int8_t>(
            base[j * scalars.channel_row_stride + sizeof(float) + row]);
        break;
      }
      case Route::q8_channel_dense_sidecar:
        out[n] = static_cast<const int8_t *>(pointers.b)[j * scalars.k + row];
        break;
      default:
        return IM2P_ERROR;
      }
    }
    return IM2P_OK;
  }

  int read_scale(size_t block, size_t column, size_t count,
                 int8_t *out) noexcept {
    if (!out || count == 0 || count > DIM || column > scalars.j ||
        count > scalars.j - column)
      return IM2P_ERROR;
    auto &entry = factor_cache[next_factor_cache++ % factor_cache.size()];
    entry = {};
    entry.valid = true;
    entry.block = block;
    entry.column = column;
    entry.count = count;
    for (size_t n = 0; n < count; ++n) {
      if (!factor(block, column + n, entry.values[n]))
        return IM2P_ERROR;
      out[n] = 1;
    }
    return IM2P_OK;
  }

  bool cached_factor(size_t block, size_t column,
                     double &value) const noexcept {
    for (const auto &entry : factor_cache)
      if (entry.valid && entry.block == block && column >= entry.column &&
          column - entry.column < entry.count) {
        value = entry.values[column - entry.column];
        return true;
      }
    return factor(block, column, value);
  }

  int write_output(size_t block, size_t row, size_t column, size_t count,
                   const int32_t *values) noexcept {
    if (!values || count == 0 || count > DIM || row >= scalars.i ||
        column > scalars.j || count > scalars.j - column ||
        block >= provider_block_count())
      return IM2P_ERROR;
    const size_t tile_begin = (column / DIM) * DIM;
    auto &slot = reducers[row % DIM];
    if (!slot.valid || slot.row != row || slot.tile_begin != tile_begin) {
      if (slot.valid && slot.expected_block != provider_block_count())
        return IM2P_ERROR;
      slot = {};
      slot.valid = true;
      slot.row = row;
      slot.tile_begin = tile_begin;
    }
    if (block != slot.expected_block)
      return IM2P_ERROR;
    for (size_t n = 0; n < count; ++n) {
      const size_t lane = column + n - tile_begin;
      double weight = 0.0;
      if (lane >= DIM || slot.seen[lane] ||
          !cached_factor(block, column + n, weight))
        return IM2P_ERROR;
      slot.sums[lane] += static_cast<double>(values[n]) * weight;
      slot.seen[lane] = true;
    }
    const size_t width = std::min<size_t>(DIM, scalars.j - tile_begin);
    if (!std::all_of(slot.seen.begin(), slot.seen.begin() + width,
                     [](bool v) { return v; }))
      return IM2P_OK;
    ++slot.expected_block;
    slot.seen.fill(false);
    if (slot.expected_block == provider_block_count()) {
      auto *dst = static_cast<float *>(const_cast<void *>(pointers.f_out));
      const size_t row_stride =
          scalars.stride_f_out ? scalars.stride_f_out : scalars.j;
      const size_t col_stride =
          scalars.col_stride_f_out ? scalars.col_stride_f_out : 1;
      for (size_t lane = 0; lane < width; ++lane)
        dst[row * row_stride + (tile_begin + lane) * col_stride] +=
            static_cast<float>(slot.sums[lane] *
                               static_cast<double>(activation_scales[row]));
    }
    return IM2P_OK;
  }

  static int provider_read_weight(void *context, size_t row, size_t column,
                                  size_t count, int8_t *out) {
    auto &x = *static_cast<Impl *>(context);
    const int result = x.read_weight(row, column, count, out);
    if (result != IM2P_OK)
      x.provider_failed = true;
    return result;
  }
  static int provider_read_scale(void *context, size_t row, size_t column,
                                 size_t count, int8_t *out) {
    auto &x = *static_cast<Impl *>(context);
    const int result = x.read_scale(row, column, count, out);
    if (result != IM2P_OK)
      x.provider_failed = true;
    return result;
  }
  static int provider_write_output(void *context, size_t block, size_t row,
                                   size_t column, size_t count,
                                   const int32_t *values) {
    auto &x = *static_cast<Impl *>(context);
    const int result = x.write_output(block, row, column, count, values);
    if (result != IM2P_OK)
      x.provider_failed = true;
    return result;
  }

  im2p_provider_t provider() noexcept {
    return {this, provider_read_weight, provider_read_scale,
            provider_write_output};
  }

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

  im2p_matmul_desc_v2_t full_descriptor() noexcept {
    im2p_matmul_desc_v2_t d{};
    d.abi_version = IM2P_ABI_VERSION_2;
    d.activation_bits = scalars.activation_bits;
    d.activation_storage_bytes = (scalars.activation_bits + 7) / 8;
    d.dim = DIM;
    d.activations = pointers.a;
    d.weights = static_cast<const int8_t *>(pointers.b);
    d.output = static_cast<int32_t *>(const_cast<void *>(pointers.c));
    d.m = scalars.i;
    d.n = scalars.j;
    d.k = scalars.k;
    d.activation_row_stride_bytes = scalars.activation_row_stride_bytes;
    d.weight_row_stride = scalars.sb == 0 ? scalars.j : scalars.sb;
    d.output_row_stride = scalars.sc == 0 ? scalars.j : scalars.sc;
    d.tile_i_rows = tile_i_rows;
    d.tile_j_columns = tile_j_columns;
    d.block_size =
        scalars.block_size == 0 ? GGML_GEMMINI_BLOCK_SIZE : scalars.block_size;
    d.vector_op = IM2P_VECTOR_BYPASS;
    return d;
  }

  im2p_matmul_desc_v2_t provider_full_descriptor() noexcept {
    auto d = full_descriptor();
    d.weights = nullptr;
    d.output = nullptr;
    d.weight_row_stride = scalars.j;
    d.output_row_stride = scalars.j;
    d.block_size = provider_block_size();
    d.scale_total_k = scalars.k;
    d.scale_row_stride = scalars.j;
    d.scale_valid_columns = scalars.j;
    d.vector_op = provider_vector_op();
    d.provider = provider();
    return d;
  }

  im2p_stripe_work_desc_v2_t stripe_descriptor() const noexcept {
    im2p_stripe_work_desc_v2_t d{};
    d.abi_version = IM2P_ABI_VERSION_2;
    d.activation_bits = scalars.activation_bits;
    d.activation_storage_bytes = (scalars.activation_bits + 7) / 8;
    d.dim = DIM;
    d.weights = static_cast<const int8_t *>(pointers.b);
    d.output = static_cast<int32_t *>(const_cast<void *>(pointers.c));
    d.m = scalars.i;
    d.n = scalars.j;
    d.k = scalars.k;
    d.weight_row_stride = scalars.sb == 0 ? scalars.j : scalars.sb;
    d.output_row_stride = scalars.sc == 0 ? scalars.j : scalars.sc;
    d.tile_i_rows = tile_i_rows;
    d.tile_j_columns = tile_j_columns;
    d.block_size =
        scalars.block_size == 0 ? GGML_GEMMINI_BLOCK_SIZE : scalars.block_size;
    d.vector_op = IM2P_VECTOR_BYPASS;
    const size_t rows = scalars.activation_rows_per_stripe;
    d.stripe_count = (scalars.i + rows - 1) / rows;
    return d;
  }

  im2p_stripe_work_desc_v2_t provider_stripe_descriptor() noexcept {
    auto d = stripe_descriptor();
    d.weights = nullptr;
    d.output = nullptr;
    d.weight_row_stride = scalars.j;
    d.output_row_stride = scalars.j;
    d.block_size = provider_block_size();
    d.scale_total_k = scalars.k;
    d.scale_row_stride = scalars.j;
    d.scale_valid_columns = scalars.j;
    d.vector_op = provider_vector_op();
    d.provider = provider();
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
    int result = IM2P_ERROR;
    if (route_policy(route) == RoutePolicy::legacy) {
      const auto d = full_descriptor();
      result = im2p_execute_matmul_extended_v2(sim.get(), &d, &stats);
    } else {
      auto d = provider_full_descriptor();
      result = im2p_execute_matmul_extended_v2(sim.get(), &d, &stats);
    }
    if (provider_failed)
      set_error(make_status(StatusCode::execution_failure, route, native,
                            "IM2P provider callback failed"));
    else if (result != IM2P_OK)
      set_error(
          from_c_status(result, route, "IM2P full execution failed", native));
  }

  int publish(im2p_stream_t *stream, const DenseEvent &e) {
    size_t offset = 0;
    const size_t stride = scalars.activation_row_stride_bytes;
    if (!checked_mul(e.row_begin, stride, offset))
      return IM2P_ERROR;
    im2p_activation_stripe_v2_t s{};
    s.abi_version = IM2P_ABI_VERSION_2;
    s.activation_bits = scalars.activation_bits;
    s.activation_storage_bytes = (scalars.activation_bits + 7) / 8;
    s.dim = DIM;
    s.stripe_id = static_cast<uint32_t>(e.stripe_id);
    s.i_start = e.row_begin;
    s.rows = e.row_end - e.row_begin;
    s.activations = static_cast<const uint8_t *>(pointers.a) + offset;
    s.activation_row_stride_bytes = stride;
    s.context = e.run_id;
    const int result = im2p_publish_stripe_v2(stream, &s);
    if (result == IM2P_OK) {
      std::lock_guard lock(mutex);
      in_flight.emplace(s.stripe_id, e);
    }
    return result;
  }

  bool poll(im2p_stream_t *stream, size_t &completion_count) {
    completion_count = 0;
#if defined(IM2P_GEMMINI_FRONTEND_TESTING)
    {
      std::lock_guard lock(mutex);
      if (poll_failure_injected) {
        poll_failure_injected = false;
        if (final_status.ok())
          final_status = make_status(StatusCode::execution_failure, route,
                                     native, "injected IM2P poll failure");
        changed.notify_all();
        return false;
      }
    }
#endif
    for (;;) {
      im2p_stripe_completion_t c{};
      const int result = im2p_poll_completed(stream, &c);
      if (result < 0) {
        set_error(from_c_status(result, route, "IM2P completion poll failed",
                                native));
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
#if defined(IM2P_GEMMINI_FRONTEND_TESTING)
    {
      std::unique_lock lock(mutex);
      changed.wait(lock, [&] { return !progress_held || !final_status.ok(); });
      if (!final_status.ok())
        return false;
      if (progress_failure_injected) {
        progress_failure_injected = false;
        final_status = make_status(StatusCode::execution_failure, route, native,
                                   "injected IM2P progress failure");
        changed.notify_all();
        return false;
      }
    }
#endif
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
      int result = IM2P_ERROR;
      if (route_policy(route) == RoutePolicy::legacy) {
        const auto d = stripe_descriptor();
        result = im2p_begin_striped_matmul_v2(sim.get(), &d, &raw);
      } else {
        auto d = provider_stripe_descriptor();
        result = im2p_begin_striped_matmul_v2(sim.get(), &d, &raw);
      }
      if (result != IM2P_OK)
        set_error(from_c_status(result, route, "failed to start IM2P stream",
                                native));
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
          changed.notify_all();
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
            set_error(from_c_status(result, route, "IM2P stripe publish failed",
                                    native));
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
      if (provider_failed)
        set_error(make_status(StatusCode::execution_failure, route, native,
                              "IM2P provider callback failed"));
      else if (result != IM2P_OK)
        set_error(
            from_c_status(result, route, "IM2P stream fence failed", native));
    } else if (provider_failed) {
      set_error(make_status(StatusCode::execution_failure, route, native,
                            "IM2P provider callback failed"));
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
  const RoutePolicy policy = route_policy(x.route);
  if (policy == RoutePolicy::deprecated) {
    x.final_status = make_status(StatusCode::unsupported_route, x.route,
                                 x.native, "q8_h2 is deprecated");
  } else if (policy == RoutePolicy::unsupported) {
    x.final_status = make_status(StatusCode::unsupported_route, x.route,
                                 x.native, "q8_hp2 is unsupported");
  } else if (policy == RoutePolicy::unknown) {
    x.final_status = make_status(StatusCode::unsupported_route, x.route,
                                 x.native, "unknown Gemmini weight route");
  }
  if (!x.final_status.ok()) {
    x.lifecycle = Run::Impl::Lifecycle::terminal;
    return {x.final_status, std::move(run)};
  }
  if (!normalize_tile_count(x.scalars.tile_i, x.scalars.i, x.tile_i_rows) ||
      !normalize_tile_count(x.scalars.tile_j, x.scalars.j, x.tile_j_columns)) {
    x.final_status =
        make_status(StatusCode::invalid_contract, x.route, x.native,
                    "Gemmini tile count overflows element extent");
    x.lifecycle = Run::Impl::Lifecycle::terminal;
    return {x.final_status, std::move(run)};
  }
  const size_t activation_storage_bytes = (x.scalars.activation_bits + 7) / 8;
  size_t minimum_activation_stride = 0;
  const bool activation_layout_ok =
      (x.scalars.activation_bits == 4 || x.scalars.activation_bits == 8 ||
       x.scalars.activation_bits == 16) &&
      x.scalars.activation_bits == IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS &&
      checked_mul(x.scalars.k, activation_storage_bytes,
                  minimum_activation_stride) &&
      x.scalars.activation_row_stride_bytes >= minimum_activation_stride;
  size_t activation_last_row = 0, activation_required_size = 0;
  const bool activation_size_ok =
      x.scalars.i != 0 &&
      checked_mul(x.scalars.i - 1, x.scalars.activation_row_stride_bytes,
                  activation_last_row) &&
      activation_last_row <=
          std::numeric_limits<size_t>::max() - minimum_activation_stride &&
      (activation_required_size =
           activation_last_row + minimum_activation_stride) <=
          x.scalars.activation_raw_size;
  const size_t sb = x.scalars.sb == 0 ? x.scalars.j : x.scalars.sb;
  const size_t sc = x.scalars.sc == 0 ? x.scalars.j : x.scalars.sc;
  if (policy == RoutePolicy::legacy) {
    if (options.max_stalled_cycles == 0 || x.scalars.i == 0 ||
        x.scalars.j == 0 || x.scalars.k == 0 || !x.pointers.a ||
        !x.pointers.b || !x.pointers.c || !activation_layout_ok ||
        !activation_size_ok)
      x.final_status =
          make_status(StatusCode::invalid_argument, x.route, x.native,
                      "missing IM2P operand, dimension, or option");
    else if (x.scalars.transpose_a || x.scalars.transpose_b ||
             x.scalars.weight_i8_scale_active || !x.scalars.full_c ||
             x.scalars.low_d || x.scalars.repeating_bias || x.pointers.d ||
             x.scalars.act != 0 ||
             x.scalars.scale_b != static_cast<scale_t>(1) ||
             x.scalars.scale_d != static_cast<scale_acc_t>(1) ||
             x.scalars.scale != static_cast<acc_scale_t>(1) ||
             x.scalars.bert_scale != static_cast<acc_scale_t>(1))
      x.final_status =
          make_status(StatusCode::unsupported_route, x.route, x.native,
                      "q8_h0 operands, bias semantics, or scalar scales are "
                      "not raw-ABI compatible");
    else if (sb < x.scalars.j || sc < x.scalars.j ||
             (mode == Mode::stripe_pipeline &&
              x.scalars.activation_rows_per_stripe == 0))
      x.final_status =
          make_status(StatusCode::invalid_contract, x.route, x.native,
                      "invalid IM2P stride or stripe layout");
  } else {
    if (options.max_stalled_cycles == 0)
      x.final_status = make_status(StatusCode::invalid_argument, x.route,
                                   x.native, "invalid IM2P option");
    else if (!activation_layout_ok || !activation_size_ok ||
             !provider_route_contract(*args, x.route))
      x.final_status =
          make_status(StatusCode::invalid_contract, x.route, x.native,
                      "invalid native Gemmini route contract");
    else if (mode == Mode::stripe_pipeline &&
             x.scalars.activation_rows_per_stripe == 0)
      x.final_status = make_status(StatusCode::invalid_contract, x.route,
                                   x.native, "invalid native stripe layout");
    else if (mode == Mode::full && !snapshot_activation_scales(
                                       *args, 0, args->I, x.activation_scales))
      x.final_status =
          make_status(StatusCode::invalid_contract, x.route, x.native,
                      "invalid activation scale metadata");
    else if (mode == Mode::stripe_pipeline) {
      if (x.pipeline_exsia) {
        try {
          x.activation_scales.assign(args->I,
                                     std::numeric_limits<float>::quiet_NaN());
        } catch (...) {
          x.final_status =
              make_status(StatusCode::out_of_memory, x.route, x.native,
                          "failed to stage activation metadata");
        }
      } else if (!snapshot_activation_scales(*args, 0, args->I,
                                             x.activation_scales)) {
        x.final_status =
            make_status(StatusCode::invalid_contract, x.route, x.native,
                        "invalid activation scale metadata");
      }
    }
  }
  if (x.final_status.ok()) {
    const bool retained = policy == RoutePolicy::legacy
                              ? x.retain_legacy_operands()
                              : x.retain_provider_operands();
    if (!retained)
      x.final_status = make_status(StatusCode::out_of_memory, x.route, x.native,
                                   "failed to retain IM2P operands");
  }
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
                                          state.route, state.native,
                                          "IM2P worker allocation failed"));
        } catch (...) {
          state.worker_failed(make_status(StatusCode::execution_failure,
                                          state.route, state.native,
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
  return {make_status(StatusCode::success, x.route, x.native, "success"),
          std::move(run)};
}

Status submit_stripe(Run &run, const exsia::StripeReadyEvent &e,
                     StripeMetadata metadata) noexcept {
  auto &x = *run.impl_;
  std::unique_lock lock(x.mutex);
  if (x.lifecycle != Run::Impl::Lifecycle::running ||
      x.mode != Mode::stripe_pipeline)
    return make_status(StatusCode::invalid_state, x.route, x.native,
                       "run is not accepting stripes");
  if (!x.final_status.ok())
    return x.final_status;
  if (e.stripe_id > std::numeric_limits<uint32_t>::max() ||
      e.stripe_id != x.next_stripe || e.row_begin != x.next_row ||
      e.row_begin >= e.row_end || e.row_end > x.scalars.i ||
      (x.bound_run && e.run_id != x.run_id))
    return make_status(StatusCode::invalid_argument, x.route, x.native,
                       "invalid stripe run, order, or bounds");
  const size_t rows = e.row_end - e.row_begin,
               expected = x.scalars.activation_rows_per_stripe;
  if ((e.row_end != x.scalars.i && rows != expected) ||
      (e.row_end == x.scalars.i && rows > expected))
    return make_status(StatusCode::invalid_argument, x.route, x.native,
                       "invalid stripe row count");
  if (x.outstanding >= Run::Impl::producer_slot_count) {
    ++x.blocked_producers;
    x.changed.notify_all();
    x.changed.wait(lock, [&] {
      return x.outstanding < Run::Impl::producer_slot_count ||
             !x.final_status.ok() ||
             x.lifecycle != Run::Impl::Lifecycle::running;
    });
    --x.blocked_producers;
    x.changed.notify_all();
  }
  if (!x.final_status.ok())
    return x.final_status;
  if (x.lifecycle != Run::Impl::Lifecycle::running)
    return make_status(StatusCode::invalid_state, x.route, x.native,
                       "run is not accepting stripes");
  if (route_policy(x.route) == RoutePolicy::provider && x.pipeline_exsia) {
    if (!metadata.has_exsia_theta ||
        metadata.exsia_theta == std::numeric_limits<int16_t>::min())
      return make_status(StatusCode::invalid_contract, x.route, x.native,
                         "missing post-fold ExSIA theta");
    const float scale = std::ldexp(1.0f, metadata.exsia_theta);
    if (!std::isfinite(scale) || scale <= 0.0f)
      return make_status(StatusCode::invalid_contract, x.route, x.native,
                         "invalid post-fold ExSIA theta");
    std::fill(x.activation_scales.begin() + e.row_begin,
              x.activation_scales.begin() + e.row_end, scale);
  }
  Run::Impl::DenseEvent dense{e.run_id,         e.stripe_id, e.slot,
                              e.row_begin,      e.row_end,   e.rmd_packet,
                              e.direct_residual};
  try {
    x.ready.push_back(dense);
  } catch (const std::bad_alloc &) {
    return make_status(StatusCode::out_of_memory, x.route, x.native,
                       "failed to queue stripe metadata");
  } catch (...) {
    return make_status(StatusCode::execution_failure, x.route, x.native,
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
  return make_status(StatusCode::success, x.route, x.native, "success");
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
    if (x.final_status.ok() && x.mode == Mode::full && !x.output_committed) {
      x.commit_output();
      x.output_committed = true;
    }
    x.join_in_progress = false;
    x.lifecycle = Run::Impl::Lifecycle::terminal;
    x.changed.notify_all();
    return {x.final_status, x.stats};
  }
}

PipelineOutputStage acquire_pipeline_output_stage(Run &run) noexcept {
  auto &x = *run.impl_;
  std::lock_guard lock(x.mutex);
  if (x.mode != Mode::stripe_pipeline ||
      x.lifecycle != Run::Impl::Lifecycle::terminal)
    return {make_status(StatusCode::invalid_state, x.route, x.native,
                        "output staging requires a fenced pipeline run"),
            nullptr, 0};
  if (!x.final_status.ok())
    return {x.final_status, nullptr, 0};
  if (x.output_committed || !x.float_output_stage)
    return {make_status(StatusCode::invalid_state, x.route, x.native,
                        "mutable pipeline output staging is unavailable"),
            nullptr, 0};
  return {x.final_status, x.float_output_stage->data(),
          x.float_output_stage->size()};
}

Status authorize_output_commit(Run &run, bool rmd_succeeded) noexcept {
  auto &x = *run.impl_;
  std::lock_guard lock(x.mutex);
  if (x.mode != Mode::stripe_pipeline ||
      x.lifecycle != Run::Impl::Lifecycle::terminal)
    return make_status(StatusCode::invalid_state, x.route, x.native,
                       "output commit requires a fenced pipeline run");
  if (!x.final_status.ok())
    return x.final_status;
  if (!rmd_succeeded) {
    x.final_status = make_status(StatusCode::execution_failure, x.route,
                                 x.native, "RMD completion failed");
    x.changed.notify_all();
    return x.final_status;
  }
  if (!x.output_committed) {
    x.commit_output();
    x.output_committed = true;
  }
  return x.final_status;
}

#if defined(IM2P_GEMMINI_FRONTEND_TESTING)
RunTestAccess::Snapshot RunTestAccess::inspect(const Run &run) noexcept {
  const auto &x = *run.impl_;
  std::lock_guard lock(x.mutex);
  Snapshot view{};
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
  view.activation_bits = x.scalars.activation_bits;
  view.activation_raw_size = x.scalars.activation_raw_size;
  view.activation_row_stride_bytes = x.scalars.activation_row_stride_bytes;
  view.queued = x.ready.size();
  view.in_flight = x.in_flight.size();
  view.outstanding = x.outstanding;
  return view;
}

bool RunTestAccess::wait_for_completion(Run &run, uint64_t target) noexcept {
  auto &x = *run.impl_;
  std::unique_lock lock(x.mutex);
  return x.changed.wait_for(lock, std::chrono::seconds(5), [&] {
    return x.completion_generation >= target || !x.final_status.ok();
  }) && x.completion_generation >= target;
}

bool RunTestAccess::wait_for_closing(Run &run) noexcept {
  auto &x = *run.impl_;
  std::unique_lock lock(x.mutex);
  return x.changed.wait_for(lock, std::chrono::seconds(5), [&] {
    return x.lifecycle == Run::Impl::Lifecycle::closing ||
           x.lifecycle == Run::Impl::Lifecycle::terminal;
  });
}

bool RunTestAccess::wait_for_blocked_submit(Run &run, size_t target) noexcept {
  auto &x = *run.impl_;
  std::unique_lock lock(x.mutex);
  return x.changed.wait_for(lock, std::chrono::seconds(5), [&] {
    return x.blocked_producers >= target || !x.final_status.ok();
  }) && x.blocked_producers >= target;
}

void RunTestAccess::hold_progress(Run &run) noexcept {
  auto &x = *run.impl_;
  std::lock_guard lock(x.mutex);
  x.progress_held = true;
}

void RunTestAccess::inject_execution_failure(Run &run) noexcept {
  auto &x = *run.impl_;
  std::lock_guard lock(x.mutex);
  if (x.final_status.ok())
    x.final_status = make_status(StatusCode::execution_failure, x.route,
                                 x.native, "injected IM2P execution failure");
  x.progress_held = false;
  x.changed.notify_all();
}

void RunTestAccess::inject_progress_failure(Run &run) noexcept {
  auto &x = *run.impl_;
  std::lock_guard lock(x.mutex);
  x.progress_failure_injected = true;
  x.changed.notify_all();
}

void RunTestAccess::inject_poll_failure(Run &run) noexcept {
  auto &x = *run.impl_;
  std::lock_guard lock(x.mutex);
  x.poll_failure_injected = true;
  x.changed.notify_all();
}

void RunTestAccess::enable_completion_gate(Run &run) noexcept {
  auto &x = *run.impl_;
  std::lock_guard lock(x.mutex);
  x.completion_gate_enabled = true;
}

void RunTestAccess::release_completion_gate(Run &run) noexcept {
  auto &x = *run.impl_;
  std::lock_guard lock(x.mutex);
  ++x.completion_gate_permits;
  x.changed.notify_all();
}

void RunTestAccess::disable_completion_gate(Run &run) noexcept {
  auto &x = *run.impl_;
  std::lock_guard lock(x.mutex);
  x.completion_gate_enabled = false;
  x.changed.notify_all();
}
#endif

} // namespace im2p::gemmini
