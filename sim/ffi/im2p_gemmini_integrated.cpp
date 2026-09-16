#include "../backends/gemmini_hp1/runtime.hpp"
#include <new>

using namespace im2p::gemmini_hp1;

double sc_time_stamp() { return 0.0; }

extern "C" im2p_handle_t im2p_create(void) {
  try {
    auto *runtime = new (std::nothrow) Runtime;
    if (!runtime) return nullptr;
    im2p_reset(runtime);
    return runtime;
  } catch (...) {
    return nullptr;
  }
}

extern "C" void im2p_destroy(im2p_handle_t handle) {
  auto *runtime = as_runtime(handle);
  if (!runtime) return;
  runtime->top.final();
  delete runtime;
}

extern "C" void im2p_reset(im2p_handle_t handle) {
  auto *runtime = as_runtime(handle);
  if (!runtime) return;
  reset_state(*runtime);
  clear_inputs(*runtime);
  runtime->top.reset = 1;
  for (unsigned i = 0; i < 5; ++i) raw_clock(*runtime);
  runtime->top.reset = 0;
  raw_clock(*runtime);
}

extern "C" void im2p_tick(im2p_handle_t handle) {
  if (auto *runtime = as_runtime(handle)) tick(*runtime);
}

extern "C" void im2p_tick_staged(im2p_handle_t handle) { im2p_tick(handle); }
extern "C" void im2p_eval(im2p_handle_t handle) {
  if (auto *runtime = as_runtime(handle)) runtime->top.eval();
}
extern "C" std::uint64_t im2p_cycle_count(im2p_handle_t handle) {
  auto *runtime = as_runtime(handle);
  return runtime ? runtime->top.io_coreCycle : 0;
}
extern "C" std::uint64_t im2p_positive_edge_count(im2p_handle_t handle) {
  auto *runtime = as_runtime(handle);
  return runtime ? runtime->edges : 0;
}
extern "C" int im2p_work_active(im2p_handle_t handle) {
  auto *runtime = as_runtime(handle);
  return runtime && runtime->active;
}
extern "C" std::uint64_t im2p_work_cycle_count(im2p_handle_t handle) {
  auto *runtime = as_runtime(handle);
  return runtime && runtime->active ? runtime->top.io_coreCycle - runtime->last_start : 0;
}
extern "C" std::uint64_t im2p_last_completed_work_cycles(im2p_handle_t handle) {
  auto *runtime = as_runtime(handle);
  return runtime ? runtime->last_cycles : 0;
}
extern "C" std::uint64_t im2p_work_start_cycle(im2p_handle_t handle) {
  auto *runtime = as_runtime(handle);
  return runtime ? runtime->last_start : 0;
}
extern "C" std::uint64_t im2p_work_completion_cycle(im2p_handle_t handle) {
  auto *runtime = as_runtime(handle);
  return runtime ? runtime->last_done : 0;
}
extern "C" std::uint32_t im2p_observed_response_mask(im2p_handle_t handle) {
  auto *runtime = as_runtime(handle);
  return runtime ? runtime->observed_responses : 0;
}
extern "C" std::uint32_t im2p_max_concurrent_responses(im2p_handle_t handle) {
  auto *runtime = as_runtime(handle);
  return runtime ? runtime->max_responses : 0;
}
extern "C" std::uint32_t im2p_compiled_activation_bits(void) { return IM2P_ACTIVATION_BITS; }
extern "C" std::uint32_t im2p_compiled_weight_bits(void) { return IM2P_WEIGHT_BITS; }
extern "C" std::uint32_t im2p_compiled_dim(void) { return IM2P_DIM; }
extern "C" std::uint32_t im2p_compiled_accumulator_bits(void) { return 32; }
extern "C" std::uint32_t im2p_compiled_accumulator_rows(void) { return IM2P_ACCUMULATOR_ROWS; }
extern "C" std::uint32_t im2p_compiled_partial_bits(void) { return IM2P_PARTIAL_BITS; }
extern "C" const char *im2p_compiled_numerical_semantics_revision(void) {
  return kNumericalRevision;
}

extern "C" const char *im2p_sim_implementation(void) {
  return "gemmini-hp1-integrated-v1";
}
extern "C" std::uint32_t im2p_compiled_activation_storage_bytes(void) { return 1; }
extern "C" std::uint32_t im2p_compiled_weight_storage_bytes(void) { return 1; }

