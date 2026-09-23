#if !defined(IM2P_RTL_TEST_BUILD) || !__has_include(<VIM2PGemminiWSHP1RtlTest.h>)
int im2p_service_boundary_requires_generated_model;
#else
#define main existing_ws_rtl_main
#include "../../../fpga/gemmini_hp1/host/test_ws_rtl.cpp"
#undef main
#include "../../include/im2p_cycle_service.h"
#include <memory>

namespace {
struct Milestones {
  std::uint64_t accepted = 0, result = 0, release = 0, reusable = 0;
  std::uint64_t submissions = 0, release_count = 0;
  unsigned scratchpad_half = UINT32_MAX, accumulator_half = UINT32_MAX;
  std::vector<std::uint64_t> release_cycles;
};
Milestones milestones;

void observe_service(Adapter &state) {
  const auto &dut = state.dut;
  if (dut.io_work_valid && dut.io_work_ready) {
    if (dut.io_work_bits_firstLoop)
      milestones.accepted = dut.io_coreCycle;
    ++milestones.submissions;
  }
  if (dut.io_logicalDone_valid)
    milestones.result = dut.io_coreCycle;
  if (dut.io_scaleRelease_valid && dut.io_scaleRelease_ready) {
    milestones.release = dut.io_coreCycle;
    ++milestones.release_count;
    milestones.release_cycles.push_back(dut.io_coreCycle);
  }
  if (dut.io_events_loadDmaAccepted && milestones.scratchpad_half == UINT32_MAX &&
      dut.io_events_loadVaddrBytes == slot_address(a_base, state.active_slot))
    milestones.scratchpad_half = dut.io_events_loadLocalAddressRaw / (IM2P_BANK_ROWS * 2);
  if (dut.io_events_storeDmaAccepted && milestones.accumulator_half == UINT32_MAX)
    milestones.accumulator_half = (dut.io_events_storeLocalAddressRaw % IM2P_ACC_ROWS) /
                                   (IM2P_ACC_ROWS / 2);
}

void reset(Adapter &state) {
  auto &dut = state.dut;
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
}

RmdRunWork residual(std::uint32_t id) {
  RmdRunWork work{};
  work.work_id = id;
  work.plan = {2, 3, 37, 1, 1, 2, 2, Mode::full, WorkKind::dense_hp1_final};
  work.geometry.tile_i_count = work.geometry.tile_j_count = 1;
  work.geometry.tile_k_count = 2;
  work.original_k = 116;
  work.runs = {{0, 0x1ffff, 0, 17}, {3, 0xfffff, 17, 20}};
  work.activations.assign(2 * 37, 1);
  work.weights.assign(37 * 3, 1);
  work.carriers = {0, 1, 0x80000000U, 1, 0, 2};
  return work;
}

FrontendRtlWork dense(std::uint32_t id, bool stripe) {
  return {id, stripe ? id - 1 : 0, stripe ? (id - 1) * 2 : 0, 2, 3, 64,
          {2, 3, 64, 1, 1, 64 / dim, 2,
           stripe ? Mode::pipeline : Mode::full, WorkKind::dense_hp1_final},
          std::vector<std::int8_t>(2 * 64, 1),
          std::vector<std::int8_t>(64 * 3, 1), {0, 1, 0x80000000U, 1, 0, 2}};
}

struct Experiment {
  unsigned sequence, period, phase, work;
  bool isolated;
};

void measure(Adapter &state, const Experiment &experiment) {
  const auto index = experiment.work;
  const bool runs = (experiment.sequence == 1 && index == 1) ||
                    experiment.sequence == 2 ||
                    (experiment.sequence == 3 && index == 0);
  const bool stripe = experiment.sequence == 4;
  const auto before_loops = state.loops;
  const auto before_done = state.logical_done;
  const auto before_scale_reads = state.scale_reads;
  const auto before_scale_responses = state.scale_responses;
  const auto backing_offset = state.cycle - state.dut.io_coreCycle;
  milestones = {};
  state.event_observer = observe_service;
  const auto work = residual(index + 1);
  const auto ordinary = dense(index + 1, stripe);
  std::vector<std::int32_t> output;
  std::uint64_t elapsed = 0;
  if (runs) {
    check(execute_runs(&state, work, output, elapsed) == IM2P_OK,
          "run-aware service fixture failed");
    check(output == std::vector<std::int32_t>{57, 54, 80, 57, 54, 80},
          "run-aware service numerical output differs");
  } else {
    FrontendRtlTiming timing{};
    check(execute(&state, ordinary, output, timing) == IM2P_OK,
          "dense service fixture failed");
    elapsed = timing.done_cycle - timing.start_cycle;
    check(output == std::vector<std::int32_t>{96, 96, 128, 96, 96, 128},
          "dense service numerical output differs");
  }
  state.event_observer = nullptr;
  state.dut.eval();
  for (unsigned i = 0; !state.dut.io_work_ready && i < 512; ++i)
    state.step();
  check(state.dut.io_work_ready, "same host slot did not become reusable");
  milestones.reusable = state.dut.io_coreCycle;
  check(milestones.accepted && milestones.result > milestones.accepted &&
            milestones.release > milestones.result &&
            milestones.reusable > milestones.release &&
            elapsed == milestones.result - milestones.accepted,
        "result, scale release and resource readiness must remain distinct");

  im2p_cycle_model_config_t config;
  im2p_cycle_model_config_init(&config);
  config.hardware = {IM2P_ACTIVATION_BITS, IM2P_OPERAND_BITS, dim, 32, 32, 4,
                     IM2P_BANK_ROWS, IM2P_ACC_ROWS, sp_bytes, acc_bytes, 4, 2};
  config.timing.read_ready_period = experiment.period;
  config.timing.backing_cycle_offset = backing_offset;
  std::unique_ptr<im2p_cycle_model_t, decltype(&im2p_cycle_model_destroy)> model(
      im2p_cycle_model_create(&config), im2p_cycle_model_destroy);
  check(static_cast<bool>(model), "service cycle profile rejected");
  im2p_cycle_request_t request;
  im2p_cycle_request_init(&request);
  request.m = 2;
  request.n = 3;
  request.k = runs ? work.plan.k : ordinary.k;
  request.tile_i = request.tile_j = 1;
  request.tile_k = runs ? work.plan.tile_k : ordinary.plan.tile_k;
  request.submission = runs ? IM2P_CYCLE_BLOCK_SUBMISSIONS : IM2P_CYCLE_TILE_SUBMISSIONS;
  request.accepted_cycle = milestones.accepted;
  request.initial_scratchpad_half = before_loops % 2;
  request.initial_accumulator_half = before_done % 2;
  check(request.initial_scratchpad_half == milestones.scratchpad_half &&
            request.initial_accumulator_half == milestones.accumulator_half,
        "observed local-memory halves differ from sequence initial state");
  request.record_events = 1;
  request.logical_work_id = index + 1;
  const im2p_compact_runs_t view{1, sizeof(view), work.original_k,
                                work.runs.size(), work.runs.data()};
  im2p_cycle_result_t result{};
  const auto status = runs ? im2p_cycle_estimate_runs(model.get(), &request, &view, &result)
                           : im2p_cycle_estimate(model.get(), &request, &result);
  check(status == IM2P_CYCLE_OK, im2p_cycle_model_error(model.get()));
  check(result.loop_count == milestones.submissions &&
            result.scale_request_count == state.scale_reads - before_scale_reads &&
            result.scale_response_count == state.scale_responses - before_scale_responses,
        "service model submission/scale counters differ from RTL");
  std::uint64_t model_release = 0;
  for (std::uint64_t i = 0; i < im2p_cycle_model_event_count(model.get()); ++i) {
    im2p_cycle_event_t event{};
    check(im2p_cycle_model_event(model.get(), i, &event) == IM2P_CYCLE_OK,
          "cycle event read failed");
    if (std::string{im2p_cycle_event_name(event.type)} == "scale_release")
      model_release = std::max(model_release, event.cycle);
  }
  im2p_cycle_service_result_t service{};
  im2p_cycle_result_t drained{};
  check(im2p_cycle_estimate_service(model.get(), &request, runs ? &view : nullptr,
                                    &drained, &service) == IM2P_CYCLE_OK,
        im2p_cycle_model_error(model.get()));
  check(drained.start_cycle == result.start_cycle && drained.done_cycle == result.done_cycle &&
            drained.total_cycles == result.total_cycles &&
            service.next_scratchpad_half == state.loops % 2 &&
            service.next_accumulator_half == state.logical_done % 2,
        "service extension changed logical result or next buffer ownership");
  std::vector<std::uint64_t> model_release_cycles;
  for (std::uint64_t i = 0; i < im2p_cycle_model_event_count(model.get()); ++i) {
    im2p_cycle_event_t event{};
    check(im2p_cycle_model_event(model.get(), i, &event) == IM2P_CYCLE_OK,
          "service event read failed");
    if (std::string{im2p_cycle_event_name(event.type)} == "scale_release")
      model_release_cycles.push_back(event.cycle);
  }
  check(model_release_cycles == milestones.release_cycles,
        "service per-cycle scale-release events differ from RTL");
  std::cout << "SERVICE {\"sequence\":" << experiment.sequence
            << ",\"period\":" << experiment.period << ",\"phase\":" << experiment.phase
            << ",\"work\":" << index << ",\"reset_isolated\":" << experiment.isolated
            << ",\"backing_cycle_offset\":" << backing_offset
            << ",\"slot\":" << (stripe ? index : 0)
            << ",\"m\":2,\"n\":3,\"k\":" << request.k
            << ",\"tile_i\":1,\"tile_j\":1,\"tile_k\":" << request.tile_k
            << ",\"run_count\":" << (runs ? 2 : 0)
            << ",\"initial_scratchpad_half\":" << request.initial_scratchpad_half
            << ",\"initial_accumulator_half\":" << request.initial_accumulator_half
            << ",\"accepted\":" << milestones.accepted
            << ",\"result_ready\":" << milestones.result
            << ",\"final_scale_release\":" << milestones.release
            << ",\"resource_ready\":" << milestones.reusable
            << ",\"submissions\":" << milestones.submissions
            << ",\"release_count\":" << milestones.release_count
            << ",\"model_done\":" << result.done_cycle
            << ",\"model_final_scale_release\":" << model_release
            << ",\"model_service_release\":" << service.final_scale_release_cycle
            << ",\"model_resource_ready\":" << service.resource_ready_cycle << "}\n";
}
}

int main(int argc, char **argv) {
  try {
    for (const unsigned period : {3U, 5U})
      for (unsigned phase = 0; phase < period; ++phase)
        for (unsigned sequence = 0; sequence < 5; ++sequence)
          for (const bool isolated : {false, true}) {
            VerilatedContext context;
            context.commandArgs(argc, argv);
            Dut dut{&context};
            Adapter state{dut, context};
            reset(state);
            state.read_ready_period = period;
            while (state.dut.io_coreCycle % period != phase)
              state.step();
            measure(state, {sequence, period, phase, 0, isolated});
            if (isolated) {
              reset(state);
              state.loops = state.logical_done = 0;
              while (state.dut.io_coreCycle % period != phase)
                state.step();
            }
            measure(state, {sequence, period, phase, 1, isolated});
            dut.final();
          }
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "SERVICE_BOUNDARY_FAIL " << error.what() << '\n';
    return 1;
  }
}
#endif
