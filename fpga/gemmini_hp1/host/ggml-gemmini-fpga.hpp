#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

struct ggml_gemmini_args_t;
namespace ggml::gemmini::quants::act::exsia {
struct StripeReadyEvent;
}

bool ggml_gemmini_fpga_supports(std::size_t rows, std::size_t columns,
                                std::size_t reduction, bool pipeline);
bool ggml_gemmini_fpga_supports(std::size_t rows, std::size_t columns,
                                std::size_t reduction, bool pipeline,
                                bool block_scaled);
bool ggml_gemmini_fpga_uses_rtl();
bool ggml_gemmini_fpga_uses_bounded();
bool ggml_gemmini_fpga_execute(ggml_gemmini_args_t &args, bool pipeline,
                               const std::function<void()> &quantize,
                               const char *layer_name,
                               bool native_scu = true);
std::string ggml_gemmini_fpga_last_error();
void ggml_gemmini_fpga_test_producer_checkpoint(std::size_t stripe_index);

using ggml_gemmini_fpga_observer = void (*)(
    const ggml_gemmini_args_t &, const std::vector<std::int32_t> &, void *);
void ggml_gemmini_fpga_set_observer(ggml_gemmini_fpga_observer observer,
                                    void *context);

using ggml_gemmini_fpga_block_observer = void (*)(
    void *, std::size_t block, std::size_t row, std::size_t column,
    std::size_t count, const std::int64_t *values);
void ggml_gemmini_fpga_set_block_observer(
    ggml_gemmini_fpga_block_observer observer, void *context);

using ggml_gemmini_fpga_result_observer = void (*)(const ggml_gemmini_args_t &,
                                                    void *);
void ggml_gemmini_fpga_set_result_observer(
    ggml_gemmini_fpga_result_observer observer, void *context);

using ggml_gemmini_fpga_boundary_observer = void (*)(
    const char *stage, const ggml_gemmini_args_t &, bool pipeline,
    const ggml::gemmini::quants::act::exsia::StripeReadyEvent *, void *context);
void ggml_gemmini_fpga_set_boundary_observer(
    ggml_gemmini_fpga_boundary_observer observer, void *context);
