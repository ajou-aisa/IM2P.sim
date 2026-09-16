#if !defined(IM2P_RTL_TEST_BUILD) || !__has_include(<VIM2PGemminiWSHP1RtlTest.h>)
int im2p_gemmini_hp1_ws_rtl_test_requires_generated_model;
#else

#include <VIM2PGemminiWSHP1RtlTest.h>
#include "frontend_rtl_fixture.hpp"
#include "rmd_rtl_fixture.hpp"
#include "bound_rmd_rtl_fixture.hpp"
#include "im2p_sim.h"
#include "im2p_integrated_signal.hpp"

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include <verilated.h>

namespace {
using Dut = VIM2PGemminiWSHP1RtlTest;
using namespace im2p::gemmini_hp1;
constexpr std::size_t dim = IM2P_DIM;
constexpr std::size_t sp_bytes = dim * IM2P_OPERAND_BITS / 8;
constexpr std::size_t acc_bytes = dim * 4;
constexpr std::uint64_t a_base = 0x100000;
constexpr std::uint64_t a_slot_stride = 0x01000000;
constexpr std::uint64_t b_base = 0x10000000;
constexpr std::uint64_t s_base = 0x18000000;
constexpr std::uint64_t c_base = 0x20000000;

constexpr std::uint64_t slot_address(std::uint64_t base, std::size_t slot) {
  return base + slot * a_slot_stride;
}

void check(bool condition, const char *message) {
  if (!condition) throw std::runtime_error(message);
}

using im2p::integrated::byte_at;
using im2p::integrated::set_bytes;

std::size_t padded(std::size_t count) { return (count + dim - 1) / dim * dim; }

void put_operand(std::vector<std::uint8_t> &bytes, std::size_t index, std::int8_t value) {
  im2p::integrated::put_operand(bytes, index, value);
}

struct Read {
  std::uint32_t id;
  std::uint64_t address, due, sequence;
  bool scale;
};
struct Write { std::uint32_t id; std::uint64_t due; };

struct Adapter {
  Dut &dut;
  VerilatedContext &context;
  std::uint64_t cycle = 0, read_sequence = 0, latest_read_sequence = 0;
  std::uint64_t reordered_reads = 0, loops = 0, logical_done = 0;
  std::uint64_t contexts = 0, raw_rows = 0, commits = 0, completions = 0;
  std::uint64_t loads = 0, executes = 0, stores = 0, overlap = 0;
  std::uint64_t load_dma = 0, store_dma = 0;
  std::uint64_t overlap_loops = 0, delayed_done_cycles = 0;
  std::uint64_t scale_reads = 0, scale_responses = 0, operand_reads = 0;
  std::uint64_t read_backpressure_cycles = 0;
  std::uint64_t read_latency = 3, reorder_latency = 13, scale_latency = 17;
  std::uint64_t write_latency = 11, read_ready_period = 5;
  std::vector<Read> reads;
  std::vector<Write> writes;
  std::array<std::vector<std::uint8_t>, 2> a, b, s, c, written;
  std::array<std::size_t, 2> rows{}, columns{}, c_stride{};
  std::vector<std::uint64_t> accepted_read_addresses, pipeline_activation_bases;
  std::vector<std::uint32_t> accepted_load_local_addresses;
  std::vector<std::uint32_t> scratchpad_read_bank_masks;
  std::vector<std::uint32_t> completed_slots;
  std::vector<std::uint32_t> logical_done_ids;
  std::vector<bool> output_ids;
  std::size_t active_slot = 0;
  std::size_t held_read = 0;
  bool read_held = false;
  bool stalled_read_valid = false;
  std::uint64_t stalled_read_address = 0;
  std::uint32_t stalled_read_beats = 0, stalled_read_id = 0;
  const char *phase = "reset";

  Adapter(Dut &model, VerilatedContext &simulation) : dut(model), context(simulation) {}

  void clock() {
    dut.clock = 0;
    dut.eval();
    context.timeInc(1);
    dut.clock = 1;
    dut.eval();
    context.timeInc(1);
    ++cycle;
    check(!context.gotFinish(), "integrated RTL stopped unexpectedly");
  }

  std::vector<std::uint8_t> read_row(std::uint64_t address) {
    const std::vector<std::uint8_t> *source = nullptr;
    std::uint64_t base = 0;
    std::size_t width = 0;
    for (std::size_t slot = 0; slot < 2 && !source; ++slot) {
      for (const auto candidate : std::array{
               std::pair{slot_address(a_base, slot), &a[slot]},
               std::pair{slot_address(b_base, slot), &b[slot]},
               std::pair{slot_address(s_base, slot), &s[slot]}}) {
        if (address >= candidate.first &&
            address < candidate.first + candidate.second->size()) {
          source = candidate.second;
          base = candidate.first;
          width = candidate.second == &s[slot] ? acc_bytes : sp_bytes;
          break;
        }
      }
    }
    check(source != nullptr, "RTL read outside all backing objects");
    check(address >= base && address - base <= source->size() &&
              width <= source->size() - (address - base),
          "RTL read outside padded A/B/scale backing object");
    check((address - base) % width == 0, "RTL read is not a packed row");
    return {source->begin() + (address - base), source->begin() + (address - base) + width};
  }

