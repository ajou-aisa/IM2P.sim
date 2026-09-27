#include "../include/im2p_cycle_sequence.h"
#include "control_engine.hpp"
#include "timing_profile.hpp"
#include <algorithm>
#include <cstdio>
#include <limits>
#include <memory>
#include <new>
#include <optional>
#include <set>

using im2p::cycle::detail::Engine;
using im2p::cycle::detail::WorkContext;

static im2p_cycle_model_config_t
model_config(const im2p_cycle_sequence_config_t &config) {
  im2p_cycle_model_config_t model{};
  model.abi_version = IM2P_CYCLE_MODEL_ABI_VERSION;
  model.struct_size = sizeof(model);
  model.hardware = config.hardware;
  model.timing = config.timing;
  model.max_cycles = config.max_work_cycles;
  model.max_fragments = config.max_fragments;
  model.max_trace_events = config.max_trace_events;
  return model;
}

static im2p_cycle_request_t
request_for(const im2p_cycle_sequence_descriptor_t &descriptor,
            uint64_t offered_cycle) {
  im2p_cycle_request_t request{};
  im2p_cycle_request_init(&request);
  request.m = descriptor.m;
  request.n = descriptor.n;
  request.k = descriptor.k;
  request.tile_i = descriptor.tile_i;
  request.tile_j = descriptor.tile_j;
  request.tile_k = descriptor.tile_k;
  request.activation_stride_bytes = descriptor.activation_stride_bytes;
  request.weight_stride_bytes = descriptor.weight_stride_bytes;
  request.output_stride_bytes = descriptor.output_stride_bytes;
  request.scale_stride_elements = descriptor.scale_stride_elements;
  request.accepted_cycle = offered_cycle;
  request.logical_work_id = descriptor.logical_work_id;
  request.submission = descriptor.submission;
  request.record_events = descriptor.record_events;
  return request;
}

struct Pending {
  uint64_t offered_cycle;
  WorkContext work;
  Pending(const im2p_cycle_model_config_t &config,
          const im2p_cycle_sequence_descriptor_t &descriptor, uint64_t cycle)
      : offered_cycle(cycle),
        work(config, request_for(descriptor, cycle), descriptor.compact_runs) {}
};

struct im2p_cycle_sequence {
  im2p_cycle_sequence_config_t config;
  im2p_cycle_sequence_status_t status;
  im2p_cycle_sequence_error_t error;
  std::unique_ptr<Engine> engine;
  std::unique_ptr<Pending> pending;
  std::set<uint64_t> seen;
  std::optional<im2p_cycle_sequence_report_t> report;
};

