#ifndef IM2P_VERILATOR_H
#define IM2P_VERILATOR_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef void *im2p_handle_t;

im2p_handle_t im2p_create(void);
void im2p_destroy(im2p_handle_t handle);
void im2p_reset(im2p_handle_t handle);
void im2p_tick(im2p_handle_t handle);
uint64_t im2p_cycle_count(im2p_handle_t handle);

int im2p_weights_ready(im2p_handle_t handle);
int im2p_load_weight_ready(im2p_handle_t handle);
int im2p_activation_ready(im2p_handle_t handle);
int im2p_execution_done(im2p_handle_t handle);
int im2p_idle(im2p_handle_t handle);

int im2p_begin_weight_load(im2p_handle_t handle);
int im2p_load_weight_row(im2p_handle_t handle, uint32_t row, const int8_t *values);
int im2p_configure_k_quant(
    im2p_handle_t handle,
    uint32_t block_size,
    uint32_t total_k,
    uint32_t block_count
);
int im2p_scale_load_ready(im2p_handle_t handle);
int im2p_load_scale_block(im2p_handle_t handle, const int8_t *scales);
int im2p_start_execution(
    im2p_handle_t handle,
    uint32_t accumulator_base_row,
    uint32_t row_count,
    int accumulate,
    uint8_t vector_op,
    uint32_t k_start,
    uint32_t k_count
);
int im2p_put_activation_row(
    im2p_handle_t handle,
    const int8_t *values
);
int im2p_acknowledge_execution(im2p_handle_t handle);
int im2p_write_accumulator_row(
    im2p_handle_t handle,
    uint32_t row,
    const int32_t *values
);
int im2p_read_accumulator_row(
    im2p_handle_t handle,
    uint32_t row,
    int32_t *values
);

#ifdef __cplusplus
}
#endif

#endif
