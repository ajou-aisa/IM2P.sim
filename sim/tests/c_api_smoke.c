#include "im2p_sim.h"

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

_Static_assert(
    sizeof(im2p_work_stats_t) == 27 * sizeof(uint64_t),
    "legacy stats ABI size changed"
);
_Static_assert(
    sizeof(im2p_work_stats_extended_t) == 41 * sizeof(uint64_t),
    "extended stats ABI size changed"
);
_Static_assert(
    offsetof(im2p_work_stats_extended_t, cross_stripe_overlap_cycles)
        == sizeof(im2p_work_stats_t),
    "extended stats prefix changed"
);
_Static_assert(
    offsetof(im2p_work_stats_extended_t, lookahead_start_cycle)
        == 40 * sizeof(uint64_t),
    "extended stats tail changed"
);
_Static_assert(IM2P_VECTOR_EXTERNAL == 3, "external vector ABI encoding changed");

typedef struct {
    const int8_t *weights;
    int32_t output[4];
    size_t weight_reads;
    size_t scale_reads;
    size_t output_writes;
    int fail_weight;
} callback_state_t;

static int callback_read_weight(
    void *context, size_t row, size_t column, size_t count, int8_t *out
) {
    callback_state_t *state = context;
    if (state->fail_weight) return -1;
    memcpy(out, state->weights + row * 2 + column, count);
    ++state->weight_reads;
    return 0;
}

static int callback_read_scale(
    void *context, size_t row, size_t column, size_t count, int8_t *out
) {
    callback_state_t *state = context;
    (void)row;
    (void)column;
    memset(out, 1, count);
    ++state->scale_reads;
    return 0;
}

static int callback_write_output(
    void *context, size_t block, size_t row, size_t column,
    size_t count, const int32_t *values
) {
    callback_state_t *state = context;
    if (block != 0 || row >= 2 || column + count > 2) return -1;
    memcpy(state->output + row * 2 + column, values, count * sizeof(*values));
    ++state->output_writes;
    return 0;
}

static im2p_provider_t callback_provider(callback_state_t *state) {
    im2p_provider_t provider = {0};
    provider.context = state;
    provider.read_weight = callback_read_weight;
    provider.read_scale = callback_read_scale;
    provider.write_output = callback_write_output;
    return provider;
}

static int expect_output(const int32_t *output, size_t stride) {
    static const int32_t expected[4] = {4, 5, 10, 11};
    return output[0] == expected[0] && output[1] == expected[1]
        && output[stride] == expected[2] && output[stride + 1] == expected[3]
        ? 0 : -1;
}

