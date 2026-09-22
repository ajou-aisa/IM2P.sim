#include "im2p_sim.h"

#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
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

#define IM2P_ACTIVATION_BITS IM2P_TEST_ACTIVATION_BITS
#define IM2P_WEIGHT_BITS IM2P_TEST_WEIGHT_BITS
#define IM2P_DIM IM2P_TEST_DIM
#include "../ffi/im2p_config.h"

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
static uint32_t callback_domain;
static uint32_t callback_scale;
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
                      uint32_t *out) {
  (void)context;
  ++callback_count;
  if (row || column || count != 1) return -1;
  out[0] = callback_scale;
  return 0;
}
static int write_output(void *context, size_t block, size_t row,
                        size_t column, size_t count, const int64_t *values, uint32_t output_domain) {
  (void)context;
  if (block || row || column || count != 1 || output_domain != callback_domain) return -1;
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

static int finish_striped_extended(im2p_stream_t *stream) {
  im2p_stripe_completion_extended_t completion = {0};
  int completed = 0;
  for (size_t cycle = 0; cycle < 10000 && !completed; ++cycle) {
    if (im2p_progress_stream(stream, 1) != IM2P_OK) return 0;
    const int status = im2p_poll_completed_extended(stream, &completion);
    if (status < 0) return 0;
    completed = status == 1;
  }
  im2p_work_stats_extended_t stats = {0};
  const int valid =
      completed && completion.base.stripe_id == 0 &&
      completion.base.i_start == 0 && completion.base.rows == 1 &&
      completion.base.context == 17 &&
      completion.publish_to_completion_cycles ==
          completion.completion_cycle - completion.publish_cycle &&
      im2p_finish_stream_extended(stream, &stats) == IM2P_OK &&
      stats.base.completed_stripes == 1;
  if (valid) {
    printf("C_API_EXTENDED stripe_id=%" PRIu32
           " i_start=%zu rows=%zu context=%" PRIu64
           " publish_cycle=%" PRIu64 " completion_cycle=%" PRIu64
           " publish_to_completion_cycles=%" PRIu64
           " algebra=completion-publish(mod2^64)\n",
           completion.base.stripe_id, completion.base.i_start,
           completion.base.rows, completion.base.context,
           completion.publish_cycle, completion.completion_cycle,
           completion.publish_to_completion_cycles);
  }
  return valid;
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
  if (im2p_compiled_accumulator_bits() != IM2P_ACCUMULATOR_BITS ||
      im2p_compiled_accumulator_rows() != IM2P_ACCUMULATOR_ROWS ||
      im2p_compiled_partial_bits() != IM2P_PARTIAL_BITS ||
      strcmp(im2p_compiled_numerical_semantics_revision(),
             IM2P_NUMERICAL_SEMANTICS_REVISION) != 0) return 13;
  const int64_t expected = IM2P_ACCUMULATOR_BITS == 32
                              ? INT32_MIN : INT64_C(6) << 30;
  const int32_t expected_raw = IM2P_ACCUMULATOR_BITS == 32
                                  ? INT32_MIN : INT32_MAX;
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

  const uint32_t scale = 30;
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
      narrowed != expected_raw) return 16;
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
      im2p_poll_completed_extended(NULL, NULL) != IM2P_ERROR ||
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

static int test_abi5_scu_metadata_and_domain(im2p_sim_t *sim) {
  im2p_matmul_desc_t work = descriptor();
  work.weights = NULL;
  work.scale_total_k = 1;
  work.scale_row_stride = 1;
  work.scale_valid_columns = 1;
  work.scale_values_len = 1;
  work.output_domain = IM2P_OUTPUT_SCU_FINAL;
#if IM2P_TEST_WEIGHT_BITS == 16
  work.provider.read_weight_i16 = read_i16;
#else
  work.provider.read_weight_i8 = read_i8;
#endif
  work.provider.read_scale = read_scale;
  work.provider.write_output = write_output;
  const uint8_t operations[] = {4,4,4,5,5,5};
  const uint32_t metadata[] = {257,65536,65790,0,14,0x80000000U};
  const int64_t expected[] = {1542,393216,394740,6,98304,0};
  callback_domain = IM2P_OUTPUT_SCU_FINAL;
  for (size_t i = 0; i < sizeof(operations); ++i) {
    work.vector_op = operations[i];
    callback_scale = metadata[i];
    callback_output = -1;
    if (im2p_execute_matmul(sim, &work, NULL) != IM2P_OK || callback_output != expected[i]) return 30;
  }
  callback_count = 0;
  work.abi_version = 4;
  if (im2p_execute_matmul(sim, &work, NULL) != IM2P_CONFIGURATION_MISMATCH || callback_count != 0) return 31;
  work.abi_version = IM2P_ABI_VERSION;
  work.output_domain = IM2P_OUTPUT_LEGACY_FINAL;
  if (im2p_execute_matmul(sim, &work, NULL) != IM2P_INVALID_LAYOUT || callback_count != 0) return 32;
  work.output_domain = IM2P_OUTPUT_LEGACY_BLOCK;
  if (im2p_execute_matmul(sim, &work, NULL) != IM2P_INVALID_LAYOUT || callback_count != 0) return 33;
  callback_domain = IM2P_OUTPUT_LEGACY_FINAL;
  puts("C_ABI5_SCU_PASS numerical_jobs=6 exact=6 stale_abi4=1 domain_rejections=2");
  return 0;
}

typedef struct {
  uint32_t carriers[2];
  int32_t output;
  int32_t second_output;
  size_t scale_reads[2];
  int fail_scale;
  int fail_write;
  int writes;
  int weight_mode;
} run_fixture_t;

static int run_weight_i8(void *context, size_t row, size_t column,
                         size_t count, int8_t *out) {
  const run_fixture_t *fixture = context;
  if (row >= 32 || column != 0 || count != 1) return -1;
  const int8_t weights[7] = {-3, 0, 3, -1, 2, -2, 1};
  out[0] = fixture->weight_mode ? weights[row % 7] : 1;
  return 0;
}

static int run_scale(void *context, size_t block, size_t column,
                     size_t count, uint32_t *out) {
  run_fixture_t *fixture = context;
  if (block >= 2 || column != 0 || count != 1 ||
      (fixture->fail_scale && block == 1)) return -1;
  ++fixture->scale_reads[block];
  out[0] = fixture->carriers[block];
  return 0;
}

static int run_output(void *context, size_t block, size_t row,
                      size_t column, size_t count, const int64_t *values,
                      uint32_t domain) {
  run_fixture_t *fixture = context;
  if (block || row || column || (count != 1 && count != 2) ||
      domain != IM2P_OUTPUT_SCU_FINAL) return -1;
  if (fixture->fail_write) return -1;
  fixture->output = (int32_t)values[0];
  if (count == 2) fixture->second_output = (int32_t)values[1];
  ++fixture->writes;
  return 0;
}

static int32_t oracle_sat32(int64_t value) {
  return value > INT32_MAX ? INT32_MAX : value < INT32_MIN ? INT32_MIN
                                                          : (int32_t)value;
}

static int32_t run_oracle(const activation_t *a,
                          const im2p_compact_runs_t *runs,
                          const uint32_t carriers[2], int weight_mode) {
  int32_t acc = 0;
  for (size_t index = 0; index < runs->run_count; ++index) {
    const im2p_compact_run_t *run = &runs->runs[index];
    for (size_t local = 0; local < run->compact_k_count;
         local += IM2P_TEST_DIM) {
      int64_t raw = 0;
      const size_t end = local + IM2P_TEST_DIM < run->compact_k_count
                             ? local + IM2P_TEST_DIM
                             : run->compact_k_count;
      for (size_t position = local; position < end; ++position)
        raw += (int64_t)a[run->compact_k_begin + position] *
               (weight_mode ? (int)((run->compact_k_begin + position) * 3 % 7) - 3
                            : 1);
      const uint32_t carrier = carriers[index];
      const int64_t scaled = carrier == 0x80000000U ? 0
                             : carrier >= 32 ? (raw < 0 ? INT32_MIN : INT32_MAX)
                                             : raw * (INT64_C(1) << carrier);
      acc = oracle_sat32((int64_t)acc + oracle_sat32(scaled));
    }
  }
  return acc;
}

static int test_planned_runs(im2p_sim_t *sim) {
  if (strcmp(im2p_sim_implementation(), "gemmini-hp1-integrated-v1") != 0)
    return 0;
  activation_t a[32];
  for (size_t k = 0; k < 32; ++k) a[k] = 1;
  run_fixture_t fixture = {{0, 1}, -99, -99, {0, 0}, 0, 0, 0, 0};
  im2p_matmul_desc_t d = descriptor();
  d.activations = a;
  d.weights = NULL;
  d.scales = NULL;
  d.m = d.n = 1;
  d.k = 22;
  d.activation_row_stride_bytes = 32 * A_STORAGE;
  d.block_size = 32;
  d.vector_op = IM2P_VECTOR_LEFT_SHIFT;
  d.output_domain = IM2P_OUTPUT_SCU_FINAL;
  d.provider.context = &fixture;
  d.provider.read_weight_i8 = run_weight_i8;
  d.provider.read_scale = run_scale;
  d.provider.write_output = run_output;
  im2p_production_geometry_v1_t g = {
      1, sizeof(g), IM2P_TEST_ACTIVATION_BITS, IM2P_TEST_WEIGHT_BITS,
      IM2P_TEST_DIM, IM2P_GEOMETRY_FULL, 1, 1, 22, 1, 1, 2,
      1, 0, 1, 0};
  const im2p_compact_run_t entries[2] = {
      {0, 0x00000fffU, 0, 12}, {1, 0x000003ffU, 12, 10}};
  im2p_compact_runs_t runs = {
      IM2P_COMPACT_RUNS_VERSION, sizeof(runs), 64, 2, entries};
  im2p_matmul_desc_t raw_scales = d;
  const uint32_t carrier = 0;
  raw_scales.scales = &carrier;
  raw_scales.scale_row_stride = (SIZE_MAX / 4) + 1;
  raw_scales.scale_values_len = raw_scales.scale_row_stride + 1;
  raw_scales.scale_valid_columns = 1;
  raw_scales.provider.read_scale = NULL;
  if (im2p_execute_matmul_planned_runs(sim, &raw_scales, &g, &runs, NULL) !=
          IM2P_INVALID_LAYOUT || fixture.output != -99 || fixture.writes != 0)
    return 44;
  puts("C_API_RAW_SCALE_BYTE_EXTENT_PASS");
  if (im2p_execute_matmul_planned_runs(sim, &d, &g, &runs, NULL) != IM2P_OK ||
      fixture.output != 32 ||
      fixture.output != run_oracle(a, &runs, fixture.carriers, 0) ||
      fixture.writes != 1 ||
      !fixture.scale_reads[0] || !fixture.scale_reads[1]) return 34;
  printf("C_API_ORACLE case=k12_plus_10 expected=%d actual=%d\n",
         run_oracle(a, &runs, fixture.carriers, 0), fixture.output);
  activation_t a2[64];
  for (size_t k = 0; k < 64; ++k) a2[k] = 1;
  d.activations = a2;
  d.m = g.m = g.row_count = 2;
  fixture.output = fixture.second_output = -99;
  fixture.writes = 0;
  if (im2p_execute_matmul_planned_runs(sim, &d, &g, &runs, NULL) != IM2P_OK ||
      fixture.output != 32 || fixture.second_output != 32 ||
      fixture.writes != 1) return 42;
  d.activations = a;
  d.m = g.m = g.row_count = 1;
  for (size_t k = 0; k < 22; ++k) a[k] = k % 3 == 0 ? 2 : -1;
  fixture.weight_mode = 1;
  fixture.output = -99;
  fixture.writes = 0;
  if (im2p_execute_matmul_planned_runs(sim, &d, &g, &runs, NULL) != IM2P_OK ||
      fixture.output != -11 ||
      fixture.output != run_oracle(a, &runs, fixture.carriers, 1) ||
      fixture.writes != 1) return 43;
  printf("C_API_ORACLE case=asymmetric_compact_k expected=%d actual=%d\n",
         run_oracle(a, &runs, fixture.carriers, 1), fixture.output);
  fixture.weight_mode = 0;
  for (size_t k = 0; k < 22; ++k) a[k] = 1;
  fixture.output = -99;
  fixture.writes = 0;
  fixture.carriers[1] = 0x80000000U;
  if (im2p_execute_matmul_planned_runs(sim, &d, &g, &runs, NULL) != IM2P_OK ||
      fixture.output != 12 ||
      fixture.output != run_oracle(a, &runs, fixture.carriers, 0) ||
      fixture.writes != 1) return 35;
  const im2p_compact_run_t gap_entries[2] = {
      {0, 0x7fffffffU, 0, 31}, {3, 1, 31, 1}};
  runs.runs = gap_entries;
  runs.original_k = 128;
  d.k = g.k = 32;
  fixture.carriers[1] = 1;
  if (im2p_execute_matmul_planned_runs(sim, &d, &g, &runs, NULL) != IM2P_OK ||
      fixture.output != 33 ||
      fixture.output != run_oracle(a, &runs, fixture.carriers, 0)) return 38;
  printf("C_API_ORACLE case=k31_plus_1_gap expected=%d actual=%d\n",
         run_oracle(a, &runs, fixture.carriers, 0), fixture.output);
  fixture.carriers[0] = fixture.carriers[1] = 31;
  if (im2p_execute_matmul_planned_runs(sim, &d, &g, &runs, NULL) != IM2P_OK ||
      fixture.output != INT32_MAX ||
      fixture.output != run_oracle(a, &runs, fixture.carriers, 0)) return 39;
  printf("C_API_ORACLE case=positive_sat expected=%d actual=%d\n",
         run_oracle(a, &runs, fixture.carriers, 0), fixture.output);
  for (size_t k = 0; k < 32; ++k) a[k] = -1;
  if (im2p_execute_matmul_planned_runs(sim, &d, &g, &runs, NULL) != IM2P_OK ||
      fixture.output != INT32_MIN ||
      fixture.output != run_oracle(a, &runs, fixture.carriers, 0)) return 40;
  printf("C_API_ORACLE case=negative_sat expected=%d actual=%d\n",
         run_oracle(a, &runs, fixture.carriers, 0), fixture.output);
  for (size_t k = 0; k < 32; ++k) a[k] = 1;
  runs.runs = entries;
  runs.original_k = 64;
  d.k = g.k = 22;
  fixture.carriers[0] = 0;
  fixture.carriers[1] = 1;
  fixture.output = -99;
  fixture.writes = 0;
  fixture.fail_scale = 1;
  if (im2p_execute_matmul_planned_runs(sim, &d, &g, &runs, NULL) != IM2P_ERROR ||
      fixture.output != -99 || fixture.writes != 0) return 36;
  fixture.fail_scale = 0;
  fixture.fail_write = 1;
  if (im2p_execute_matmul_planned_runs(sim, &d, &g, &runs, NULL) != IM2P_ERROR ||
      fixture.output != -99 || fixture.writes != 0) return 41;
  fixture.fail_write = 0;
  im2p_compact_run_t bad[2] = {entries[0], entries[1]};
  bad[1].compact_k_begin = 13;
  runs.runs = bad;
  if (im2p_execute_matmul_planned_runs(sim, &d, &g, &runs, NULL) !=
          IM2P_INVALID_LAYOUT || fixture.output != -99 || fixture.writes != 0)
    return 37;
  puts("C_API_PLANNED_RUNS_PASS unequal=1 asymmetric_k=1 m2_single_write=1 zero=1 gap31=1 saturation=2 failed_scale=1 failed_write=1 malformed=1");
  return 0;
}

int main(int argc, char **argv) {
  if (im2p_sim_abi_version() != IM2P_ABI_VERSION ||
      im2p_sim_activation_bits() != IM2P_TEST_ACTIVATION_BITS ||
      im2p_sim_weight_bits() != IM2P_TEST_WEIGHT_BITS ||
      im2p_sim_activation_storage_bytes() != A_STORAGE ||
      im2p_sim_weight_storage_bytes() != W_STORAGE ||
      im2p_sim_dim() != IM2P_TEST_DIM) return 1;
  im2p_sim_t *sim = im2p_sim_create();
  if (!sim) return 2;
  if (argc == 2 && strcmp(argv[1], "--planned-runs-only") == 0) {
    const int status = test_planned_runs(sim);
    im2p_sim_destroy(sim);
    return status;
  }

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
      !finish_striped_extended(stream) || callback_count == 0 ||
      callback_output != 6) {
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

  const int scu_status = test_abi5_scu_metadata_and_domain(sim);
  if (scu_status != 0) return scu_status;
  const int runs_status = test_planned_runs(sim);
  if (runs_status != 0) return runs_status;
  im2p_sim_destroy(sim);
  return 0;
}
