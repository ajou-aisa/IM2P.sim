#if !defined(IM2P_RTL_TEST_BUILD) || !__has_include(<VIM2PGemminiWSHP1RtlTest.h>)
int im2p_production_sequence_requires_generated_model;
#else
#define IM2P_SERVICE_PROBE_NO_MAIN
#include "service_boundary_probe.cpp"
#undef IM2P_SERVICE_PROBE_NO_MAIN
#include <VIM2PGemminiWSHP1RtlTest___024root.h>
#include <fstream>
#include <sstream>
#include <string_view>

#define IM2P_SEQUENCE_TAP_(top, suffix) top##__DOT__control__DOT__##suffix
#define IM2P_SEQUENCE_TAP(top, suffix) IM2P_SEQUENCE_TAP_(top, suffix)
#define IM2P_TAG_REG(index, field) IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP, execute__DOT__mesh__DOT__tagq__DOT__regs_##index##_##field)

namespace {
struct WorkInput {
  std::uint64_t ordinal{}, id{}, slot{}, phase{}, parent{}, call{}, stripe{}, row_begin{};
  std::uint64_t parent_m{}, m{}, n{}, k{}, ti{}, tj{}, tk{};
  std::uint64_t a_stride{}, b_stride{}, c_stride{}, s_stride{}, original_k{};
  char kind{};
  std::string binding;
  std::vector<im2p_compact_run_t> runs;
};

std::vector<WorkInput> read_work(const char *path, unsigned &period) {
  std::ifstream input(path);
  check(bool(input), "projection manifest missing");
  std::string token, profile;
  input >> token;
  check(token == "IM2P_PRODUCTION_SEQUENCE_V1", "projection revision differs");
  input >> token >> profile;
  check(token == "PROFILE" && profile ==
            "a" + std::to_string(IM2P_ACTIVATION_BITS) + "w" +
                std::to_string(IM2P_OPERAND_BITS) + "-d" + std::to_string(dim) + "-hp1",
        "projection profile differs from generated RTL");
  input >> token >> period;
  check(token == "PERIOD" && period == 5, "unsupported reference-memory period");
  unsigned count = 0;
  input >> token >> count;
  check(token == "WORKS" && count == 4, "finite producer work count differs");
  std::vector<WorkInput> works;
  for (unsigned ordinal = 0; ordinal < count; ++ordinal) {
    WorkInput w{};
    unsigned run_count = 0;
    input >> token >> w.ordinal >> w.id >> w.kind >> w.slot >> w.phase >> w.parent >> w.call >>
        w.stripe >> w.row_begin >> w.parent_m >> w.m >> w.n >> w.k >> w.ti >> w.tj >> w.tk >>
        w.a_stride >> w.b_stride >> w.c_stride >> w.s_stride >> w.original_k >> w.binding >> run_count;
    check(bool(input) && token == "W" && w.ordinal == ordinal && w.id == ordinal &&
              (w.kind == 'D' || w.kind == 'R') && w.slot < 2 && w.phase < period &&
              w.m && w.n && w.k && w.m <= 1024 && w.n <= 1024 && w.k <= 1024 &&
              w.ti && w.tj && w.tk && run_count <= 32 &&
              ((w.kind == 'R') == (run_count > 0)), "malformed/broad producer work");
    for (unsigned i = 0; i < run_count; ++i) {
      im2p_compact_run_t run{};
      input >> token >> run.original_block_id >> run.original_k_mask >>
          run.compact_k_begin >> run.compact_k_count;
      check(bool(input) && token == "R", "malformed compact run");
      w.runs.push_back(run);
    }
    works.push_back(w);
  }
  check(!(input >> token), "trailing projection records");
  return works;
}

unsigned current_ordinal = UINT32_MAX;
unsigned current_work_id = UINT32_MAX;
std::uint64_t accepted_final_fragments = 0;
std::uint64_t tag_enqueues = 0, tag_dequeues = 0;
unsigned prior_tag_read = 0, prior_tag_write = 0;
bool tag_initialized = false;
unsigned max_tag_queue_len = 0;
std::uint64_t tag_full_backpressure_cycles = 0;
std::uint64_t first_dequeue_cycle = 0, prior_response_cycle = 0;
unsigned first_dequeue_out_id = 0, first_dequeue_resp_valid = 0;
unsigned first_dequeue_resp_last = 0, first_dequeue_match_ready = 0;
unsigned prior_response_out_id = 0, prior_response_valid = 0;
unsigned prior_response_last = 0, prior_response_match_ready = 0;
bool first_dequeue_seen = false;
void sample_tag(const Dut &dut) {
  const auto *root = dut.rootp;
  const auto len = unsigned(root->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
      execute__DOT__mesh__DOT__tagq__DOT__len));
  if (current_ordinal != UINT32_MAX) {
    max_tag_queue_len = std::max(max_tag_queue_len, len);
    tag_full_backpressure_cycles += len == 6 &&
        root->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP, execute__DOT___GEN_40) &&
        !root->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP, execute__DOT___mesh_io_req_ready);
  }
  const auto read = unsigned(root->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
      execute__DOT__mesh__DOT__tagq__DOT__raddr));
  const auto write = unsigned(root->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
      execute__DOT__mesh__DOT__tagq__DOT__waddr));
  if (tag_initialized) {
    const auto dequeues = (read + 6 - prior_tag_read) % 6;
    const auto enqueues = (write + 6 - prior_tag_write) % 6;
    check(dequeues <= 1 && enqueues <= 1, "mesh tag pointer skipped a cycle");
    if (dequeues && current_ordinal != UINT32_MAX && !first_dequeue_seen) {
      first_dequeue_seen = true;
      first_dequeue_cycle = prior_response_cycle;
      first_dequeue_out_id = prior_response_out_id;
      first_dequeue_resp_valid = prior_response_valid;
      first_dequeue_resp_last = prior_response_last;
      first_dequeue_match_ready = prior_response_match_ready;
    }
    tag_dequeues += dequeues;
    tag_enqueues += enqueues;
  }
  prior_tag_read = read;
  prior_tag_write = write;
  tag_initialized = true;
  prior_response_cycle = dut.io_coreCycle;
  prior_response_out_id = root->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
      execute__DOT__mesh__DOT__out_matmul_id_RegShifted_0_0);
  prior_response_valid = root->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
      execute__DOT__mesh__DOT__io_resp_valid_RegShifted_0_0);
  prior_response_last = root->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
      execute__DOT__mesh__DOT__out_last_RegShifted_0_0);
  prior_response_match_ready = root->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
      execute__DOT__mesh__DOT___tagq_io_deq_ready_T_1);
}

