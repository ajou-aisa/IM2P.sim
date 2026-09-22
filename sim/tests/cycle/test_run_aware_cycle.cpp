#include "../../cycle/cycle_model.hpp"
#include <array>
#undef NDEBUG
#include <cassert>
#include <cstring>
#include <iostream>
#include <memory>

int main() {
  im2p_cycle_model_config_t config;
  im2p_cycle_model_config_init(&config);
  config.hardware = {8, 8, 16, 32, 32, 4, 4096, 1024, 16, 64, 4, 2};
  std::unique_ptr<im2p_cycle_model_t, decltype(&im2p_cycle_model_destroy)> model(
      im2p_cycle_model_create(&config), im2p_cycle_model_destroy);
  assert(model);
  im2p_cycle_request_t request;
  im2p_cycle_request_init(&request);
  request.m = request.n = 1;
  request.k = 22;
  request.tile_k = 2;
  request.accepted_cycle = 1;
  request.submission = IM2P_CYCLE_TILE_SUBMISSIONS;
  std::array<im2p_compact_run_t, 2> runs{{{0, 0x00000fff, 0, 12},
                                           {1, 0x000003ff, 12, 10}}};
  im2p_compact_runs_t view{IM2P_COMPACT_RUNS_VERSION, sizeof(view), 42,
                           runs.size(), runs.data()};
  im2p_cycle_result_t answer{};
  auto estimate = [&] {
    return im2p_cycle_estimate_runs(model.get(), &request, &view, &answer);
  };
  assert(estimate() == IM2P_CYCLE_OK);
  assert(answer.start_cycle == 1 && answer.logical_work_count == 1);
  assert(answer.loop_count == 2 && answer.planner_loop_count == 2 &&
         answer.fragment_count == 2 && answer.scale_request_count == 2);
  const auto first = answer;
  assert(estimate() == IM2P_CYCLE_OK &&
         std::memcmp(&first, &answer, sizeof(answer)) == 0);

  const auto schedule = im2p::cycle::expand_work(config, request, &view);
  assert(schedule.work.size() == 2);
  assert(schedule.planner.tile.tile_i == request.tile_i &&
         schedule.planner.tile.tile_j == request.tile_j &&
         schedule.planner.tile.tile_k == request.tile_k);
  assert(schedule.work[0].first_plan.original_block_id == 0);
  assert(schedule.work[1].first_plan.original_block_id == 1);
  assert(schedule.work[1].fragment_base == 2);
  assert(schedule.work[0].fragments[0].plan.reduction == 12);
  assert(schedule.work[1].fragments[0].plan.reduction == 10);

  runs[1].original_block_id = 3;
  view.original_k = 106;
  assert(estimate() == IM2P_CYCLE_OK);
  assert(answer.loop_count == 2 && answer.fragment_count == 2);
  const auto gap = im2p::cycle::expand_work(config, request, &view);
  assert(gap.work[1].fragment_base == 6);
  assert(im2p::gemmini::scale_read(gap.planner, gap.work[1].first_plan, 0)
             .byte_offset == 12);

  request.k = 32;
  request.tile_k = 1;
  runs = {{{0, 0x7fffffff, 0, 31}, {1, 1, 31, 1}}};
  view.original_k = 33;
  assert(estimate() == IM2P_CYCLE_OK);
  assert(answer.loop_count == 3 && answer.planner_loop_count == 3 &&
         answer.fragment_count == 3);
  const auto fragments = im2p::cycle::expand_work(config, request, &view);
  assert(fragments.work.size() == 3);
  assert(fragments.work[1].fragment_base == 1 &&
         fragments.work[2].fragment_base == 2);
  assert(fragments.work[0].fragments[0].plan.reduction == 16);
  assert(fragments.work[1].fragments[0].plan.reduction == 15);
  assert(fragments.work[2].fragments[0].plan.reduction == 1);

  const auto before = answer;
  runs[1].compact_k_begin = 30;
  assert(estimate() == IM2P_CYCLE_INVALID);
  assert(std::memcmp(&before, &answer, sizeof(answer)) == 0);
  assert(im2p_cycle_model_event_count(model.get()) == 0);
  runs[1].compact_k_begin = 31;
  request.tile_k = 0;
  assert(estimate() == IM2P_CYCLE_INVALID);
  request.tile_k = 1;
  runs[1].original_block_id = 32768;
  view.original_k = 1048577;
  assert(estimate() == IM2P_CYCLE_INVALID);
  std::cout << "CYCLE_RUNS_PASS loops=" << first.loop_count
            << " fragments=" << first.fragment_count << '\n';
}
