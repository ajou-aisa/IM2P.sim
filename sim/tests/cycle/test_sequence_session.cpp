#include "../../cycle/control_engine.hpp"
#include "../../include/im2p_cycle_sequence.h"
#include <algorithm>
#include <array>
#undef NDEBUG
#include <cassert>
#include <cstring>
#include <iostream>
#include <memory>
#include <type_traits>
#include <utility>
#include <vector>

using im2p::cycle::EventType;
using im2p::cycle::ModelResult;
using im2p::cycle::detail::Array;
using im2p::cycle::detail::Engine;
using im2p::cycle::detail::WorkContext;
static_assert(
    std::is_same_v<decltype(WorkContext::request), im2p_cycle_request_t>);
static_assert(
    std::is_same_v<decltype(std::declval<WorkContext>().schedule.planner.runs),
                   std::vector<im2p_compact_run_t>>);

struct Fixture {
  std::unique_ptr<Engine> engine;
  std::unique_ptr<WorkContext> work;
  ModelResult expected;
};

Fixture make_scoped_fixture() {
  im2p_cycle_model_config_t config{};
  im2p_cycle_model_config_init(&config);
  config.hardware = {8, 8, 16, 32, 32, 4, 4096, 1024, 16, 64, 4, 2};
  im2p_cycle_request_t request{};
  im2p_cycle_request_init(&request);
  request.m = request.n = 1;
  request.k = 22;
  request.tile_k = 2;
  request.accepted_cycle = 5;
  request.submission = IM2P_CYCLE_TILE_SUBMISSIONS;
  std::array<im2p_compact_run_t, 2> runs{
      {{0, 0x00000fff, 0, 12}, {1, 0x000003ff, 12, 10}}};
  im2p_compact_runs_t view{IM2P_COMPACT_RUNS_VERSION, sizeof(view), 42,
                           runs.size(), runs.data()};
  Fixture fixture;
  fixture.expected = im2p::cycle::estimate(config, request, &view, true);
  fixture.engine = std::make_unique<Engine>(config, request, &view);
  fixture.work = std::make_unique<WorkContext>(config, request, &view);
  assert(&fixture.work->request != &request);
  assert(fixture.work->schedule.planner.runs.data() != runs.data());
  config.max_cycles = 1;
  request.accepted_cycle = 1000000;
  runs[1].original_block_id = 3;
  view.original_k = 106;
  return fixture;
}

static std::vector<std::pair<unsigned, unsigned>> tag_keys(const Array &array) {
  std::vector<std::pair<unsigned, unsigned>> keys;
  for (const auto &tag : array.tags)
    keys.emplace_back(tag.id, tag.preload.rob);
  return keys;
}

