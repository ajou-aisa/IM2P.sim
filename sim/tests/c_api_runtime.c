#include "im2p_sim.h"

#include <stdint.h>
#include <string.h>

#ifndef IM2P_TEST_ACTIVATION_BITS
#define IM2P_TEST_ACTIVATION_BITS 8
#endif
#ifndef IM2P_TEST_WEIGHT_BITS
#define IM2P_TEST_WEIGHT_BITS 8
#endif
#ifndef IM2P_TEST_DIM
#define IM2P_TEST_DIM 16
#endif

#if IM2P_TEST_ACTIVATION_BITS == 16
typedef int16_t activation_t;
#define A_STORAGE 2
#else
typedef int8_t activation_t;
#define A_STORAGE 1
#endif
#if IM2P_TEST_WEIGHT_BITS == 16
typedef int16_t weight_t;
#define W_STORAGE 2
#else
typedef int8_t weight_t;
#define W_STORAGE 1
#endif

static const activation_t ACTIVATIONS[1] = {2};
static const weight_t WEIGHTS[1] = {3};
enum { PROGRESS_K = IM2P_TEST_DIM + 1 };
static activation_t progress_activations[PROGRESS_K];
static weight_t progress_weights[PROGRESS_K];
static int callback_count;
static int64_t callback_output;
static int8_t callback_scale;
static int callback_fail;
static int callback_fail_read;

static int read_i8(void *context, size_t row, size_t column, size_t count,
                   int8_t *out) {
  (void)context;
  ++callback_count;
  if (callback_fail_read) return -1;
  if (row || column || count != 1) return -1;
  out[0] = (int8_t)WEIGHTS[0];
  return 0;
}
static int read_i16(void *context, size_t row, size_t column, size_t count,
                    int16_t *out) {
  (void)context;
  ++callback_count;
  if (callback_fail_read) return -1;
  if (row || column || count != 1) return -1;
  out[0] = (int16_t)WEIGHTS[0];
  return 0;
}
static int read_scale(void *context, size_t row, size_t column, size_t count,
                      int8_t *out) {
  (void)context;
  ++callback_count;
  if (row || column || count != 1) return -1;
  out[0] = callback_scale;
  return 0;
}
static int write_output(void *context, size_t block, size_t row,
                        size_t column, size_t count, const int64_t *values) {
  (void)context;
  if (block || row || column || count != 1) return -1;
  if (callback_fail) return -1;
  callback_output = values[0];
  return 0;
}

static im2p_matmul_desc_t descriptor(void) {
  im2p_matmul_desc_t desc = {
      .abi_version = IM2P_ABI_VERSION,
      .activation_bits = IM2P_TEST_ACTIVATION_BITS,
      .activation_storage_bytes = A_STORAGE,
      .weight_bits = IM2P_TEST_WEIGHT_BITS,
      .weight_storage_bytes = W_STORAGE,
      .dim = IM2P_TEST_DIM,
      .activations = ACTIVATIONS,
      .weights = WEIGHTS,
      .m = 1, .n = 1, .k = 1,
      .activation_row_stride_bytes = A_STORAGE,
      .weight_row_stride_bytes = W_STORAGE,
      .output_row_stride = 1,
      .tile_i_rows = 1, .tile_j_columns = 1,
      .block_size = 1,
      .vector_op = IM2P_VECTOR_BYPASS,
  };
  return desc;
}

static im2p_stripe_work_desc_t striped_descriptor(void) {
  im2p_stripe_work_desc_t desc = {
      .abi_version = IM2P_ABI_VERSION,
      .activation_bits = IM2P_TEST_ACTIVATION_BITS,
      .activation_storage_bytes = A_STORAGE,
      .weight_bits = IM2P_TEST_WEIGHT_BITS,
      .weight_storage_bytes = W_STORAGE,
      .dim = IM2P_TEST_DIM,
      .weights = WEIGHTS,
      .m = 1, .n = 1, .k = 1,
      .weight_row_stride_bytes = W_STORAGE,
      .output_row_stride = 1,
      .tile_i_rows = 1, .tile_j_columns = 1,
      .block_size = 1,
      .stripe_count = 1,
      .vector_op = IM2P_VECTOR_BYPASS,
  };
  return desc;
}

