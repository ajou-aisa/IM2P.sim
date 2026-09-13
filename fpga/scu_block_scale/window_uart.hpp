#pragma once

#include "rtl_plugin.hpp"
#include <string>

namespace im2p::fpga {
// The existing descriptor/provider ABI also serves the explicit IFR4 transport.
// Opening is an execution operation; supports_op never calls this factory.
void *open_window_uart(const std::string &path, double timeout_seconds);
const im2p_scu_rtl_api_v1 *window_uart_api();
struct WindowUARTMetrics {
    uint64_t request_bytes = 0, response_bytes = 0, transactions = 0;
    double transfer_seconds = 0;
};
WindowUARTMetrics window_uart_metrics(void *handle);
}
