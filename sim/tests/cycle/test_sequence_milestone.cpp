#include "../../include/im2p_cycle_sequence.h"

#undef NDEBUG
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <limits>
#include <memory>
#include <vector>

using Session =
    std::unique_ptr<im2p_cycle_sequence_t,
                    decltype(&im2p_cycle_sequence_destroy)>;

static im2p_cycle_sequence_config_t config(uint64_t event_capacity = 4096) {
  im2p_cycle_sequence_config_t value;
  im2p_cycle_sequence_config_init(&value);
  value.hardware = {8, 8, 16, 32, 32, 4, 4096, 1024, 16, 64, 4, 2};
  value.max_trace_events = event_capacity;
  return value;
}

static Session session(const im2p_cycle_sequence_config_t &config,
                       bool reset = true) {
  im2p_cycle_sequence_t *raw = nullptr;
  assert(im2p_cycle_sequence_create(&config, &raw) ==
         IM2P_CYCLE_SEQUENCE_OK);
  Session result(raw, im2p_cycle_sequence_destroy);
  if (reset)
    assert(im2p_cycle_sequence_reset(result.get()) ==
           IM2P_CYCLE_SEQUENCE_OK);
  return result;
}

static im2p_cycle_sequence_descriptor_t work(uint64_t id,
                                             bool record_events = false) {
  im2p_cycle_sequence_descriptor_t value;
  im2p_cycle_sequence_descriptor_init(&value);
  value.logical_work_id = id;
  value.m = value.n = 1;
  value.k = 22;
  value.tile_k = 2;
  value.submission = IM2P_CYCLE_TILE_SUBMISSIONS;
  value.record_events = record_events;
  return value;
}

static im2p_cycle_sequence_status_t status(im2p_cycle_sequence_t *sequence) {
  im2p_cycle_sequence_status_t value;
  im2p_cycle_sequence_status_init(&value);
  assert(im2p_cycle_sequence_get_status(sequence, &value) ==
         IM2P_CYCLE_SEQUENCE_OK);
  return value;
}

static im2p_cycle_result_t counters(im2p_cycle_sequence_t *sequence) {
  im2p_cycle_result_t value{};
  value.abi_version = IM2P_CYCLE_MODEL_ABI_VERSION;
  value.struct_size = sizeof(value);
  assert(im2p_cycle_sequence_get_cumulative_counters(sequence, &value) ==
         IM2P_CYCLE_SEQUENCE_OK);
  return value;
}

static im2p_cycle_sequence_row_pressure_t
row_pressure(im2p_cycle_sequence_t *sequence) {
  im2p_cycle_sequence_row_pressure_t value;
  im2p_cycle_sequence_row_pressure_init(&value);
  assert(im2p_cycle_sequence_get_row_pressure(sequence, &value) ==
         IM2P_CYCLE_SEQUENCE_OK);
  return value;
}

static im2p_cycle_sequence_domain_snapshot_t
domain(im2p_cycle_sequence_t *sequence) {
  im2p_cycle_sequence_domain_snapshot_t value;
  im2p_cycle_sequence_domain_snapshot_init(&value);
  assert(im2p_cycle_sequence_get_domain_snapshot(sequence, &value) ==
         IM2P_CYCLE_SEQUENCE_OK);
  return value;
}

static void same_state(const im2p_cycle_sequence_status_t &left,
                       const im2p_cycle_sequence_status_t &right) {
  assert(left.generation == right.generation);
  assert(left.cursor == right.cursor);
  assert(left.session_cycles == right.session_cycles);
  assert(left.work_cycles == right.work_cycles);
  assert(left.accepted_work_id == right.accepted_work_id);
  assert(left.offered_cycle == right.offered_cycle);
  assert(left.accepted_cycle == right.accepted_cycle);
  assert(left.last_discarded_generation == right.last_discarded_generation);
  assert(left.initialized == right.initialized);
  assert(left.has_pending == right.has_pending);
  assert(left.has_active == right.has_active);
  assert(left.has_report == right.has_report);
  assert(left.has_accepted == right.has_accepted);
  assert(left.faulted == right.faulted);
  assert(left.next_scratchpad_half == right.next_scratchpad_half);
  assert(left.next_accumulator_half == right.next_accumulator_half);
}

