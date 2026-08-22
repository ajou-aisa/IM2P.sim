#include "im2p_sim.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

enum { MAX_CYCLES = 100000 };

#ifndef IM2P_TEST_ACTIVATION_BITS
#error "IM2P_TEST_ACTIVATION_BITS is required"
#endif
#ifndef IM2P_TEST_DIM
#error "IM2P_TEST_DIM is required"
#endif

#if IM2P_TEST_ACTIVATION_BITS == 4
typedef int8_t activation_t;
#define TEST_ACTIVATION 7
#define TEST_SHIFT 23
#define TEST_EXPECTED INT64_C(7457472512)
#define TEST_STORAGE_BYTES 1
#elif IM2P_TEST_ACTIVATION_BITS == 8
typedef int8_t activation_t;
#define TEST_ACTIVATION 127
#define TEST_SHIFT 18
#define TEST_EXPECTED INT64_C(4228120576)
#define TEST_STORAGE_BYTES 1
#elif IM2P_TEST_ACTIVATION_BITS == 16
typedef int16_t activation_t;
#define TEST_ACTIVATION 32767
#define TEST_SHIFT 11
#define TEST_EXPECTED INT64_C(8522565632)
#define TEST_STORAGE_BYTES 2
#else
#error "unsupported activation width"
#endif

static const int8_t WEIGHTS[2] = {127, -127};
static const activation_t ACTIVATIONS[1] = {TEST_ACTIVATION};
static const int64_t EXPECTED[2] = {TEST_EXPECTED, -TEST_EXPECTED};

typedef struct {
    int64_t output[2];
    size_t weight_reads;
    size_t scale_reads;
    size_t output_writes;
    int fail_output;
} provider_state_t;

static int read_weight(void *context, size_t row, size_t column,
                       size_t count, int8_t *output) {
    provider_state_t *state = context;
    if (row != 0 || column + count > 2) return -1;
    memcpy(output, WEIGHTS + column, count);
    ++state->weight_reads;
    return 0;
}

static int read_scale(void *context, size_t row, size_t column,
                      size_t count, int8_t *output) {
    provider_state_t *state = context;
    if (row != 0 || column + count > 2) return -1;
    memset(output, TEST_SHIFT, count);
    ++state->scale_reads;
    return 0;
}

static int write_output_v2(void *context, size_t block, size_t row,
                           size_t column, size_t count,
                           const int32_t *values) {
    provider_state_t *state = context;
    if (block != 0 || row != 0 || column + count > 2) return -1;
    for (size_t lane = 0; lane < count; ++lane)
        state->output[column + lane] = values[lane];
    ++state->output_writes;
    return state->fail_output ? -1 : 0;
}

static int write_output_v3(void *context, size_t block, size_t row,
                           size_t column, size_t count,
                           const int64_t *values) {
    provider_state_t *state = context;
    if (block != 0 || row != 0 || column + count > 2) return -1;
    memcpy(state->output + column, values, count * sizeof(*values));
    ++state->output_writes;
    return state->fail_output ? -1 : 0;
}

static im2p_provider_t provider_v2(provider_state_t *state) {
    im2p_provider_t provider = {
        .context = state,
        .read_weight = read_weight,
        .read_scale = read_scale,
        .write_output = write_output_v2,
    };
    return provider;
}

static im2p_provider_v3_t provider_v3(provider_state_t *state) {
    im2p_provider_v3_t provider = {
        .context = state,
        .read_weight = read_weight,
        .read_scale = read_scale,
        .write_output = write_output_v3,
    };
    return provider;
}

static im2p_matmul_desc_v2_t full_v2(provider_state_t *state) {
    im2p_matmul_desc_v2_t descriptor = {
        .abi_version = IM2P_ABI_VERSION_2,
        .activation_bits = IM2P_TEST_ACTIVATION_BITS,
        .activation_storage_bytes = TEST_STORAGE_BYTES,
        .dim = IM2P_TEST_DIM,
        .activations = ACTIVATIONS,
        .m = 1, .n = 2, .k = 1,
        .activation_row_stride_bytes = TEST_STORAGE_BYTES,
        .weight_row_stride = 2,
        .output_row_stride = 2,
        .tile_i_rows = 1,
        .tile_j_columns = 2,
        .block_size = 1,
        .vector_op = IM2P_VECTOR_SHIFT,
        .provider = provider_v2(state),
    };
    return descriptor;
}