struct TagHead {
  unsigned len, read, write, id, rob_valid, rob_bits, address;
  unsigned is_acc, accumulate, full_row, garbage;
};
TagHead tag_head(const Dut &dut) {
  sample_tag(dut);
  const auto *root = dut.rootp;
  const unsigned read = prior_tag_read;
  check(read < 6, "mesh tag read pointer outside queue");
  const std::array<unsigned, 6> ids = {root->IM2P_TAG_REG(0, id), root->IM2P_TAG_REG(1, id),
      root->IM2P_TAG_REG(2, id), root->IM2P_TAG_REG(3, id),
      root->IM2P_TAG_REG(4, id), root->IM2P_TAG_REG(5, id)};
  const std::array<unsigned, 6> valid = {root->IM2P_TAG_REG(0, tag_rob_id_valid),
      root->IM2P_TAG_REG(1, tag_rob_id_valid), root->IM2P_TAG_REG(2, tag_rob_id_valid),
      root->IM2P_TAG_REG(3, tag_rob_id_valid), root->IM2P_TAG_REG(4, tag_rob_id_valid),
      root->IM2P_TAG_REG(5, tag_rob_id_valid)};
  const std::array<unsigned, 6> rob = {root->IM2P_TAG_REG(0, tag_rob_id_bits),
      root->IM2P_TAG_REG(1, tag_rob_id_bits), root->IM2P_TAG_REG(2, tag_rob_id_bits),
      root->IM2P_TAG_REG(3, tag_rob_id_bits), root->IM2P_TAG_REG(4, tag_rob_id_bits),
      root->IM2P_TAG_REG(5, tag_rob_id_bits)};
  const std::array<unsigned, 6> address = {root->IM2P_TAG_REG(0, tag_addr_data),
      root->IM2P_TAG_REG(1, tag_addr_data), root->IM2P_TAG_REG(2, tag_addr_data),
      root->IM2P_TAG_REG(3, tag_addr_data), root->IM2P_TAG_REG(4, tag_addr_data),
      root->IM2P_TAG_REG(5, tag_addr_data)};
  const std::array<unsigned, 6> garbage = {root->IM2P_TAG_REG(0, tag_addr_garbage_bit),
      root->IM2P_TAG_REG(1, tag_addr_garbage_bit), root->IM2P_TAG_REG(2, tag_addr_garbage_bit),
      root->IM2P_TAG_REG(3, tag_addr_garbage_bit), root->IM2P_TAG_REG(4, tag_addr_garbage_bit),
      root->IM2P_TAG_REG(5, tag_addr_garbage_bit)};
  const std::array<unsigned, 6> is_acc = {root->IM2P_TAG_REG(0, tag_addr_is_acc_addr),
      root->IM2P_TAG_REG(1, tag_addr_is_acc_addr), root->IM2P_TAG_REG(2, tag_addr_is_acc_addr),
      root->IM2P_TAG_REG(3, tag_addr_is_acc_addr), root->IM2P_TAG_REG(4, tag_addr_is_acc_addr),
      root->IM2P_TAG_REG(5, tag_addr_is_acc_addr)};
  const std::array<unsigned, 6> accumulate = {root->IM2P_TAG_REG(0, tag_addr_accumulate),
      root->IM2P_TAG_REG(1, tag_addr_accumulate), root->IM2P_TAG_REG(2, tag_addr_accumulate),
      root->IM2P_TAG_REG(3, tag_addr_accumulate), root->IM2P_TAG_REG(4, tag_addr_accumulate),
      root->IM2P_TAG_REG(5, tag_addr_accumulate)};
  const std::array<unsigned, 6> full_row = {root->IM2P_TAG_REG(0, tag_addr_read_full_acc_row),
      root->IM2P_TAG_REG(1, tag_addr_read_full_acc_row), root->IM2P_TAG_REG(2, tag_addr_read_full_acc_row),
      root->IM2P_TAG_REG(3, tag_addr_read_full_acc_row), root->IM2P_TAG_REG(4, tag_addr_read_full_acc_row),
      root->IM2P_TAG_REG(5, tag_addr_read_full_acc_row)};
  return {unsigned(root->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
              execute__DOT__mesh__DOT__tagq__DOT__len)), read, prior_tag_write,
          ids[read], valid[read], rob[read], address[read], is_acc[read],
          accumulate[read], full_row[read], garbage[read]};
}
TagHead first_output_tag{};
std::uint64_t first_output_cycle = 0;
unsigned first_output_matmul_id = 0;
bool first_output_seen = false;
void event(const char *kind, std::uint64_t cycle) {
  if (current_ordinal != UINT32_MAX)
    std::cout << "RTL_EVENT " << current_ordinal << ' ' << current_work_id << ' '
              << cycle << ' ' << kind << '\n';
}