static im2p_activation_stripe_t activation_stripe(void) {
  im2p_activation_stripe_t stripe = {
      .abi_version = IM2P_ABI_VERSION,
      .activation_bits = IM2P_TEST_ACTIVATION_BITS,
      .activation_storage_bytes = A_STORAGE,
      .weight_bits = IM2P_TEST_WEIGHT_BITS,
      .weight_storage_bytes = W_STORAGE,
      .dim = IM2P_TEST_DIM,
      .stripe_id = 0,
      .i_start = 0,
      .rows = 1,
      .activations = ACTIVATIONS,
      .activation_row_stride_bytes = A_STORAGE,
      .context = 17,
  };
  return stripe;
}

static int finish_striped(im2p_stream_t *stream) {
  im2p_stripe_completion_t completion = {0};
  int completed = 0;
  int observed_progress = 0;
  uint64_t progress_count = im2p_stream_progress_count(stream);
  for (size_t cycle = 0; cycle < 10000 && !completed; ++cycle) {
    if (im2p_progress_stream(stream, 1) != IM2P_OK) return 0;
    const uint64_t next_progress = im2p_stream_progress_count(stream);
    observed_progress |= next_progress != progress_count;
    progress_count = next_progress;
    const int status = im2p_poll_completed(stream, &completion);
    if (status < 0) return 0;
    completed = status == 1;
  }
  im2p_work_stats_extended_t stats = {0};
  return completed && observed_progress &&
         completion.stripe_id == 0 && completion.i_start == 0 &&
         completion.rows == 1 && completion.context == 17 &&
         im2p_finish_stream_extended(stream, &stats) == IM2P_OK &&
         stats.base.completed_stripes == 1 &&
         stats.base.stripes_published == 1;
}

static int finish_progress_striped(im2p_stream_t *stream) {
  im2p_stripe_completion_t completion = {0};
  uint64_t progress_count = im2p_stream_progress_count(stream);
  int completed = 0;
  int progress_before_completion = 0;
  for (size_t cycle = 0; cycle < 10000 && !completed; ++cycle) {
    if (im2p_progress_stream(stream, 1) != IM2P_OK) return 0;
    const uint64_t next_progress = im2p_stream_progress_count(stream);
    if (next_progress < progress_count) return 0;
    const int status = im2p_poll_completed(stream, &completion);
    if (status < 0) return 0;
    if (next_progress != progress_count && status == 0)
      progress_before_completion = 1;
    progress_count = next_progress;
    completed = status == 1;
  }
  return completed && progress_before_completion &&
         im2p_finish_stream(stream, NULL) == IM2P_OK;
}

static int test_duplicate_stripe(im2p_sim_t *sim) {
  int32_t output[2] = {0, 0};
  im2p_stripe_work_desc_t work = striped_descriptor();
  work.output = output;
  work.m = 2;
  work.stripe_count = 2;
  im2p_stream_t *stream = NULL;
  if (im2p_begin_striped_matmul(sim, &work, &stream) != IM2P_OK ||
      stream == NULL) return 17;

  im2p_activation_stripe_t first = activation_stripe();
  if (im2p_publish_stripe(stream, &first) != IM2P_OK ||
      im2p_publish_stripe(stream, &first) != IM2P_DUPLICATE_STRIPE) {
    im2p_destroy_stream(stream);
    return 18;
  }
  im2p_activation_stripe_t second = first;
  second.stripe_id = 1;
  second.i_start = 1;
  second.context = 18;
  if (im2p_publish_stripe(stream, &second) != IM2P_OK) {
    im2p_destroy_stream(stream);
    return 19;
  }

  int completed = 0;
  for (size_t cycle = 0; cycle < 10000 && completed != 2; ++cycle) {
    if (im2p_progress_stream(stream, 1) != IM2P_OK) {
      im2p_destroy_stream(stream);
      return 20;
    }
    im2p_stripe_completion_t completion = {0};
    const int status = im2p_poll_completed(stream, &completion);
    if (status < 0) {
      im2p_destroy_stream(stream);
      return 21;
    }
    completed += status == 1;
  }
  const int ok = completed == 2 && output[0] == 6 && output[1] == 6 &&
                 im2p_finish_stream(stream, NULL) == IM2P_OK;
  im2p_destroy_stream(stream);
  return ok ? 0 : 22;
}

