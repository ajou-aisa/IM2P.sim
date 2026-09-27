#ifndef IM2P_CYCLE_SEQUENCE_H
#define IM2P_CYCLE_SEQUENCE_H

#include "im2p_cycle_model.h"

#ifdef __cplusplus
extern "C" {
#endif

#define IM2P_CYCLE_SEQUENCE_ABI_VERSION 1u
#define IM2P_CYCLE_SEQUENCE_DOMAIN_ABI_VERSION 2u
#define IM2P_CYCLE_SEQUENCE_ROW_PRESSURE_ABI_VERSION 1u
#define IM2P_CYCLE_SEQUENCE_DOMAIN_CAPACITY 6u

#define IM2P_CYCLE_SEQUENCE_DOMAIN_DMA (1u << 0)
#define IM2P_CYCLE_SEQUENCE_DOMAIN_MEMORY (1u << 1)
#define IM2P_CYCLE_SEQUENCE_DOMAIN_EXECUTE (1u << 2)
#define IM2P_CYCLE_SEQUENCE_DOMAIN_LOOP (1u << 3)
#define IM2P_CYCLE_SEQUENCE_DOMAIN_ARRAY (1u << 4)
#define IM2P_CYCLE_SEQUENCE_DOMAIN_ROWS (1u << 5)
#define IM2P_CYCLE_SEQUENCE_DOMAIN_BANKS (1u << 6)

typedef struct im2p_cycle_sequence im2p_cycle_sequence_t;

typedef enum im2p_cycle_sequence_code {
  IM2P_CYCLE_SEQUENCE_OK = 0,
  IM2P_CYCLE_SEQUENCE_INCOMPLETE = 1,
  IM2P_CYCLE_SEQUENCE_WOULD_BLOCK = 2,
  IM2P_CYCLE_SEQUENCE_INVALID = -1,
  IM2P_CYCLE_SEQUENCE_UNSUPPORTED = -2,
  IM2P_CYCLE_SEQUENCE_OVERFLOW = -3,
  IM2P_CYCLE_SEQUENCE_LIMIT = -4,
  IM2P_CYCLE_SEQUENCE_INTERNAL = -5,
  IM2P_CYCLE_SEQUENCE_FAULTED = -6
} im2p_cycle_sequence_code_t;

typedef enum im2p_cycle_sequence_stop_reason {
  IM2P_CYCLE_SEQUENCE_STOP_NONE = 0,
  IM2P_CYCLE_SEQUENCE_STOP_TARGET = 1,
  IM2P_CYCLE_SEQUENCE_STOP_WORK_BUDGET = 2,
  IM2P_CYCLE_SEQUENCE_STOP_SESSION_BUDGET = 3,
  IM2P_CYCLE_SEQUENCE_STOP_FAULT = 4,
  IM2P_CYCLE_SEQUENCE_STOP_REPORT_BUFFER = 5,
  IM2P_CYCLE_SEQUENCE_STOP_EVENT_BUFFER_AVAILABLE = 6,
  IM2P_CYCLE_SEQUENCE_STOP_EVENT_BURST = 7,
  IM2P_CYCLE_SEQUENCE_STOP_REPORT_AVAILABLE = 8,
  IM2P_CYCLE_SEQUENCE_STOP_SOFT_BUDGET = 9
} im2p_cycle_sequence_stop_reason_t;

typedef struct im2p_cycle_sequence_config {
  uint32_t abi_version;
  uint32_t struct_size;
  im2p_cycle_hardware_t hardware;
  im2p_cycle_timing_t timing;
  uint64_t max_work_cycles;
  uint64_t max_session_cycles;
  uint64_t max_fragments;
  uint64_t max_trace_events;
  uint32_t pending_capacity;
  uint32_t report_capacity;
  uint64_t max_work_ids;
} im2p_cycle_sequence_config_t;

typedef struct im2p_cycle_sequence_descriptor {
  uint32_t abi_version;
  uint32_t struct_size;
  uint64_t logical_work_id;
  uint64_t m, n, k;
  uint64_t tile_i, tile_j, tile_k;
  uint64_t activation_stride_bytes;
  uint64_t weight_stride_bytes;
  uint64_t output_stride_bytes;
  uint64_t scale_stride_elements;
  /* Input view is borrowed only until offer returns; offer owns a deep copy. */
  const im2p_compact_runs_t *compact_runs;
  uint32_t submission;
  uint32_t record_events;
} im2p_cycle_sequence_descriptor_t;

typedef struct im2p_cycle_sequence_status {
  uint32_t abi_version;
  uint32_t struct_size;
  uint64_t generation;
  uint64_t cursor;
  uint64_t session_cycles;
  uint64_t work_cycles;
  uint64_t accepted_work_id;
  uint64_t offered_cycle;
  uint64_t accepted_cycle;
  uint64_t last_discarded_generation;
  uint32_t initialized;
  uint32_t has_pending;
  uint32_t has_active;
  uint32_t has_report;
  uint32_t has_accepted;
  uint32_t faulted;
  uint32_t next_scratchpad_half;
  uint32_t next_accumulator_half;
  uint32_t stop_reason;
} im2p_cycle_sequence_status_t;

/* Read-only mesh tag snapshot at cursor. Enqueue/dequeue counters are
   generation-cumulative; unmapped backpressure is UINT64_MAX with mapped=0. */
typedef struct im2p_cycle_sequence_tag_state {
  uint32_t abi_version;
  uint32_t struct_size;
  uint64_t generation;
  uint64_t cursor;
  uint64_t enqueues;
  uint64_t dequeues;
  uint64_t full_backpressure_cycles;
  uint32_t queue_len;
  uint32_t head_valid;
  uint32_t head_id;
  uint32_t backpressure_mapped;
} im2p_cycle_sequence_tag_state_t;

/* Row-count queue occupancy at cursor and its peak across every committed
   step in this generation, including steps hidden inside advance_until. */
typedef struct im2p_cycle_sequence_row_pressure {
  uint32_t abi_version;
  uint32_t struct_size;
  uint64_t generation;
  uint64_t cursor;
  uint32_t row_count;
  uint32_t max_row_occupancy;
} im2p_cycle_sequence_row_pressure_t;

typedef struct im2p_cycle_sequence_domain_row {
  uint32_t id;
  uint32_t rows;
} im2p_cycle_sequence_domain_row_t;

typedef struct im2p_cycle_sequence_domain_tag {
  uint64_t origin_generation;
  uint64_t origin_ordinal;
  uint64_t origin_work_id;
  uint32_t id;
  uint32_t rows;
  uint32_t rob_valid;
  uint32_t rob_id;
  uint32_t preload_src;
  uint32_t preload_dst;
  uint32_t preload_output_rows;
  uint32_t preload_output_cols;
  uint32_t preload_accumulate;
} im2p_cycle_sequence_domain_tag_t;

typedef struct im2p_cycle_sequence_domain_bank {
  uint32_t pending;
  uint32_t queued;
  /* Bit i is the valid bit of native pipe stage i, i=0..3. */
  uint32_t pipe_valid_mask;
} im2p_cycle_sequence_domain_bank_t;

/* Value-free native state at cursor. Entries [0,count) are FIFO ordered;
   unused entries are zero. Violation bits describe the current state and
   become admission boundary checks when resource_ready=1. Invalid ROB tags
   may remain at a ready boundary. The peak covers every committed engine step
   in this generation, including steps hidden inside advance_until. */
typedef struct im2p_cycle_sequence_domain_snapshot {
  uint32_t abi_version;
  uint32_t struct_size;
  uint64_t generation;
  uint64_t cursor;
  uint32_t resource_ready;
  uint32_t row_count;
  uint32_t tag_count;
  uint32_t max_tag_occupancy;
  uint32_t ready_violation_mask;
  im2p_cycle_sequence_domain_row_t rows[IM2P_CYCLE_SEQUENCE_DOMAIN_CAPACITY];
  im2p_cycle_sequence_domain_tag_t tags[IM2P_CYCLE_SEQUENCE_DOMAIN_CAPACITY];
  im2p_cycle_sequence_domain_bank_t banks[4];
} im2p_cycle_sequence_domain_snapshot_t;

typedef struct im2p_cycle_sequence_report {
  uint32_t abi_version;
  uint32_t struct_size;
  uint64_t generation;
  uint64_t logical_work_id;
  uint64_t offered_cycle;
  uint64_t accepted_cycle;
  uint64_t result_ready_cycle;
  uint64_t final_scale_release_cycle;
  uint64_t resource_ready_cycle;
  uint64_t event_count;
  uint32_t next_scratchpad_half;
  uint32_t next_accumulator_half;
  im2p_cycle_result_t counters;
} im2p_cycle_sequence_report_t;

typedef struct im2p_cycle_sequence_event {
  uint32_t abi_version;
  uint32_t struct_size;
  uint64_t generation;
  uint64_t work_ordinal;
  uint32_t submission;
  uint32_t reserved;
  im2p_cycle_event_t event;
} im2p_cycle_sequence_event_t;

/* Snapshot of a faulted generation. Partial counters describe the active work
   through last_good_cycle; they are not a completed report. */
typedef struct im2p_cycle_sequence_error {
  uint32_t abi_version;
  uint32_t struct_size;
  int32_t code;
  uint32_t stop_reason;
  uint64_t last_good_cycle;
  uint64_t active_work_id;
  uint64_t work_cycles;
  uint32_t has_active;
  uint32_t reserved;
  im2p_cycle_result_t partial_counters;
  char message[128];
} im2p_cycle_sequence_error_t;

void im2p_cycle_sequence_config_init(im2p_cycle_sequence_config_t *config);
void im2p_cycle_sequence_descriptor_init(
    im2p_cycle_sequence_descriptor_t *descriptor);
void im2p_cycle_sequence_status_init(im2p_cycle_sequence_status_t *status);
void im2p_cycle_sequence_tag_state_init(
    im2p_cycle_sequence_tag_state_t *tag_state);
void im2p_cycle_sequence_row_pressure_init(
    im2p_cycle_sequence_row_pressure_t *pressure);
void im2p_cycle_sequence_domain_snapshot_init(
    im2p_cycle_sequence_domain_snapshot_t *snapshot);
void im2p_cycle_sequence_report_init(im2p_cycle_sequence_report_t *report);
void im2p_cycle_sequence_error_init(im2p_cycle_sequence_error_t *error);

/* Creation copies the resolved profile. A generation starts only at reset.
   Calls on the same live handle require external serialization; distinct
   handles are independent. A destroyed handle is an invalid caller pointer. */
int im2p_cycle_sequence_create(const im2p_cycle_sequence_config_t *config,
                               im2p_cycle_sequence_t **sequence);
/* destroy(NULL) is a no-op; passing a destroyed handle is invalid. */
void im2p_cycle_sequence_destroy(im2p_cycle_sequence_t *sequence);
/* Normal reset rejects pending, active or unread-report work. Fault recovery
   discards its incomplete generation and exposes that generation in status. */
int im2p_cycle_sequence_reset(im2p_cycle_sequence_t *sequence);
/* Offer owns the descriptor and compact runs before returning. The offered
   cycle is host availability; physical halves and acceptance are chosen at
   the first ready edge no earlier than that cycle. */
int im2p_cycle_sequence_offer(
    im2p_cycle_sequence_t *sequence,
    const im2p_cycle_sequence_descriptor_t *descriptor, uint64_t offered_cycle);
/* Processes edges [cursor, until_cycle); edge until_cycle remains unprocessed.
   An unread completed report may stop at the resource-ready edge so it can be
   consumed before the next pending work accepts on that edge. */
int im2p_cycle_sequence_advance_until(im2p_cycle_sequence_t *sequence,
                                      uint64_t until_cycle);
/* Uses the same half-open edge interval as advance_until, but returns as soon
   as a completed report is available or max_steps committed edges have run.
   max_steps must be positive. REPORT_AVAILABLE returns OK without processing
   the report's resource-ready edge; SOFT_BUDGET returns INCOMPLETE and is
   resumable. Existing hard limits and event backpressure retain their codes. */
int im2p_cycle_sequence_advance_to_boundary(
    im2p_cycle_sequence_t *sequence, uint64_t until_cycle,
    uint64_t max_steps);
int im2p_cycle_sequence_get_status(const im2p_cycle_sequence_t *sequence,
                                   im2p_cycle_sequence_status_t *status);
int im2p_cycle_sequence_get_tag_state(
    const im2p_cycle_sequence_t *sequence,
    im2p_cycle_sequence_tag_state_t *tag_state);
/* Exact version and struct_size are required; failure leaves output untouched.
   This read-only diagnostic never advances the session cursor. */
int im2p_cycle_sequence_get_row_pressure(
    const im2p_cycle_sequence_t *sequence,
    im2p_cycle_sequence_row_pressure_t *pressure);
/* Exact version and struct_size are required; failure leaves output untouched.
   This is a read-only diagnostic and never advances the session cursor. */
int im2p_cycle_sequence_get_domain_snapshot(
    const im2p_cycle_sequence_t *sequence,
    im2p_cycle_sequence_domain_snapshot_t *snapshot);
/* Returns a versioned, bounded diagnostic snapshot without advancing time.
   A nonfaulted handle reports code=OK and an empty partial diagnostic. */
int im2p_cycle_sequence_get_error(const im2p_cycle_sequence_t *sequence,
                                  im2p_cycle_sequence_error_t *error);
int im2p_cycle_sequence_pop_report(im2p_cycle_sequence_t *sequence,
                                   im2p_cycle_sequence_report_t *report);
/* capacity=0 queries buffered count. Otherwise copies and consumes up to
   capacity events; neither form advances simulated time. */
int im2p_cycle_sequence_read_events(im2p_cycle_sequence_t *sequence,
                                    im2p_cycle_sequence_event_t *events,
                                    uint64_t capacity, uint64_t *count);
int im2p_cycle_sequence_get_cumulative_counters(
    const im2p_cycle_sequence_t *sequence, im2p_cycle_result_t *counters);

#ifdef __cplusplus
}
#endif
#endif