void observe(Adapter &state) {
  observe_service(state);
  const auto &d = state.dut;
  check(!d.reset, "mid-sequence RTL reset observed");
  sample_tag(d);
  const auto cycle = static_cast<std::uint64_t>(d.io_coreCycle);
  if (d.io_work_valid && d.io_work_ready && d.io_work_bits_finalFragment)
    ++accepted_final_fragments;
  if (d.io_work_valid && d.io_work_ready) event("work", cycle);
  if (d.io_events_loadIssued) event("load_issue", cycle);
  if (d.io_events_executeIssued) event("execute_issue", cycle);
  if (d.io_events_storeIssued) event("store_issue", cycle);
  if (d.io_events_outputContextIssued) event("context", cycle);
  if (d.io_events_loadDmaAccepted) event("load_dma", cycle);
  if (d.io_readRequest_valid && d.io_readRequest_ready &&
      (d.io_readRequest_bits_address < s_base || d.io_readRequest_bits_address >= c_base))
    event("read_request", cycle);
  if (d.io_readBeat_valid && d.io_readBeat_ready) {
    const auto it = std::find_if(state.reads.begin(), state.reads.end(), [&](const Read &read) {
      return read.id == d.io_readBeat_bits_id;
    });
    check(it != state.reads.end(), "read response lacks backing ID");
    if (!it->scale) event("read_response", cycle);
  }
  if (d.io_events_scratchpadReadAcceptedBankMask) event("scratchpad_read", cycle);
  if (d.rootp->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
      execute__DOT__mesh__DOT__input_next_row_into_spatial_array)) event("array_input", cycle);
  if (d.rootp->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP, _execute_io_completed_valid))
    event("raw_completed", cycle);
  if (d.io_events_rawRow) event("array_output", cycle);
  if (d.io_events_scaledRow) event("accumulator_write", cycle);
  if (d.io_events_accumulatorCommitted) event("accumulator_commit", cycle);
  if (d.io_events_storeDmaAccepted) event("store_dma", cycle);
  if (d.io_events_rawRow && !first_output_seen && current_ordinal != UINT32_MAX) {
    first_output_tag = tag_head(d);
    first_output_matmul_id = d.rootp->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
        execute__DOT__mesh__DOT__out_matmul_id_RegShifted_0_0);
    first_output_cycle = cycle;
    first_output_seen = true;
  }
  if (d.io_writeRequest_valid && d.io_writeRequest_ready) event("write_request", cycle);
  if (d.io_writeCompletion_valid && d.io_writeCompletion_ready) event("write_completion", cycle);
  if (d.io_loopDone_valid && d.io_loopDone_ready) event("loop_done", cycle);
  if (d.io_logicalDone_valid) event("logical_done", cycle);
}