extern "C" int im2p_configure_work_plan(im2p_handle_t handle,
                                         const im2p_ffi_work_plan_t *plan) {
  auto *runtime = as_runtime(handle);
  if (!runtime || !plan || runtime->active || !plan->tile_i || !plan->tile_j ||
      !plan->tile_k || !plan->activation_rows_per_stripe ||
      plan->tile_i > UINT16_MAX / kDim || plan->tile_j > UINT16_MAX / kDim ||
      plan->tile_k > UINT32_MAX / kDim) return -1;
  runtime->plan = *plan;
  return 1;
}

extern "C" int im2p_start_matmul(im2p_handle_t handle,
                                   const im2p_matmul_descriptor_t *descriptor) {
  auto *runtime = as_runtime(handle);
  if (!runtime || !descriptor) return IM2P_REQUEST_INVALID_ARGUMENT;
  if (runtime->active) return 0;
  if (!valid_descriptor(*descriptor)) return IM2P_REQUEST_INVALID_ARGUMENT;
  runtime->descriptor = *descriptor;
  const auto &d = *descriptor;
  runtime->schedule = {
      {d.row_count, d.column_count, d.reduction_count}, {kDim, kOperandBits},
      {static_cast<std::size_t>(runtime->plan.tile_i),
       static_cast<std::size_t>(runtime->plan.tile_j),
       static_cast<std::size_t>(runtime->plan.tile_k),
       static_cast<std::size_t>(runtime->plan.activation_rows_per_stripe)},
      {d.activation_row_stride, d.weight_row_stride, d.scale_row_stride,
       d.output_row_stride, d.k_origin},
      d.vector_op != 0, d.vector_op != 0, d.accumulate_first_fragment != 0};
  runtime->active = true;
  runtime->async = descriptor->mode == 1;
  runtime->matrix_done = false;
  runtime->fault = false;
  runtime->published_rows = 0;
  runtime->next_stripe_id = 0;
  runtime->last_start = runtime->top.io_coreCycle;
  if (!runtime->async) {
    Stripe stripe{0, 0, descriptor->row_count, descriptor->activation_row_stride,
                  runtime->top.io_coreCycle, 0};
    if (!enqueue_stripe(*runtime, stripe)) return 0;
    runtime->published_rows = descriptor->row_count;
  }
  return 1;
}

extern "C" int im2p_publish_activation_stripe(im2p_handle_t handle,
                                                std::uint32_t row_begin,
                                                std::uint32_t row_count,
                                                std::uint64_t row_stride) {
  auto *runtime = as_runtime(handle);
  if (!runtime || !runtime->active || !runtime->async) return IM2P_PUBLISH_LATE;
  if (row_begin < runtime->published_rows) return IM2P_PUBLISH_DUPLICATE;
  if (row_begin != runtime->published_rows || row_count == 0 ||
      row_count > runtime->descriptor.row_count - row_begin ||
      row_stride < runtime->descriptor.reduction_count * kStorageBytes)
    return IM2P_PUBLISH_INVALID;
  Stripe stripe{runtime->next_stripe_id, row_begin, row_count, row_stride,
                runtime->top.io_coreCycle,
                static_cast<std::uint8_t>(runtime->next_stripe_id & 1U)};
  if (!enqueue_stripe(*runtime, stripe)) return IM2P_PUBLISH_BACKPRESSURE;
  ++runtime->next_stripe_id;
  runtime->published_rows += row_count;
  ++runtime->counters.stripes_published;
  runtime->counters.stripe_rows_published += row_count;
  return IM2P_PUBLISH_ACCEPTED;
}

extern "C" int im2p_activation_stripe_ready(im2p_handle_t handle) {
  auto *runtime = as_runtime(handle);
  return runtime && runtime->active && runtime->async && runtime->stripe_count < 2;
}
extern "C" int im2p_matmul_done(im2p_handle_t handle) {
  auto *runtime = as_runtime(handle);
  return runtime && runtime->matrix_done && !runtime->fault;
}
extern "C" int im2p_acknowledge_matmul(im2p_handle_t handle) {
  auto *runtime = as_runtime(handle);
  if (!runtime || !runtime->matrix_done || runtime->fault) return 0;
  runtime->active = false;
  runtime->matrix_done = false;
  return 1;
}