static int test_wide_transport_and_recovery(im2p_sim_t *sim) {
  const int64_t expected = INT64_C(6) << 30;
  im2p_matmul_desc_t provider = descriptor();
  provider.weights = NULL;
  provider.scale_total_k = 1;
  provider.scale_row_stride = 1;
  provider.scale_valid_columns = 1;
  provider.scale_values_len = 1;
  provider.vector_op = IM2P_VECTOR_SHIFT;
  provider.provider.context = &callback_count;
#if IM2P_TEST_WEIGHT_BITS == 16
  provider.provider.read_weight_i16 = read_i16;
#else
  provider.provider.read_weight_i8 = read_i8;
#endif
  provider.provider.read_scale = read_scale;
  provider.provider.write_output = write_output;

  callback_count = 0;
  callback_output = 0;
  callback_scale = 30;
  callback_fail = 0;
  if (im2p_execute_matmul(sim, &provider, NULL) != IM2P_OK ||
      callback_count == 0 || callback_output != expected) return 13;

  callback_fail = 1;
  if (im2p_execute_matmul(sim, &provider, NULL) != IM2P_ERROR) return 14;
  callback_fail = 0;
  callback_output = 0;
  if (im2p_execute_matmul(sim, &provider, NULL) != IM2P_OK ||
      callback_output != expected) return 15;

  const int8_t scale = 30;
  int32_t narrowed = 0;
  im2p_matmul_desc_t raw = descriptor();
  raw.scales = &scale;
  raw.output = &narrowed;
  raw.scale_total_k = 1;
  raw.scale_row_stride = 1;
  raw.scale_valid_columns = 1;
  raw.scale_values_len = 1;
  raw.vector_op = IM2P_VECTOR_SHIFT;
  if (im2p_execute_matmul(sim, &raw, NULL) != IM2P_OK ||
      narrowed != INT32_MAX) return 16;
  return 0;
}

static int test_error_and_ownership_contracts(im2p_sim_t *sim) {
  im2p_stream_t *stream = (im2p_stream_t *)(uintptr_t)1;
  if (im2p_execute_matmul(sim, NULL, NULL) != IM2P_INVALID_LAYOUT ||
      im2p_begin_striped_matmul(sim, NULL, &stream) != IM2P_INVALID_LAYOUT ||
      stream != NULL ||
      im2p_publish_stripe(NULL, NULL) != IM2P_INVALID_LAYOUT ||
      im2p_progress_stream(NULL, 1) != IM2P_ERROR ||
      im2p_poll_completed(NULL, NULL) != IM2P_ERROR ||
      im2p_finish_stream(NULL, NULL) != IM2P_ERROR) return 23;

  im2p_stripe_work_desc_t unfinished = striped_descriptor();
  int32_t unfinished_output = 0;
  unfinished.output = &unfinished_output;
  if (im2p_begin_striped_matmul(sim, &unfinished, &stream) != IM2P_OK ||
      stream == NULL) {
    im2p_destroy_stream(stream);
    return 24;
  }
  im2p_destroy_stream(stream);
  int32_t recovered_output = 0;
  im2p_matmul_desc_t recovered = descriptor();
  recovered.output = &recovered_output;
  if (im2p_execute_matmul(sim, &recovered, NULL) != IM2P_OK ||
      recovered_output != 6) return 24;

  im2p_sim_t *detached_sim = im2p_sim_create();
  if (detached_sim == NULL) return 25;
  im2p_stripe_work_desc_t detached = striped_descriptor();
  int32_t detached_output = 0;
  detached.output = &detached_output;
  stream = NULL;
  const im2p_activation_stripe_t stripe = activation_stripe();
  if (im2p_begin_striped_matmul(detached_sim, &detached, &stream) != IM2P_OK ||
      stream == NULL || im2p_publish_stripe(stream, &stripe) != IM2P_OK) {
    im2p_destroy_stream(stream);
    im2p_sim_destroy(detached_sim);
    return 26;
  }
  im2p_sim_destroy(detached_sim);
  const int detached_ok =
      finish_striped(stream) && detached_output == 6;
  im2p_destroy_stream(stream);
  return detached_ok ? 0 : 27;
}