std::vector<std::int32_t> result(Adapter &state, const WorkInput &w) {
  const auto slot = w.slot;
  std::vector<std::int32_t> answer;
  answer.reserve(w.m * w.n);
  for (std::size_t row = 0; row < w.m; ++row)
    for (std::size_t col = 0; col < w.n; ++col) {
      std::uint32_t raw = 0;
      for (std::size_t byte = 0; byte < 4; ++byte) {
        const auto at = row * state.c_stride[slot] + col * 4 + byte;
        check(state.written[slot].at(at) == 1, "RTL result coverage differs");
        raw |= std::uint32_t{state.c[slot].at(at)} << (8 * byte);
      }
      std::int32_t value;
      std::memcpy(&value, &raw, sizeof(value));
      answer.push_back(value);
    }
  return answer;
}

void execute_dense(Adapter &state, const WorkInput &w, std::vector<std::int32_t> &output) {
  FrontendRtlWork work{};
  work.work_id = w.id;
  work.host_slot = w.slot;
  work.row_begin = w.row_begin;
  work.rows = w.m;
  work.columns = w.n;
  work.k = w.k;
  work.plan = {w.m, w.n, w.k, w.ti, w.tj, w.tk, w.m,
               Mode::pipeline, WorkKind::dense_hp1_final};
  work.activations.assign(w.m * w.k, 1);
  work.weights.assign(w.k * w.n, 1);
  work.carriers.assign(((w.k + 31) / 32) * w.n, 0);
  const auto slot = w.slot;
  state.active_slot = slot;
  state.rows[slot] = w.m;
  state.columns[slot] = w.n;
  state.c_stride[slot] = padded(w.n) * 4;
  state.c[slot].assign(padded(w.m) * state.c_stride[slot], 0xA5);
  state.written[slot].assign(state.c[slot].size(), 0);
  schedule::ScheduleConfig config{{w.m, w.n, w.k}, {dim, IM2P_OPERAND_BITS},
                                  {w.ti, w.tj, w.tk, w.m}, {}, true, true, false};
  check(schedule::valid_config(config), "producer dense schedule rejected");
  schedule::LoopCursor cursor{};
  std::uint32_t generation = 1;
  const auto old_done = state.logical_done;
  while (cursor.i < w.m) {
    const auto loop = schedule::plan_loop(config, w.m, cursor);
    const auto tile_start = loop.k - loop.fragment_base * std::min<std::size_t>(dim, 32);
    state.output_ids.assign(128, false);
    state.loop(work, loop.i, loop.j, loop.is, loop.js, loop.k, loop.k + loop.ks,
               tile_start, loop.first, loop.last, generation);
    schedule::advance_loop(config, loop, cursor);
    generation = generation % 255 + 1;
  }
  check(state.logical_done == old_done + 1 &&
            state.logical_done_ids.back() == (w.id & 255U),
        "dense work logical completion missing");
  output = result(state, w);
}