extern "C" int im2p_activation_read_request(im2p_handle_t handle,
                                              im2p_read_request_t *request) {
  return get_read<ReadKind::activation>(handle, request);
}
extern "C" int im2p_weight_read_request(im2p_handle_t handle,
                                          im2p_read_request_t *request) {
  return get_read<ReadKind::weight>(handle, request);
}
extern "C" int im2p_scale_read_request(im2p_handle_t handle,
                                         im2p_read_request_t *request) {
  return get_read<ReadKind::scale>(handle, request);
}
extern "C" int im2p_stage_activation_read_response(im2p_handle_t handle,
                                                     std::uint64_t tag,
                                                     const void *values,
                                                     std::uint32_t count) {
  return stage_read<ReadKind::activation>(handle, tag,
                                          static_cast<const std::int8_t *>(values), count);
}
extern "C" int im2p_put_activation_read_response(im2p_handle_t handle,
                                                   std::uint64_t tag,
                                                   const void *values,
                                                   std::uint32_t count) {
  const auto status = im2p_stage_activation_read_response(handle, tag, values, count);
  if (status == 1) im2p_tick(handle);
  return status;
}
extern "C" int im2p_stage_weight_read_response(im2p_handle_t handle, std::uint64_t tag,
                                                 const void *values, std::uint32_t count) {
  return stage_read<ReadKind::weight>(handle, tag,
                                      static_cast<const std::int8_t *>(values), count);
}
extern "C" int im2p_put_weight_read_response(im2p_handle_t handle, std::uint64_t tag,
                                               const void *values, std::uint32_t count) {
  const auto status = im2p_stage_weight_read_response(handle, tag, values, count);
  if (status == 1) im2p_tick(handle);
  return status;
}
extern "C" int im2p_stage_scale_read_response(im2p_handle_t handle, std::uint64_t tag,
                                                const std::uint32_t *values,
                                                std::uint32_t count) {
  return stage_read<ReadKind::scale>(handle, tag, values, count);
}
extern "C" int im2p_put_scale_read_response(im2p_handle_t handle, std::uint64_t tag,
                                              const std::uint32_t *values,
                                              std::uint32_t count) {
  const auto status = im2p_stage_scale_read_response(handle, tag, values, count);
  if (status == 1) im2p_tick(handle);
  return status;
}

extern "C" int im2p_output_write_request_i64(im2p_handle_t handle,
                                               im2p_write_request_t *request,
                                               std::int64_t *values) {
  auto *runtime = as_runtime(handle);
  if (!runtime || !request || !values) return IM2P_REQUEST_INVALID_ARGUMENT;
  if (runtime->fault) return IM2P_REQUEST_INVALID_ARGUMENT;
  if (!runtime->write.active || runtime->write.response) return IM2P_REQUEST_ABSENT;
  request->tag = runtime->write.tag;
  request->address = runtime->write.address;
  request->element_count = runtime->write.count;
  std::copy(runtime->write.values.begin(), runtime->write.values.end(), values);
  return IM2P_REQUEST_PRESENT;
}
extern "C" int im2p_output_write_request(im2p_handle_t handle,
                                           im2p_write_request_t *request,
                                           std::int32_t *values) {
  if (!values) return IM2P_REQUEST_INVALID_ARGUMENT;
  std::array<std::int64_t, kDim> exact{};
  const auto status = im2p_output_write_request_i64(handle, request, exact.data());
  if (status == IM2P_REQUEST_PRESENT)
    std::transform(exact.begin(), exact.end(), values,
                   [](std::int64_t value) { return static_cast<std::int32_t>(value); });
  return status;
}
extern "C" int im2p_stage_output_write_response(im2p_handle_t handle, std::uint64_t tag) {
  auto *runtime = as_runtime(handle);
  if (!runtime) return IM2P_REQUEST_INVALID_ARGUMENT;
  if (!runtime->write.active || runtime->write.response || runtime->write.tag != tag)
    return IM2P_REQUEST_IDENTITY_MISMATCH;
  runtime->write.response = true;
  return 1;
}
extern "C" int im2p_put_output_write_response(im2p_handle_t handle, std::uint64_t tag) {
  const auto status = im2p_stage_output_write_response(handle, tag);
  if (status == 1) im2p_tick(handle);
  return status;
}

extern "C" int im2p_stripe_completion(im2p_handle_t handle,
                                        im2p_stripe_completion_t *completion) {
  auto *runtime = as_runtime(handle);
  if (!runtime || !completion) return IM2P_REQUEST_INVALID_ARGUMENT;
  if (!runtime->completion_count) return IM2P_REQUEST_ABSENT;
  const auto &value = runtime->completions[runtime->completion_head];
  completion->stripe_id = value.stripe.id;
  completion->row_begin = value.stripe.row_begin;
  completion->row_count = value.stripe.row_count;
  completion->stripe_context = 0;
  completion->publish_cycle = value.stripe.publish_cycle;
  completion->completion_cycle = value.completion_cycle;
  return IM2P_REQUEST_PRESENT;
}
extern "C" int im2p_stage_acknowledge_stripe_completion(im2p_handle_t handle) {
  auto *runtime = as_runtime(handle);
  if (!runtime || !runtime->completion_count || runtime->staged_completion_ack) return 0;
  runtime->staged_completion_ack = true;
  return 1;
}
extern "C" int im2p_acknowledge_stripe_completion(im2p_handle_t handle) {
  const auto status = im2p_stage_acknowledge_stripe_completion(handle);
  if (status == 1) im2p_tick(handle);
  return status;
}

