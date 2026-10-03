#include "../../include/im2p_cycle_sequence.h"
#include <stddef.h>
#include <stdio.h>
#include <string.h>

#define CHECK(condition)                                                       \
  do {                                                                         \
    if (!(condition)) {                                                        \
      fprintf(stderr, "sequence ABI check failed at line %d\n", __LINE__);     \
      return 1;                                                                \
    }                                                                          \
  } while (0)

_Static_assert(offsetof(im2p_cycle_sequence_config_t, abi_version) == 0,
               "config version must lead");
_Static_assert(offsetof(im2p_cycle_sequence_descriptor_t, abi_version) == 0,
               "descriptor version must lead");
_Static_assert(offsetof(im2p_cycle_sequence_status_t, abi_version) == 0,
               "status version must lead");
_Static_assert(offsetof(im2p_cycle_sequence_report_t, abi_version) == 0,
               "report version must lead");
_Static_assert(offsetof(im2p_cycle_sequence_error_t, abi_version) == 0,
               "error version must lead");
_Static_assert(sizeof(im2p_cycle_sequence_config_t) == 136,
               "config layout changed");
_Static_assert(offsetof(im2p_cycle_sequence_config_t, max_work_ids) == 128,
               "work ID limit offset changed");
_Static_assert(sizeof(im2p_cycle_sequence_descriptor_t) == 112,
               "descriptor layout changed");
_Static_assert(sizeof(im2p_cycle_sequence_status_t) == 112,
               "status layout changed");
_Static_assert(sizeof(im2p_cycle_sequence_report_t) == 200,
               "report layout changed");
_Static_assert(sizeof(im2p_cycle_sequence_event_t) == 112,
               "event layout changed");
_Static_assert(sizeof(im2p_cycle_sequence_error_t) == 296,
               "error layout changed");
_Static_assert(offsetof(im2p_cycle_sequence_error_t, partial_counters) == 48,
               "error counters offset changed");
_Static_assert(offsetof(im2p_cycle_sequence_error_t, message) == 168,
               "error message offset changed");
_Static_assert(sizeof(im2p_cycle_sequence_domain_row_t) == 8,
               "domain row layout changed");
_Static_assert(IM2P_CYCLE_SEQUENCE_DOMAIN_ABI_VERSION == 2u,
               "domain ABI version changed");
_Static_assert(sizeof(im2p_cycle_sequence_domain_tag_t) == 64,
               "domain tag layout changed");
_Static_assert(offsetof(im2p_cycle_sequence_domain_tag_t,
                        preload_accumulate) == 56,
               "domain accumulation offset changed");
_Static_assert(sizeof(im2p_cycle_sequence_domain_snapshot_t) == 528,
               "domain snapshot layout changed");
_Static_assert(offsetof(im2p_cycle_sequence_domain_snapshot_t, tags) == 96,
               "domain tag offset changed");
_Static_assert(offsetof(im2p_cycle_sequence_domain_snapshot_t, banks) == 480,
               "domain bank offset changed");

static int drain_events(im2p_cycle_sequence_t *sequence, uint64_t work_id,
                        uint64_t ordinal, uint64_t *last_id,
                        uint64_t *drained) {
  uint64_t available = 0, count = 0;
  im2p_cycle_sequence_event_t events[64];
  im2p_cycle_sequence_status_t before, after;
  im2p_cycle_sequence_status_init(&before);
  im2p_cycle_sequence_status_init(&after);
  CHECK(im2p_cycle_sequence_get_status(sequence, &before) ==
        IM2P_CYCLE_SEQUENCE_OK);
  CHECK(im2p_cycle_sequence_read_events(sequence, NULL, 0, &available) ==
        IM2P_CYCLE_SEQUENCE_OK);
  CHECK(available <= 64);
  if (available) {
    CHECK(im2p_cycle_sequence_read_events(sequence, events, 1, &count) ==
              IM2P_CYCLE_SEQUENCE_OK &&
          count == 1);
    CHECK(im2p_cycle_sequence_read_events(sequence, NULL, 0, &count) ==
              IM2P_CYCLE_SEQUENCE_OK &&
          count == available - 1);
    CHECK(im2p_cycle_sequence_read_events(sequence, events + 1, 63,
                                          &count) ==
              IM2P_CYCLE_SEQUENCE_OK &&
          count == available - 1);
  }
  CHECK(im2p_cycle_sequence_read_events(sequence, NULL, 0, &count) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        count == 0);
  for (uint64_t i = 0; i < available; ++i) {
    CHECK(events[i].abi_version == IM2P_CYCLE_SEQUENCE_ABI_VERSION &&
          events[i].struct_size == sizeof(events[i]) &&
          events[i].generation == 1 && events[i].work_ordinal == ordinal &&
          events[i].event.logical_work_id == work_id &&
          events[i].event.id > *last_id);
    *last_id = events[i].event.id;
  }
  *drained += available;
  CHECK(im2p_cycle_sequence_get_status(sequence, &after) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        before.cursor == after.cursor);
  return 0;
}

