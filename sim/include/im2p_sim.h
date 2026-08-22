#ifndef IM2P_SIM_H
#define IM2P_SIM_H

/* allow: SIZE_OK - the public C ABI must remain one self-contained header. */

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct im2p_sim im2p_sim_t;
typedef struct im2p_stream im2p_stream_t;

/*
 * Raw handles are single-thread/thread-affine. The thread that starts an
 * operation or stream must perform all progress, poll, finish, and destroy
 * calls for that handle. Callers must externally synchronize ownership; no two
 * operations on the same simulator or stream may execute concurrently.
 */

enum {
  IM2P_OK = 0,
  IM2P_ERROR = -1,
  IM2P_BACKPRESSURE = -2,
  IM2P_UNFINISHED_STREAM = -3,
  IM2P_INVALID_LAYOUT = -4,
  IM2P_DUPLICATE_STRIPE = -5,
  IM2P_LATE_STRIPE = -6,
  IM2P_CONFIGURATION_MISMATCH = -7,
  IM2P_VECTOR_BYPASS = 0,
  IM2P_VECTOR_MULTIPLY = 1,
  IM2P_VECTOR_SHIFT = 2,
  IM2P_VECTOR_EXTERNAL = 3,
  IM2P_ABI_VERSION = 4,
};

/*
 * The public ABI uses the selected activation/weight artifact identity.
 * A4/W4 values occupy one signed byte each; A16/W16 values occupy int16_t.
 * Activation and weight strides are bytes. Raw output storage remains signed
 * 32-bit; provider output transport preserves signed 64-bit accumulator lanes.
 */
typedef int (*im2p_read_weight_i8_fn)(
    void *context, size_t row, size_t column, size_t count, int8_t *out
);
typedef int (*im2p_read_weight_i16_fn)(
    void *context, size_t row, size_t column, size_t count, int16_t *out
);
typedef int (*im2p_read_scale_fn)(
    void *context, size_t row, size_t column, size_t count, int8_t *out
);
typedef int (*im2p_write_output_fn)(
    void *context,
    size_t block,
    size_t row,
    size_t column,
    size_t count,
    const int64_t *values
);

typedef struct {
  void *context;
  im2p_read_weight_i8_fn read_weight_i8;
  im2p_read_weight_i16_fn read_weight_i16;
  im2p_read_scale_fn read_scale;
  im2p_write_output_fn write_output;
} im2p_provider_t;

typedef struct {
  uint32_t abi_version;
  uint32_t activation_bits;
  uint32_t activation_storage_bytes;
  uint32_t weight_bits;
  uint32_t weight_storage_bytes;
  uint32_t dim;
  const void *activations;
  const void *weights;
  const int8_t *scales;
  int32_t *output;
  size_t m;
  size_t n;
  size_t k;
  size_t activation_row_stride_bytes;
  size_t weight_row_stride_bytes;
  size_t output_row_stride;
  size_t tile_i_rows;
  size_t tile_j_columns;
  size_t block_size;
  size_t scale_total_k;
  size_t scale_row_stride;
  size_t scale_column_offset;
  size_t scale_valid_columns;
  size_t scale_values_len;
  uint8_t vector_op;
  uint64_t work_context;
  im2p_provider_t provider;
} im2p_matmul_desc_t;

typedef struct {
  uint32_t abi_version;
  uint32_t activation_bits;
  uint32_t activation_storage_bytes;
  uint32_t weight_bits;
  uint32_t weight_storage_bytes;
  uint32_t dim;
  const void *weights;
  const int8_t *scales;
  int32_t *output;
  size_t m;
  size_t n;
  size_t k;
  size_t weight_row_stride_bytes;
  size_t output_row_stride;
  size_t tile_i_rows;
  size_t tile_j_columns;
  size_t block_size;
  size_t scale_total_k;
  size_t scale_row_stride;
  size_t scale_column_offset;
  size_t scale_valid_columns;
  size_t scale_values_len;
  size_t stripe_count;
  uint8_t vector_op;
  uint64_t work_context;
  im2p_provider_t provider;
} im2p_stripe_work_desc_t;