template <typename T>
static void same_bytes(const T &left, const T &right) {
  assert(std::memcmp(&left, &right, sizeof(T)) == 0);
}

static im2p_cycle_sequence_report_t
pop_report(im2p_cycle_sequence_t *sequence) {
  im2p_cycle_sequence_report_t value;
  im2p_cycle_sequence_report_init(&value);
  assert(im2p_cycle_sequence_pop_report(sequence, &value) ==
         IM2P_CYCLE_SEQUENCE_OK);
  return value;
}

static void append_events(im2p_cycle_sequence_t *sequence,
                          std::vector<im2p_cycle_sequence_event_t> &output) {
  uint64_t count = 0;
  assert(im2p_cycle_sequence_read_events(sequence, nullptr, 0, &count) ==
         IM2P_CYCLE_SEQUENCE_OK);
  if (!count)
    return;
  std::vector<im2p_cycle_sequence_event_t> events(count);
  uint64_t copied = 0;
  assert(im2p_cycle_sequence_read_events(sequence, events.data(), count,
                                         &copied) ==
             IM2P_CYCLE_SEQUENCE_OK &&
         copied == count);
  output.insert(output.end(), events.begin(), events.end());
}

static void legacy_to_report(
    im2p_cycle_sequence_t *sequence,
    std::vector<im2p_cycle_sequence_event_t> *events = nullptr) {
  for (;;) {
    const auto before = status(sequence);
    if (before.has_report)
      break;
    const int code =
        im2p_cycle_sequence_advance_until(sequence, before.cursor + 1);
    if (code == IM2P_CYCLE_SEQUENCE_WOULD_BLOCK) {
      assert(status(sequence).stop_reason ==
             IM2P_CYCLE_SEQUENCE_STOP_EVENT_BUFFER_AVAILABLE);
      assert(events);
      append_events(sequence, *events);
    } else {
      assert(code == IM2P_CYCLE_SEQUENCE_INCOMPLETE ||
             code == IM2P_CYCLE_SEQUENCE_OK);
    }
  }
  if (events)
    append_events(sequence, *events);
}

static unsigned boundary_to_report(
    im2p_cycle_sequence_t *sequence, uint64_t max_steps,
    std::vector<im2p_cycle_sequence_event_t> *events = nullptr) {
  unsigned soft_stops = 0;
  for (;;) {
    const int code = im2p_cycle_sequence_advance_to_boundary(
        sequence, std::numeric_limits<uint64_t>::max(), max_steps);
    const auto current = status(sequence);
    if (code == IM2P_CYCLE_SEQUENCE_OK) {
      assert(current.has_report &&
             current.stop_reason ==
                 IM2P_CYCLE_SEQUENCE_STOP_REPORT_AVAILABLE);
      break;
    }
    if (code == IM2P_CYCLE_SEQUENCE_WOULD_BLOCK) {
      assert(current.stop_reason ==
             IM2P_CYCLE_SEQUENCE_STOP_EVENT_BUFFER_AVAILABLE);
      assert(events);
      append_events(sequence, *events);
      continue;
    }
    assert(code == IM2P_CYCLE_SEQUENCE_INCOMPLETE &&
           current.stop_reason == IM2P_CYCLE_SEQUENCE_STOP_SOFT_BUDGET);
    ++soft_stops;
  }
  if (events)
    append_events(sequence, *events);
  return soft_stops;
}

