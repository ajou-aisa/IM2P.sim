#if !defined(IM2P_RTL_TEST_BUILD) || !__has_include(<VIM2PGemminiWSHP1RtlTest.h>)
int im2p_service_certificate_requires_generated_model;
#else
#define IM2P_SERVICE_PROBE_NO_MAIN
#include "service_boundary_probe.cpp"
#undef IM2P_SERVICE_PROBE_NO_MAIN

void check_drained(Adapter &state, const Experiment &experiment) {
  state.dut.eval();
  check(!state.dut.io_busy && state.dut.io_memoryDrained &&
            state.dut.io_writebackDrained && !state.dut.io_controllerBusy &&
            state.reads.empty() && state.writes.empty(),
        "reusable host slot did not leave one-outstanding RTL drained");
  std::cout << "DRAINED " << experiment.sequence << ' ' << experiment.period
            << ' ' << experiment.phase << ' ' << experiment.isolated << ' '
            << experiment.work << ' ' << milestones.reusable << '\n';
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
            for (unsigned work = 0; work < 2; ++work) {
              if (work == 1 && isolated) {
                reset(state);
                state.loops = state.logical_done = 0;
              }
              while (state.dut.io_coreCycle % period != phase)
                state.step();
              const Experiment experiment{sequence, period, phase, work,
                                          isolated};
              measure(state, experiment);
              check_drained(state, experiment);
            }
            dut.final();
          }
    for (const unsigned period : {3U, 5U})
      for (unsigned phase = 0; phase < period; ++phase) {
        VerilatedContext context;
        context.commandArgs(argc, argv);
        Dut dut{&context};
        Adapter state{dut, context};
        reset(state);
        state.read_ready_period = period;
        auto primer = dense(99, false);
        primer.k = primer.plan.k = 32;
        primer.plan.tile_k = dim == 16 ? 2 : 1;
        primer.activations.assign(2 * 32, 1);
        primer.weights.assign(32 * 3, 1);
        primer.carriers = {0, 1, 0x80000000U};
        std::vector<std::int32_t> output;
        FrontendRtlTiming timing{};
        const auto status = execute(&state, primer, output, timing);
        check(status == IM2P_OK &&
                  output == std::vector<std::int32_t>{32, 64, 0, 32, 64, 0},
              "K32 buffer-half primer failed numerical RTL output");
        for (unsigned i = 0; !state.dut.io_work_ready && i < 512; ++i)
          state.step();
        state.dut.eval();
        check(state.dut.io_work_ready && !state.dut.io_busy &&
                  state.dut.io_memoryDrained && state.dut.io_writebackDrained &&
                  state.reads.empty() && state.writes.empty(),
              "K32 primer did not drain");
        while (state.dut.io_coreCycle % period != phase)
          state.step();
        for (unsigned work = 0; work < 2; ++work) {
          const Experiment experiment{5, period, phase, work, false};
          measure(state, experiment);
          check_drained(state, experiment);
          while (state.dut.io_coreCycle % period != phase)
            state.step();
        }
        dut.final();
      }
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "SERVICE_CERTIFICATE_FAIL " << error.what() << '\n';
    return 1;
  }
}
#endif
