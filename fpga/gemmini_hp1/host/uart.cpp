#include "uart.hpp"

#include <algorithm>
#include <limits>
#include <stdexcept>
#include <type_traits>

namespace im2p {
namespace gemmini_hp1 {
namespace {

constexpr std::uint32_t magic = 0x31504847;
constexpr std::uint16_t protocol_version = 1;
constexpr std::uint16_t work_plan_version = 1;
constexpr std::size_t header_bytes = 16;
constexpr std::size_t crc_bytes = 4;
constexpr std::size_t maximum_payload_bytes = 64U * 1024U * 1024U;

void require(bool value, const char *message) {
  if (!value) {
    throw std::invalid_argument(message);
  }
}

void validate_work_kind(const WorkPlanV1 &plan) {
  switch (plan.kind) {
  case WorkKind::dense_hp1_final:
    return;
  case WorkKind::rmd_raw:
    require(plan.k > 0 && plan.k <= 32, "RMD raw requires compact K in 1..32");
    return;
  }
  throw std::invalid_argument("unsupported work kind");
}

template <typename T>
void put(std::vector<std::uint8_t> &bytes, T value) {
  static_assert(std::is_unsigned<T>::value, "wire fields must be unsigned");
  for (std::size_t index = 0; index < sizeof(T); ++index) {
    bytes.push_back(static_cast<std::uint8_t>(value >> (index * 8U)));
  }
}

template <typename T>
T get(const std::vector<std::uint8_t> &bytes, std::size_t &offset) {
  static_assert(std::is_unsigned<T>::value, "wire fields must be unsigned");
  require(offset <= bytes.size() && sizeof(T) <= bytes.size() - offset,
          "truncated little-endian field");
  T value = 0;
  for (std::size_t index = 0; index < sizeof(T); ++index) {
    value |= static_cast<T>(bytes[offset + index]) << (index * 8U);
  }
  offset += sizeof(T);
  return value;
}

void put_string(std::vector<std::uint8_t> &bytes, const std::string &value) {
  require(value.size() <= std::numeric_limits<std::uint16_t>::max(), "string too long");
  put(bytes, static_cast<std::uint16_t>(value.size()));
  bytes.insert(bytes.end(), value.begin(), value.end());
}

std::string get_string(const std::vector<std::uint8_t> &bytes, std::size_t &offset) {
  const auto count = get<std::uint16_t>(bytes, offset);
  require(offset <= bytes.size() && count <= bytes.size() - offset, "truncated string");
  std::string value(reinterpret_cast<const char *>(bytes.data() + offset), count);
  offset += count;
  return value;
}

std::uint32_t crc32(const std::vector<std::uint8_t> &bytes, std::size_t count) noexcept {
  std::uint32_t value = std::numeric_limits<std::uint32_t>::max();
  for (std::size_t index = 0; index < count; ++index) {
    const auto byte = bytes[index];
    value ^= byte;
    for (unsigned bit = 0; bit < 8; ++bit) {
      value = (value >> 1U) ^ ((value & 1U) != 0U ? 0xedb88320U : 0U);
    }
  }
  return ~value;
}

}

bool compatible(const Capability &expected, const Capability &actual) noexcept {
  return expected.profile == actual.profile && expected.build_id == actual.build_id &&
         expected.numerical_revision == actual.numerical_revision &&
         expected.activation_bits == actual.activation_bits &&
         expected.weight_bits == actual.weight_bits && expected.dim == actual.dim &&
         expected.accumulator_bits == actual.accumulator_bits &&
         expected.block_size == actual.block_size && expected.packing == actual.packing &&
         expected.hp1_shift_only == actual.hp1_shift_only && expected.ws == actual.ws &&
         expected.rmd == actual.rmd && expected.bank_count == actual.bank_count &&
         expected.bank_rows == actual.bank_rows &&
         expected.accumulator_rows == actual.accumulator_rows &&
         expected.scratchpad_row_bytes == actual.scratchpad_row_bytes &&
         expected.accumulator_row_bytes == actual.accumulator_row_bytes;
}

std::vector<Fragment> fragment_work(const Capability &profile, const WorkPlanV1 &plan) {
  require(profile.dim == 16 || profile.dim == 32 || profile.dim == 64, "unsupported DIM");
  require(profile.block_size == 32 && profile.hp1_shift_only && profile.ws,
          "unsupported capability");
  require(plan.m != 0 && plan.n != 0 && plan.k != 0 && plan.tile_i != 0 &&
              plan.tile_j != 0 && plan.tile_k != 0,
          "invalid work plan geometry");
  validate_work_kind(plan);
  require(plan.kind != WorkKind::rmd_raw || profile.rmd, "RMD raw capability missing");
  require(plan.mode == Mode::full ||
              (plan.mode == Mode::pipeline && plan.activation_rows_per_stripe != 0),
          "invalid work plan mode");

  std::vector<Fragment> fragments;
  for (std::uint64_t k_begin = 0; k_begin < plan.k;) {
    const auto remaining = plan.k - k_begin;
    const auto in_block = static_cast<std::uint64_t>(32U - (k_begin % 32U));
    const auto valid = std::min({static_cast<std::uint64_t>(profile.dim), remaining, in_block});
    fragments.push_back({fragments.size(), k_begin, static_cast<std::uint32_t>(valid),
                         k_begin / 32U, k_begin == 0, k_begin + valid == plan.k});
    k_begin += valid;
  }
  return fragments;
}

Scale normalize_scale(std::uint32_t carrier) {
  if (carrier == 0x80000000U) {
    return {true, 0};
  }
  require(carrier <= 32767U, "invalid HP1 scale carrier");
  return {false, static_cast<std::uint16_t>(carrier)};
}

std::vector<std::uint8_t> pack_int4(const std::vector<std::int8_t> &scalar_bytes) {
  std::vector<std::uint8_t> packed((scalar_bytes.size() + 1U) / 2U, 0);
  for (std::size_t index = 0; index < scalar_bytes.size(); ++index) {
    const auto value = scalar_bytes[index];
    require(value >= -8 && value <= 7, "INT4 scalar out of range");
    const auto nibble = static_cast<std::uint8_t>(value) & 0x0fU;
    packed[index / 2U] |= static_cast<std::uint8_t>(nibble << ((index % 2U) * 4U));
  }
  return packed;
}

std::vector<std::int8_t> unpack_int4(const std::vector<std::uint8_t> &packed,
                                     std::size_t count) {
  require(count <= packed.size() * 2U, "packed INT4 input is short");
  std::vector<std::int8_t> scalars;
  scalars.reserve(count);
  for (std::size_t index = 0; index < count; ++index) {
    const auto nibble = static_cast<std::uint8_t>(
        (packed[index / 2U] >> ((index % 2U) * 4U)) & 0x0fU);
    scalars.push_back(static_cast<std::int8_t>(nibble | ((nibble & 0x08U) != 0U ? 0xf0U : 0U)));
  }
  return scalars;
}

std::vector<std::uint8_t> encode_capability(const Capability &capability) {
  std::vector<std::uint8_t> bytes;
  put_string(bytes, capability.profile);
  put_string(bytes, capability.build_id);
  put_string(bytes, capability.numerical_revision);
  put(bytes, capability.activation_bits);
  put(bytes, capability.weight_bits);
  put(bytes, capability.dim);
  put(bytes, capability.accumulator_bits);
  put(bytes, capability.block_size);
  put(bytes, static_cast<std::uint8_t>(capability.packing));
  put(bytes, static_cast<std::uint8_t>((capability.hp1_shift_only ? 1U : 0U) |
                                       (capability.ws ? 2U : 0U) |
                                       (capability.rmd ? 4U : 0U)));
  put(bytes, capability.bank_count);
  put(bytes, capability.bank_rows);
  put(bytes, capability.accumulator_rows);
  put(bytes, capability.scratchpad_row_bytes);
  put(bytes, capability.accumulator_row_bytes);
  return bytes;
}

Capability decode_capability(const std::vector<std::uint8_t> &payload) {
  std::size_t offset = 0;
  Capability capability;
  capability.profile = get_string(payload, offset);
  capability.build_id = get_string(payload, offset);
  capability.numerical_revision = get_string(payload, offset);
  capability.activation_bits = get<std::uint16_t>(payload, offset);
  capability.weight_bits = get<std::uint16_t>(payload, offset);
  capability.dim = get<std::uint16_t>(payload, offset);
  capability.accumulator_bits = get<std::uint16_t>(payload, offset);
  capability.block_size = get<std::uint16_t>(payload, offset);
  capability.packing = static_cast<Packing>(get<std::uint8_t>(payload, offset));
  const auto flags = get<std::uint8_t>(payload, offset);
  capability.hp1_shift_only = (flags & 1U) != 0U;
  capability.ws = (flags & 2U) != 0U;
  capability.rmd = (flags & 4U) != 0U;
  capability.bank_count = get<std::uint32_t>(payload, offset);
  capability.bank_rows = get<std::uint32_t>(payload, offset);
  capability.accumulator_rows = get<std::uint32_t>(payload, offset);
  capability.scratchpad_row_bytes = get<std::uint32_t>(payload, offset);
  capability.accumulator_row_bytes = get<std::uint32_t>(payload, offset);
  require(offset == payload.size(), "capability payload has trailing bytes");
  require(capability.packing == Packing::signed_int4_low_nibble_first ||
              capability.packing == Packing::signed_int8,
          "invalid capability packing");
  return capability;
}

std::vector<std::uint8_t> encode_work_plan_v1(const WorkPlanV1 &plan) {
  require(plan.mode == Mode::full || plan.mode == Mode::pipeline,
          "invalid work plan mode");
  validate_work_kind(plan);
  std::vector<std::uint8_t> bytes;
  put(bytes, work_plan_version);
  put(bytes, plan.m);
  put(bytes, plan.n);
  put(bytes, plan.k);
  put(bytes, plan.tile_i);
  put(bytes, plan.tile_j);
  put(bytes, plan.tile_k);
  put(bytes, plan.activation_rows_per_stripe);
  put(bytes, static_cast<std::uint8_t>(plan.mode));
  put(bytes, static_cast<std::uint8_t>(plan.kind));
  return bytes;
}

WorkPlanV1 decode_work_plan_v1(const std::vector<std::uint8_t> &payload) {
  std::size_t offset = 0;
  require(get<std::uint16_t>(payload, offset) == work_plan_version,
          "work plan version mismatch");
  WorkPlanV1 plan;
  plan.m = get<std::uint64_t>(payload, offset);
  plan.n = get<std::uint64_t>(payload, offset);
  plan.k = get<std::uint64_t>(payload, offset);
  plan.tile_i = get<std::uint64_t>(payload, offset);
  plan.tile_j = get<std::uint64_t>(payload, offset);
  plan.tile_k = get<std::uint64_t>(payload, offset);
  plan.activation_rows_per_stripe = get<std::uint64_t>(payload, offset);
  plan.mode = static_cast<Mode>(get<std::uint8_t>(payload, offset));
  plan.kind = static_cast<WorkKind>(get<std::uint8_t>(payload, offset));
  require(offset == payload.size(), "work plan payload has trailing bytes");
  require(plan.mode == Mode::full || plan.mode == Mode::pipeline,
          "invalid work plan mode");
  validate_work_kind(plan);
  return plan;
}

std::vector<std::uint8_t> encode_frame(const Frame &frame) {
  require(frame.payload.size() <= maximum_payload_bytes, "frame payload too large");
  std::vector<std::uint8_t> bytes;
  bytes.reserve(header_bytes + frame.payload.size() + crc_bytes);
  put(bytes, magic);
  put(bytes, protocol_version);
  put(bytes, static_cast<std::uint16_t>(frame.opcode));
  put(bytes, frame.sequence);
  put(bytes, static_cast<std::uint32_t>(frame.payload.size()));
  bytes.insert(bytes.end(), frame.payload.begin(), frame.payload.end());
  put(bytes, crc32(bytes, bytes.size()));
  return bytes;
}

Frame decode_frame(const std::vector<std::uint8_t> &bytes) {
  require(bytes.size() >= header_bytes + crc_bytes, "frame is too short");
  std::size_t offset = 0;
  require(get<std::uint32_t>(bytes, offset) == magic, "frame magic mismatch");
  require(get<std::uint16_t>(bytes, offset) == protocol_version, "protocol version mismatch");
  const auto opcode = static_cast<Opcode>(get<std::uint16_t>(bytes, offset));
  const auto sequence = get<std::uint32_t>(bytes, offset);
  const auto payload_bytes = get<std::uint32_t>(bytes, offset);
  require(payload_bytes <= maximum_payload_bytes, "frame payload too large");
  require(bytes.size() == header_bytes + payload_bytes + crc_bytes, "frame size mismatch");
  const auto payload_end = header_bytes + payload_bytes;
  std::size_t crc_offset = payload_end;
  const auto expected_crc = get<std::uint32_t>(bytes, crc_offset);
  require(crc32(bytes, payload_end) == expected_crc, "frame CRC mismatch");
  return {opcode, sequence,
          std::vector<std::uint8_t>(bytes.begin() + static_cast<std::ptrdiff_t>(header_bytes),
                                    bytes.begin() + static_cast<std::ptrdiff_t>(payload_end))};
}

}
}
