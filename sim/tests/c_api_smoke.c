#include "im2p_sim.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

static int expect_output(const int32_t *output) {
    static const int32_t expected[4] = {4, 5, 10, 11};
    return memcmp(output, expected, sizeof(expected)) == 0 ? 0 : -1;
}

static im2p_matmul_desc_t full_desc(
    const int8_t *activations,
    const int8_t *weights,
    int32_t *output
) {
    im2p_matmul_desc_t desc = {0};
    desc.activations = activations;
    desc.weights = weights;
    desc.output = output;
    desc.m = 2;
    desc.n = 2;
    desc.k = 3;
    desc.activation_row_stride = 3;
    desc.weight_row_stride = 2;
    desc.output_row_stride = 2;
    desc.tile_i_rows = 2;
    desc.tile_j_columns = 2;
    desc.block_size = 3;
    desc.vector_op = IM2P_VECTOR_BYPASS;
    desc.work_context = 17;
    return desc;
}

static im2p_stripe_work_desc_t stripe_desc(
    const int8_t *weights,
    int32_t *output
) {
    im2p_stripe_work_desc_t desc = {0};
    desc.weights = weights;
    desc.output = output;
    desc.m = 2;
    desc.n = 2;
    desc.k = 3;
    desc.weight_row_stride = 2;
    desc.output_row_stride = 2;
    desc.tile_i_rows = 2;
    desc.tile_j_columns = 2;
    desc.block_size = 3;
    desc.vector_op = IM2P_VECTOR_BYPASS;
    desc.stripe_count = 1;
    desc.work_context = 18;
    return desc;
}

int main(void) {
    static const int8_t activations[6] = {1, 2, 3, 4, 5, 6};
    static const int8_t weights[6] = {1, 0, 0, 1, 1, 1};
    int32_t output[4] = {0};
    im2p_work_stats_t stats = {0};

    if (im2p_execute_matmul(NULL, NULL, NULL) != IM2P_ERROR
            || im2p_progress_stream(NULL, 1) != IM2P_ERROR) {
        return 8;
    }

    im2p_sim_t *sim = im2p_sim_create();
    if (sim == NULL) {
        return 1;
    }

    im2p_matmul_desc_t matmul = full_desc(activations, weights, output);
    if (im2p_execute_matmul(sim, &matmul, &stats) != IM2P_OK
            || expect_output(output) != 0) {
        im2p_sim_destroy(sim);
        return 2;
    }

    memset(output, 0, sizeof(output));
    im2p_stripe_work_desc_t work = stripe_desc(weights, output);
    im2p_stream_t *stream = im2p_begin_striped_matmul(sim, &work);
    if (stream == NULL) {
        im2p_sim_destroy(sim);
        return 3;
    }

    im2p_activation_stripe_t stripe = {
        .stripe_id = 0,
        .i_start = 0,
        .rows = 2,
        .activations = activations,
        .activation_row_stride = 3,
        .context = 23,
    };
    if (im2p_publish_stripe(stream, &stripe) != IM2P_OK) {
        im2p_destroy_stream(stream);
        im2p_sim_destroy(sim);
        return 4;
    }

    im2p_stripe_completion_t completion = {0};
    int completion_seen = 0;
    for (uint32_t cycle = 0; cycle < 100000 && !completion_seen; ++cycle) {
        if (im2p_progress_stream(stream, 1) != IM2P_OK) {
            return 5;
        }
        int poll = im2p_poll_completed(stream, &completion);
        if (poll < 0) {
            return 6;
        }
        completion_seen = poll == 1;
    }

    int status = im2p_finish_stream(stream, &stats);
    im2p_destroy_stream(stream);
    im2p_sim_destroy(sim);

    if (status != IM2P_OK
            || !completion_seen
            || completion.stripe_id != 0
            || expect_output(output) != 0) {
        return 7;
    }

    puts("IM2P C API: PASS");
    return 0;
}
