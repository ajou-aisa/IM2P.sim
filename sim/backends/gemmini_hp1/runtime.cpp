#include "runtime.hpp"

namespace im2p::gemmini_hp1 {

void clear_inputs(Runtime &runtime) {
  auto &top = runtime.top;
  top.io_work_valid = 0;
  top.io_loopDone_ready = 1;
  top.io_scaleRelease_valid = 0;
  top.io_readRequest_ready = 0;
  top.io_readBeat_valid = 0;
  top.io_writeRequest_ready = 0;
  top.io_writeCompletion_valid = 0;
}

void reset_state(Runtime &runtime) {
  const auto plan = runtime.plan;
  runtime.descriptor = {};
  runtime.explicit_geometry = false;
  runtime.geometry = {};
  runtime.edges = 0;
  runtime.tag_sequence = 0;
  runtime.last_start = 0;
  runtime.last_done = 0;
  runtime.last_cycles = 0;
  runtime.next_generation = 1;
  runtime.observed_responses = 0;
  runtime.max_responses = 0;
  runtime.active = false;
  runtime.async = false;
  runtime.matrix_done = false;
  runtime.fault = false;
  runtime.loop_inflight = false;
  runtime.waiting_logical_done = false;
  runtime.staged_completion_ack = false;
  runtime.published_rows = 0;
  runtime.next_stripe_id = 0;
  runtime.stripe_head = 0;
  runtime.stripe_count = 0;
  runtime.completion_head = 0;
  runtime.completion_count = 0;
  runtime.have_stripe = false;
  runtime.schedule = {};
  runtime.cursor = {};
  runtime.release_head = runtime.release_count = 0;
  runtime.read = {};
  runtime.write = {};
  runtime.counters = {};
  runtime.plan = plan;
}

void raw_clock(Runtime &runtime) {
  runtime.top.clock = 0;
  runtime.top.eval();
  runtime.context.timeInc(1);
  runtime.top.clock = 1;
  runtime.top.eval();
  runtime.context.timeInc(1);
  runtime.top.clock = 0;
  runtime.top.eval();
  ++runtime.edges;
}

bool valid_extent(std::uint64_t base, std::uint64_t stride, std::uint64_t rows,
                  std::uint64_t bytes) {
  return rows && bytes && stride >= bytes && bytes - 1 <= UINT64_MAX - base &&
         rows - 1 <= (UINT64_MAX - base - bytes + 1) / stride;
}

bool valid_descriptor(const im2p_matmul_descriptor_t &d) {
  if (d.mode > 1 || d.row_count == 0 || d.column_count == 0 ||
      d.reduction_count == 0 || d.tile_i_rows == 0 || d.tile_i_rows > kDim ||
      d.tile_j_columns == 0 || d.tile_j_columns > kDim ||
      d.activation_row_stride < d.reduction_count * kStorageBytes ||
      d.weight_row_stride < d.column_count * kStorageBytes ||
      d.output_row_stride < d.column_count * sizeof(std::int32_t))
    return false;
  const bool dense = d.vector_op == 5 && d.scale_block_size == 32 &&
                     d.k_origin <= d.scale_total_k &&
                     d.reduction_count <= d.scale_total_k - d.k_origin;
  const bool raw =
      d.vector_op == 0 && d.scale_block_size == 1 && d.reduction_count <= 32;
  if (!dense && !raw)
    return false;
  if (!valid_extent(d.activation_base, d.activation_row_stride, d.row_count,
                    d.reduction_count * kStorageBytes) ||
      !valid_extent(d.weight_base, d.weight_row_stride, d.reduction_count,
                    d.column_count * kStorageBytes) ||
      !valid_extent(d.output_base, d.output_row_stride, d.row_count,
                    d.column_count * sizeof(std::int32_t)))
    return false;
  if (dense && (!valid_extent(d.scale_base, d.scale_row_stride,
                              (d.reduction_count + 31) / 32,
                              d.column_count * sizeof(std::uint32_t)) ||
                d.scale_base % alignof(std::uint32_t) ||
                d.scale_row_stride % alignof(std::uint32_t)))
    return false;
  return true;
}

bool enqueue_stripe(Runtime &runtime, const Stripe &stripe) {
  if (runtime.stripe_count == runtime.stripes.size())
    return false;
  runtime.stripes[(runtime.stripe_head + runtime.stripe_count) %
                  runtime.stripes.size()] = stripe;
  ++runtime.stripe_count;
  return true;
}

void begin_next_stripe(Runtime &runtime) {
  if (runtime.have_stripe || runtime.stripe_count == 0)
    return;
  runtime.current_stripe = runtime.stripes[runtime.stripe_head];
  runtime.stripe_head = (runtime.stripe_head + 1) % runtime.stripes.size();
  --runtime.stripe_count;
  runtime.have_stripe = true;
  if (runtime.explicit_geometry) {
    const auto &g = runtime.current_stripe.geometry;
    runtime.schedule.tile = {static_cast<std::size_t>(g.tile_i_count),
                             static_cast<std::size_t>(g.tile_j_count),
                             static_cast<std::size_t>(g.tile_k_count),
                             static_cast<std::size_t>(g.stripe_rows)};
    // Global row origins retain the operation layout. The memory adapter maps
    // each stripe's explicit row stride relative to its own backing range.
  }
  runtime.cursor = {runtime.current_stripe.row_begin};
}

Loop next_loop(Runtime &runtime) {
  const auto stripe_end =
      static_cast<std::size_t>(runtime.current_stripe.row_begin) +
      runtime.current_stripe.row_count;
  Loop loop{};
  static_cast<gemmini::LoopPlan &>(loop) =
      gemmini::plan_loop(runtime.schedule, stripe_end, runtime.cursor);
  // Deliberately retain the original per-drive generation advance, including
  // a stalled descriptor. Latching this until ready would change behavior.
  loop.generation = runtime.next_generation++;
  if (runtime.next_generation == 256)
    runtime.next_generation = 1;
  loop.scale_base =
      static_cast<std::uint16_t>(runtime.current_stripe.slot * 128);
  return loop;
}

void drive_work(Runtime &runtime) {
  if (!runtime.active || runtime.fault || runtime.loop_inflight ||
      runtime.waiting_logical_done || runtime.release_count ||
      runtime.read.active || runtime.write.active)
    return;
  begin_next_stripe(runtime);
  if (!runtime.have_stripe)
    return;
  runtime.current_loop = next_loop(runtime);
  const auto &loop = runtime.current_loop;
  auto &top = runtime.top;
  top.io_work_bits_maxI = loop.ip / kDim;
  top.io_work_bits_maxJ = loop.jp / kDim;
  top.io_work_bits_maxK = loop.kp / kDim;
  top.io_work_bits_padI = loop.ip - loop.is;
  top.io_work_bits_padJ = loop.jp - loop.js;
  top.io_work_bits_padK = loop.kp - loop.ks;
  top.io_work_bits_aAddress = slot_address(kABase, runtime.current_stripe.slot);
  top.io_work_bits_bAddress = slot_address(kBBase, runtime.current_stripe.slot);
  top.io_work_bits_cAddress =
      loop.final_contribution
          ? slot_address(kCBase, runtime.current_stripe.slot) +
                loop.i * runtime.descriptor.output_row_stride +
                loop.j * sizeof(std::int32_t)
          : 0;
  top.io_work_bits_scaleBackingAddress =
      slot_address(kSBase, runtime.current_stripe.slot);
  top.io_work_bits_aStrideBytes = loop.kp * kOperandBits / 8;
  top.io_work_bits_bStrideBytes = loop.jp * kOperandBits / 8;
  top.io_work_bits_cStrideBytes = runtime.descriptor.output_row_stride;
  top.io_work_bits_scaleBase = loop.scale_base;
  top.io_work_bits_scaleGeneration = loop.generation;
  top.io_work_bits_fragmentBase = loop.fragment_base;
  top.io_work_bits_workBase = runtime.current_stripe.slot * 64;
  top.io_work_bits_accumulate = loop.accumulate;
  top.io_work_bits_finalFragment = loop.final_contribution;
  top.io_work_bits_firstLoop = loop.first;
  top.io_work_bits_finalLoop = loop.last;
  top.io_work_bits_logicalWorkId =
      (runtime.descriptor.job_id + runtime.current_stripe.id) & 255U;
  top.io_work_bits_hostSlot = runtime.current_stripe.slot;
  top.io_work_bits_rmdRaw = runtime.descriptor.vector_op == 0;
  top.io_work_valid = 1;
}

void advance_loop(Runtime &runtime) {
  const auto &loop = runtime.current_loop;
  gemmini::advance_loop(runtime.schedule, loop, runtime.cursor);
  runtime.release_head = 0;
  runtime.release_count = loop.scale_release_count;
  if (loop.last)
    runtime.waiting_logical_done = true;
}

void drive_release(Runtime &runtime) {
  if (runtime.release_count == 0)
    return;
  auto &top = runtime.top;
  top.io_scaleRelease_valid = 1;
  top.io_scaleRelease_bits_column = runtime.release_head % kDim;
  top.io_scaleRelease_bits_address =
      runtime.current_loop.scale_base + runtime.release_head / kDim;
  top.io_scaleRelease_bits_generation = runtime.current_loop.generation;
}

void finish_stripe(Runtime &runtime) {
  runtime.last_done = runtime.top.io_doneCycle;
  runtime.last_start = runtime.top.io_startCycle;
  runtime.last_cycles = runtime.top.io_elapsedCycles;
  ++runtime.counters.works_completed;
  if (runtime.async) {
    if (runtime.completion_count == runtime.completions.size()) {
      runtime.fault = true;
      return;
    }
    runtime.completions[(runtime.completion_head + runtime.completion_count) %
                        runtime.completions.size()] = {runtime.current_stripe,
                                                       runtime.last_done};
    ++runtime.completion_count;
  }
  runtime.have_stripe = false;
  runtime.waiting_logical_done = false;
  const bool all_published =
      runtime.published_rows == runtime.descriptor.row_count;
  if (!runtime.async || (all_published && runtime.stripe_count == 0))
    runtime.matrix_done = true;
}

void update_counters(Runtime &runtime) {
  const auto &top = runtime.top;
  runtime.counters.fragments_completed += top.io_events_accumulatorCommitted;
  runtime.counters.activation_wait_cycles +=
      runtime.read.active && runtime.read.kind == ReadKind::activation &&
      !runtime.read.response;
  runtime.counters.weight_wait_cycles +=
      runtime.read.active && runtime.read.kind == ReadKind::weight &&
      !runtime.read.response;
  runtime.counters.output_wait_cycles +=
      runtime.write.active && !runtime.write.response;
  runtime.counters.compute_cycles += top.io_executeBusy;
  runtime.counters.drain_cycles += top.io_storeBusy;
  runtime.counters.activation_overlap_cycles +=
      top.io_events_loadExecuteOverlap;
  runtime.counters.overlap_cycles += top.io_events_loadExecuteOverlap;
  runtime.counters.weight_bank_activations += top.io_events_executeIssued;
}

void tick(Runtime &runtime) {
  if (runtime.fault)
    return;
  clear_inputs(runtime);
  drive_release(runtime);
  drive_work(runtime);
  auto &top = runtime.top;
  top.io_readRequest_ready = !runtime.read.active;
  top.io_writeRequest_ready = !runtime.write.active;
  if (runtime.read.active && runtime.read.response) {
    top.io_readBeat_valid = 1;
    top.io_readBeat_bits_id = runtime.read.rtl_id;
    top.io_readBeat_bits_last = 1;
    top.io_readBeat_bits_error = 0;
    im2p::integrated::set_bytes(top.io_readBeat_bits_data, runtime.read.bytes);
  }
  if (runtime.write.active && runtime.write.response) {
    top.io_writeCompletion_valid = 1;
    top.io_writeCompletion_bits_id = runtime.write.rtl_id;
    top.io_writeCompletion_bits_error = 0;
  }
  top.eval();
  const bool work_accepted = top.io_work_valid && top.io_work_ready;
#if defined(IM2P_VERILATOR_TEST_HOOKS)
  observe_rmd_scu(runtime);
  if (top.io_work_valid)
    observe_geometry(runtime, IM2P_OBSERVE_OFFERED);
  if (work_accepted)
    observe_geometry(runtime, IM2P_OBSERVE_ACCEPTED);
#endif
  const bool release_accepted =
      top.io_scaleRelease_valid && top.io_scaleRelease_ready;
#if defined(IM2P_VERILATOR_TEST_HOOKS)
  if (release_accepted)
    observe_geometry(runtime, IM2P_OBSERVE_RELEASE);
#endif
  const bool read_accepted =
      top.io_readRequest_valid && top.io_readRequest_ready;
  const bool read_response_accepted =
      top.io_readBeat_valid && top.io_readBeat_ready;
  const bool write_accepted =
      top.io_writeRequest_valid && top.io_writeRequest_ready;
  const bool write_response_accepted =
      top.io_writeCompletion_valid && top.io_writeCompletion_ready;
  const bool loop_done = top.io_loopDone_valid && top.io_loopDone_ready;
  const bool logical_done = top.io_logicalDone_valid;
  const bool completion_ack = runtime.staged_completion_ack;
  const bool model_error = top.io_error || runtime.context.gotFinish();
  if (read_accepted) {
    if (top.io_readRequest_bits_beats != 1)
      runtime.fault = true;
    else
      map_read(runtime);
  }
  if (write_accepted)
    map_write(runtime);
  raw_clock(runtime);
  if (work_accepted) {
    runtime.loop_inflight = true;
    runtime.cursor.first = false;
  }
  if (release_accepted) {
    ++runtime.release_head;
    --runtime.release_count;
  }
  if (read_accepted && !runtime.fault) {
    if (runtime.read.kind == ReadKind::activation)
      ++runtime.counters.activation_read_requests;
    else if (runtime.read.kind == ReadKind::weight)
      ++runtime.counters.weight_read_requests;
    else if (runtime.read.kind == ReadKind::scale)
      ++runtime.counters.scale_read_requests;
  }
  if (read_response_accepted)
    runtime.read = {};
  if (write_accepted && !runtime.fault)
    ++runtime.counters.output_write_requests;
  if (write_response_accepted) {
    runtime.write = {};
    ++runtime.counters.output_write_responses;
  }
  if (loop_done) {
    runtime.loop_inflight = false;
    advance_loop(runtime);
  }
  if (logical_done) {
#if defined(IM2P_VERILATOR_TEST_HOOKS)
    observe_geometry(runtime, IM2P_OBSERVE_DONE);
#endif
    finish_stripe(runtime);
  }
  if (completion_ack && runtime.completion_count) {
    runtime.completion_head =
        (runtime.completion_head + 1) % runtime.completions.size();
    --runtime.completion_count;
  }
  runtime.staged_completion_ack = false;
  update_counters(runtime);
  const auto responses = static_cast<std::uint8_t>(
      (read_response_accepted ? 1 : 0) | (write_response_accepted ? 8 : 0));
  runtime.observed_responses |= responses;
  runtime.max_responses = std::max<std::uint8_t>(
      runtime.max_responses,
      static_cast<std::uint8_t>((read_response_accepted ? 1 : 0) +
                                (write_response_accepted ? 1 : 0)));
  runtime.fault |= model_error || runtime.context.gotFinish();
}

} // namespace im2p::gemmini_hp1