static int run_event_case(unsigned record_events,
                          im2p_cycle_sequence_report_t reports[2],
                          im2p_cycle_result_t *cumulative,
                          uint64_t *drained_total) {
  im2p_cycle_sequence_config_t config;
  im2p_cycle_sequence_config_init(&config);
  config.hardware =
      (im2p_cycle_hardware_t){8, 8, 16, 32, 32, 4, 4096, 1024, 16, 64, 4, 2};
  config.max_trace_events = 64;
  im2p_cycle_sequence_t *sequence = NULL;
  CHECK(im2p_cycle_sequence_create(&config, &sequence) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        im2p_cycle_sequence_reset(sequence) == IM2P_CYCLE_SEQUENCE_OK);
  im2p_cycle_sequence_descriptor_t work;
  im2p_cycle_sequence_descriptor_init(&work);
  work.m = work.n = 1;
  work.submission = IM2P_CYCLE_TILE_SUBMISSIONS;
  work.record_events = record_events;
  uint64_t last_id = 0;
  *drained_total = 0;
  unsigned buffer_pauses = 0;
  for (unsigned ordinal = 1; ordinal <= 2; ++ordinal) {
    im2p_cycle_sequence_status_t status;
    im2p_cycle_sequence_status_init(&status);
    CHECK(im2p_cycle_sequence_get_status(sequence, &status) ==
          IM2P_CYCLE_SEQUENCE_OK);
    work.logical_work_id = 499 + ordinal;
    work.k = ordinal == 1 ? 22 : 1;
    work.tile_k = ordinal == 1 ? 2 : 1;
    CHECK(im2p_cycle_sequence_offer(sequence, &work, status.cursor) ==
          IM2P_CYCLE_SEQUENCE_OK);
    uint64_t drained_work = 0;
    for (;;) {
      CHECK(im2p_cycle_sequence_get_status(sequence, &status) ==
            IM2P_CYCLE_SEQUENCE_OK);
      if (status.has_report)
        break;
      CHECK(status.cursor < 1000);
      const int advanced =
          im2p_cycle_sequence_advance_until(sequence, status.cursor + 1);
      if (advanced == IM2P_CYCLE_SEQUENCE_WOULD_BLOCK) {
        ++buffer_pauses;
        CHECK(im2p_cycle_sequence_get_status(sequence, &status) ==
                  IM2P_CYCLE_SEQUENCE_OK &&
              status.stop_reason ==
                  IM2P_CYCLE_SEQUENCE_STOP_EVENT_BUFFER_AVAILABLE &&
              !status.faulted);
        CHECK(record_events &&
              drain_events(sequence, work.logical_work_id, ordinal, &last_id,
                           &drained_work) == 0);
      } else
        CHECK(advanced == IM2P_CYCLE_SEQUENCE_INCOMPLETE ||
              advanced == IM2P_CYCLE_SEQUENCE_OK);
    }
    if (record_events)
      CHECK(drain_events(sequence, work.logical_work_id, ordinal, &last_id,
                         &drained_work) == 0);
    im2p_cycle_sequence_report_init(&reports[ordinal - 1]);
    CHECK(im2p_cycle_sequence_pop_report(sequence, &reports[ordinal - 1]) ==
              IM2P_CYCLE_SEQUENCE_OK &&
          reports[ordinal - 1].logical_work_id == work.logical_work_id &&
          reports[ordinal - 1].resource_ready_cycle == status.cursor &&
          reports[ordinal - 1].event_count ==
              reports[ordinal - 1].counters.event_count);
    if (record_events)
      CHECK(drained_work == reports[ordinal - 1].event_count);
    else
      CHECK(drained_work == 0);
    *drained_total += drained_work;
  }
  cumulative->abi_version = IM2P_CYCLE_MODEL_ABI_VERSION;
  cumulative->struct_size = sizeof(*cumulative);
  CHECK(im2p_cycle_sequence_get_cumulative_counters(sequence, cumulative) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        cumulative->logical_work_count == 2 &&
        cumulative->event_count == reports[0].event_count +
                                       reports[1].event_count);
  CHECK(record_events ? buffer_pauses > 0 : buffer_pauses == 0);
  printf("SEQUENCE_EVENT_BUFFER_PASS enabled=%u pauses=%u drained=%llu\n",
         record_events, buffer_pauses, (unsigned long long)*drained_total);
  im2p_cycle_sequence_destroy(sequence);
  return 0;
}

static int test_tiny_event_buffer(void) {
  im2p_cycle_sequence_config_t config;
  im2p_cycle_sequence_config_init(&config);
  config.hardware =
      (im2p_cycle_hardware_t){8, 8, 16, 32, 32, 4, 4096, 1024, 16, 64, 4, 2};
  config.max_trace_events = 1;
  im2p_cycle_sequence_t *sequence = NULL;
  CHECK(im2p_cycle_sequence_create(&config, &sequence) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        im2p_cycle_sequence_reset(sequence) == IM2P_CYCLE_SEQUENCE_OK);
  im2p_cycle_sequence_descriptor_t work;
  im2p_cycle_sequence_descriptor_init(&work);
  work.logical_work_id = 600;
  work.m = work.n = 1;
  work.k = 22;
  work.tile_k = 2;
  work.submission = IM2P_CYCLE_TILE_SUBMISSIONS;
  work.record_events = 1;
  CHECK(im2p_cycle_sequence_offer(sequence, &work, 0) ==
        IM2P_CYCLE_SEQUENCE_OK);
  int faulted = 0;
  for (unsigned i = 0; i < 1000; ++i) {
    im2p_cycle_sequence_status_t status;
    im2p_cycle_sequence_status_init(&status);
    CHECK(im2p_cycle_sequence_get_status(sequence, &status) ==
          IM2P_CYCLE_SEQUENCE_OK);
    const int advanced =
        im2p_cycle_sequence_advance_until(sequence, status.cursor + 1);
    CHECK(im2p_cycle_sequence_get_status(sequence, &status) ==
          IM2P_CYCLE_SEQUENCE_OK);
    if (advanced == IM2P_CYCLE_SEQUENCE_FAULTED) {
      CHECK(status.faulted && !status.has_report &&
            status.stop_reason == IM2P_CYCLE_SEQUENCE_STOP_EVENT_BURST);
      im2p_cycle_sequence_error_t error;
      im2p_cycle_sequence_error_init(&error);
      CHECK(im2p_cycle_sequence_get_error(sequence, &error) ==
                IM2P_CYCLE_SEQUENCE_OK &&
            error.code == IM2P_CYCLE_SEQUENCE_FAULTED &&
            error.stop_reason == IM2P_CYCLE_SEQUENCE_STOP_EVENT_BURST &&
            error.last_good_cycle == status.cursor && error.has_active &&
            error.active_work_id == work.logical_work_id &&
            error.partial_counters.logical_work_count == 1);
      faulted = 1;
      break;
    }
    if (advanced == IM2P_CYCLE_SEQUENCE_WOULD_BLOCK) {
      uint64_t count = 0;
      im2p_cycle_sequence_event_t event;
      CHECK(status.stop_reason ==
            IM2P_CYCLE_SEQUENCE_STOP_EVENT_BUFFER_AVAILABLE);
      CHECK(im2p_cycle_sequence_read_events(sequence, &event, 1, &count) ==
                IM2P_CYCLE_SEQUENCE_OK &&
            count == 1);
    } else
      CHECK(advanced == IM2P_CYCLE_SEQUENCE_INCOMPLETE);
  }
  im2p_cycle_sequence_destroy(sequence);
  CHECK(faulted);
  puts("SEQUENCE_TINY_EVENT_BUFFER_FAULT_PASS capacity=1 exact_report=0");
  return 0;
}