static im2p_matmul_desc_v3_t full_v3(provider_state_t *state) {
    im2p_matmul_desc_v3_t descriptor = {
        .abi_version = IM2P_ABI_VERSION_3,
        .activation_bits = IM2P_TEST_ACTIVATION_BITS,
        .activation_storage_bytes = TEST_STORAGE_BYTES,
        .dim = IM2P_TEST_DIM,
        .activations = ACTIVATIONS,
        .m = 1, .n = 2, .k = 1,
        .activation_row_stride_bytes = TEST_STORAGE_BYTES,
        .weight_row_stride = 2,
        .output_row_stride = 2,
        .tile_i_rows = 1,
        .tile_j_columns = 2,
        .block_size = 1,
        .vector_op = IM2P_VECTOR_SHIFT,
        .provider = provider_v3(state),
    };
    return descriptor;
}

static im2p_stripe_work_desc_v2_t striped_v2(provider_state_t *state) {
    im2p_stripe_work_desc_v2_t descriptor = {
        .abi_version = IM2P_ABI_VERSION_2,
        .activation_bits = IM2P_TEST_ACTIVATION_BITS,
        .activation_storage_bytes = TEST_STORAGE_BYTES,
        .dim = IM2P_TEST_DIM,
        .m = 1, .n = 2, .k = 1,
        .weight_row_stride = 2,
        .output_row_stride = 2,
        .tile_i_rows = 1,
        .tile_j_columns = 2,
        .block_size = 1,
        .stripe_count = 1,
        .vector_op = IM2P_VECTOR_SHIFT,
        .provider = provider_v2(state),
    };
    return descriptor;
}

static im2p_stripe_work_desc_v3_t striped_v3(provider_state_t *state) {
    im2p_stripe_work_desc_v3_t descriptor = {
        .abi_version = IM2P_ABI_VERSION_3,
        .activation_bits = IM2P_TEST_ACTIVATION_BITS,
        .activation_storage_bytes = TEST_STORAGE_BYTES,
        .dim = IM2P_TEST_DIM,
        .m = 1, .n = 2, .k = 1,
        .weight_row_stride = 2,
        .output_row_stride = 2,
        .tile_i_rows = 1,
        .tile_j_columns = 2,
        .block_size = 1,
        .stripe_count = 1,
        .vector_op = IM2P_VECTOR_SHIFT,
        .provider = provider_v3(state),
    };
    return descriptor;
}

static int exact_output(const provider_state_t *state) {
    return state->output[0] == EXPECTED[0] &&
           state->output[1] == EXPECTED[1] &&
           state->weight_reads != 0 && state->scale_reads != 0 &&
           state->output_writes != 0;
}

static int run_full(im2p_sim_t *sim) {
    provider_state_t v3_state = {0};
    int32_t sentinel[2] = {123, 456};
    im2p_matmul_desc_v3_t v3 = full_v3(&v3_state);
    v3.output = sentinel;
    if (im2p_execute_matmul_v3(sim, &v3, NULL) != IM2P_OK ||
        !exact_output(&v3_state) || sentinel[0] != 123 || sentinel[1] != 456)
        return 10;

    provider_state_t v2_state = {0};
    im2p_matmul_desc_v2_t v2 = full_v2(&v2_state);
    if (im2p_execute_matmul_v2(sim, &v2, NULL) != IM2P_OK ||
        v2_state.output[0] != INT32_MAX || v2_state.output[1] != INT32_MIN)
        return 11;

    provider_state_t failure = {.fail_output = 1};
    v3 = full_v3(&failure);
    if (im2p_execute_matmul_v3(sim, &v3, NULL) != IM2P_ERROR) return 12;
    failure.fail_output = 0;
    if (im2p_execute_matmul_v3(sim, &v3, NULL) != IM2P_OK) return 13;

    printf("FULL exact=[%lld,%lld] V2=[%lld,%lld] statuses=typed\n",
           (long long)v3_state.output[0], (long long)v3_state.output[1],
           (long long)v2_state.output[0], (long long)v2_state.output[1]);
    return 0;
}