static void test_persistent_idle() {
  im2p_cycle_model_config_t config{};
  im2p_cycle_model_config_init(&config);
  config.hardware = {8, 8, 16, 32, 32, 4, 4096, 1024, 16, 64, 4, 2};
  im2p_cycle_request_t request{};
  im2p_cycle_request_init(&request);
  request.m = request.n = 1;
  request.k = 22;
  request.tile_k = 2;
  request.submission = IM2P_CYCLE_TILE_SUBMISSIONS;
  request.logical_work_id = 10;
  request.record_events = 1;
  Engine engine(config, true);
  engine.accept(WorkContext(config, request), 0, 0, 0);
  while (!engine.resource_ready()) {
    assert(engine.cycle() < 1000);
    engine.step();
  }
  const auto q = engine.cycle();
  const auto a = engine.finish().service;
  const Array *physical = &engine.array_state_for_test();
  assert(!physical->request && physical->outputs.empty() &&
         engine.execute_state_for_test().controls.empty());
  unsigned bank_payload = 0;
  for (const auto &bank : engine.banks_state_for_test()) {
    bank_payload += unsigned(bank.pending) + unsigned(bank.queued);
    for (bool valid : bank.pipe)
      bank_payload += unsigned(valid);
  }
  const auto events_at_release = engine.cumulative_counters().event_count;
  const auto buffered_at_release = engine.buffered_event_count();
  const auto tags = tag_keys(*physical);
  const auto row_count = physical->row_counts.size();
  const auto array_id = physical->id;
  assert(!tags.empty());
  const auto resident = physical->tags.front();
  assert(resident.preload.rob == im2p::cycle::detail::none);
  assert(tags.size() < im2p::cycle::detail::P::tag_queue);
  engine.retire_work();
  assert(!engine.current_work());
  for (unsigned i = 0; i < 100; ++i) {
    engine.step();
    assert(engine.resource_ready() && !engine.current_work());
    assert(!physical->request && physical->outputs.empty() &&
           engine.execute_state_for_test().controls.empty());
  }
  assert(engine.cumulative_counters().event_count == events_at_release &&
         engine.buffered_event_count() == buffered_at_release);
  assert(&engine.array_state_for_test() == physical);
  assert(engine.cycle() == q + 100);
  assert(physical->id == array_id);
  assert(tag_keys(*physical) == tags);
  assert(physical->row_counts.size() == row_count);
  Array early_dequeue = *physical;
  early_dequeue.tags.pop_front();
  assert(tag_keys(early_dequeue) != tag_keys(*physical));
  request.logical_work_id = 11;
  request.k = 1;
  request.tile_k = 1;
  engine.accept(WorkContext(config, request), engine.cycle(),
                a.next_scratchpad_half, a.next_accumulator_half);
  assert(engine.array_state_for_test().id == array_id);
  assert(tag_keys(engine.array_state_for_test()) == tags);
  const auto &head = engine.array_state_for_test().tags.front();
  assert(head.id == resident.id && head.rows == resident.rows &&
         head.preload.rob == resident.preload.rob &&
         head.preload.frame == resident.preload.frame &&
         head.preload.parent == resident.preload.parent);
  while (!engine.resource_ready()) {
    assert(engine.cycle() < q + 1000);
    engine.step();
  }
  assert(engine.current_work()->request.logical_work_id == 11);
  assert(engine.finish().service.resource_ready_cycle == engine.cycle());
  std::cout << "SEQUENCE_IDLE_CORE_PASS a_resource=" << q
            << " b_accepted=" << q + 100 << " a_tags=" << tags.size()
            << " a_row_counts=" << row_count << " a_array_id=" << array_id
            << " resident_tag_id=" << resident.id
            << " resident_rob=" << resident.preload.rob
            << " bank_payload_at_release=" << bank_payload << '\n';
}

static void test_no_retired_loop_restart() {
  im2p_cycle_model_config_t config{};
  im2p_cycle_model_config_init(&config);
  config.hardware = {8, 8, 16, 32, 32, 4, 4096, 1024, 16, 64, 4, 2};
  im2p_cycle_request_t request{};
  im2p_cycle_request_init(&request);
  request.m = request.n = request.k = 1;
  request.tile_k = 1;
  request.submission = IM2P_CYCLE_TILE_SUBMISSIONS;
  request.logical_work_id = 20;
  Engine engine(config);
  engine.accept(WorkContext(config, request), 0, 0, 0);
  while (!engine.resource_ready()) {
    assert(engine.cycle() < 1000);
    engine.step();
  }
  const auto q = engine.cycle();
  const auto a = engine.finish().service;
  engine.retire_work();
  for (unsigned i = 0; i < 100; ++i)
    engine.step();
  request.logical_work_id = 21;
  request.k = 22;
  request.tile_k = 2;
  request.record_events = 1;
  engine.accept(WorkContext(config, request), engine.cycle(),
                a.next_scratchpad_half, a.next_accumulator_half);
  const auto accepted = engine.cycle();
  assert(accepted == q + 100);
  engine.step();
  const auto &first_edge = engine.current_work()->result.trace.events;
  const auto early =
      std::find_if(first_edge.begin(), first_edge.end(), [](const auto &event) {
        return event.type == static_cast<uint32_t>(EventType::LoopIssue);
      });
  if (early != first_edge.end())
    std::cerr << "EARLY_LOOP_ISSUE cycle=" << early->cycle
              << " before B bridge config\n";
  assert(early == first_edge.end());
  while (!engine.resource_ready()) {
    assert(engine.cycle() < accepted + 1000);
    engine.step();
  }
  const auto &events = engine.current_work()->result.trace.events;
  const auto config_issue =
      std::find_if(events.begin(), events.end(), [](const auto &event) {
        return event.type == static_cast<uint32_t>(EventType::BridgeIssue) &&
               event.detail == 10;
      });
  const auto first_loop =
      std::find_if(events.begin(), events.end(), [](const auto &event) {
        return event.type == static_cast<uint32_t>(EventType::LoopIssue);
      });
  assert(config_issue != events.end() && first_loop != events.end());
  assert(first_loop->cycle > config_issue->cycle);
  std::cout << "SEQUENCE_NO_STALE_LOOP_PASS a_resource=" << q
            << " b_accepted=" << accepted
            << " b_bridge_config=" << config_issue->cycle
            << " b_first_loop=" << first_loop->cycle
            << " b_resource=" << engine.cycle() << '\n';
}