  void write_row() {
    const auto address = static_cast<std::uint64_t>(dut.io_writeRequest_bits_address);
    check(dut.io_writeRequest_bits_first && dut.io_writeRequest_bits_last,
          "unexpected multi-beat store");
    std::size_t slot = 2;
    for (std::size_t candidate = 0; candidate < 2; ++candidate) {
      const auto base = slot_address(c_base, candidate);
      if (address >= base && address < base + c[candidate].size()) slot = candidate;
    }
    check(slot < 2, "RTL write outside padded INT32 C backing object");
    const auto offset = static_cast<std::size_t>(address - slot_address(c_base, slot));
    check(acc_bytes <= c[slot].size() - offset,
          "RTL write exceeds padded INT32 C backing object");
    check(offset % acc_bytes == 0, "RTL store is not an INT32 row");
    for (std::size_t i = 0; i < acc_bytes; ++i) {
      if ((byte_at(dut.io_writeRequest_bits_mask, i / 8) & (1U << (i % 8))) == 0) continue;
      const auto at = offset + i;
      check(at / c_stride[slot] < rows[slot] && at % c_stride[slot] < columns[slot] * 4,
            "RTL store mask includes padded output");
      check(written[slot].at(at)++ == 0, "duplicate RTL output byte");
      c[slot].at(at) = byte_at(dut.io_writeRequest_bits_data, i);
    }
    check(writes.empty(), "unexpected overlapping backing store IDs");
    writes.push_back({dut.io_writeRequest_bits_id, cycle + write_latency});
  }

  void step() {
    dut.clock = 0;
    dut.io_readRequest_ready = read_ready_period == 0 || cycle % read_ready_period != 0;
    dut.io_readBeat_valid = 0;
    dut.io_writeCompletion_valid = 0;
    std::size_t selected = read_held ? held_read : reads.size();
    if (!read_held) {
      for (std::size_t i = reads.size(); i > 0; --i) {
        if (reads[i - 1].due <= cycle) { selected = i - 1; break; }
      }
    }
    if (selected != reads.size()) {
      dut.io_readBeat_valid = 1;
      dut.io_readBeat_bits_id = reads[selected].id;
      dut.io_readBeat_bits_last = 1;
      dut.io_readBeat_bits_error = 0;
      set_bytes(dut.io_readBeat_bits_data, read_row(reads[selected].address));
    }
    if (!writes.empty() && writes.front().due <= cycle) {
      dut.io_writeCompletion_valid = 1;
      dut.io_writeCompletion_bits_id = writes.front().id;
      dut.io_writeCompletion_bits_error = 0;
    }
    dut.eval();
    if (dut.io_error) {
      std::cerr << "WS protocol error phase=" << phase << " cycle=" << cycle
                << " loads=" << loads << " executes=" << executes << " stores=" << stores
                << " contexts=" << contexts << " raw=" << raw_rows << " commits=" << commits
                << " pending_reads=" << reads.size() << " pending_writes=" << writes.size()
                << " scale_release=" << unsigned(dut.io_scaleRelease_valid) << '\n';
      throw std::runtime_error("integrated WS RTL protocol error");
    }
    if (!writes.empty()) check(!dut.io_logicalDone_valid, "logicalDone preceded backing write completion");
    delayed_done_cycles += !writes.empty() && !dut.io_logicalDone_valid;
    if (dut.io_readBeat_valid && dut.io_readBeat_ready) {
      scale_responses += reads[selected].scale;
      reordered_reads += reads[selected].sequence < latest_read_sequence;
      latest_read_sequence = std::max(latest_read_sequence, reads[selected].sequence);
      reads.erase(reads.begin() + selected);
      read_held = false;
    } else if (dut.io_readBeat_valid) {
      held_read = selected;
      read_held = true;
    }
    read_backpressure_cycles += dut.io_readRequest_valid && !dut.io_readRequest_ready;
    if (stalled_read_valid) {
      check(dut.io_readRequest_valid &&
                static_cast<std::uint64_t>(dut.io_readRequest_bits_address) == stalled_read_address &&
                static_cast<std::uint32_t>(dut.io_readRequest_bits_beats) == stalled_read_beats &&
                static_cast<std::uint32_t>(dut.io_readRequest_bits_id) == stalled_read_id,
            "backing read payload changed while ready was low");
    }
    stalled_read_valid = dut.io_readRequest_valid && !dut.io_readRequest_ready;
    if (stalled_read_valid) {
      stalled_read_address = dut.io_readRequest_bits_address;
      stalled_read_beats = dut.io_readRequest_bits_beats;
      stalled_read_id = dut.io_readRequest_bits_id;
    }
    if (dut.io_readRequest_valid && dut.io_readRequest_ready) {
      check(dut.io_readRequest_bits_beats == 1, "unexpected multi-beat read");
      const auto id = static_cast<std::uint32_t>(dut.io_readRequest_bits_id);
      check(std::none_of(reads.begin(), reads.end(), [id](const Read &r) { return r.id == id; }),
            "backing read ID reused before response");
      const auto address = static_cast<std::uint64_t>(dut.io_readRequest_bits_address);
      const bool scale = address >= s_base && address < c_base;
      scale_reads += scale;
      operand_reads += !scale;
      accepted_read_addresses.push_back(address);
      reads.push_back({id, address, cycle + read_latency +
          (id % 2 == 0 ? reorder_latency : 0) + (scale ? scale_latency : 0),
          ++read_sequence, scale});
    }
    if (dut.io_writeCompletion_valid && dut.io_writeCompletion_ready) writes.erase(writes.begin());
    if (dut.io_writeRequest_valid && dut.io_writeRequest_ready) write_row();
    loads += dut.io_events_loadIssued;
    executes += dut.io_events_executeIssued;
    stores += dut.io_events_storeIssued;
    contexts += dut.io_events_outputContextIssued;
    raw_rows += dut.io_events_rawRow;
    commits += dut.io_events_accumulatorCommitted;
    overlap += dut.io_events_loadExecuteOverlap;
    overlap_loops += dut.io_overlapLoopIssued;
    if (dut.io_events_loadDmaAccepted) {
      ++load_dma;
      accepted_load_local_addresses.push_back(dut.io_events_loadLocalAddressRaw);
    }
    store_dma += dut.io_events_storeDmaAccepted;
    if (dut.io_events_scratchpadReadAcceptedBankMask)
      scratchpad_read_bank_masks.push_back(dut.io_events_scratchpadReadAcceptedBankMask);
    if (dut.io_outputCompleted_valid) {
      const auto id = static_cast<std::size_t>(dut.io_outputCompleted_bits);
      check(id < output_ids.size() && !output_ids[id], "duplicate or unknown output completion");
      output_ids[id] = true;
      ++completions;
    }
    if (dut.io_loopDone_valid && dut.io_loopDone_ready) {
      ++loops;
      completed_slots.push_back(dut.io_completedHostSlot);
    }
    if (dut.io_logicalDone_valid) {
      ++logical_done;
      logical_done_ids.push_back(dut.io_logicalDone_bits);
    }
    clock();
  }

