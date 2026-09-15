#include "uart.hpp"

#include <array>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {

std::uint32_t number(const char *text) {
  std::size_t used = 0;
  const auto value = std::stoul(text, &used);
  if (text[used] != '\0' || value > UINT32_MAX) {
    throw std::invalid_argument("invalid profile number");
  }
  return static_cast<std::uint32_t>(value);
}

void check(bool condition) {
  if (!condition) {
    throw std::runtime_error("host contract check failed");
  }
}

}

int main(int argc, char **argv) {
  using namespace im2p::gemmini_hp1;
  if (argc != 15) {
    throw std::invalid_argument("host test requires a resolved profile");
  }
  const auto packing = std::string(argv[9]) == "Packing::signed_int4_low_nibble_first"
                           ? Packing::signed_int4_low_nibble_first
                           : Packing::signed_int8;
  const Capability capability{
      argv[1], argv[2], argv[3],
      static_cast<std::uint16_t>(number(argv[4])),
      static_cast<std::uint16_t>(number(argv[5])),
      static_cast<std::uint16_t>(number(argv[6])),
      static_cast<std::uint16_t>(number(argv[7])),
      static_cast<std::uint16_t>(number(argv[8])), packing, true, true, false,
      number(argv[10]), number(argv[11]), number(argv[12]), number(argv[13]), number(argv[14])};
  const auto round_trip = decode_capability(encode_capability(capability));
  check(compatible(capability, round_trip));

  const WorkPlanV1 full{17, 19, 65, 3, 5, 7, 0, Mode::full,
                        WorkKind::dense_hp1_final};
  const auto fragments = fragment_work(capability, full);
  check(!fragments.empty() && fragments.front().first && fragments.back().final);
  check(fragments.front().k_begin == 0 && fragments.back().k_begin < full.k);
  for (const auto &fragment : fragments) {
    check(fragment.valid_k <= capability.dim && fragment.valid_k <= 32);
    check(fragment.k_begin / 32 == fragment.block);
    check(fragment.k_begin % 32 + fragment.valid_k <= 32);
  }

  auto pipeline = full;
  pipeline.mode = Mode::pipeline;
  pipeline.activation_rows_per_stripe = 8;
  const auto work = decode_frame(
      encode_frame({Opcode::work, 18, encode_work_plan_v1(pipeline)}));
  check(work.opcode == Opcode::work && work.sequence == 18);
  const auto decoded_plan = decode_work_plan_v1(work.payload);
  check(decoded_plan.m == pipeline.m && decoded_plan.n == pipeline.n &&
        decoded_plan.k == pipeline.k && decoded_plan.tile_i == pipeline.tile_i &&
        decoded_plan.tile_j == pipeline.tile_j && decoded_plan.tile_k == pipeline.tile_k &&
        decoded_plan.activation_rows_per_stripe == pipeline.activation_rows_per_stripe &&
        decoded_plan.mode == pipeline.mode && decoded_plan.kind == pipeline.kind);
  const auto pipeline_fragments = fragment_work(capability, pipeline);
  check(pipeline_fragments.size() == fragments.size());
  for (std::size_t index = 0; index < fragments.size(); ++index) {
    check(pipeline_fragments[index].k_begin == fragments[index].k_begin);
    check(pipeline_fragments[index].valid_k == fragments[index].valid_k);
  }
  check(full.tile_i == 3 && full.tile_j == 5 && full.tile_k == 7);

  const std::array<std::int8_t, 5> source_values{-8, -1, 0, 3, 7};
  const std::vector<std::int8_t> values(source_values.begin(), source_values.end());
  const auto packed = pack_int4(values);
  check((packed == std::vector<std::uint8_t>{0xf8, 0x30, 0x07}));
  check(unpack_int4(packed, values.size()) ==
        std::vector<std::int8_t>(values.begin(), values.end()));
  check(normalize_scale(0).shift == 0 && !normalize_scale(0).zero);
  check(normalize_scale(0x80000000U).zero);
  bool invalid_scale = false;
  try {
    static_cast<void>(normalize_scale(0x80000001U));
  } catch (const std::invalid_argument &) {
    invalid_scale = true;
  }
  check(invalid_scale);

  Frame frame{Opcode::capability_response, 17, encode_capability(capability)};
  const auto wire = encode_frame(frame);
  const auto decoded = decode_frame(wire);
  check(decoded.opcode == frame.opcode && decoded.sequence == frame.sequence &&
        compatible(capability, decode_capability(decoded.payload)));
  auto corrupt = wire;
  corrupt.back() ^= 1U;
  bool bad_crc = false;
  try {
    static_cast<void>(decode_frame(corrupt));
  } catch (const std::invalid_argument &) {
    bad_crc = true;
  }
  check(bad_crc);

  std::cout << "GEMMINI_HP1_HOST_COMMON_PASS profile=" << capability.profile
            << " fragments=" << fragments.size() << '\n';
}
