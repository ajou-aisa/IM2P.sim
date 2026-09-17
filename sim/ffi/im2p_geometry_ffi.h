#ifndef IM2P_GEOMETRY_FFI_H
#define IM2P_GEOMETRY_FFI_H

#include "im2p_verilator.h"
#include "../include/im2p_geometry.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Internal additive bridge; no clocks occur in admission/publication. */
int im2p_start_matmul_geometry(
    im2p_handle_t handle, const im2p_matmul_descriptor_t *descriptor,
    const im2p_production_geometry_v1_t *geometry);
int im2p_publish_activation_stripe_geometry(
    im2p_handle_t handle, uint32_t row_begin, uint32_t row_count,
    uint64_t row_stride, const im2p_production_geometry_v1_t *geometry);

#ifdef __cplusplus
}
#endif
#endif