  template <typename Ready>
  void accept(Ready ready, const char *message) {
    phase = message;
    for (unsigned wait = 0; wait < 2000000; ++wait) {
      dut.eval();
      const bool accepted = ready();
      step();
      if (accepted) return;
    }
    throw std::runtime_error(message);
  }

  void pack_scales(const FrontendRtlWork &work, std::size_t j0, std::size_t js,
                   std::size_t kb, std::size_t ke) {
    const auto max_j = padded(js) / dim;
    const auto first_block = kb / 32;
    const auto last_block = (ke - 1) / 32;
    s[active_slot].assign((last_block - first_block + 1) * max_j * acc_bytes, 0);
    for (auto block = first_block; block <= last_block; ++block) {
      for (std::size_t j = 0; j < max_j; ++j) {
        for (std::size_t lane = 0; lane < dim && j * dim + lane < js; ++lane) {
          const auto carrier = work.carriers.at(block * work.columns + j0 + j * dim + lane);
          const auto row = (block - first_block) * max_j + j;
          for (std::size_t byte = 0; byte < 4; ++byte) {
            s[active_slot].at(row * acc_bytes + lane * 4 + byte) =
                static_cast<std::uint8_t>(carrier >> (byte * 8));
          }
        }
      }
    }
  }

  void release_scales(std::size_t js, std::size_t kb, std::size_t ke,
                      std::size_t tile_start, std::uint32_t generation,
                      std::size_t scale_base) {
    const auto max_j = padded(js) / dim;
    const auto fragments_per_block = std::max<std::size_t>(1, 32 / dim);
    const auto fragment_base = (kb - tile_start) / std::min<std::size_t>(dim, 32);
    const auto first_address = scale_base +
        fragment_base / fragments_per_block * max_j;
    const auto rows = ((ke - kb + 31) / 32) * max_j;
    check(first_address + rows <= 256,
          "WorkPlan scale footprint exceeds integrated ScaleMemory");
    for (std::size_t address = first_address; address < first_address + rows; ++address) {
      for (std::size_t lane = 0; lane < dim; ++lane) {
        dut.io_scaleRelease_bits_column = lane;
        dut.io_scaleRelease_bits_address = address;
        dut.io_scaleRelease_bits_generation = generation;
        dut.io_scaleRelease_valid = 1;
        accept([&] { return dut.io_scaleRelease_ready; }, "scale release stalled");
        dut.io_scaleRelease_valid = 0;
      }
    }
  }

