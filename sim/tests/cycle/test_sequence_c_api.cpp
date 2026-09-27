#include "../../include/im2p_cycle_sequence.h"
#include "../../cycle/control_engine.hpp"
#undef NDEBUG
#include <cassert>
#include <cstddef>
#include <cstdlib>
#include <new>
#include <cstring>
#include <cstdio>

static bool fail_next_allocation = false;

void *operator new(std::size_t size) {
  if (fail_next_allocation) {
    fail_next_allocation = false;
    throw std::bad_alloc();
  }
  if (void *memory = std::malloc(size ? size : 1))
    return memory;
  throw std::bad_alloc();
}

void operator delete(void *memory) noexcept { std::free(memory); }
void operator delete(void *memory, std::size_t) noexcept { std::free(memory); }

static_assert(offsetof(im2p_cycle_sequence_report_t, abi_version) == 0);
static_assert(offsetof(im2p_cycle_sequence_error_t, abi_version) == 0);
static_assert(sizeof(im2p_cycle_sequence_config_t) == 136);
static_assert(offsetof(im2p_cycle_sequence_config_t, max_work_ids) == 128);
static_assert(sizeof(im2p_cycle_sequence_descriptor_t) == 112);
static_assert(sizeof(im2p_cycle_sequence_status_t) == 112);
static_assert(sizeof(im2p_cycle_sequence_tag_state_t) == 64);
static_assert(sizeof(im2p_cycle_sequence_row_pressure_t) == 32);
static_assert(IM2P_CYCLE_SEQUENCE_MESH_STATE_ABI_VERSION == 1u);
static_assert(sizeof(im2p_cycle_sequence_mesh_state_t) == 120);
static_assert(offsetof(im2p_cycle_sequence_mesh_state_t,
                       request_owner_generation) == 56);
static_assert(offsetof(im2p_cycle_sequence_mesh_state_t, control_valid) == 80);
static_assert(offsetof(im2p_cycle_sequence_mesh_state_t, stall_reason_mask) ==
              116);
static_assert(IM2P_CYCLE_SEQUENCE_DOMAIN_ABI_VERSION == 2u);
static_assert(sizeof(im2p_cycle_sequence_domain_snapshot_t) == 528);
static_assert(offsetof(im2p_cycle_sequence_domain_snapshot_t, tags) == 96);
static_assert(offsetof(im2p_cycle_sequence_domain_snapshot_t, banks) == 480);
static_assert(sizeof(im2p_cycle_sequence_report_t) == 200);
static_assert(sizeof(im2p_cycle_sequence_event_t) == 112);
static_assert(sizeof(im2p_cycle_sequence_error_t) == 296);
static_assert(offsetof(im2p_cycle_sequence_error_t, partial_counters) == 48);
static_assert(offsetof(im2p_cycle_sequence_error_t, message) == 168);
static_assert(IM2P_CYCLE_SEQUENCE_OK == 0);
static_assert(IM2P_CYCLE_SEQUENCE_INCOMPLETE == 1);
static_assert(IM2P_CYCLE_SEQUENCE_WOULD_BLOCK == 2);
static_assert(IM2P_CYCLE_SEQUENCE_INVALID == -1);
static_assert(IM2P_CYCLE_SEQUENCE_UNSUPPORTED == -2);
static_assert(IM2P_CYCLE_SEQUENCE_OVERFLOW == -3);
static_assert(IM2P_CYCLE_SEQUENCE_LIMIT == -4);
static_assert(IM2P_CYCLE_SEQUENCE_INTERNAL == -5);
static_assert(IM2P_CYCLE_SEQUENCE_FAULTED == -6);

