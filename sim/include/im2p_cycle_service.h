#ifndef IM2P_CYCLE_SERVICE_H
#define IM2P_CYCLE_SERVICE_H
#include "im2p_cycle_model.h"
#ifdef __cplusplus
extern "C" {
#endif
#define IM2P_CYCLE_SERVICE_ABI_VERSION 1u
typedef struct im2p_cycle_service_result {
  uint32_t abi_version;
  uint32_t struct_size;
  uint64_t result_ready_cycle;
  uint64_t final_scale_release_cycle;
  uint64_t resource_ready_cycle;
  uint32_t next_scratchpad_half;
  uint32_t next_accumulator_half;
} im2p_cycle_service_result_t;

/* Drained single-work reference-memory service. Null runs selects dense work.
   Request geometry, accepted epoch and initial halves retain cycle ABI v1.
   Existing result.total_cycles ends at result-ready, NOT resource-ready.
   No queue, persistent multi-work state, host delay or frequency is modeled.
   Both outputs are unchanged on failure. */
int im2p_cycle_estimate_service(im2p_cycle_model_t *model,
    const im2p_cycle_request_t *request, const im2p_compact_runs_t *runs,
    im2p_cycle_result_t *result, im2p_cycle_service_result_t *service);
#ifdef __cplusplus
}
#endif
#endif
