// IFR3 target copied explicitly from fpga/dense_pipeline/ggml-gemmini-fpga.hpp; legacy source is preserved.
#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

struct ggml_gemmini_args_t;
namespace ggml::gemmini::quants::act::exsia { struct StripeReadyEvent; }

// This query must not open a device or issue transport commands.
bool ggml_gemmini_fpga_supports(std::size_t rows, std::size_t columns,
                                std::size_t reduction, bool pipeline);
// Channel-scale BYPASS has no quantization-block K alignment requirement.
// Keep the original query/ABI conservative for block-scaled weights.
bool ggml_gemmini_fpga_supports(std::size_t rows, std::size_t columns,
                                std::size_t reduction, bool pipeline, bool block_scaled);
bool ggml_gemmini_fpga_uses_rtl();
bool ggml_gemmini_fpga_uses_bounded();
// False preserves the existing bounded External contract after Q8_0 has been
// reprocessed into H1 storage. Native H1/HP1 select SCU independently of storage.
bool ggml_gemmini_fpga_execute(ggml_gemmini_args_t &args, bool pipeline,
                               const std::function<void()> &quantize,
                               const char *layer_name, bool native_scu = true);
std::string ggml_gemmini_fpga_last_error();
void ggml_gemmini_fpga_test_producer_checkpoint(std::size_t stripe_index);

// Optional numerical observer. Invoked only after actual execution succeeds.
// Raw values are final signed32 [I][J], callback domain2. No borrowed data is retained.
using ggml_gemmini_fpga_observer = void (*)(const ggml_gemmini_args_t &,
                                           const std::vector<std::int32_t> &, void *);
void ggml_gemmini_fpga_set_observer(ggml_gemmini_fpga_observer observer, void *context);

// Main-compatible provider output: signed64 block partials (domain1), or the
// single final BYPASS contribution (domain0) for channel-scale weights.
// These observations are provisional until execute succeeds; no dot is computed
// by this hook. It must not retain the borrowed values or throw.
using ggml_gemmini_fpga_block_observer = void (*)(void *, std::size_t block,
    std::size_t row, std::size_t column, std::size_t count, const std::int64_t *values);
void ggml_gemmini_fpga_set_block_observer(ggml_gemmini_fpga_block_observer, void *context);
using ggml_gemmini_fpga_result_observer = void (*)(const ggml_gemmini_args_t &, void *);
void ggml_gemmini_fpga_set_result_observer(ggml_gemmini_fpga_result_observer, void *context);

// Optional capture of the existing host invocation/publication boundary. All
// arguments are borrowed; callbacks must copy retained bytes and must not throw.
// Observations are provisional until the "commit" stage, after checked release.
using ggml_gemmini_fpga_boundary_observer = void (*)(const char *stage,
    const ggml_gemmini_args_t &, bool pipeline,
    const ggml::gemmini::quants::act::exsia::StripeReadyEvent *, void *context);
void ggml_gemmini_fpga_set_boundary_observer(ggml_gemmini_fpga_boundary_observer, void *context);