  void loop(const FrontendRtlWork &work, std::size_t i0, std::size_t j0,
            std::size_t is, std::size_t js, std::size_t kb, std::size_t ke,
            std::size_t tile_start, bool first_loop, bool final_loop,
            std::uint32_t generation) {
    const auto kp = padded(ke - kb), jp = padded(js);
    active_slot = work.host_slot;
    check(active_slot < 2, "frontend host slot exceeds RTL slot count");
    a[active_slot].assign(padded(is) * kp * IM2P_OPERAND_BITS / 8, 0);
    b[active_slot].assign(kp * jp * IM2P_OPERAND_BITS / 8, 0);
    for (std::size_t i = 0; i < is; ++i)
      for (auto k = kb; k < ke; ++k)
        put_operand(a[active_slot], i * kp + k - kb,
                    work.activations.at((i0 + i) * work.k + k));
    for (auto k = kb; k < ke; ++k)
      for (std::size_t j = 0; j < js; ++j)
        put_operand(b[active_slot], (k - kb) * jp + j,
                    work.weights.at(k * work.columns + j0 + j));
    pack_scales(work, j0, js, kb, ke);
    const auto scale_base = work.plan.mode == Mode::pipeline
        ? active_slot * 128 : 0;
    const bool final_fragment = ke == work.k;
    dut.io_work_bits_maxI = padded(is) / dim;
    dut.io_work_bits_maxJ = jp / dim;
    dut.io_work_bits_maxK = kp / dim;
    dut.io_work_bits_padI = padded(is) - is;
    dut.io_work_bits_padJ = jp - js;
    dut.io_work_bits_padK = kp - (ke - kb);
    dut.io_work_bits_aAddress = slot_address(a_base, active_slot);
    dut.io_work_bits_bAddress = slot_address(b_base, active_slot);
    dut.io_work_bits_cAddress = final_fragment
        ? slot_address(c_base, active_slot) + i0 * c_stride[active_slot] + j0 * 4
        : 0;
    dut.io_work_bits_scaleBackingAddress = slot_address(s_base, active_slot);
    dut.io_work_bits_aStrideBytes = kp * IM2P_OPERAND_BITS / 8;
    dut.io_work_bits_bStrideBytes = jp * IM2P_OPERAND_BITS / 8;
    dut.io_work_bits_cStrideBytes = c_stride[active_slot];
    dut.io_work_bits_scaleBase = scale_base;
    dut.io_work_bits_scaleGeneration = generation;
    dut.io_work_bits_fragmentBase =
        (kb - tile_start) / std::min<std::size_t>(dim, 32);
    dut.io_work_bits_workBase = 0;
    dut.io_work_bits_accumulate = kb != 0;
    dut.io_work_bits_finalFragment = final_fragment;
    dut.io_work_bits_firstLoop = first_loop;
    dut.io_work_bits_finalLoop = final_loop;
    dut.io_work_bits_logicalWorkId = work.work_id & 255U;
    dut.io_work_bits_hostSlot = active_slot;
    dut.io_work_bits_rmdRaw = work.plan.kind == WorkKind::rmd_raw;
    dut.io_work_valid = 1;
    accept([&] { return dut.io_work_ready; }, "loop descriptor stalled");
    dut.io_work_valid = 0;
    accept([&] { return dut.io_loopDone_valid; }, "integrated WS loop stalled");
    check(reads.empty() && writes.empty(), "loop finished before backing traffic drained");
    release_scales(js, kb, ke, tile_start, generation, scale_base);
  }
};

int execute(void *opaque, const FrontendRtlWork &work,
            std::vector<std::int32_t> &output, FrontendRtlTiming &timing) {
  auto &state = *static_cast<Adapter *>(opaque);
  check(work.rows && work.columns && work.k && work.plan.tile_i && work.plan.tile_j && work.plan.tile_k,
        "empty frontend WorkPlan");
  check(work.activations.size() == work.rows * work.k &&
            work.weights.size() == work.k * work.columns &&
            work.carriers.size() == (work.k + 31) / 32 * work.columns,
        "frontend work storage shape mismatch");
  check(work.host_slot < 2, "frontend supplied invalid host slot");
  check(work.plan.kind == WorkKind::dense_hp1_final ||
            (work.plan.kind == WorkKind::rmd_raw && work.k <= 32),
        "invalid raw work domain or compact K");
  state.active_slot = work.host_slot;
  state.rows[state.active_slot] = work.rows;
  state.columns[state.active_slot] = work.columns;
  state.c_stride[state.active_slot] = padded(work.columns) * 4;
  state.c[state.active_slot].assign(
      padded(work.rows) * state.c_stride[state.active_slot], 0xA5);
  state.written[state.active_slot].assign(state.c[state.active_slot].size(), 0);
  const auto old_contexts = state.contexts, old_raw = state.raw_rows, old_commits = state.commits;
  const auto old_done = state.logical_done, old_completions = state.completions;
  const auto old_loads = state.loads, old_executes = state.executes, old_stores = state.stores;
  const auto old_reordered = state.reordered_reads;
  const auto old_scale_reads = state.scale_reads, old_scale_responses = state.scale_responses;
  const auto old_backpressure = state.read_backpressure_cycles;
  const auto old_addresses = state.accepted_read_addresses.size();
  std::uint64_t expected_contexts = 0, expected_rows = 0, expected_outputs = 0;
  std::uint64_t expected_scale_reads = 0;
  bool first = true;
  std::uint32_t generation = 1;
  for (std::size_t i = 0; i < work.rows; i += work.plan.tile_i * dim) {
    const auto is = std::min<std::size_t>(work.plan.tile_i * dim, work.rows - i);
    for (std::size_t j = 0; j < work.columns; j += work.plan.tile_j * dim) {
      const auto js = std::min<std::size_t>(work.plan.tile_j * dim, work.columns - j);
      const auto outputs = padded(is) / dim * (padded(js) / dim);
      check(outputs <= 128, "WorkPlan output footprint exceeds integrated tracker");
      state.output_ids.assign(outputs, false);
      expected_outputs += outputs;
      for (std::size_t k = 0; k < work.k; k += work.plan.tile_k * dim) {
        const auto tile_end = std::min<std::size_t>(work.k, k + work.plan.tile_k * dim);
        for (auto kb = k; kb < tile_end;) {
          const auto ke = dim == 64 ? std::min(tile_end, kb + 32) : tile_end;
          const bool last = i + is == work.rows && j + js == work.columns && ke == work.k;
          const auto fragments = padded(ke - kb) / dim;
          expected_contexts += outputs * fragments;
          expected_rows += is * (padded(js) / dim) * fragments;
          expected_scale_reads += ((ke - kb + 31) / 32) * (padded(js) / dim);
          state.loop(work, i, j, is, js, kb, ke, k, first, last, generation);
          first = false;
          generation = generation % 255 + 1;
          kb = ke;
        }
      }
      check(std::all_of(state.output_ids.begin(), state.output_ids.end(), [](bool done) { return done; }),
            "missing RTL output tile completion");
    }
  }
  check(state.logical_done - old_done == 1, "logical work completed more or less than once");
  check(state.contexts - old_contexts == expected_contexts && state.raw_rows - old_raw == expected_rows &&
            state.commits - old_commits == expected_rows && state.completions - old_completions == expected_outputs,
        "accepted output contexts/raw rows/commits differ from WorkPlan coverage");
  check(state.loads > old_loads && state.executes > old_executes && state.stores > old_stores,
        "missing accepted upstream load/execute/store events");
  check(state.reordered_reads > old_reordered, "backing fixture did not exercise out-of-order IDs");
  check(state.scale_reads - old_scale_reads == expected_scale_reads &&
            state.scale_responses - old_scale_responses == expected_scale_reads,
        "scale backing reads do not match WorkPlan scale rows");
  check(state.read_backpressure_cycles > old_backpressure,
        "backing fixture did not exercise read-request backpressure");
  check(std::any_of(state.accepted_read_addresses.begin() + old_addresses,
                    state.accepted_read_addresses.end(), [&](std::uint64_t address) {
                      const auto base = slot_address(a_base, state.active_slot);
                      return address >= base &&
                             address < base + state.a[state.active_slot].size();
                    }),
        "work did not read its activation backing slot");
  if (work.plan.mode == Mode::pipeline && work.plan.kind == WorkKind::dense_hp1_final) {
    state.pipeline_activation_bases.push_back(slot_address(a_base, state.active_slot));
  }
  check(state.dut.io_measurementValid && state.dut.io_elapsedCycles &&
            state.dut.io_doneCycle - state.dut.io_startCycle == state.dut.io_elapsedCycles,
        "integrated RTL cycle endpoints are invalid");
  check(!state.logical_done_ids.empty() &&
            state.logical_done_ids.back() == (work.work_id & 255U),
        "wrong logical work completion ID");
  timing.start_cycle = state.dut.io_startCycle;
  timing.done_cycle = state.dut.io_doneCycle;
  output.clear();
  output.reserve(work.rows * work.columns);
  for (std::size_t i = 0; i < state.c[state.active_slot].size(); ++i)
    check(state.written[state.active_slot][i] ==
              (i / state.c_stride[state.active_slot] < work.rows &&
                       i % state.c_stride[state.active_slot] < work.columns * 4 ? 1 : 0),
          "RTL result byte coverage is not exactly M*N INT32 values");
  for (std::size_t i = 0; i < work.rows; ++i) {
    for (std::size_t j = 0; j < work.columns; ++j) {
      std::uint32_t raw = 0;
      for (std::size_t byte = 0; byte < 4; ++byte)
        raw |= static_cast<std::uint32_t>(state.c[state.active_slot][
            i * state.c_stride[state.active_slot] + j * 4 + byte]) << (byte * 8);
      std::int32_t value;
      std::memcpy(&value, &raw, sizeof(value));
      output.push_back(value);
    }
  }
  std::cout << "WS RTL " << (work.plan.mode == Mode::full ? "FULL" : "PIPELINE")
            << " rows=" << work.rows << " row_begin=" << work.row_begin
            << " N=" << work.columns << " K=" << work.k
            << " cycles=" << timing.done_cycle - timing.start_cycle
            << " tile=" << work.plan.tile_i << '/' << work.plan.tile_j << '/' << work.plan.tile_k
            << " load/ex/store=" << state.loads - old_loads << '/' << state.executes - old_executes
            << '/' << state.stores - old_stores
            << " contexts=" << expected_contexts << " committed_rows=" << expected_rows
            << " scale_reads=" << expected_scale_reads
            << " reordered_reads=" << state.reordered_reads - old_reordered << '\n';
  return IM2P_OK;
}

int execute_raw(void *opaque, const RmdRawWork &raw,
                std::vector<std::int32_t> &output, std::uint64_t &cycles) {
  check(raw.plan.kind == WorkKind::rmd_raw, "RMD callback received dense work");
  FrontendRtlWork work{};
  work.work_id = raw.work_id;
  work.host_slot = raw.host_slot;
  work.rows = raw.plan.m;
  work.columns = raw.plan.n;
  work.k = raw.plan.k;
  work.plan = raw.plan;
  work.activations = raw.activations;
  work.weights = raw.weights;
  // Raw identity comes from immutable RTL work context, even with a hostile scale.
  work.carriers.assign(work.columns, 30);
  FrontendRtlTiming timing{};
  const auto status = execute(opaque, work, output, timing);
  cycles = timing.done_cycle - timing.start_cycle;
  return status;
}

void run_dual_loop_overlap(Adapter &state) {
  const auto old_loads = state.loads, old_executes = state.executes;
  const auto old_stores = state.stores, old_overlap = state.overlap;
  const auto old_load_dma = state.load_dma, old_store_dma = state.store_dma;
  const auto old_contexts = state.contexts;
  const auto old_overlap_loops = state.overlap_loops;
  const auto old_delayed = state.delayed_done_cycles;
  const auto old_local = state.accepted_load_local_addresses.size();
  const auto old_banks = state.scratchpad_read_bank_masks.size();
  const auto old_reads = state.accepted_read_addresses.size();
  const auto old_completed = state.completed_slots.size();
  const auto old_done = state.logical_done;
  state.output_ids.assign(128, false);
  state.write_latency = 97;

  const auto prepare = [&](std::size_t slot, std::int8_t activation,
                           std::int8_t weight, std::uint32_t carrier) {
    state.rows[slot] = 1;
    state.columns[slot] = 1;
    state.c_stride[slot] = acc_bytes;
    state.a[slot].assign(dim * sp_bytes, 0);
    state.b[slot].assign(dim * sp_bytes, 0);
    state.s[slot].assign(acc_bytes, 0);
    state.c[slot].assign(dim * acc_bytes, 0xA5);
    state.written[slot].assign(state.c[slot].size(), 0);
    put_operand(state.a[slot], 0, activation);
    put_operand(state.b[slot], 0, weight);
    for (std::size_t byte = 0; byte < 4; ++byte)
      state.s[slot][byte] = static_cast<std::uint8_t>(carrier >> (byte * 8));
  };
  prepare(0, 2, 3, 0);
  prepare(1, -2, 4, 1);

  const auto drive = [&](std::size_t slot, std::uint32_t generation,
                         std::uint32_t work_base, std::uint32_t logical_id) {
    auto &dut = state.dut;
    dut.io_work_bits_maxI = 1;
    dut.io_work_bits_maxJ = 1;
    dut.io_work_bits_maxK = 1;
    dut.io_work_bits_padI = dim - 1;
    dut.io_work_bits_padJ = dim - 1;
    dut.io_work_bits_padK = dim - 1;
    dut.io_work_bits_aAddress = slot_address(a_base, slot);
    dut.io_work_bits_bAddress = slot_address(b_base, slot);
    dut.io_work_bits_cAddress = slot_address(c_base, slot);
    dut.io_work_bits_scaleBackingAddress = slot_address(s_base, slot);
    dut.io_work_bits_aStrideBytes = sp_bytes;
    dut.io_work_bits_bStrideBytes = sp_bytes;
    dut.io_work_bits_cStrideBytes = acc_bytes;
    dut.io_work_bits_scaleBase = slot * 128;
    dut.io_work_bits_scaleGeneration = generation;
    dut.io_work_bits_fragmentBase = 0;
    dut.io_work_bits_workBase = work_base;
    dut.io_work_bits_accumulate = 0;
    dut.io_work_bits_finalFragment = 1;
    dut.io_work_bits_firstLoop = 1;
    dut.io_work_bits_finalLoop = 1;
    dut.io_work_bits_logicalWorkId = logical_id;
    dut.io_work_bits_hostSlot = slot;
    dut.io_work_bits_rmdRaw = 0;
  };
  const auto submit = [&](std::size_t slot, std::uint32_t generation,
                          std::uint32_t work_base, std::uint32_t logical_id) {
    drive(slot, generation, work_base, logical_id);
    state.dut.io_work_valid = 1;
    state.accept([&] { return state.dut.io_work_ready; },
                 "dual-loop descriptor stalled");
    state.dut.io_work_valid = 0;
  };

  submit(0, 41, 0, 240);
  submit(1, 42, 64, 241);
  state.accept([&] { return state.overlap_loops > old_overlap_loops; },
               "second loop was not issued while controller remained busy");

  drive(0, 43, 0, 242);
  state.dut.io_work_valid = 1;
  state.dut.eval();
  check(!state.dut.io_work_ready,
        "active slot0 accepted a third work before loop completion");
  const auto held_address = static_cast<std::uint64_t>(state.dut.io_work_bits_aAddress);
  const auto held_scale = static_cast<std::uint32_t>(state.dut.io_work_bits_scaleBase);
  for (unsigned cycle = 0; cycle < 8; ++cycle) {
    state.step();
    check(!state.dut.io_work_ready &&
              static_cast<std::uint64_t>(state.dut.io_work_bits_aAddress) == held_address &&
              static_cast<std::uint32_t>(state.dut.io_work_bits_scaleBase) == held_scale,
          "stalled work request changed payload or became ready early");
  }
  state.accept([&] {
    return std::find(state.completed_slots.begin() + old_completed,
                     state.completed_slots.end(), 0U) != state.completed_slots.end();
  }, "slot0 loop completion stalled");
  state.dut.eval();
  check(!state.dut.io_work_ready,
        "slot0 became reusable before all scale references were released");
  state.dut.io_work_valid = 0;
  state.release_scales(1, 0, 1, 0, 41, 0);
  state.step();
  drive(0, 43, 0, 242);
  state.dut.io_work_valid = 1;
  state.dut.eval();
  check(state.dut.io_work_ready,
        "slot0 did not become reusable after ordered completion and scale release");
  state.dut.io_work_valid = 0;

  state.accept([&] {
    return std::find(state.completed_slots.begin() + old_completed,
                     state.completed_slots.end(), 1U) != state.completed_slots.end();
  }, "slot1 loop completion stalled");
  state.release_scales(1, 0, 1, 0, 42, 128);
  state.accept([&] { return !state.dut.io_busy; }, "dual-loop top did not drain");
  check(state.completed_slots.size() - old_completed == 2 &&
            state.completed_slots[old_completed] == 0 &&
            state.completed_slots[old_completed + 1] == 1,
        "dual-loop completion did not preserve host-slot order");
  check(state.logical_done_ids.size() >= 2 &&
            state.logical_done_ids[state.logical_done_ids.size() - 2] == 240 &&
            state.logical_done_ids.back() == 241,
        "dual-loop logical completion IDs are not ordered exactly once");
  check(state.load_dma - old_load_dma == 2 * (dim + 1) &&
            state.store_dma - old_store_dma == 2 &&
            state.contexts - old_contexts == 2 && state.logical_done - old_done == 2,
        "dual-loop accepted DMA/context/completion counts differ from two WS works");
  check(state.overlap > old_overlap && state.overlap_loops > old_overlap_loops,
        "dual-loop test observed no load/execute or loop-issue overlap");
  check(state.delayed_done_cycles > old_delayed,
        "delayed final stores did not hold logical completion");
  check(state.output_ids[0] && state.output_ids[64],
        "dual-loop output tracker IDs did not complete exactly once");

  const auto scalar = [&](std::size_t slot) {
    std::uint32_t raw = 0;
    for (std::size_t byte = 0; byte < 4; ++byte)
      raw |= static_cast<std::uint32_t>(state.c[slot][byte]) << (byte * 8);
    std::int32_t value;
    std::memcpy(&value, &raw, sizeof(value));
    return value;
  };
  check(scalar(0) == 6 && scalar(1) == -16,
        "dual-loop independent backing outputs mismatch integer oracle");
  for (std::size_t slot = 0; slot < 2; ++slot) {
    for (std::size_t index = 0; index < state.written[slot].size(); ++index)
      check(state.written[slot][index] == (index < 4 ? 1 : 0),
            "dual-loop output byte coverage is not exactly two scalars");
  }

  const auto sp_rows = std::size_t{4} * IM2P_BANK_ROWS;
  bool lower_half = false, upper_half = false;
  for (auto raw = state.accepted_load_local_addresses.begin() + old_local;
       raw != state.accepted_load_local_addresses.end(); ++raw) {
    const auto address = *raw & (sp_rows - 1);
    lower_half |= address < sp_rows / 2;
    upper_half |= address >= sp_rows / 2;
  }
  check(lower_half && upper_half,
        "dual-loop loads did not occupy both original LoopMatmul scratchpad halves");
  check(std::any_of(state.scratchpad_read_bank_masks.begin() + old_banks,
                    state.scratchpad_read_bank_masks.end(),
                    [](std::uint32_t mask) { return mask != 0; }),
        "dual-loop mesh accepted no scratchpad bank reads");
  for (std::size_t slot = 0; slot < 2; ++slot) {
    check(std::any_of(state.accepted_read_addresses.begin() + old_reads,
                      state.accepted_read_addresses.end(), [&](std::uint64_t address) {
                        return address >= slot_address(a_base, slot) &&
                               address < slot_address(a_base, slot) + state.a[slot].size();
                      }) &&
              std::any_of(state.accepted_read_addresses.begin() + old_reads,
                          state.accepted_read_addresses.end(), [&](std::uint64_t address) {
                            return address >= slot_address(b_base, slot) &&
                                   address < slot_address(b_base, slot) + state.b[slot].size();
                          }),
          "dual-loop backing trace missed immutable slot A/B objects");
  }
  std::cout << "WS_DUAL_LOOP slots=0,1 overlap_loop_issue=1 load_execute_overlap="
            << state.overlap - old_overlap << " slot0_reuse_blocked=1 delayed_done_cycles="
            << state.delayed_done_cycles - old_delayed
            << " local_halves=0,1 outputs=6,-16 accepted_load_execute_store="
            << state.loads - old_loads << ',' << state.executes - old_executes << ','
            << state.stores - old_stores << " accepted_load_rows_store_rows_contexts="
            << state.load_dma - old_load_dma << ',' << state.store_dma - old_store_dma << ','
            << state.contexts - old_contexts << '\n';
  state.write_latency = 11;
}
}

