#include "cycle_model.hpp"
#include <algorithm>
#include <array>
#include <cstdio>
#include <cstring>
#include <new>
#include <string>

struct im2p_cycle_model {
  im2p_cycle_model_config_t config;
  im2p::cycle::Trace trace;
  std::array<char, 256> error{};
};
extern "C" {
void im2p_cycle_model_config_init(im2p_cycle_model_config_t *c) {
  if (!c)
    return;
  *c = {};
  c->abi_version = IM2P_CYCLE_MODEL_ABI_VERSION;
  c->struct_size = sizeof(*c);
  c->timing = {1, 3, 13, 17, 11, 5, 5, 0};
  c->max_cycles = 10000000;
  c->max_fragments = 1000000;
  c->max_trace_events = 1000000;
}
void im2p_cycle_request_init(im2p_cycle_request_t *r) {
  if (!r)
    return;
  *r = {};
  r->abi_version = IM2P_CYCLE_MODEL_ABI_VERSION;
  r->struct_size = sizeof(*r);
  r->tile_i = r->tile_j = r->tile_k = 1;
}
im2p_cycle_model_t *
im2p_cycle_model_create(const im2p_cycle_model_config_t *c) {
  if (!c)
    return nullptr;
  try {
    im2p::cycle::validate_config(*c);
    return new im2p_cycle_model{*c, {}, {}};
  } catch (...) {
    return nullptr;
  }
}
void im2p_cycle_model_destroy(im2p_cycle_model_t *m) { delete m; }
static int estimate_impl(im2p_cycle_model_t *m, const im2p_cycle_request_t *r,
                         const im2p_compact_runs_t *runs,
                         im2p_cycle_result_t *out) {
  if (!m)
    return IM2P_CYCLE_INVALID;
  m->error.fill(0);
  m->trace = {};
  if (!r || !out) {
    std::snprintf(m->error.data(), m->error.size(), "%s",
                  "null request or result");
    return IM2P_CYCLE_INVALID;
  }
  try {
    auto result = im2p::cycle::estimate(m->config, *r, runs);
    m->trace = std::move(result.trace);
    *out = result.counters;
    return IM2P_CYCLE_OK;
  } catch (const im2p::cycle::Error &error) {
    std::snprintf(m->error.data(), m->error.size(), "%s", error.what());
    return error.status;
  } catch (const std::bad_alloc &) {
    std::snprintf(m->error.data(), m->error.size(), "%s", "allocation limit");
    return IM2P_CYCLE_LIMIT;
  } catch (const std::exception &error) {
    std::snprintf(m->error.data(), m->error.size(), "%s", error.what());
    return IM2P_CYCLE_INTERNAL;
  } catch (...) {
    std::snprintf(m->error.data(), m->error.size(), "%s",
                  "unexpected cycle-model failure");
    return IM2P_CYCLE_INTERNAL;
  }
}
int im2p_cycle_estimate(im2p_cycle_model_t *m, const im2p_cycle_request_t *r,
                        im2p_cycle_result_t *out) {
  return estimate_impl(m, r, nullptr, out);
}
int im2p_cycle_estimate_runs(im2p_cycle_model_t *m,
                             const im2p_cycle_request_t *r,
                             const im2p_compact_runs_t *runs,
                             im2p_cycle_result_t *out) {
  if (!runs) {
    if (m) {
      m->error.fill(0);
      m->trace = {};
      std::snprintf(m->error.data(), m->error.size(), "%s",
                    "null compact run view");
    }
    return IM2P_CYCLE_INVALID;
  }
  return estimate_impl(m, r, runs, out);
}
const char *im2p_cycle_model_error(const im2p_cycle_model_t *m) {
  return m ? m->error.data() : "null cycle model";
}
uint64_t im2p_cycle_model_event_count(const im2p_cycle_model_t *m) {
  return m ? m->trace.events.size() : 0;
}
int im2p_cycle_model_event(const im2p_cycle_model_t *m, uint64_t index,
                           im2p_cycle_event_t *out) {
  if (!m || !out || index >= m->trace.events.size())
    return IM2P_CYCLE_INVALID;
  *out = m->trace.events[static_cast<size_t>(index)];
  return IM2P_CYCLE_OK;
}
const char *im2p_cycle_event_name(uint32_t kind) {
  static constexpr const char *names[] = {"work",
                                          "scale_request",
                                          "scale_response",
                                          "scale_lane",
                                          "bridge_issue",
                                          "loop_issue",
                                          "command_allocated",
                                          "load_issue",
                                          "execute_issue",
                                          "store_issue",
                                          "context",
                                          "load_dma",
                                          "read_request",
                                          "read_response",
                                          "scratchpad_read",
                                          "preload_issue",
                                          "compute_issue",
                                          "array_input",
                                          "array_output",
                                          "accumulator_write",
                                          "accumulator_commit",
                                          "raw_completed",
                                          "rob_completed",
                                          "store_dma",
                                          "write_request",
                                          "write_completion",
                                          "loop_done",
                                          "scale_release",
                                          "logical_done"};
  return kind < sizeof(names) / sizeof(*names) ? names[kind] : "invalid";
}
const char *im2p_cycle_resource_name(uint32_t kind) {
  static constexpr const char *names[] = {
      "host_work",     "scale_path",  "command_bridge", "loop_controller",
      "reservation",   "load_engine", "scratchpad",     "array",
      "scu_writeback", "accumulator", "store_engine",   "backing"};
  return kind < sizeof(names) / sizeof(*names) ? names[kind] : "invalid";
}
} // extern "C"