static void test_retired_origin_ids() {
  im2p_cycle_model_config_t config{};
  im2p_cycle_model_config_init(&config);
  config.hardware = {8, 8, 16, 32, 32, 4, 4096, 1024, 16, 64, 4, 2};
  im2p_cycle_request_t request{};
  im2p_cycle_request_init(&request);
  request.m = request.n = 1;
  request.k = 22;
  request.tile_k = 2;
  request.submission = IM2P_CYCLE_TILE_SUBMISSIONS;
  request.logical_work_id = 100;
  request.record_events = 1;
  Engine engine(config);
  engine.accept(WorkContext(config, request), 0, 0, 0);
  while (!engine.resource_ready()) {
    assert(engine.cycle() < 1000);
    engine.step();
  }
  const auto a_events = engine.current_work()->result.trace.events;
  const auto a_service = engine.finish().service;
  assert(!a_events.empty());
  const auto old_tag = engine.array_state_for_test().tags.front();
  assert(old_tag.preload.rob == im2p::cycle::detail::none);
  engine.retire_work();
  request.logical_work_id = 101;
  request.k = request.tile_k = 1;
  engine.accept(WorkContext(config, request), engine.cycle(),
                a_service.next_scratchpad_half,
                a_service.next_accumulator_half);
  engine.step();
  const auto &b_events = engine.current_work()->result.trace.events;
  assert(!b_events.empty());
  assert(engine.array_state_for_test().tags.front().id == old_tag.id);
  assert(engine.array_state_for_test().tags.front().preload.origin.work == 100);
  assert(engine.array_state_for_test().tags.front().preload.origin.ordinal == 1);
  assert(engine.current_work()->origin.work == 101 &&
         engine.current_work()->origin.ordinal == 2);
  std::cerr << "RETIRED_ORIGIN_FIRST_ID a_last=" << a_events.back().id
            << " b_first=" << b_events.front().id << '\n';
  assert(b_events.front().id > a_events.back().id);
}

