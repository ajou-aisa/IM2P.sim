#include "im2p_verilator.h"
#include "im2p_config.h"
#include "im2p_integrated_signal.hpp"

#include <VIM2PGemminiWSHP1Sim.h>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <new>

#include <verilated.h>

double sc_time_stamp() { return 0.0; }

namespace {

using Top = VIM2PGemminiWSHP1Sim;
constexpr std::size_t kDim = IM2P_DIM;
constexpr std::size_t kOperandBits = IM2P_ACTIVATION_BITS;
constexpr std::size_t kStorageBytes = 1;
constexpr std::size_t kSpBytes = kDim * kOperandBits / 8;
constexpr std::size_t kAccBytes = kDim * sizeof(std::int32_t);
constexpr std::uint64_t kSlotStride = 0x01000000;
constexpr std::uint64_t kABase = 0x00100000;
constexpr std::uint64_t kBBase = 0x10000000;
constexpr std::uint64_t kSBase = 0x18000000;
constexpr std::uint64_t kCBase = 0x20000000;
constexpr const char *kNumericalRevision = "signed-scu-sat-v2";

constexpr std::uint64_t slot_address(std::uint64_t base, std::size_t slot) {
  return base + slot * kSlotStride;
}

constexpr std::size_t padded(std::size_t value) {
  return (value + kDim - 1) / kDim * kDim;
}

enum class ReadKind : std::uint8_t { none, activation, weight, scale };

struct Stripe {
  std::uint32_t id = 0;
  std::uint32_t row_begin = 0;
  std::uint32_t row_count = 0;
  std::uint64_t row_stride = 0;
  std::uint64_t publish_cycle = 0;
  std::uint8_t slot = 0;
};

struct Completion {
  Stripe stripe{};
  std::uint64_t completion_cycle = 0;
};

struct Loop {
  std::size_t i = 0;
  std::size_t j = 0;
  std::size_t k = 0;
  std::size_t is = 0;
  std::size_t js = 0;
  std::size_t ks = 0;
  std::size_t kp = 0;
  std::size_t jp = 0;
  std::uint32_t generation = 0;
  std::uint16_t scale_base = 0;
  std::uint16_t fragment_base = 0;
  bool first = false;
  bool last = false;
};

struct PendingRead {
  bool active = false;
  bool response = false;
  ReadKind kind = ReadKind::none;
  std::uint8_t rtl_id = 0;
  std::uint64_t tag = 0;
  std::uint64_t address = 0;
  std::uint32_t count = 0;
  std::array<std::uint8_t, kAccBytes> bytes{};
};

struct PendingWrite {
  bool active = false;
  bool response = false;
  std::uint8_t rtl_id = 0;
  std::uint64_t tag = 0;
  std::uint64_t address = 0;
  std::uint32_t count = 0;
  std::array<std::int64_t, kDim> values{};
};

struct Runtime {
  VerilatedContext context{};
  Top top{&context};
  Runtime() : context{}, top{&context} {
    const char *argv[] = {nullptr};
    Verilated::commandArgs(0, argv);
  }
  im2p_matmul_descriptor_t descriptor{};
  std::uint64_t edges = 0;
  std::uint64_t tag_sequence = 0;
  std::uint64_t tile_k = 32;
  im2p_ffi_work_plan_t plan{1, 1, 1, 1};
  std::uint64_t last_start = 0;
  std::uint64_t last_done = 0;
  std::uint64_t last_cycles = 0;
  std::uint32_t next_generation = 1;
  std::uint8_t observed_responses = 0;
  std::uint8_t max_responses = 0;
  bool active = false;
  bool async = false;
  bool matrix_done = false;
  bool fault = false;
  bool loop_inflight = false;
  bool waiting_logical_done = false;
  bool work_driven = false;
  bool staged_completion_ack = false;
  std::uint32_t published_rows = 0;
  std::uint32_t next_stripe_id = 0;
  std::array<Stripe, 2> stripes{};
  std::size_t stripe_head = 0;
  std::size_t stripe_count = 0;
  std::array<Completion, 2> completions{};
  std::size_t completion_head = 0;
  std::size_t completion_count = 0;
  Stripe current_stripe{};
  bool have_stripe = false;
  std::size_t loop_i = 0;
  std::size_t loop_j = 0;
  std::size_t loop_k = 0;
  bool first_loop = true;
  Loop current_loop{};
  std::array<std::uint8_t, kDim> release_columns{};
  std::size_t release_head = 0;
  std::size_t release_count = 0;
  PendingRead read{};
  PendingWrite write{};
  im2p_matrix_counters_t counters{};
};

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
  const auto tile_k = runtime.tile_k;
  const auto plan = runtime.plan;
  runtime.descriptor = {};
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
  runtime.work_driven = false;
  runtime.staged_completion_ack = false;
  runtime.published_rows = 0;
  runtime.next_stripe_id = 0;
  runtime.stripe_head = 0;
  runtime.stripe_count = 0;
  runtime.completion_head = 0;
  runtime.completion_count = 0;
  runtime.have_stripe = false;
  runtime.loop_i = runtime.loop_j = runtime.loop_k = 0;
  runtime.first_loop = true;
  runtime.release_head = runtime.release_count = 0;
  runtime.read = {};
  runtime.write = {};
  runtime.counters = {};
  runtime.tile_k = tile_k;
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

bool valid_descriptor(const Runtime &runtime, const im2p_matmul_descriptor_t &d) {
  if (d.mode > 1 || d.row_count == 0 || d.column_count == 0 || d.reduction_count == 0 ||
      d.tile_i_rows == 0 || d.tile_i_rows > kDim || d.tile_j_columns == 0 ||
      d.tile_j_columns > kDim || d.activation_row_stride < d.reduction_count * kStorageBytes ||
      d.weight_row_stride < d.column_count * kStorageBytes ||
      d.output_row_stride < d.column_count * sizeof(std::int32_t)) return false;
  const bool dense = d.vector_op == 5 && d.scale_block_size == 32 &&
      d.k_origin <= d.scale_total_k &&
      d.reduction_count <= d.scale_total_k - d.k_origin;
  const bool raw = d.vector_op == 0 && d.scale_block_size == 1 && d.reduction_count <= 32;
  if (!dense && !raw) return false;
  if (!valid_extent(d.activation_base, d.activation_row_stride, d.row_count,
                    d.reduction_count * kStorageBytes) ||
      !valid_extent(d.weight_base, d.weight_row_stride, d.reduction_count,
                    d.column_count * kStorageBytes) ||
      !valid_extent(d.output_base, d.output_row_stride, d.row_count,
                    d.column_count * sizeof(std::int32_t))) return false;
  if (dense && (!valid_extent(d.scale_base, d.scale_row_stride,
                              (d.reduction_count + 31) / 32,
                              d.column_count * sizeof(std::uint32_t)) ||
                d.scale_base % alignof(std::uint32_t) ||
                d.scale_row_stride % alignof(std::uint32_t))) return false;
  return runtime.tile_k != 0;
}

bool enqueue_stripe(Runtime &runtime, const Stripe &stripe) {
  if (runtime.stripe_count == runtime.stripes.size()) return false;
  runtime.stripes[(runtime.stripe_head + runtime.stripe_count) % runtime.stripes.size()] = stripe;
  ++runtime.stripe_count;
  return true;
}

void begin_next_stripe(Runtime &runtime) {
  if (runtime.have_stripe || runtime.stripe_count == 0) return;
  runtime.current_stripe = runtime.stripes[runtime.stripe_head];
  runtime.stripe_head = (runtime.stripe_head + 1) % runtime.stripes.size();
  --runtime.stripe_count;
  runtime.have_stripe = true;
  runtime.loop_i = runtime.current_stripe.row_begin;
  runtime.loop_j = 0;
  runtime.loop_k = 0;
  runtime.first_loop = true;
}

Loop next_loop(Runtime &runtime) {
  const auto stripe_end = static_cast<std::size_t>(runtime.current_stripe.row_begin) +
                          runtime.current_stripe.row_count;
  Loop loop{};
  loop.i = runtime.loop_i;
  loop.j = runtime.loop_j;
  loop.k = runtime.loop_k;
  loop.is = std::min<std::size_t>(runtime.plan.tile_i * kDim, stripe_end - loop.i);
  loop.js = std::min<std::size_t>(runtime.plan.tile_j * kDim,
                                  runtime.descriptor.column_count - loop.j);
  const auto tile_end = runtime.descriptor.vector_op == 0
      ? runtime.descriptor.reduction_count
      : std::min<std::size_t>(runtime.descriptor.reduction_count,
                              (loop.k / (runtime.plan.tile_k * kDim) + 1) *
                                  runtime.plan.tile_k * kDim);
  const auto block_end = runtime.descriptor.vector_op == 0
      ? tile_end : std::min<std::size_t>(tile_end, (loop.k / 32 + 1) * 32);
  loop.ks = std::min<std::size_t>(block_end - loop.k, 32);
  loop.kp = padded(loop.ks);
  loop.jp = padded(loop.js);
  loop.generation = runtime.next_generation++;
  if (runtime.next_generation == 256) runtime.next_generation = 1;
  loop.scale_base = static_cast<std::uint16_t>(runtime.current_stripe.slot * 128);
  loop.fragment_base = static_cast<std::uint16_t>(loop.k / std::min<std::size_t>(kDim, 32));
  loop.first = runtime.first_loop;
  loop.last = loop.i + loop.is == stripe_end &&
              loop.j + loop.js == runtime.descriptor.column_count &&
              loop.k + loop.ks == runtime.descriptor.reduction_count;
  return loop;
}

void drive_work(Runtime &runtime) {
  if (!runtime.active || runtime.fault || runtime.loop_inflight || runtime.waiting_logical_done ||
      runtime.release_count || runtime.read.active || runtime.write.active) return;
  begin_next_stripe(runtime);
  if (!runtime.have_stripe) return;
  runtime.current_loop = next_loop(runtime);
  const auto &loop = runtime.current_loop;
  auto &top = runtime.top;
  top.io_work_bits_maxI = padded(loop.is) / kDim;
  top.io_work_bits_maxJ = loop.jp / kDim;
  top.io_work_bits_maxK = loop.kp / kDim;
  top.io_work_bits_padI = padded(loop.is) - loop.is;
  top.io_work_bits_padJ = loop.jp - loop.js;
  top.io_work_bits_padK = loop.kp - loop.ks;
  top.io_work_bits_aAddress = slot_address(kABase, runtime.current_stripe.slot);
  top.io_work_bits_bAddress = slot_address(kBBase, runtime.current_stripe.slot);
  top.io_work_bits_cAddress = loop.k + loop.ks == runtime.descriptor.reduction_count
      ? slot_address(kCBase, runtime.current_stripe.slot) +
          loop.i * runtime.descriptor.output_row_stride + loop.j * sizeof(std::int32_t)
      : 0;
  top.io_work_bits_scaleBackingAddress = slot_address(kSBase, runtime.current_stripe.slot);
  top.io_work_bits_aStrideBytes = loop.kp * kOperandBits / 8;
  top.io_work_bits_bStrideBytes = loop.jp * kOperandBits / 8;
  top.io_work_bits_cStrideBytes = runtime.descriptor.output_row_stride;
  top.io_work_bits_scaleBase = loop.scale_base;
  top.io_work_bits_scaleGeneration = loop.generation;
  top.io_work_bits_fragmentBase = loop.fragment_base;
  top.io_work_bits_workBase = runtime.current_stripe.slot * 64;
  top.io_work_bits_accumulate = runtime.descriptor.accumulate_first_fragment || loop.k != 0;
  top.io_work_bits_finalFragment = loop.k + loop.ks == runtime.descriptor.reduction_count;
  top.io_work_bits_firstLoop = loop.first;
  top.io_work_bits_finalLoop = loop.last;
  top.io_work_bits_logicalWorkId = (runtime.descriptor.job_id + runtime.current_stripe.id) & 255U;
  top.io_work_bits_hostSlot = runtime.current_stripe.slot;
  top.io_work_bits_rmdRaw = runtime.descriptor.vector_op == 0;
  top.io_work_valid = 1;
  runtime.work_driven = true;
}

void advance_loop(Runtime &runtime) {
  const auto loop = runtime.current_loop;
  runtime.first_loop = false;
  runtime.loop_k += loop.ks;
  if (runtime.loop_k == runtime.descriptor.reduction_count) {
    runtime.loop_k = 0;
    runtime.loop_j += loop.js;
    if (runtime.loop_j == runtime.descriptor.column_count) {
      runtime.loop_j = 0;
      runtime.loop_i += loop.is;
    }
  }
  runtime.release_head = 0;
  const auto fragments_per_block = std::max<std::size_t>(1, 32 / kDim);
  const auto first_block = loop.fragment_base / fragments_per_block;
  const auto last_block =
      (loop.fragment_base + loop.kp / kDim - 1) / fragments_per_block;
  runtime.release_count = (last_block - first_block + 1) * (loop.jp / kDim) * kDim;
  for (std::size_t i = 0; i < kDim; ++i) runtime.release_columns[i] = i;
  if (loop.last) runtime.waiting_logical_done = true;
}

void drive_release(Runtime &runtime) {
  if (runtime.release_count == 0) return;
  auto &top = runtime.top;
  top.io_scaleRelease_valid = 1;
  const auto fragments_per_block = std::max<std::size_t>(1, 32 / kDim);
  const auto first_block = runtime.current_loop.fragment_base / fragments_per_block;
  const auto rows_per_block = runtime.current_loop.jp / kDim;
  top.io_scaleRelease_bits_column =
      runtime.release_columns[runtime.release_head % kDim];
  top.io_scaleRelease_bits_address = runtime.current_loop.scale_base + first_block * rows_per_block +
      runtime.release_head / kDim;
  top.io_scaleRelease_bits_generation = runtime.current_loop.generation;
}

void map_read(Runtime &runtime) {
  auto &pending = runtime.read;
  const auto &loop = runtime.current_loop;
  const auto address = static_cast<std::uint64_t>(runtime.top.io_readRequest_bits_address);
  pending = {};
  pending.active = true;
  pending.rtl_id = runtime.top.io_readRequest_bits_id;
  pending.tag = ++runtime.tag_sequence << 8 | pending.rtl_id;
  pending.bytes.fill(0);
  const auto a_base = slot_address(kABase, runtime.current_stripe.slot);
  const auto b_base = slot_address(kBBase, runtime.current_stripe.slot);
  const auto s_base = slot_address(kSBase, runtime.current_stripe.slot);
  const auto a_size = padded(loop.is) * loop.kp * kOperandBits / 8;
  const auto b_size = loop.kp * loop.jp * kOperandBits / 8;
  if (address >= a_base && address < a_base + a_size) {
    pending.kind = ReadKind::activation;
    const auto element = (address - a_base) * 8 / kOperandBits;
    const auto local_i = element / loop.kp;
    const auto local_k = element % loop.kp;
    if (local_i < loop.is && local_k < loop.ks) {
      pending.count = std::min<std::size_t>(kDim, loop.ks - local_k);
      pending.address = runtime.descriptor.activation_base +
          (loop.i + local_i) * runtime.descriptor.activation_row_stride +
          (loop.k + local_k) * kStorageBytes;
    } else {
      pending.response = true;
    }
  } else if (address >= b_base && address < b_base + b_size) {
    pending.kind = ReadKind::weight;
    const auto element = (address - b_base) * 8 / kOperandBits;
    const auto local_k = element / loop.jp;
    const auto local_j = element % loop.jp;
    if (local_k < loop.ks && local_j < loop.js) {
      pending.count = std::min<std::size_t>(kDim, loop.js - local_j);
      pending.address = runtime.descriptor.weight_base +
          (loop.k + local_k) * runtime.descriptor.weight_row_stride +
          (loop.j + local_j) * kStorageBytes;
    } else {
      pending.response = true;
    }
  } else if (address >= s_base) {
    pending.kind = ReadKind::scale;
    if (runtime.descriptor.vector_op == 0) {
      pending.response = true;
    } else {
      const auto offset = address - s_base;
      const auto rows_per_block = loop.jp / kDim;
      const auto fragments_per_block = std::max<std::size_t>(1, 32 / kDim);
      const auto first_block = loop.fragment_base / fragments_per_block;
      const auto last_block =
          (loop.fragment_base + loop.kp / kDim - 1) / fragments_per_block;
      const auto row_count = (last_block - first_block + 1) * rows_per_block;
      if (offset % kAccBytes != 0 || offset / kAccBytes >= row_count) {
        runtime.fault = true;
        return;
      }
      const auto row = offset / kAccBytes;
      const auto block_offset = row / rows_per_block;
      const auto tile_column = row % rows_per_block;
      const auto source_column = loop.j + tile_column * kDim;
      pending.count = std::min<std::size_t>(kDim,
                                            runtime.descriptor.column_count - source_column);
      const auto global_block = (runtime.descriptor.k_origin + loop.k) / 32 + block_offset;
      pending.address = runtime.descriptor.scale_base +
          global_block * runtime.descriptor.scale_row_stride +
          source_column * sizeof(std::uint32_t);
    }
  } else {
    std::fprintf(stderr, "integrated: unmapped read 0x%llx loop k=%zu kp=%zu\n",
                 static_cast<unsigned long long>(address), loop.k, loop.kp);
    runtime.fault = true;
  }
}

std::uint32_t write_lane_count(const Top &top) {
  std::uint32_t count = 0;
  bool gap = false;
  for (std::size_t lane = 0; lane < kDim; ++lane) {
    bool enabled = true;
    for (std::size_t byte = 0; byte < sizeof(std::int32_t); ++byte) {
      const auto bit = lane * sizeof(std::int32_t) + byte;
      enabled &= (im2p::integrated::byte_at(top.io_writeRequest_bits_mask, bit / 8) &
                  (1U << (bit % 8))) != 0;
    }
    if (enabled && gap) return UINT32_MAX;
    if (enabled) ++count;
    else gap = true;
  }
  return count;
}

void map_write(Runtime &runtime) {
  auto &pending = runtime.write;
  pending = {};
  pending.active = true;
  pending.rtl_id = runtime.top.io_writeRequest_bits_id;
  pending.tag = ++runtime.tag_sequence << 8 | pending.rtl_id;
  const auto address = static_cast<std::uint64_t>(runtime.top.io_writeRequest_bits_address);
  const auto base = slot_address(kCBase, runtime.current_stripe.slot);
  if (address < base) {
    runtime.fault = true;
    return;
  }
  pending.address = runtime.descriptor.output_base + address - base;
  pending.count = write_lane_count(runtime.top);
  if (pending.count == UINT32_MAX || pending.count > kDim) {
    runtime.fault = true;
    return;
  }
  for (std::size_t lane = 0; lane < kDim; ++lane) {
    std::uint32_t bits = 0;
    for (std::size_t byte = 0; byte < sizeof(bits); ++byte)
      bits |= static_cast<std::uint32_t>(im2p::integrated::byte_at(
          runtime.top.io_writeRequest_bits_data, lane * sizeof(bits) + byte)) << (byte * 8);
    std::int32_t value;
    std::memcpy(&value, &bits, sizeof(value));
    pending.values[lane] = value;
  }
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
                        runtime.completions.size()] = {runtime.current_stripe, runtime.last_done};
    ++runtime.completion_count;
  }
  runtime.have_stripe = false;
  runtime.waiting_logical_done = false;
  const bool all_published = runtime.published_rows == runtime.descriptor.row_count;
  if (!runtime.async || (all_published && runtime.stripe_count == 0)) runtime.matrix_done = true;
}

