#include "runtime.hpp"
#include <cstdio>
#include <cstring>

namespace im2p::gemmini_hp1 {

void map_read(Runtime &runtime) {
  auto &pending = runtime.read;
  const auto &loop = runtime.current_loop;
  const auto address = static_cast<std::uint64_t>(runtime.top.io_readRequest_bits_address);
  pending = {};
  pending.active = true;
  pending.rtl_id = runtime.top.io_readRequest_bits_id;
  pending.tag = ++runtime.tag_sequence << 8 | pending.rtl_id;
  pending.bytes.fill(0);
  const auto a_base = slot_address(kABase, runtime.current_stripe.slot);
  const auto b_base = slot_address(kBBase, runtime.current_stripe.slot);
  const auto s_base = slot_address(kSBase, runtime.current_stripe.slot);
  gemmini::ReadExtent extent{};
  std::uint64_t host_base = 0;
  if (address >= a_base && address < a_base + loop.activation_packed_bytes) {
    pending.kind = ReadKind::activation;
    extent = gemmini::activation_read(runtime.schedule, loop, address - a_base);
    if (runtime.explicit_geometry && extent.valid && extent.element_count) {
      const auto stride = runtime.descriptor.activation_row_stride;
      const auto row = extent.byte_offset / stride;
      const auto column = extent.byte_offset % stride;
      const auto &stripe = runtime.current_stripe;
      if (row < stripe.row_begin || row - stripe.row_begin >= stripe.row_count) {
        runtime.fault = true;
        return;
      }
      extent.byte_offset = stripe.row_begin * stride +
          (row - stripe.row_begin) * stripe.row_stride + column;
    }
    host_base = runtime.descriptor.activation_base;
  } else if (address >= b_base && address < b_base + loop.weight_packed_bytes) {
    pending.kind = ReadKind::weight;
    extent = gemmini::weight_read(runtime.schedule, loop, address - b_base);
    host_base = runtime.descriptor.weight_base;
  } else if (address >= s_base) {
    pending.kind = ReadKind::scale;
    extent = gemmini::scale_read(runtime.schedule, loop, address - s_base);
    host_base = runtime.descriptor.scale_base;
  } else {
    std::fprintf(stderr, "integrated: unmapped read 0x%llx loop k=%zu kp=%zu\n",
                 static_cast<unsigned long long>(address), loop.k, loop.kp);
    runtime.fault = true;
    return;
  }
  if (!extent.valid) {
    runtime.fault = true;
    return;
  }
  pending.count = extent.element_count;
  if (pending.count) pending.address = host_base + extent.byte_offset;
  else pending.response = true;
}

std::uint32_t write_lane_count(const Top &top) {
  std::uint32_t count = 0;
  bool gap = false;
  for (std::size_t lane = 0; lane < kDim; ++lane) {
    bool enabled = true;
    for (std::size_t byte = 0; byte < sizeof(std::int32_t); ++byte) {
      const auto bit = lane * sizeof(std::int32_t) + byte;
      enabled &= (im2p::integrated::byte_at(top.io_writeRequest_bits_mask, bit / 8) &
                  (1U << (bit % 8))) != 0;
    }
    if (enabled && gap) return UINT32_MAX;
    if (enabled) ++count;
    else gap = true;
  }
  return count;
}

void map_write(Runtime &runtime) {
  auto &pending = runtime.write;
  pending = {};
  pending.active = true;
  pending.rtl_id = runtime.top.io_writeRequest_bits_id;
  pending.tag = ++runtime.tag_sequence << 8 | pending.rtl_id;
  const auto address = static_cast<std::uint64_t>(runtime.top.io_writeRequest_bits_address);
  const auto base = slot_address(kCBase, runtime.current_stripe.slot);
  if (address < base) {
    runtime.fault = true;
    return;
  }
  pending.address = runtime.descriptor.output_base + address - base;
  pending.count = write_lane_count(runtime.top);
  if (pending.count == UINT32_MAX || pending.count > kDim) {
    runtime.fault = true;
    return;
  }
  for (std::size_t lane = 0; lane < kDim; ++lane) {
    std::uint32_t bits = 0;
    for (std::size_t byte = 0; byte < sizeof(bits); ++byte)
      bits |= static_cast<std::uint32_t>(im2p::integrated::byte_at(
          runtime.top.io_writeRequest_bits_data, lane * sizeof(bits) + byte)) << (byte * 8);
    std::int32_t value;
    std::memcpy(&value, &bits, sizeof(value));
    pending.values[lane] = value;
  }
}

} // namespace im2p::gemmini_hp1
