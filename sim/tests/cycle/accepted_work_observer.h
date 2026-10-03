#ifndef IM2P_ACCEPTED_WORK_OBSERVER_H
#define IM2P_ACCEPTED_WORK_OBSERVER_H

#include "im2p_geometry.h"
#include <stdint.h>

/* Test-only ABI, compiled only with IM2P_VERILATOR_TEST_HOOKS. Not exported by
 * production builds. Install/uninstall only while all simulator workers are
 * quiescent. The callback must not throw, drive signals, or re-enter the DUT.
 */
enum {
  IM2P_OBSERVE_ADMISSION = 1,
  IM2P_OBSERVE_PUBLICATION = 2,
  IM2P_OBSERVE_ACCEPTED = 3,
  IM2P_OBSERVE_DONE = 4,
  IM2P_OBSERVE_OFFERED = 5,
  IM2P_OBSERVE_RELEASE = 6
};

typedef struct im2p_accepted_work_observation {
  uint64_t event, cycle, explicit_geometry;
  uint64_t work_ready, release_column, release_address, release_generation;
  im2p_production_geometry_v1_t geometry;
  uint64_t source_m, source_n, source_k;
  uint64_t activation_host_stride, weight_host_stride, scale_host_stride,
      output_host_stride;
  uint64_t lowerer_tile_i, lowerer_tile_j, lowerer_tile_k;
  uint64_t stripe_id, stripe_row_begin, stripe_row_count, host_slot;
  /* Offsets are the runtime's mapping metadata associated with this fire.
   * All following descriptor fields are sampled from actual DUT inputs.
   */
  uint64_t i, j, k, rows, columns, reduction, order, fragment_count;
  uint64_t max_i, max_j, max_k, pad_i, pad_j, pad_k;
  uint64_t a_address, b_address, c_address, scale_address;
  uint64_t a_stride, b_stride, c_stride;
  uint64_t scale_base, scale_generation, fragment_base, work_base;
  uint64_t accumulate, final_fragment, first_loop, final_loop, logical_work_id,
      rmd_raw;
  uint64_t start_cycle, done_cycle, elapsed_cycles;
  uint64_t load_requests, load_responses, store_requests, store_responses;
  uint64_t scale_requests, scale_responses;
} im2p_accepted_work_observation_t;

typedef void (*im2p_accepted_work_observer_fn)(
    void *context, const im2p_accepted_work_observation_t *record);
#ifdef __cplusplus
extern "C" {
#endif
void im2p_test_set_work_observer(im2p_accepted_work_observer_fn observer,
                                 void *context);
#ifdef __cplusplus
}
#endif
#endif