void update_counters(Runtime &runtime) {
  const auto &top = runtime.top;
  runtime.counters.fragments_completed += top.io_events_accumulatorCommitted;
  runtime.counters.activation_wait_cycles +=
      runtime.read.active && runtime.read.kind == ReadKind::activation && !runtime.read.response;
  runtime.counters.weight_wait_cycles +=
      runtime.read.active && runtime.read.kind == ReadKind::weight && !runtime.read.response;
  runtime.counters.output_wait_cycles += runtime.write.active && !runtime.write.response;
  runtime.counters.compute_cycles += top.io_executeBusy;
  runtime.counters.drain_cycles += top.io_storeBusy;
  runtime.counters.activation_overlap_cycles += top.io_events_loadExecuteOverlap;
  runtime.counters.overlap_cycles += top.io_events_loadExecuteOverlap;
  runtime.counters.weight_bank_activations += top.io_events_executeIssued;
}

void tick(Runtime &runtime) {
  if (runtime.fault) return;
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
  const bool release_accepted = top.io_scaleRelease_valid && top.io_scaleRelease_ready;
  const bool read_accepted = top.io_readRequest_valid && top.io_readRequest_ready;
  const bool read_response_accepted = top.io_readBeat_valid && top.io_readBeat_ready;
  const bool write_accepted = top.io_writeRequest_valid && top.io_writeRequest_ready;
  const bool write_response_accepted = top.io_writeCompletion_valid && top.io_writeCompletion_ready;
  const bool loop_done = top.io_loopDone_valid && top.io_loopDone_ready;
  const bool logical_done = top.io_logicalDone_valid;
  const bool completion_ack = runtime.staged_completion_ack;
  const bool model_error = top.io_error || runtime.context.gotFinish();
  if (read_accepted) {
    if (top.io_readRequest_bits_beats != 1) runtime.fault = true;
    else map_read(runtime);
  }
  if (write_accepted) map_write(runtime);
  raw_clock(runtime);
  if (work_accepted) {
    runtime.loop_inflight = true;
    runtime.work_driven = false;
    runtime.first_loop = false;
  }
  if (release_accepted) {
    ++runtime.release_head;
    --runtime.release_count;
  }
  if (read_accepted && !runtime.fault) {
    if (runtime.read.kind == ReadKind::activation) ++runtime.counters.activation_read_requests;
    else if (runtime.read.kind == ReadKind::weight) ++runtime.counters.weight_read_requests;
    else if (runtime.read.kind == ReadKind::scale) ++runtime.counters.scale_read_requests;
  }
  if (read_response_accepted) runtime.read = {};
  if (write_accepted && !runtime.fault) ++runtime.counters.output_write_requests;
  if (write_response_accepted) {
    runtime.write = {};
    ++runtime.counters.output_write_responses;
  }
  if (loop_done) {
    runtime.loop_inflight = false;
    advance_loop(runtime);
  }
  if (logical_done) finish_stripe(runtime);
  if (completion_ack && runtime.completion_count) {
    runtime.completion_head = (runtime.completion_head + 1) % runtime.completions.size();
    --runtime.completion_count;
  }
  runtime.staged_completion_ack = false;
  update_counters(runtime);
  const auto responses = static_cast<std::uint8_t>((read_response_accepted ? 1 : 0) |
      (write_response_accepted ? 8 : 0));
  runtime.observed_responses |= responses;
  runtime.max_responses = std::max<std::uint8_t>(runtime.max_responses,
      static_cast<std::uint8_t>((read_response_accepted ? 1 : 0) +
                                (write_response_accepted ? 1 : 0)));
  runtime.fault |= model_error || runtime.context.gotFinish();
}