static void test_alias_mutation_rejected() {
  im2p_cycle_model_config_t config{};
  im2p_cycle_model_config_init(&config);
  config.hardware = {8, 8, 16, 32, 32, 4, 4096, 1024, 16, 64, 4, 2};
  im2p_cycle_request_t request{};
  im2p_cycle_request_init(&request);
  request.m = request.n = 1;
  request.k = 22;
  request.tile_k = 2;
  request.submission = IM2P_CYCLE_TILE_SUBMISSIONS;
  request.logical_work_id = 200;
  Engine engine(config);
  engine.accept(WorkContext(config, request), 0, 0, 0);
  while (!engine.resource_ready()) {
    assert(engine.cycle() < 1000);
    engine.step();
  }
  const auto a_service = engine.finish().service;
  engine.retire_work();
  request.logical_work_id = 201;
  request.k = request.tile_k = 1;
  engine.accept(WorkContext(config, request), engine.cycle(),
                a_service.next_scratchpad_half,
                a_service.next_accumulator_half);
  auto &physical =
      const_cast<Array &>(engine.array_state_for_test());
  assert(!physical.tags.empty() && physical.tags.front().preload.rob ==
                                       im2p::cycle::detail::none);
  const auto a_origin = physical.tags.front().preload.origin;
  physical.tags.front().preload.rob = 0;
  physical.tags.front().preload.origin = engine.current_work()->origin;
  physical.outputs.push_front(
      {a_origin,
       im2p::cycle::TimingEvent::accepted(
           engine.cycle(), 0, 1, EventType::ArrayOutput,
           im2p::cycle::Resource::Array),
       physical.tags.front().id, true, 0});
  bool rejected = false;
  try {
    engine.step();
  } catch (const im2p::cycle::Error &error) {
    rejected = error.status == IM2P_CYCLE_INTERNAL;
  }
  assert(rejected);
  std::cout << "SEQUENCE_ALIAS_MUTATION_REJECTED old_work=" << a_origin.work
            << " new_work=" << engine.current_work()->origin.work << '\n';
}

static void test_missing_origin_rejected() {
  im2p_cycle_model_config_t config{};
  im2p_cycle_model_config_init(&config);
  config.hardware = {8, 8, 16, 32, 32, 4, 4096, 1024, 16, 64, 4, 2};
  im2p_cycle_request_t request{};
  im2p_cycle_request_init(&request);
  request.m = request.n = request.k = 1;
  request.tile_k = 1;
  request.submission = IM2P_CYCLE_TILE_SUBMISSIONS;
  request.record_events = 1;
  Engine engine(config, true);
  engine.accept(WorkContext(config, request), 0, 0, 0);
  while (!engine.array_state_for_test().request ||
         !std::all_of(engine.array_state_for_test().written.begin(),
                      engine.array_state_for_test().written.end(),
                      [](bool written) { return written; })) {
    assert(engine.cycle() < 1000 && engine.step());
  }
  const auto before = engine.cycle();
  auto &physical = const_cast<Array &>(engine.array_state_for_test());
  physical.origin = {};
  bool rejected = false;
  try {
    engine.step();
  } catch (const im2p::cycle::Error &error) {
    rejected = error.status == IM2P_CYCLE_INTERNAL;
  }
  assert(rejected && engine.cycle() == before);
  std::cout << "SEQUENCE_MISSING_ORIGIN_REJECTED cycle=" << before << '\n';
}

static void test_bounded_retirement() {
  im2p_cycle_model_config_t config{};
  im2p_cycle_model_config_init(&config);
  config.hardware = {8, 8, 16, 32, 32, 4, 4096, 1024, 16, 64, 4, 2};
  Engine engine(config, true);
  im2p_cycle_request_t request{};
  im2p_cycle_request_init(&request);
  request.m = request.n = request.k = 1;
  request.tile_k = 1;
  request.submission = IM2P_CYCLE_TILE_SUBMISSIONS;
  unsigned half = 0, acc = 0;
  size_t max_tags = 0;
  std::array<bool, im2p::cycle::detail::P::mesh_ids> seen_ids{};
  bool reused_id = false;
  for (unsigned i = 0; i < 64; ++i) {
    request.logical_work_id = 300 + i;
    request.k = i % 2 ? 1 : 22;
    request.tile_k = i % 2 ? 1 : 2;
    engine.accept(WorkContext(config, request), engine.cycle(), half, acc);
    const auto start = engine.cycle();
    while (!engine.resource_ready()) {
      assert(engine.cycle() < start + 1000);
      assert(engine.step());
    }
    const auto &done = engine.finish();
    assert(!engine.array_state_for_test().request &&
           engine.array_state_for_test().outputs.empty() &&
           engine.execute_state_for_test().controls.empty());
    assert(done.counters.load_request_count ==
           done.counters.load_response_count);
    assert(done.counters.event_count > 0);
    half = done.service.next_scratchpad_half;
    acc = done.service.next_accumulator_half;
    engine.retire_work();
    assert(!engine.current_work() && engine.buffered_event_count() == 0);
    max_tags = std::max(max_tags, engine.array_state_for_test().tags.size());
    assert(max_tags <= im2p::cycle::detail::P::tag_queue);
    const auto id = engine.array_state_for_test().id;
    reused_id |= seen_ids[id];
    seen_ids[id] = true;
  }
  assert(reused_id && engine.cumulative_counters().logical_work_count == 64);
  std::cout << "SEQUENCE_RETIRED_BOUNDED_PASS repeats=64 retained_schedules=0"
            << " max_tags=" << max_tags << " mesh_id_reuse=" << reused_id
            << " cumulative_events="
            << engine.cumulative_counters().event_count << '\n';
}

