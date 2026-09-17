#include "control_engine.hpp"
#include <algorithm>

namespace im2p::cycle::detail {
namespace {
bool any(const std::array<bool, 3> &v) { return v[0] || v[1] || v[2]; }
bool all(const std::array<bool, 3> &v) { return v[0] && v[1] && v[2]; }
} // namespace

void Engine::execute(State &n, const State &s, Signals &g) {
  const auto dim = profile.hardware.dim;
  const auto &a = s.array;
  auto &na = n.array;
  const auto &ex = s.execute;
  auto &nx = n.execute;
  // A row token represents the complete spatial wavefront's metadata. There is
  // no per-PE state, numerical operand, partial product, scale or accumulator.
  if (!a.outputs.empty() && a.outputs.front().timing.eligible(s.cycle)) {
    const auto row = a.outputs.front();
    na.outputs.pop_front();
    if (!a.tags.empty() && a.tags.front().id == row.id) {
      const auto tag = a.tags.front();
      if (tag.preload.rob != none) {
        if (a.output_counter < tag.preload.output_rows) {
          Write w;
          w.command = tag.preload;
          w.address = tag.preload.dst + a.output_counter;
          w.parent = record(s, EventType::ArrayOutput, Resource::Array,
                            w.command, a.output_counter, row.parent);
          g.raw_write = w;
        }
        na.output_counter = row.last ? 0 : a.output_counter + 1;
        if (row.last)
          g.raw_completed = tag.preload.rob;
      }
      if (row.last)
        na.tags.pop_front();
    }
    if (!a.row_counts.empty() && a.row_counts.front().id == row.id && row.last)
      na.row_counts.pop_front();
  }
  const bool row_input = a.request && all(a.written);
  const bool last_input = row_input && a.counter + 1 == a.rows;
  const bool mesh_request_ready = (!a.request || last_input) &&
                                  a.tags.size() < P::tag_queue &&
                                  a.row_counts.size() < P::tag_queue;
  std::array<bool, 3> side_ready{}, side_fire{};
  for (unsigned side = 0; side < 3; ++side)
    side_ready[side] = !a.written[side] || row_input || mesh_request_ready;
  bool control_pop = false, mesh_request = false;
  if (!ex.controls.empty()) {
    const auto &c = ex.controls.front();
    control_pop = !c.first || mesh_request_ready;
    for (unsigned side = 0; side < 3; ++side) {
      const bool valid = !c.reads[side] || s.banks[c.banks[side]].pipe.back();
      side_fire[side] = c.fire[side] && valid && side_ready[side];
      control_pop &= !c.fire[side] || side_fire[side] || !side_ready[side];
    }
    if (control_pop) {
      for (unsigned side = 0; side < 3; ++side)
        if (c.fire[side] && side_fire[side] && c.reads[side])
          g.sp_consume[c.banks[side]] = true;
      mesh_request = any(c.fire) && mesh_request_ready;
      nx.controls.pop_front();
    }
    if (mesh_request) {
      na.request = true;
      na.rows = c.rows;
      na.id = (a.id + 1) % P::mesh_ids;
      na.tags.push_back({(a.id + 2) % P::mesh_ids, c.rows, c.preload});
      na.row_counts.push_back({(a.id + 1) % P::mesh_ids, c.rows, {}});
    }
  }
  if (row_input) {
    const U parent =
        record(s, EventType::ArrayInput, Resource::Array, {}, a.counter);
    na.outputs.push_back(
        {TimingEvent::accepted(s.cycle, profile.array_latency(), 1,
                               EventType::ArrayOutput, Resource::Array),
         a.id, last_input, parent});
    na.written.fill(false);
    na.counter = last_input ? 0 : a.counter + 1;
    if (last_input && !mesh_request)
      na.request = false;
  }
  for (unsigned side = 0; side < 3; ++side)
    if (side_fire[side])
      na.written[side] = true;

  // ScratchpadBank's one synchronous read and flow/pipe response queue feed the
  // four-stage elastic SP response pipe. Latency and initiation interval
  // differ.
  for (unsigned bank = 0; bank < s.banks.size(); ++bank) {
    const auto &old = s.banks[bank];
    auto &next = n.banks[bank];
    std::array<bool, 4> ready{};
    bool downstream = g.sp_consume[bank];
    for (unsigned i = 4; i-- > 0;) {
      ready[i] = !old.pipe[i] || downstream;
      downstream = ready[i];
    }
    const bool q_valid = old.queued || old.pending;
    const bool q_pop = q_valid && ready[0];
    const bool q_push = old.pending && (!old.queued || ready[0]);
    const unsigned count =
        unsigned(old.queued) + unsigned(q_push) - unsigned(q_pop);
    if (count > 1)
      throw Error(IM2P_CYCLE_INTERNAL, "scratchpad response queue overflow");
    next.queued = count != 0;
    g.sp_ready[bank] = count == 0;
    for (unsigned i = 0; i < 4; ++i)
      if (ready[i])
        next.pipe[i] = i == 0 ? q_valid : old.pipe[i - 1];
    next.pending = false;
  }

  const bool tags_busy =
      std::any_of(a.tags.begin(), a.tags.end(),
                  [](const auto &t) { return t.preload.rob != none; });
  unsigned mode = ex.mode;
  if (!mode && !ex.queue.empty()) {
    const auto &first = ex.queue[0];
    if (first.kind == Kind::ExecuteConfig) {
      if (!tags_busy && ex.pending[0] == none && ex.pending[1] == none) {
        nx.queue.pop_front();
        g.raw_completed = first.rob;
      }
    } else if (first.kind == Kind::Preload && ex.queue.size() >= 2)
      mode = 1;
    else if (first.kind == Kind::Compute) {
      mode = ex.queue.size() >= 2 && ex.queue[1].kind == Kind::Preload ? 3 : 2;
    }
  }
  const bool cntl_ready =
      ex.controls.size() < profile.hardware.scratchpad_read_delay + 1 ||
      control_pop;
  if (mode) {
    const bool head_preload = ex.queue.front().kind == Kind::Preload;
    Command compute = ex.queue.at(head_preload ? 1 : 0);
    Command preload;
    if (mode == 1)
      preload = ex.queue.front();
    else if (mode == 3)
      preload = ex.queue.at(1);
    else {
      preload.rob = none;
      preload.src = none;
      preload.dst = none;
    }
    const bool a_enabled = mode != 1;
    const bool d_enabled = mode != 2;
    const bool real_a = a_enabled && compute.src != none;
    const bool real_d = d_enabled && preload.src != none;
    const unsigned rows =
        real_d ? dim
               : std::max(P::minimum_compute_rows, real_a ? compute.rows : 1u);
    RowControl c;
    c.rows = rows;
    c.preload = preload;
    c.first = !any(ex.started);
    std::array<unsigned, 3> addresses{{compute.src + ex.counter[0], none,
                                       preload.src + dim - 1 - ex.counter[2]}};
    const std::array<bool, 3> real{{real_a, false, real_d}};
    const std::array<bool, 3> nonpadding{
        {ex.counter[0] < compute.rows, false,
         dim - 1 - ex.counter[2] < preload.rows}};
    std::array<bool, 3> valid{};
    for (unsigned side = 0; side < 3; ++side) {
      c.banks[side] =
          real[side] ? addresses[side] / profile.hardware.bank_rows : 0;
      if (c.banks[side] >= 4)
        throw Error(IM2P_CYCLE_INTERNAL, "scratchpad bank address overflow");
      valid[side] = true;
      for (unsigned other = 0; other < 3; ++other) {
        if (side == other)
          continue;
        const bool one_ahead =
            ex.started[side] &&
            ex.counter[side] == (ex.counter[other] + 1) % rows;
        const bool same_counter = ex.started[side] == ex.started[other] &&
                                  ex.counter[side] == ex.counter[other];
        const bool same_bank =
            real[side] && real[other] &&
            addresses[side] / profile.hardware.bank_rows ==
                addresses[other] / profile.hardware.bank_rows;
        if (one_ahead || (same_bank && other < side && same_counter))
          valid[side] = false;
      }
      c.reads[side] = real[side] && nonpadding[side];
      const bool ready = !c.reads[side] || g.sp_ready[c.banks[side]];
      c.fire[side] = valid[side] && ready;
      if (valid[side] && c.reads[side] && cntl_ready && ready)
        g.sp_request[c.banks[side]] = true;
    }
    nx.mode = mode;
    bool finished = any(ex.started) && cntl_ready;
    for (unsigned side = 0; side < 3; ++side)
      finished &= ex.counter[side] == 0 ||
                  (ex.counter[side] + 1 == rows && c.fire[side]);
    if (cntl_ready) {
      nx.controls.push_back(c);
      for (unsigned side = 0; side < 3; ++side)
        if (c.fire[side]) {
          nx.counter[side] = (ex.counter[side] + 1) % rows;
          nx.started[side] = true;
        }
    }
    if (!ex.mode)
      record(s, mode == 1 ? EventType::PreloadIssue : EventType::ComputeIssue,
             Resource::Array, mode == 1 ? preload : compute);
    if (finished) {
      const unsigned pop = mode == 3 ? 2 : 1;
      for (unsigned i = 0; i < pop; ++i)
        nx.queue.pop_front();
      nx.mode = 0;
      nx.started.fill(false);
      if (mode == 2 || mode == 3)
        nx.pending[0] = compute.rob;
      if ((mode == 1 || mode == 3) && preload.dst == none)
        nx.pending[mode == 3 ? 1 : 0] = preload.rob;
    }
  } else {
    nx.counter.fill(0);
  }
  U mask = 0;
  for (unsigned bank = 0; bank < g.sp_request.size(); ++bank) {
    n.banks[bank].pending = g.sp_request[bank];
    if (g.sp_request[bank])
      mask |= U(1) << bank;
  }
  if (mask)
    record(s, EventType::ScratchpadRead, Resource::Scratchpad, {}, mask);

  if (!g.raw_completed) {
    for (unsigned i = 0; i < ex.pending.size(); ++i)
      if (ex.pending[i] != none) {
        g.raw_completed = ex.pending[i];
        nx.pending[i] = none;
        break;
      }
  }
  if (g.raw_completed)
    record(s, EventType::RawCompleted, Resource::Array, {}, *g.raw_completed);
  // The retained non-transpose path still traverses its two-entry unroller.
  if (!ex.transpose.empty() && ex.queue.size() < P::execute_queue) {
    nx.queue.push_back(ex.transpose.front());
    nx.transpose.pop_front();
  }
}

void Engine::writeback(State &n, const State &s, Signals &g) {
  const auto &w = s.writeback;
  auto &nw = n.writeback;
  if (!w.commits.empty() && w.commits.front().timing.eligible(s.cycle)) {
    const auto committed = w.commits.front();
    nw.commits.pop_front();
    if (committed.last)
      nw.write_done[committed.command.rob] = true;
    record(s, EventType::AccumulatorCommit, Resource::Accumulator,
           committed.command, committed.address, committed.parent);
  }
  if (!w.writes.empty()) {
    const auto pending = w.writes.front();
    const bool hazard =
        std::any_of(w.commits.begin(), w.commits.end(), [&](const auto &c) {
          return c.address == pending.address;
        });
    if (!hazard) {
      g.write = pending;
      g.write->timing = TimingEvent::accepted(
          s.cycle, profile.hardware.accumulator_latency, 1,
          EventType::AccumulatorCommit, Resource::Accumulator);
      g.write->parent =
          record(s, EventType::AccumulatorWrite, Resource::Accumulator,
                 pending.command, pending.address, pending.parent);
      nw.writes.pop_front();
      nw.commits.push_back(*g.write);
    }
  }
  if (g.raw_write) {
    if (w.contexts.empty() || !w.reserved)
      throw Error(IM2P_CYCLE_INTERNAL, "unreserved array response");
    const auto context = w.contexts.front();
    auto write = *g.raw_write;
    if (context.command.rob != write.command.rob)
      throw Error(IM2P_CYCLE_INTERNAL, "array/context response order mismatch");
    write.last = context.received + 1 == context.command.output_rows;
    nw.writes.push_back(write);
    --nw.reserved;
    if (write.last)
      nw.contexts.pop_front();
    else
      ++nw.contexts.front().received;
  }
  if (g.raw_completed) {
    const unsigned id = *g.raw_completed;
    if (id >= 64)
      throw Error(IM2P_CYCLE_INTERNAL, "invalid raw completion id");
    if (w.pending[id])
      nw.raw_done[id] = true;
    else {
      if (w.immediate.size() >= P::completion_queue)
        throw Error(IM2P_CYCLE_INTERNAL, "immediate completion overflow");
      nw.immediate.push_back(id);
    }
  }
  for (unsigned id = 0; id < w.pending.size(); ++id) {
    if (w.pending[id] && w.raw_done[id] && w.write_done[id] &&
        w.delayed.size() < P::completion_queue) {
      nw.pending[id] = false;
      nw.raw_done[id] = false;
      nw.write_done[id] = false;
      nw.delayed.push_back(id);
      break;
    }
  }
  if (!w.immediate.empty() || !w.delayed.empty()) {
    const bool choose_delayed =
        !w.delayed.empty() && (w.immediate.empty() || w.last_grant == 0);
    g.wb_grant = choose_delayed ? 1 : 0;
    g.wb_completed = choose_delayed ? w.delayed.front() : w.immediate.front();
  }
}
} // namespace im2p::cycle::detail
