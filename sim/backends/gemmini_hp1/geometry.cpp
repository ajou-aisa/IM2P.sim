#include "runtime.hpp"

namespace im2p::gemmini_hp1 {

bool valid_geometry(const im2p_production_geometry_v1_t &g,
                    const im2p_matmul_descriptor_t &d, std::uint32_t scope) {
  if (g.version != IM2P_PRODUCTION_GEOMETRY_VERSION ||
      g.struct_size != sizeof(g) || g.activation_bits != kOperandBits ||
      g.weight_bits != kOperandBits || g.dim != kDim || g.scope != scope ||
      scope > IM2P_GEOMETRY_STRIPE || g.m != d.row_count ||
      g.n != d.column_count || g.k != d.reduction_count || !g.m || !g.n ||
      !g.k || !g.stripe_rows || g.stripe_rows > UINT32_MAX || !g.tile_i_count ||
      !g.tile_j_count || !g.tile_k_count ||
      g.tile_i_count > UINT16_MAX / kDim ||
      g.tile_j_count > UINT16_MAX / kDim || g.tile_k_count > UINT32_MAX / kDim)
    return false;
  if (scope != IM2P_GEOMETRY_STRIPE)
    return g.row_begin == 0 && g.row_count == g.m && g.stripe_id == 0;
  return g.row_begin < g.m && g.row_count && g.row_count <= g.m - g.row_begin &&
         g.row_count <= g.stripe_rows && g.stripe_id <= UINT32_MAX;
}

bool geometry_fits(const im2p_production_geometry_v1_t &g, std::size_t rows,
                   std::uint8_t slot) {
  if (!rows || slot > 1)
    return false;
  const auto max_i =
      (std::min<std::uint64_t>(rows, g.tile_i_count * kDim) + kDim - 1) / kDim;
  const auto max_j = (std::min(g.n, g.tile_j_count * kDim) + kDim - 1) / kDim;
  const auto max_k = (std::min<std::uint64_t>(g.k, 32) + kDim - 1) / kDim;
  const auto fragments_per_block = std::max<std::size_t>(1, 32 / kDim);
  const auto scale_blocks =
      (max_k + fragments_per_block - 1) / fragments_per_block;
  const auto scale_rows = scale_blocks * max_j;
  // Scale rows are generation-tagged and released after each loop, so physical
  // cache capacity is a per-loop/per-slot constraint rather than an all-K
  // bound. Reject unrepresentable work; never substitute smaller tile factors.
  return max_i * max_j <= 64 &&
         (max_i + max_j) * max_k * kDim <=
             IM2P_GEMMINI_BANK_COUNT * IM2P_GEMMINI_BANK_ROWS / 2 &&
         max_i * max_j * kDim <= IM2P_GEMMINI_ACCUMULATOR_ROWS / 2 &&
         (g.k - 1) / std::min<std::size_t>(kDim, 32) <= UINT16_MAX &&
         slot * 128 + scale_rows <= 256;
}

int start_matmul(Runtime &runtime, const im2p_matmul_descriptor_t &d,
                 const im2p_production_geometry_v1_t *g) {
  if (runtime.active)
    return 0;
  if (!valid_descriptor(d))
    return IM2P_REQUEST_INVALID_ARGUMENT;
  if (g &&
      (!valid_geometry(
           *g, d, d.mode == 1 ? IM2P_GEOMETRY_STREAM : IM2P_GEOMETRY_FULL) ||
       (d.mode == 0 && !geometry_fits(*g, d.row_count, 0))))
    return IM2P_REQUEST_INVALID_ARGUMENT;
  runtime.descriptor = d;
  runtime.explicit_geometry = g != nullptr;
  runtime.geometry = g ? *g : im2p_production_geometry_v1_t{};
  const auto &p = runtime.plan;
  runtime.schedule = {{d.row_count, d.column_count, d.reduction_count},
                      {kDim, kOperandBits},
                      {static_cast<std::size_t>(g ? g->tile_i_count : p.tile_i),
                       static_cast<std::size_t>(g ? g->tile_j_count : p.tile_j),
                       static_cast<std::size_t>(g ? g->tile_k_count : p.tile_k),
                       static_cast<std::size_t>(
                           g ? g->stripe_rows : p.activation_rows_per_stripe)},
                      {d.activation_row_stride, d.weight_row_stride,
                       d.scale_row_stride, d.output_row_stride, d.k_origin},
                      d.vector_op != 0,
                      d.vector_op != 0,
                      d.accumulate_first_fragment != 0};
  runtime.active = true;
  runtime.async = d.mode == 1;
  runtime.matrix_done = false;
  runtime.fault = false;
  runtime.published_rows = 0;
  runtime.next_stripe_id = 0;
  runtime.last_start = runtime.top.io_coreCycle;
  if (!runtime.async) {
    Stripe stripe{
        0, 0, d.row_count, d.activation_row_stride, runtime.top.io_coreCycle,
        0};
    if (g)
      stripe.geometry = *g;
    if (!enqueue_stripe(runtime, stripe))
      return 0;
    runtime.published_rows = d.row_count;
  }
#if defined(IM2P_VERILATOR_TEST_HOOKS)
  observe_geometry(runtime, IM2P_OBSERVE_ADMISSION, g);
#endif
  return 1;
}

int publish_stripe(Runtime &runtime, std::uint32_t row_begin,
                   std::uint32_t row_count, std::uint64_t row_stride,
                   const im2p_production_geometry_v1_t *g) {
  if (!runtime.active || !runtime.async)
    return IM2P_PUBLISH_LATE;
  if (row_begin < runtime.published_rows)
    return IM2P_PUBLISH_DUPLICATE;
  if (row_begin != runtime.published_rows || row_count == 0 ||
      row_count > runtime.descriptor.row_count - row_begin ||
      row_stride < runtime.descriptor.reduction_count * kStorageBytes)
    return IM2P_PUBLISH_INVALID;
  const auto slot = static_cast<std::uint8_t>(runtime.next_stripe_id & 1U);
  if (g) {
    const auto anchor = static_cast<std::uint64_t>(row_begin) *
                        runtime.descriptor.activation_row_stride;
    const auto base = runtime.descriptor.activation_base;
    const auto bytes = runtime.descriptor.reduction_count * kStorageBytes;
    if (anchor > UINT64_MAX - base || bytes - 1 > UINT64_MAX - base - anchor ||
        row_count - 1 > (UINT64_MAX - base - anchor - bytes + 1) / row_stride)
      return IM2P_PUBLISH_INVALID;
  }
  if (runtime.explicit_geometry != (g != nullptr))
    return IM2P_PUBLISH_INVALID;
  if (g && (!valid_geometry(*g, runtime.descriptor, IM2P_GEOMETRY_STRIPE) ||
            g->row_begin != row_begin || g->row_count != row_count ||
            g->stripe_id != runtime.next_stripe_id ||
            g->stripe_rows != runtime.geometry.stripe_rows ||
            (row_begin + row_count < g->m && row_count != g->stripe_rows) ||
            !geometry_fits(*g, row_count, slot)))
    return IM2P_PUBLISH_INVALID;
  Stripe stripe{runtime.next_stripe_id,   row_begin, row_count, row_stride,
                runtime.top.io_coreCycle, slot};
  if (g)
    stripe.geometry = *g;
  if (!enqueue_stripe(runtime, stripe))
    return IM2P_PUBLISH_BACKPRESSURE;
  ++runtime.next_stripe_id;
  runtime.published_rows += row_count;
  ++runtime.counters.stripes_published;
  runtime.counters.stripe_rows_published += row_count;
#if defined(IM2P_VERILATOR_TEST_HOOKS)
  observe_geometry(runtime, IM2P_OBSERVE_PUBLICATION, g);
#endif
  return IM2P_PUBLISH_ACCEPTED;
}

} // namespace im2p::gemmini_hp1

extern "C" int
im2p_start_matmul_geometry(im2p_handle_t handle,
                           const im2p_matmul_descriptor_t *descriptor,
                           const im2p_production_geometry_v1_t *geometry) {
  auto *runtime = im2p::gemmini_hp1::as_runtime(handle);
  if (!runtime || !descriptor || !geometry)
    return IM2P_REQUEST_INVALID_ARGUMENT;
  return im2p::gemmini_hp1::start_matmul(*runtime, *descriptor, geometry);
}

extern "C" int im2p_publish_activation_stripe_geometry(
    im2p_handle_t handle, std::uint32_t row_begin, std::uint32_t row_count,
    std::uint64_t row_stride, const im2p_production_geometry_v1_t *geometry) {
  auto *runtime = im2p::gemmini_hp1::as_runtime(handle);
  if (!runtime || !geometry)
    return IM2P_PUBLISH_INVALID;
  return im2p::gemmini_hp1::publish_stripe(*runtime, row_begin, row_count,
                                           row_stride, geometry);
}
