#pragma once

#include "ggml-gemmini-args.h"
#include "residual/rmd/rmd-builder.hpp"
#include "rmd.hpp"

#include <cstddef>
#include <cstdint>
#include <vector>

namespace im2p::gemmini_hp1 {

class RmdRtlFixture {
public:
  static constexpr std::size_t rows = 9;
  static constexpr std::size_t columns = 3;
  static constexpr std::size_t k = 64;

  ggml_gemmini_args_t args;
  std::vector<float> output;

  RmdRtlFixture();
  ggml::gemmini::rmd::StripePacketHandle packet(std::size_t stripe_id,
                                                std::size_t row_begin,
                                                std::size_t row_count) const;
  std::vector<std::int64_t> expected_correction(std::size_t row_begin,
                                                std::size_t row_count) const;
  std::vector<float> expected_merge(std::size_t row_begin,
                                    std::size_t row_count) const;
  void set_exponent(std::size_t column, std::size_t block,
                    std::int16_t exponent);

private:
  std::vector<block_q4_hp1> q4_;
  std::vector<block_q8_hp1> q8_;
  std::vector<std::int32_t> residuals_;
  std::vector<std::int16_t> exponents_;
};

// Frozen raw-mode evidence links the historical overload from its saved object.
void run_rmd_ws_rtl_fixture(const Capability &, void *, RmdRawExecute);
void run_rmd_ws_rtl_fixture(const Capability &, void *, RmdScuExecute);

} // namespace im2p::gemmini_hp1
