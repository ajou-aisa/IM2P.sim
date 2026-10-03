#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>

#include <verilated.h>
#include "../common/operand_packing.hpp"

namespace im2p::integrated {

template <typename Signal, typename Bytes>
void set_bytes(Signal &signal, const Bytes &bytes) {
  if constexpr (VlIsVlWide<Signal>::value) {
    std::fill_n(signal.data(), signal.size(), 0U);
    for (std::size_t i = 0; i < bytes.size(); ++i)
      signal.at(i / 4) |= static_cast<std::uint32_t>(bytes[i]) << (i % 4 * 8);
  } else {
    std::uint64_t value = 0;
    for (std::size_t i = 0; i < bytes.size(); ++i)
      value |= static_cast<std::uint64_t>(bytes[i]) << (i * 8);
    signal = static_cast<Signal>(value);
  }
}

template <typename Signal>
std::uint8_t byte_at(const Signal &signal, std::size_t index) {
  if constexpr (VlIsVlWide<Signal>::value)
    return static_cast<std::uint8_t>(signal.at(index / 4) >> (index % 4 * 8));
  else
    return static_cast<std::uint8_t>(static_cast<std::uint64_t>(signal) >> (index * 8));
}

template <typename Bytes>
void put_operand(Bytes &bytes, std::size_t index, std::int8_t value) {
  gemmini::put_operand<IM2P_ACTIVATION_BITS>(bytes, index, value);
}

}
