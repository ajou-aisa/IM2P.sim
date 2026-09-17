#ifndef IM2P_RMD_SCU_OBSERVER_H
#define IM2P_RMD_SCU_OBSERVER_H
#include <stdint.h>

/* Passive test-only taps. Bits 0..18 follow rmd_scu_certificate.py's event
 * names. scu_data is sampled from the actual writeback queue input on rawRow,
 * after SCU scaling and before accumulator addition. No signals are driven
 * here.
 */
typedef struct {
  uint64_t cycle;
  uint64_t event_mask;
  uint32_t dim;
  int32_t scu_data[64];
} im2p_rmd_scu_observation_t;
typedef void (*im2p_rmd_scu_observer_fn)(void *,
                                         const im2p_rmd_scu_observation_t *);
#ifdef __cplusplus
extern "C" {
#endif
void im2p_test_set_rmd_scu_observer(im2p_rmd_scu_observer_fn, void *);
#ifdef __cplusplus
}
#endif
#endif
