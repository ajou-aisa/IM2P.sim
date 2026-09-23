#if !defined(IM2P_RTL_TEST_BUILD) || !__has_include(<VIM2PGemminiWSHP1RtlTest.h>)
int im2p_compositional_sequence_requires_generated_model;
#else
#define read_work legacy_sequence_read_work
#define measure legacy_sequence_measure
#include IM2P_COMPOSITIONAL_BASE_SOURCE
#undef main
#undef read_work
#undef measure

namespace {
std::vector<WorkInput> read_work(const char *path, unsigned &period) {
  std::ifstream input(path);
  check(bool(input), "composition manifest missing");
  std::string token, profile;
  input >> token >> profile;
  check(token == "IM2P_COMPOSITIONAL_SEQUENCE_V1" && profile ==
            "a" + std::to_string(IM2P_ACTIVATION_BITS) + "w" +
                std::to_string(IM2P_OPERAND_BITS) + "-d" + std::to_string(dim) + "-hp1",
        "composition profile differs from RTL");
  unsigned count = 0;
  input >> token >> period;
  check(token == "PERIOD" && period == 5, "unsupported reference-memory period");
  input >> token >> count;
  check(token == "WORKS" && count > 4 && count <= 4096, "composition needs >4 bounded works");
  std::vector<WorkInput> works;
  for (unsigned ordinal = 0; ordinal < count; ++ordinal) {
    WorkInput w{};
    unsigned run_count = 0;
    input >> token >> w.ordinal >> w.id >> w.kind >> w.slot >> w.phase >> w.parent >> w.call >>
        w.stripe >> w.row_begin >> w.parent_m >> w.m >> w.n >> w.k >> w.ti >> w.tj >> w.tk >>
        w.a_stride >> w.b_stride >> w.c_stride >> w.s_stride >> w.original_k >> w.binding >> run_count;
    check(bool(input) && token == "W" && w.ordinal == ordinal && w.id <= UINT32_MAX &&
              (ordinal || w.phase < period) && (w.kind == 'D' || w.kind == 'R') &&
              w.slot < 2 && w.m && w.n && w.k && w.m <= 8192 && w.n <= 8192 &&
              w.k <= 8192 && w.ti && w.tj && w.tk && run_count <= 256 &&
              ((w.kind == 'R') == (run_count > 0)), "malformed composition work");
    for (unsigned i = 0; i < run_count; ++i) {
      im2p_compact_run_t run{};
      input >> token >> run.original_block_id >> run.original_k_mask >>
          run.compact_k_begin >> run.compact_k_count;
      check(bool(input) && token == "R", "malformed composition run");
      w.runs.push_back(run);
    }
    works.push_back(w);
  }
  check(!(input >> token), "trailing composition records");
  return works;
}

bool public_ready(Adapter &state) {
  state.dut.eval();
  const auto &d = state.dut;
  return d.io_work_ready && !d.io_busy && d.io_memoryDrained &&
      d.io_writebackDrained && !d.io_controllerBusy && !d.io_loadBusy &&
      !d.io_executeBusy && !d.io_storeBusy && !d.io_loopBusy &&
      state.reads.empty() && state.writes.empty();
}

struct Prediction {
  std::uint64_t resource = 0;
  unsigned scratchpad_half = 0, accumulator_half = 0;
};

Prediction estimate(const WorkInput &w, unsigned period, std::uint64_t accepted,
                    unsigned scratchpad_half, unsigned accumulator_half,
                    std::uint64_t offset, bool emit_events) {
  im2p_cycle_model_config_t config;
  im2p_cycle_model_config_init(&config);
  config.hardware = {IM2P_ACTIVATION_BITS, IM2P_OPERAND_BITS, dim, 32, 32, 4,
                     IM2P_BANK_ROWS, IM2P_ACC_ROWS, sp_bytes, acc_bytes, 4, 2};
  config.timing.read_ready_period = period;
  config.timing.backing_cycle_offset = offset;
  config.max_trace_events = 10000000;
  std::unique_ptr<im2p_cycle_model_t, decltype(&im2p_cycle_model_destroy)> model(
      im2p_cycle_model_create(&config), im2p_cycle_model_destroy);
  check(bool(model), "service model rejected profile");
  im2p_cycle_request_t request;
  im2p_cycle_request_init(&request);
  request.m = w.m; request.n = w.n; request.k = w.k;
  request.tile_i = w.ti; request.tile_j = w.tj; request.tile_k = w.tk;
  request.activation_stride_bytes = w.a_stride;
  request.weight_stride_bytes = w.b_stride;
  request.output_stride_bytes = w.c_stride;
  request.scale_stride_elements = w.s_stride;
  request.accepted_cycle = accepted;
  request.logical_work_id = w.id;
  request.submission = IM2P_CYCLE_BLOCK_SUBMISSIONS;
  request.record_events = 1;
  request.initial_scratchpad_half = scratchpad_half;
  request.initial_accumulator_half = accumulator_half;
  const im2p_compact_runs_t runs{IM2P_COMPACT_RUNS_VERSION, sizeof(runs),
                                 static_cast<std::uint32_t>(w.original_k),
                                 w.runs.size(), w.runs.data()};
  im2p_cycle_result_t result{};
  im2p_cycle_service_result_t service{};
  check(im2p_cycle_estimate_service(model.get(), &request,
              w.kind == 'R' ? &runs : nullptr, &result, &service) == IM2P_CYCLE_OK,
        im2p_cycle_model_error(model.get()));
  unsigned releases = 0;
  for (std::uint64_t i = 0; i < im2p_cycle_model_event_count(model.get()); ++i) {
    im2p_cycle_event_t e{};
    check(im2p_cycle_model_event(model.get(), i, &e) == IM2P_CYCLE_OK,
          "composition model event read failed");
    const std::string_view name = im2p_cycle_event_name(e.type);
    releases += name == "scale_release";
    if (emit_events && selected(name))
      std::cout << "MODEL_EVENT " << w.ordinal << ' ' << w.id << ' '
                << e.cycle << ' ' << name << '\n';
  }
  if (emit_events)
    std::cout << "MODEL_COUNTS " << w.ordinal << ' ' << w.id << ' '
              << result.loop_count << ' ' << result.scale_request_count << ' '
              << result.scale_response_count << ' ' << releases << ' '
              << result.load_request_count << ' ' << result.load_response_count << ' '
              << result.store_request_count << ' ' << result.store_response_count << ' '
              << service.result_ready_cycle << ' ' << service.final_scale_release_cycle << ' '
              << service.resource_ready_cycle << ' ' << service.next_scratchpad_half << ' '
              << service.next_accumulator_half << '\n';
  return {service.resource_ready_cycle, service.next_scratchpad_half,
          service.next_accumulator_half};
}

Prediction measure(Adapter &state, const WorkInput &w, unsigned period,
                   const Prediction &previous, std::uint64_t previous_rtl_ready) {
  const std::uint64_t arrival = w.ordinal ? previous_rtl_ready + w.phase : 0;
  if (!w.ordinal) {
    while (state.dut.io_coreCycle % period != w.phase) state.step();
  }
  else
    while (state.dut.io_coreCycle < arrival) state.step();
  if (w.ordinal)
    check(public_ready(state), "declared offer preceded public readiness");
  const auto offered = state.dut.io_coreCycle;
  const auto predicted_offer = w.ordinal ? previous.resource + w.phase : offered;
  const auto before_loops = state.loops, before_final = accepted_final_fragments;
  const auto before_reads = state.scale_reads, before_responses = state.scale_responses;
  const auto before_load_requests = state.dut.io_loadRequests;
  const auto before_load_responses = state.dut.io_loadResponses;
  const auto before_store_requests = state.dut.io_storeRequests;
  const auto before_store_responses = state.dut.io_storeResponses;
  const auto offset = state.cycle - state.dut.io_coreCycle;
  const auto carry = tag_head(state.dut);
  const auto prior_enqueues = tag_enqueues, prior_dequeues = tag_dequeues;
  milestones = {};
  current_ordinal = w.ordinal;
  current_work_id = w.id;
  first_output_seen = first_dequeue_seen = false;
  max_tag_queue_len = 0;
  tag_full_backpressure_cycles = 0;
  std::vector<std::int32_t> output;
  if (w.kind == 'D') execute_dense(state, w, output);
  else execute_residual(state, w, output);
  check(output.size() == w.m * w.n &&
        std::all_of(output.begin(), output.end(), [&](auto value) { return value == w.k; }),
        "composition numeric result differs");
  bool ready = false;
  for (unsigned wait = 0; wait < 2000000; ++wait) {
    if (public_ready(state)) { ready = true; break; }
    state.step();
  }
  check(ready, "composition public resource readiness stalled");
  milestones.reusable = state.dut.io_coreCycle;
  const auto head = tag_head(state.dut);
  current_ordinal = UINT32_MAX;
  check(milestones.accepted > 0 && milestones.accepted < milestones.result &&
        milestones.result < milestones.release && milestones.release < milestones.reusable,
        "composition milestone order differs");
  check(milestones.scratchpad_half == before_loops % 2 &&
        milestones.accumulator_half == before_final % 2,
        "composition RTL memory half differs");
  const auto predicted_accepted = predicted_offer;
  const auto predicted = estimate(w, period, predicted_accepted,
      previous.scratchpad_half, previous.accumulator_half, offset, false);
  std::cout << "ACCEPTANCE " << w.ordinal << ' ' << w.id << ' '
            << offered << ' ' << milestones.accepted << ' ' << predicted_offer << ' '
            << predicted_accepted << ' ' << milestones.reusable << ' '
            << predicted.resource << '\n';
  estimate(w, period, milestones.accepted, before_loops % 2,
           before_final % 2, offset, true);
  std::cout << "COMPOSITION_WORK {\"ordinal\":" << w.ordinal
            << ",\"work_id\":" << w.id << ",\"work_binding\":\"" << w.binding
            << "\",\"slot\":" << w.slot << ",\"parent_id\":" << w.parent
            << ",\"call_id\":" << w.call << ",\"arrival_delay\":" << w.phase
            << ",\"offered\":" << offered << ",\"accepted\":" << milestones.accepted
            << ",\"predicted_offered\":" << predicted_offer
            << ",\"predicted_accepted\":" << predicted_accepted
            << ",\"result_ready\":" << milestones.result
            << ",\"final_scale_release\":" << milestones.release
            << ",\"resource_ready\":" << milestones.reusable
            << ",\"predicted_resource_ready\":" << predicted.resource
            << ",\"submissions\":" << milestones.submissions
            << ",\"scale_read_requests\":" << state.scale_reads - before_reads
            << ",\"scale_read_responses\":" << state.scale_responses - before_responses
            << ",\"scale_release_count\":" << milestones.release_count
            << ",\"load_requests\":" << state.dut.io_loadRequests - before_load_requests
            << ",\"load_responses\":" << state.dut.io_loadResponses - before_load_responses
            << ",\"store_requests\":" << state.dut.io_storeRequests - before_store_requests
            << ",\"store_responses\":" << state.dut.io_storeResponses - before_store_responses
            << ",\"initial_scratchpad_half\":" << before_loops % 2
            << ",\"initial_accumulator_half\":" << before_final % 2
            << ",\"next_scratchpad_half\":" << state.loops % 2
            << ",\"next_accumulator_half\":" << accepted_final_fragments % 2
            << ",\"carry_in_tag_len\":" << carry.len
            << ",\"mesh_tag_queue_len\":" << head.len
            << ",\"mesh_tag_head_id\":" << head.id
            << ",\"mesh_tag_read_pointer\":" << head.read
            << ",\"mesh_tag_write_pointer\":" << head.write
            << ",\"mesh_tag_enqueues\":" << tag_enqueues - prior_enqueues
            << ",\"mesh_tag_dequeues\":" << tag_dequeues - prior_dequeues
            << ",\"mesh_tag_max_occupancy\":" << max_tag_queue_len
            << ",\"mesh_tag_full_backpressure_cycles\":" << tag_full_backpressure_cycles
            << ",\"numeric_pass\":true}\n";
  return predicted;
}
}

int main(int argc, char **argv) {
  try {
    check(argc == 2, "composition projection path required");
    unsigned period = 0;
    const auto works = read_work(argv[1], period);
    VerilatedContext context;
    context.commandArgs(argc, argv);
    Dut dut{&context};
    Adapter state{dut, context};
    reset(state);
    state.read_ready_period = period;
    state.event_observer = observe;
    std::cout << "COMPOSITION_RUN {\"instance_count\":1,\"reset_count\":1,\"period\":"
              << period << ",\"work_count\":" << works.size() << "}\n";
    Prediction previous{};
    std::uint64_t previous_rtl_ready = 0;
    for (const auto &work : works) {
      previous = measure(state, work, period, previous, previous_rtl_ready);
      previous_rtl_ready = milestones.reusable;
    }
    state.event_observer = nullptr;
    dut.final();
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "COMPOSITION_FAIL " << error.what() << '\n';
    return 1;
  }
}
#endif