static void test_idle_pending_report_and_q_edge() {
  const auto settings = config();
  auto legacy = session(settings);
  auto boundary = session(settings);

  for (uint64_t cycle = 1; cycle <= 5; ++cycle)
    assert(im2p_cycle_sequence_advance_until(legacy.get(), cycle) ==
           IM2P_CYCLE_SEQUENCE_OK);
  assert(im2p_cycle_sequence_advance_to_boundary(boundary.get(), 5, 2) ==
         IM2P_CYCLE_SEQUENCE_INCOMPLETE);
  assert(status(boundary.get()).cursor == 2 &&
         status(boundary.get()).stop_reason ==
             IM2P_CYCLE_SEQUENCE_STOP_SOFT_BUDGET);
  assert(im2p_cycle_sequence_advance_to_boundary(boundary.get(), 5, 8) ==
         IM2P_CYCLE_SEQUENCE_OK);
  assert(status(boundary.get()).stop_reason ==
         IM2P_CYCLE_SEQUENCE_STOP_TARGET);
  same_state(status(legacy.get()), status(boundary.get()));
  same_bytes(counters(legacy.get()), counters(boundary.get()));

  auto first = work(10);
  auto second = work(11);
  assert(im2p_cycle_sequence_offer(legacy.get(), &first, 8) ==
         IM2P_CYCLE_SEQUENCE_OK);
  assert(im2p_cycle_sequence_offer(boundary.get(), &first, 8) ==
         IM2P_CYCLE_SEQUENCE_OK);
  for (uint64_t cycle = 6; cycle <= 8; ++cycle)
    assert(im2p_cycle_sequence_advance_until(legacy.get(), cycle) ==
           IM2P_CYCLE_SEQUENCE_INCOMPLETE);
  assert(im2p_cycle_sequence_advance_to_boundary(boundary.get(), 8, 16) ==
         IM2P_CYCLE_SEQUENCE_INCOMPLETE);
  auto at_q = status(boundary.get());
  assert(at_q.cursor == 8 && at_q.has_pending && !at_q.has_active &&
         at_q.stop_reason == IM2P_CYCLE_SEQUENCE_STOP_TARGET);
  same_state(status(legacy.get()), at_q);
  assert(im2p_cycle_sequence_advance_to_boundary(boundary.get(), 8, 1) ==
         IM2P_CYCLE_SEQUENCE_INCOMPLETE);
  same_state(at_q, status(boundary.get()));

  assert(im2p_cycle_sequence_advance_until(legacy.get(), 9) ==
         IM2P_CYCLE_SEQUENCE_INCOMPLETE);
  assert(im2p_cycle_sequence_advance_to_boundary(boundary.get(), 9, 1) ==
         IM2P_CYCLE_SEQUENCE_INCOMPLETE);
  same_state(status(legacy.get()), status(boundary.get()));
  assert(status(boundary.get()).accepted_cycle == 8);
  assert(im2p_cycle_sequence_offer(legacy.get(), &second, 9) ==
         IM2P_CYCLE_SEQUENCE_OK);
  assert(im2p_cycle_sequence_offer(boundary.get(), &second, 9) ==
         IM2P_CYCLE_SEQUENCE_OK);

  legacy_to_report(legacy.get());
  assert(boundary_to_report(boundary.get(), 13) > 0);
  const auto report_q = status(boundary.get()).cursor;
  same_state(status(legacy.get()), status(boundary.get()));
  assert(status(boundary.get()).has_report &&
         status(boundary.get()).has_pending);

  const auto before_repeat = status(boundary.get());
  assert(im2p_cycle_sequence_advance_to_boundary(
             boundary.get(), std::numeric_limits<uint64_t>::max(), 1) ==
         IM2P_CYCLE_SEQUENCE_OK);
  const auto after_repeat = status(boundary.get());
  same_state(before_repeat, after_repeat);
  assert(after_repeat.cursor == report_q &&
         after_repeat.stop_reason ==
             IM2P_CYCLE_SEQUENCE_STOP_REPORT_AVAILABLE);

  const auto first_legacy = pop_report(legacy.get());
  const auto first_boundary = pop_report(boundary.get());
  same_bytes(first_legacy, first_boundary);
  assert(first_boundary.resource_ready_cycle == report_q);

  const auto legacy_at_q = status(legacy.get());
  const auto boundary_at_q = status(boundary.get());
  assert(im2p_cycle_sequence_advance_until(legacy.get(), report_q) ==
         IM2P_CYCLE_SEQUENCE_INCOMPLETE);
  assert(im2p_cycle_sequence_advance_to_boundary(boundary.get(), report_q, 1) ==
         IM2P_CYCLE_SEQUENCE_INCOMPLETE);
  same_state(legacy_at_q, status(legacy.get()));
  same_state(boundary_at_q, status(boundary.get()));

  assert(im2p_cycle_sequence_advance_until(legacy.get(), report_q + 1) ==
         IM2P_CYCLE_SEQUENCE_INCOMPLETE);
  assert(im2p_cycle_sequence_advance_to_boundary(boundary.get(), report_q + 1,
                                                  1) ==
         IM2P_CYCLE_SEQUENCE_INCOMPLETE);
  same_state(status(legacy.get()), status(boundary.get()));
  assert(status(boundary.get()).accepted_work_id == second.logical_work_id &&
         status(boundary.get()).accepted_cycle == report_q &&
         status(boundary.get()).cursor == report_q + 1);

  legacy_to_report(legacy.get());
  assert(boundary_to_report(boundary.get(), 11) > 0);
  same_state(status(legacy.get()), status(boundary.get()));
  same_bytes(pop_report(legacy.get()), pop_report(boundary.get()));
  same_bytes(counters(legacy.get()), counters(boundary.get()));
  const auto legacy_rows = row_pressure(legacy.get());
  const auto boundary_rows = row_pressure(boundary.get());
  assert(legacy_rows.row_count == boundary_rows.row_count &&
         legacy_rows.max_row_occupancy == boundary_rows.max_row_occupancy);
  const auto legacy_domain = domain(legacy.get());
  const auto boundary_domain = domain(boundary.get());
  assert(legacy_domain.max_tag_occupancy ==
             boundary_domain.max_tag_occupancy &&
         legacy_domain.tag_count == boundary_domain.tag_count);
}

