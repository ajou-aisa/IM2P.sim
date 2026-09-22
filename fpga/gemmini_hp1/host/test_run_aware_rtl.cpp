#define main legacy_ws_rtl_main
#include "test_ws_rtl.cpp"
#undef main
#include <cstdlib>
#include <charconv>
#include <filesystem>
#include <fstream>
#include <limits>
#include <sstream>
#include <VIM2PGemminiWSHP1RtlTest___024root.h>

#define IM2P_ROOT_SYMBOL_INNER(top, suffix) top##__DOT__control__DOT__##suffix
#define IM2P_ROOT_SYMBOL(top, suffix) IM2P_ROOT_SYMBOL_INNER(top, suffix)

namespace {
std::ofstream events;
unsigned observed_case = 0;

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

template <typename T>
std::vector<T> read_numbers(std::istream &input, const char *label,
                            std::size_t count) {
  check(count <= 1'000'000, "production fixture count exceeds limit");
  std::string line, token;
  check(static_cast<bool>(std::getline(input, line)),
        "production fixture line is missing");
  std::istringstream fields(line);
  if (*label) {
    check(static_cast<bool>(fields >> token) && token == label,
          "production fixture label mismatch");
  }
  std::vector<T> values;
  values.reserve(count);
  for (std::size_t i = 0; i < count; ++i) {
    check(static_cast<bool>(fields >> token),
          "production fixture value is missing");
    T value{};
    const auto parsed = std::from_chars(token.data(), token.data() + token.size(), value);
    check(parsed.ec == std::errc{} && parsed.ptr == token.data() + token.size(),
          "production fixture value is invalid");
    values.push_back(value);
  }
  check(!(fields >> token), "production fixture has extra values");
  return values;
}

std::size_t fixture_size(std::uint64_t left, std::uint64_t right) {
  check(left && right && left <= 1'000'000 / right,
        "production fixture shape exceeds limit");
  return static_cast<std::size_t>(left * right);
}

struct ProductionCase {
  RmdRunWork work;
  std::vector<std::int32_t> expected;
};

ProductionCase load_production_case(const char *path) {
  std::ifstream input(path);
  check(input.is_open(), "production fixture could not be opened");
  std::string version;
  check(static_cast<bool>(std::getline(input, version)) &&
            version == "RMD_RUN_WORK_V1",
        "production fixture version mismatch");
  const auto d = read_numbers<std::uint64_t>(input, "descriptor", 23);
  const auto g = read_numbers<std::uint64_t>(input, "geometry", 16);
  const auto v = read_numbers<std::uint64_t>(input, "runs", 4);
  check(d[0] == IM2P_ABI_VERSION && d[1] == IM2P_ACTIVATION_BITS &&
            d[2] == 1 && d[3] == IM2P_OPERAND_BITS && d[4] == 1 &&
            d[5] == dim && d[6] && d[7] && d[8] &&
            d[6] <= UINT32_MAX && d[7] <= UINT32_MAX && d[8] <= UINT32_MAX &&
            d[9] >= d[8] && d[10] >= d[7] && d[11] >= d[7] &&
            d[12] && d[13] && d[14] == 32 && d[15] <= UINT32_MAX &&
            d[16] == d[7] && d[17] == 0 && d[18] == d[7] &&
            d[20] == IM2P_VECTOR_LEFT_SHIFT &&
            d[21] == IM2P_OUTPUT_SCU_FINAL &&
            g[0] == IM2P_PRODUCTION_GEOMETRY_VERSION &&
            g[1] == sizeof(im2p_production_geometry_v1_t) &&
            g[2] == d[1] && g[3] == d[3] && g[4] == d[5] &&
            g[5] == IM2P_GEOMETRY_FULL && g[6] == d[6] &&
            g[7] == d[7] && g[8] == d[8] &&
            g[9] && g[10] && g[11] && g[12] == d[6] &&
            g[13] == 0 && g[14] == d[6] && g[15] == 0 &&
            v[0] == IM2P_COMPACT_RUNS_VERSION &&
            v[1] == sizeof(im2p_compact_runs_t) && v[2] == d[15] &&
            v[3] && v[3] <= d[8],
        "production fixture metadata mismatch");
  const auto a_count = fixture_size(d[6], d[8]);
  const auto b_count = fixture_size(d[8], d[7]);
  const auto carrier_count = fixture_size(v[3], d[7]);
  const auto output_count = fixture_size(d[6], d[7]);
  check(d[19] == carrier_count, "production fixture carrier count mismatch");
  ProductionCase result;
  auto &work = result.work;
  work.work_id = 0;
  work.host_slot = 0;
  work.plan = {d[6], d[7], d[8], g[9], g[10], g[11], d[6],
               Mode::full, WorkKind::dense_hp1_final};
  work.geometry = {static_cast<std::uint32_t>(g[0]),
                   static_cast<std::uint32_t>(g[1]),
                   static_cast<std::uint32_t>(g[2]),
                   static_cast<std::uint32_t>(g[3]),
                   static_cast<std::uint32_t>(g[4]),
                   static_cast<std::uint32_t>(g[5]),
                   g[6], g[7], g[8], g[9], g[10], g[11],
                   g[12], g[13], g[14], g[15]};
  work.original_k = static_cast<std::uint32_t>(v[2]);
  for (std::size_t i = 0; i < v[3]; ++i) {
    const auto r = read_numbers<std::uint64_t>(input, "run", 4);
    check(std::all_of(r.begin(), r.end(),
                      [](auto value) { return value <= UINT32_MAX; }),
          "production fixture run value exceeds uint32");
    work.runs.push_back({static_cast<std::uint32_t>(r[0]),
                         static_cast<std::uint32_t>(r[1]),
                         static_cast<std::uint32_t>(r[2]),
                         static_cast<std::uint32_t>(r[3])});
  }
  check(read_numbers<std::uint64_t>(input, "ROW_MAP", 1).front() == d[6],
        "production fixture row map length mismatch");
  for (std::size_t i = 0; i < d[6]; ++i)
    (void)read_numbers<std::uint64_t>(input, "", 2);
  const auto read_signed = [&](const char *label, std::size_t count) {
    check(read_numbers<std::uint64_t>(input, label, 1).front() == count,
          "production fixture tensor length mismatch");
    return read_numbers<std::int64_t>(input, "", count);
  };
  const auto a = read_signed("A", a_count);
  const auto b = read_signed("B", b_count);
  const auto min_code = -(std::int64_t{1} << (IM2P_ACTIVATION_BITS - 1));
  for (const auto value : a) {
    check(value >= min_code && value < -min_code,
          "production fixture activation code out of range");
    work.activations.push_back(static_cast<std::int8_t>(value));
  }
  for (const auto value : b) {
    check(value >= min_code && value < -min_code,
          "production fixture weight code out of range");
    work.weights.push_back(static_cast<std::int8_t>(value));
  }
  check(read_numbers<std::uint64_t>(input, "CARRIERS", 1).front() == carrier_count,
        "production fixture carrier length mismatch");
  for (const auto value : read_numbers<std::uint64_t>(input, "", carrier_count)) {
    check(value <= UINT32_MAX, "production fixture carrier exceeds uint32");
    work.carriers.push_back(static_cast<std::uint32_t>(value));
  }
  for (const auto value : read_signed("OUTPUT", output_count)) {
    check(value >= INT32_MIN && value <= INT32_MAX,
          "production fixture output exceeds int32");
    result.expected.push_back(static_cast<std::int32_t>(value));
  }
  std::string extra;
  check(!std::getline(input, extra), "production fixture has trailing lines");
  return result;
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
    const bool production_case =
        argc == 3 && std::string{argv[1]} == "--production-case";
    const std::string selected_case = argc == 3 && std::string{argv[1]} == "--case"
                                          ? argv[2]
                                          : "";
    check(argc == 1 || !selected_case.empty() || production_case,
          "expected --case <name> or --production-case <file>");
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
                              const RmdRunWork &case_work,
                              const std::vector<std::int32_t> *captured = nullptr) {
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
      const std::vector<std::int32_t> expected =
          captured ? *captured : std::vector<std::int32_t>{oracle(case_work)};
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
      if (status != IM2P_OK || output != expected)
        throw std::runtime_error(std::string{name} + " RTL numerical mismatch: status=" +
                                 std::to_string(status) + " expected=" +
                                 (expected.empty() ? "empty" : std::to_string(expected.front())) + " actual=" +
                                 (output.empty() ? "empty" : std::to_string(output.front())));
      std::cout << (captured ? "PRODUCTION_RUN case=" : "FIXTURE_ONLY case=") << name
                << " profile=a" << IM2P_ACTIVATION_BITS << "w"
                << IM2P_OPERAND_BITS << "-d" << dim << "-hp1"
                << " attempted=1 admitted=1 exact=1"
                << " m=" << case_work.plan.m << " n=" << case_work.plan.n
                << " k=" << case_work.plan.k
                << " original_k=" << case_work.original_k
                << " runs=" << case_work.runs.size()
                << " first_block=" << case_work.runs.front().original_block_id
                << " last_block=" << case_work.runs.back().original_block_id
                << " expected=" << expected.front() << " actual=" << output.front()
                << " expected_values=" << expected.size()
                << " actual_values=" << output.size()
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
    if (production_case) {
      run_loop_label = "PRODUCTION_RUN_LOOP loop=";
      const auto captured = load_production_case(argv[2]);
      std::string name = std::filesystem::path(argv[2]).stem().string();
      const std::string prefix = "rmd-run-work-";
      if (name.starts_with(prefix))
        name.erase(0, prefix.size());
      std::replace(name.begin(), name.end(), '-', '_');
      name.insert(0, "production_");
      run_case(9, name.c_str(), captured.work, &captured.expected);
      check(executed == 1, "production fixture did not execute exactly once");
      dut.final();
      return 0;
    }
    run_loop_label = "FIXTURE_ONLY loop=";
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
