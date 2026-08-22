#include "im2p_sim.h"

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

_Static_assert(IM2P_ABI_VERSION_3 == 3, "ABI v3 identity changed");

/* ABI v2 is frozen on supported LP64 hosts. */
_Static_assert(sizeof(im2p_provider_t) == 32, "ABI v2 provider size changed");
_Static_assert(sizeof(im2p_matmul_desc_v2_t) == 208, "ABI v2 full size changed");
_Static_assert(offsetof(im2p_matmul_desc_v2_t, activations) == 16,
               "ABI v2 full activation offset changed");
_Static_assert(offsetof(im2p_matmul_desc_v2_t, provider) == 176,
               "ABI v2 full provider offset changed");
_Static_assert(sizeof(im2p_stripe_work_desc_v2_t) == 200,
               "ABI v2 striped size changed");
_Static_assert(offsetof(im2p_stripe_work_desc_v2_t, provider) == 168,
               "ABI v2 striped provider offset changed");
_Static_assert(sizeof(im2p_activation_stripe_v2_t) == 64,
               "ABI v2 publish size changed");
_Static_assert(offsetof(im2p_activation_stripe_v2_t, activations) == 40,
               "ABI v2 publish activation offset changed");

_Static_assert(sizeof(im2p_provider_v3_t) == sizeof(im2p_provider_t),
               "ABI v3 provider layout changed unexpectedly");
_Static_assert(sizeof(im2p_matmul_desc_v3_t) == sizeof(im2p_matmul_desc_v2_t),
               "ABI v3 full layout changed unexpectedly");
_Static_assert(offsetof(im2p_matmul_desc_v3_t, provider) == 176,
               "ABI v3 full provider offset changed");
_Static_assert(sizeof(im2p_stripe_work_desc_v3_t) ==
                   sizeof(im2p_stripe_work_desc_v2_t),
               "ABI v3 striped layout changed unexpectedly");
_Static_assert(offsetof(im2p_stripe_work_desc_v3_t, provider) == 168,
               "ABI v3 striped provider offset changed");
_Static_assert(sizeof(im2p_activation_stripe_v3_t) ==
                   sizeof(im2p_activation_stripe_v2_t),
               "ABI v3 publish layout changed unexpectedly");
_Static_assert(sizeof(*((im2p_matmul_desc_v3_t *)0)->output) == 4,
               "ABI v3 direct output elements must remain 32-bit");
_Static_assert(sizeof(*((im2p_stripe_work_desc_v3_t *)0)->output) == 4,
               "ABI v3 striped output elements must remain 32-bit");

static int write_output_v3(void *context, size_t block, size_t row,
                           size_t column, size_t count,
                           const int64_t *values) {
    (void)context;
    (void)block;
    (void)row;
    (void)column;
    (void)count;
    (void)values;
    return IM2P_OK;
}

int main(void) {
    im2p_provider_v3_t provider = {0};
    provider.write_output = write_output_v3;
    if (provider.write_output == NULL) {
        return 1;
    }
    printf("ABI=%d V2-full=%zu V2-provider-offset=%zu "
           "V3-raw-output-element-size=%zu V3-provider-element-size=%zu\n",
           IM2P_ABI_VERSION_3, sizeof(im2p_matmul_desc_v2_t),
           offsetof(im2p_matmul_desc_v2_t, provider),
           sizeof(*((im2p_matmul_desc_v3_t *)0)->output), sizeof(int64_t));
    return 0;
}