static int run_striped(im2p_sim_t *sim) {
    provider_state_t duplicate_state = {0};
    im2p_stripe_work_desc_v3_t duplicate_work = striped_v3(&duplicate_state);
    duplicate_work.m = 2;
    im2p_stream_t *duplicate_stream = NULL;
    if (im2p_begin_striped_matmul_v3(sim, &duplicate_work,
                                     &duplicate_stream) != IM2P_OK ||
        duplicate_stream == NULL) return 20;
    im2p_activation_stripe_v3_t duplicate_stripe = {
        .abi_version = IM2P_ABI_VERSION_3,
        .activation_bits = IM2P_TEST_ACTIVATION_BITS,
        .activation_storage_bytes = TEST_STORAGE_BYTES,
        .dim = IM2P_TEST_DIM,
        .stripe_id = 0,
        .i_start = 0,
        .rows = 1,
        .activations = ACTIVATIONS,
        .activation_row_stride_bytes = TEST_STORAGE_BYTES,
    };
    if (im2p_publish_stripe_v3(duplicate_stream, &duplicate_stripe) !=
            IM2P_OK ||
        im2p_publish_stripe_v3(duplicate_stream, &duplicate_stripe) !=
            IM2P_DUPLICATE_STRIPE) {
        im2p_destroy_stream(duplicate_stream);
        return 21;
    }
    im2p_destroy_stream(duplicate_stream);

    provider_state_t state = {0};
    im2p_stripe_work_desc_v3_t work = striped_v3(&state);
    im2p_stream_t *stream = (im2p_stream_t *)(uintptr_t)1;
    if (im2p_begin_striped_matmul_v3(sim, &work, &stream) != IM2P_OK ||
        stream == NULL) return 20;

    im2p_activation_stripe_v3_t stripe = {
        .abi_version = IM2P_ABI_VERSION_3,
        .activation_bits = IM2P_TEST_ACTIVATION_BITS,
        .activation_storage_bytes = TEST_STORAGE_BYTES,
        .dim = IM2P_TEST_DIM,
        .stripe_id = 0,
        .i_start = 0,
        .rows = 1,
        .activations = ACTIVATIONS,
        .activation_row_stride_bytes = TEST_STORAGE_BYTES,
        .context = 99,
    };
    im2p_activation_stripe_v3_t mixed_v3 = stripe;
    mixed_v3.abi_version = IM2P_ABI_VERSION_2;
    im2p_activation_stripe_v3_t out_of_order = stripe;
    out_of_order.stripe_id = 1;
    if (im2p_publish_stripe_v3(stream, &mixed_v3) !=
            IM2P_CONFIGURATION_MISMATCH ||
        im2p_publish_stripe_v3(stream, &out_of_order) != IM2P_INVALID_LAYOUT ||
        im2p_publish_stripe_v3(stream, &stripe) != IM2P_OK ||
        im2p_publish_stripe_v3(stream, &stripe) != IM2P_LATE_STRIPE) {
        im2p_destroy_stream(stream);
        return 21;
    }
    im2p_activation_stripe_v2_t mixed = {
        .abi_version = IM2P_ABI_VERSION_2,
        .activation_bits = IM2P_TEST_ACTIVATION_BITS,
        .activation_storage_bytes = TEST_STORAGE_BYTES,
        .dim = IM2P_TEST_DIM,
        .stripe_id = 0,
        .rows = 1,
        .activations = ACTIVATIONS,
        .activation_row_stride_bytes = TEST_STORAGE_BYTES,
    };
    if (im2p_publish_stripe_v2(stream, &mixed) !=
        IM2P_CONFIGURATION_MISMATCH) {
        im2p_destroy_stream(stream);
        return 22;
    }

    im2p_stripe_completion_t completion = {0};
    int completed = 0;
    for (int cycle = 0; cycle < MAX_CYCLES && !completed; ++cycle) {
        if (im2p_progress_stream(stream, 1) != IM2P_OK) {
            im2p_destroy_stream(stream);
            return 23;
        }
        completed = im2p_poll_completed(stream, &completion) == 1;
    }
    if (!completed || completion.context != 99 ||
        im2p_finish_stream(stream, NULL) != IM2P_OK || !exact_output(&state)) {
        im2p_destroy_stream(stream);
        return 24;
    }
    im2p_destroy_stream(stream);

    provider_state_t failure = {.fail_output = 1};
    work = striped_v3(&failure);
    stream = NULL;
    if (im2p_begin_striped_matmul_v3(sim, &work, &stream) != IM2P_OK ||
        im2p_publish_stripe_v3(stream, &stripe) != IM2P_OK) {
        im2p_destroy_stream(stream);
        return 25;
    }
    int callback_status = IM2P_OK;
    for (int cycle = 0; cycle < MAX_CYCLES; ++cycle) {
        callback_status = im2p_progress_stream(stream, 1);
        if (callback_status != IM2P_OK) break;
    }
    if (callback_status != IM2P_ERROR ||
        im2p_progress_stream(stream, 1) != IM2P_ERROR ||
        im2p_finish_stream(stream, NULL) != IM2P_ERROR) {
        im2p_destroy_stream(stream);
        return 26;
    }
    im2p_destroy_stream(stream);

    printf("PIPELINE exact=[%lld,%lld] completion=%llu statuses=typed\n",
           (long long)state.output[0], (long long)state.output[1],
           (unsigned long long)completion.context);
    return 0;
}