static int test_result_ready_releasing(void) {
  im2p_cycle_sequence_config_t config;
  im2p_cycle_sequence_config_init(&config);
  config.hardware =
      (im2p_cycle_hardware_t){8, 8, 16, 32, 32, 4, 4096, 1024, 16, 64, 4, 2};
  im2p_cycle_sequence_t *sequence = NULL;
  CHECK(im2p_cycle_sequence_create(&config, &sequence) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        im2p_cycle_sequence_reset(sequence) == IM2P_CYCLE_SEQUENCE_OK);
  im2p_cycle_sequence_descriptor_t work;
  im2p_cycle_sequence_descriptor_init(&work);
  work.logical_work_id = 650;
  work.m = work.n = 1;
  work.k = 22;
  work.tile_k = 2;
  work.submission = IM2P_CYCLE_TILE_SUBMISSIONS;
  work.record_events = 1;
  CHECK(im2p_cycle_sequence_offer(sequence, &work, 0) ==
        IM2P_CYCLE_SEQUENCE_OK);
  uint64_t result_cycle = 0;
  for (unsigned i = 0; i < 1000 && !result_cycle; ++i) {
    im2p_cycle_sequence_status_t status;
    im2p_cycle_sequence_status_init(&status);
    CHECK(im2p_cycle_sequence_get_status(sequence, &status) ==
          IM2P_CYCLE_SEQUENCE_OK);
    CHECK(im2p_cycle_sequence_advance_until(sequence, status.cursor + 1) ==
          IM2P_CYCLE_SEQUENCE_INCOMPLETE);
    CHECK(im2p_cycle_sequence_get_status(sequence, &status) ==
          IM2P_CYCLE_SEQUENCE_OK);
    im2p_cycle_sequence_event_t events[64];
    uint64_t count = 0;
    CHECK(im2p_cycle_sequence_read_events(sequence, events, 64, &count) ==
          IM2P_CYCLE_SEQUENCE_OK);
    for (uint64_t j = 0; j < count; ++j)
      if (strcmp(im2p_cycle_event_name(events[j].event.type),
                 "logical_done") == 0) {
        result_cycle = status.cursor;
        CHECK(status.has_active && !status.has_report && !status.faulted);
        CHECK(im2p_cycle_sequence_reset(sequence) ==
              IM2P_CYCLE_SEQUENCE_WOULD_BLOCK);
      }
  }
  CHECK(result_cycle > 0);
  im2p_cycle_sequence_status_t status;
  im2p_cycle_sequence_status_init(&status);
  CHECK(im2p_cycle_sequence_advance_until(sequence, 1000) ==
        IM2P_CYCLE_SEQUENCE_OK);
  CHECK(im2p_cycle_sequence_get_status(sequence, &status) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        status.has_report && !status.has_active &&
        status.cursor > result_cycle);
  CHECK(im2p_cycle_sequence_reset(sequence) ==
        IM2P_CYCLE_SEQUENCE_WOULD_BLOCK);
  im2p_cycle_sequence_report_t report;
  im2p_cycle_sequence_report_init(&report);
  CHECK(im2p_cycle_sequence_pop_report(sequence, &report) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        report.logical_work_id == work.logical_work_id &&
        report.resource_ready_cycle > result_cycle &&
        report.resource_ready_cycle <= status.cursor);
  printf("SEQUENCE_RELEASING_PASS result=%llu resource=%llu\n",
         (unsigned long long)result_cycle,
         (unsigned long long)report.resource_ready_cycle);
  im2p_cycle_sequence_destroy(sequence);
  return 0;
}

