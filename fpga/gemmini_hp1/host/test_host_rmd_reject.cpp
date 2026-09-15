#include "ggml-gemmini-args.h"
#include "ggml-gemmini-fpga.hpp"

#include <cstdlib>
#include <iostream>
#include <stdexcept>

extern "C" void gemmini_log_debug_layer(const char *, const char *, ...) noexcept {}

int main() {
#if defined(_WIN32)
  _putenv_s("IM2P_FPGA_DEVICE", "");
#else
  unsetenv("IM2P_FPGA_DEVICE");
#endif
  ggml_gemmini_args_t args{};
  const auto capability = ggml_gemmini_hp1_capability();
  if (!capability.rmd) throw std::runtime_error("RMD capability missing");
  ggml_gemmini_hp1_executor incomplete{};
  incomplete.capability = capability;
  if (ggml_gemmini_hp1_bind_executor(incomplete))
    throw std::runtime_error("incomplete RMD executor accepted");
  bool quantized = false;
  if (ggml_gemmini_fpga_execute(
          args, false, [&] { quantized = true; }, "host-rmd-reject") ||
      quantized ||
      ggml_gemmini_fpga_last_error() != "GEMMINI_HP1 requires a native HP1 block-scaled work item") {
    throw std::runtime_error("invalid RMD work reached dispatch");
  }
  std::cout << "GEMMINI_HP1_RMD_INVALID_DISPATCH_REJECT_PASS\n";
}
