#include "im2p_sim.h"

#include <stddef.h>
#include <stdint.h>

_Static_assert(IM2P_ABI_VERSION == 4, "ABI identity changed");
_Static_assert(sizeof(im2p_work_stats_t) == 27 * sizeof(uint64_t),
               "base stats layout changed");
_Static_assert(sizeof(im2p_work_stats_extended_t) == 41 * sizeof(uint64_t),
               "extended stats layout changed");
_Static_assert(
    offsetof(im2p_work_stats_extended_t, cross_stripe_overlap_cycles) ==
        sizeof(im2p_work_stats_t),
    "extended stats prefix changed");
_Static_assert(
    offsetof(im2p_work_stats_extended_t, lookahead_start_cycle) ==
        40 * sizeof(uint64_t),
    "extended stats tail changed");
_Static_assert(IM2P_VECTOR_EXTERNAL == 3, "vector encoding changed");
_Static_assert(sizeof(im2p_provider_t) == 40, "provider layout changed");
_Static_assert(offsetof(im2p_matmul_desc_t, activations) == 24,
               "full descriptor identity prefix changed");
_Static_assert(offsetof(im2p_matmul_desc_t, provider) == 184,
               "full descriptor provider offset changed");
_Static_assert(sizeof(im2p_matmul_desc_t) == 224,
               "full descriptor size changed");
_Static_assert(offsetof(im2p_stripe_work_desc_t, weights) == 24,
               "stripe descriptor identity prefix changed");
_Static_assert(offsetof(im2p_stripe_work_desc_t, provider) == 176,
               "stripe descriptor provider offset changed");
_Static_assert(sizeof(im2p_stripe_work_desc_t) == 216,
               "stripe descriptor size changed");
_Static_assert(sizeof(im2p_activation_stripe_t) == 72,
               "activation stripe layout changed");
_Static_assert(sizeof(im2p_stripe_completion_t) == 32,
               "legacy completion size changed");
_Static_assert(offsetof(im2p_stripe_completion_t, stripe_id) == 0,
               "legacy completion stripe offset changed");
_Static_assert(offsetof(im2p_stripe_completion_t, i_start) == 8,
               "legacy completion row offset changed");
_Static_assert(offsetof(im2p_stripe_completion_t, rows) == 16,
               "legacy completion count offset changed");
_Static_assert(offsetof(im2p_stripe_completion_t, context) == 24,
               "legacy completion context offset changed");
_Static_assert(offsetof(im2p_stripe_completion_extended_t, base) == 0,
               "extended completion base must start at offset zero");
_Static_assert(offsetof(im2p_stripe_completion_extended_t, publish_cycle) == 32,
               "extended completion publish offset changed");
_Static_assert(offsetof(im2p_stripe_completion_extended_t, completion_cycle) == 40,
               "extended completion endpoint offset changed");
_Static_assert(
    offsetof(im2p_stripe_completion_extended_t,
             publish_to_completion_cycles) == 48,
    "extended completion duration offset changed");
_Static_assert(sizeof(im2p_stripe_completion_extended_t) == 56,
               "extended completion size changed");

typedef int (*poll_completed_fn)(im2p_stream_t *,
                                 im2p_stripe_completion_t *);
typedef int (*poll_completed_extended_fn)(
    im2p_stream_t *, im2p_stripe_completion_extended_t *);
_Static_assert(
    _Generic(&im2p_poll_completed, poll_completed_fn: 1, default: 0),
    "legacy poll signature changed");
_Static_assert(_Generic(&im2p_poll_completed_extended,
                        poll_completed_extended_fn: 1, default: 0),
               "extended poll signature changed");

static int typed_i8(void *context, size_t row, size_t column, size_t count,
                    int8_t *out) {
  (void)context; (void)row; (void)column; (void)count; (void)out;
  return 0;
}
static int typed_i16(void *context, size_t row, size_t column, size_t count,
                     int16_t *out) {
  (void)context; (void)row; (void)column; (void)count; (void)out;
  return 0;
}

int main(void) {
  im2p_provider_t provider = {0};
  im2p_stripe_work_desc_t striped = {0};
  im2p_activation_stripe_t activation = {0};
  provider.read_weight_i8 = typed_i8;
  provider.read_weight_i16 = typed_i16;
  return provider.read_weight_i8 == NULL || provider.read_weight_i16 == NULL ||
         striped.provider.context != NULL || activation.activations != NULL;
}