static int test_domain_snapshot(void) {
  im2p_cycle_sequence_config_t config;
  im2p_cycle_sequence_config_init(&config);
  config.hardware =
      (im2p_cycle_hardware_t){4, 4, 16, 32, 32, 4, 8192, 1024, 8, 64, 4, 2};
  im2p_cycle_sequence_t *sequence = NULL;
  CHECK(im2p_cycle_sequence_create(&config, &sequence) ==
        IM2P_CYCLE_SEQUENCE_OK);
  im2p_cycle_sequence_domain_snapshot_t domain;
  im2p_cycle_sequence_domain_snapshot_init(&domain);
  im2p_cycle_sequence_domain_snapshot_t saved = domain;
  CHECK(im2p_cycle_sequence_get_domain_snapshot(sequence, &domain) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        memcmp(&domain, &saved, sizeof(domain)) == 0);
  CHECK(im2p_cycle_sequence_reset(sequence) == IM2P_CYCLE_SEQUENCE_OK);
  CHECK(im2p_cycle_sequence_get_domain_snapshot(sequence, &domain) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        domain.generation == 1 && domain.cursor == 0 &&
        domain.resource_ready && !domain.row_count && !domain.tag_count &&
        !domain.max_tag_occupancy && !domain.ready_violation_mask);
  --domain.abi_version;
  saved = domain;
  CHECK(im2p_cycle_sequence_get_domain_snapshot(sequence, &domain) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        memcmp(&domain, &saved, sizeof(domain)) == 0);
  im2p_cycle_sequence_domain_snapshot_init(&domain);
  ++domain.abi_version;
  saved = domain;
  CHECK(im2p_cycle_sequence_get_domain_snapshot(sequence, &domain) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        memcmp(&domain, &saved, sizeof(domain)) == 0);
  im2p_cycle_sequence_domain_snapshot_init(&domain);
  --domain.struct_size;
  saved = domain;
  CHECK(im2p_cycle_sequence_get_domain_snapshot(sequence, &domain) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        memcmp(&domain, &saved, sizeof(domain)) == 0);
  im2p_cycle_sequence_domain_snapshot_init(&domain);
  ++domain.struct_size;
  saved = domain;
  CHECK(im2p_cycle_sequence_get_domain_snapshot(sequence, &domain) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        memcmp(&domain, &saved, sizeof(domain)) == 0);
  im2p_cycle_sequence_domain_snapshot_init(&domain);
  saved = domain;
  CHECK(im2p_cycle_sequence_get_domain_snapshot(NULL, &domain) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        memcmp(&domain, &saved, sizeof(domain)) == 0);
  CHECK(im2p_cycle_sequence_get_domain_snapshot(sequence, NULL) ==
        IM2P_CYCLE_SEQUENCE_INVALID);

  im2p_cycle_sequence_descriptor_t a, b;
  im2p_cycle_sequence_descriptor_init(&a);
  a.logical_work_id = 0;
  a.m = 80;
  a.n = 81;
  a.k = 96;
  a.tile_i = 5;
  a.tile_j = 6;
  a.tile_k = 6;
  a.activation_stride_bytes = 96;
  a.weight_stride_bytes = 81;
  a.output_stride_bytes = 324;
  a.scale_stride_elements = 81;
  a.submission = IM2P_CYCLE_BLOCK_SUBMISSIONS;
  b = a;
  b.logical_work_id = 1;
  b.m = b.n = b.k = 1;
  b.tile_i = b.tile_j = b.tile_k = 1;
  b.activation_stride_bytes = b.weight_stride_bytes = 0;
  b.output_stride_bytes = b.scale_stride_elements = 0;
  CHECK(im2p_cycle_sequence_offer(sequence, &a, 0) ==
        IM2P_CYCLE_SEQUENCE_OK);
  CHECK(im2p_cycle_sequence_advance_until(sequence, 1) ==
        IM2P_CYCLE_SEQUENCE_INCOMPLETE);
  CHECK(im2p_cycle_sequence_get_domain_snapshot(sequence, &domain) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        domain.cursor == 1 && !domain.resource_ready);
  CHECK(im2p_cycle_sequence_offer(sequence, &b, 1) ==
        IM2P_CYCLE_SEQUENCE_OK);
  im2p_cycle_sequence_status_t status;
  im2p_cycle_sequence_status_init(&status);
  CHECK(im2p_cycle_sequence_get_status(sequence, &status) ==
        IM2P_CYCLE_SEQUENCE_OK);
  im2p_cycle_sequence_status_t before = status;
  --domain.struct_size;
  saved = domain;
  CHECK(im2p_cycle_sequence_get_domain_snapshot(sequence, &domain) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        memcmp(&domain, &saved, sizeof(domain)) == 0);
  CHECK(im2p_cycle_sequence_get_status(sequence, &status) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        memcmp(&status, &before, sizeof(status)) == 0);
  im2p_cycle_sequence_domain_snapshot_init(&domain);
  unsigned saw_rows = 0, saw_tags = 0, saw_valid_tag = 0;
  unsigned saw_replacement = 0, saw_accumulated = 0;
  uint32_t replacement_dst = UINT32_MAX;
  unsigned saw_banks = 0, saw_violation = 0;
  while (!status.has_report) {
    CHECK(status.cursor < 20000);
    CHECK(im2p_cycle_sequence_advance_until(sequence, status.cursor + 1) ==
          IM2P_CYCLE_SEQUENCE_INCOMPLETE);
    CHECK(im2p_cycle_sequence_get_status(sequence, &status) ==
          IM2P_CYCLE_SEQUENCE_OK);
    CHECK(im2p_cycle_sequence_get_domain_snapshot(sequence, &domain) ==
          IM2P_CYCLE_SEQUENCE_OK);
    CHECK(domain.cursor == status.cursor && domain.row_count <= 6 &&
          domain.tag_count <= 6 && domain.max_tag_occupancy >= domain.tag_count);
    im2p_cycle_sequence_tag_state_t tags;
    im2p_cycle_sequence_tag_state_init(&tags);
    CHECK(im2p_cycle_sequence_get_tag_state(sequence, &tags) ==
              IM2P_CYCLE_SEQUENCE_OK &&
          tags.cursor == domain.cursor && tags.queue_len == domain.tag_count &&
          (!tags.head_valid || tags.head_id == domain.tags[0].id));
    saw_violation |= domain.ready_violation_mask != 0;
    for (unsigned bank = 0; bank < 4; ++bank) {
      CHECK((domain.banks[bank].pipe_valid_mask & ~15u) == 0);
      saw_banks |= domain.banks[bank].pending || domain.banks[bank].queued ||
                   domain.banks[bank].pipe_valid_mask;
    }
    if (domain.row_count) {
      saw_rows = 1;
      CHECK(domain.rows[0].rows > 0 && domain.rows[0].id < 5);
    }
    if (domain.tag_count) {
      saw_tags = 1;
      CHECK(domain.tags[0].rows > 0 && domain.tags[0].id < 5 &&
            domain.tags[0].origin_generation == 1 &&
            domain.tags[0].origin_ordinal == 1 &&
            domain.tags[0].origin_work_id == 0 &&
            domain.tags[0].rob_valid ==
                (domain.tags[0].rob_id != UINT32_MAX));
      if (domain.tags[0].rob_valid) {
        saw_valid_tag = 1;
        CHECK(domain.tags[0].preload_dst != UINT32_MAX &&
              domain.tags[0].preload_output_rows > 0 &&
              domain.tags[0].preload_output_cols > 0);
      }
      for (unsigned i = 0; i < domain.tag_count; ++i) {
        const im2p_cycle_sequence_domain_tag_t *tag = &domain.tags[i];
        if (!tag->rob_valid)
          continue;
        CHECK(tag->preload_accumulate <= 1);
        if (!tag->preload_accumulate) {
          saw_replacement = 1;
          if (replacement_dst == UINT32_MAX)
            replacement_dst = tag->preload_dst;
        } else if (tag->preload_dst == replacement_dst)
          saw_accumulated = 1;
      }
    }
  }
  CHECK(saw_rows && saw_tags && saw_valid_tag && saw_replacement &&
        saw_accumulated && saw_banks && saw_violation &&
        domain.resource_ready &&
        domain.ready_violation_mask == 0 && domain.row_count == 0);
  const uint64_t first_ready = status.cursor;
  im2p_cycle_sequence_report_t report;
  im2p_cycle_sequence_report_init(&report);
  CHECK(im2p_cycle_sequence_pop_report(sequence, &report) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        report.logical_work_id == 0 &&
        report.resource_ready_cycle == first_ready);
  CHECK(im2p_cycle_sequence_advance_until(sequence, first_ready + 1) ==
        IM2P_CYCLE_SEQUENCE_INCOMPLETE);
  CHECK(im2p_cycle_sequence_get_status(sequence, &status) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        status.has_active && status.accepted_work_id == 1 &&
        status.accepted_cycle == first_ready);
  CHECK(im2p_cycle_sequence_advance_until(sequence, first_ready + 1000) ==
        IM2P_CYCLE_SEQUENCE_OK);
  CHECK(im2p_cycle_sequence_get_domain_snapshot(sequence, &domain) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        domain.max_tag_occupancy > domain.tag_count &&
        domain.max_tag_occupancy >= 2 && domain.cursor == first_ready + 1000);
  CHECK(im2p_cycle_sequence_get_status(sequence, &status) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        status.cursor == domain.cursor && status.has_report);
  im2p_cycle_sequence_report_init(&report);
  CHECK(im2p_cycle_sequence_pop_report(sequence, &report) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        report.logical_work_id == 1);
  printf("SEQUENCE_DOMAIN_SNAPSHOT_PASS first_ready=%llu cursor=%llu "
         "rows=%u tags=%u peak=%u violations=%u replacement=%u "
         "accumulated=%u dst=%u\n",
         (unsigned long long)first_ready, (unsigned long long)domain.cursor,
         domain.row_count, domain.tag_count, domain.max_tag_occupancy,
         domain.ready_violation_mask, saw_replacement, saw_accumulated,
         replacement_dst);
  CHECK(im2p_cycle_sequence_reset(sequence) == IM2P_CYCLE_SEQUENCE_OK);
  CHECK(im2p_cycle_sequence_get_domain_snapshot(sequence, &domain) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        domain.generation == 2 && !domain.max_tag_occupancy &&
        !domain.tag_count && !domain.row_count);
  im2p_cycle_sequence_destroy(sequence);
  return 0;
}

