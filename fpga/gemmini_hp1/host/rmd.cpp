#include "rmd.hpp"

#include "ggml-gemmini-args.h"
#include "quants/common/hp1_scu.hpp"
#include <gemmini.h>

#include <algorithm>
#include <bit>
#include <limits>
#include <new>
#include <stdexcept>

namespace im2p::gemmini_hp1 {

static int execute_descriptor(void *opaque,
                              const im2p_matmul_desc_t *descriptor,
                              const im2p_production_geometry_v1_t *selected,
                              im2p_work_stats_extended_t *stats) {
  const bool scaled = selected != nullptr;
  if (!opaque || !descriptor)
    return IM2P_INVALID_LAYOUT;
  auto &executor = *static_cast<RmdExecutorContext *>(opaque);
  const auto &capability = executor.capability;
  const auto &d = *descriptor;
  if ((scaled ? !executor.execute_scu : !executor.execute) || !capability.rmd ||
      !capability.ws || !capability.hp1_shift_only ||
      capability.accumulator_bits != 32 || capability.block_size != 32 ||
      capability.dim != DIM ||
      capability.activation_bits != GGML_GEMMINI_ACTIVATION_BITS ||
      capability.weight_bits != GGML_GEMMINI_WEIGHT_BITS ||
      capability.bank_count != BANK_NUM || capability.bank_rows != BANK_ROWS ||
      capability.accumulator_rows != ACC_ROWS ||
      capability.scratchpad_row_bytes != DIM * GGML_GEMMINI_WEIGHT_BITS / 8 ||
      capability.accumulator_row_bytes != DIM * sizeof(std::int32_t) ||
      capability.packing != (GGML_GEMMINI_WEIGHT_BITS == 4
                                 ? Packing::signed_int4_low_nibble_first
                                 : Packing::signed_int8) ||
      d.abi_version != IM2P_ABI_VERSION || d.dim != capability.dim ||
      d.activation_bits != capability.activation_bits ||
      d.weight_bits != capability.weight_bits ||
      d.activation_bits != d.weight_bits ||
      (d.activation_bits != 4 && d.activation_bits != 8) ||
      d.activation_storage_bytes != 1 || d.weight_storage_bytes != 1 ||
      d.vector_op != (scaled ? IM2P_VECTOR_LEFT_SHIFT : IM2P_VECTOR_BYPASS) ||
      d.output_domain !=
          (scaled ? IM2P_OUTPUT_SCU_FINAL : IM2P_OUTPUT_LEGACY_FINAL) ||
      d.block_size != (scaled ? 32 : 1) || !d.activations ||
      !d.provider.read_weight_i8 || !d.provider.write_output ||
      (scaled ? (!d.provider.read_scale || d.scale_total_k != d.k ||
                 d.scale_valid_columns != d.n || d.scale_row_stride != d.n)
              : (d.scales || d.provider.read_scale)) ||
      d.m == 0 || d.n == 0 || d.k == 0 || d.k > 32 ||
      d.activation_row_stride_bytes < d.k || d.weight_row_stride_bytes < d.n ||
      d.output_row_stride < d.n || executor.host_slot > 1 ||
      executor.next_work_id == std::numeric_limits<std::uint32_t>::max() ||
      d.m > UINT32_MAX || d.n > UINT32_MAX ||
      d.m > std::numeric_limits<std::size_t>::max() / d.n ||
      d.m > std::numeric_limits<std::size_t>::max() /
                d.activation_row_stride_bytes ||
      d.n > std::numeric_limits<std::size_t>::max() / d.k)
    return IM2P_INVALID_LAYOUT;

  try {
    RmdScuWork work;
    if (scaled) {
      const auto &g = *selected;
      if (g.version != IM2P_PRODUCTION_GEOMETRY_VERSION ||
          g.struct_size != sizeof(g) ||
          g.activation_bits != d.activation_bits ||
          g.weight_bits != d.weight_bits || g.dim != d.dim ||
          g.scope != IM2P_GEOMETRY_FULL || g.m != d.m || g.n != d.n ||
          g.k != d.k || !g.tile_i_count || !g.tile_j_count || !g.tile_k_count ||
          g.tile_i_count > UINT16_MAX / DIM ||
          g.tile_j_count > UINT16_MAX / DIM ||
          g.tile_k_count > UINT32_MAX / DIM || g.stripe_rows != d.m ||
          g.row_begin || g.row_count != d.m || g.stripe_id ||
          d.work_context > UINT32_MAX)
        return IM2P_INVALID_LAYOUT;
      work.geometry = g;
      work.original_block_id = static_cast<std::uint32_t>(d.work_context);
      work.plan = {d.m,
                   d.n,
                   d.k,
                   g.tile_i_count,
                   g.tile_j_count,
                   g.tile_k_count,
                   d.m,
                   Mode::full,
                   WorkKind::dense_hp1_final};
      work.carriers.resize(d.n);
      if (d.provider.read_scale(d.provider.context, 0, 0, d.n,
                                work.carriers.data()) != IM2P_OK)
        return IM2P_ERROR;
      if (!std::all_of(work.carriers.begin(), work.carriers.end(),
                       ggml::gemmini::quants::hp1::valid_carrier))
        return IM2P_INVALID_LAYOUT;
    } else {
      // Historical raw-mode diagnostic only. Production supplies its geometry.
      ggml_gemmini_args_t geometry{};
      geometry.I = d.m;
      geometry.J = d.n;
      geometry.K = d.k;
      ggml::gemmini::gemmini_set_tile_ws(&geometry);
      work.plan = {d.m,
                   d.n,
                   d.k,
                   geometry.tile_I,
                   geometry.tile_J,
                   geometry.tile_K,
                   d.m,
                   Mode::full,
                   WorkKind::rmd_raw};
    }
    work.host_slot = executor.host_slot;
    (void)fragment_work(capability, work.plan);
    work.activations.resize(d.m * d.k);
    work.weights.resize(d.k * d.n);
    const auto *activations = static_cast<const std::int8_t *>(d.activations);
    for (std::size_t row = 0; row < d.m; ++row) {
      std::copy_n(activations + row * d.activation_row_stride_bytes, d.k,
                  work.activations.data() + row * d.k);
    }
    for (std::size_t k = 0; k < d.k; ++k) {
      if (d.provider.read_weight_i8(d.provider.context, k, 0, d.n,
                                    work.weights.data() + k * d.n) != IM2P_OK)
        return IM2P_ERROR;
    }
    const int magnitude = 1 << (d.activation_bits - 1);
    const auto valid_code = [magnitude](std::int8_t code) {
      return code >= -magnitude && code < magnitude;
    };
    if (!std::all_of(work.activations.begin(), work.activations.end(),
                     valid_code) ||
        !std::all_of(work.weights.begin(), work.weights.end(), valid_code))
      return IM2P_INVALID_LAYOUT;
    std::vector<std::int32_t> raw;
    std::uint64_t cycles = 0;
    work.work_id = executor.next_work_id++;
    const int status =
        scaled ? executor.execute_scu(executor.context, work, raw, cycles)
               : executor.execute(executor.context, work, raw, cycles);
    if (status != IM2P_OK)
      return status;
    const std::int64_t bound = std::int64_t{magnitude} * magnitude * d.k;
    if (raw.size() != d.m * d.n || cycles == 0 ||
        (!scaled &&
         !std::all_of(raw.begin(), raw.end(), [bound](std::int32_t value) {
           return value >= -bound && value <= bound;
         })))
      return IM2P_ERROR;

    std::vector<std::int64_t> row(d.n);
    for (std::size_t i = 0; i < d.m; ++i) {
      std::copy_n(raw.data() + i * d.n, d.n, row.begin());
      if (d.provider.write_output(d.provider.context, 0, i, 0, d.n, row.data(),
                                  d.output_domain) != IM2P_OK)
        return IM2P_ERROR;
    }
    if (stats) {
      *stats = {};
      stats->base.work_total_cycles = cycles;
      stats->base.output_write_requests = d.m;
      stats->base.output_write_responses = d.m;
      stats->base.completed_output_tiles =
          ((d.m + DIM - 1) / DIM) * ((d.n + DIM - 1) / DIM);
      stats->base.completed_fragments =
          (d.k + std::min<std::size_t>(DIM, 32) - 1) /
          std::min<std::size_t>(DIM, 32);
    }
    return IM2P_OK;
  } catch (const std::bad_alloc &) {
    return IM2P_ERROR;
  } catch (const std::exception &) {
    return IM2P_INVALID_LAYOUT;
  }
}

int execute_rmd_raw_descriptor(void *opaque, const im2p_matmul_desc_t *d,
                               im2p_work_stats_extended_t *stats) {
  return execute_descriptor(opaque, d, nullptr, stats);
}

int execute_rmd_scu_descriptor(void *opaque, const im2p_matmul_desc_t *d,
                               const im2p_production_geometry_v1_t *geometry,
                               im2p_work_stats_extended_t *stats) {
  if (!geometry)
    return IM2P_INVALID_LAYOUT;
  return execute_descriptor(opaque, d, geometry, stats);
}

int execute_rmd_runs_descriptor(void *opaque, const im2p_matmul_desc_t *d,
                                const im2p_production_geometry_v1_t *geometry,
                                const im2p_compact_runs_t *runs,
                                im2p_work_stats_extended_t *stats) {
  if (!opaque || !d || !geometry || !runs)
    return IM2P_INVALID_LAYOUT;
  auto &executor = *static_cast<RmdExecutorContext *>(opaque);
  const auto &capability = executor.capability;
  const auto &g = *geometry;
  if (!executor.execute_runs || !capability.rmd || !capability.ws ||
      !capability.hp1_shift_only || capability.accumulator_bits != 32 ||
      capability.block_size != 32 || capability.dim != DIM ||
      capability.activation_bits != GGML_GEMMINI_ACTIVATION_BITS ||
      capability.weight_bits != GGML_GEMMINI_WEIGHT_BITS ||
      capability.bank_count != BANK_NUM || capability.bank_rows != BANK_ROWS ||
      capability.accumulator_rows != ACC_ROWS ||
      capability.scratchpad_row_bytes != DIM * GGML_GEMMINI_WEIGHT_BITS / 8 ||
      capability.accumulator_row_bytes != DIM * sizeof(std::int32_t) ||
      capability.packing != (GGML_GEMMINI_WEIGHT_BITS == 4
                                 ? Packing::signed_int4_low_nibble_first
                                 : Packing::signed_int8) ||
      d->abi_version != IM2P_ABI_VERSION || d->dim != capability.dim ||
      d->activation_bits != capability.activation_bits ||
      d->weight_bits != capability.weight_bits ||
      d->activation_bits != d->weight_bits ||
      (d->activation_bits != 4 && d->activation_bits != 8) ||
      d->activation_storage_bytes != 1 || d->weight_storage_bytes != 1 ||
      d->vector_op != IM2P_VECTOR_LEFT_SHIFT ||
      d->output_domain != IM2P_OUTPUT_SCU_FINAL || d->block_size != 32 ||
      !d->activations || !d->provider.read_weight_i8 ||
      !d->provider.read_scale || !d->provider.write_output || !d->m || !d->n ||
      !d->k || d->activation_row_stride_bytes < d->k ||
      d->weight_row_stride_bytes < d->n || d->output_row_stride < d->n ||
      d->scale_total_k != runs->original_k ||
      d->scale_row_stride != d->n || d->scale_column_offset != 0 ||
      d->scale_valid_columns != d->n || executor.host_slot > 1 ||
      executor.next_work_id == std::numeric_limits<std::uint32_t>::max() ||
      d->m > UINT32_MAX || d->n > UINT32_MAX || d->k > UINT32_MAX ||
      d->m > std::numeric_limits<std::size_t>::max() / d->n ||
      d->m > std::numeric_limits<std::size_t>::max() /
                 d->activation_row_stride_bytes ||
      d->n > std::numeric_limits<std::size_t>::max() / d->k ||
      runs->version != IM2P_COMPACT_RUNS_VERSION ||
      runs->struct_size != sizeof(*runs) || !runs->original_k ||
      !runs->run_count || !runs->runs || runs->run_count > d->k ||
      runs->run_count >
          std::numeric_limits<std::size_t>::max() / sizeof(*runs->runs) ||
      runs->run_count > std::numeric_limits<std::size_t>::max() / d->n ||
      d->scale_values_len != runs->run_count * d->n ||
      g.version != IM2P_PRODUCTION_GEOMETRY_VERSION ||
      g.struct_size != sizeof(g) || g.activation_bits != d->activation_bits ||
      g.weight_bits != d->weight_bits || g.dim != d->dim ||
      g.scope != IM2P_GEOMETRY_FULL || g.m != d->m || g.n != d->n ||
      g.k != d->k || !g.tile_i_count || !g.tile_j_count || !g.tile_k_count ||
      g.tile_i_count > UINT16_MAX / DIM ||
      g.tile_j_count > UINT16_MAX / DIM ||
      g.tile_k_count > UINT32_MAX / DIM || g.stripe_rows != d->m ||
      g.row_begin || g.row_count != d->m || g.stripe_id)
    return IM2P_INVALID_LAYOUT;

  std::uint64_t compact_end = 0;
  std::uint32_t previous_block = 0;
  const auto fragments_per_block =
      std::max<std::uint32_t>(1, capability.block_size / capability.dim);
  for (std::size_t ordinal = 0; ordinal < runs->run_count; ++ordinal) {
    const auto &run = runs->runs[ordinal];
    if (!run.compact_k_count || run.compact_k_count > capability.block_size ||
        run.compact_k_begin != compact_end ||
        (ordinal && run.original_block_id <= previous_block) ||
        run.original_block_id > UINT16_MAX / fragments_per_block ||
        static_cast<std::uint32_t>(std::popcount(run.original_k_mask)) !=
            run.compact_k_count)
      return IM2P_INVALID_LAYOUT;
    for (unsigned bit = 0; bit < capability.block_size; ++bit) {
      if ((run.original_k_mask & (std::uint32_t{1} << bit)) &&
          std::uint64_t{run.original_block_id} * capability.block_size + bit >=
              runs->original_k)
        return IM2P_INVALID_LAYOUT;
    }
    compact_end += run.compact_k_count;
    if (compact_end > d->k || compact_end > UINT32_MAX)
      return IM2P_INVALID_LAYOUT;
    previous_block = run.original_block_id;
  }
  if (compact_end != d->k)
    return IM2P_INVALID_LAYOUT;

  try {
    RmdRunWork work;
    work.geometry = g;
    work.original_k = runs->original_k;
    work.plan = {d->m,
                 d->n,
                 d->k,
                 g.tile_i_count,
                 g.tile_j_count,
                 g.tile_k_count,
                 d->m,
                 Mode::full,
                 WorkKind::dense_hp1_final};
    work.host_slot = executor.host_slot;
    work.runs.assign(runs->runs, runs->runs + runs->run_count);
    work.activations.resize(d->m * d->k);
    work.weights.resize(d->k * d->n);
    work.carriers.resize(runs->run_count * d->n);
    (void)fragment_work(capability, work.plan);
    const auto *activations = static_cast<const std::int8_t *>(d->activations);
    for (std::size_t row = 0; row < d->m; ++row)
      std::copy_n(activations + row * d->activation_row_stride_bytes, d->k,
                  work.activations.data() + row * d->k);
    for (std::size_t k = 0; k < d->k; ++k) {
      if (d->provider.read_weight_i8(d->provider.context, k, 0, d->n,
                                     work.weights.data() + k * d->n) != IM2P_OK)
        return IM2P_ERROR;
    }
    for (std::size_t ordinal = 0; ordinal < runs->run_count; ++ordinal) {
      if (d->provider.read_scale(d->provider.context, ordinal, 0, d->n,
                                 work.carriers.data() + ordinal * d->n) !=
          IM2P_OK)
        return IM2P_ERROR;
    }
    if (!std::all_of(work.carriers.begin(), work.carriers.end(),
                     ggml::gemmini::quants::hp1::valid_carrier))
      return IM2P_INVALID_LAYOUT;
    const int magnitude = 1 << (d->activation_bits - 1);
    const auto valid_code = [magnitude](std::int8_t code) {
      return code >= -magnitude && code < magnitude;
    };
    if (!std::all_of(work.activations.begin(), work.activations.end(),
                     valid_code) ||
        !std::all_of(work.weights.begin(), work.weights.end(), valid_code))
      return IM2P_INVALID_LAYOUT;

    std::vector<std::int32_t> raw;
    std::uint64_t cycles = 0;
    work.work_id = executor.next_work_id++;
    const int status =
        executor.execute_runs(executor.context, work, raw, cycles);
    if (status != IM2P_OK)
      return status;
    if (raw.size() != d->m * d->n || !cycles)
      return IM2P_ERROR;
    const std::vector<std::int64_t> output(raw.begin(), raw.end());
    if (d->provider.write_output(d->provider.context, 0, 0, 0, output.size(),
                                 output.data(), d->output_domain) != IM2P_OK)
      return IM2P_ERROR;
    if (stats) {
      *stats = {};
      stats->base.work_total_cycles = cycles;
      stats->base.output_write_requests = 1;
      stats->base.output_write_responses = 1;
      stats->base.completed_output_tiles =
          ((d->m + DIM - 1) / DIM) * ((d->n + DIM - 1) / DIM);
      const auto fragment_k = std::min<std::size_t>(DIM, 32);
      for (const auto &run : work.runs)
        stats->base.completed_fragments +=
            (run.compact_k_count + fragment_k - 1) / fragment_k;
    }
    return IM2P_OK;
  } catch (const std::bad_alloc &) {
    return IM2P_ERROR;
  } catch (const std::exception &) {
    return IM2P_INVALID_LAYOUT;
  }
}

ggml::gemmini::rmd::RmdStatus
execute_rmd_packet(RmdExecutorContext &context, const ggml_gemmini_args_t &args,
                   const ggml::gemmini::rmd::StripePacket &packet,
                   ggml::gemmini::rmd::Correction &correction,
                   ggml::gemmini::rmd::RmdExecutionMetrics *metrics) {
  namespace rmd = ggml::gemmini::rmd;
  using Format = ggml_gemmini_args_t::im2p_weight_format_t;
  if (!context.capability.rmd || !context.execute_scu ||
      (args.weight_format != Format::q4_hp1 &&
       args.weight_format != Format::q8_hp1))
    return rmd::RmdStatus::unsupported_route;
  const rmd::Im2pFullExecutor executor{
      &context, nullptr, execute_rmd_scu_descriptor,
      context.execute_runs ? execute_rmd_runs_descriptor : nullptr};
  return rmd::execute_rmd_stripe_im2p(nullptr, args, packet, correction,
                                      metrics, &executor);
}

} // namespace im2p::gemmini_hp1
