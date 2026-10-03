#pragma once

#include <array>
#include <cstdint>

struct TagPressureSignals {
  unsigned queue_len;
  std::array<std::uint32_t, 3> control_head;
  bool control_valid;
  bool a_ready, b_ready, d_ready;
  bool a_valid, b_valid, d_valid;
  bool req_held, last_fire, total_rows_ready, req_ready;
  bool post_gate_req_valid;
};

constexpr bool control_bit(const TagPressureSignals &signals, unsigned position) {
  return bool(signals.control_head[position / 32] & (1U << (position % 32)));
}

constexpr unsigned control_first_bit(unsigned activation_bits, unsigned dim) {
  return (activation_bits == 4 ? 83U : 82U) +
         (dim == 16 ? 0U : dim == 32 ? 5U : 10U);
}

constexpr bool tag_full_stall(const TagPressureSignals &signals,
                              unsigned first_bit = 83) {
  const bool a = control_bit(signals, 19), b = control_bit(signals, 20);
  const bool d = control_bit(signals, 21);
  const bool operands_ready =
      (!a || !signals.a_ready || signals.a_valid) &&
      (!b || !signals.b_ready || signals.b_valid) &&
      (!d || !signals.d_ready || signals.d_valid);
  return signals.queue_len == 6 && signals.control_valid &&
         control_bit(signals, first_bit) &&
         (a || b || d) && operands_ready &&
         (!signals.req_held || signals.last_fire) &&
         signals.total_rows_ready && !signals.req_ready;
}
