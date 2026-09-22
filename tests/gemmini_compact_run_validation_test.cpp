#include "../sim/common/gemmini_schedule.hpp"

#include <cassert>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <limits>

using namespace im2p::gemmini;

static ScheduleConfig base_config() {
  return {{1, 1, 22}, {16, 8}, {1, 1, 2, 1}};
}

static void valid_and_owned() {
  im2p_compact_run_t runs[] = {{0, 0xfffu, 0, 12}, {1, 0x3ffu, 12, 10}};
  im2p_compact_runs_t view{IM2P_COMPACT_RUNS_VERSION, sizeof(view), 64, 2, runs};
  auto config = base_config();
  assert(set_compact_runs(config, &view));
  runs[1].original_block_id = 3;
  assert(config.runs[1].original_block_id == 1 && valid_config(config));
  view.original_k = 128;
  assert(config.original_k == 64);
  runs[1].original_block_id = 3;
  view.original_k = 128;
  auto gap_config = base_config();
  assert(set_compact_runs(gap_config, &view) && valid_config(gap_config));
  std::cout << "compact runs valid [12,10] and [0,3] owned PASS\n";
}

static void invalid() {
  const im2p_compact_run_t good[] = {{0, 0xfffu, 0, 12}, {1, 0x3ffu, 12, 10}};
  auto config = base_config();
  im2p_compact_run_t runs[2];
  im2p_compact_runs_t view{IM2P_COMPACT_RUNS_VERSION, sizeof(view), 64, 2, runs};
  auto reject = [&] {
    assert(!set_compact_runs(config, &view));
    assert(config.runs.empty() && config.original_k == 0);
  };
  assert(!set_compact_runs(config, nullptr));
  std::memcpy(runs, good, sizeof(runs));
  view.version = 2; reject(); view.version = IM2P_COMPACT_RUNS_VERSION;
  view.struct_size = sizeof(view) - 1; reject(); view.struct_size = sizeof(view);
  view.original_k = 0; reject(); view.original_k = 64;
  view.run_count = 0; reject();
  view.run_count = std::numeric_limits<std::size_t>::max(); reject();
  view.run_count = 2; view.runs = nullptr; reject(); view.runs = runs;
  runs[0].compact_k_begin = 1; reject(); runs[0].compact_k_begin = 0;
  runs[1].compact_k_count = 0; reject();
  runs[1].compact_k_count = 10; runs[1].original_k_mask = 0; reject();
  runs[1].original_k_mask = 0x1ffu; reject();
  runs[1].original_k_mask = 0x3ffu;
  runs[1].compact_k_begin = 11; reject();
  runs[1].compact_k_begin = 13; reject();
  runs[1].compact_k_begin = 12;
  runs[1].original_block_id = 0; reject();
  runs[0].original_block_id = 2; reject();
  std::memcpy(runs, good, sizeof(runs));
  view.original_k = 40; reject(); view.original_k = 64;
  runs[1].compact_k_begin = UINT32_MAX; reject();
  runs[1].compact_k_begin = 12;
  runs[1].compact_k_count = 11; runs[1].original_k_mask = 0x7ffu; reject();
  runs[1].compact_k_count = 10; runs[1].original_k_mask = 0x3ffu;
  config.shape.k = 33;
  view.run_count = 1;
  runs[0].compact_k_count = 33;
  runs[0].original_k_mask = UINT32_MAX;
  reject();
  config.shape.k = 22;
  view.run_count = 2;
  std::memcpy(runs, good, sizeof(runs));
  runs[1].original_block_id = 32768;
  view.original_k = UINT32_MAX; reject();
  config.runs.assign(runs, runs + 2);
  config.original_k = view.original_k;
  assert(!valid_config(config));
  std::cout << "compact runs malformed map and fragment overflow rejected PASS\n";
}

int main(int argc, char **argv) {
  assert(argc == 1 || (argc == 2 && std::strcmp(argv[1], "--invalid-runs") == 0));
  if (argc == 1) valid_and_owned();
  invalid();
}