struct PublicRepeatWitness {
  std::array<im2p_cycle_sequence_report_t, 32> reports{};
  unsigned phase_mask = 0, half_mask = 0, head_wraps = 0;
  uint32_t max_tags = 0;
  uint64_t enqueues = 0, dequeues = 0, events = 0;
};

static PublicRepeatWitness run_public_repeats(bool record_events) {
  im2p_cycle_sequence_config_t config;
  im2p_cycle_sequence_config_init(&config);
  config.hardware = {8, 8, 16, 32, 32, 4, 4096, 1024, 16, 64, 4, 2};
  config.max_work_ids = 32;
  im2p_cycle_sequence_t *sequence = nullptr;
  assert(im2p_cycle_sequence_create(&config, &sequence) ==
         IM2P_CYCLE_SEQUENCE_OK);
  assert(im2p_cycle_sequence_reset(sequence) == IM2P_CYCLE_SEQUENCE_OK);
  PublicRepeatWitness witness;
  const unsigned idle_cycles[] = {0, 1, 4, 5, 6, 100};
  bool had_head = false;
  uint32_t previous_head = 0;
  uint64_t last_event_id = 0, total_events = 0;
  for (unsigned i = 0; i < witness.reports.size(); ++i) {
    im2p_cycle_sequence_status_t status;
    im2p_cycle_sequence_status_init(&status);
    assert(im2p_cycle_sequence_get_status(sequence, &status) ==
           IM2P_CYCLE_SEQUENCE_OK);
    const auto until = status.cursor + idle_cycles[i % 6];
    assert(im2p_cycle_sequence_advance_until(sequence, until) ==
           IM2P_CYCLE_SEQUENCE_OK);
    assert(im2p_cycle_sequence_get_status(sequence, &status) ==
           IM2P_CYCLE_SEQUENCE_OK);
    assert(status.cursor == until && !status.has_active &&
           !status.has_pending && !status.has_report);
    const auto offered = status.cursor;
    witness.half_mask |= 1u << (2 * status.next_scratchpad_half +
                                status.next_accumulator_half);
    im2p_cycle_sequence_descriptor_t work;
    im2p_cycle_sequence_descriptor_init(&work);
    work.logical_work_id = 1000 + i;
    work.m = work.n = 1;
    work.k = i % 2 ? 24 : 22;
    work.tile_k = i % 2 ? 1 : 2;
    work.submission = IM2P_CYCLE_TILE_SUBMISSIONS;
    work.record_events = record_events;
    assert(im2p_cycle_sequence_offer(sequence, &work, offered) ==
           IM2P_CYCLE_SEQUENCE_OK);
    const auto start = status.cursor;
    while (!status.has_report) {
      assert(status.cursor < start + 2000);
      const int advanced =
          im2p_cycle_sequence_advance_until(sequence, status.cursor + 1);
      assert(advanced == IM2P_CYCLE_SEQUENCE_OK ||
             advanced == IM2P_CYCLE_SEQUENCE_INCOMPLETE);
      assert(im2p_cycle_sequence_get_status(sequence, &status) ==
             IM2P_CYCLE_SEQUENCE_OK);
      im2p_cycle_sequence_tag_state_t tags;
      im2p_cycle_sequence_tag_state_init(&tags);
      assert(im2p_cycle_sequence_get_tag_state(sequence, &tags) ==
             IM2P_CYCLE_SEQUENCE_OK);
      assert(tags.queue_len <= im2p::cycle::detail::P::tag_queue);
      assert(tags.enqueues >= tags.dequeues &&
             tags.enqueues - tags.dequeues == tags.queue_len);
      assert(!tags.head_valid ||
             tags.head_id < im2p::cycle::detail::P::mesh_ids);
      witness.max_tags = std::max(witness.max_tags, tags.queue_len);
      if (tags.head_valid && had_head && tags.head_id < previous_head)
        ++witness.head_wraps;
      if (tags.head_valid)
        previous_head = tags.head_id;
      had_head = tags.head_valid;
      witness.enqueues = tags.enqueues;
      witness.dequeues = tags.dequeues;
    }
    assert(status.accepted_work_id == work.logical_work_id &&
           status.accepted_cycle == offered);
    witness.phase_mask |= 1u << (status.accepted_cycle % 5);
    auto &report = witness.reports[i];
    im2p_cycle_sequence_report_init(&report);
    assert(im2p_cycle_sequence_pop_report(sequence, &report) ==
           IM2P_CYCLE_SEQUENCE_OK);
    assert(report.logical_work_id == work.logical_work_id &&
           report.accepted_cycle == offered &&
           report.resource_ready_cycle == status.cursor &&
           report.next_scratchpad_half == status.next_scratchpad_half &&
           report.next_accumulator_half == status.next_accumulator_half);
    uint64_t buffered = 0;
    assert(im2p_cycle_sequence_read_events(sequence, nullptr, 0, &buffered) ==
           IM2P_CYCLE_SEQUENCE_OK);
    assert(buffered == (record_events ? report.event_count : 0));
    if (record_events) {
      std::vector<im2p_cycle_sequence_event_t> events(buffered);
      uint64_t drained = 0;
      assert(im2p_cycle_sequence_read_events(sequence, events.data(),
                                             events.size(), &drained) ==
                 IM2P_CYCLE_SEQUENCE_OK &&
             drained == buffered);
      for (const auto &event : events) {
        assert(event.event.logical_work_id == work.logical_work_id &&
               event.work_ordinal == i + 1 && event.event.id > last_event_id);
        last_event_id = event.event.id;
      }
    }
    total_events += report.event_count;
  }
  im2p_cycle_result_t cumulative{};
  cumulative.abi_version = IM2P_CYCLE_MODEL_ABI_VERSION;
  cumulative.struct_size = sizeof(cumulative);
  assert(im2p_cycle_sequence_get_cumulative_counters(sequence, &cumulative) ==
             IM2P_CYCLE_SEQUENCE_OK &&
         cumulative.logical_work_count == witness.reports.size() &&
         cumulative.event_count == total_events);
  im2p_cycle_sequence_status_t status;
  im2p_cycle_sequence_status_init(&status);
  assert(im2p_cycle_sequence_get_status(sequence, &status) ==
             IM2P_CYCLE_SEQUENCE_OK &&
         !status.has_active && !status.has_pending && !status.has_report);
  im2p_cycle_sequence_descriptor_t extra;
  im2p_cycle_sequence_descriptor_init(&extra);
  extra.logical_work_id = 2000;
  extra.m = extra.n = extra.k = 1;
  assert(im2p_cycle_sequence_offer(sequence, &extra, status.cursor) ==
         IM2P_CYCLE_SEQUENCE_LIMIT);
  im2p_cycle_sequence_destroy(sequence);
  witness.events = total_events;
  assert(witness.enqueues > im2p::cycle::detail::P::mesh_ids &&
         witness.head_wraps > 0 && witness.max_tags > 0 &&
         witness.max_tags < im2p::cycle::detail::P::tag_queue);
  assert(witness.phase_mask == 0x1f);
  assert(witness.half_mask == 0xf);
  return witness;
}