static int run_v2_configuration_smoke(uint32_t activation_bits,
                                      uint32_t activation_storage_bytes,
                                      uint32_t dim) {
  static const uint16_t zero_activations[8] = {0};
  static const int8_t weights[6] = {1, -1, 2, -2, 3, -3};
  int32_t output[4] = {91, 92, 93, 94};
  im2p_sim_t *sim = im2p_sim_create();
  if (sim == NULL)
    return 40;

  im2p_matmul_desc_v2_t full = {
      .abi_version = IM2P_ABI_VERSION_2,
      .activation_bits = activation_bits,
      .activation_storage_bytes = activation_storage_bytes,
      .dim = dim,
      .activations = zero_activations,
      .weights = weights,
      .output = output,
      .m = 2,
      .n = 2,
      .k = 3,
      .activation_row_stride_bytes = 4 * activation_storage_bytes,
      .weight_row_stride = 2,
      .output_row_stride = 2,
      .tile_i_rows = 1,
      .tile_j_columns = 1,
      .block_size = 3,
      .vector_op = IM2P_VECTOR_BYPASS,
  };
  if (activation_storage_bytes == 2) {
    full.activation_row_stride_bytes = 3;
    if (im2p_execute_matmul_v2(sim, &full, NULL) != IM2P_INVALID_LAYOUT ||
        output[0] != 91 || output[1] != 92 || output[2] != 93 ||
        output[3] != 94) {
      im2p_sim_destroy(sim);
      return 41;
    }
    full.activation_row_stride_bytes = 8;
  }
  if (im2p_execute_matmul_v2(sim, &full, NULL) != IM2P_OK || output[0] != 0 ||
      output[1] != 0 || output[2] != 0 || output[3] != 0) {
    im2p_sim_destroy(sim);
    return 42;
  }

  output[0] = 91;
  output[1] = 92;
  output[2] = 93;
  output[3] = 94;
  im2p_stripe_work_desc_v2_t work = {
      .abi_version = IM2P_ABI_VERSION_2,
      .activation_bits = activation_bits,
      .activation_storage_bytes = activation_storage_bytes,
      .dim = dim,
      .weights = weights,
      .output = output,
      .m = 2,
      .n = 2,
      .k = 3,
      .weight_row_stride = 2,
      .output_row_stride = 2,
      .tile_i_rows = 1,
      .tile_j_columns = 1,
      .block_size = 3,
      .stripe_count = 1,
      .vector_op = IM2P_VECTOR_BYPASS,
  };
  im2p_stream_t *stream = NULL;
  if (im2p_begin_striped_matmul_v2(sim, &work, &stream) != IM2P_OK ||
      stream == NULL) {
    im2p_sim_destroy(sim);
    return 43;
  }
  im2p_activation_stripe_v2_t stripe = {
      .abi_version = IM2P_ABI_VERSION_2,
      .activation_bits = activation_bits,
      .activation_storage_bytes = activation_storage_bytes,
      .dim = dim,
      .stripe_id = 0,
      .i_start = 0,
      .rows = 2,
      .activations = zero_activations,
      .activation_row_stride_bytes = 4 * activation_storage_bytes,
  };
  if (im2p_publish_stripe_v2(stream, &stripe) != IM2P_OK) {
    im2p_destroy_stream(stream);
    im2p_sim_destroy(sim);
    return 44;
  }
  int completed = 0;
  im2p_stripe_completion_t completion = {0};
  for (uint32_t cycle = 0; cycle < 100000 && !completed; ++cycle) {
    if (im2p_progress_stream(stream, 1) != IM2P_OK) {
      im2p_destroy_stream(stream);
      im2p_sim_destroy(sim);
      return 45;
    }
    completed = im2p_poll_completed(stream, &completion) == 1;
  }
  if (!completed ||
      im2p_publish_stripe_v2(stream, &stripe) != IM2P_LATE_STRIPE) {
    im2p_destroy_stream(stream);
    im2p_sim_destroy(sim);
    return 46;
  }
  int status = im2p_finish_stream(stream, NULL);
  im2p_destroy_stream(stream);
  im2p_sim_destroy(sim);
  if (status != IM2P_OK || output[0] != 0 || output[1] != 0 || output[2] != 0 ||
      output[3] != 0) {
    return 47;
  }
  printf("IM2P C API v2: PASS A%u/DIM%u storage=%u stride=%u\n",
         activation_bits, dim, activation_storage_bytes,
         4 * activation_storage_bytes);
  return 0;
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
    desc.tile_i_rows = 1;
    desc.tile_j_columns = 1;
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
    desc.tile_i_rows = 1;
    desc.tile_j_columns = 1;
    desc.block_size = 3;
    desc.vector_op = IM2P_VECTOR_BYPASS;
    desc.stripe_count = 1;
    desc.work_context = 18;
    return desc;
}