static int sequence_code(int legacy) {
  switch (legacy) {
  case IM2P_CYCLE_INVALID:
    return IM2P_CYCLE_SEQUENCE_INVALID;
  case IM2P_CYCLE_UNSUPPORTED:
    return IM2P_CYCLE_SEQUENCE_UNSUPPORTED;
  case IM2P_CYCLE_OVERFLOW:
    return IM2P_CYCLE_SEQUENCE_OVERFLOW;
  case IM2P_CYCLE_LIMIT:
    return IM2P_CYCLE_SEQUENCE_LIMIT;
  default:
    return IM2P_CYCLE_SEQUENCE_INTERNAL;
  }
}
static int sequence_fault(im2p_cycle_sequence_t *sequence, int code,
                          im2p_cycle_sequence_stop_reason_t reason,
                          const char *message) noexcept {
  auto &status = sequence->status;
  status.faulted = 1;
  status.stop_reason = reason;
  im2p_cycle_sequence_error_init(&sequence->error);
  auto &error = sequence->error;
  error.code = code;
  error.stop_reason = reason;
  error.last_good_cycle = status.cursor;
  error.work_cycles = status.work_cycles;
  error.has_active = status.has_active;
  if (const auto *work = sequence->engine->current_work();
      status.has_active && work) {
    error.active_work_id = work->request.logical_work_id;
    error.partial_counters = work->result.counters;
  }
  std::snprintf(error.message, sizeof(error.message), "%s", message);
  return code;
}
extern "C" {
void im2p_cycle_sequence_config_init(im2p_cycle_sequence_config_t *config) {
  if (!config)
    return;
  im2p_cycle_model_config_t defaults;
  im2p_cycle_model_config_init(&defaults);
  *config = {};
  config->abi_version = IM2P_CYCLE_SEQUENCE_ABI_VERSION;
  config->struct_size = sizeof(*config);
  config->hardware = defaults.hardware;
  config->timing = defaults.timing;
  config->max_work_cycles = defaults.max_cycles;
  config->max_session_cycles = 1000000000;
  config->max_fragments = defaults.max_fragments;
  config->max_trace_events = defaults.max_trace_events;
  config->pending_capacity = 1;
  config->report_capacity = 1;
  config->max_work_ids = 65536;
}
void im2p_cycle_sequence_descriptor_init(
    im2p_cycle_sequence_descriptor_t *descriptor) {
  if (!descriptor)
    return;
  *descriptor = {};
  descriptor->abi_version = IM2P_CYCLE_SEQUENCE_ABI_VERSION;
  descriptor->struct_size = sizeof(*descriptor);
  descriptor->tile_i = descriptor->tile_j = descriptor->tile_k = 1;
}
void im2p_cycle_sequence_status_init(im2p_cycle_sequence_status_t *status) {
  if (!status)
    return;
  *status = {};
  status->abi_version = IM2P_CYCLE_SEQUENCE_ABI_VERSION;
  status->struct_size = sizeof(*status);
}
void im2p_cycle_sequence_tag_state_init(
    im2p_cycle_sequence_tag_state_t *tag_state) {
  if (!tag_state)
    return;
  *tag_state = {};
  tag_state->abi_version = IM2P_CYCLE_SEQUENCE_ABI_VERSION;
  tag_state->struct_size = sizeof(*tag_state);
  tag_state->full_backpressure_cycles = UINT64_MAX;
}
void im2p_cycle_sequence_row_pressure_init(
    im2p_cycle_sequence_row_pressure_t *pressure) {
  if (!pressure)
    return;
  *pressure = {};
  pressure->abi_version = IM2P_CYCLE_SEQUENCE_ROW_PRESSURE_ABI_VERSION;
  pressure->struct_size = sizeof(*pressure);
}
void im2p_cycle_sequence_domain_snapshot_init(
    im2p_cycle_sequence_domain_snapshot_t *snapshot) {
  if (!snapshot)
    return;
  *snapshot = {};
  snapshot->abi_version = IM2P_CYCLE_SEQUENCE_DOMAIN_ABI_VERSION;
  snapshot->struct_size = sizeof(*snapshot);
}
void im2p_cycle_sequence_report_init(im2p_cycle_sequence_report_t *report) {
  if (!report)
    return;
  *report = {};
  report->abi_version = IM2P_CYCLE_SEQUENCE_ABI_VERSION;
  report->struct_size = sizeof(*report);
  report->counters.abi_version = IM2P_CYCLE_MODEL_ABI_VERSION;
  report->counters.struct_size = sizeof(report->counters);
}
void im2p_cycle_sequence_error_init(im2p_cycle_sequence_error_t *error) {
  if (!error)
    return;
  *error = {};
  error->abi_version = IM2P_CYCLE_SEQUENCE_ABI_VERSION;
  error->struct_size = sizeof(*error);
  error->partial_counters.abi_version = IM2P_CYCLE_MODEL_ABI_VERSION;
  error->partial_counters.struct_size = sizeof(error->partial_counters);
}
int im2p_cycle_sequence_create(const im2p_cycle_sequence_config_t *config,
                               im2p_cycle_sequence_t **sequence) {
  if (!config || !sequence ||
      config->abi_version != IM2P_CYCLE_SEQUENCE_ABI_VERSION ||
      config->struct_size != sizeof(*config) || !config->max_session_cycles ||
      !config->pending_capacity || !config->report_capacity ||
      !config->max_work_ids)
    return IM2P_CYCLE_SEQUENCE_INVALID;
  if (config->pending_capacity != 1 || config->report_capacity != 1)
    return IM2P_CYCLE_SEQUENCE_UNSUPPORTED;
  try {
    im2p::cycle::validate_config(model_config(*config));
    auto *created =
        new im2p_cycle_sequence{*config, {}, {}, {}, {}, {}, {}};
    im2p_cycle_sequence_status_init(&created->status);
    im2p_cycle_sequence_error_init(&created->error);
    *sequence = created;
    return IM2P_CYCLE_SEQUENCE_OK;
  } catch (const im2p::cycle::Error &error) {
    return sequence_code(error.status);
  } catch (const std::bad_alloc &) {
    return IM2P_CYCLE_SEQUENCE_LIMIT;
  } catch (...) {
    return IM2P_CYCLE_SEQUENCE_INTERNAL;
  }
}
void im2p_cycle_sequence_destroy(im2p_cycle_sequence_t *sequence) {
  delete sequence;
}
int im2p_cycle_sequence_reset(im2p_cycle_sequence_t *sequence) {
  if (!sequence)
    return IM2P_CYCLE_SEQUENCE_INVALID;
  auto &status = sequence->status;
  if (!status.faulted &&
      (status.has_pending || status.has_active || status.has_report))
    return IM2P_CYCLE_SEQUENCE_WOULD_BLOCK;
  if (status.generation == UINT64_MAX)
    return IM2P_CYCLE_SEQUENCE_OVERFLOW;
  try {
    auto engine = std::make_unique<Engine>(model_config(sequence->config), true);
    im2p_cycle_sequence_status_t reset;
    im2p_cycle_sequence_status_init(&reset);
    reset.generation = status.generation + 1;
    reset.initialized = 1;
    reset.last_discarded_generation =
        status.faulted ? status.generation : status.last_discarded_generation;
    sequence->engine = std::move(engine);
    sequence->pending.reset();
    sequence->report.reset();
    sequence->seen.clear();
    status = reset;
    im2p_cycle_sequence_error_init(&sequence->error);
    return IM2P_CYCLE_SEQUENCE_OK;
  } catch (const std::bad_alloc &) {
    return IM2P_CYCLE_SEQUENCE_LIMIT;
  } catch (...) {
    return IM2P_CYCLE_SEQUENCE_INTERNAL;
  }
}
int im2p_cycle_sequence_offer(
    im2p_cycle_sequence_t *sequence,
    const im2p_cycle_sequence_descriptor_t *descriptor,
    uint64_t offered_cycle) {
  if (!sequence || !descriptor ||
      descriptor->abi_version != IM2P_CYCLE_SEQUENCE_ABI_VERSION ||
      descriptor->struct_size != sizeof(*descriptor))
    return IM2P_CYCLE_SEQUENCE_INVALID;
  const auto &status = sequence->status;
  if (status.faulted)
    return IM2P_CYCLE_SEQUENCE_FAULTED;
  if (!status.initialized || offered_cycle < status.cursor ||
      sequence->seen.contains(descriptor->logical_work_id))
    return IM2P_CYCLE_SEQUENCE_INVALID;
  if (sequence->seen.size() >= sequence->config.max_work_ids)
    return IM2P_CYCLE_SEQUENCE_LIMIT;
  if (sequence->pending)
    return IM2P_CYCLE_SEQUENCE_WOULD_BLOCK;
  try {
    auto candidate = std::make_unique<Pending>(model_config(sequence->config),
                                               *descriptor, offered_cycle);
    sequence->seen.insert(descriptor->logical_work_id);
    sequence->pending = std::move(candidate);
    sequence->status.has_pending = 1;
    return IM2P_CYCLE_SEQUENCE_OK;
  } catch (const im2p::cycle::Error &error) {
    return sequence_code(error.status);
  } catch (const std::bad_alloc &) {
    return IM2P_CYCLE_SEQUENCE_LIMIT;
  } catch (...) {
    return IM2P_CYCLE_SEQUENCE_INTERNAL;
  }
}
int im2p_cycle_sequence_advance_until(im2p_cycle_sequence_t *sequence,
                                      uint64_t until_cycle) {
  if (!sequence || !sequence->status.initialized ||
      until_cycle < sequence->status.cursor)
    return IM2P_CYCLE_SEQUENCE_INVALID;
  auto &status = sequence->status;
  if (status.faulted)
    return IM2P_CYCLE_SEQUENCE_FAULTED;
  if (until_cycle == status.cursor)
    return status.has_pending || status.has_active
               ? IM2P_CYCLE_SEQUENCE_INCOMPLETE
               : IM2P_CYCLE_SEQUENCE_OK;
  try {
    while (status.cursor < until_cycle) {
      if (status.cursor >= sequence->config.max_session_cycles) {
        return sequence_fault(sequence, IM2P_CYCLE_SEQUENCE_LIMIT,
                              IM2P_CYCLE_SEQUENCE_STOP_SESSION_BUDGET,
                              "session cycle budget exhausted");
      }
      if (sequence->pending &&
          sequence->pending->offered_cycle <= status.cursor &&
          sequence->engine->resource_ready()) {
        if (sequence->report) {
          status.stop_reason = IM2P_CYCLE_SEQUENCE_STOP_REPORT_BUFFER;
          return IM2P_CYCLE_SEQUENCE_WOULD_BLOCK;
        }
        const auto &candidate = sequence->pending->work;
        const unsigned half = status.next_scratchpad_half;
        const unsigned acc = status.next_accumulator_half;
        const unsigned next_half = half ^ (candidate.schedule.work.size() % 2);
        const unsigned next_acc =
            acc ^
            (std::count_if(candidate.schedule.work.begin(),
                           candidate.schedule.work.end(),
                           [](const auto &work) { return work.final_store; }) %
             2);
        const uint64_t id = candidate.request.logical_work_id;
        const uint64_t offered = sequence->pending->offered_cycle;
        try {
          WorkContext prepared = sequence->pending->work;
          sequence->engine->accept(std::move(prepared), status.cursor, half,
                                   acc, status.generation);
        } catch (const im2p::cycle::Error &error) {
          return sequence_code(error.status);
        } catch (const std::bad_alloc &) {
          return IM2P_CYCLE_SEQUENCE_LIMIT;
        } catch (...) {
          return IM2P_CYCLE_SEQUENCE_INTERNAL;
        }
        sequence->pending.reset();
        status.has_pending = 0;
        status.has_active = 1;
        status.has_accepted = 1;
        status.accepted_work_id = id;
        status.offered_cycle = offered;
        status.accepted_cycle = status.cursor;
        status.work_cycles = 0;
        status.next_scratchpad_half = next_half;
        status.next_accumulator_half = next_acc;
      }
      if (status.has_active && status.cursor - status.accepted_cycle >=
                                   sequence->config.max_work_cycles) {
        return sequence_fault(sequence, IM2P_CYCLE_SEQUENCE_LIMIT,
                              IM2P_CYCLE_SEQUENCE_STOP_WORK_BUDGET,
                              "work cycle budget exhausted");
      }
      if (!sequence->engine->step()) {
        if (!sequence->engine->buffered_event_count()) {
          return sequence_fault(sequence, IM2P_CYCLE_SEQUENCE_FAULTED,
                                IM2P_CYCLE_SEQUENCE_STOP_EVENT_BURST,
                                "event burst exceeds buffer capacity");
        }
        status.stop_reason =
            IM2P_CYCLE_SEQUENCE_STOP_EVENT_BUFFER_AVAILABLE;
        return IM2P_CYCLE_SEQUENCE_WOULD_BLOCK;
      }
      status.cursor = sequence->engine->cycle();
      status.session_cycles = status.cursor;
      if (status.has_active) {
        status.work_cycles = status.cursor - status.accepted_cycle;
        if (sequence->engine->resource_ready()) {
          const auto &done = sequence->engine->finish();
          const auto *work = sequence->engine->current_work();
          im2p_cycle_sequence_report_t report;
          im2p_cycle_sequence_report_init(&report);
          report.generation = status.generation;
          report.logical_work_id = work->request.logical_work_id;
          report.offered_cycle = status.offered_cycle;
          report.accepted_cycle = status.accepted_cycle;
          report.result_ready_cycle = done.service.result_ready_cycle;
          report.final_scale_release_cycle =
              done.service.final_scale_release_cycle;
          report.resource_ready_cycle = done.service.resource_ready_cycle;
          report.event_count = done.counters.event_count;
          report.next_scratchpad_half = done.service.next_scratchpad_half;
          report.next_accumulator_half = done.service.next_accumulator_half;
          report.counters = done.counters;
          sequence->report = report;
          sequence->engine->retire_work();
          status.has_report = 1;
          status.has_active = 0;
        }
      }
    }
    status.stop_reason = IM2P_CYCLE_SEQUENCE_STOP_TARGET;
    return status.has_active || status.has_pending
               ? IM2P_CYCLE_SEQUENCE_INCOMPLETE
               : IM2P_CYCLE_SEQUENCE_OK;
  } catch (const im2p::cycle::Error &error) {
    return sequence_fault(sequence, IM2P_CYCLE_SEQUENCE_FAULTED,
                          IM2P_CYCLE_SEQUENCE_STOP_FAULT, error.what());
  } catch (const std::bad_alloc &) {
    return sequence_fault(sequence, IM2P_CYCLE_SEQUENCE_FAULTED,
                          IM2P_CYCLE_SEQUENCE_STOP_FAULT,
                          "allocation failed during simulation step");
  } catch (...) {
    return sequence_fault(sequence, IM2P_CYCLE_SEQUENCE_FAULTED,
                          IM2P_CYCLE_SEQUENCE_STOP_FAULT,
                          "simulation step failed");
  }
}
int im2p_cycle_sequence_get_status(const im2p_cycle_sequence_t *sequence,
                                   im2p_cycle_sequence_status_t *status) {
  if (!sequence || !status ||
      status->abi_version != IM2P_CYCLE_SEQUENCE_ABI_VERSION ||
      status->struct_size != sizeof(*status))
    return IM2P_CYCLE_SEQUENCE_INVALID;
  *status = sequence->status;
  return IM2P_CYCLE_SEQUENCE_OK;
}
int im2p_cycle_sequence_get_tag_state(
    const im2p_cycle_sequence_t *sequence,
    im2p_cycle_sequence_tag_state_t *tag_state) {
  if (!sequence || !tag_state ||
      tag_state->abi_version != IM2P_CYCLE_SEQUENCE_ABI_VERSION ||
      tag_state->struct_size != sizeof(*tag_state) ||
      !sequence->status.initialized || !sequence->engine)
    return IM2P_CYCLE_SEQUENCE_INVALID;
  im2p_cycle_sequence_tag_state_t snapshot;
  im2p_cycle_sequence_tag_state_init(&snapshot);
  const auto &array = sequence->engine->array_state_for_test();
  snapshot.generation = sequence->status.generation;
  snapshot.cursor = sequence->status.cursor;
  snapshot.enqueues = sequence->engine->tag_enqueues_for_test();
  snapshot.dequeues = sequence->engine->tag_dequeues_for_test();
  snapshot.queue_len = static_cast<uint32_t>(array.tags.size());
  snapshot.head_valid = !array.tags.empty();
  if (snapshot.head_valid)
    snapshot.head_id = array.tags.front().id;
  *tag_state = snapshot;
  return IM2P_CYCLE_SEQUENCE_OK;
}
int im2p_cycle_sequence_get_row_pressure(
    const im2p_cycle_sequence_t *sequence,
    im2p_cycle_sequence_row_pressure_t *pressure) {
  if (!sequence || !pressure ||
      pressure->abi_version != IM2P_CYCLE_SEQUENCE_ROW_PRESSURE_ABI_VERSION ||
      pressure->struct_size != sizeof(*pressure) ||
      !sequence->status.initialized || !sequence->engine)
    return IM2P_CYCLE_SEQUENCE_INVALID;
  im2p_cycle_sequence_row_pressure_t copy;
  im2p_cycle_sequence_row_pressure_init(&copy);
  copy.generation = sequence->status.generation;
  copy.cursor = sequence->status.cursor;
  copy.row_count =
      static_cast<uint32_t>(sequence->engine->array_state_for_test().row_counts.size());
  copy.max_row_occupancy = sequence->engine->row_count_peak();
  *pressure = copy;
  return IM2P_CYCLE_SEQUENCE_OK;
}
int im2p_cycle_sequence_get_domain_snapshot(
    const im2p_cycle_sequence_t *sequence,
    im2p_cycle_sequence_domain_snapshot_t *snapshot) {
  if (!sequence || !snapshot ||
      snapshot->abi_version != IM2P_CYCLE_SEQUENCE_DOMAIN_ABI_VERSION ||
      snapshot->struct_size != sizeof(*snapshot) ||
      !sequence->status.initialized || !sequence->engine)
    return IM2P_CYCLE_SEQUENCE_INVALID;
  im2p_cycle_sequence_domain_snapshot_t copy;
  im2p_cycle_sequence_domain_snapshot_init(&copy);
  copy.generation = sequence->status.generation;
  copy.cursor = sequence->status.cursor;
  if (!sequence->engine->domain_snapshot(copy))
    return IM2P_CYCLE_SEQUENCE_INTERNAL;
  *snapshot = copy;
  return IM2P_CYCLE_SEQUENCE_OK;
}
int im2p_cycle_sequence_get_error(const im2p_cycle_sequence_t *sequence,
                                  im2p_cycle_sequence_error_t *error) {
  if (!sequence || !error ||
      error->abi_version != IM2P_CYCLE_SEQUENCE_ABI_VERSION ||
      error->struct_size != sizeof(*error))
    return IM2P_CYCLE_SEQUENCE_INVALID;
  *error = sequence->error;
  return IM2P_CYCLE_SEQUENCE_OK;
}
int im2p_cycle_sequence_pop_report(im2p_cycle_sequence_t *sequence,
                                   im2p_cycle_sequence_report_t *report) {
  if (!sequence || !report ||
      report->abi_version != IM2P_CYCLE_SEQUENCE_ABI_VERSION ||
      report->struct_size != sizeof(*report))
    return IM2P_CYCLE_SEQUENCE_INVALID;
  if (!sequence->status.initialized)
    return IM2P_CYCLE_SEQUENCE_INVALID;
  if (sequence->status.faulted)
    return IM2P_CYCLE_SEQUENCE_FAULTED;
  if (!sequence->report)
    return IM2P_CYCLE_SEQUENCE_WOULD_BLOCK;
  *report = *sequence->report;
  sequence->report.reset();
  sequence->status.has_report = 0;
  return IM2P_CYCLE_SEQUENCE_OK;
}
int im2p_cycle_sequence_read_events(im2p_cycle_sequence_t *sequence,
                                    im2p_cycle_sequence_event_t *events,
                                    uint64_t capacity, uint64_t *count) {
  if (!sequence || !count || (capacity && !events) ||
      !sequence->status.initialized)
    return IM2P_CYCLE_SEQUENCE_INVALID;
  if (capacity >
      std::numeric_limits<size_t>::max() / sizeof(im2p_cycle_sequence_event_t))
    return IM2P_CYCLE_SEQUENCE_OVERFLOW;
  const auto available = sequence->engine->buffered_event_count();
  if (!capacity) {
    *count = available;
    return IM2P_CYCLE_SEQUENCE_OK;
  }
  const auto amount = std::min<uint64_t>(available, capacity);
  for (uint64_t i = 0; i < amount; ++i) {
    im2p::cycle::detail::DiagnosticEvent item;
    sequence->engine->drain_events(&item, 1);
    events[i] = {IM2P_CYCLE_SEQUENCE_ABI_VERSION,
                 sizeof(im2p_cycle_sequence_event_t),
                 item.origin.generation,
                 item.origin.ordinal,
                 item.origin.submission,
                 0,
                 item.event};
  }
  *count = amount;
  return IM2P_CYCLE_SEQUENCE_OK;
}
int im2p_cycle_sequence_get_cumulative_counters(
    const im2p_cycle_sequence_t *sequence, im2p_cycle_result_t *counters) {
  if (!sequence || !counters || !sequence->status.initialized ||
      counters->abi_version != IM2P_CYCLE_MODEL_ABI_VERSION ||
      counters->struct_size != sizeof(*counters))
    return IM2P_CYCLE_SEQUENCE_INVALID;
  *counters = sequence->engine->cumulative_counters();
  return IM2P_CYCLE_SEQUENCE_OK;
}
}
