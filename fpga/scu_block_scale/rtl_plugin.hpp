#pragma once

// Explicit in-process transport for the same synthesized mkScuPipeline RTL.
// A plugin supplies clocks and bounded memory windows, never a dot-product
// substitute. Descriptor/provider lifetimes follow im2p_sim.h; no simulator
// instance is passed through this interface.
#include "im2p_sim.h"
#include <cstddef>
#include <cstdint>

inline unsigned im2p_scu_stride_log(size_t extent) {
    unsigned value = 4;
    while (value < 31 && (uint64_t(1) << value) < extent) ++value;
    return value;
}

// HostAddress/HostStride are 64 bits; N/K strides have a 5-bit shift encoding.
// Bound the padded External block-plane span without a hardware multiplier.
// This is an address bound, not resident storage or a new reduction boundary.
inline bool im2p_scu_logical_extent_valid(size_t m, size_t n, size_t k, bool external) {
    if (!m || m > UINT32_MAX || !n || n > (uint64_t(1) << 31) ||
        !k || k > (uint64_t(1) << 31)) return false;
    const unsigned kl = im2p_scu_stride_log(k), nl = im2p_scu_stride_log(n);
    const unsigned output_shift = nl + 2 + (external && kl > 5 ? kl - 5 : 0);
    return m <= (UINT64_MAX >> output_shift);
}

inline bool im2p_scu_provider_contract_valid(const im2p_matmul_desc_t &d) {
    if (d.vector_op == IM2P_VECTOR_BYPASS)
        return d.output_domain == IM2P_OUTPUT_LEGACY_FINAL;
    const bool external = d.vector_op == IM2P_VECTOR_EXTERNAL && d.output_domain == IM2P_OUTPUT_LEGACY_BLOCK;
    const bool scu = (d.vector_op == IM2P_VECTOR_UNSIGNED_MULTIPLY || d.vector_op == IM2P_VECTOR_LEFT_SHIFT) &&
                     d.output_domain == IM2P_OUTPUT_SCU_FINAL;
    return (external || scu) && d.block_size == 32 && d.k % 32 == 0 && d.provider.read_scale;
}

inline bool im2p_scu_scale_carrier_valid(uint8_t op, uint32_t value) {
    if (op == IM2P_VECTOR_UNSIGNED_MULTIPLY) return value <= 65790;
    if (op == IM2P_VECTOR_LEFT_SHIFT) return value <= 32767 || value == UINT32_C(0x80000000);
    return op == IM2P_VECTOR_EXTERNAL;
}

struct im2p_scu_rtl_observation_v1 {
    uint64_t first_activation_cycle;
    uint64_t first_activation_published_rows;
    uint64_t activation_refills, weight_refills, scale_refills;
    uint64_t publications, completions;
};

// Optional audit receives only validated RTL output, in canonical block order,
// before the original provider callback. Values are borrowed for this call.
using im2p_scu_rtl_output_observer_v1 = void (*)(void *, size_t block,
    size_t row, size_t column, size_t count, const int64_t *values);

struct im2p_scu_rtl_api_v1 {
    uint32_t struct_size, version, abi_version;
    uint32_t dim, activation_bits, weight_bits, vector_op, output_domain;
    const char *bsv_sha256;
    const char *rtl_sha256;
    void *(*create)();
    void (*destroy)(void *);
    const char *(*error)(void *);
    int (*full)(void *, const im2p_matmul_desc_t *, im2p_work_stats_extended_t *);
    int (*begin)(void *, const im2p_stripe_work_desc_t *, size_t stripe_rows);
    int (*publish)(void *, const im2p_activation_stripe_t *);
    int (*poll)(void *, im2p_stripe_completion_extended_t *);
    int (*finish)(void *, im2p_work_stats_extended_t *);
    int (*release)(void *);
    void (*set_output_observer)(void *, im2p_scu_rtl_output_observer_v1, void *);
    int (*observation)(void *, im2p_scu_rtl_observation_v1 *);
};

using im2p_scu_rtl_get_api_v1_fn = const im2p_scu_rtl_api_v1 *(*)();
extern "C" const im2p_scu_rtl_api_v1 *im2p_scu_rtl_get_api_v1();