static int reject_oversized_provider_descriptors(im2p_sim_t *sim) {
    static const int32_t sentinel[2] = {123, 456};
    const size_t oversized_block = (size_t)UINT32_MAX + 1;

    for (int version = 2; version <= 3; ++version) {
        provider_state_t state = {0};
        int32_t output[2] = {sentinel[0], sentinel[1]};
        int status;
        if (version == 2) {
            im2p_matmul_desc_v2_t descriptor = full_v2(&state);
            descriptor.output = output;
            descriptor.output_row_stride = SIZE_MAX;
            status = im2p_execute_matmul_v2(sim, &descriptor, NULL);
            descriptor = full_v2(&state);
            descriptor.output = output;
            descriptor.block_size = oversized_block;
            if (status == IM2P_INVALID_LAYOUT)
                status = im2p_execute_matmul_v2(sim, &descriptor, NULL);
        } else {
            im2p_matmul_desc_v3_t descriptor = full_v3(&state);
            descriptor.output = output;
            descriptor.output_row_stride = SIZE_MAX;
            status = im2p_execute_matmul_v3(sim, &descriptor, NULL);
            descriptor = full_v3(&state);
            descriptor.output = output;
            descriptor.block_size = oversized_block;
            if (status == IM2P_INVALID_LAYOUT)
                status = im2p_execute_matmul_v3(sim, &descriptor, NULL);
        }
        if (status != IM2P_INVALID_LAYOUT || state.weight_reads != 0 ||
            state.scale_reads != 0 || state.output_writes != 0 ||
            output[0] != sentinel[0] || output[1] != sentinel[1]) return 40 + version;
    }

    for (int version = 2; version <= 3; ++version) {
        for (int field = 0; field < 2; ++field) {
            provider_state_t state = {0};
            int32_t output[2] = {sentinel[0], sentinel[1]};
            im2p_stream_t *stream = (im2p_stream_t *)(uintptr_t)1;
            int status;
            if (version == 2) {
                im2p_stripe_work_desc_v2_t descriptor = striped_v2(&state);
                descriptor.output = output;
                if (field == 0) descriptor.output_row_stride = SIZE_MAX;
                else descriptor.block_size = oversized_block;
                status = im2p_begin_striped_matmul_v2(sim, &descriptor, &stream);
            } else {
                im2p_stripe_work_desc_v3_t descriptor = striped_v3(&state);
                descriptor.output = output;
                if (field == 0) descriptor.output_row_stride = SIZE_MAX;
                else descriptor.block_size = oversized_block;
                status = im2p_begin_striped_matmul_v3(sim, &descriptor, &stream);
            }
            if (status != IM2P_INVALID_LAYOUT || stream != NULL ||
                state.weight_reads != 0 || state.scale_reads != 0 ||
                state.output_writes != 0 || output[0] != sentinel[0] ||
                output[1] != sentinel[1]) return 50 + 2 * version + field;
        }
    }

    provider_state_t reuse_v2 = {0};
    im2p_matmul_desc_v2_t valid_v2 = full_v2(&reuse_v2);
    if (im2p_execute_matmul_v2(sim, &valid_v2, NULL) != IM2P_OK ||
        reuse_v2.output[0] != INT32_MAX || reuse_v2.output[1] != INT32_MIN)
        return 60;
    provider_state_t reuse_v3 = {0};
    im2p_matmul_desc_v3_t valid_v3 = full_v3(&reuse_v3);
    if (im2p_execute_matmul_v3(sim, &valid_v3, NULL) != IM2P_OK ||
        !exact_output(&reuse_v3)) return 61;
    return 0;
}

static int reject_mixed(im2p_sim_t *sim) {
    provider_state_t state = {0};
    im2p_matmul_desc_v3_t full = full_v3(&state);
    full.abi_version = IM2P_ABI_VERSION_2;
    if (im2p_execute_matmul_v3(sim, &full, NULL) !=
        IM2P_CONFIGURATION_MISMATCH) return 30;
    full = full_v3(&state);
    full.dim = IM2P_TEST_DIM == 16 ? 32 : 16;
    if (im2p_execute_matmul_v3(sim, &full, NULL) !=
        IM2P_CONFIGURATION_MISMATCH) return 31;

    im2p_stripe_work_desc_v3_t work = striped_v3(&state);
    work.activation_storage_bytes = 2;
    im2p_stream_t *stream = (im2p_stream_t *)(uintptr_t)1;
    if (im2p_begin_striped_matmul_v3(sim, &work, &stream) !=
            IM2P_CONFIGURATION_MISMATCH || stream != NULL) return 32;
    return 0;
}

int main(void) {
    if (im2p_sim_abi_version() != IM2P_ABI_VERSION_3 ||
        im2p_sim_activation_bits() != IM2P_TEST_ACTIVATION_BITS ||
        im2p_sim_activation_storage_bytes() != TEST_STORAGE_BYTES ||
        im2p_sim_dim() != IM2P_TEST_DIM) return 1;
    im2p_sim_t *sim = im2p_sim_create();
    if (sim == NULL) return 2;
    int status = reject_mixed(sim);
    if (status == 0) status = reject_oversized_provider_descriptors(sim);
    if (status == 0) status = run_full(sim);
    if (status == 0) status = run_striped(sim);
    im2p_sim_destroy(sim);
    return status;
}