struct EventWitness {
  im2p_cycle_sequence_report_t report{};
  im2p_cycle_result_t cumulative{};
  im2p_cycle_sequence_row_pressure_t rows{};
  im2p_cycle_sequence_domain_snapshot_t domain{};
  std::vector<im2p_cycle_sequence_event_t> events;
  unsigned pauses = 0;
  unsigned soft_stops = 0;
};

static EventWitness run_event_work(bool boundary) {
  auto settings = config(64);
  auto sequence = session(settings);
  auto descriptor = work(20, true);
  assert(im2p_cycle_sequence_offer(sequence.get(), &descriptor, 0) ==
         IM2P_CYCLE_SEQUENCE_OK);
  EventWitness witness;
  if (boundary) {
    for (;;) {
      const int code = im2p_cycle_sequence_advance_to_boundary(
          sequence.get(), std::numeric_limits<uint64_t>::max(), 17);
      const auto current = status(sequence.get());
      if (code == IM2P_CYCLE_SEQUENCE_OK) {
        assert(current.has_report &&
               current.stop_reason ==
                   IM2P_CYCLE_SEQUENCE_STOP_REPORT_AVAILABLE);
        break;
      }
      if (code == IM2P_CYCLE_SEQUENCE_WOULD_BLOCK) {
        assert(current.stop_reason ==
               IM2P_CYCLE_SEQUENCE_STOP_EVENT_BUFFER_AVAILABLE);
        ++witness.pauses;
        append_events(sequence.get(), witness.events);
      } else {
        assert(code == IM2P_CYCLE_SEQUENCE_INCOMPLETE &&
               current.stop_reason ==
                   IM2P_CYCLE_SEQUENCE_STOP_SOFT_BUDGET);
        ++witness.soft_stops;
      }
    }
    append_events(sequence.get(), witness.events);
  } else {
    for (;;) {
      const auto before = status(sequence.get());
      if (before.has_report)
        break;
      const int code =
          im2p_cycle_sequence_advance_until(sequence.get(), before.cursor + 1);
      if (code == IM2P_CYCLE_SEQUENCE_WOULD_BLOCK) {
        ++witness.pauses;
        append_events(sequence.get(), witness.events);
      } else {
        assert(code == IM2P_CYCLE_SEQUENCE_INCOMPLETE ||
               code == IM2P_CYCLE_SEQUENCE_OK);
      }
    }
    append_events(sequence.get(), witness.events);
  }
  witness.report = pop_report(sequence.get());
  witness.cumulative = counters(sequence.get());
  witness.rows = row_pressure(sequence.get());
  witness.domain = domain(sequence.get());
  assert(witness.events.size() == witness.report.event_count);
  return witness;
}

