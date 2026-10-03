namespace schedule = im2p::gemmini;
std::uint32_t observed_block = 0, observed_generation = 0;
std::size_t observed_compact_k = 0, observed_compact_count = 0;
const char *run_loop_label = "RUN_AWARE_RTL_LOOP loop=";

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
  dut.io_work_bits_workBase = 0;
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
    std::cout << run_loop_label << expected_loops
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
