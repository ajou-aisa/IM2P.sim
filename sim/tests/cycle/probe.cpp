// Internal development probe: explicit resolved scalar inputs only, no golden
// file.
#include "../../include/im2p_cycle_model.h"
#include <charconv>
#include <cstring>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string_view>

uint64_t number(const char *s) {
  uint64_t value{};
  const auto parsed = std::from_chars(s, s + std::strlen(s), value);
  if (parsed.ec != std::errc{} || *parsed.ptr != '\0')
    throw std::invalid_argument("invalid integer");
  return value;
}
int main(int argc, char **argv) {
  try {
    if (argc != 21)
      throw std::invalid_argument(
          "bits dim bank_rows acc_rows M N K ti tj tk start read even scale "
          "write period offset half acc_half trace_path");
    im2p_cycle_model_config_t c;
    im2p_cycle_model_config_init(&c);
    const unsigned bits = number(argv[1]), dim = number(argv[2]);
    c.hardware = {bits,
                  bits,
                  dim,
                  32,
                  32,
                  4,
                  static_cast<uint32_t>(number(argv[3])),
                  static_cast<uint32_t>(number(argv[4])),
                  dim * bits / 8,
                  dim * 4,
                  4,
                  2};
    c.timing = {1,
                static_cast<uint32_t>(number(argv[12])),
                static_cast<uint32_t>(number(argv[13])),
                static_cast<uint32_t>(number(argv[14])),
                static_cast<uint32_t>(number(argv[15])),
                static_cast<uint32_t>(number(argv[16])),
                static_cast<uint32_t>(number(argv[17])),
                0};
    c.max_cycles = 2000000;
    im2p_cycle_request_t r;
    im2p_cycle_request_init(&r);
    r.m = number(argv[5]);
    r.n = number(argv[6]);
    r.k = number(argv[7]);
    r.tile_i = number(argv[8]);
    r.tile_j = number(argv[9]);
    r.tile_k = number(argv[10]);
    r.accepted_cycle = number(argv[11]);
    r.submission = IM2P_CYCLE_TILE_SUBMISSIONS;
    r.record_events = 1;
    r.initial_scratchpad_half = number(argv[18]);
    r.initial_accumulator_half = number(argv[19]);
    auto *model = im2p_cycle_model_create(&c);
    if (!model)
      throw std::runtime_error("config rejected");
    im2p_cycle_result_t out{};
    const int status = im2p_cycle_estimate(model, &r, &out);
    if (status) {
      std::cerr << status << ": " << im2p_cycle_model_error(model) << '\n';
      im2p_cycle_model_destroy(model);
      return status;
    }
    std::ofstream trace(argv[20]);
    for (uint64_t i = 0; i < out.event_count; ++i) {
      im2p_cycle_event_t e{};
      im2p_cycle_model_event(model, i, &e);
      trace << e.cycle << ',' << im2p_cycle_event_name(e.type) << ','
            << e.detail << ',' << e.loop << ',' << e.fragment << ','
            << im2p_cycle_resource_name(e.resource) << ',' << e.id << ','
            << e.dependency << '\n';
    }
    std::cout << "{\"start\":" << out.start_cycle
              << ",\"done\":" << out.done_cycle
              << ",\"cycles\":" << out.total_cycles
              << ",\"loop_count\":" << out.loop_count
              << ",\"work_count\":" << out.logical_work_count
              << ",\"load_req\":" << out.load_request_count
              << ",\"load_resp\":" << out.load_response_count
              << ",\"store_req\":" << out.store_request_count
              << ",\"store_resp\":" << out.store_response_count
              << ",\"scale_req\":" << out.scale_request_count
              << ",\"scale_resp\":" << out.scale_response_count << "}\n";
    im2p_cycle_model_destroy(model);
  } catch (const std::exception &error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