int main() {
  im2p_cycle_sequence_config_t config;
  im2p_cycle_sequence_config_init(&config);
  im2p_cycle_sequence_descriptor_t descriptor;
  im2p_cycle_sequence_descriptor_init(&descriptor);
  im2p_cycle_sequence_report_t report;
  im2p_cycle_sequence_report_init(&report);
  assert(config.struct_size == sizeof(config) &&
         descriptor.struct_size == sizeof(descriptor) &&
         report.struct_size == sizeof(report));
  config.hardware = {8, 8, 16, 32, 32, 4, 4096, 1024, 16, 64, 4, 2};
  im2p_cycle_model_config_t model;
  im2p_cycle_model_config_init(&model);
  model.hardware = config.hardware;
  im2p_cycle_request_t request;
  im2p_cycle_request_init(&request);
  request.m = request.n = 1;
  request.k = 22;
  request.tile_k = 2;
  request.submission = IM2P_CYCLE_TILE_SUBMISSIONS;
  im2p::cycle::detail::Engine engine(model, true);
  im2p::cycle::detail::WorkContext work(model, request);
  fail_next_allocation = true;
  bool lowering_rejected = false;
  try {
    engine.accept(std::move(work), 0, 0, 0);
  } catch (const std::bad_alloc &) {
    lowering_rejected = true;
  }
  fail_next_allocation = false;
  assert(lowering_rejected && engine.cycle() == 0 && !engine.current_work() &&
         engine.cumulative_counters().logical_work_count == 0);
  engine.accept(std::move(work), 0, 0, 0);
  assert(engine.current_work() &&
         engine.cumulative_counters().logical_work_count == 1);
  im2p_cycle_sequence_t *sequence = nullptr;
  assert(im2p_cycle_sequence_create(&config, &sequence) ==
         IM2P_CYCLE_SEQUENCE_OK);
  assert(im2p_cycle_sequence_reset(sequence) == IM2P_CYCLE_SEQUENCE_OK);
  im2p_cycle_sequence_mesh_state_t mesh;
  im2p_cycle_sequence_mesh_state_init(&mesh);
  assert(im2p_cycle_sequence_get_mesh_state(sequence, &mesh) ==
             IM2P_CYCLE_SEQUENCE_OK &&
         mesh.generation == 1 && mesh.cursor == 0 && !mesh.request_valid &&
         !mesh.control_valid && !mesh.mesh_request_valid &&
         mesh.mesh_request_ready && !mesh.mesh_request_fire &&
         mesh.side_ready_mask == 7 && !mesh.stall_reason_mask);
  const auto cold_mesh = mesh;
  assert(im2p_cycle_sequence_get_mesh_state(sequence, &mesh) ==
             IM2P_CYCLE_SEQUENCE_OK &&
         std::memcmp(&mesh, &cold_mesh, sizeof(mesh)) == 0);
  ++mesh.abi_version;
  const auto malformed_mesh_version = mesh;
  assert(im2p_cycle_sequence_get_mesh_state(sequence, &mesh) ==
             IM2P_CYCLE_SEQUENCE_INVALID &&
         std::memcmp(&mesh, &malformed_mesh_version, sizeof(mesh)) == 0);
  mesh = cold_mesh;
  --mesh.struct_size;
  const auto malformed_mesh_size = mesh;
  assert(im2p_cycle_sequence_get_mesh_state(sequence, &mesh) ==
             IM2P_CYCLE_SEQUENCE_INVALID &&
         std::memcmp(&mesh, &malformed_mesh_size, sizeof(mesh)) == 0);
  mesh = cold_mesh;
  im2p_cycle_sequence_row_pressure_t pressure;
  im2p_cycle_sequence_row_pressure_init(&pressure);
  assert(im2p_cycle_sequence_get_row_pressure(sequence, &pressure) ==
             IM2P_CYCLE_SEQUENCE_OK &&
         pressure.generation == 1 && pressure.cursor == 0 &&
         pressure.row_count == 0 && pressure.max_row_occupancy == 0);
  ++pressure.abi_version;
  const auto malformed_pressure_version = pressure;
  assert(im2p_cycle_sequence_get_row_pressure(sequence, &pressure) ==
             IM2P_CYCLE_SEQUENCE_INVALID &&
         std::memcmp(&pressure, &malformed_pressure_version,
                     sizeof(pressure)) == 0);
  im2p_cycle_sequence_row_pressure_init(&pressure);
  --pressure.struct_size;
  const auto malformed_pressure_size = pressure;
  assert(im2p_cycle_sequence_get_row_pressure(sequence, &pressure) ==
             IM2P_CYCLE_SEQUENCE_INVALID &&
         std::memcmp(&pressure, &malformed_pressure_size,
                     sizeof(pressure)) == 0);
  im2p_cycle_sequence_tag_state_t tags;
  im2p_cycle_sequence_tag_state_init(&tags);
  assert(im2p_cycle_sequence_get_tag_state(sequence, &tags) ==
             IM2P_CYCLE_SEQUENCE_OK &&
         tags.generation == 1 && tags.cursor == 0 && tags.queue_len == 0 &&
         tags.enqueues == 0 && tags.dequeues == 0 &&
         tags.full_backpressure_cycles == UINT64_MAX &&
         !tags.backpressure_mapped);
  const auto cold_tags = tags;
  ++tags.abi_version;
  const auto malformed_tags = tags;
  assert(im2p_cycle_sequence_get_tag_state(sequence, &tags) ==
             IM2P_CYCLE_SEQUENCE_INVALID &&
         std::memcmp(&tags, &malformed_tags, sizeof(tags)) == 0);
  tags = cold_tags;
  --tags.struct_size;
  const auto malformed_size = tags;
  assert(im2p_cycle_sequence_get_tag_state(sequence, &tags) ==
             IM2P_CYCLE_SEQUENCE_INVALID &&
         std::memcmp(&tags, &malformed_size, sizeof(tags)) == 0);
  tags = cold_tags;
  descriptor.logical_work_id = 73;
  descriptor.m = descriptor.n = 1;
  descriptor.k = 22;
  descriptor.tile_k = 2;
  descriptor.submission = IM2P_CYCLE_TILE_SUBMISSIONS;
  assert(im2p_cycle_sequence_offer(sequence, &descriptor, 0) ==
         IM2P_CYCLE_SEQUENCE_OK);
  im2p_cycle_sequence_status_t before, after;
  im2p_cycle_sequence_status_init(&before);
  im2p_cycle_sequence_status_init(&after);
  assert(im2p_cycle_sequence_get_status(sequence, &before) ==
         IM2P_CYCLE_SEQUENCE_OK);
  fail_next_allocation = true;
  const int rejected = im2p_cycle_sequence_advance_until(sequence, 1);
  fail_next_allocation = false;
  assert(rejected == IM2P_CYCLE_SEQUENCE_LIMIT);
  assert(im2p_cycle_sequence_get_status(sequence, &after) ==
         IM2P_CYCLE_SEQUENCE_OK);
  assert(std::memcmp(&before, &after, sizeof(before)) == 0);
  assert(im2p_cycle_sequence_get_tag_state(sequence, &tags) ==
             IM2P_CYCLE_SEQUENCE_OK &&
         std::memcmp(&tags, &cold_tags, sizeof(tags)) == 0);
  assert(im2p_cycle_sequence_get_mesh_state(sequence, &mesh) ==
             IM2P_CYCLE_SEQUENCE_OK &&
         std::memcmp(&mesh, &cold_mesh, sizeof(mesh)) == 0);
  im2p_cycle_sequence_report_init(&report);
  const auto empty_report = report;
  assert(im2p_cycle_sequence_pop_report(sequence, &report) ==
         IM2P_CYCLE_SEQUENCE_WOULD_BLOCK);
  assert(std::memcmp(&report, &empty_report, sizeof(report)) == 0);
  bool saw_control = false, saw_request = false;
  bool saw_attempt_without_ready = false, saw_fire = false;
  for (unsigned step = 0; step < 1000; ++step) {
    im2p_cycle_sequence_status_init(&after);
    assert(im2p_cycle_sequence_get_status(sequence, &after) ==
           IM2P_CYCLE_SEQUENCE_OK);
    if (after.has_report)
      break;
    const int advanced =
        im2p_cycle_sequence_advance_until(sequence, after.cursor + 1);
    assert(advanced == IM2P_CYCLE_SEQUENCE_INCOMPLETE ||
           advanced == IM2P_CYCLE_SEQUENCE_OK);
    im2p_cycle_sequence_mesh_state_init(&mesh);
    assert(im2p_cycle_sequence_get_mesh_state(sequence, &mesh) ==
               IM2P_CYCLE_SEQUENCE_OK &&
           mesh.cursor == after.cursor + 1 && mesh.generation == 1);
    saw_control |= mesh.control_valid;
    saw_request |= mesh.request_valid;
    saw_attempt_without_ready |=
        mesh.mesh_request_valid && !mesh.mesh_request_ready &&
        !mesh.mesh_request_fire;
    saw_fire |= mesh.mesh_request_fire;
    assert(!mesh.mesh_request_fire ||
           (mesh.mesh_request_valid && mesh.mesh_request_ready));
    assert(!(mesh.stall_reason_mask &
             IM2P_CYCLE_SEQUENCE_MESH_STALL_RESIDENT_NOT_LAST) ||
           (mesh.request_valid &&
            mesh.request_counter + 1 != mesh.request_rows));
  }
  assert(saw_control && saw_request && saw_attempt_without_ready && saw_fire);
  im2p_cycle_sequence_status_init(&after);
  assert(im2p_cycle_sequence_get_status(sequence, &after) ==
             IM2P_CYCLE_SEQUENCE_OK &&
         after.has_report);
  im2p_cycle_sequence_mesh_state_init(&mesh);
  assert(im2p_cycle_sequence_get_mesh_state(sequence, &mesh) ==
             IM2P_CYCLE_SEQUENCE_OK &&
         mesh.cursor == after.cursor &&
         mesh.next_scratchpad_half == after.next_scratchpad_half &&
         mesh.next_accumulator_half == after.next_accumulator_half &&
         !mesh.request_valid && !mesh.control_valid &&
         !mesh.mesh_request_valid && mesh.mesh_request_ready &&
         !mesh.mesh_request_fire && !mesh.stall_reason_mask);
  assert(im2p_cycle_sequence_pop_report(sequence, &report) ==
             IM2P_CYCLE_SEQUENCE_OK &&
         report.logical_work_id == descriptor.logical_work_id);
  assert(im2p_cycle_sequence_get_tag_state(sequence, &tags) ==
         IM2P_CYCLE_SEQUENCE_OK);
  assert(tags.cursor >= report.resource_ready_cycle);
  assert(tags.enqueues > 0);
  assert(tags.enqueues >= tags.dequeues);
  assert(tags.enqueues - tags.dequeues == tags.queue_len);
  assert(tags.queue_len <= 6);
  assert(tags.head_valid == (tags.queue_len != 0));
  im2p_cycle_sequence_row_pressure_init(&pressure);
  assert(im2p_cycle_sequence_get_row_pressure(sequence, &pressure) ==
         IM2P_CYCLE_SEQUENCE_OK);
  im2p_cycle_sequence_domain_snapshot_t domain;
  im2p_cycle_sequence_domain_snapshot_init(&domain);
  assert(im2p_cycle_sequence_get_domain_snapshot(sequence, &domain) ==
             IM2P_CYCLE_SEQUENCE_OK &&
         pressure.row_count == domain.row_count &&
         pressure.max_row_occupancy > pressure.row_count &&
         pressure.max_row_occupancy <= IM2P_CYCLE_SEQUENCE_DOMAIN_CAPACITY);
  const auto peak = pressure.max_row_occupancy;
  assert(im2p_cycle_sequence_advance_until(sequence, pressure.cursor + 100) ==
         IM2P_CYCLE_SEQUENCE_OK);
  assert(im2p_cycle_sequence_get_row_pressure(sequence, &pressure) ==
             IM2P_CYCLE_SEQUENCE_OK &&
         pressure.max_row_occupancy == peak);
  assert(im2p_cycle_sequence_reset(sequence) == IM2P_CYCLE_SEQUENCE_OK);
  assert(im2p_cycle_sequence_get_row_pressure(sequence, &pressure) ==
             IM2P_CYCLE_SEQUENCE_OK &&
         pressure.generation == 2 && pressure.cursor == 0 &&
         pressure.row_count == 0 && pressure.max_row_occupancy == 0);
  std::printf("ROW_PRESSURE_NATIVE_PASS peak=%u release_rows=%u "
              "generation=%llu\n",
              peak, domain.row_count,
              static_cast<unsigned long long>(pressure.generation));
  std::printf("SEQUENCE_PRECOMMIT_ALLOCATION_PASS cursor=%llu pending=%u "
              "completed=%llu\n",
              static_cast<unsigned long long>(before.cursor),
              before.has_pending,
              static_cast<unsigned long long>(report.logical_work_id));
  im2p_cycle_sequence_destroy(sequence);
  return 0;
}