static int test_provider_stream_failure(im2p_sim_t *sim) {
  im2p_stripe_work_desc_t work = striped_descriptor();
  work.weights = NULL;
  work.provider.context = &callback_count;
#if IM2P_TEST_WEIGHT_BITS == 16
  work.provider.read_weight_i16 = read_i16;
#else
  work.provider.read_weight_i8 = read_i8;
#endif
  work.provider.write_output = write_output;

  im2p_stream_t *stream = NULL;
  const im2p_activation_stripe_t stripe = activation_stripe();
  if (im2p_begin_striped_matmul(sim, &work, &stream) != IM2P_OK ||
      stream == NULL || im2p_publish_stripe(stream, &stripe) != IM2P_OK) {
    im2p_destroy_stream(stream);
    return 28;
  }
  callback_fail_read = 1;
  int status = IM2P_OK;
  for (size_t cycle = 0; cycle < 10000 && status == IM2P_OK; ++cycle)
    status = im2p_progress_stream(stream, 1);
  callback_fail_read = 0;
  const int failed =
      status == IM2P_ERROR &&
      im2p_progress_stream(stream, 1) == IM2P_ERROR &&
      im2p_finish_stream(stream, NULL) == IM2P_ERROR;
  im2p_destroy_stream(stream);
  return failed ? 0 : 29;
}