static void test_public_repeats() {
  const auto off = run_public_repeats(false);
  const auto on = run_public_repeats(true);
  assert(off.phase_mask == on.phase_mask && off.half_mask == on.half_mask &&
         off.max_tags == on.max_tags && off.head_wraps == on.head_wraps &&
         off.enqueues == on.enqueues && off.dequeues == on.dequeues &&
         off.events == on.events);
  for (unsigned i = 0; i < off.reports.size(); ++i) {
    const auto &a = off.reports[i];
    const auto &b = on.reports[i];
    assert(a.accepted_cycle == b.accepted_cycle &&
           a.result_ready_cycle == b.result_ready_cycle &&
           a.final_scale_release_cycle == b.final_scale_release_cycle &&
           a.resource_ready_cycle == b.resource_ready_cycle &&
           a.event_count == b.event_count &&
           a.next_scratchpad_half == b.next_scratchpad_half &&
           a.next_accumulator_half == b.next_accumulator_half &&
           std::memcmp(&a.counters, &b.counters, sizeof(a.counters)) == 0);
  }
  std::cout << "SEQUENCE_PUBLIC_32_PASS works=32 phases=" << off.phase_mask
            << " half_pairs=" << off.half_mask << " max_tags=" << off.max_tags
            << " head_id_wraps=" << off.head_wraps
            << " enqueues=" << off.enqueues << " dequeues=" << off.dequeues
            << " events=" << on.events << " tag_full=NOT_OBSERVED\n";
}