/*
 * `activations` remains caller-owned and must remain readable until matching
 * completion is returned. A successful publish permits RTL reads on the next
 * logical cycle, so the pointer must remain valid through that completion.
 * Published stripes must be contiguous and ordered.
 */
typedef struct {
  uint32_t abi_version;
  uint32_t activation_bits;
  uint32_t activation_storage_bytes;
  uint32_t weight_bits;
  uint32_t weight_storage_bytes;
  uint32_t dim;
  uint32_t stripe_id;
  size_t i_start;
  size_t rows;
  const void *activations;
  size_t activation_row_stride_bytes;
  uint64_t context;
} im2p_activation_stripe_t;

typedef struct {
    uint32_t stripe_id;
    size_t i_start;
    size_t rows;
    uint64_t context;
} im2p_stripe_completion_t;

typedef struct {
    uint64_t work_total_cycles;
    uint64_t activation_read_requests;
    uint64_t weight_read_requests;
    uint64_t scale_read_requests;
    uint64_t output_write_requests;
    uint64_t output_write_responses;
    uint64_t activation_wait_cycles;
    uint64_t weight_wait_cycles;
    uint64_t scale_wait_cycles;
    uint64_t output_wait_cycles;
    uint64_t stripe_host_wait_cycles;
    uint64_t drain_cycles;
    uint64_t weight_preload_cycles;
    uint64_t same_block_scale_hits;
    uint64_t next_scale_hits;
    uint64_t scale_demand_misses;
    uint64_t compute_cycles;
    uint64_t overlap_cycles;
    uint64_t activation_overlap_cycles;
    uint64_t weight_overlap_cycles;
    uint64_t scale_overlap_cycles;
    uint64_t completed_fragments;
    uint64_t completed_output_tiles;
    uint64_t completed_stripes;
    uint64_t stripes_published;
    uint64_t stripe_rows_published;
    uint64_t weight_bank_activations;
} im2p_work_stats_t;

/* Extended entry points add lookahead telemetry to the base statistics. */
typedef struct {
    im2p_work_stats_t base;
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
} im2p_work_stats_extended_t;

im2p_sim_t *im2p_sim_create(void);
void im2p_sim_destroy(im2p_sim_t *sim);
uint32_t im2p_sim_abi_version(void);
uint32_t im2p_sim_activation_bits(void);
uint32_t im2p_sim_activation_storage_bytes(void);
uint32_t im2p_sim_weight_bits(void);
uint32_t im2p_sim_weight_storage_bytes(void);
uint32_t im2p_sim_dim(void);
int im2p_execute_matmul(
    im2p_sim_t *sim,
    const im2p_matmul_desc_t *descriptor,
    im2p_work_stats_t *stats
);
int im2p_execute_matmul_extended(
    im2p_sim_t *sim,
    const im2p_matmul_desc_t *descriptor,
    im2p_work_stats_extended_t *stats
);
/* The returned stream remains valid if `sim` is destroyed. */
int im2p_begin_striped_matmul(
    im2p_sim_t *sim,
    const im2p_stripe_work_desc_t *descriptor,
    im2p_stream_t **stream
);
int im2p_publish_stripe(
    im2p_stream_t *stream,
    const im2p_activation_stripe_t *stripe
);
/*
 * Advances exactly cycle_budget logical RTL clock periods. A zero budget only
 * observes host-visible state; it does not evaluate a clock edge.
 */
int im2p_progress_stream(im2p_stream_t *stream, uint64_t cycle_budget);
uint64_t im2p_stream_cycle_count(const im2p_stream_t *stream);
/*
 * Returns completed RTL K fragments for this stream. The count wraps at
 * UINT64_MAX; compare for change rather than ordering.
 */
uint64_t im2p_stream_progress_count(const im2p_stream_t *stream);
int im2p_poll_completed(
    im2p_stream_t *stream,
    im2p_stripe_completion_t *completion
);
int im2p_finish_stream(im2p_stream_t *stream, im2p_work_stats_t *stats);
int im2p_finish_stream_extended(
    im2p_stream_t *stream,
    im2p_work_stats_extended_t *stats
);
void im2p_destroy_stream(im2p_stream_t *stream);

#ifdef __cplusplus
}
#endif

#endif
