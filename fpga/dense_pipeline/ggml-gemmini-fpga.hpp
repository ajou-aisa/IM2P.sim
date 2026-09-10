#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

struct ggml_gemmini_args_t;

// This query must not open a device or issue transport commands.
bool ggml_gemmini_fpga_supports(std::size_t rows, std::size_t columns,
                                std::size_t reduction, bool pipeline);
bool ggml_gemmini_fpga_execute(ggml_gemmini_args_t &args, bool pipeline,
                               const std::function<void()> &quantize,
                               const char *layer_name);
std::string ggml_gemmini_fpga_last_error();
void ggml_gemmini_fpga_test_producer_checkpoint(std::size_t stripe_index);

// Optional numerical observer. Invoked only after actual execution succeeds.
// Raw values are signed32, block-major [K/32][I][J]. No borrowed data is retained.
using ggml_gemmini_fpga_observer = void (*)(const ggml_gemmini_args_t &,
                                           const std::vector<std::int32_t> &, void *);
void ggml_gemmini_fpga_set_observer(ggml_gemmini_fpga_observer observer, void *context);