void execute_residual(Adapter &state, const WorkInput &w, std::vector<std::int32_t> &output) {
  RmdRunWork work{};
  work.work_id = w.id;
  work.host_slot = w.slot;
  work.plan = {w.m, w.n, w.k, w.ti, w.tj, w.tk, w.m,
               Mode::full, WorkKind::dense_hp1_final};
  work.geometry.version = IM2P_PRODUCTION_GEOMETRY_VERSION;
  work.geometry.struct_size = sizeof(work.geometry);
  work.geometry.activation_bits = IM2P_ACTIVATION_BITS;
  work.geometry.weight_bits = IM2P_OPERAND_BITS;
  work.geometry.dim = dim;
  work.geometry.scope = IM2P_GEOMETRY_FULL;
  work.geometry.m = w.m;
  work.geometry.n = w.n;
  work.geometry.k = w.k;
  work.geometry.tile_i_count = w.ti;
  work.geometry.tile_j_count = w.tj;
  work.geometry.tile_k_count = w.tk;
  work.geometry.stripe_rows = work.geometry.row_count = w.m;
  work.original_k = w.original_k;
  work.runs = w.runs;
  work.activations.assign(w.m * w.k, 1);
  work.weights.assign(w.k * w.n, 1);
  work.carriers.assign(w.runs.size() * w.n, 0);
  const auto slot = w.slot;
  state.active_slot = slot;
  state.rows[slot] = w.m;
  state.columns[slot] = w.n;
  state.c_stride[slot] = padded(w.n) * 4;
  state.c[slot].assign(padded(w.m) * state.c_stride[slot], 0xA5);
  state.written[slot].assign(state.c[slot].size(), 0);
  schedule::ScheduleConfig config{{w.m, w.n, w.k}, {dim, IM2P_OPERAND_BITS},
                                  {w.ti, w.tj, w.tk, w.m}, {}, true, true, false};
  const im2p_compact_runs_t view{IM2P_COMPACT_RUNS_VERSION, sizeof(view),
                                 static_cast<std::uint32_t>(w.original_k),
                                 w.runs.size(), w.runs.data()};
  check(schedule::set_compact_runs(config, &view), "producer compact runs rejected");
  schedule::LoopCursor cursor{};
  std::uint32_t generation = 1;
  const auto old_done = state.logical_done;
  while (cursor.i < w.m) {
    const auto loop = schedule::plan_loop(config, w.m, cursor);
    const auto span = std::find_if(w.runs.begin(), w.runs.end(), [&](const auto &run) {
      return run.original_block_id == loop.original_block_id;
    });
    check(span != w.runs.end(), "producer run lost original block");
    state.output_ids.assign(128, false);
    run_loop(state, work, loop, span - w.runs.begin(), generation);
    schedule::advance_loop(config, loop, cursor);
    generation = generation % 255 + 1;
  }
  check(state.logical_done == old_done + 1 &&
            state.logical_done_ids.back() == (w.id & 255U),
        "residual work logical completion missing");
  output = result(state, w);
}

bool selected(std::string_view kind) {
  static constexpr std::array<std::string_view, 19> names = {
      "work", "load_issue", "execute_issue", "store_issue", "context", "load_dma",
      "read_request", "read_response", "scratchpad_read", "array_input",
      "raw_completed", "array_output", "accumulator_write", "accumulator_commit",
      "store_dma", "write_request", "write_completion", "loop_done", "logical_done"};
  return std::find(names.begin(), names.end(), kind) != names.end();
}

bool internal_drained(const Dut &dut) {
  const auto *root = dut.rootp;
  return root->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP, metadataQueue__DOT__empty) &&
      root->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP, execute__DOT__mesh__DOT__tagq__DOT__len) == 0 &&
      root->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP, execute__DOT__mesh__DOT__total_rows_q__DOT__empty) &&
      !root->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP, execute__DOT__mesh__DOT__req_valid);
}

