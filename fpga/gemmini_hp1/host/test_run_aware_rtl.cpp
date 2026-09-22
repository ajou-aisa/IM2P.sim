#define main legacy_ws_rtl_main
#include "test_ws_rtl.cpp"
#undef main
#include "../../../sim/common/gemmini_schedule.hpp"
#include "quants/common/hp1_scu.hpp"

#include <cstdlib>
#include <fstream>
#include <limits>
#include <VIM2PGemminiWSHP1RtlTest___024root.h>

#define IM2P_ROOT_SYMBOL_INNER(top, suffix) top##__DOT__control__DOT__##suffix
#define IM2P_ROOT_SYMBOL(top, suffix) IM2P_ROOT_SYMBOL_INNER(top, suffix)

namespace {
namespace schedule = im2p::gemmini;
std::ofstream events;
unsigned observed_case = 0;
std::uint32_t observed_block = 0, observed_generation = 0;
std::size_t observed_compact_k = 0, observed_compact_count = 0;

void observe(Adapter &state) {
  auto &dut = state.dut;
  const auto emit = [&](const char *kind, std::uint64_t a = 0,
                        std::uint64_t b = 0, std::uint64_t c = 0) {
    events << observed_case << ',' << dut.io_coreCycle << ',' << kind << ','
           << a << ',' << b << ',' << c << '\n';
  };
  if (dut.io_work_valid && dut.io_work_ready) {
    emit("work", dut.io_work_bits_maxI, dut.io_work_bits_maxJ,
         dut.io_work_bits_maxK);
    events << "LOOP," << observed_case << ',' << dut.io_coreCycle << ','
           << unsigned(dut.io_work_bits_firstLoop) << ','
           << unsigned(dut.io_work_bits_finalLoop) << ','
           << unsigned(dut.io_work_bits_accumulate) << ','
           << dut.io_work_bits_fragmentBase << ','
           << unsigned(dut.io_work_bits_hostSlot) << ','
           << dut.io_work_bits_scaleBase << ',' << observed_generation << ','
           << observed_block << ',' << observed_compact_k << ','
           << observed_compact_count << '\n';
  }
  if (dut.io_events_loadIssued) emit("load_issue");
  if (dut.io_events_executeIssued)
    emit("execute_issue", dut.rootp->IM2P_ROOT_SYMBOL(
                              IM2P_RTL_SELECTED_TOP,
                              _reservation_io_issue_ex_cmd_cmd_inst_funct));
  if (dut.io_events_storeIssued) emit("store_issue");
  if (dut.io_events_outputContextIssued) emit("context");
  if (dut.io_events_loadDmaAccepted)
    emit("load_dma", dut.io_events_loadLocalAddressRaw,
         dut.io_events_loadColumnsElements, dut.io_events_loadVaddrBytes);
  if (dut.io_readRequest_valid && dut.io_readRequest_ready)
    emit("read_request", dut.io_readRequest_bits_id,
         dut.io_readRequest_bits_address);
  if (dut.io_readBeat_valid && dut.io_readBeat_ready)
    emit("read_response", dut.io_readBeat_bits_id);
  if (dut.io_events_scratchpadReadAcceptedBankMask)
    emit("scratchpad_read", dut.io_events_scratchpadReadAcceptedBankMask);
  if (dut.rootp->IM2P_ROOT_SYMBOL(
          IM2P_RTL_SELECTED_TOP,
          execute__DOT__mesh__DOT__input_next_row_into_spatial_array))
    emit("array_input",
         dut.rootp->IM2P_ROOT_SYMBOL(
             IM2P_RTL_SELECTED_TOP, execute__DOT__mesh__DOT__matmul_id),
         dut.rootp->IM2P_ROOT_SYMBOL(
             IM2P_RTL_SELECTED_TOP, execute__DOT__mesh__DOT__fire_counter));
  if (dut.rootp->IM2P_ROOT_SYMBOL(IM2P_RTL_SELECTED_TOP,
                                  _execute_io_completed_valid))
    emit("raw_completed",
         dut.rootp->IM2P_ROOT_SYMBOL(IM2P_RTL_SELECTED_TOP,
                                     _execute_io_completed_bits));
  if (dut.io_events_rawRow) emit("array_output");
  if (dut.io_events_scaledRow) emit("accumulator_write");
  if (dut.io_events_accumulatorCommitted) emit("accumulator_commit");
  if (dut.io_events_storeDmaAccepted)
    emit("store_dma", dut.io_events_storeLocalAddressRaw,
         dut.io_events_storeLengthElements);
  if (dut.io_writeRequest_valid && dut.io_writeRequest_ready)
    emit("write_request", dut.io_writeRequest_bits_id,
         dut.io_writeRequest_bits_address);
  if (dut.io_writeCompletion_valid && dut.io_writeCompletion_ready)
    emit("write_completion", dut.io_writeCompletion_bits_id);
  if (dut.io_loopDone_valid && dut.io_loopDone_ready) emit("loop_done");
  if (dut.io_logicalDone_valid) emit("logical_done");
  if (dut.io_scaleRelease_valid && dut.io_scaleRelease_ready)
    emit("scale_release", dut.io_scaleRelease_bits_address,
         dut.io_scaleRelease_bits_column, dut.io_scaleRelease_bits_generation);
}

void run_loop(Adapter &state, const RmdRunWork &work,
              const schedule::LoopPlan &loop, std::size_t run,
              std::uint32_t generation, bool stale_generation = false) {
  const auto slot = work.host_slot;
  state.active_slot = slot;
  state.a[slot].assign(loop.activation_packed_bytes, 0);
  state.b[slot].assign(loop.weight_packed_bytes, 0);
  state.s[slot].assign(loop.scale_packed_bytes, 0);
  for (std::size_t i = 0; i < loop.is; ++i)
    for (std::size_t k = 0; k < loop.ks; ++k)
      put_operand(state.a[slot], i * loop.kp + k,
                  work.activations.at((loop.i + i) * work.plan.k + loop.k + k));
  for (std::size_t k = 0; k < loop.ks; ++k)
    for (std::size_t j = 0; j < loop.js; ++j)
      put_operand(state.b[slot], k * loop.jp + j,
                  work.weights.at((loop.k + k) * work.plan.n + loop.j + j));
  for (std::size_t row = 0; row < loop.scale_rows; ++row)
    for (std::size_t lane = 0; lane < dim && row * dim + lane < loop.js;
         ++lane) {
      const auto carrier = work.carriers.at(run * work.plan.n + loop.j +
                                             row * dim + lane);
      for (std::size_t byte = 0; byte < 4; ++byte)
        state.s[slot][row * acc_bytes + lane * 4 + byte] =
            static_cast<std::uint8_t>(carrier >> (byte * 8));
    }
  auto &dut = state.dut;
  dut.io_work_bits_maxI = loop.ip / dim;
  dut.io_work_bits_maxJ = loop.jp / dim;
  dut.io_work_bits_maxK = loop.kp / dim;
  dut.io_work_bits_padI = loop.ip - loop.is;
  dut.io_work_bits_padJ = loop.jp - loop.js;
  dut.io_work_bits_padK = loop.kp - loop.ks;
  dut.io_work_bits_aAddress = slot_address(a_base, slot);
  dut.io_work_bits_bAddress = slot_address(b_base, slot);
  dut.io_work_bits_cAddress = loop.final_contribution
                                  ? slot_address(c_base, slot) +
                                        loop.i * state.c_stride[slot] + loop.j * 4
                                  : 0;
  dut.io_work_bits_scaleBackingAddress = slot_address(s_base, slot);
  dut.io_work_bits_aStrideBytes = loop.kp * IM2P_OPERAND_BITS / 8;
  dut.io_work_bits_bStrideBytes = loop.jp * IM2P_OPERAND_BITS / 8;
  dut.io_work_bits_cStrideBytes = state.c_stride[slot];
  dut.io_work_bits_scaleBase = slot * 128;
  dut.io_work_bits_scaleGeneration = generation;
  dut.io_work_bits_fragmentBase = loop.fragment_base;
  dut.io_work_bits_workBase = slot * 64;
  dut.io_work_bits_accumulate = loop.accumulate;
  dut.io_work_bits_finalFragment = loop.final_contribution;
  dut.io_work_bits_firstLoop = loop.first;
  dut.io_work_bits_finalLoop = loop.last;
  dut.io_work_bits_logicalWorkId = work.work_id & 255U;
  dut.io_work_bits_hostSlot = slot;
  dut.io_work_bits_rmdRaw = 0;
  observed_block = loop.original_block_id;
  observed_generation = generation;
  observed_compact_k = loop.k;
  observed_compact_count = loop.ks;
  dut.io_work_valid = 1;
  state.accept([&] { return dut.io_work_ready; }, "run descriptor stalled");
  dut.io_work_valid = 0;
  state.accept([&] { return dut.io_loopDone_valid; }, "run loop stalled");
  check(state.reads.empty() && state.writes.empty(),
        "run loop left backing traffic pending");
  if (stale_generation) {
    dut.io_scaleRelease_bits_column = 0;
    dut.io_scaleRelease_bits_address = slot * 128;
    dut.io_scaleRelease_bits_generation = generation + 1;
    dut.io_scaleRelease_valid = 1;
    try {
      state.step();
    } catch (const std::runtime_error &) {
      dut.io_scaleRelease_valid = 0;
      check(dut.io_error, "stale generation failed for unrelated reason");
      return;
    }
    dut.io_scaleRelease_valid = 0;
    dut.eval();
    check(dut.io_error, "stale scale generation was accepted");
    return;
  }
  state.release_scales(loop.js, loop.k, loop.k + loop.ks, 0, generation,
                       slot * 128);
}

int execute_runs_impl(void *opaque, const RmdRunWork &work,
                      std::vector<std::int32_t> &output,
                      std::uint64_t &cycles, bool stale_generation) {
  auto &state = *static_cast<Adapter *>(opaque);
  const auto m = work.plan.m, n = work.plan.n, k = work.plan.k;
  if (!m || !n || !k || work.host_slot > 1 ||
      work.plan.kind != WorkKind::dense_hp1_final ||
      work.activations.size() != m * k || work.weights.size() != k * n ||
      work.carriers.size() != work.runs.size() * n ||
      work.geometry.tile_i_count != work.plan.tile_i ||
      work.geometry.tile_j_count != work.plan.tile_j ||
      work.geometry.tile_k_count != work.plan.tile_k)
    return IM2P_INVALID_LAYOUT;
  schedule::ScheduleConfig config{{m, n, k}, {dim, IM2P_OPERAND_BITS},
                                  {work.plan.tile_i, work.plan.tile_j,
                                   work.plan.tile_k, m},
                                  {}, true, true, false};
  const im2p_compact_runs_t view{IM2P_COMPACT_RUNS_VERSION, sizeof(view),
                                 work.original_k, work.runs.size(),
                                 work.runs.data()};
  if (!schedule::set_compact_runs(config, &view) ||
      !std::all_of(work.carriers.begin(), work.carriers.end(),
                   ggml::gemmini::quants::hp1::valid_carrier))
    return IM2P_INVALID_LAYOUT;
  const auto slot = work.host_slot;
  state.active_slot = slot;
  state.rows[slot] = m;
  state.columns[slot] = n;
  state.c_stride[slot] = padded(n) * 4;
  state.c[slot].assign(padded(m) * state.c_stride[slot], 0xA5);
  state.written[slot].assign(state.c[slot].size(), 0);
  state.output_ids.assign(padded(m) / dim * (padded(n) / dim), false);
  const auto old_done = state.logical_done;
  const auto old_loops = state.loops;
  schedule::LoopCursor cursor{};
  std::uint32_t generation = 1;
  std::size_t expected_loops = 0;
  while (cursor.i < m) {
    const auto loop = schedule::plan_loop(config, m, cursor);
    const auto it = std::find_if(config.runs.begin(), config.runs.end(),
                                 [&](const auto &run) {
                                   return run.original_block_id ==
                                          loop.original_block_id;
                                 });
    check(it != config.runs.end(), "loop lost original block owner");
    const auto run = static_cast<std::size_t>(it - config.runs.begin());
    run_loop(state, work, loop, run, generation, stale_generation);
    if (stale_generation)
      return IM2P_ERROR;
    std::cout << "FIXTURE_ONLY loop=" << expected_loops
              << " original_block=" << loop.original_block_id
              << " compact_k=" << loop.k << "+" << loop.ks
              << " fragment_base=" << loop.fragment_base
              << " generation=" << generation
              << " carrier=" << work.carriers.at(run * n + loop.j) << '\n';
    schedule::advance_loop(config, loop, cursor);
    generation = generation % 255 + 1;
    ++expected_loops;
  }
  check(state.loops - old_loops == expected_loops &&
            state.logical_done - old_done == 1 &&
            state.logical_done_ids.back() == (work.work_id & 255U),
        "run work did not complete exactly once");
  check(std::all_of(state.output_ids.begin(), state.output_ids.end(),
                    [](bool done) { return done; }),
        "run work did not publish every output tile");
  output.clear();
  for (std::size_t i = 0; i < m; ++i)
    for (std::size_t j = 0; j < n; ++j) {
      std::uint32_t raw = 0;
      for (std::size_t byte = 0; byte < 4; ++byte)
        raw |= std::uint32_t{state.c[slot][i * state.c_stride[slot] + j * 4 +
                                             byte]}
               << (byte * 8);
      std::int32_t value;
      std::memcpy(&value, &raw, sizeof(value));
      output.push_back(value);
    }
  cycles = state.dut.io_elapsedCycles;
  check(state.dut.io_measurementValid && cycles > 0 &&
            state.dut.io_doneCycle - state.dut.io_startCycle == cycles,
        "run work has invalid RTL cycle endpoints");
  return IM2P_OK;
}

int execute_runs(void *opaque, const RmdRunWork &work,
                 std::vector<std::int32_t> &output, std::uint64_t &cycles) {
  return execute_runs_impl(opaque, work, output, cycles, false);
}

RmdRunWork fixture(std::uint32_t second_block, std::uint32_t first_count,
                   std::uint32_t second_count) {
  RmdRunWork work{};
  const auto k = first_count + second_count;
  work.work_id = 71 + second_block + first_count;
  work.host_slot = 0;
  work.plan = {1, 1, k, 1, 1, 2, 1, Mode::full,
               WorkKind::dense_hp1_final};
  work.geometry.tile_i_count = 1;
  work.geometry.tile_j_count = 1;
  work.geometry.tile_k_count = 2;
  work.original_k = second_block * 32 + second_count;
  work.runs = {{0, (std::uint32_t{1} << first_count) - 1, 0, first_count},
               {second_block, (std::uint32_t{1} << second_count) - 1,
                first_count, second_count}};
  work.activations.assign(k, 1);
  work.weights.assign(k, 1);
  work.carriers = {0, 1};
  return work;
}

std::int32_t oracle(const RmdRunWork &work) {
  const auto sat32 = [](std::int64_t value) {
    return static_cast<std::int32_t>(std::clamp(
        value, std::int64_t{INT32_MIN}, std::int64_t{INT32_MAX}));
  };
  std::int32_t sum = 0;
  for (std::size_t run = 0; run < work.runs.size(); ++run) {
    const auto &span = work.runs[run];
    for (std::size_t start = 0; start < span.compact_k_count;
         start += std::min<std::size_t>(dim, 32)) {
      std::int64_t dot = 0;
      for (std::size_t offset = start;
           offset < std::min<std::size_t>(span.compact_k_count,
                                          start + std::min<std::size_t>(dim, 32));
           ++offset) {
        const auto k = span.compact_k_begin + offset;
        dot += std::int64_t{work.activations.at(k)} * work.weights.at(k);
      }
      const auto carrier = work.carriers.at(run);
      const auto scaled = carrier == 0x80000000U || dot == 0
                              ? 0
                          : carrier >= 32
                              ? (dot < 0 ? std::int64_t{INT32_MIN}
                                         : std::int64_t{INT32_MAX})
                              : dot * (std::int64_t{1} << carrier);
      sum = sat32(std::int64_t{sum} + sat32(scaled));
    }
  }
  return sum;
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
    for (unsigned i = 0; i < 5; ++i)
      state.clock();
    dut.reset = 0;
    state.clock();
    auto work = fixture(1, 12, 10);
    if (argc == 2 && std::string{argv[1]} == "--stale-generation") {
      std::vector<std::int32_t> output{999};
      std::uint64_t cycles = 0;
      check(execute_runs_impl(&state, work, output, cycles, true) == IM2P_ERROR &&
                output == std::vector<std::int32_t>{999},
            "stale scale generation published output");
      return 0;
    }
    if (argc == 2 && std::string{argv[1]} == "--invalid-run-owner") {
      work.original_k = 32;
      std::vector<std::int32_t> output{999};
      std::uint64_t cycles = 0;
      check(execute_runs(&state, work, output, cycles) == IM2P_INVALID_LAYOUT &&
                output == std::vector<std::int32_t>{999},
            "invalid run owner was published");
      return 0;
    }
    if (argc == 2 && std::string{argv[1]} == "--invalid-run-mask") {
      work.runs[1].original_k_mask = 1;
      std::vector<std::int32_t> output{999};
      std::uint64_t cycles = 0;
      check(execute_runs(&state, work, output, cycles) == IM2P_INVALID_LAYOUT &&
                output == std::vector<std::int32_t>{999},
            "invalid run mask published output");
      return 0;
    }
    const std::string selected_case = argc == 3 && std::string{argv[1]} == "--case"
                                          ? argv[2]
                                          : "";
    check(argc == 1 || !selected_case.empty(), "expected --case <name>");
    std::vector<std::int32_t> output;
    std::uint64_t cycles = 0;
    const RmdRunExecute callback = execute_runs;
    if (const auto *path = std::getenv("IM2P_RUN_AWARE_EVENTS"); path && *path) {
      events.open(path, selected_case.empty() ? std::ios::out : std::ios::app);
      check(events.is_open(), "run-aware event output could not be opened");
      state.event_observer = observe;
    }
    unsigned executed = 0;
    const auto run_case = [&](unsigned case_index, const char *name,
                              const RmdRunWork &case_work) {
      if (!selected_case.empty() && selected_case != name)
        return;
      ++executed;
      if (events.is_open()) {
        observed_case = case_index;
        events << "CASE," << observed_case << ',' << name << ','
               << case_work.plan.m << ',' << case_work.plan.n << ','
               << case_work.plan.k << ',' << case_work.original_k << ','
               << case_work.plan.tile_i << ',' << case_work.plan.tile_j << ','
               << case_work.plan.tile_k << ',' << state.read_latency << ','
               << state.reorder_latency << ',' << state.scale_latency << ','
               << state.write_latency << ',' << state.read_ready_period << ','
               << state.cycle - state.dut.io_coreCycle << '\n';
      }
      const auto expected = oracle(case_work);
      const auto old_done = state.logical_done;
      const auto old_loops = state.loops;
      const auto old_loads = state.loads;
      const auto old_executes = state.executes;
      const auto old_stores = state.stores;
      const auto old_commits = state.commits;
      const auto old_scale_reads = state.scale_reads;
      const auto old_scale_responses = state.scale_responses;
      const auto old_completions = state.completions;
      const auto status = callback(&state, case_work, output, cycles);
      if (status != IM2P_OK || output != std::vector<std::int32_t>{expected})
        throw std::runtime_error(std::string{name} + " RTL numerical mismatch: status=" +
                                 std::to_string(status) + " expected=" +
                                 std::to_string(expected) + " actual=" +
                                 (output.empty() ? "empty" : std::to_string(output.front())));
      std::cout << "FIXTURE_ONLY case=" << name
                << " profile=a" << IM2P_ACTIVATION_BITS << "w"
                << IM2P_OPERAND_BITS << "-d" << dim << "-hp1"
                << " attempted=1 admitted=1 exact=1"
                << " expected=" << expected << " actual=" << output.front()
                << " logical_done=" << state.logical_done - old_done
                << " start=" << state.dut.io_startCycle
                << " done=" << state.dut.io_doneCycle
                << " cycles=" << cycles
                << " loops=" << state.loops - old_loops
                << " loads=" << state.loads - old_loads
                << " executes=" << state.executes - old_executes
                << " stores=" << state.stores - old_stores
                << " commits=" << state.commits - old_commits
                << " scale_reads=" << state.scale_reads - old_scale_reads
                << " scale_responses=" << state.scale_responses - old_scale_responses
                << " completions=" << state.completions - old_completions
                << '\n';
    };
    work = fixture(1, 12, 10);
    run_case(1, "unequal_12_10", work);
    work = fixture(3, 12, 10);
    run_case(2, "gap_0_3", work);
    work = fixture(1, 31, 1);
    run_case(3, "boundary_31_1", work);
    work = fixture(1, 12, 10);
    work.carriers = {0x80000000U, 1};
    run_case(4, "zero_sentinel", work);
    work.carriers = {32767, 0};
    run_case(5, "high_exponent", work);
    work.carriers = {31, 31};
    run_case(6, "positive_sat32", work);
    std::fill(work.activations.begin(), work.activations.end(), -1);
    run_case(7, "negative_sat32", work);
    std::fill_n(work.activations.begin(), 12, 1);
    run_case(8, "sat32_order", work);
    check(executed == (selected_case.empty() ? 8U : 1U),
          "unknown or incomplete run-aware case");
    dut.final();
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "run-aware RTL failed: " << error.what() << '\n';
    return 1;
  }
}