extern "C" void im2p_matrix_counters(im2p_handle_t handle,
                                      im2p_matrix_counters_t *counters) {
  auto *runtime = as_runtime(handle);
  if (runtime && counters) *counters = runtime->counters;
}
extern "C" void im2p_matrix_debug(im2p_handle_t handle, im2p_matrix_debug_t *debug) {
  auto *runtime = as_runtime(handle);
  if (!runtime || !debug) return;
  *debug = {};
  debug->execution_active = runtime->active;
  debug->configured_rows = runtime->descriptor.row_count;
  debug->accepted_rows = runtime->published_rows;
  debug->activation_request_valid = runtime->read.active &&
      runtime->read.kind == ReadKind::activation;
  debug->weight_request_valid = runtime->read.active && runtime->read.kind == ReadKind::weight;
  debug->scale_request_valid = runtime->read.active && runtime->read.kind == ReadKind::scale;
  debug->output_request_valid = runtime->write.active;
  debug->stripe_host_waiting = runtime->async && runtime->stripe_count == 0 &&
      runtime->published_rows < runtime->descriptor.row_count;
}

extern "C" int im2p_idle(im2p_handle_t handle) {
  auto *runtime = as_runtime(handle);
  return runtime && !runtime->active && !runtime->top.io_busy;
}
extern "C" int im2p_weights_ready(im2p_handle_t handle) { return unsupported(handle); }
extern "C" int im2p_load_weight_ready(im2p_handle_t handle) { return unsupported(handle); }
extern "C" int im2p_activation_ready(im2p_handle_t handle) { return unsupported(handle); }
extern "C" int im2p_execution_done(im2p_handle_t handle) { return unsupported(handle); }
extern "C" int im2p_begin_weight_load(im2p_handle_t handle) { return unsupported(handle); }
extern "C" int im2p_load_weight_row(im2p_handle_t handle, std::uint32_t, const void *) {
  return unsupported(handle);
}
extern "C" int im2p_configure_scaling(im2p_handle_t handle, std::uint32_t, std::uint32_t,
                                        std::uint64_t) { return unsupported(handle); }
extern "C" int im2p_service_scale_request(im2p_handle_t handle,
                                            const im2p_scale_matrix_view_t *) {
  auto *runtime = as_runtime(handle);
  if (!runtime) return IM2P_REQUEST_INVALID_ARGUMENT;
  if (runtime->fault) return IM2P_REQUEST_INVALID_ARGUMENT;
  if (!runtime->read.active || runtime->read.response || runtime->read.kind != ReadKind::scale)
    return IM2P_REQUEST_ABSENT;
  return IM2P_REQUEST_INVALID_ARGUMENT;
}
extern "C" void im2p_scale_counters(im2p_handle_t,
                                      im2p_scale_counters_t *counters) {
  if (counters) *counters = {};
}
extern "C" int im2p_start_execution(im2p_handle_t handle, std::uint32_t, std::uint32_t,
                                      int, std::uint8_t, std::uint32_t, std::uint32_t) {
  return unsupported(handle);
}
extern "C" int im2p_put_activation_row(im2p_handle_t handle, const void *) {
  return unsupported(handle);
}
extern "C" int im2p_acknowledge_execution(im2p_handle_t handle) {
  return unsupported(handle);
}
extern "C" int im2p_write_accumulator_row_i64(im2p_handle_t handle, std::uint32_t,
                                                const std::int64_t *) {
  return unsupported(handle);
}
extern "C" int im2p_read_accumulator_row_i64(im2p_handle_t handle, std::uint32_t,
                                               std::int64_t *) { return unsupported(handle); }
extern "C" int im2p_write_accumulator_row(im2p_handle_t handle, std::uint32_t,
                                            const std::int32_t *) { return unsupported(handle); }
extern "C" int im2p_read_accumulator_row(im2p_handle_t handle, std::uint32_t,
                                           std::int32_t *) { return unsupported(handle); }
extern "C" int im2p_request_accumulator_row_read(im2p_handle_t handle, std::uint32_t) {
  return unsupported(handle);
}
extern "C" int im2p_accumulator_row_read_response(im2p_handle_t handle, std::int64_t *) {
  return unsupported(handle);
}
extern "C" int im2p_consume_accumulator_row_read_response(im2p_handle_t handle) {
  return unsupported(handle);
}
