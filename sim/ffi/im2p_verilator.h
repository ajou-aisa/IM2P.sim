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

/*
 * Compact view of a pending host read request published by the RTL
 * scheduler. `element_count` is the RDY-guarded lane count for the request
 * and never exceeds the build's DIM.
 */
typedef struct {
    uint64_t tag;
    uint64_t address;
    uint32_t element_count;
} im2p_read_request_t;

/*
 * Pending C (output) write request. `values` receives exactly DIM int32
 * lanes copied out of the accumulator row the RTL is presenting; lanes at or
 * beyond `element_count` are RTL-side padding and carry no meaning.
 */
typedef struct {
    uint64_t tag;
    uint64_t address;
    uint32_t element_count;
} im2p_write_request_t;

typedef struct {
    uint32_t stripe_id;
    uint32_t row_begin;
    uint32_t row_count;
    uint64_t stripe_context;
} im2p_stripe_completion_t;

/*
 * Scalar descriptor for a matmul job. Every field is copied into the model
 * synchronously by im2p_start_matmul; no host matrix pointer is retained.
 * `mode` is 0 for full-matrix and 1 for async-stripe scheduling.
 */
typedef struct {
    uint32_t job_id;
    uint8_t mode;
    uint64_t activation_base;
    uint64_t weight_base;
    uint64_t scale_base;
    uint64_t output_base;
    uint64_t activation_row_stride;
    uint64_t weight_row_stride;
    uint64_t scale_row_stride;
    uint64_t output_row_stride;
    uint32_t row_count;
    uint32_t column_count;
    uint32_t reduction_count;
    uint32_t tile_i_rows;
    uint32_t tile_j_columns;
    uint32_t k_origin;
    uint32_t scale_total_k;
    uint32_t scale_block_size;
    uint64_t scale_context;
    int accumulate_first_fragment;
    uint8_t vector_op;
} im2p_matmul_descriptor_t;

typedef struct {
    uint64_t fragments_completed;
    uint64_t works_completed;
    uint64_t stripes_published;
    uint64_t stripe_rows_published;
    uint64_t activation_read_requests;
    uint64_t weight_read_requests;
    uint64_t scale_read_requests;
    uint64_t output_write_requests;
    uint64_t output_write_responses;
    uint64_t weight_bank_activations;
    uint64_t activation_wait_cycles;
    uint64_t weight_wait_cycles;
    uint64_t output_wait_cycles;
    uint64_t stripe_host_wait_cycles;
    uint64_t compute_cycles;
    uint64_t drain_cycles;
    uint64_t weight_preload_cycles;
    uint64_t activation_overlap_cycles;
    uint64_t weight_overlap_cycles;
    uint64_t scale_overlap_cycles;
    uint64_t overlap_cycles;
    uint64_t cross_stripe_overlap_cycles;
    uint64_t lookahead_prepared;
    uint64_t lookahead_publish_cycle;
    uint64_t lookahead_first_activation_cycle;
    uint64_t lookahead_first_weight_cycle;
    uint64_t lookahead_weight_preload_cycle;
    uint64_t lookahead_weight_requests;
    uint64_t lookahead_weight_reuse_hits;
    uint64_t lookahead_scale_cycle;
    uint64_t lookahead_scale_requests;
    uint64_t lookahead_scale_reuses;
    uint64_t current_stripe_completion_cycle;
    uint64_t lookahead_ready_cycle;
    uint64_t lookahead_start_cycle;
} im2p_matrix_counters_t;

typedef struct {
    uint8_t matmul_scheduler_state;
    uint8_t work_scheduler_state;
    uint8_t matrix_core_state;
    int active_weight_bank;
    int inactive_weight_bank_loading;
    int execution_active;
    uint32_t accepted_rows;
    uint32_t configured_rows;
    uint32_t first_column_issued;
    uint32_t first_column_committed;
    int engine_result_valid;
    int vector_busy;
    int activation_request_valid;
    int weight_request_valid;
    int scale_request_valid;
    int output_request_valid;
    int stripe_host_waiting;
    int lookahead_prepared;
    uint32_t lookahead_stripe_id;
} im2p_matrix_debug_t;

enum {
    IM2P_PUBLISH_BACKPRESSURE = 0,
    IM2P_PUBLISH_ACCEPTED = 1,
    IM2P_PUBLISH_INVALID = -1,
    IM2P_PUBLISH_DUPLICATE = -2,
    IM2P_PUBLISH_LATE = -3,
};

enum {
    IM2P_REQUEST_ABSENT = 0,
    IM2P_REQUEST_PRESENT = 1,
    IM2P_REQUEST_INVALID_ARGUMENT = -1,
    IM2P_REQUEST_IDENTITY_MISMATCH = -2,
};

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

/* Address-driven matmul scheduler surface. */

int im2p_start_matmul(
    im2p_handle_t handle,
    const im2p_matmul_descriptor_t *descriptor
);
int im2p_publish_activation_stripe(
    im2p_handle_t handle,
    uint32_t row_begin,
    uint32_t row_count,
    uint64_t row_stride
);
int im2p_activation_stripe_ready(im2p_handle_t handle);
int im2p_matmul_done(im2p_handle_t handle);
int im2p_acknowledge_matmul(im2p_handle_t handle);

/*
 * Request getters return IM2P_REQUEST_PRESENT and fill `request` when the
 * RTL is presenting a valid request, IM2P_REQUEST_ABSENT when it is not, and
 * IM2P_REQUEST_INVALID_ARGUMENT for null arguments.
 * Response writers return IM2P_REQUEST_IDENTITY_MISMATCH without pulsing RTL
 * when a supplied tag does not match the currently presented request.
 */
int im2p_activation_read_request(
    im2p_handle_t handle,
    im2p_read_request_t *request
);
int im2p_weight_read_request(
    im2p_handle_t handle,
    im2p_read_request_t *request
);
int im2p_scale_read_request(
    im2p_handle_t handle,
    im2p_read_request_t *request
);

/*
 * Response functions pack exactly DIM lanes: the first `count` entries come
 * from `values` and remaining lanes are zero-filled. They return 1 on accept,
 * 0 when the matching RDY is false, and negative on invalid arguments.
 */
int im2p_put_activation_read_response(
    im2p_handle_t handle,
    uint64_t tag,
    const int8_t *values,
    uint32_t count
);
int im2p_put_weight_read_response(
    im2p_handle_t handle,
    uint64_t tag,
    const int8_t *values,
    uint32_t count
);
int im2p_put_scale_read_response(
    im2p_handle_t handle,
    uint64_t tag,
    const int8_t *values,
    uint32_t count
);

/*
 * Copies the pending C write request, including exactly DIM int32 lanes into
 * `values`. `values` must have room for the build's DIM entries.
 */
int im2p_output_write_request(
    im2p_handle_t handle,
    im2p_write_request_t *request,
    int32_t *values
);
int im2p_put_output_write_response(im2p_handle_t handle, uint64_t tag);
int im2p_stripe_completion(
    im2p_handle_t handle,
    im2p_stripe_completion_t *completion
);
int im2p_acknowledge_stripe_completion(im2p_handle_t handle);

void im2p_matrix_counters(
    im2p_handle_t handle,
    im2p_matrix_counters_t *counters
);
void im2p_matrix_debug(im2p_handle_t handle, im2p_matrix_debug_t *debug);

#ifdef __cplusplus
}
#endif

#endif
