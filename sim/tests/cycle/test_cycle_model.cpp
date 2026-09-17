#include "../../cycle/cycle_model.hpp"
#include "../../cycle/timing_events.hpp"
#include <algorithm>
#include <cstring>
#include <future>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <type_traits>

namespace {
unsigned checks = 0;
void check(bool value, const char *message) {
  ++checks;
  if (!value)
    throw std::runtime_error(message);
}
using Handle =
    std::unique_ptr<im2p_cycle_model_t, decltype(&im2p_cycle_model_destroy)>;
// Unit fixture of the pinned rtl-regression hardware facts. Production profile
// resolution uses the existing Python resolver, not this test fixture helper.
im2p_cycle_model_config_t config(unsigned bits = 8, unsigned dim = 32) {
  im2p_cycle_model_config_t c;
  im2p_cycle_model_config_init(&c);
  c.hardware = {bits,
                bits,
                dim,
                32,
                32,
                4,
                256u * 1024u / (4u * dim * bits / 8u),
                64u * 1024u / (dim * 4u),
                dim * bits / 8u,
                dim * 4u,
                4,
                2};
  return c;
}
im2p_cycle_request_t request() {
  im2p_cycle_request_t r;
  im2p_cycle_request_init(&r);
  r.m = 2;
  r.n = 3;
  r.k = 64;
  r.tile_k = 2;
  r.submission = IM2P_CYCLE_TILE_SUBMISSIONS;
  r.accepted_cycle = 589;
  return r;
}
im2p_cycle_result_t run(im2p_cycle_model_t *m, const im2p_cycle_request_t &r) {
  im2p_cycle_result_t result{};
  if (im2p_cycle_estimate(m, &r, &result) != IM2P_CYCLE_OK)
    throw std::runtime_error(im2p_cycle_model_error(m));
  return result;
}
std::vector<im2p_cycle_event_t> events(im2p_cycle_model_t *m) {
  std::vector<im2p_cycle_event_t> out(im2p_cycle_model_event_count(m));
  for (size_t i = 0; i < out.size(); ++i)
    check(im2p_cycle_model_event(m, i, &out[i]) == IM2P_CYCLE_OK,
          "event getter");
  return out;
}
void validation() {
  auto c = config();
  const auto r0 = request();
  Handle m(im2p_cycle_model_create(&c), im2p_cycle_model_destroy);
  check(bool(m), "valid config rejected");
  auto invalid = [&](im2p_cycle_request_t r, int expected) {
    im2p_cycle_result_t out, original;
    std::memset(&out, 0x5a, sizeof(out));
    original = out;
    check(im2p_cycle_estimate(m.get(), &r, &out) == expected,
          "invalid request classification");
    check(std::memcmp(&out, &original, sizeof(out)) == 0,
          "failed request changed caller output");
    check(im2p_cycle_model_event_count(m.get()) == 0,
          "failed request retained stale public events");
  };
  for (auto field :
       {&im2p_cycle_request_t::m, &im2p_cycle_request_t::n,
        &im2p_cycle_request_t::k, &im2p_cycle_request_t::tile_i,
        &im2p_cycle_request_t::tile_j, &im2p_cycle_request_t::tile_k}) {
    auto r = r0;
    r.*field = 0;
    invalid(r, IM2P_CYCLE_INVALID);
  }
  auto r = r0;
  ++r.abi_version;
  invalid(r, IM2P_CYCLE_INVALID);
  r = r0;
  --r.struct_size;
  invalid(r, IM2P_CYCLE_INVALID);
  r = r0;
  r.submission = 99;
  invalid(r, IM2P_CYCLE_INVALID);
  r = r0;
  r.initial_scratchpad_half = 2;
  invalid(r, IM2P_CYCLE_INVALID);
  r = r0;
  r.activation_stride_bytes = 1;
  invalid(r, IM2P_CYCLE_INVALID);
  r = r0;
  r.weight_stride_bytes = 1;
  invalid(r, IM2P_CYCLE_INVALID);
  r = r0;
  r.output_stride_bytes = 7;
  invalid(r, IM2P_CYCLE_INVALID);
  r = r0;
  r.scale_stride_elements = 1;
  invalid(r, IM2P_CYCLE_INVALID);
  r = r0;
  r.n = UINT64_MAX;
  invalid(r, IM2P_CYCLE_OVERFLOW);
  r = r0;
  r.accepted_cycle = UINT64_MAX;
  invalid(r, IM2P_CYCLE_OVERFLOW);
  r = r0;
  r.activation_stride_bytes = UINT64_MAX;
  invalid(r, IM2P_CYCLE_OVERFLOW);
  r = r0;
  r.scale_stride_elements = UINT64_MAX;
  invalid(r, IM2P_CYCLE_OVERFLOW);
  r = r0;
  r.m = UINT64_C(1) << 32;
  invalid(r, IM2P_CYCLE_OVERFLOW);
  r = r0;
  r.m = r.n = 1;
  r.k = 8256;
  r.tile_k = 1;
  r.submission = IM2P_CYCLE_BLOCK_SUBMISSIONS;
  invalid(r, IM2P_CYCLE_UNSUPPORTED);
  r.submission = IM2P_CYCLE_TILE_SUBMISSIONS;
  r.tile_k = 64;
  check(run(m.get(), r).loop_count > 1,
        "regression-tile scale addressing lost relative fragmentBase reset");
  for (auto field :
       {&im2p_cycle_hardware_t::activation_bits,
        &im2p_cycle_hardware_t::weight_bits, &im2p_cycle_hardware_t::dim,
        &im2p_cycle_hardware_t::block_k,
        &im2p_cycle_hardware_t::accumulator_bits,
        &im2p_cycle_hardware_t::bank_count, &im2p_cycle_hardware_t::bank_rows,
        &im2p_cycle_hardware_t::accumulator_rows,
        &im2p_cycle_hardware_t::scratchpad_row_bytes,
        &im2p_cycle_hardware_t::accumulator_row_bytes,
        &im2p_cycle_hardware_t::scratchpad_read_delay,
        &im2p_cycle_hardware_t::accumulator_latency}) {
    auto bad = c;
    bad.hardware.*field += 1;
    Handle rejected(im2p_cycle_model_create(&bad), im2p_cycle_model_destroy);
    check(!rejected, "mismatched profile admitted");
  }
  auto bad = c;
  bad.timing.read_ready_period = 1;
  check(im2p_cycle_model_create(&bad) == nullptr,
        "never-ready protocol admitted");
  bad = c;
  bad.max_cycles = 1;
  Handle limited(im2p_cycle_model_create(&bad), im2p_cycle_model_destroy);
  im2p_cycle_result_t out{};
  check(im2p_cycle_estimate(limited.get(), &r0, &out) == IM2P_CYCLE_LIMIT,
        "cycle limit ignored");
  bad = c;
  bad.max_fragments = 1;
  Handle fragments(im2p_cycle_model_create(&bad), im2p_cycle_model_destroy);
  check(im2p_cycle_estimate(fragments.get(), &r0, &out) == IM2P_CYCLE_LIMIT,
        "fragment limit ignored");
  bad = c;
  bad.max_trace_events = 1;
  Handle traces(im2p_cycle_model_create(&bad), im2p_cycle_model_destroy);
  r = r0;
  r.record_events = 1;
  check(im2p_cycle_estimate(traces.get(), &r, &out) == IM2P_CYCLE_LIMIT,
        "trace limit ignored");
}
void deterministic() {
  auto c = config();
  auto r = request();
  r.record_events = 1;
  Handle m(im2p_cycle_model_create(&c), im2p_cycle_model_destroy);
  auto first = run(m.get(), r);
  const auto trace = events(m.get());
  // Preserved six-profile RTL corpus: DIM32 K64 fast fixture starts at 589.
  check(first.total_cycles == 503 && first.done_cycle == 1092,
        "certified K64 fixture changed");
  check(first.load_request_count == 68 && first.scale_request_count == 2 &&
            first.store_request_count == 2,
        "row traffic changed");
  for (int i = 0; i < 3; ++i) {
    const auto repeated = run(m.get(), r);
    const auto second = events(m.get());
    check(std::memcmp(&first, &repeated, sizeof(first)) == 0,
          "result not deterministic");
    check(trace.size() == second.size() &&
              std::memcmp(trace.data(), second.data(),
                          trace.size() * sizeof(trace[0])) == 0,
          "diagnostic events not byte-identical");
  }
  uint64_t previous_cycle = 0;
  for (const auto &event : trace) {
    check(event.cycle >= previous_cycle && event.dependency < event.id,
          "invalid event dependency/order");
    previous_cycle = event.cycle;
  }
  r.record_events = 0;
  auto quiet = run(m.get(), r);
  check(quiet.event_count == 0, "trace disabled but allocated");
  first.event_count = 0;
  check(std::memcmp(&first, &quiet, sizeof(first)) == 0,
        "trace changes timing");
  r.record_events = 1;
  r.accepted_cycle += 50;
  const auto shifted = run(m.get(), r);
  check(shifted.total_cycles == first.total_cycles &&
            shifted.done_cycle == first.done_cycle + 50,
        "absolute ready-phase convention changed");
  c.timing = {1, 11, 31, 29, 37, 3, 5, 0};
  r.accepted_cycle = 1158;
  Handle slow(im2p_cycle_model_create(&c), im2p_cycle_model_destroy);
  check(run(slow.get(), r).total_cycles == 730,
        "certified delayed fixture changed");
}
void profiles_and_planner() {
  using namespace im2p::cycle;
  for (unsigned bits : {4u, 8u})
    for (unsigned dim : {16u, 32u, 64u}) {
      auto c = config(bits, dim);
      auto r = request();
      r.m = dim + 1;
      r.n = dim + 3;
      r.k = 96;
      r.tile_i = 1;
      r.tile_j = 2;
      r.tile_k = 96 / dim + 1;
      r.record_events = 0;
      auto schedule = expand_work(c, r);
      im2p::gemmini::LoopCursor cursor{};
      std::vector<im2p::gemmini::FragmentPlan> original;
      while (cursor.i < r.m) {
        const auto loop =
            im2p::gemmini::plan_loop(schedule.planner, r.m, cursor);
        for (size_t i = 0; i < loop.fragment_count; ++i)
          original.push_back(
              im2p::gemmini::plan_fragment(schedule.planner, loop, i));
        im2p::gemmini::advance_loop(schedule.planner, loop, cursor);
      }
      size_t count = 0;
      for (const auto &work : schedule.work)
        for (const auto &fragment : work.fragments) {
          const auto &a = fragment.plan, &b = original.at(count++);
          check(a.i == b.i && a.j == b.j && a.k == b.k && a.rows == b.rows &&
                    a.columns == b.columns && a.reduction == b.reduction &&
                    a.accumulate == b.accumulate &&
                    a.first_contribution == b.first_contribution &&
                    a.final_contribution == b.final_contribution,
                "timing expander changed planner fragments");
        }
      check(count == original.size() && count == schedule.fragments,
            "planner fragments lost");
      Handle model(im2p_cycle_model_create(&c), im2p_cycle_model_destroy);
      const auto result = run(model.get(), r);
      check(result.loop_count == schedule.work.size() &&
                result.fragment_count == count,
            "planner not consumed");
      check(result.load_request_count == result.load_response_count &&
                result.scale_request_count == result.scale_response_count &&
                result.store_request_count == result.store_response_count,
            "request/response conservation");
      r.submission = IM2P_CYCLE_BLOCK_SUBMISSIONS;
      const auto blocks = expand_work(c, r);
      const auto direct = run(model.get(), r);
      check(blocks.work.size() == blocks.planner_loops &&
                direct.loop_count == blocks.planner_loops,
            "block submission differs from existing planner");
      std::cout << "CYCLE_PROFILE A" << bits << "D" << dim << " PASS\n";
    }
}
} // namespace
int main() {
  try {
    static_assert(std::is_standard_layout_v<im2p_cycle_request_t>);
    static_assert(std::is_trivially_copyable_v<im2p_cycle_request_t>);
    static_assert(sizeof(im2p_cycle_request_t) == 120);
    validation();
    deterministic();
    profiles_and_planner();
    std::vector<std::future<uint64_t>> parallel;
    for (int i = 0; i < 4; ++i)
      parallel.push_back(std::async(std::launch::async, [] {
        auto c = config();
        auto r = request();
        Handle m(im2p_cycle_model_create(&c), im2p_cycle_model_destroy);
        im2p_cycle_result_t out{};
        if (im2p_cycle_estimate(m.get(), &r, &out))
          throw std::runtime_error("parallel estimate failed");
        return out.total_cycles;
      }));
    for (auto &future : parallel)
      check(future.get() == 503, "handles share mutable state");
    std::cout << "CYCLE_MODEL_UNIT_PASS checks=" << checks << "\n";
  } catch (const std::exception &error) {
    std::cerr << "CYCLE_MODEL_UNIT_FAIL " << error.what() << '\n';
    return 1;
  }
}
