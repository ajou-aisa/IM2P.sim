#include "im2p_sim.h"

#include <stdio.h>

int main(void) {
    im2p_matmul_desc_v3_t valid = {
        .abi_version = IM2P_ABI_VERSION_3,
        .activation_bits = 8,
        .activation_storage_bytes = 1,
        .dim = 16,
    };
    im2p_matmul_desc_v3_t mixed = {
        .abi_version = IM2P_ABI_VERSION_2,
        .activation_bits = 8,
        .activation_storage_bytes = 1,
        .dim = 16,
    };
    im2p_matmul_desc_v3_t malformed = {
        .abi_version = IM2P_ABI_VERSION_3,
        .activation_bits = 99,
        .activation_storage_bytes = 77,
        .dim = 123,
    };

    const int valid_status = im2p_execute_matmul_v3(NULL, &valid, NULL);
    const int null_status = im2p_execute_matmul_v3(NULL, NULL, NULL);
    const int mixed_status = im2p_execute_matmul_v3(NULL, &mixed, NULL);
    const int malformed_status = im2p_execute_matmul_v3(NULL, &malformed, NULL);
    printf("V3 stub valid=%d null=%d mixed=%d malformed=%d\n",
           valid_status, null_status, mixed_status, malformed_status);

    return valid_status == IM2P_ERROR &&
                   null_status == IM2P_INVALID_LAYOUT &&
                   mixed_status == IM2P_CONFIGURATION_MISMATCH &&
                   malformed_status == IM2P_INVALID_LAYOUT
               ? 0
               : 1;
}
