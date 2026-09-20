#pragma once

#include "im2p_sim.h"
#include <vector>

namespace im2p::cpu_functional {
struct Operands {
  im2p_matmul_desc_t descriptor{};
  std::vector<int8_t> weights;
  std::vector<uint32_t> scales;
};
bool identity(uint32_t abi, uint32_t a, uint32_t a_bytes, uint32_t w,
              uint32_t w_bytes, uint32_t dim) noexcept;
bool geometry(const im2p_production_geometry_v1_t &g,
              const im2p_matmul_desc_t &d, uint32_t scope) noexcept;
int prepare(Operands &out, const im2p_matmul_desc_t &descriptor);
int execute(const Operands &operands, const void *activations, size_t stride,
            size_t first_row, size_t rows);
im2p_matmul_desc_t descriptor(const im2p_stripe_work_desc_t &d) noexcept;
} // namespace im2p::cpu_functional
