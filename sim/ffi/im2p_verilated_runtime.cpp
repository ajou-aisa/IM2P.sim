#include "im2p_verilator_log.h"

#define VL_WRITEF_NX im2p_verilator_original_writef_nx
#define VL_FWRITEF_NX im2p_verilator_original_fwritef_nx
#include "verilated.cpp"
#undef VL_FWRITEF_NX
#undef VL_WRITEF_NX

namespace {
inline bool im2p_is_standard_console_fd(IData fpi) noexcept {
    constexpr IData non_mcd = 0x80000000U;
    return fpi == (non_mcd | 1U) || fpi == (non_mcd | 2U);
}
} // namespace

void VL_WRITEF_NX(const std::string &format, int argc, ...) VL_MT_SAFE {
    static thread_local std::string output;
    output.clear();

    va_list ap;
    va_start(ap, argc);
#if VERILATOR_VERSION_INTEGER >= 5048000
    _vl_vsformat(output, format, argc, ap);
#else
    _vl_vsformat(output, format, ap);
#endif
    va_end(ap);

    if (!im2p_verilator_log_message(output.data(), output.size())) {
        VL_PRINTF_MT("%s", output.c_str());
    }
}

void VL_FWRITEF_NX(IData fpi, const std::string &format, int argc, ...) VL_MT_SAFE {
    static thread_local std::string output;
    output.clear();

    va_list ap;
    va_start(ap, argc);
#if VERILATOR_VERSION_INTEGER >= 5048000
    _vl_vsformat(output, format, argc, ap);
#else
    _vl_vsformat(output, format, ap);
#endif
    va_end(ap);

    if (im2p_is_standard_console_fd(fpi) &&
        im2p_verilator_log_message(output.data(), output.size())) {
        return;
    }

    Verilated::threadContextp()->impp()->fdWrite(fpi, output);
}

void VL_WRITEF_NX(const char *format, int argc, ...) VL_MT_SAFE {
    static thread_local std::string output;
    output.clear();

    va_list ap;
    va_start(ap, argc);
#if VERILATOR_VERSION_INTEGER >= 5048000
    _vl_vsformat(output, format, argc, ap);
#else
    _vl_vsformat(output, format, ap);
#endif
    va_end(ap);

    if (!im2p_verilator_log_message(output.data(), output.size())) {
        VL_PRINTF_MT("%s", output.c_str());
    }
}

void VL_FWRITEF_NX(IData fpi, const char *format, int argc, ...) VL_MT_SAFE {
    static thread_local std::string output;
    output.clear();

    va_list ap;
    va_start(ap, argc);
#if VERILATOR_VERSION_INTEGER >= 5048000
    _vl_vsformat(output, format, argc, ap);
#else
    _vl_vsformat(output, format, ap);
#endif
    va_end(ap);

    if (im2p_is_standard_console_fd(fpi) &&
        im2p_verilator_log_message(output.data(), output.size())) {
        return;
    }

    Verilated::threadContextp()->impp()->fdWrite(fpi, output);
}