int main(int argc, char **argv) {
  try {
    VerilatedContext context;
    context.commandArgs(argc, argv);
    Dut dut{&context};
    Adapter state{dut, context};
    dut.io_work_valid = 0;
    dut.io_loopDone_ready = 1;
    dut.io_scaleRelease_valid = 0;
    dut.io_readRequest_ready = 1;
    dut.io_readBeat_valid = 0;
    dut.io_writeRequest_ready = 1;
    dut.io_writeCompletion_valid = 0;
    dut.reset = 1;
    for (unsigned i = 0; i < 5; ++i) state.clock();
    dut.reset = 0;
    state.clock();
    const Capability capability{"ws-rtl-test", "ws-rtl-test", "hp1-fragment-sat32-v1",
      IM2P_OPERAND_BITS, IM2P_OPERAND_BITS, IM2P_DIM, 32, 32,
      IM2P_OPERAND_BITS == 4 ? Packing::signed_int4_low_nibble_first : Packing::signed_int8,
      true, true, true, 4, IM2P_BANK_ROWS, IM2P_ACC_ROWS, sp_bytes, acc_bytes};
    run_dual_loop_overlap(state);
    FrontendRtlWork smoke{7, 0, 0, 2, 3, 64,
      {2, 3, 64, 1, 1, 64 / dim, 2, Mode::full, WorkKind::dense_hp1_final},
      std::vector<std::int8_t>(128, 1), std::vector<std::int8_t>(64 * 3, 1),
      {30, 1, 0x80000000U, 1, 0, 2}};
    for (std::size_t k = 32; k < 64; ++k) {
      smoke.weights[k * 3 + 1] = 2;
      smoke.weights[k * 3 + 2] = -1;
    }
    const std::vector<std::int32_t> smoke_expected = {
        INT32_MAX, 128, -128, INT32_MAX, 128, -128};
    std::vector<std::int32_t> smoke_output;
    FrontendRtlTiming fast_timing{};
    execute(&state, smoke, smoke_output, fast_timing);
    if (smoke_output != smoke_expected) {
      std::cerr << "K64 saturation/zero-replace smoke mismatch actual=";
      for (const auto value : smoke_output) std::cerr << value << ',';
      std::cerr << '\n';
    }
    check(smoke_output == smoke_expected, "integrated K64 smoke output mismatch");
    state.read_latency = 11;
    state.reorder_latency = 31;
    state.scale_latency = 29;
    state.write_latency = 37;
    state.read_ready_period = 3;
    FrontendRtlTiming slow_timing{};
    execute(&state, smoke, smoke_output, slow_timing);
    check(smoke_output == smoke_expected &&
              slow_timing.done_cycle - slow_timing.start_cycle >
                  fast_timing.done_cycle - fast_timing.start_cycle,
          "backing latency did not increase the RTL endpoint interval");
    state.read_latency = 3;
    state.reorder_latency = 13;
    state.scale_latency = 17;
    state.write_latency = 11;
    state.read_ready_period = 5;
    run_frontend_ws_rtl_fixture(capability, &state, execute);
    check(state.dut.io_loadRequests == state.dut.io_loadResponses &&
              state.dut.io_storeRequests == state.dut.io_storeResponses &&
              state.dut.io_scaleReadRequests == state.scale_reads &&
              state.dut.io_scaleReadResponses == state.scale_responses &&
              state.dut.io_scaleReadBytes == state.scale_reads * acc_bytes,
          "integrated memory request/response counters do not balance");
    check(state.pipeline_activation_bases == std::vector<std::uint64_t>{
              a_base, a_base + a_slot_stride, a_base},
          "PIPELINE activation backing slots did not cycle 0->1->0");
    run_rmd_ws_rtl_fixture(capability, &state, execute_raw);
    RmdRawWork raw_boundary;
    raw_boundary.plan = {1, 1, 32, 1, 1, (32 + dim - 1) / dim,
                         1, Mode::full, WorkKind::rmd_raw};
    const auto minimum_operand = static_cast<std::int8_t>(-(1 << (IM2P_OPERAND_BITS - 1)));
    raw_boundary.activations.assign(32, minimum_operand);
    raw_boundary.weights.assign(32, minimum_operand);
    std::vector<std::int32_t> raw_boundary_output;
    std::uint64_t raw_boundary_cycles = 0;
    check(execute_raw(&state, raw_boundary, raw_boundary_output, raw_boundary_cycles) == IM2P_OK &&
              raw_boundary_output == std::vector<std::int32_t>{32 * minimum_operand * minimum_operand},
          "RMD K32 extremal dot narrowed or saturated");
    std::fill(raw_boundary.activations.begin(), raw_boundary.activations.end(), 0);
    check(execute_raw(&state, raw_boundary, raw_boundary_output, raw_boundary_cycles) == IM2P_OK &&
              raw_boundary_output == std::vector<std::int32_t>{0},
          "RMD all-zero first contribution did not replace prior accumulator");
    std::cout << "WS_RMD_RAW_BOUNDARY compact_k=32 extremal_exact=1 zero_replace=1\n";
    run_bound_rmd_ws_rtl_fixture(capability, &state, execute, execute_raw);
    std::cout << "integrated upstream WS HP1 RTL passed A" << IM2P_OPERAND_BITS
              << "W" << IM2P_OPERAND_BITS << "D" << IM2P_DIM << " loops=" << state.loops
              << " load_execute_overlap=" << state.overlap << '\n';
    dut.final();
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "integrated WS RTL test failed: " << error.what() << '\n';
    return 1;
  }
}
#endif