int main(void) {
  if (im2p_sim_abi_version() != IM2P_ABI_VERSION ||
      im2p_sim_activation_bits() != IM2P_TEST_ACTIVATION_BITS ||
      im2p_sim_weight_bits() != IM2P_TEST_WEIGHT_BITS ||
      im2p_sim_activation_storage_bytes() != A_STORAGE ||
      im2p_sim_weight_storage_bytes() != W_STORAGE ||
      im2p_sim_dim() != IM2P_TEST_DIM) return 1;
  im2p_sim_t *sim = im2p_sim_create();
  if (!sim) return 2;

  int32_t output = 0;
  im2p_matmul_desc_t direct = descriptor();
  direct.output = &output;
  if (im2p_execute_matmul(sim, &direct, NULL) != IM2P_OK || output != 6)
    return 3;

  im2p_matmul_desc_t provider = descriptor();
  provider.weights = NULL;
  provider.provider.write_output = write_output;
#if IM2P_TEST_WEIGHT_BITS == 16
  provider.provider.read_weight_i16 = read_i16;
#else
  provider.provider.read_weight_i8 = read_i8;
#endif
  callback_count = 0;
  callback_output = 0;
  if (im2p_execute_matmul_extended(sim, &provider, NULL) != IM2P_OK ||
      callback_count == 0 || callback_output != 6) return 4;
  const int wide_status = test_wide_transport_and_recovery(sim);
  if (wide_status != 0) return wide_status;
  const int duplicate_status = test_duplicate_stripe(sim);
  if (duplicate_status != 0) return duplicate_status;
  const int contract_status = test_error_and_ownership_contracts(sim);
  if (contract_status != 0) return contract_status;
  const int provider_failure_status = test_provider_stream_failure(sim);
  if (provider_failure_status != 0) return provider_failure_status;

  im2p_matmul_desc_t wrong = descriptor();
  wrong.weights = NULL;
  wrong.provider.write_output = write_output;
#if IM2P_TEST_WEIGHT_BITS == 16
  wrong.provider.read_weight_i8 = read_i8;
#else
  wrong.provider.read_weight_i16 = read_i16;
#endif
  callback_count = 0;
  callback_output = 0;
  if (im2p_execute_matmul(sim, &wrong, NULL) != IM2P_INVALID_LAYOUT ||
      callback_count != 0 || callback_output != 0) return 5;

  im2p_stripe_work_desc_t direct_striped = striped_descriptor();
  int32_t striped_output = 0;
  direct_striped.output = &striped_output;
  im2p_stream_t *stream = NULL;
  if (im2p_begin_striped_matmul(sim, &direct_striped, &stream) != IM2P_OK ||
      stream == NULL) return 6;
  const im2p_activation_stripe_t stripe = activation_stripe();
  if (im2p_publish_stripe(stream, &stripe) != IM2P_OK ||
      !finish_striped(stream) || striped_output != 6) {
    im2p_destroy_stream(stream);
    return 7;
  }
  im2p_destroy_stream(stream);

  for (size_t index = 0; index < PROGRESS_K; ++index) {
    progress_activations[index] = 1;
    progress_weights[index] = 1;
  }
  im2p_stripe_work_desc_t progress_work = striped_descriptor();
  int32_t progress_output = 0;
  progress_work.weights = progress_weights;
  progress_work.output = &progress_output;
  progress_work.k = PROGRESS_K;
  progress_work.block_size = PROGRESS_K;
  stream = NULL;
  if (im2p_stream_progress_count(NULL) != 0 ||
      im2p_begin_striped_matmul(sim, &progress_work, &stream) != IM2P_OK ||
      stream == NULL || im2p_stream_progress_count(stream) != 0 ||
      im2p_progress_stream(stream, 0) != IM2P_OK ||
      im2p_stream_progress_count(stream) != 0) {
    im2p_destroy_stream(stream);
    return 11;
  }
  im2p_activation_stripe_t progress_stripe = activation_stripe();
  progress_stripe.activations = progress_activations;
  progress_stripe.activation_row_stride_bytes = PROGRESS_K * A_STORAGE;
  if (im2p_publish_stripe(stream, &progress_stripe) != IM2P_OK ||
      !finish_progress_striped(stream) || progress_output != PROGRESS_K) {
    im2p_destroy_stream(stream);
    return 12;
  }
  im2p_destroy_stream(stream);

  im2p_stripe_work_desc_t provider_striped = striped_descriptor();
  provider_striped.weights = NULL;
  provider_striped.provider.write_output = write_output;
#if IM2P_TEST_WEIGHT_BITS == 16
  provider_striped.provider.read_weight_i16 = read_i16;
#else
  provider_striped.provider.read_weight_i8 = read_i8;
#endif
  callback_count = 0;
  callback_output = 0;
  stream = NULL;
  if (im2p_begin_striped_matmul(sim, &provider_striped, &stream) != IM2P_OK ||
      stream == NULL || im2p_publish_stripe(stream, &stripe) != IM2P_OK ||
      !finish_striped(stream) || callback_count == 0 || callback_output != 6) {
    im2p_destroy_stream(stream);
    return 8;
  }
  im2p_destroy_stream(stream);

  im2p_stripe_work_desc_t wrong_striped = striped_descriptor();
  wrong_striped.weights = NULL;
  wrong_striped.provider.write_output = write_output;
#if IM2P_TEST_WEIGHT_BITS == 16
  wrong_striped.provider.read_weight_i8 = read_i8;
#else
  wrong_striped.provider.read_weight_i16 = read_i16;
#endif
  callback_count = 0;
  callback_output = 0;
  stream = (im2p_stream_t *)(uintptr_t)1;
  if (im2p_begin_striped_matmul(sim, &wrong_striped, &stream) !=
          IM2P_INVALID_LAYOUT ||
      stream != NULL || callback_count != 0 || callback_output != 0) return 9;

  im2p_stripe_work_desc_t foreign = striped_descriptor();
  foreign.weight_bits ^= 4;
  stream = (im2p_stream_t *)(uintptr_t)1;
  if (im2p_begin_striped_matmul(sim, &foreign, &stream) !=
          IM2P_CONFIGURATION_MISMATCH ||
      stream != NULL) return 10;

  im2p_sim_destroy(sim);
  return 0;
}
