#include "gemmini_schedule.hpp"
#include "operand_packing.hpp"

#include <algorithm>
#include <array>
#include <cassert>
#include <cstdint>
#include <iostream>

using namespace im2p::gemmini;

// Golden digests from unmodified 43afd503 next_loop/advance_loop; no second planner.
static void original_vectors(std::size_t bits, std::size_t dim,
                             std::uint64_t expected_cases, std::uint64_t expected_loops,
                             std::uint64_t expected_hash) {
  std::uint64_t hash = 14695981039346656037ULL, loops = 0, cases = 0;
  const auto mix = [&](std::uint64_t value) {
    for (unsigned byte = 0; byte < 8; ++byte) {
      hash ^= (value >> (byte * 8)) & 255;
      hash *= 1099511628211ULL;
    }
  };
  for (std::size_t m : {std::size_t{1}, dim + 3})
  for (std::size_t n : {std::size_t{1}, dim + 5})
  for (std::size_t k : {std::size_t{1}, dim, dim + 1, std::size_t{31},
                         std::size_t{32}, std::size_t{33}, std::size_t{65}, std::size_t{97}})
  for (std::size_t ti : {1, 2})
  for (std::size_t tj : {1, 2})
  for (std::size_t tk : {1, 3})
  for (std::size_t begin : {0, 3})
  for (bool raw : {false, true}) {
    if (raw && k > 32) continue;
    ++cases;
    ScheduleConfig config{{begin + m, n, k}, {dim, bits}, {ti, tj, tk, m}};
    config.split_at_block = !raw;
    config.backing_scales = !raw;
    LoopCursor cursor{begin};
    for (auto value : std::array<std::uint64_t, 9>{bits, dim, m, n, k, ti, tj, tk, begin})
      mix(value);
    mix(raw);
    bool last = false;
    do {
      const auto loop = plan_loop(config, begin + m, cursor);
      advance_loop(config, loop, cursor);
      ++loops;
      for (auto value : std::array<std::uint64_t, 15>{
               loop.i, loop.j, loop.k, loop.is, loop.js, loop.ks, loop.kp, loop.jp,
               loop.fragment_base, loop.first, loop.last, loop.scale_release_count,
               loop.activation_packed_bytes, loop.weight_packed_bytes,
               loop.final_contribution}) mix(value);
      assert(loop.order + 1 == cursor.order);
      assert(loop.accumulate == (loop.k != 0));
      assert(loop.first_contribution == !loop.accumulate);
      assert(loop.ks <= 32);
      std::size_t fragment_work = 0;
      for (std::size_t ordinal = 0; ordinal < loop.fragment_count; ++ordinal) {
        const auto fragment = plan_fragment(config, loop, ordinal);
        assert(fragment.rows && fragment.columns && fragment.reduction);
        assert(fragment.reduction <= std::min(dim, std::size_t{32}));
        assert(fragment.final_contribution == (fragment.k + fragment.reduction == k));
        assert(fragment.accumulate == (fragment.k != 0));
        fragment_work += fragment.rows * fragment.columns * fragment.reduction;
      }
      assert(fragment_work == loop.is * loop.js * loop.ks);
      last = loop.last;
    } while (!last);
    assert(cursor.i == begin + m && cursor.j == 0 && cursor.k == 0);
  }
  assert(cases == expected_cases && loops == expected_loops && hash == expected_hash);
  std::cout << "schedule A" << bits << "D" << dim << " cases=" << cases
            << " loops=" << loops << " digest=" << hash << " PASS\n";
}

