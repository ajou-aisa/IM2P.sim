#pragma once

#include "../../ffi/im2p_verilator.h"
#include "../../ffi/im2p_geometry_ffi.h"
#include "../../ffi/im2p_config.h"
#include "../../ffi/im2p_integrated_signal.hpp"
#include "../../common/gemmini_schedule.hpp"
#include <VIM2PGemminiWSHP1Sim.h>
#include <im2p_gemmini_hardware.h>
#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <verilated.h>
#if defined(IM2P_VERILATOR_TEST_HOOKS)
#include "../../tests/cycle/accepted_work_observer.h"
#endif

namespace im2p::gemmini_hp1 {

static_assert(IM2P_ACTIVATION_BITS == IM2P_GEMMINI_ACTIVATION_BITS);
static_assert(IM2P_WEIGHT_BITS == IM2P_GEMMINI_WEIGHT_BITS);
static_assert(IM2P_DIM == IM2P_GEMMINI_DIM);
static_assert(gemmini::HardwareShape::block_k == IM2P_GEMMINI_BLOCK_SIZE);
static_assert(IM2P_GEMMINI_ACCUMULATOR_BITS == 32);
static_assert(IM2P_ACCUMULATOR_ROWS == IM2P_GEMMINI_ACCUMULATOR_ROWS);
static_assert(IM2P_GEMMINI_SCRATCHPAD_ROW_BYTES == IM2P_DIM * IM2P_ACTIVATION_BITS / 8);
static_assert(IM2P_GEMMINI_ACCUMULATOR_ROW_BYTES == IM2P_DIM * sizeof(std::int32_t));
// ARRAY_PARTIAL_BITS is the RTL array width; the C ABI retains full INT32 raw results.
using Top = VIM2PGemminiWSHP1Sim;
constexpr std::size_t kDim = IM2P_DIM;
constexpr std::size_t kOperandBits = IM2P_ACTIVATION_BITS;
constexpr std::size_t kStorageBytes = 1;
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


enum class ReadKind : std::uint8_t { none, activation, weight, scale };

struct Stripe {
  std::uint32_t id = 0;
  std::uint32_t row_begin = 0;
  std::uint32_t row_count = 0;
  std::uint64_t row_stride = 0;
  std::uint64_t publish_cycle = 0;
  std::uint8_t slot = 0;
  im2p_production_geometry_v1_t geometry{};
};

struct Completion {
  Stripe stripe{};
  std::uint64_t completion_cycle = 0;
};

struct Loop : gemmini::LoopPlan {
  std::uint32_t generation = 0;
  std::uint16_t scale_base = 0;
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
  im2p_ffi_work_plan_t plan{1, 1, 1, 1};
  // Per-operation explicit companion; never overwrites the legacy plan above.
  bool explicit_geometry = false;
  im2p_production_geometry_v1_t geometry{};
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
  gemmini::ScheduleConfig schedule{};
  gemmini::LoopCursor cursor{};
  Loop current_loop{};
  std::size_t release_head = 0;
  std::size_t release_count = 0;
  PendingRead read{};
  PendingWrite write{};
  im2p_matrix_counters_t counters{};
};

void clear_inputs(Runtime &runtime);
void reset_state(Runtime &runtime);
void raw_clock(Runtime &runtime);
void tick(Runtime &runtime);
bool valid_descriptor(const im2p_matmul_descriptor_t &descriptor);
bool enqueue_stripe(Runtime &runtime, const Stripe &stripe);
void map_read(Runtime &runtime);
void map_write(Runtime &runtime);
bool valid_geometry(const im2p_production_geometry_v1_t &geometry,
                    const im2p_matmul_descriptor_t &descriptor,
                    std::uint32_t scope);
bool geometry_fits(const im2p_production_geometry_v1_t &geometry,
                   std::size_t rows, std::uint8_t slot);
int start_matmul(Runtime &runtime, const im2p_matmul_descriptor_t &descriptor,
                 const im2p_production_geometry_v1_t *geometry);
int publish_stripe(Runtime &runtime, std::uint32_t row_begin,
                   std::uint32_t row_count, std::uint64_t row_stride,
                   const im2p_production_geometry_v1_t *geometry);

#if defined(IM2P_VERILATOR_TEST_HOOKS)
void observe_geometry(const Runtime &runtime, std::uint64_t event,
                       const im2p_production_geometry_v1_t *geometry = nullptr);
#endif

inline Runtime *as_runtime(im2p_handle_t handle) {
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

inline int unsupported(im2p_handle_t) { return IM2P_REQUEST_INVALID_ARGUMENT; }

} // namespace im2p::gemmini_hp1
