#pragma once

#include <cstddef>
#include <cstdint>

namespace im2p::gemmini {

template <std::size_t OperandBits, typename Bytes>
void put_operand(Bytes &bytes, std::size_t index, std::int8_t value) {
  static_assert(OperandBits == 4 || OperandBits == 8);
  if constexpr (OperandBits == 4)
    bytes.at(index / 2) |= (static_cast<std::uint8_t>(value) & 15U) << (index % 2 * 4);
  else
    bytes.at(index) = static_cast<std::uint8_t>(value);
}

} // namespace im2p::gemmini
