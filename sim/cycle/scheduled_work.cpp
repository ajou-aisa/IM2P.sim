#include "scheduled_work.hpp"
#include <algorithm>
#include <limits>

namespace im2p::cycle {
uint64_t checked_add(uint64_t a, uint64_t b) {
  if (b > UINT64_MAX - a)
    throw Error(IM2P_CYCLE_OVERFLOW, "integer addition overflow");
  return a + b;
}
uint64_t checked_mul(uint64_t a, uint64_t b) {
  if (a && b > UINT64_MAX / a)
    throw Error(IM2P_CYCLE_OVERFLOW, "integer multiplication overflow");
  return a * b;
}
void validate_config(const im2p_cycle_model_config_t &c) {
  const auto &h = c.hardware;
  if (c.abi_version != IM2P_CYCLE_MODEL_ABI_VERSION ||
      c.struct_size != sizeof(c))
    throw Error(IM2P_CYCLE_INVALID, "cycle-model config ABI mismatch");
  if ((h.activation_bits != 4 && h.activation_bits != 8) ||
      h.weight_bits != h.activation_bits ||
      (h.dim != 16 && h.dim != 32 && h.dim != 64))
    throw Error(IM2P_CYCLE_UNSUPPORTED, "unsupported hardware profile");
  if (h.block_k != 32 || h.accumulator_bits != 32 || h.bank_count != 4 ||
      h.scratchpad_row_bytes != h.dim * h.activation_bits / 8 ||
      h.accumulator_row_bytes != h.dim * 4 ||
      uint64_t(h.bank_rows) * h.bank_count * h.scratchpad_row_bytes !=
          256 * 1024 ||
      uint64_t(h.accumulator_rows) * h.accumulator_row_bytes != 64 * 1024 ||
      h.scratchpad_read_delay != 4 || h.accumulator_latency != 2)
    throw Error(IM2P_CYCLE_UNSUPPORTED,
                "resolved hardware differs from rtl-regression contract");
  if (c.timing.revision != 1 || c.timing.reserved ||
      c.timing.read_ready_period == 1 || !c.timing.backing_read_delay ||
      !c.timing.backing_write_delay)
    throw Error(IM2P_CYCLE_INVALID, "invalid regression timing profile");
  if (!c.max_cycles || !c.max_fragments || !c.max_trace_events ||
      c.max_fragments > 10000000 || c.max_trace_events > 100000000)
    throw Error(IM2P_CYCLE_INVALID, "invalid software admission limit");
}
Schedule expand_work(const im2p_cycle_model_config_t &c,
                     const im2p_cycle_request_t &r) {
  validate_config(c);
  if (r.abi_version != IM2P_CYCLE_MODEL_ABI_VERSION ||
      r.struct_size != sizeof(r))
    throw Error(IM2P_CYCLE_INVALID, "cycle request ABI mismatch");
  if (!r.m || !r.n || !r.k || !r.tile_i || !r.tile_j || !r.tile_k ||
      r.submission > IM2P_CYCLE_TILE_SUBMISSIONS || r.record_events > 1 ||
      r.initial_scratchpad_half > 1 || r.initial_accumulator_half > 1)
    throw Error(IM2P_CYCLE_INVALID,
                "invalid GEMM shape, tile or initial layout");
  checked_add(checked_add(r.accepted_cycle, c.max_cycles),
              c.timing.backing_cycle_offset);
  const uint64_t as =
      r.activation_stride_bytes ? r.activation_stride_bytes : r.k;
  const uint64_t bs = r.weight_stride_bytes ? r.weight_stride_bytes : r.n;
  const uint64_t cs =
      r.output_stride_bytes ? r.output_stride_bytes : checked_mul(r.n, 4);
  const uint64_t ss = r.scale_stride_elements ? r.scale_stride_elements : r.n;
  if (as < r.k || bs < r.n || cs < checked_mul(r.n, 4) || cs % 4 || ss < r.n)
    throw Error(IM2P_CYCLE_INVALID, "invalid row stride");
  checked_add(checked_mul(r.m - 1, as), r.k);
  checked_add(checked_mul(r.k - 1, bs), r.n);
  checked_add(checked_mul(r.m - 1, cs), checked_mul(r.n, 4));
  checked_mul(checked_mul(checked_add(r.k, 31) / 32, ss), 4);
  if (std::max({r.m, r.n, r.k, r.tile_i, r.tile_j, r.tile_k}) > UINT32_MAX)
    throw Error(IM2P_CYCLE_OVERFLOW,
                "shape exceeds planner/hardware field width");
  const auto dim = c.hardware.dim;
  Schedule s;
  s.planner = {
      {size_t(r.m), size_t(r.n), size_t(r.k)},
      {dim, c.hardware.activation_bits},
      {size_t(r.tile_i), size_t(r.tile_j), size_t(r.tile_k), size_t(r.m)},
      {as, bs, checked_mul(ss, 4), cs, 0},
      true,
      true,
      false};
  if (!gemmini::valid_config(s.planner))
    throw Error(IM2P_CYCLE_INVALID, "planner rejected request");
  const auto tile_k = checked_mul(r.tile_k, dim);
  gemmini::LoopCursor cursor{};
  while (cursor.i < r.m) {
    const auto loop = gemmini::plan_loop(s.planner, r.m, cursor);
    if (loop.fragment_index > UINT16_MAX)
      throw Error(IM2P_CYCLE_UNSUPPORTED,
                  "fragment identity is not representable");
    const bool coalesce =
        !s.work.empty() && r.submission == IM2P_CYCLE_TILE_SUBMISSIONS &&
        dim != 64 && s.work.back().first_plan.i == loop.i &&
        s.work.back().first_plan.j == loop.j &&
        s.work.back().first_plan.k / tile_k == loop.k / tile_k;
    if (!coalesce) {
      ScheduledWork w;
      w.first_plan = loop;
      w.max_i = loop.ip / dim;
      w.max_j = loop.jp / dim;
      w.rows = loop.is;
      w.columns = loop.js;
      w.fragment_base = r.submission == IM2P_CYCLE_TILE_SUBMISSIONS
                            ? (loop.k % tile_k) / std::min(dim, 32u)
                            : loop.fragment_base;
      s.work.push_back(std::move(w));
    }
    auto &w = s.work.back();
    const uint64_t count = checked_add(s.fragments, loop.fragment_count);
    if (count > c.max_fragments)
      throw Error(IM2P_CYCLE_LIMIT, "planned fragment limit exceeded");
    for (size_t i = 0; i < loop.fragment_count; ++i) {
      auto f = gemmini::plan_fragment(s.planner, loop, i);
      w.fragments.push_back({f, s.planner_loops});
    }
    w.max_k += loop.kp / dim;
    w.reduction += loop.ks;
    w.scale_rows += loop.scale_rows;
    w.final_store = loop.final_contribution;
    if (uint64_t(w.max_i + w.max_j) * w.max_k * dim >
            uint64_t(c.hardware.bank_rows) * c.hardware.bank_count / 2 ||
        uint64_t(w.max_i) * w.max_j * dim > c.hardware.accumulator_rows / 2 ||
        w.scale_rows > RtlTimingProfile::scale_entries)
      throw Error(
          IM2P_CYCLE_UNSUPPORTED,
          "hardware loop exceeds scratchpad, accumulator or scale capacity");
    s.fragments = count;
    ++s.planner_loops;
    gemmini::advance_loop(s.planner, loop, cursor);
  }
  return s;
}
} // namespace im2p::cycle