int main(void) {
    static const int8_t activations[6] = {1, 2, 3, 4, 5, 6};
    static const int8_t weights[6] = {1, 0, 0, 1, 1, 1};
    static const int8_t padded_weights[15] = {
        1, 0, 99, 99, 99, 0, 1, 99, 99, 99, 1, 1, 99, 99, 99
    };
    int32_t output[8] = {0};
    im2p_work_stats_t stats = {0};

    uint32_t configured_bits = im2p_sim_activation_bits();
    uint32_t configured_storage_bytes = im2p_sim_activation_storage_bytes();
    uint32_t configured_dim = im2p_sim_dim();
    if (im2p_sim_abi_version() != IM2P_ABI_VERSION_3 ||
        (configured_bits != 4 && configured_bits != 8 &&
         configured_bits != 16) ||
        configured_storage_bytes != (configured_bits == 16 ? 2u : 1u) ||
        (configured_dim != 16 && configured_dim != 32 &&
         configured_dim != 64)) {
      return 31;
    }
    if (configured_bits != 8 || configured_dim != 16) {
      return run_v2_configuration_smoke(
          configured_bits, configured_storage_bytes, configured_dim);
    }

    if (im2p_execute_matmul(NULL, NULL, NULL) != IM2P_ERROR
            || im2p_progress_stream(NULL, 1) != IM2P_ERROR) {
        return 8;
    }

    im2p_sim_t *sim = im2p_sim_create();
    if (sim == NULL) {
        return 1;
    }

    int32_t v2_sentinel[4] = {INT32_C(0x5a5a5a5a), INT32_C(0x5a5a5a5a),
                              INT32_C(0x5a5a5a5a), INT32_C(0x5a5a5a5a)};
    im2p_matmul_desc_v2_t v2 = {
        .abi_version = IM2P_ABI_VERSION_2,
        .activation_bits = 8,
        .activation_storage_bytes = 1,
        .dim = 16,
        .activations = activations,
        .weights = weights,
        .output = v2_sentinel,
        .m = 2,
        .n = 2,
        .k = 3,
        .activation_row_stride_bytes = 3,
        .weight_row_stride = 2,
        .output_row_stride = 2,
        .tile_i_rows = 1,
        .tile_j_columns = 1,
        .block_size = 3,
        .vector_op = IM2P_VECTOR_BYPASS,
        .work_context = 31,
    };
    const uint32_t wrong_values[4][4] = {
        {IM2P_ABI_VERSION_2 + 1, 8, 1, 16},
        {IM2P_ABI_VERSION_2, 4, 1, 16},
        {IM2P_ABI_VERSION_2, 8, 2, 16},
        {IM2P_ABI_VERSION_2, 8, 1, 32},
    };
    for (size_t mismatch = 0; mismatch < 4; ++mismatch) {
      v2.abi_version = wrong_values[mismatch][0];
      v2.activation_bits = wrong_values[mismatch][1];
      v2.activation_storage_bytes = wrong_values[mismatch][2];
      v2.dim = wrong_values[mismatch][3];
      if (im2p_execute_matmul_v2(sim, &v2, NULL) !=
          IM2P_CONFIGURATION_MISMATCH) {
        im2p_sim_destroy(sim);
        return 32;
      }
      for (size_t i = 0; i < 4; ++i) {
        if (v2_sentinel[i] != INT32_C(0x5a5a5a5a)) {
          im2p_sim_destroy(sim);
          return 33;
        }
      }
    }
    puts("IM2P C ABI v2 mismatch status/sentinel: PASS");
    v2.abi_version = IM2P_ABI_VERSION_2;
    v2.activation_bits = 8;
    v2.activation_storage_bytes = 1;
    v2.dim = 16;
    if (im2p_execute_matmul_v2(sim, &v2, &stats) != IM2P_OK ||
        expect_output(v2_sentinel, 2) != 0) {
      im2p_sim_destroy(sim);
      return 34;
    }

    for (size_t i = 0; i < 4; ++i)
      v2_sentinel[i] = INT32_C(0x5a5a5a5a);
    im2p_stripe_work_desc_v2_t v2_work = {
        .abi_version = IM2P_ABI_VERSION_2,
        .activation_bits = 8,
        .activation_storage_bytes = 1,
        .dim = 16,
        .weights = weights,
        .output = v2_sentinel,
        .m = 2,
        .n = 2,
        .k = 3,
        .weight_row_stride = 2,
        .output_row_stride = 2,
        .tile_i_rows = 1,
        .tile_j_columns = 1,
        .block_size = 3,
        .stripe_count = 1,
        .vector_op = IM2P_VECTOR_BYPASS,
        .work_context = 32,
    };
    im2p_stream_t *v2_stream = (im2p_stream_t *)(uintptr_t)1;
    v2_work.activation_bits = 16;
    if (im2p_begin_striped_matmul_v2(sim, &v2_work, &v2_stream) !=
            IM2P_CONFIGURATION_MISMATCH ||
        v2_stream != NULL) {
      im2p_sim_destroy(sim);
      return 35;
    }
    v2_work.activation_bits = 8;
    if (im2p_begin_striped_matmul_v2(sim, &v2_work, &v2_stream) != IM2P_OK ||
        v2_stream == NULL) {
      im2p_sim_destroy(sim);
      return 36;
    }
    im2p_activation_stripe_v2_t v2_stripe = {
        .abi_version = IM2P_ABI_VERSION_2,
        .activation_bits = 8,
        .activation_storage_bytes = 1,
        .dim = 16,
        .stripe_id = 0,
        .i_start = 0,
        .rows = 2,
        .activations = activations,
        .activation_row_stride_bytes = 3,
        .context = 33,
    };
    v2_stripe.activation_row_stride_bytes = 2;
    if (im2p_publish_stripe_v2(v2_stream, &v2_stripe) != IM2P_INVALID_LAYOUT ||
        v2_sentinel[0] != INT32_C(0x5a5a5a5a) ||
        v2_sentinel[1] != INT32_C(0x5a5a5a5a) ||
        v2_sentinel[2] != INT32_C(0x5a5a5a5a) ||
        v2_sentinel[3] != INT32_C(0x5a5a5a5a)) {
      im2p_destroy_stream(v2_stream);
      im2p_sim_destroy(sim);
      return 37;
    }
    v2_stripe.activation_row_stride_bytes = 3;
    im2p_activation_stripe_t mixed_version_stripe = {
        .stripe_id = 0,
        .i_start = 0,
        .rows = 2,
        .activations = activations,
        .activation_row_stride = 3,
        .context = 33,
    };
    if (im2p_publish_stripe(v2_stream, &mixed_version_stripe) !=
            IM2P_CONFIGURATION_MISMATCH ||
        im2p_publish_stripe_v2(v2_stream, &v2_stripe) != IM2P_OK) {
      im2p_destroy_stream(v2_stream);
      im2p_sim_destroy(sim);
      return 37;
    }
    int v2_done = 0;
    im2p_stripe_completion_t v2_completion = {0};
    for (uint32_t cycle = 0; cycle < 100000 && !v2_done; ++cycle) {
      if (im2p_progress_stream(v2_stream, 1) != IM2P_OK) {
        im2p_destroy_stream(v2_stream);
        im2p_sim_destroy(sim);
        return 38;
      }
      v2_done = im2p_poll_completed(v2_stream, &v2_completion) == 1;
    }
    if (!v2_done ||
        im2p_publish_stripe_v2(v2_stream, &v2_stripe) != IM2P_LATE_STRIPE ||
        im2p_finish_stream(v2_stream, &stats) != IM2P_OK ||
        expect_output(v2_sentinel, 2) != 0) {
      im2p_destroy_stream(v2_stream);
      im2p_sim_destroy(sim);
      return 39;
    }
    im2p_destroy_stream(v2_stream);

    static const int8_t scales[2] = {1, 1};
    im2p_matmul_desc_t invalid_matmul =
        full_desc(activations, weights, output);
    invalid_matmul.scales = scales;
    invalid_matmul.scale_values_len = 2;
    invalid_matmul.scale_total_k = 3;
    invalid_matmul.scale_valid_columns = 2;
    invalid_matmul.scale_row_stride = 0;
    invalid_matmul.vector_op = IM2P_VECTOR_MULTIPLY;
    if (im2p_execute_matmul(sim, &invalid_matmul, NULL)
            != IM2P_INVALID_LAYOUT) {
        im2p_sim_destroy(sim);
        return 11;
    }
    im2p_stripe_work_desc_t invalid_stream =
        stripe_desc(weights, output);
    invalid_stream.scales = scales;
    invalid_stream.scale_values_len = 2;
    invalid_stream.scale_total_k = 3;
    invalid_stream.scale_valid_columns = 2;
    invalid_stream.scale_row_stride = 0;
    invalid_stream.vector_op = IM2P_VECTOR_MULTIPLY;
    im2p_stream_t *rejected = (im2p_stream_t *)(uintptr_t)1;
    if (im2p_begin_striped_matmul_ex(sim, &invalid_stream, &rejected)
            != IM2P_INVALID_LAYOUT || rejected != NULL) {
        im2p_sim_destroy(sim);
        return 12;
    }
    invalid_stream = stripe_desc(weights, output);
    invalid_stream.vector_op = UINT8_MAX;
    rejected = (im2p_stream_t *)(uintptr_t)1;
    if (im2p_begin_striped_matmul_ex(sim, &invalid_stream, &rejected)
            != IM2P_INVALID_LAYOUT || rejected != NULL) {
        im2p_sim_destroy(sim);
        return 13;
    }
    invalid_stream = stripe_desc(weights, output);
    invalid_stream.m = (size_t)UINT32_MAX + 1;
    rejected = (im2p_stream_t *)(uintptr_t)1;
    if (im2p_begin_striped_matmul_ex(sim, &invalid_stream, &rejected)
            != IM2P_INVALID_LAYOUT || rejected != NULL) {
        im2p_sim_destroy(sim);
        return 14;
    }
    im2p_stripe_work_desc_t retry = stripe_desc(weights, output);
    im2p_stream_t *retry_stream = NULL;
    if (im2p_begin_striped_matmul_ex(sim, &retry, &retry_stream) != IM2P_OK
            || retry_stream == NULL) {
        im2p_sim_destroy(sim);
        return 15;
    }
    im2p_activation_stripe_t retry_stripe = {
        .stripe_id = 0,
        .i_start = 0,
        .rows = 2,
        .activations = activations,
        .activation_row_stride = 3,
        .context = 19,
    };
    if (im2p_publish_stripe(retry_stream, &retry_stripe) != IM2P_OK) {
        im2p_destroy_stream(retry_stream);
        im2p_sim_destroy(sim);
        return 16;
    }
    im2p_stripe_completion_t retry_completion = {0};
    int retry_completion_seen = 0;
    for (uint32_t cycle = 0;
            cycle < 100000 && !retry_completion_seen;
            ++cycle) {
        if (im2p_progress_stream(retry_stream, 1) != IM2P_OK) {
            im2p_destroy_stream(retry_stream);
            im2p_sim_destroy(sim);
            return 17;
        }
        int poll = im2p_poll_completed(retry_stream, &retry_completion);
        if (poll < 0) {
            im2p_destroy_stream(retry_stream);
            im2p_sim_destroy(sim);
            return 18;
        }
        retry_completion_seen = poll == 1;
    }
    if (!retry_completion_seen
            || im2p_finish_stream(retry_stream, &stats) != IM2P_OK
            || stats.completed_output_tiles != 4
            || expect_output(output, 2) != 0) {
        im2p_destroy_stream(retry_stream);
        im2p_sim_destroy(sim);
        return 19;
    }
    im2p_destroy_stream(retry_stream);

    im2p_matmul_desc_t matmul = full_desc(activations, weights, output);
    if (im2p_execute_matmul(sim, &matmul, &stats) != IM2P_OK
            || stats.completed_output_tiles != 4
            || expect_output(output, 2) != 0) {
        im2p_sim_destroy(sim);
        return 2;
    }
    im2p_work_stats_extended_t extended = {0};
    memset(output, 0, sizeof(output));
    if (im2p_execute_matmul_extended(sim, &matmul, &extended) != IM2P_OK
            || extended.base.completed_output_tiles != 4
            || expect_output(output, 2) != 0) {
        im2p_sim_destroy(sim);
        return 10;
    }

    callback_state_t callback = {.weights = weights};
    im2p_matmul_desc_v1_t callback_matmul = {
        .version = IM2P_PROVIDER_VERSION_1,
        .legacy = full_desc(activations, NULL, NULL),
        .provider = callback_provider(&callback),
    };
    callback_matmul.legacy.weight_row_stride = 5;
    callback_matmul.legacy.output_row_stride = 4;
    if (im2p_execute_matmul_ex(sim, &callback_matmul, &stats) != IM2P_OK
            || callback.weight_reads == 0 || callback.output_writes == 0
            || expect_output(callback.output, 2) != 0) {
        im2p_sim_destroy(sim);
        return 20;
    }
    memset(callback.output, 0, sizeof(callback.output));
    if (im2p_execute_matmul_extended_ex(sim, &callback_matmul, &extended) != IM2P_OK
            || extended.base.completed_output_tiles != 4
            || expect_output(callback.output, 2) != 0) {
        return 27;
    }
    callback.fail_weight = 1;
    if (im2p_execute_matmul_ex(sim, &callback_matmul, NULL) != IM2P_ERROR) {
        im2p_sim_destroy(sim);
        return 21;
    }
    callback.fail_weight = 0;

    memset(output, 0, sizeof(output));
    for (size_t i = 0; i < 8; ++i) output[i] = 0x5a5a5a5a;
    im2p_stripe_work_desc_t work = stripe_desc(padded_weights, output);
    work.weight_row_stride = 5;
    work.output_row_stride = 4;
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
    im2p_activation_stripe_t invalid = stripe;
    invalid.stripe_id = 1;
    if (im2p_publish_stripe(stream, &invalid) != IM2P_INVALID_LAYOUT
            || im2p_publish_stripe(stream, &stripe) != IM2P_OK
            || im2p_publish_stripe(stream, &stripe) != IM2P_LATE_STRIPE) {
        im2p_destroy_stream(stream);
        im2p_sim_destroy(sim);
        return 4;
    }
    im2p_stream_t *duplicate = NULL;
    if (im2p_begin_striped_matmul_ex(sim, &work, &duplicate)
            != IM2P_UNFINISHED_STREAM || duplicate != NULL) {
        return 9;
    }
    im2p_sim_destroy(sim);
    sim = NULL;

    im2p_stripe_completion_t completion = {0};
    int completion_seen = 0;
    uint64_t logical_cycles = im2p_stream_cycle_count(stream);
    if (im2p_progress_stream(stream, 0) != IM2P_OK
            || im2p_stream_cycle_count(stream) != logical_cycles) {
        return 10;
    }
    if (im2p_progress_stream(stream, 7) != IM2P_OK
            || im2p_stream_cycle_count(stream) != logical_cycles + 7) {
        return 11;
    }
    logical_cycles += 7;
    for (uint32_t cycle = 0; cycle < 100000 && !completion_seen; ++cycle) {
        if (im2p_progress_stream(stream, 1) != IM2P_OK
                || im2p_stream_cycle_count(stream) != ++logical_cycles) {
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

    if (status != IM2P_OK
            || !completion_seen
            || completion.stripe_id != 0
            || stats.completed_output_tiles != 4
            || expect_output(output, 4) != 0
            || output[2] != 0x5a5a5a5a || output[3] != 0x5a5a5a5a
            || output[6] != 0x5a5a5a5a || output[7] != 0x5a5a5a5a) {
        return 7;
    }

    sim = im2p_sim_create();
    if (sim == NULL) return 22;
    memset(&callback, 0, sizeof(callback));
    callback.weights = weights;
    im2p_stripe_work_desc_v1_t callback_work = {
        .version = IM2P_PROVIDER_VERSION_1,
        .legacy = stripe_desc(NULL, NULL),
        .provider = callback_provider(&callback),
    };
    callback_work.legacy.weight_row_stride = 5;
    callback_work.legacy.output_row_stride = 4;
    stream = NULL;
    if (im2p_begin_striped_matmul_v1_ex(sim, &callback_work, &stream) != IM2P_OK
            || stream == NULL
            || im2p_publish_stripe(stream, &retry_stripe) != IM2P_OK) {
        im2p_destroy_stream(stream);
        im2p_sim_destroy(sim);
        return 23;
    }
    completion_seen = 0;
    for (uint32_t cycle = 0; cycle < 100000 && !completion_seen; ++cycle) {
        if (im2p_progress_stream(stream, 1) != IM2P_OK) return 24;
        int poll = im2p_poll_completed(stream, &completion);
        if (poll < 0) return 25;
        completion_seen = poll == 1;
    }
    status = im2p_finish_stream(stream, &stats);
    im2p_destroy_stream(stream);
    im2p_sim_destroy(sim);
    if (status != IM2P_OK || !completion_seen || callback.weight_reads == 0
            || callback.output_writes == 0
            || expect_output(callback.output, 2) != 0) {
        return 26;
    }

    sim = im2p_sim_create();
    if (sim == NULL) return 28;
    callback.fail_weight = 1;
    callback_work.provider = callback_provider(&callback);
    stream = NULL;
    if (im2p_begin_striped_matmul_v1_ex(sim, &callback_work, &stream) != IM2P_OK
            || stream == NULL
            || im2p_publish_stripe(stream, &retry_stripe) != IM2P_OK) {
        return 29;
    }
    int callback_error = IM2P_OK;
    for (uint32_t cycle = 0; cycle < 100000; ++cycle) {
        callback_error = im2p_progress_stream(stream, 1);
        if (callback_error != IM2P_OK) break;
    }
    if (callback_error != IM2P_ERROR
            || im2p_progress_stream(stream, 1) != IM2P_ERROR
            || im2p_finish_stream(stream, NULL) != IM2P_ERROR) {
        im2p_destroy_stream(stream);
        im2p_sim_destroy(sim);
        return 30;
    }
    im2p_destroy_stream(stream);
    im2p_sim_destroy(sim);

    puts("IM2P C API: PASS");
    return 0;
}