static void test_event_drain_and_cumulative_parity() {
  const auto legacy = run_event_work(false);
  const auto boundary = run_event_work(true);
  assert(legacy.pauses > 0 && boundary.pauses > 0 &&
         boundary.soft_stops > 0);
  same_bytes(legacy.report, boundary.report);
  same_bytes(legacy.cumulative, boundary.cumulative);
  assert(legacy.rows.max_row_occupancy ==
         boundary.rows.max_row_occupancy);
  assert(legacy.domain.max_tag_occupancy ==
         boundary.domain.max_tag_occupancy);
  assert(legacy.events.size() == boundary.events.size());
  for (size_t i = 0; i < legacy.events.size(); ++i)
    same_bytes(legacy.events[i], boundary.events[i]);
}

static void test_invalid_hard_fault_and_reset() {
  auto settings = config();
  auto cold = session(settings, false);
  assert(im2p_cycle_sequence_advance_to_boundary(nullptr, 1, 1) ==
         IM2P_CYCLE_SEQUENCE_INVALID);
  assert(im2p_cycle_sequence_advance_to_boundary(cold.get(), 1, 1) ==
         IM2P_CYCLE_SEQUENCE_INVALID);
  assert(im2p_cycle_sequence_reset(cold.get()) == IM2P_CYCLE_SEQUENCE_OK);
  const auto before = status(cold.get());
  assert(im2p_cycle_sequence_advance_to_boundary(cold.get(), 1, 0) ==
         IM2P_CYCLE_SEQUENCE_INVALID);
  same_bytes(before, status(cold.get()));
  assert(im2p_cycle_sequence_advance_to_boundary(cold.get(), 2, 2) ==
         IM2P_CYCLE_SEQUENCE_OK);
  assert(status(cold.get()).stop_reason ==
         IM2P_CYCLE_SEQUENCE_STOP_TARGET);
  const auto at_two = status(cold.get());
  assert(im2p_cycle_sequence_advance_to_boundary(cold.get(), 1, 1) ==
         IM2P_CYCLE_SEQUENCE_INVALID);
  same_bytes(at_two, status(cold.get()));

  settings.max_work_cycles = 1;
  auto legacy = session(settings);
  auto boundary = session(settings);
  auto descriptor = work(30);
  assert(im2p_cycle_sequence_offer(legacy.get(), &descriptor, 0) ==
         IM2P_CYCLE_SEQUENCE_OK);
  assert(im2p_cycle_sequence_offer(boundary.get(), &descriptor, 0) ==
         IM2P_CYCLE_SEQUENCE_OK);
  assert(im2p_cycle_sequence_advance_until(legacy.get(), 2) ==
         IM2P_CYCLE_SEQUENCE_LIMIT);
  assert(im2p_cycle_sequence_advance_to_boundary(boundary.get(), 2, 8) ==
         IM2P_CYCLE_SEQUENCE_LIMIT);
  same_state(status(legacy.get()), status(boundary.get()));
  assert(status(boundary.get()).faulted &&
         status(boundary.get()).stop_reason ==
             IM2P_CYCLE_SEQUENCE_STOP_WORK_BUDGET);
  im2p_cycle_sequence_error_t legacy_error, boundary_error;
  im2p_cycle_sequence_error_init(&legacy_error);
  im2p_cycle_sequence_error_init(&boundary_error);
  assert(im2p_cycle_sequence_get_error(legacy.get(), &legacy_error) ==
         IM2P_CYCLE_SEQUENCE_OK);
  assert(im2p_cycle_sequence_get_error(boundary.get(), &boundary_error) ==
         IM2P_CYCLE_SEQUENCE_OK);
  same_bytes(legacy_error, boundary_error);
  assert(im2p_cycle_sequence_advance_to_boundary(boundary.get(), 3, 1) ==
         IM2P_CYCLE_SEQUENCE_FAULTED);
  assert(im2p_cycle_sequence_reset(legacy.get()) ==
         IM2P_CYCLE_SEQUENCE_OK);
  assert(im2p_cycle_sequence_reset(boundary.get()) ==
         IM2P_CYCLE_SEQUENCE_OK);
  same_state(status(legacy.get()), status(boundary.get()));
  assert(status(boundary.get()).generation == 2 &&
         status(boundary.get()).last_discarded_generation == 1 &&
         !status(boundary.get()).faulted);
}