int main(void) {
  im2p_cycle_sequence_config_t config;
  im2p_cycle_sequence_descriptor_t descriptor;
  im2p_cycle_sequence_status_t status;
  im2p_cycle_sequence_report_t report;
  im2p_cycle_sequence_config_init(&config);
  im2p_cycle_sequence_descriptor_init(&descriptor);
  im2p_cycle_sequence_status_init(&status);
  im2p_cycle_sequence_report_init(&report);
  CHECK(config.abi_version == IM2P_CYCLE_SEQUENCE_ABI_VERSION);
  CHECK(config.struct_size == sizeof(config));
  CHECK(config.max_work_cycles == 10000000 &&
        config.max_session_cycles == 1000000000);
  CHECK(config.pending_capacity == 1 && config.report_capacity == 1);
  CHECK(config.max_work_ids == 65536);
  CHECK(descriptor.abi_version == IM2P_CYCLE_SEQUENCE_ABI_VERSION &&
        descriptor.struct_size == sizeof(descriptor));
  CHECK(descriptor.tile_i == 1 && descriptor.tile_j == 1 &&
        descriptor.tile_k == 1);
  CHECK(status.abi_version == IM2P_CYCLE_SEQUENCE_ABI_VERSION &&
        status.struct_size == sizeof(status));
  CHECK(report.abi_version == IM2P_CYCLE_SEQUENCE_ABI_VERSION &&
        report.struct_size == sizeof(report));

  config.hardware =
      (im2p_cycle_hardware_t){8, 8, 16, 32, 32, 4, 4096, 1024, 16, 64, 4, 2};
  im2p_cycle_sequence_t *first = NULL;
  im2p_cycle_sequence_t *second = NULL;
  CHECK(im2p_cycle_sequence_create(&config, &first) == IM2P_CYCLE_SEQUENCE_OK &&
        first != NULL);
  CHECK(im2p_cycle_sequence_create(&config, &second) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        second != NULL && second != first);
  config.hardware.dim = 99;
  CHECK(im2p_cycle_sequence_get_status(first, &status) ==
        IM2P_CYCLE_SEQUENCE_OK);
  CHECK(status.generation == 0 && status.initialized == 0 &&
        status.cursor == 0 && status.next_scratchpad_half == 0 &&
        status.next_accumulator_half == 0);
  im2p_cycle_sequence_report_t cold_report = report;
  CHECK(im2p_cycle_sequence_pop_report(first, &report) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        memcmp(&report, &cold_report, sizeof(report)) == 0);
  uint64_t cold_count = 0xa5a5a5a5a5a5a5a5ULL;
  CHECK(im2p_cycle_sequence_read_events(first, NULL, 0, &cold_count) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        cold_count == 0xa5a5a5a5a5a5a5a5ULL);
  CHECK(im2p_cycle_sequence_reset(first) == IM2P_CYCLE_SEQUENCE_OK);
  CHECK(im2p_cycle_sequence_get_status(first, &status) ==
        IM2P_CYCLE_SEQUENCE_OK);
  CHECK(status.generation == 1 && status.initialized == 1 &&
        status.cursor == 0 && status.next_scratchpad_half == 0 &&
        status.next_accumulator_half == 0 && !status.has_pending &&
        !status.has_active && !status.has_report && !status.faulted);
  im2p_cycle_sequence_error_t error;
  im2p_cycle_sequence_error_init(&error);
  CHECK(im2p_cycle_sequence_get_error(first, &error) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        error.code == IM2P_CYCLE_SEQUENCE_OK && error.last_good_cycle == 0 &&
        !error.has_active);
  error.abi_version++;
  im2p_cycle_sequence_error_t invalid_error = error;
  CHECK(im2p_cycle_sequence_get_error(first, &error) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        memcmp(&error, &invalid_error, sizeof(error)) == 0);
  error.abi_version--;
  error.struct_size--;
  invalid_error = error;
  CHECK(im2p_cycle_sequence_get_error(first, &error) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        memcmp(&error, &invalid_error, sizeof(error)) == 0);
  im2p_cycle_sequence_error_init(&error);
  invalid_error = error;
  CHECK(im2p_cycle_sequence_get_error(NULL, &error) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        memcmp(&error, &invalid_error, sizeof(error)) == 0);
  im2p_cycle_sequence_event_t untouched_event;
  memset(&untouched_event, 0xa5, sizeof(untouched_event));
  im2p_cycle_sequence_event_t saved_event = untouched_event;
  uint64_t untouched_count = UINT64_MAX;
  CHECK(im2p_cycle_sequence_read_events(first, &untouched_event, UINT64_MAX,
                                        &untouched_count) ==
            IM2P_CYCLE_SEQUENCE_OVERFLOW &&
        untouched_count == UINT64_MAX &&
        memcmp(&untouched_event, &saved_event, sizeof(saved_event)) == 0);
  CHECK(im2p_cycle_sequence_get_status(second, &status) ==
        IM2P_CYCLE_SEQUENCE_OK);
  CHECK(status.generation == 0 && status.initialized == 0);
  CHECK(im2p_cycle_sequence_reset(first) == IM2P_CYCLE_SEQUENCE_OK);
  CHECK(im2p_cycle_sequence_get_status(first, &status) ==
        IM2P_CYCLE_SEQUENCE_OK);
  CHECK(status.generation == 2);
  CHECK(im2p_cycle_sequence_reset(second) == IM2P_CYCLE_SEQUENCE_OK);
  CHECK(im2p_cycle_sequence_get_status(second, &status) ==
        IM2P_CYCLE_SEQUENCE_OK);
  CHECK(status.generation == 1);

  im2p_cycle_sequence_t *sentinel = first;
  config.hardware.dim = 16;
  config.abi_version++;
  CHECK(im2p_cycle_sequence_create(&config, &sentinel) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        sentinel == first);
  config.abi_version--;
  config.struct_size--;
  CHECK(im2p_cycle_sequence_create(&config, &sentinel) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        sentinel == first);
  config.struct_size++;
  CHECK(im2p_cycle_sequence_create(NULL, &sentinel) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        sentinel == first);
  CHECK(im2p_cycle_sequence_create(&config, NULL) ==
        IM2P_CYCLE_SEQUENCE_INVALID);
  config.max_session_cycles = 0;
  CHECK(im2p_cycle_sequence_create(&config, &sentinel) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        sentinel == first);
  config.max_session_cycles = 1000000000;
  config.max_work_cycles = 0;
  CHECK(im2p_cycle_sequence_create(&config, &sentinel) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        sentinel == first);
  config.max_work_cycles = 10000000;
  config.pending_capacity = 2;
  CHECK(im2p_cycle_sequence_create(&config, &sentinel) ==
            IM2P_CYCLE_SEQUENCE_UNSUPPORTED &&
        sentinel == first);
  config.pending_capacity = 1;
  config.hardware.dim = 99;
  CHECK(im2p_cycle_sequence_create(&config, &sentinel) ==
            IM2P_CYCLE_SEQUENCE_UNSUPPORTED &&
        sentinel == first);

  memset(&status, 0xa5, sizeof(status));
  im2p_cycle_sequence_status_t unchanged = status;
  CHECK(im2p_cycle_sequence_get_status(first, &status) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        memcmp(&status, &unchanged, sizeof(status)) == 0);
  im2p_cycle_sequence_status_init(&status);
  status.abi_version++;
  unchanged = status;
  CHECK(im2p_cycle_sequence_get_status(first, &status) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        memcmp(&status, &unchanged, sizeof(status)) == 0);
  im2p_cycle_sequence_status_init(&status);
  status.struct_size--;
  unchanged = status;
  CHECK(im2p_cycle_sequence_get_status(first, &status) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        memcmp(&status, &unchanged, sizeof(status)) == 0);
  im2p_cycle_sequence_status_init(&status);
  unchanged = status;
  CHECK(im2p_cycle_sequence_get_status(NULL, &status) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        memcmp(&status, &unchanged, sizeof(status)) == 0);
  CHECK(im2p_cycle_sequence_get_status(first, NULL) ==
        IM2P_CYCLE_SEQUENCE_INVALID);
  CHECK(im2p_cycle_sequence_reset(NULL) == IM2P_CYCLE_SEQUENCE_INVALID);
  im2p_cycle_result_t invalid_counters;
  memset(&invalid_counters, 0xa5, sizeof(invalid_counters));
  im2p_cycle_result_t saved_counters = invalid_counters;
  CHECK(im2p_cycle_sequence_get_cumulative_counters(first,
                                                    &invalid_counters) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        memcmp(&invalid_counters, &saved_counters,
               sizeof(invalid_counters)) == 0);
  uint64_t invalid_count = 0xa5a5a5a5a5a5a5a5ULL;
  CHECK(im2p_cycle_sequence_read_events(first, NULL, 1, &invalid_count) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        invalid_count == 0xa5a5a5a5a5a5a5a5ULL);

  config.hardware.dim = 16;
  im2p_cycle_sequence_t *stream = NULL;
  CHECK(im2p_cycle_sequence_create(&config, &stream) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        stream != NULL);
  im2p_cycle_sequence_descriptor_t a, b;
  im2p_cycle_sequence_descriptor_init(&a);
  im2p_cycle_sequence_descriptor_init(&b);
  a.logical_work_id = 0;
  a.m = a.n = 1;
  a.k = 22;
  a.tile_k = 2;
  a.submission = IM2P_CYCLE_TILE_SUBMISSIONS;
  im2p_compact_run_t runs[2] = {{0, 0x00000fff, 0, 12},
                                {1, 0x000003ff, 12, 10}};
  im2p_compact_runs_t view = {IM2P_COMPACT_RUNS_VERSION, sizeof(view), 42, 2,
                              runs};
  a.compact_runs = &view;
  b = a;
  b.logical_work_id = 1;
  b.compact_runs = NULL;
  b.k = 1;
  CHECK(im2p_cycle_sequence_offer(stream, &a, 0) ==
        IM2P_CYCLE_SEQUENCE_INVALID);
  CHECK(im2p_cycle_sequence_reset(stream) == IM2P_CYCLE_SEQUENCE_OK);
  im2p_cycle_sequence_status_init(&status);
  CHECK(im2p_cycle_sequence_get_status(stream, &status) ==
        IM2P_CYCLE_SEQUENCE_OK);
  im2p_cycle_sequence_status_t cold = status;
  im2p_compact_runs_t bad_view = view;
  bad_view.original_k = 0;
  a.compact_runs = &bad_view;
  CHECK(im2p_cycle_sequence_offer(stream, &a, 0) ==
        IM2P_CYCLE_SEQUENCE_INVALID);
  CHECK(im2p_cycle_sequence_get_status(stream, &status) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        memcmp(&status, &cold, sizeof(status)) == 0);
  a.compact_runs = &view;
  a.activation_stride_bytes = 1;
  CHECK(im2p_cycle_sequence_offer(stream, &a, 0) ==
        IM2P_CYCLE_SEQUENCE_INVALID);
  CHECK(im2p_cycle_sequence_get_status(stream, &status) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        memcmp(&status, &cold, sizeof(status)) == 0);
  a.activation_stride_bytes = 0;
  CHECK(im2p_cycle_sequence_offer(stream, &a, 0) == IM2P_CYCLE_SEQUENCE_OK);
  CHECK(im2p_cycle_sequence_reset(stream) == IM2P_CYCLE_SEQUENCE_WOULD_BLOCK);
  runs[1].original_block_id = 3;
  view.original_k = 106;
  CHECK(im2p_cycle_sequence_advance_until(stream, 1) ==
        IM2P_CYCLE_SEQUENCE_INCOMPLETE);
  im2p_cycle_sequence_status_init(&status);
  CHECK(im2p_cycle_sequence_get_status(stream, &status) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        status.cursor == 1 && status.has_active && status.has_accepted &&
        status.accepted_work_id == 0 && status.accepted_cycle == 0);
  CHECK(im2p_cycle_sequence_reset(stream) == IM2P_CYCLE_SEQUENCE_WOULD_BLOCK);
  CHECK(im2p_cycle_sequence_offer(stream, &b, 1) == IM2P_CYCLE_SEQUENCE_OK);
  CHECK(im2p_cycle_sequence_reset(stream) == IM2P_CYCLE_SEQUENCE_WOULD_BLOCK);
  im2p_cycle_sequence_status_t snapshot = status;
  CHECK(im2p_cycle_sequence_offer(stream, &b, 1) ==
        IM2P_CYCLE_SEQUENCE_INVALID);
  b.logical_work_id = 2;
  CHECK(im2p_cycle_sequence_offer(stream, &b, 1) ==
        IM2P_CYCLE_SEQUENCE_WOULD_BLOCK);
  CHECK(im2p_cycle_sequence_offer(stream, &b, 0) ==
        IM2P_CYCLE_SEQUENCE_INVALID);
  CHECK(im2p_cycle_sequence_advance_until(stream, 0) ==
        IM2P_CYCLE_SEQUENCE_INVALID);
  CHECK(im2p_cycle_sequence_get_status(stream, &status) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        status.cursor == snapshot.cursor && status.has_active &&
        status.has_pending && !status.faulted);
  CHECK(im2p_cycle_sequence_advance_until(stream, 1000) ==
        IM2P_CYCLE_SEQUENCE_WOULD_BLOCK);
  CHECK(im2p_cycle_sequence_get_status(stream, &status) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        status.has_report && status.has_pending && !status.has_active &&
        status.cursor > 1 &&
        status.stop_reason == IM2P_CYCLE_SEQUENCE_STOP_REPORT_BUFFER);
  const uint64_t q = status.cursor;
  memset(&report, 0xa5, sizeof(report));
  im2p_cycle_sequence_report_t invalid_report = report;
  CHECK(im2p_cycle_sequence_pop_report(stream, &report) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        memcmp(&report, &invalid_report, sizeof(report)) == 0);
  im2p_cycle_sequence_report_init(&report);
  CHECK(im2p_cycle_sequence_pop_report(stream, &report) ==
        IM2P_CYCLE_SEQUENCE_OK);
  CHECK(report.logical_work_id == 0 && report.accepted_cycle == 0 &&
        report.result_ready_cycle < report.final_scale_release_cycle &&
        report.final_scale_release_cycle < report.resource_ready_cycle &&
        report.resource_ready_cycle == q &&
        report.counters.fragment_count == 2 &&
        report.next_scratchpad_half == status.next_scratchpad_half &&
        report.next_accumulator_half == status.next_accumulator_half);
  const uint64_t a_result = report.result_ready_cycle;
  const uint64_t a_release = report.final_scale_release_cycle;
  CHECK(im2p_cycle_sequence_get_status(stream, &status) ==
        IM2P_CYCLE_SEQUENCE_OK);
  im2p_cycle_sequence_status_t at_q = status;
  CHECK(im2p_cycle_sequence_advance_until(stream, q) ==
        IM2P_CYCLE_SEQUENCE_INCOMPLETE);
  CHECK(im2p_cycle_sequence_get_status(stream, &status) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        memcmp(&status, &at_q, sizeof(status)) == 0);
  CHECK(im2p_cycle_sequence_advance_until(stream, q + 1) ==
        IM2P_CYCLE_SEQUENCE_INCOMPLETE);
  CHECK(im2p_cycle_sequence_get_status(stream, &status) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        status.accepted_work_id == 1 && status.offered_cycle == 1 &&
        status.accepted_cycle == q && status.cursor == q + 1);
  CHECK(im2p_cycle_sequence_advance_until(stream, 1000) ==
        IM2P_CYCLE_SEQUENCE_OK);
  CHECK(im2p_cycle_sequence_reset(stream) == IM2P_CYCLE_SEQUENCE_WOULD_BLOCK);
  im2p_cycle_sequence_report_init(&report);
  CHECK(im2p_cycle_sequence_pop_report(stream, &report) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        report.logical_work_id == 1 && report.accepted_cycle == q &&
        report.result_ready_cycle < report.resource_ready_cycle);
  CHECK(im2p_cycle_sequence_reset(stream) == IM2P_CYCLE_SEQUENCE_OK);
  CHECK(im2p_cycle_sequence_get_status(stream, &status) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        status.generation == 2 && status.cursor == 0 &&
        status.next_scratchpad_half == 0 && status.next_accumulator_half == 0);
  printf("SEQUENCE_OFFER_EDGE_PASS a=0 b=%llu result=%llu release=%llu "
         "resource=%llu copied_runs=1\n",
         (unsigned long long)q, (unsigned long long)a_result,
         (unsigned long long)a_release, (unsigned long long)q);
  const unsigned idle_cycles[] = {0, 1, 4, 5, 6, 100};
  for (unsigned trial = 0; trial < sizeof(idle_cycles) / sizeof(*idle_cycles);
       ++trial) {
    const unsigned idle = idle_cycles[trial];
    a.logical_work_id = 10 + 2 * trial;
    b.logical_work_id = a.logical_work_id + 1;
    CHECK(im2p_cycle_sequence_advance_until(stream, idle) ==
          IM2P_CYCLE_SEQUENCE_OK);
    CHECK(im2p_cycle_sequence_get_status(stream, &status) ==
              IM2P_CYCLE_SEQUENCE_OK &&
          status.cursor == idle && !status.has_active && !status.has_pending);
    CHECK(im2p_cycle_sequence_offer(stream, &a, idle) ==
          IM2P_CYCLE_SEQUENCE_OK);
    for (;;) {
      CHECK(im2p_cycle_sequence_get_status(stream, &status) ==
            IM2P_CYCLE_SEQUENCE_OK);
      if (status.has_report)
        break;
      CHECK(status.cursor < idle + 1000);
      const int advanced =
          im2p_cycle_sequence_advance_until(stream, status.cursor + 1);
      CHECK(advanced == IM2P_CYCLE_SEQUENCE_OK ||
            advanced == IM2P_CYCLE_SEQUENCE_INCOMPLETE);
    }
    const uint64_t a_resource = status.cursor;
    im2p_cycle_sequence_report_init(&report);
    CHECK(im2p_cycle_sequence_pop_report(stream, &report) ==
              IM2P_CYCLE_SEQUENCE_OK &&
          report.logical_work_id == a.logical_work_id &&
          report.accepted_cycle == idle &&
          report.resource_ready_cycle == a_resource);
    CHECK(im2p_cycle_sequence_advance_until(stream, a_resource + idle) ==
          IM2P_CYCLE_SEQUENCE_OK);
    CHECK(im2p_cycle_sequence_get_status(stream, &status) ==
              IM2P_CYCLE_SEQUENCE_OK &&
          status.cursor == a_resource + idle && !status.has_active &&
          !status.has_pending && !status.has_report &&
          status.accepted_work_id == a.logical_work_id);
    CHECK(im2p_cycle_sequence_offer(stream, &b, status.cursor) ==
          IM2P_CYCLE_SEQUENCE_OK);
    CHECK(im2p_cycle_sequence_advance_until(stream, status.cursor + 1) ==
          IM2P_CYCLE_SEQUENCE_INCOMPLETE);
    CHECK(im2p_cycle_sequence_get_status(stream, &status) ==
              IM2P_CYCLE_SEQUENCE_OK &&
          status.accepted_work_id == b.logical_work_id &&
          status.offered_cycle == a_resource + idle &&
          status.accepted_cycle == a_resource + idle);
    CHECK(im2p_cycle_sequence_advance_until(stream, a_resource + idle + 1000) ==
          IM2P_CYCLE_SEQUENCE_OK);
    im2p_cycle_sequence_report_init(&report);
    CHECK(im2p_cycle_sequence_pop_report(stream, &report) ==
              IM2P_CYCLE_SEQUENCE_OK &&
          report.logical_work_id == b.logical_work_id &&
          report.accepted_cycle == a_resource + idle &&
          report.resource_ready_cycle > report.accepted_cycle);
    CHECK(im2p_cycle_sequence_pop_report(stream, &report) ==
          IM2P_CYCLE_SEQUENCE_WOULD_BLOCK);
    printf("SEQUENCE_IDLE_ABI_PASS cold_idle=%u resource_idle=%u "
           "a_resource=%llu b_offer=%llu b_accepted=%llu b_resource=%llu\n",
           idle, idle, (unsigned long long)a_resource,
           (unsigned long long)(a_resource + idle),
           (unsigned long long)report.accepted_cycle,
           (unsigned long long)report.resource_ready_cycle);
    CHECK(im2p_cycle_sequence_reset(stream) == IM2P_CYCLE_SEQUENCE_OK);
  }
  config.max_work_cycles = 1;
  im2p_cycle_sequence_t *fault = NULL;
  CHECK(im2p_cycle_sequence_create(&config, &fault) == IM2P_CYCLE_SEQUENCE_OK &&
        im2p_cycle_sequence_reset(fault) == IM2P_CYCLE_SEQUENCE_OK);
  view.original_k = 42;
  runs[1].original_block_id = 1;
  CHECK(im2p_cycle_sequence_offer(fault, &a, 0) == IM2P_CYCLE_SEQUENCE_OK);
  CHECK(im2p_cycle_sequence_advance_until(fault, 2) ==
        IM2P_CYCLE_SEQUENCE_LIMIT);
  CHECK(im2p_cycle_sequence_get_status(fault, &status) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        status.faulted && status.cursor == 1 && status.has_active &&
        !status.has_report &&
        status.stop_reason == IM2P_CYCLE_SEQUENCE_STOP_WORK_BUDGET);
  im2p_cycle_sequence_error_init(&error);
  CHECK(im2p_cycle_sequence_get_error(fault, &error) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        error.code == IM2P_CYCLE_SEQUENCE_LIMIT &&
        error.stop_reason == IM2P_CYCLE_SEQUENCE_STOP_WORK_BUDGET &&
        error.last_good_cycle == status.cursor && error.has_active &&
        error.active_work_id == a.logical_work_id &&
        error.partial_counters.logical_work_count == 1 &&
        error.message[0] != '\0');
  memset(&report, 0xa5, sizeof(report));
  invalid_report = report;
  CHECK(im2p_cycle_sequence_pop_report(fault, &report) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        memcmp(&report, &invalid_report, sizeof(report)) == 0);
  im2p_cycle_sequence_report_init(&report);
  invalid_report = report;
  CHECK(im2p_cycle_sequence_pop_report(fault, &report) ==
            IM2P_CYCLE_SEQUENCE_FAULTED &&
        memcmp(&report, &invalid_report, sizeof(report)) == 0);
  CHECK(im2p_cycle_sequence_offer(fault, &b, 1) == IM2P_CYCLE_SEQUENCE_FAULTED);
  CHECK(im2p_cycle_sequence_reset(fault) == IM2P_CYCLE_SEQUENCE_OK);
  CHECK(im2p_cycle_sequence_get_status(fault, &status) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        status.generation == 2 && status.last_discarded_generation == 1 &&
        !status.faulted && !status.has_active);
  im2p_cycle_sequence_error_init(&error);
  CHECK(im2p_cycle_sequence_get_error(fault, &error) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        error.code == IM2P_CYCLE_SEQUENCE_OK && !error.has_active);
  im2p_cycle_sequence_destroy(fault);
  config.max_work_cycles = 10000000;
  config.max_session_cycles = 1;
  fault = NULL;
  CHECK(im2p_cycle_sequence_create(&config, &fault) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        im2p_cycle_sequence_reset(fault) == IM2P_CYCLE_SEQUENCE_OK);
  CHECK(im2p_cycle_sequence_offer(fault, &a, 0) == IM2P_CYCLE_SEQUENCE_OK);
  CHECK(im2p_cycle_sequence_advance_until(fault, 2) ==
        IM2P_CYCLE_SEQUENCE_LIMIT);
  im2p_cycle_sequence_status_init(&status);
  CHECK(im2p_cycle_sequence_get_status(fault, &status) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        status.faulted && status.cursor == 1 && status.has_active &&
        !status.has_report &&
        status.stop_reason == IM2P_CYCLE_SEQUENCE_STOP_SESSION_BUDGET);
  im2p_cycle_sequence_error_init(&error);
  CHECK(im2p_cycle_sequence_get_error(fault, &error) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        error.code == IM2P_CYCLE_SEQUENCE_LIMIT &&
        error.last_good_cycle == 1 && error.has_active &&
        error.active_work_id == a.logical_work_id);
  im2p_cycle_sequence_report_init(&report);
  CHECK(im2p_cycle_sequence_pop_report(fault, &report) ==
        IM2P_CYCLE_SEQUENCE_FAULTED);
  CHECK(im2p_cycle_sequence_offer(fault, &b, 1) == IM2P_CYCLE_SEQUENCE_FAULTED);
  CHECK(im2p_cycle_sequence_reset(fault) == IM2P_CYCLE_SEQUENCE_OK);
  CHECK(im2p_cycle_sequence_get_status(fault, &status) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        !status.faulted && status.generation == 2 &&
        status.last_discarded_generation == 1);
  printf("SEQUENCE_SESSION_FAULT_PASS last_good=%llu active=%llu no_report=1 "
         "recovered_generation=%llu\n",
         (unsigned long long)error.last_good_cycle,
         (unsigned long long)error.active_work_id,
         (unsigned long long)status.generation);
  im2p_cycle_sequence_destroy(fault);
  im2p_cycle_sequence_destroy(stream);
  im2p_cycle_sequence_destroy(first);
  im2p_cycle_sequence_destroy(second);
  im2p_cycle_sequence_destroy(NULL);
  im2p_cycle_sequence_report_t on[2], off[2];
  im2p_cycle_result_t on_total = {0}, off_total = {0};
  uint64_t on_drained = 0, off_drained = 0;
  CHECK(run_event_case(1, on, &on_total, &on_drained) == 0);
  CHECK(run_event_case(0, off, &off_total, &off_drained) == 0);
  CHECK(on_drained == on_total.event_count && off_drained == 0 &&
        memcmp(&on_total, &off_total, sizeof(on_total)) == 0);
  for (unsigned i = 0; i < 2; ++i)
    CHECK(memcmp(&on[i], &off[i], sizeof(on[i])) == 0);
  printf("SEQUENCE_EVENT_ON_OFF_PASS a_resource=%llu b_resource=%llu "
         "events=%llu off_payload=%llu\n",
         (unsigned long long)on[0].resource_ready_cycle,
         (unsigned long long)on[1].resource_ready_cycle,
         (unsigned long long)on_drained,
         (unsigned long long)off_drained);
  CHECK(test_tiny_event_buffer() == 0);
  CHECK(test_result_ready_releasing() == 0);
  CHECK(test_domain_snapshot() == 0);
  im2p_cycle_sequence_config_t bounded_config;
  im2p_cycle_sequence_config_init(&bounded_config);
  bounded_config.hardware =
      (im2p_cycle_hardware_t){8, 8, 16, 32, 32, 4, 4096, 1024, 16, 64, 4, 2};
  im2p_cycle_sequence_t *bounded = NULL;
  bounded_config.max_work_ids = 0;
  CHECK(im2p_cycle_sequence_create(&bounded_config, &bounded) ==
            IM2P_CYCLE_SEQUENCE_INVALID &&
        bounded == NULL);
  bounded_config.max_work_ids = 1;
  CHECK(im2p_cycle_sequence_create(&bounded_config, &bounded) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        im2p_cycle_sequence_reset(bounded) == IM2P_CYCLE_SEQUENCE_OK);
  im2p_cycle_sequence_descriptor_t small;
  im2p_cycle_sequence_descriptor_init(&small);
  small.logical_work_id = 700;
  small.m = small.n = small.k = 1;
  small.submission = IM2P_CYCLE_TILE_SUBMISSIONS;
  CHECK(im2p_cycle_sequence_offer(bounded, &small, 0) ==
        IM2P_CYCLE_SEQUENCE_OK);
  CHECK(im2p_cycle_sequence_advance_until(bounded, 1000) ==
        IM2P_CYCLE_SEQUENCE_OK);
  im2p_cycle_sequence_report_init(&report);
  CHECK(im2p_cycle_sequence_pop_report(bounded, &report) ==
        IM2P_CYCLE_SEQUENCE_OK);
  im2p_cycle_sequence_status_init(&status);
  CHECK(im2p_cycle_sequence_get_status(bounded, &status) ==
        IM2P_CYCLE_SEQUENCE_OK);
  im2p_cycle_sequence_status_t at_cap = status;
  CHECK(im2p_cycle_sequence_offer(bounded, &small, status.cursor) ==
        IM2P_CYCLE_SEQUENCE_INVALID);
  small.logical_work_id = 701;
  CHECK(im2p_cycle_sequence_offer(bounded, &small, status.cursor) ==
        IM2P_CYCLE_SEQUENCE_LIMIT);
  CHECK(im2p_cycle_sequence_get_status(bounded, &status) ==
            IM2P_CYCLE_SEQUENCE_OK &&
        memcmp(&status, &at_cap, sizeof(status)) == 0);
  im2p_cycle_sequence_destroy(bounded);
  puts("SEQUENCE_WORK_ID_BOUND_PASS capacity=1 duplicate=INVALID new=LIMIT");
  puts("SEQUENCE_C_ABI_PASS create=2 independent=1 generations=2/1 "
       "malformed_unchanged=1");
  return 0;
}
