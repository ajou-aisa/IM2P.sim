#include "im2p_cycle_model.h"
#include <stddef.h>
#include <stdio.h>
#include <string.h>

_Static_assert(offsetof(im2p_cycle_request_t, m) == 8,
               "request layout changed");
_Static_assert(sizeof(im2p_cycle_hardware_t) == 48,
               "hardware profile layout changed");
_Static_assert(sizeof(im2p_cycle_request_t) == 120, "request ABI size changed");
_Static_assert(sizeof(im2p_cycle_event_t) == 80, "event ABI size changed");

int main(void) {
  im2p_cycle_model_config_t c;
  im2p_cycle_request_t r;
  im2p_cycle_result_t out;
  im2p_cycle_model_config_init(&c);
  im2p_cycle_request_init(&r);
  c.hardware =
      (im2p_cycle_hardware_t){8, 8, 32, 32, 32, 4, 2048, 512, 32, 128, 4, 2};
  r.m = r.n = 1;
  r.k = 32;
  r.accepted_cycle = 106449;
  im2p_cycle_model_t *model = im2p_cycle_model_create(&c);
  if (!model || im2p_cycle_estimate(model, &r, &out) != IM2P_CYCLE_OK ||
      out.total_cycles != 316 || out.load_request_count != 33 ||
      out.load_response_count != 33 || out.store_request_count != 1 ||
      out.store_response_count != 1 || out.scale_request_count != 1 ||
      out.scale_response_count != 1)
    return 1;
  r.k = 0;
  im2p_cycle_result_t before = out;
  if (im2p_cycle_estimate(model, &r, &out) != IM2P_CYCLE_INVALID ||
      memcmp(&before, &out, sizeof(out)) != 0 ||
      !*im2p_cycle_model_error(model))
    return 1;
  r.k = 32;
  r.record_events = 1;
  if (im2p_cycle_estimate(model, &r, &out) != IM2P_CYCLE_OK ||
      im2p_cycle_model_event_count(model) == 0)
    return 1;
  before = out;
  if (im2p_cycle_estimate(model, NULL, &out) != IM2P_CYCLE_INVALID ||
      im2p_cycle_model_event_count(model) != 0 ||
      memcmp(&before, &out, sizeof(out)) != 0 ||
      !*im2p_cycle_model_error(model))
    return 1;
  if (im2p_cycle_estimate(model, &r, NULL) != IM2P_CYCLE_INVALID ||
      im2p_cycle_model_event_count(model) != 0)
    return 1;
  im2p_cycle_model_destroy(model);
  im2p_cycle_model_destroy(NULL);
  if (im2p_cycle_model_create(NULL) != NULL ||
      im2p_cycle_estimate(NULL, &r, &out) != IM2P_CYCLE_INVALID)
    return 1;
  puts("CYCLE_C_ABI_PASS version=1 layout=exact transactional_failure=1");
  return 0;
}