static void test_targets_surround_each_milestone() {
  const auto settings = config();
  const auto descriptor = work(40);
  auto reference = session(settings);
  assert(im2p_cycle_sequence_offer(reference.get(), &descriptor, 3) == 0);
  legacy_to_report(reference.get());
  const auto report = pop_report(reference.get());
  const uint64_t boundaries[] = {
      report.accepted_cycle, report.result_ready_cycle,
      report.final_scale_release_cycle, report.resource_ready_cycle};
  for (const auto milestone : boundaries) {
    for (const auto target : {milestone - 1, milestone, milestone + 1}) {
      auto legacy = session(settings);
      auto boundary = session(settings);
      assert(im2p_cycle_sequence_offer(legacy.get(), &descriptor, 3) == 0);
      assert(im2p_cycle_sequence_offer(boundary.get(), &descriptor, 3) == 0);
      while (status(legacy.get()).cursor < target &&
             !status(legacy.get()).has_report)
        assert(im2p_cycle_sequence_advance_until(
                   legacy.get(), status(legacy.get()).cursor + 1) >= 0);
      const int code = im2p_cycle_sequence_advance_to_boundary(
          boundary.get(), target, 100000);
      const auto current = status(boundary.get());
      same_state(status(legacy.get()), current);
      same_bytes(counters(legacy.get()), counters(boundary.get()));
      same_bytes(domain(legacy.get()), domain(boundary.get()));
      same_bytes(row_pressure(legacy.get()), row_pressure(boundary.get()));
      assert(current.cursor ==
             std::min(target, report.resource_ready_cycle));
      if (current.has_report) {
        assert(code == IM2P_CYCLE_SEQUENCE_OK);
        assert(current.stop_reason ==
               IM2P_CYCLE_SEQUENCE_STOP_REPORT_AVAILABLE);
        same_bytes(pop_report(legacy.get()), pop_report(boundary.get()));
      } else {
        assert(code == IM2P_CYCLE_SEQUENCE_INCOMPLETE);
        assert(current.stop_reason == IM2P_CYCLE_SEQUENCE_STOP_TARGET);
      }
    }
  }
}

static void test_session_limit_and_indivisible_event_burst() {
  for (const bool event_burst : {false, true}) {
    auto settings = config(event_burst ? 1 : 64);
    if (!event_burst)
      settings.max_session_cycles = 2;
    auto legacy = session(settings);
    auto boundary = session(settings);
    auto descriptor = work(50, event_burst);
    assert(im2p_cycle_sequence_offer(legacy.get(), &descriptor, 0) == 0);
    assert(im2p_cycle_sequence_offer(boundary.get(), &descriptor, 0) == 0);
    int legacy_code = 0;
    do {
      legacy_code = im2p_cycle_sequence_advance_until(
          legacy.get(), status(legacy.get()).cursor + 1);
      std::vector<im2p_cycle_sequence_event_t> drained;
      append_events(legacy.get(), drained);
    } while (legacy_code >= 0);
    int boundary_code = 0;
    do {
      boundary_code =
          im2p_cycle_sequence_advance_to_boundary(boundary.get(), 100000, 97);
      std::vector<im2p_cycle_sequence_event_t> drained;
      append_events(boundary.get(), drained);
    } while (boundary_code >= 0);
    assert(legacy_code == boundary_code);
    same_state(status(legacy.get()), status(boundary.get()));
    same_bytes(counters(legacy.get()), counters(boundary.get()));
    same_bytes(domain(legacy.get()), domain(boundary.get()));
    same_bytes(row_pressure(legacy.get()), row_pressure(boundary.get()));
  }
}

int main() {
  static_assert(IM2P_CYCLE_SEQUENCE_STOP_EVENT_BURST == 7,
                "existing stop values must remain stable");
  static_assert(IM2P_CYCLE_SEQUENCE_STOP_REPORT_AVAILABLE == 8,
                "report boundary value");
  static_assert(IM2P_CYCLE_SEQUENCE_STOP_SOFT_BUDGET == 9,
                "soft boundary value");
  test_idle_pending_report_and_q_edge();
  test_event_drain_and_cumulative_parity();
  test_invalid_hard_fault_and_reset();
  test_targets_surround_each_milestone();
  test_session_limit_and_indivisible_event_burst();
  std::cout << "SEQUENCE_MILESTONE_BOUNDARY_PASS"
            << " report_q=exact soft_resume=1 event_parity=1 hard_parity=1\n";
  return 0;
}
