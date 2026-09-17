#ifndef IM2P_CYCLE_MODEL_H
#define IM2P_CYCLE_MODEL_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define IM2P_CYCLE_MODEL_ABI_VERSION 1u

typedef struct im2p_cycle_model im2p_cycle_model_t;

typedef enum im2p_cycle_status {
  IM2P_CYCLE_OK = 0,
  IM2P_CYCLE_INVALID = 1,
  IM2P_CYCLE_UNSUPPORTED = 2,
  IM2P_CYCLE_OVERFLOW = 3,
  IM2P_CYCLE_LIMIT = 4,
  IM2P_CYCLE_INTERNAL = 5
} im2p_cycle_status_t;

typedef enum im2p_cycle_submission {
  /* One hardware submission per existing planner LoopPlan. */
  IM2P_CYCLE_BLOCK_SUBMISSIONS = 0,
  /* Coalesce consecutive planned blocks within the requested hardware tile K.
     DIM64 retains separate K32 submissions. The independent RTL fixture uses
     this framing, which differs from the production C ABI runtime. */
  IM2P_CYCLE_TILE_SUBMISSIONS = 1
} im2p_cycle_submission_t;

typedef struct im2p_cycle_hardware {
  uint32_t activation_bits;
  uint32_t weight_bits;
  uint32_t dim;
  uint32_t block_k;
  uint32_t accumulator_bits;
  uint32_t bank_count;
  uint32_t bank_rows;
  uint32_t accumulator_rows;
  uint32_t scratchpad_row_bytes;
  uint32_t accumulator_row_bytes;
  uint32_t scratchpad_read_delay;
  uint32_t accumulator_latency;
} im2p_cycle_hardware_t;

typedef struct im2p_cycle_timing {
  /* rtl-regression revision 1: current Adapter::step protocol. */
  uint32_t revision;
  uint32_t backing_read_delay;
  uint32_t even_read_id_delay;
  uint32_t scale_read_extra_delay;
  uint32_t backing_write_delay;
  uint32_t
      read_ready_period; /* 0 = always ready, otherwise stall at phase 0. */
  uint32_t
      backing_cycle_offset; /* Harness clock minus reset-excluded RTL clock. */
  uint32_t reserved;
} im2p_cycle_timing_t;

typedef struct im2p_cycle_model_config {
  uint32_t abi_version;
  uint32_t struct_size;
  im2p_cycle_hardware_t hardware;
  im2p_cycle_timing_t timing;
  /* Software admission limits, NOT hardware queue sizes or timing constants. */
  uint64_t max_cycles;
  uint64_t max_fragments;
  uint64_t max_trace_events;
} im2p_cycle_model_config_t;

typedef struct im2p_cycle_request {
  uint32_t abi_version;
  uint32_t struct_size;
  uint64_t m, n, k;
  uint64_t tile_i, tile_j, tile_k;
  /* Zero derives the minimum canonical host stride; A4 host elements are bytes.
     Backing rows are packed by the existing planner/adapter, not host pointers.
   */
  uint64_t activation_stride_bytes;
  uint64_t weight_stride_bytes;
  uint64_t output_stride_bytes;
  uint64_t scale_stride_elements;
  uint64_t accepted_cycle;
  uint64_t logical_work_id;
  uint32_t submission;
  uint32_t record_events;
  uint32_t initial_scratchpad_half;
  uint32_t initial_accumulator_half;
} im2p_cycle_request_t;

typedef struct im2p_cycle_result {
  uint32_t abi_version;
  uint32_t struct_size;
  uint64_t start_cycle, done_cycle, total_cycles;
  uint64_t logical_work_count;
  uint64_t loop_count;
  uint64_t planner_loop_count;
  uint64_t fragment_count;
  uint64_t load_request_count, load_response_count;
  uint64_t store_request_count, store_response_count;
  uint64_t scale_request_count, scale_response_count;
  uint64_t event_count;
} im2p_cycle_result_t;

typedef struct im2p_cycle_event {
  uint64_t id;
  uint64_t cycle;
  uint64_t logical_work_id;
  /* ABI v1 name retained: serialized hardware submission/frame index, not a
     pure planner-loop ordinal. JSON diagnostics also expose
     submission_index. */
  uint64_t loop;
  uint64_t fragment;
  uint64_t dependency;
  uint64_t detail;
  uint32_t type;
  uint32_t resource;
  uint32_t tile_i, tile_j, tile_k;
  uint32_t reserved;
} im2p_cycle_event_t;

/* Initialize version/size, timing defaults and software limits. Hardware fields
   must be populated from the existing resolved hardware profile. */
void im2p_cycle_model_config_init(im2p_cycle_model_config_t *config);
void im2p_cycle_request_init(im2p_cycle_request_t *request);
im2p_cycle_model_t *
im2p_cycle_model_create(const im2p_cycle_model_config_t *config);
void im2p_cycle_model_destroy(im2p_cycle_model_t *model);
/* No numerical pointers, outputs, numerical runtime flags, frequency or time
   units. Results are estimates until independently certified for a
   case/profile. The result is written only on success. Calls on distinct
   handles are isolated. Calls on the same handle require external
   serialization. */
int im2p_cycle_estimate(im2p_cycle_model_t *model,
                        const im2p_cycle_request_t *request,
                        im2p_cycle_result_t *result);
const char *im2p_cycle_model_error(const im2p_cycle_model_t *model);
uint64_t im2p_cycle_model_event_count(const im2p_cycle_model_t *model);
int im2p_cycle_model_event(const im2p_cycle_model_t *model, uint64_t index,
                           im2p_cycle_event_t *event);
const char *im2p_cycle_event_name(uint32_t type);
const char *im2p_cycle_resource_name(uint32_t resource);

#ifdef __cplusplus
}
#endif
#endif
