#pragma once
#include "timing_profile.hpp"
#include <vector>

namespace im2p::cycle {
enum class Resource : uint32_t {
  HostWork,
  ScalePath,
  CommandBridge,
  LoopController,
  Reservation,
  LoadEngine,
  Scratchpad,
  Array,
  ScuWriteback,
  Accumulator,
  StoreEngine,
  Backing
};
enum class EventType : uint32_t {
  Work,
  ScaleRequest,
  ScaleResponse,
  ScaleLane,
  BridgeIssue,
  LoopIssue,
  CommandAllocated,
  LoadIssue,
  ExecuteIssue,
  StoreIssue,
  Context,
  LoadDma,
  ReadRequest,
  ReadResponse,
  ScratchpadRead,
  PreloadIssue,
  ComputeIssue,
  ArrayInput,
  ArrayOutput,
  AccumulatorWrite,
  AccumulatorCommit,
  RawCompleted,
  RobCompleted,
  StoreDma,
  WriteRequest,
  WriteCompletion,
  LoopDone,
  ScaleRelease,
  LogicalDone
};
// A scheduled dependency edge in the incremental timing/resource graph.
// Eligible is the earliest completion edge. Arbitration may consume an eligible
// backing event later. II is a minimum acceptance spacing, not the latency;
// queue and address-hazard credits impose their independent additional
// constraints.
struct TimingEvent {
  uint64_t accepted_cycle = 0;
  uint64_t eligible_cycle = 0;
  uint64_t minimum_initiation_interval = 0;
  EventType type = EventType::Work;
  Resource resource = Resource::HostWork;
  static TimingEvent accepted(uint64_t at, uint64_t latency, uint64_t interval,
                              EventType type, Resource resource) {
    return {at, checked_add(at, latency), interval, type, resource};
  }
  bool eligible(uint64_t cycle) const { return cycle >= eligible_cycle; }
};

struct Trace {
  bool enabled = false;
  uint64_t limit = 0, next_id = 0, work = 0;
  std::vector<im2p_cycle_event_t> events;
  uint64_t add(uint64_t cycle, EventType type, Resource resource,
               uint64_t loop = 0, uint64_t fragment = 0,
               uint64_t dependency = 0, uint64_t detail = 0, uint32_t i = 0,
               uint32_t j = 0, uint32_t k = 0) {
    if (!enabled)
      return 0;
    if (events.size() >= limit)
      throw Error(IM2P_CYCLE_LIMIT, "trace event limit exceeded");
    const auto id = ++next_id;
    events.push_back({id, cycle, work, loop, fragment, dependency, detail,
                      static_cast<uint32_t>(type),
                      static_cast<uint32_t>(resource), i, j, k, 0});
    return id;
  }
};
} // namespace im2p::cycle
