#include "im2p_sim.h"

#include <stdint.h>
#include <stdio.h>

#ifndef IM2P_TEST_ACTIVATION_BITS
#error "IM2P_TEST_ACTIVATION_BITS is required"
#endif
#ifndef IM2P_TEST_DIM
#error "IM2P_TEST_DIM is required"
#endif

#if IM2P_TEST_ACTIVATION_BITS == 16
typedef int16_t activation_t;
#define IM2P_TEST_STORAGE_BYTES 2
#define IM2P_TEST_INVALID_BYTE_STRIDE 3
#elif IM2P_TEST_ACTIVATION_BITS == 4 || IM2P_TEST_ACTIVATION_BITS == 8
typedef int8_t activation_t;
#define IM2P_TEST_STORAGE_BYTES 1
#define IM2P_TEST_INVALID_BYTE_STRIDE 2
#else
#error "unsupported test activation width"
#endif

static int output_matches_oracle(const int32_t output[4]) {
    return output[0] == 14 && output[1] == -14
        && output[2] == 32 && output[3] == -32;
}

int main(void) {
    static const activation_t activations[8] = {1, 2, 3, 99, 4, 5, 6, 99};
    static const int8_t weights[6] = {1, -1, 2, -2, 3, -3};
    int32_t output[4] = {91, 92, 93, 94};

    if (im2p_sim_abi_version() != IM2P_ABI_VERSION_3
            || im2p_sim_activation_bits() != IM2P_TEST_ACTIVATION_BITS
            || im2p_sim_activation_storage_bytes() != IM2P_TEST_STORAGE_BYTES
            || im2p_sim_dim() != IM2P_TEST_DIM) {
        return 1;
    }

    im2p_sim_t *sim = im2p_sim_create();
    if (sim == NULL) return 2;

    im2p_matmul_desc_v2_t full = {
        .abi_version = IM2P_ABI_VERSION_2,
        .activation_bits = IM2P_TEST_ACTIVATION_BITS,
        .activation_storage_bytes = IM2P_TEST_STORAGE_BYTES,
        .dim = IM2P_TEST_DIM,
        .activations = activations,
        .weights = weights,
        .output = output,
        .m = 2, .n = 2, .k = 3,
        .activation_row_stride_bytes = 4 * IM2P_TEST_STORAGE_BYTES,
        .weight_row_stride = 2,
        .output_row_stride = 2,
        .tile_i_rows = 1,
        .tile_j_columns = 1,
        .block_size = 3,
        .vector_op = IM2P_VECTOR_BYPASS,
    };
    full.activation_row_stride_bytes = IM2P_TEST_INVALID_BYTE_STRIDE;
    if (im2p_execute_matmul_v2(sim, &full, NULL) != IM2P_INVALID_LAYOUT
            || output[0] != 91 || output[1] != 92
            || output[2] != 93 || output[3] != 94) {
        im2p_sim_destroy(sim);
        return 3;
    }
    full.activation_row_stride_bytes = 4 * IM2P_TEST_STORAGE_BYTES;
    full.dim = IM2P_TEST_DIM == 16 ? 32 : 16;
    if (im2p_execute_matmul_v2(sim, &full, NULL)
            != IM2P_CONFIGURATION_MISMATCH
            || output[0] != 91 || output[1] != 92
            || output[2] != 93 || output[3] != 94) {
        im2p_sim_destroy(sim);
        return 4;
    }
    full.dim = IM2P_TEST_DIM;
    if (im2p_execute_matmul_v2(sim, &full, NULL) != IM2P_OK
            || !output_matches_oracle(output)) {
        im2p_sim_destroy(sim);
        return 5;
    }

    output[0] = 91; output[1] = 92; output[2] = 93; output[3] = 94;
    im2p_stripe_work_desc_v2_t work = {
        .abi_version = IM2P_ABI_VERSION_2,
        .activation_bits = IM2P_TEST_ACTIVATION_BITS,
        .activation_storage_bytes = IM2P_TEST_STORAGE_BYTES,
        .dim = IM2P_TEST_DIM,
        .weights = weights,
        .output = output,
        .m = 2, .n = 2, .k = 3,
        .weight_row_stride = 2,
        .output_row_stride = 2,
        .tile_i_rows = 1,
        .tile_j_columns = 1,
        .block_size = 3,
        .stripe_count = 1,
        .vector_op = IM2P_VECTOR_BYPASS,
    };
    im2p_stream_t *stream = NULL;
    if (im2p_begin_striped_matmul_v2(sim, &work, &stream) != IM2P_OK
            || stream == NULL) {
        im2p_sim_destroy(sim);
        return 6;
    }
    im2p_activation_stripe_v2_t stripe = {
        .abi_version = IM2P_ABI_VERSION_2,
        .activation_bits = IM2P_TEST_ACTIVATION_BITS,
        .activation_storage_bytes = IM2P_TEST_STORAGE_BYTES,
        .dim = IM2P_TEST_DIM,
        .stripe_id = 0,
        .i_start = 0,
        .rows = 2,
        .activations = activations,
        .activation_row_stride_bytes = 4 * IM2P_TEST_STORAGE_BYTES,
    };
    if (im2p_publish_stripe_v2(stream, &stripe) != IM2P_OK) {
        im2p_destroy_stream(stream);
        im2p_sim_destroy(sim);
        return 7;
    }
    int completed = 0;
    im2p_stripe_completion_t completion = {0};
    for (uint32_t cycle = 0; cycle < 100000 && !completed; ++cycle) {
        if (im2p_progress_stream(stream, 1) != IM2P_OK) {
            im2p_destroy_stream(stream);
            im2p_sim_destroy(sim);
            return 8;
        }
        completed = im2p_poll_completed(stream, &completion) == 1;
    }
    if (!completed || im2p_finish_stream(stream, NULL) != IM2P_OK
            || !output_matches_oracle(output)) {
        im2p_destroy_stream(stream);
        im2p_sim_destroy(sim);
        return 9;
    }
    im2p_destroy_stream(stream);
    im2p_sim_destroy(sim);
    printf("IM2P C ABI v2: PASS A%d/DIM%d storage=%d stride=%d\n",
        IM2P_TEST_ACTIVATION_BITS, IM2P_TEST_DIM,
        IM2P_TEST_STORAGE_BYTES, 4 * IM2P_TEST_STORAGE_BYTES);
    return 0;
}
