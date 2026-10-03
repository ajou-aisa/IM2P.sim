#include "im2p_verilator_log.h"
#include "im2p_sim.h"

#include <mutex>

namespace {
std::mutex rtl_log_mutex;
im2p_rtl_log_fn rtl_log_callback = nullptr;
void *rtl_log_context = nullptr;
} // namespace

extern "C" void im2p_set_rtl_log_callback(im2p_rtl_log_fn callback, void *context) {
    std::lock_guard<std::mutex> lock(rtl_log_mutex);
    rtl_log_callback = callback;
    rtl_log_context = callback != nullptr ? context : nullptr;
}

bool im2p_verilator_log_message(const char *message, std::size_t length) noexcept {
    im2p_rtl_log_fn callback = nullptr;
    void *context = nullptr;
    {
        std::lock_guard<std::mutex> lock(rtl_log_mutex);
        callback = rtl_log_callback;
        context = rtl_log_context;
    }

    if (callback == nullptr || message == nullptr || length == 0) {
        return false;
    }

    try {
        callback(context, message, length);
    } catch (...) {
        // RTL diagnostics must never affect simulator execution.
    }
    return true;
}
