#include "control_engine.hpp"
#include <algorithm>
#include <set>
#include <tuple>

namespace im2p::cycle::detail {
namespace {
unsigned first_free(const DmaController &d, unsigned count) {
  for (unsigned i = 0; i < count; ++i)
    if (!d.tracker[i].valid)
      return i;
  return none;
}
int first_completed(const DmaController &d, unsigned count) {
  for (unsigned i = 0; i < count; ++i)
    if (d.tracker[i].valid && !d.tracker[i].remaining)
      return static_cast<int>(i);
  return -1;
}
bool dma_busy(const DmaController &d) {
  return !d.queue.empty() || std::any_of(d.tracker.begin(), d.tracker.end(),
                                         [](const auto &t) { return t.valid; });
}
struct Range {
  bool valid = false, acc = false;
  unsigned start = 0, end = 0;
};
bool overlaps(Range a, Range b) {
  return a.valid && b.valid && a.acc == b.acc && a.start < b.end &&
         b.start < a.end;
}
std::pair<Range, Range> operands(const Command &c) {
  if (c.config())
    return {};
  auto range = [](unsigned address, unsigned rows, bool acc) {
    return Range{address != none, acc, address, address + rows};
  };
  switch (c.kind) {
  case Kind::LoadA:
  case Kind::LoadB:
    return {range(c.dst, c.rows, false), {}};
  case Kind::Preload:
    return {range(c.dst, c.output_rows, true), range(c.src, c.rows, false)};
  case Kind::Compute:
    return {range(c.src, c.rows, false), {}};
  case Kind::Store:
    return {range(c.src, c.rows, true), {}};
  default:
    return {};
  }
}
} // namespace

Engine::Engine(const im2p_cycle_model_config_t &c,
               const im2p_cycle_request_t &r, const im2p_compact_runs_t *runs)
    : config(c), request(r), schedule(expand_work(c, r, runs)),
      profile{c.hardware, c.timing} {
  state.cycle = r.accepted_cycle;
  result.trace.enabled = r.record_events;
  result.trace.limit = c.max_trace_events;
  result.trace.work = r.logical_work_id;
  result.counters.abi_version = IM2P_CYCLE_MODEL_ABI_VERSION;
  result.counters.struct_size = sizeof(im2p_cycle_result_t);
  result.counters.start_cycle = r.accepted_cycle;
  result.counters.logical_work_count = 1;
  result.counters.planner_loop_count = schedule.planner_loops;
  result.counters.fragment_count = schedule.fragments;
  expand_commands();
}
U Engine::record(const State &s, EventType type, Resource resource,
                 const Command &c, U detail, U parent) {
  return result.trace.add(s.cycle, type, resource, s.frame, c.fragment,
                          parent ? parent : c.parent, detail, c.i, c.j, c.k);
}
void Engine::expand_commands() {
  const auto dim = profile.hardware.dim;
  unsigned half = request.initial_scratchpad_half,
           acc = request.initial_accumulator_half;
  for (unsigned widx = 0; widx < schedule.work.size(); ++widx) {
    const auto &w = schedule.work[widx];
    Programs p;
    std::set<std::pair<unsigned, unsigned>> seen_a, seen_b;
    const auto a_base = half * profile.hardware.bank_rows * 2;
    const auto b_base =
        (half + 1) * profile.hardware.bank_rows * 2 - w.max_k * w.max_j * dim;
    const auto c_base = acc * profile.hardware.accumulator_rows / 2;
    for (unsigned fidx = 0; fidx < w.fragments.size(); ++fidx) {
      const auto &f = w.fragments[fidx].plan;
      const unsigned i = (f.i - w.first_plan.i) / dim;
      const unsigned j = (f.j - w.first_plan.j) / dim;
      const unsigned k = (f.k - w.first_plan.k) / dim;
      Command command;
      command.frame = widx;
      command.fragment = fidx;
      command.i = i;
      command.j = j;
      command.k = k;
      command.matmul = true;
      command.rows = f.rows;
      command.cols = f.reduction;
      command.output_rows = f.rows;
      command.output_cols = f.columns;
      if (seen_a.emplace(k, i).second) {
        auto a = command;
        a.kind = Kind::LoadA;
        a.dst = a_base + (i * w.max_k + k) * dim;
        p.a.push_back(a);
      }
      if (seen_b.emplace(k, j).second) {
        auto b = command;
        b.kind = Kind::LoadB;
        b.rows = dim;
        b.cols = f.columns;
        b.dst = b_base + (k * w.max_j + j) * dim;
        p.b.push_back(b);
      }
      auto pre = command;
      pre.kind = Kind::Preload;
      pre.rows = f.reduction;
      pre.cols = f.columns;
      pre.src = i == 0 ? b_base + (k * w.max_j + j) * dim : none;
      pre.dst = c_base + (i * w.max_j + j) * dim;
      pre.accumulate = f.accumulate;
      p.execute.push_back(pre);
      auto compute = command;
      compute.kind = Kind::Compute;
      compute.src = a_base + (i * w.max_k + k) * dim;
      p.execute.push_back(compute);
      if (f.final_contribution && w.final_store) {
        auto store = command;
        store.kind = Kind::Store;
        store.src = c_base + (i * w.max_j + j) * dim;
        store.cols = f.columns;
        p.store.push_back(store);
      }
    }
    programs.push_back(std::move(p));
    half ^= 1;
    if (w.final_store)
      acc ^= 1;
  }
}

bool Engine::busy(const State &s) const {
  const auto &w = s.writeback;
  const bool station = std::any_of(s.station.begin(), s.station.end(),
                                   [](const auto &e) { return e.valid; });
  const bool tags =
      std::any_of(s.array.tags.begin(), s.array.tags.end(),
                  [](const auto &t) { return t.preload.rob != none; });
  const bool wb = !w.contexts.empty() || !w.writes.empty() ||
                  !w.commits.empty() || w.reserved ||
                  std::any_of(w.pending.begin(), w.pending.end(),
                              [](bool b) { return b; }) ||
                  !w.immediate.empty() || !w.delayed.empty();
  const auto &m = s.memory;
  return !s.raw.empty() || !s.loop_input.empty() || !s.unrolled.empty() ||
         s.loop.configured || station || dma_busy(s.load) ||
         dma_busy(s.store) || !s.execute.queue.empty() ||
         !s.execute.transpose.empty() || tags || wb || s.scale_state != 0 ||
         m.store_state != 0 || m.dma_response || m.settling ||
         std::any_of(m.active.begin(), m.active.end(),
                     [](bool b) { return b; });
}
void Engine::memory(State &n, const State &s, Signals &g) {
  const auto &m = s.memory;
  auto &nm = n.memory;
  nm.dma_response.reset();
  nm.settling = g.write      ? profile.hardware.accumulator_latency
                : m.settling ? m.settling - 1
                             : 0;
  // The newest due response is selected, exactly as Adapter::step's reverse
  // scan.
  for (size_t pos = m.responses.size(); pos-- > 0;) {
    const auto read = m.responses[pos];
    if (!read.timing.eligible(s.cycle))
      continue;
    nm.responses.erase(nm.responses.begin() + static_cast<std::ptrdiff_t>(pos));
    if (read.scale) {
      ++result.counters.scale_response_count;
      if (s.scale_state != 2)
        throw Error(IM2P_CYCLE_INTERNAL, "unexpected scale response");
      n.scale_state = 3;
      n.scale_column = 0;
      record(s, EventType::ScaleResponse, Resource::ScalePath, {}, read.id,
             read.parent);
    } else {
      nm.active[read.id] = false;
      nm.dma_response = read.command;
      record(s, EventType::ReadResponse, Resource::Backing, {}, read.id,
             read.parent);
    }
    break;
  }
  if (m.backing && (!profile.memory.read_ready_period ||
                    (s.cycle + profile.memory.backing_cycle_offset) %
                        profile.memory.read_ready_period)) {
    auto read = *m.backing;
    const U delay =
        profile.memory.backing_read_delay +
        U((read.id % 2 == 0) ? profile.memory.even_read_id_delay : 0) +
        (read.scale ? profile.memory.scale_read_extra_delay : 0);
    read.timing = TimingEvent::accepted(s.cycle, delay, P::backing_queue + 1,
                                        read.scale ? EventType::ScaleResponse
                                                   : EventType::ReadResponse,
                                        Resource::Backing);
    read.parent =
        record(s, read.scale ? EventType::ScaleRequest : EventType::ReadRequest,
               read.scale ? Resource::ScalePath : Resource::Backing, {},
               read.id, read.parent);
    nm.responses.push_back(read);
    nm.backing.reset();
    if (read.scale)
      ++result.counters.scale_request_count;
  }
  if (m.write_due && m.write_due->eligible(s.cycle)) {
    if (m.store_state != 3)
      throw Error(IM2P_CYCLE_INTERNAL, "unexpected backing write completion");
    nm.write_due.reset();
    nm.store_state = 0;
    g.dma_store_return = m.store_command;
    ++result.counters.store_response_count;
    record(s, EventType::WriteCompletion, Resource::Backing);
  }
  if (m.store_state == 1) {
    const bool hazard = std::any_of(
        s.writeback.commits.begin(), s.writeback.commits.end(),
        [&](const auto &c) { return c.address == m.store_address; });
    const auto bank_rows = profile.hardware.accumulator_rows / 2;
    const bool rmw =
        g.write && g.write->command.accumulate &&
        g.write->address / bank_rows == m.store_address / bank_rows;
    if (!m.acc_read_issued && !hazard && !rmw &&
        (!g.write || g.write->address != m.store_address)) {
      nm.acc_read_issued = true;
    } else if (m.acc_read_issued) {
      nm.acc_read_issued = false;
      nm.store_state = 2;
    }
  } else if (m.store_state == 2) {
    nm.store_state = 3;
    nm.write_due =
        TimingEvent::accepted(s.cycle, profile.memory.backing_write_delay, 1,
                              EventType::WriteCompletion, Resource::Backing);
    record(s, EventType::WriteRequest, Resource::Backing);
  }
}

void Engine::dma(State &n, const State &s, Signals &g) {
  const auto commands = profile.dma_commands();
  g.load_completed = first_completed(s.load, commands);
  g.store_completed = first_completed(s.store, commands);
  if (s.memory.dma_response) {
    auto &t = n.load.tracker[*s.memory.dma_response];
    if (!t.valid || !t.remaining)
      throw Error(IM2P_CYCLE_INTERNAL, "load return without credit");
    --t.remaining;
    ++result.counters.load_response_count;
  }
  if (g.dma_store_return) {
    auto &t = n.store.tracker[*g.dma_store_return];
    if (!t.valid || !t.remaining)
      throw Error(IM2P_CYCLE_INTERNAL, "store return without credit");
    --t.remaining;
  }
  for (unsigned which = 0; which < 2; ++which) {
    const auto &d = which == 0 ? s.load : s.store;
    auto &nd = which == 0 ? n.load : n.store;
    if (d.queue.empty())
      continue;
    const auto c = d.queue.front();
    if (d.state == 0 && c.config()) {
      nd.queue.pop_front();
      continue;
    }
    const auto free = first_free(d, commands);
    const bool allocate = d.state == 0 && free != none;
    const bool first = allocate || d.state == 1;
    const bool valid = first || (d.state == 2 && d.row != 0);
    unsigned id = d.command_id;
    if (allocate) {
      id = free;
      nd.command_id = id;
      nd.tracker[id] = {true, c.rows, c.rob};
      nd.state = 1;
    }
    const unsigned row = first ? 0 : d.row;
    bool fire = false;
    if (valid && which == 0) {
      unsigned read_id = none;
      for (unsigned i = 0; i < P::read_slots; ++i)
        if (!s.memory.active[i]) {
          read_id = i;
          break;
        }
      if (read_id != none && s.memory.queued.size() < P::read_queue) {
        fire = true;
        Read read;
        read.id = read_id;
        read.command = id;
        read.row = c.dst + row;
        read.parent =
            record(s, EventType::LoadDma, Resource::LoadEngine, c, read.row);
        g.dma_read = read;
        n.memory.active[read_id] = true;
        ++result.counters.load_request_count;
      }
    } else if (valid && which == 1 && s.memory.store_state == 0) {
      fire = true;
      n.memory.store_state = 1;
      n.memory.store_address = c.src + row;
      n.memory.store_command = id;
      ++result.counters.store_request_count;
      record(s, EventType::StoreDma, Resource::StoreEngine, c, c.src + row);
    }
    if (fire) {
      nd.row = (row + 1) % c.rows;
      nd.state = 2;
    }
    if (d.state == 2 && (d.row == 0 || (fire && d.row + 1 == c.rows))) {
      nd.queue.pop_front();
      nd.state = 0;
    }
  }
  // Scale has fixed priority into a non-flow, non-pipelined one-entry backing
  // queue.
  const bool top_space = !s.memory.backing;
  const bool scale_valid = s.scale_state == 1;
  const bool operand_valid = !s.memory.queued.empty() || g.dma_read.has_value();
  const bool operand_take = top_space && !scale_valid && operand_valid;
  if (top_space && scale_valid) {
    Read read;
    read.id = 15;
    read.scale = true;
    n.memory.backing = read;
    n.scale_state = 2;
  } else if (operand_take) {
    n.memory.backing =
        s.memory.queued.empty() ? *g.dma_read : s.memory.queued.front();
  }
  if (operand_take && !s.memory.queued.empty())
    n.memory.queued.pop_front();
  if (g.dma_read && !(operand_take && s.memory.queued.empty()))
    n.memory.queued.push_back(*g.dma_read);
}

void Engine::reservation(State &n, const State &s, Signals &g) {
  if (g.wb_completed)
    g.completed = g.wb_completed;
  else if (g.load_completed >= 0) {
    const auto index = static_cast<unsigned>(g.load_completed);
    g.completed = s.load.tracker[index].rob;
    n.load.tracker[index].valid = false;
  } else if (g.store_completed >= 0) {
    const auto index = static_cast<unsigned>(g.store_completed);
    g.completed = s.store.tracker[index].rob;
    n.store.tracker[index].valid = false;
  }
  if (g.wb_completed) {
    if (g.wb_grant == 0)
      n.writeback.immediate.pop_front();
    else
      n.writeback.delayed.pop_front();
    n.writeback.last_grant = static_cast<unsigned>(g.wb_grant);
  }
  if (!s.unrolled.empty()) {
    auto command = s.unrolled.front();
    const unsigned q = command.queue();
    const unsigned capacity = q == 0   ? P::load_entries
                              : q == 1 ? P::execute_entries
                                       : P::store_entries;
    unsigned slot = none;
    for (unsigned i = q * 16; i < q * 16 + capacity; ++i)
      if (!s.station[i].valid) {
        slot = i;
        break;
      }
    if (slot != none) {
      command.rob = slot;
      const auto [a, b] = operands(command);
      Entry e;
      e.valid = true;
      e.command = command;
      for (unsigned i = 0; i < s.station.size(); ++i) {
        const auto &old = s.station[i];
        if (!old.valid)
          continue;
        bool dependency = old.command.queue() == q && !old.issued;
        if (old.command.queue() != q && !command.config()) {
          const auto [oa, ob] = operands(old.command);
          if (q == 0)
            dependency = overlaps(a, oa) || overlaps(a, ob);
          else if (q == 1 && old.command.queue() == 0)
            dependency = overlaps(a, oa) || overlaps(b, oa);
          else if (q == 1 && old.command.queue() == 2)
            dependency = command.kind == Kind::Preload && overlaps(a, oa);
          else if (q == 2)
            dependency = (old.command.queue() == 0 ||
                          old.command.kind == Kind::Preload) &&
                         overlaps(a, oa);
        }
        if (dependency)
          e.deps |= U(1) << i;
      }
      e.command.parent = record(s, EventType::CommandAllocated,
                                Resource::Reservation, command, slot);
      n.station[slot] = e;
      n.unrolled.pop_front();
    }
  }
  for (unsigned q = 0; q < 3; ++q) {
    const unsigned capacity = q == 0   ? P::load_entries
                              : q == 1 ? P::execute_entries
                                       : P::store_entries;
    for (unsigned i = q * 16; i < q * 16 + capacity; ++i) {
      const auto &entry = s.station[i];
      if (!entry.valid || entry.issued || entry.deps)
        continue;
      auto command = entry.command;
      const bool room = q == 0 ? s.load.queue.size() < P::load_queue
                        : q == 2
                            ? s.store.queue.size() < P::store_queue
                            : s.execute.transpose.size() < P::transpose_queue;
      if (!room)
        break;
      if (command.kind == Kind::Preload) {
        const auto &wb = s.writeback;
        if (g.raw_write || wb.contexts.size() >= P::contexts || wb.pending[i] ||
            wb.reserved + wb.writes.size() + command.output_rows >
                profile.hardware.dim * P::execute_entries)
          break;
        n.writeback.contexts.push_back({command, 0});
        n.writeback.reserved += command.output_rows;
        n.writeback.pending[i] = true;
        n.writeback.raw_done[i] = n.writeback.write_done[i] = false;
        record(s, EventType::Context, Resource::ScuWriteback, command);
      }
      g.issued[q] = static_cast<int>(i);
      n.station[i].issued = true;
      const auto type = q == 0   ? EventType::LoadIssue
                        : q == 1 ? EventType::ExecuteIssue
                                 : EventType::StoreIssue;
      const auto resource = q == 0   ? Resource::LoadEngine
                            : q == 1 ? Resource::Array
                                     : Resource::StoreEngine;
      const U funct = command.kind == Kind::Preload   ? 6
                      : command.kind == Kind::Compute ? (command.i == 0 ? 4 : 5)
                                                      : 0;
      command.parent = record(s, type, resource, command, funct);
      if (q == 0)
        n.load.queue.push_back(command);
      else if (q == 2)
        n.store.queue.push_back(command);
      else
        n.execute.transpose.push_back(command);
      if (command.config() && q != 1)
        n.station[i].valid = false;
      for (auto &e : n.station)
        if (e.valid && (e.command.queue() == q || (command.config() && q != 1)))
          e.deps &= ~(U(1) << i);
      break;
    }
  }
  if (g.completed) {
    const unsigned rob = *g.completed;
    if (rob >= 64 || !s.station[rob].valid)
      throw Error(IM2P_CYCLE_INTERNAL, "completion for absent reservation");
    const auto &command = s.station[rob].command;
    n.station[rob].valid = false;
    for (auto &e : n.station)
      e.deps &= ~(U(1) << rob);
    if (command.matmul) {
      auto &u = n.loop.utilized[command.queue()];
      if (!u)
        throw Error(IM2P_CYCLE_INTERNAL, "controller credit underflow");
      --u;
    }
    record(s, EventType::RobCompleted, Resource::Reservation, command, rob);
  }
}

void Engine::control(State &n, const State &s, Signals &) {
  const auto &w = schedule.work[s.frame];
  const auto &p = programs[s.frame];
  const auto &l = s.loop;
  auto &nl = n.loop;
  const bool output_ready = s.unrolled.size() < P::unrolled_queue;
  std::optional<Command> generated;
  int stage = -1;
  const bool a_active = l.started[0] && l.position[0] < p.a.size();
  const bool b_active = l.started[1] && l.position[1] < p.b.size();
  const bool ex_active = l.started[3] && l.position[2] < p.execute.size();
  const bool st_active = l.started[4] && l.position[3] < p.store.size();
  auto ahead = [](const Command &next, const Command &target, bool a) {
    return next.k > target.k ||
           (next.k == target.k && (a ? next.i > target.i : next.j > target.j));
  };
  if (st_active && l.utilized[2] < P::store_entries) {
    const auto &c = p.store[l.position[3]];
    const auto &last =
        p.execute.empty()
            ? c
            : p.execute[std::min<size_t>(l.position[2], p.execute.size() - 1)];
    const bool ready =
        !ex_active || (last.k + 1 == w.max_k &&
                       (last.j > c.j || (last.j == c.j && last.i > c.i)));
    if (ready) {
      generated = c;
      stage = 3;
    }
  }
  if (!generated && ex_active && l.utilized[1] < P::execute_entries) {
    const auto &c = p.execute[l.position[2]];
    const bool a_done = !a_active || ahead(p.a[l.position[0]], c, true);
    // Preserve the pinned LoopMatmulExecute ldb_ahead expression exactly:
    // its same-K comparison reads ld_ka (A), not ld_kb (B). This affects
    // admission/queue events even when later DMA serialization hides the delta.
    const unsigned a_k = a_active ? p.a[l.position[0]].k : 0;
    const bool b_done = !b_active || p.b[l.position[1]].k > c.k ||
                        (a_k == c.k && p.b[l.position[1]].j > c.j);
    if (a_done && b_done) {
      generated = c;
      stage = 2;
    }
  }
  if (!generated && l.utilized[0] < P::load_entries) {
    bool choose_b = !a_active;
    if (a_active && b_active)
      choose_b = p.a[l.position[0]].k > p.b[l.position[1]].k ||
                 (p.b[l.position[1]].k == 0 && p.b[l.position[1]].j == 0);
    if (b_active && choose_b) {
      generated = p.b[l.position[1]];
      stage = 1;
    } else if (a_active && !choose_b) {
      generated = p.a[l.position[0]];
      stage = 0;
    }
  }
  if (generated && output_ready) {
    auto command = *generated;
    command.parent = record(s, EventType::LoopIssue, Resource::LoopController,
                            command, static_cast<U>(command.kind));
    n.unrolled.push_back(command);
    ++nl.position[static_cast<unsigned>(stage)];
    ++nl.utilized[command.queue()];
  }
  bool input_pop = false;
  if (!s.loop_input.empty()) {
    const auto command = s.loop_input.front();
    if (command.kind == Kind::LoopControl && !l.configured) {
      input_pop = true;
      if (command.index == 10) {
        nl.configured = true;
        nl.position.fill(0);
        nl.started.fill(false);
        nl.completed.fill(false);
      }
    } else if (command.kind != Kind::LoopControl && !l.configured &&
               output_ready && !generated) {
      n.unrolled.push_back(command);
      input_pop = true;
    }
  }
  if (input_pop)
    n.loop_input.pop_front();
  if (!s.raw.empty() && s.loop_input.size() < P::loop_queue) {
    n.loop_input.push_back(s.raw.front());
    n.raw.pop_front();
  }
  if (l.configured) {
    for (unsigned i : {0u, 1u, 2u})
      if (!l.started[i]) {
        nl.started[i] = true;
        nl.running = true;
      }
    if (!l.started[3] && l.started[0] && l.started[1] && l.started[2]) {
      nl.started[3] = true;
      nl.running = true;
    }
    if (!l.started[4] && l.started[3]) {
      nl.started[4] = true;
      nl.running = true;
    }
  }
  if (l.running) {
    if (l.started[0] && !a_active)
      nl.completed[0] = true;
    if (l.started[1] && !b_active)
      nl.completed[1] = true;
    if (l.started[2])
      nl.completed[2] = true;
    if (l.started[3] && !ex_active)
      nl.completed[3] = true;
    if (l.started[4] && !st_active)
      nl.completed[4] = true;
    if (std::all_of(l.completed.begin(), l.completed.end(),
                    [](bool b) { return b; })) {
      nl.configured = false;
      nl.running = false;
    }
  }
  const bool controller_busy = busy(s);
  if (controller_busy && s.bridge_issued)
    n.saw_busy = true;
  if (s.bridge_state == 0) {
    if (s.bridge_pending) {
      n.bridge_state = 1;
      n.bridge_index = 0;
    } else if (s.bridge_issued && s.saw_busy && !controller_busy)
      n.bridge_state = 2;
  } else if (s.bridge_state == 1 && s.raw.size() < P::incoming_queue) {
    Command c;
    c.frame = s.frame;
    c.index = s.bridge_index;
    c.kind = c.index < 3    ? Kind::LoadConfig
             : c.index == 3 ? Kind::StoreConfig
             : c.index == 4 ? Kind::ExecuteConfig
                            : Kind::LoopControl;
    c.parent =
        record(s, EventType::BridgeIssue, Resource::CommandBridge, c, c.index);
    n.raw.push_back(c);
    if (c.index == 10) {
      n.bridge_state = 0;
      n.bridge_pending = false;
      n.bridge_issued = true;
      if (!controller_busy)
        n.saw_busy = false;
    } else
      ++n.bridge_index;
  } else if (s.bridge_state == 2) {
    record(s, EventType::LoopDone, Resource::HostWork);
    n.bridge_issued = false;
    n.bridge_state = 0;
    n.saw_busy = false;
    n.host = 2;
    n.release = 0;
    if (s.frame + 1 == schedule.work.size()) {
      result.counters.done_cycle = s.cycle;
      result.counters.total_cycles = s.cycle - request.accepted_cycle;
      result.service.result_ready_cycle = s.cycle;
      record(s, EventType::LogicalDone, Resource::HostWork);
      n.host = drain_final_release ? 2 : 3;
    }
  }
  if (s.host == 0 && s.scale_state == 0) {
    n.host = 1;
    n.scale_state = 1;
    n.scale_row = n.scale_column = 0;
    ++result.counters.loop_count;
    Command c;
    c.frame = s.frame;
    record(s, EventType::Work, Resource::HostWork, c);
  } else if (s.scale_state == 3) {
    record(s, EventType::ScaleLane, Resource::ScalePath, {}, s.scale_column);
    if (s.scale_column + 1 == profile.hardware.dim) {
      n.scale_column = 0;
      if (s.scale_row + 1 == w.scale_rows)
        n.scale_state = 4;
      else {
        ++n.scale_row;
        n.scale_state = 1;
      }
    } else
      ++n.scale_column;
  } else if (s.scale_state == 4) {
    // The bridge's pending queue flows through into its select state.
    n.scale_state = 0;
    n.bridge_pending = true;
    if (s.bridge_state == 0 && !s.bridge_issued) {
      n.bridge_state = 1;
      n.bridge_index = 0;
    }
  }
  if (s.host == 2) {
    if (s.release < w.scale_rows * profile.hardware.dim) {
      record(s, EventType::ScaleRelease, Resource::ScalePath, {}, s.release);
      ++n.release;
      if (s.frame + 1 == schedule.work.size())
        result.service.final_scale_release_cycle = s.cycle;
    } else {
      if (s.frame + 1 == schedule.work.size()) {
        n.host = 3;
        // UpstreamWsHp1Top clears activeSlots on the registered zero count;
        // work.ready observes that ownership update on the following cycle.
        result.service.resource_ready_cycle = checked_add(s.cycle, 1);
      } else {
        ++n.frame;
        n.host = 0;
        n.loop = {};
      }
    }
  }
}
void Engine::step() {
  State next = state;
  Signals signals;
  execute(next, state, signals);
  writeback(next, state, signals);
  memory(next, state, signals);
  dma(next, state, signals);
  reservation(next, state, signals);
  control(next, state, signals);
  next.cycle = checked_add(state.cycle, 1);
  state = std::move(next);
}
ModelResult Engine::run(bool drain_release) {
  drain_final_release = drain_release;
  while (state.host != 3) {
    if (state.cycle - request.accepted_cycle >= config.max_cycles)
      throw Error(IM2P_CYCLE_LIMIT,
                  drain_final_release ? "cycle budget exhausted before resource release"
                                      : "cycle budget exhausted before logical completion");
    step();
  }
  result.counters.event_count = result.trace.events.size();
  result.service.abi_version = IM2P_CYCLE_SERVICE_ABI_VERSION;
  result.service.struct_size = sizeof(result.service);
  result.service.next_scratchpad_half =
      request.initial_scratchpad_half ^ (schedule.work.size() % 2);
  result.service.next_accumulator_half = request.initial_accumulator_half ^
      (std::count_if(schedule.work.begin(), schedule.work.end(),
                     [](const auto &work) { return work.final_store; }) % 2);
  if (result.counters.load_request_count !=
          result.counters.load_response_count ||
      result.counters.store_request_count !=
          result.counters.store_response_count ||
      result.counters.scale_request_count !=
          result.counters.scale_response_count)
    throw Error(IM2P_CYCLE_INTERNAL,
                "logical completion before response conservation");
  return std::move(result);
}
} // namespace im2p::cycle::detail

namespace im2p::cycle {
ModelResult estimate(const im2p_cycle_model_config_t &config,
                     const im2p_cycle_request_t &request,
                     const im2p_compact_runs_t *runs, bool drain_final_release) {
  return detail::Engine(config, request, runs).run(drain_final_release);
}
} // namespace im2p::cycle