Runtime *as_runtime(im2p_handle_t handle) {
  return static_cast<Runtime *>(handle);
}

template <ReadKind Kind>
int get_read(im2p_handle_t handle, im2p_read_request_t *request) {
  auto *runtime = as_runtime(handle);
  if (!runtime || !request) return IM2P_REQUEST_INVALID_ARGUMENT;
  if (runtime->fault) return IM2P_REQUEST_INVALID_ARGUMENT;
  if (!runtime->read.active || runtime->read.response || runtime->read.kind != Kind)
    return IM2P_REQUEST_ABSENT;
  request->tag = runtime->read.tag;
  request->address = runtime->read.address;
  request->element_count = runtime->read.count;
  return IM2P_REQUEST_PRESENT;
}

template <ReadKind Kind, typename Value>
int stage_read(im2p_handle_t handle, std::uint64_t tag, const Value *values,
               std::uint32_t count) {
  auto *runtime = as_runtime(handle);
  if (!runtime || !values || count > kDim) return IM2P_REQUEST_INVALID_ARGUMENT;
  auto &pending = runtime->read;
  if (!pending.active || pending.response || pending.kind != Kind || pending.tag != tag)
    return IM2P_REQUEST_IDENTITY_MISMATCH;
  if (count != pending.count) return IM2P_REQUEST_INVALID_ARGUMENT;
  pending.bytes.fill(0);
  if constexpr (Kind == ReadKind::scale) {
    for (std::size_t lane = 0; lane < count; ++lane) {
      const auto carrier = static_cast<std::uint32_t>(values[lane]);
      if (carrier > 32767U && carrier != 0x80000000U)
        return IM2P_REQUEST_INVALID_ARGUMENT;
      for (std::size_t byte = 0; byte < sizeof(carrier); ++byte)
        pending.bytes[lane * sizeof(carrier) + byte] = carrier >> (byte * 8);
    }
  } else {
    for (std::size_t lane = 0; lane < count; ++lane) {
      const auto value = static_cast<std::int8_t>(values[lane]);
      if constexpr (kOperandBits == 4) {
        if (value < -8 || value > 7) return IM2P_REQUEST_INVALID_ARGUMENT;
      }
      im2p::integrated::put_operand(pending.bytes, lane, value);
    }
  }
  pending.response = true;
  return 1;
}

int unsupported(im2p_handle_t handle) {
  return handle ? IM2P_REQUEST_INVALID_ARGUMENT : IM2P_REQUEST_INVALID_ARGUMENT;
}

}

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
  if (!valid_descriptor(*runtime, *descriptor)) return IM2P_REQUEST_INVALID_ARGUMENT;
  runtime->descriptor = *descriptor;
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
