#ifndef IM2P_VERILATOR_H
#define IM2P_VERILATOR_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef void *im2p_handle_t;

/*
 * Handles returned by im2p_create remain caller-owned and must stay valid
 * until im2p_destroy. Data pointers are borrowed only for the duration of
 * their synchronous call and are never retained by the bridge.
 */
typedef struct {
    const int8_t *values;
    size_t values_len;
    size_t block_size;
    size_t total_k;
    size_t columns;
    size_t row_stride;
    size_t column_offset;
    size_t valid_columns;
    uint64_t context;
} im2p_scale_matrix_view_t;

typedef struct {
    uint64_t demand_requests;
    uint64_t prefetch_requests;
    uint64_t current_hits;
    uint64_t next_hits;
    uint64_t demand_misses;
    uint64_t rows_received;
    uint64_t wait_cycles;
} im2p_scale_counters_t;

enum {
    IM2P_SCALE_NO_REQUEST = 0,
    IM2P_SCALE_ROW_ACCEPTED = 1,
    IM2P_SCALE_INVALID_VIEW = -1,
    IM2P_SCALE_REQUEST_NOT_READY = -2,
    IM2P_SCALE_CONTEXT_MISMATCH = -3,
    IM2P_SCALE_INVALID_LAYOUT = -4,
    IM2P_SCALE_BLOCK_OUT_OF_RANGE = -5,
    IM2P_SCALE_RESPONSE_NOT_READY = -6,
};

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
int im2p_configure_scaling(im2p_handle_t handle, uint32_t block_size, uint32_t total_k, uint64_t context);
int im2p_service_scale_request(im2p_handle_t handle, const im2p_scale_matrix_view_t *view);
void im2p_scale_counters(im2p_handle_t handle, im2p_scale_counters_t *counters);
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