static void extents(std::size_t bits, std::size_t dim) {
  ScheduleConfig c{{dim + 3, dim + 5, 65}, {dim, bits}, {2, 2, 3, dim + 3},
                   {71, dim + 12, (dim + 9) * 4, (dim + 7) * 4, 32}};
  assert(valid_config(c));
  assert(padded_shape(c).m == 2 * dim && padded_shape(c).n == 2 * dim);
  LoopCursor cursor{};
  std::size_t output_bytes = 0;
  do {
    const auto loop = plan_loop(c, c.shape.m, cursor);
    std::size_t a = 0, b = 0, scales = 0;
    const auto row_bytes = dim * bits / 8;
    for (std::size_t offset = 0; offset < loop.activation_packed_bytes; offset += row_bytes) {
      const auto read = activation_read(c, loop, offset);
      assert(read.valid && read.element_count <= dim);
      if (read.element_count) {
        assert(read.byte_offset / c.layout.activation_stride < c.shape.m);
        const auto column = read.byte_offset % c.layout.activation_stride;
        assert(column >= loop.k && column + read.element_count <= loop.k + loop.ks);
      }
      a += read.element_count;
    }
    for (std::size_t offset = 0; offset < loop.weight_packed_bytes; offset += row_bytes) {
      const auto read = weight_read(c, loop, offset);
      assert(read.valid && read.element_count <= dim);
      b += read.element_count;
    }
    for (std::size_t offset = 0; offset < loop.scale_packed_bytes; offset += dim * 4) {
      const auto read = scale_read(c, loop, offset);
      assert(read.valid && read.element_count <= dim);
      assert(read.byte_offset / c.layout.scale_stride == (c.layout.k_origin + loop.k) / 32);
      scales += read.element_count * 4;
    }
    assert(a == loop.activation_host_bytes && b == loop.weight_host_bytes);
    assert(scales == loop.scale_host_bytes);
    assert(!activation_read(c, loop, loop.activation_packed_bytes).valid);
    assert(!weight_read(c, loop, loop.weight_packed_bytes).valid);
    assert(!scale_read(c, loop, loop.scale_packed_bytes).valid);
    assert(!scale_read(c, loop, 1).valid);
    output_bytes += loop.final_output_bytes;
    advance_loop(c, loop, cursor);
  } while (cursor.i != c.shape.m);
  assert(output_bytes == c.shape.m * c.shape.n * 4);
  c.accumulate_first = true;
  const auto loop = plan_loop(c, c.shape.m, {});
  assert(loop.accumulate && !loop.first_contribution);
  assert(plan_fragment(c, loop, 0).accumulate);
  c.backing_scales = false;
  assert(plan_loop(c, c.shape.m, {}).scale_host_bytes == 0);
  assert(scale_read(c, loop, UINT64_MAX).valid);
  assert(scale_read(c, loop, UINT64_MAX).element_count == 0);
  c.hardware.dim = 24;
  assert(!valid_config(c));
  c.hardware.dim = dim;
  c.tile.tile_i = 0;
  assert(!valid_config(c));
  std::cout << "extents A" << bits << "D" << dim << " PASS\n";
}

template <std::size_t Bits>
static void packing() {
  constexpr int minimum = -(1 << (Bits - 1));
  constexpr int maximum = (1 << (Bits - 1)) - 1;
  constexpr std::size_t count = 1 << Bits;
  std::array<std::uint8_t, count * Bits / 8> bytes{};
  for (int value = minimum; value <= maximum; ++value)
    put_operand<Bits>(bytes, value - minimum, static_cast<std::int8_t>(value));
  for (std::size_t index = 0; index < count; ++index) {
    int code = Bits == 4 ? ((bytes[index / 2] >> (index % 2 * 4)) & 15) : bytes[index];
    if (code > maximum) code -= count;
    assert(code == static_cast<int>(index) + minimum);
  }
  std::cout << "packing A" << Bits << " all signed codes PASS\n";
}

int main() {
  packing<4>();
  packing<8>();
  original_vectors(4, 16, 832, 2450, 10163628011726388662ULL);
  original_vectors(4, 32, 768, 1900, 13712483495204976165ULL);
  original_vectors(4, 64, 704, 2000, 5927949193360949765ULL);
  original_vectors(8, 16, 832, 2450, 369477847572046622ULL);
  original_vectors(8, 32, 768, 1900, 5563155324140875285ULL);
  original_vectors(8, 64, 704, 2000, 15379386570286628069ULL);
  for (std::size_t bits : {4, 8})
    for (std::size_t dim : {16, 32, 64}) extents(bits, dim);
}