static void test_hidden_event_source(const char *which) {
  im2p_cycle_model_config_t config{};
  im2p_cycle_model_config_init(&config);
  config.hardware = {8, 8, 16, 32, 32, 4, 4096, 1024, 16, 64, 4, 2};
  im2p_cycle_request_t request{};
  im2p_cycle_request_init(&request);
  request.m = request.n = request.k = 1;
  request.tile_k = 1;
  request.submission = IM2P_CYCLE_TILE_SUBMISSIONS;
  Engine engine(config, true);
  engine.accept(WorkContext(config, request), 0, 0, 0);
  while (!engine.resource_ready()) {
    assert(engine.cycle() < 1000 && engine.step());
  }
  auto &array = const_cast<Array &>(engine.array_state_for_test());
  auto &execute = const_cast<im2p::cycle::detail::Execute &>(
      engine.execute_state_for_test());
  assert(!array.request && array.outputs.empty() && execute.controls.empty());
  if (std::strcmp(which, "request") == 0) {
    array.request = true;
  } else if (std::strcmp(which, "output") == 0) {
    array.outputs.push_back({});
  } else {
    assert(std::strcmp(which, "control") == 0);
    execute.controls.push_back({});
  }
  const auto before = engine.cycle();
  bool rejected = false;
  try {
    engine.finish();
  } catch (const im2p::cycle::Error &error) {
    rejected = error.status == IM2P_CYCLE_INTERNAL;
  }
  assert(rejected && engine.cycle() == before);
  std::cout << "SEQUENCE_HIDDEN_EVENT_SOURCE_REJECTED source=" << which
            << " cycle=" << before << '\n';
}

int main(int argc, char **argv) {
  if (argc == 2) {
    test_hidden_event_source(argv[1]);
    return 0;
  }
  auto fixture = make_scoped_fixture();
  assert(fixture.work->request.accepted_cycle == 5);
  assert(fixture.work->schedule.planner.runs.size() == 2);
  assert(fixture.work->schedule.planner.runs[1].original_block_id == 1);
  assert(fixture.work->schedule.planner.original_k == 42);
  const auto actual = fixture.engine->run(true);
  assert(std::memcmp(&actual.counters, &fixture.expected.counters,
                     sizeof(actual.counters)) == 0);
  assert(std::memcmp(&actual.service, &fixture.expected.service,
                     sizeof(actual.service)) == 0);
  std::cout << "SEQUENCE_OWNERSHIP_PASS loops=" << actual.counters.loop_count
            << " result=" << actual.service.result_ready_cycle << '\n';
  test_persistent_idle();
  test_no_retired_loop_restart();
  test_retired_origin_ids();
  test_alias_mutation_rejected();
  test_missing_origin_rejected();
  test_bounded_retirement();
  test_public_repeats();
}
