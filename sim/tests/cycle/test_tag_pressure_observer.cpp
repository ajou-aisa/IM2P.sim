#include "tag_pressure_observer.hpp"

#include <cassert>

int main() {
  TagPressureSignals normal{6, {1U << 19, 0, 1U << 19}, true,
                            true, true, true, true, false, false,
                            false, false, true, false, false};
  assert(!(normal.queue_len == 6 && normal.post_gate_req_valid && !normal.req_ready));
  assert(tag_full_stall(normal));

  auto flush = normal;
  flush.control_valid = false;
  flush.post_gate_req_valid = true;
  assert(flush.queue_len == 6 && flush.post_gate_req_valid && !flush.req_ready);
  assert(!tag_full_stall(flush));

  auto blocked_operand = normal;
  blocked_operand.a_valid = false;
  assert(!tag_full_stall(blocked_operand));

  auto occupied_request = normal;
  occupied_request.req_held = true;
  assert(!tag_full_stall(occupied_request));
  occupied_request.last_fire = true;
  assert(tag_full_stall(occupied_request));

  auto other_queue_full = normal;
  other_queue_full.total_rows_ready = false;
  assert(!tag_full_stall(other_queue_full));

  auto nonfirst = normal;
  nonfirst.control_head[2] = 0;
  assert(!tag_full_stall(nonfirst));

  auto room = normal;
  room.queue_len = 5;
  assert(!tag_full_stall(room));

  constexpr std::array<std::array<unsigned, 3>, 6> profiles{{
      {4, 16, 83}, {4, 32, 88}, {4, 64, 93},
      {8, 16, 82}, {8, 32, 87}, {8, 64, 92}}};
  for (const auto &[activation_bits, dim, expected_first] : profiles) {
    const auto first = control_first_bit(activation_bits, dim);
    assert(first == expected_first);
    auto head = normal;
    head.control_head[2] = 0;
    head.control_head[first / 32] |= 1U << (first % 32);
    assert(tag_full_stall(head, first));
    head.control_head[first / 32] &= ~(1U << (first % 32));
    assert(!tag_full_stall(head, first));
  }
}
