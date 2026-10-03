#pragma once

#include <gemmini/optrace.hpp>
#include <im2p_sim.h>
#include <limits>
#include <stdexcept>

// Scalar-only projection of a final production descriptor and its companion.
// No pointer/provider is copied, no tile is chosen, and no timing is estimated.
namespace im2p::gemmini::production_trace {
namespace trace = ggml::gemmini::optrace;

inline void require(bool condition, const char *message) {
  if (!condition) throw std::runtime_error(message);
}

template <class Descriptor>
trace::Work common(const Descriptor &d,
                   const im2p_production_geometry_v1_t &g,
                   const std::string &layer) {
  require(d.abi_version == IM2P_ABI_VERSION &&
              g.version == IM2P_PRODUCTION_GEOMETRY_VERSION &&
              g.struct_size == sizeof(g) && d.m == g.m && d.n == g.n &&
              d.k == g.k && d.activation_bits == g.activation_bits &&
              d.weight_bits == g.weight_bits && d.dim == g.dim,
          "optrace: final descriptor/geometry mismatch");
  require(d.output_row_stride <= UINT64_MAX / sizeof(int32_t),
          "optrace: output byte stride overflow");
  trace::Work w;
  w.layer = layer;
  w.activation_bits = d.activation_bits;
  w.weight_bits = d.weight_bits;
  w.dim = d.dim;
  w.m = g.row_count;
  w.n = d.n;
  w.k = d.k;
  w.geometry_m = g.m;
  w.row_begin = g.row_begin;
  w.row_count = g.row_count;
  w.tile_i_count = g.tile_i_count;
  w.tile_j_count = g.tile_j_count;
  w.tile_k_count = g.tile_k_count;
  w.weight_stride_bytes = d.weight_row_stride_bytes;
  w.output_stride_bytes = d.output_row_stride * sizeof(int32_t);
  w.scale_stride_elements = d.scale_row_stride;
  w.block_size = static_cast<uint32_t>(d.block_size);
  w.vector_op = d.vector_op;
  w.output_domain = d.output_domain;
  w.production_geometry_version = g.version;
  w.work_context = d.work_context;
  return w;
}

inline trace::Work full(const im2p_matmul_desc_t &d,
                        const im2p_production_geometry_v1_t &g,
                        const std::string &layer) {
  require(g.scope == IM2P_GEOMETRY_FULL && g.row_begin == 0 &&
              g.row_count == d.m && g.stripe_id == 0,
          "optrace: final FULL geometry mismatch");
  auto w = common(d, g, layer);
  w.activation_stride_bytes = d.activation_row_stride_bytes;
  return w;
}

inline trace::Work stripe(const im2p_stripe_work_desc_t &d,
                          const im2p_activation_stripe_t &s,
                          const im2p_production_geometry_v1_t &g,
                          const std::string &layer, uint64_t slot) {
  require(g.scope == IM2P_GEOMETRY_STRIPE && g.row_begin == s.i_start &&
              g.row_count == s.rows && g.stripe_id == s.stripe_id &&
              s.activation_bits == g.activation_bits &&
              s.weight_bits == g.weight_bits && s.dim == g.dim,
          "optrace: final accepted stripe geometry mismatch");
  auto w = common(d, g, layer);
  w.scope = "stripe";
  w.activation_stride_bytes = s.activation_row_stride_bytes;
  w.stripe_id = s.stripe_id;
  w.host_slot = slot;
  w.work_context = s.context;
  return w;
}
} // namespace im2p::gemmini::production_trace
