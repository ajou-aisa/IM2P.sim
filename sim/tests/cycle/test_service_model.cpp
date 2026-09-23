#include "../../include/im2p_cycle_service.h"
#include <array>
#undef NDEBUG
#include <cassert>
#include <cstring>
#include <memory>

int main() {
  im2p_cycle_model_config_t config{};
  im2p_cycle_model_config_init(&config);
  config.hardware = {8, 8, 16, 32, 32, 4, 4096, 1024, 16, 64, 4, 2};
  std::unique_ptr<im2p_cycle_model_t, decltype(&im2p_cycle_model_destroy)> model(
      im2p_cycle_model_create(&config), im2p_cycle_model_destroy);
  assert(model);
  im2p_cycle_request_t request{};
  im2p_cycle_request_init(&request);
  request.m = 2;
  request.n = 3;
  request.k = 64;
  request.tile_k = 4;
  request.accepted_cycle = 4;
  request.record_events = 1;
  request.submission = IM2P_CYCLE_TILE_SUBMISSIONS;
  im2p_cycle_result_t original{}, drained{};
  assert(im2p_cycle_estimate(model.get(), &request, &original) == IM2P_CYCLE_OK);
  const auto original_count = im2p_cycle_model_event_count(model.get());
  std::array<im2p_cycle_event_t, 10000> original_events{};
  assert(original_count < original_events.size());
  for (std::uint64_t i = 0; i < original_count; ++i)
    assert(im2p_cycle_model_event(model.get(), i, &original_events[i]) == IM2P_CYCLE_OK);
  im2p_cycle_service_result_t service{};
  assert(im2p_cycle_estimate_service(model.get(), &request, nullptr, &drained,
                                     &service) == IM2P_CYCLE_OK);
  assert(service.abi_version == IM2P_CYCLE_SERVICE_ABI_VERSION);
  assert(service.struct_size == sizeof(service));
  assert(service.result_ready_cycle == 414);
  assert(service.final_scale_release_cycle == 446);
  assert(service.resource_ready_cycle == 448);
  assert(service.next_scratchpad_half == 1 && service.next_accumulator_half == 1);
  assert(drained.done_cycle == original.done_cycle && drained.total_cycles == original.total_cycles);
  assert(drained.event_count > original_count);
  for (std::uint64_t i = 0; i < original_count; ++i) {
    im2p_cycle_event_t event{};
    assert(im2p_cycle_model_event(model.get(), i, &event) == IM2P_CYCLE_OK);
    assert(std::memcmp(&event, &original_events[i], sizeof(event)) == 0);
  }
  im2p_cycle_result_t repeated{};
  assert(im2p_cycle_estimate(model.get(), &request, &repeated) == IM2P_CYCLE_OK);
  assert(std::memcmp(&original, &repeated, sizeof(original)) == 0);
  const auto before = service;
  const auto before_result = drained;
  request.tile_k = 0;
  assert(im2p_cycle_estimate_service(model.get(), &request, nullptr, &drained,
                                     &service) == IM2P_CYCLE_INVALID);
  assert(std::memcmp(&before, &service, sizeof(service)) == 0);
  assert(std::memcmp(&before_result, &drained, sizeof(drained)) == 0);
  assert(im2p_cycle_estimate_service(model.get(), &request, nullptr, &drained,
                                     nullptr) == IM2P_CYCLE_INVALID);
  assert(std::memcmp(&before_result, &drained, sizeof(drained)) == 0);
  request.tile_k = 4;
  config.max_cycles = original.total_cycles + 2;
  model.reset(im2p_cycle_model_create(&config));
  assert(model);
  assert(im2p_cycle_estimate(model.get(), &request, &repeated) == IM2P_CYCLE_OK);
  assert(im2p_cycle_estimate_service(model.get(), &request, nullptr, &drained,
                                     &service) == IM2P_CYCLE_LIMIT);
  assert(std::memcmp(&before, &service, sizeof(service)) == 0);
  assert(std::memcmp(&before_result, &drained, sizeof(drained)) == 0);
}
