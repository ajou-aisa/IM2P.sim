#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace im2p {
namespace gemmini_hp1 {

enum class Packing : std::uint8_t {
  signed_int4_low_nibble_first = 1,
  signed_int8 = 2,
};

enum class WorkKind : std::uint8_t {
  dense_hp1_final = 1,
  rmd_raw = 2,
};

enum class Mode : std::uint8_t {
  full = 1,
  pipeline = 2,
};

enum class Opcode : std::uint16_t {
  capability_request = 1,
  capability_response = 2,
  work = 3,
  activation = 4,
  weight = 5,
  scale = 6,
  start = 7,
  result = 8,
  completion = 9,
  release = 10,
  error = 255,
};

struct Capability {
  std::string profile;
  std::string build_id;
  std::string numerical_revision;
  std::uint16_t activation_bits = 0;
  std::uint16_t weight_bits = 0;
  std::uint16_t dim = 0;
  std::uint16_t accumulator_bits = 0;
  std::uint16_t block_size = 0;
  Packing packing = Packing::signed_int8;
  bool hp1_shift_only = false;
  bool ws = false;
  bool rmd = false;
  std::uint32_t bank_count = 0;
  std::uint32_t bank_rows = 0;
  std::uint32_t accumulator_rows = 0;
  std::uint32_t scratchpad_row_bytes = 0;
  std::uint32_t accumulator_row_bytes = 0;
};

struct WorkPlanV1 {
  std::uint64_t m = 0;
  std::uint64_t n = 0;
  std::uint64_t k = 0;
  std::uint64_t tile_i = 0;
  std::uint64_t tile_j = 0;
  std::uint64_t tile_k = 0;
  std::uint64_t activation_rows_per_stripe = 0;
  Mode mode = Mode::full;
  WorkKind kind = WorkKind::dense_hp1_final;
};

struct Fragment {
  std::uint64_t index = 0;
  std::uint64_t k_begin = 0;
  std::uint32_t valid_k = 0;
  std::uint64_t block = 0;
  bool first = false;
  bool final = false;
};

struct Scale {
  bool zero = false;
  std::uint16_t shift = 0;
};

struct Frame {
  Opcode opcode = Opcode::error;
  std::uint32_t sequence = 0;
  std::vector<std::uint8_t> payload;
};

[[nodiscard]] bool compatible(const Capability &expected,
                              const Capability &actual) noexcept;
[[nodiscard]] std::vector<Fragment> fragment_work(const Capability &profile,
                                                  const WorkPlanV1 &plan);
[[nodiscard]] Scale normalize_scale(std::uint32_t carrier);
[[nodiscard]] std::vector<std::uint8_t>
pack_int4(const std::vector<std::int8_t> &scalar_bytes);
[[nodiscard]] std::vector<std::int8_t>
unpack_int4(const std::vector<std::uint8_t> &packed, std::size_t count);
[[nodiscard]] std::vector<std::uint8_t> encode_capability(const Capability &capability);
[[nodiscard]] Capability decode_capability(const std::vector<std::uint8_t> &payload);
[[nodiscard]] std::vector<std::uint8_t> encode_work_plan_v1(const WorkPlanV1 &plan);
[[nodiscard]] WorkPlanV1 decode_work_plan_v1(const std::vector<std::uint8_t> &payload);
[[nodiscard]] std::vector<std::uint8_t> encode_frame(const Frame &frame);
[[nodiscard]] Frame decode_frame(const std::vector<std::uint8_t> &bytes);

}
}