void measure(Adapter &state, const WorkInput &w, unsigned period) {
  while (state.dut.io_coreCycle % period != w.phase)
    state.step();
  const auto before_loops = state.loops, before_final = accepted_final_fragments;
  const auto before_reads = state.scale_reads, before_responses = state.scale_responses;
  const auto before_load_requests = state.dut.io_loadRequests;
  const auto before_load_responses = state.dut.io_loadResponses;
  const auto before_store_requests = state.dut.io_storeRequests;
  const auto before_store_responses = state.dut.io_storeResponses;
  sample_tag(state.dut);
  const auto carry_tag = tag_head(state.dut);
  const auto before_tag_enqueues = tag_enqueues, before_tag_dequeues = tag_dequeues;
  const auto offset = state.cycle - state.dut.io_coreCycle;
  milestones = {};
  current_ordinal = w.ordinal;
  current_work_id = w.id;
  max_tag_queue_len = 0;
  tag_full_backpressure_cycles = 0;
  first_dequeue_seen = false;
  first_dequeue_cycle = 0;
  first_dequeue_out_id = first_dequeue_resp_valid = 0;
  first_dequeue_resp_last = first_dequeue_match_ready = 0;
  first_output_seen = false;
  std::vector<std::int32_t> output;
  if (w.kind == 'D') execute_dense(state, w, output);
  else execute_residual(state, w, output);
  const bool numerical = output.size() == w.m * w.n &&
      std::all_of(output.begin(), output.end(), [&](auto value) { return value == w.k; });
  check(numerical, "deterministic RTL numerical oracle differs");
  check(first_output_seen, "RTL mesh emitted no output row");
  for (unsigned wait = 0; wait < 2000000; ++wait) {
    state.dut.eval();
    if (state.dut.io_work_ready && !state.dut.io_busy &&
        state.dut.io_memoryDrained && state.dut.io_writebackDrained &&
        !state.dut.io_controllerBusy && !state.dut.io_loadBusy &&
        !state.dut.io_executeBusy && !state.dut.io_storeBusy &&
        !state.dut.io_loopBusy && state.reads.empty() && state.writes.empty())
      break;
    state.step();
  }
  state.dut.eval();
  check(state.dut.io_work_ready && !state.dut.io_busy && state.dut.io_memoryDrained &&
            state.dut.io_writebackDrained && !state.dut.io_controllerBusy &&
            state.reads.empty() && state.writes.empty(), "RTL public resource readiness stalled");
  milestones.reusable = state.dut.io_coreCycle;
  const auto public_tag = tag_head(state.dut);
  const auto mesh_matmul_id = unsigned(state.dut.rootp->IM2P_SEQUENCE_TAP(
      IM2P_RTL_SELECTED_TOP, execute__DOT__mesh__DOT__matmul_id));
  const auto *root = state.dut.rootp;
  const bool metadata_empty = root->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
      metadataQueue__DOT__empty);
  const auto tag_len = public_tag.len;
  const bool row_empty = root->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
      execute__DOT__mesh__DOT__total_rows_q__DOT__empty);
  const bool mesh_idle = !root->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
      execute__DOT__mesh__DOT__req_valid);
  bool canonical_drain = internal_drained(state.dut);
  for (unsigned wait = 0; wait < 512 && !canonical_drain; ++wait) {
    state.step();
    canonical_drain = internal_drained(state.dut);
  }
  const auto passive_tag = tag_head(state.dut);
  current_ordinal = UINT32_MAX;
  check(milestones.accepted < milestones.result &&
            milestones.result < milestones.release &&
            milestones.release < milestones.reusable,
        "result/release/resource ordering differs");
  check(milestones.scratchpad_half == before_loops % 2 &&
            milestones.accumulator_half == before_final % 2,
        "observed RTL memory half differs from carried state");

  im2p_cycle_model_config_t config;
  im2p_cycle_model_config_init(&config);
  config.hardware = {IM2P_ACTIVATION_BITS, IM2P_OPERAND_BITS, dim, 32, 32, 4,
                     IM2P_BANK_ROWS, IM2P_ACC_ROWS, sp_bytes, acc_bytes, 4, 2};
  config.timing.read_ready_period = period;
  config.timing.backing_cycle_offset = offset;
  std::unique_ptr<im2p_cycle_model_t, decltype(&im2p_cycle_model_destroy)> model(
      im2p_cycle_model_create(&config), im2p_cycle_model_destroy);
  check(bool(model), "service model rejected hardware profile");
  im2p_cycle_request_t request;
  im2p_cycle_request_init(&request);
  request.m = w.m; request.n = w.n; request.k = w.k;
  request.tile_i = w.ti; request.tile_j = w.tj; request.tile_k = w.tk;
  request.activation_stride_bytes = w.a_stride;
  request.weight_stride_bytes = w.b_stride;
  request.output_stride_bytes = w.c_stride;
  request.scale_stride_elements = w.s_stride;
  request.accepted_cycle = milestones.accepted;
  request.logical_work_id = w.id;
  request.submission = IM2P_CYCLE_BLOCK_SUBMISSIONS;
  request.record_events = 1;
  request.initial_scratchpad_half = before_loops % 2;
  request.initial_accumulator_half = before_final % 2;
  const im2p_compact_runs_t runs{IM2P_COMPACT_RUNS_VERSION, sizeof(runs),
                                 static_cast<std::uint32_t>(w.original_k),
                                 w.runs.size(), w.runs.data()};
  im2p_cycle_result_t modeled{};
  im2p_cycle_service_result_t service{};
  check(im2p_cycle_estimate_service(model.get(), &request,
              w.kind == 'R' ? &runs : nullptr, &modeled, &service) == IM2P_CYCLE_OK,
        im2p_cycle_model_error(model.get()));
  unsigned model_release_count = 0;
  for (std::uint64_t i = 0; i < im2p_cycle_model_event_count(model.get()); ++i) {
    im2p_cycle_event_t e{};
    check(im2p_cycle_model_event(model.get(), i, &e) == IM2P_CYCLE_OK,
          "service model event read failed");
    const std::string_view name = im2p_cycle_event_name(e.type);
    model_release_count += name == "scale_release";
    if (selected(name))
      std::cout << "MODEL_EVENT " << w.ordinal << ' ' << w.id << ' '
                << e.cycle << ' ' << name << '\n';
  }
  std::cout << "SEQUENCE_WORK {\"ordinal\":" << w.ordinal << ",\"work_id\":" << w.id
            << ",\"work_binding\":\"" << w.binding << "\",\"slot\":" << w.slot
            << ",\"parent_id\":" << w.parent << ",\"call_id\":" << w.call
            << ",\"stripe_id\":" << w.stripe << ",\"row_begin\":" << w.row_begin
            << ",\"parent_m\":" << w.parent_m << ",\"m\":" << w.m
            << ",\"n\":" << w.n << ",\"k\":" << w.k
            << ",\"tile_i\":" << w.ti << ",\"tile_j\":" << w.tj
            << ",\"tile_k\":" << w.tk << ",\"accepted_phase\":" << (milestones.accepted % period)
            << ",\"backing_cycle_offset\":" << offset
            << ",\"initial_scratchpad_half\":" << request.initial_scratchpad_half
            << ",\"initial_accumulator_half\":" << request.initial_accumulator_half
            << ",\"next_scratchpad_half\":" << (state.loops % 2)
            << ",\"next_accumulator_half\":" << (accepted_final_fragments % 2)
            << ",\"accepted\":" << milestones.accepted
            << ",\"result_ready\":" << milestones.result
            << ",\"final_scale_release\":" << milestones.release
            << ",\"resource_ready\":" << milestones.reusable
            << ",\"passive_drain_cycle\":" << state.dut.io_coreCycle
            << ",\"submissions\":" << milestones.submissions
            << ",\"scale_read_requests\":" << (state.scale_reads - before_reads)
            << ",\"scale_read_responses\":" << (state.scale_responses - before_responses)
            << ",\"scale_release_count\":" << milestones.release_count
            << ",\"load_requests\":" << (state.dut.io_loadRequests - before_load_requests)
            << ",\"load_responses\":" << (state.dut.io_loadResponses - before_load_responses)
            << ",\"store_requests\":" << (state.dut.io_storeRequests - before_store_requests)
            << ",\"store_responses\":" << (state.dut.io_storeResponses - before_store_responses)
            << ",\"model_result_ready\":" << service.result_ready_cycle
            << ",\"model_final_scale_release\":" << service.final_scale_release_cycle
            << ",\"model_resource_ready\":" << service.resource_ready_cycle
            << ",\"model_next_scratchpad_half\":" << service.next_scratchpad_half
            << ",\"model_next_accumulator_half\":" << service.next_accumulator_half
            << ",\"model_submissions\":" << modeled.loop_count
            << ",\"model_scale_read_requests\":" << modeled.scale_request_count
            << ",\"model_scale_read_responses\":" << modeled.scale_response_count
            << ",\"model_scale_release_count\":" << model_release_count
            << ",\"model_load_requests\":" << modeled.load_request_count
            << ",\"model_load_responses\":" << modeled.load_response_count
            << ",\"model_store_requests\":" << modeled.store_request_count
            << ",\"model_store_responses\":" << modeled.store_response_count
            << ",\"numeric_pass\":true,\"drain_work_ready\":true,\"drain_busy_clear\":true"
               ",\"drain_memory\":true,\"drain_writeback\":true,\"drain_controller\":true"
               ",\"drain_backing_reads\":true,\"drain_backing_writes\":true"
               ",\"metadata_queue_empty\":" << (metadata_empty ? "true" : "false")
            << ",\"mesh_tag_queue_len\":" << unsigned(tag_len)
            << ",\"mesh_tag_head_id\":" << public_tag.id
            << ",\"mesh_tag_head_rob_valid\":" << public_tag.rob_valid
            << ",\"mesh_tag_head_rob_bits\":" << public_tag.rob_bits
            << ",\"mesh_tag_head_address\":" << public_tag.address
            << ",\"mesh_tag_head_is_acc\":" << public_tag.is_acc
            << ",\"mesh_tag_head_accumulate\":" << public_tag.accumulate
            << ",\"mesh_tag_head_full_row\":" << public_tag.full_row
            << ",\"mesh_tag_head_garbage\":" << public_tag.garbage
            << ",\"mesh_matmul_id\":" << mesh_matmul_id
            << ",\"mesh_expected_carry_id\":" << ((mesh_matmul_id + 1) % 5)
            << ",\"carry_in_tag_len\":" << carry_tag.len
            << ",\"carry_in_tag_head_id\":" << carry_tag.id
            << ",\"first_tag_dequeue_seen\":" << (first_dequeue_seen ? "true" : "false")
            << ",\"first_tag_dequeue_cycle\":" << first_dequeue_cycle
            << ",\"first_tag_dequeue_out_id\":" << first_dequeue_out_id
            << ",\"first_tag_dequeue_resp_valid\":" << first_dequeue_resp_valid
            << ",\"first_tag_dequeue_resp_last\":" << first_dequeue_resp_last
            << ",\"first_tag_dequeue_match_ready\":" << first_dequeue_match_ready
            << ",\"mesh_tag_read_pointer\":" << public_tag.read
            << ",\"mesh_tag_write_pointer\":" << public_tag.write
            << ",\"mesh_tag_enqueues\":" << (tag_enqueues - before_tag_enqueues)
            << ",\"mesh_tag_dequeues\":" << (tag_dequeues - before_tag_dequeues)
            << ",\"mesh_tag_max_occupancy\":" << max_tag_queue_len
            << ",\"mesh_tag_never_full\":" << (max_tag_queue_len < 6 ? "true" : "false")
            << ",\"mesh_tag_full_backpressure_cycles\":" << tag_full_backpressure_cycles
            << ",\"first_output_cycle\":" << first_output_cycle
            << ",\"first_output_tag_queue_len\":" << first_output_tag.len
            << ",\"first_output_tag_head_id\":" << first_output_tag.id
            << ",\"first_output_tag_head_rob_valid\":" << first_output_tag.rob_valid
            << ",\"first_output_tag_head_rob_bits\":" << first_output_tag.rob_bits
            << ",\"first_output_matmul_id\":" << first_output_matmul_id
            << ",\"mesh_row_queue_empty\":" << (row_empty ? "true" : "false")
            << ",\"mesh_request_idle\":" << (mesh_idle ? "true" : "false")
            << ",\"passive_mesh_tag_queue_len\":" << passive_tag.len
            << ",\"passive_mesh_tag_head_id\":" << passive_tag.id
            << ",\"passive_mesh_tag_read_pointer\":" << passive_tag.read
            << ",\"passive_mesh_tag_write_pointer\":" << passive_tag.write
            << ",\"internal_queue_drain_observed\":" << (canonical_drain ? "true" : "false")
            << "}\n";
}
}

int main(int argc, char **argv) {
  try {
    check(argc == 2, "numeric projection path required");
    unsigned period = 0;
    const auto works = read_work(argv[1], period);
    VerilatedContext context;
    context.commandArgs(argc, argv);
    Dut dut{&context};
    Adapter state{dut, context};
    reset(state);
    state.read_ready_period = period;
    state.event_observer = observe;
    std::cout << "SEQUENCE_RUN {\"instance_count\":1,\"reset_count\":1,\"period\":"
              << period << ",\"work_count\":" << works.size() << "}\n";
    for (const auto &work : works)
      measure(state, work, period);
    state.event_observer = nullptr;
    dut.final();
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "PRODUCTION_SEQUENCE_FAIL " << error.what() << '\n';
    return 1;
  }
}
#undef IM2P_SEQUENCE_TAP
#undef IM2P_SEQUENCE_TAP_
#undef IM2P_TAG_REG
#endif
