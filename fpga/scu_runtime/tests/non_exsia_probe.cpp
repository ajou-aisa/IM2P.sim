// Device-free queries against the ordinary backend, with missing transports.
#include "ggml.h"
#include "ggml-backend.h"
#include "ggml-gemmini.h"
#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <string>

static void require(bool ok, const char *message) {
    if (!ok) throw std::runtime_error(message);
}

int main(int argc, char **argv) {
    try {
        require(argc == 3, "usage: non_exsia_probe library TENSOR|TOKEN|BLOCK|STRIPE");
        const std::string family(argv[2]);
        require(family == "TENSOR" || family == "TOKEN" || family == "BLOCK" || family == "STRIPE", "activation family");
        const bool token = family == "TOKEN";
        std::string activation = family;
        std::transform(activation.begin(), activation.end(), activation.begin(), [](unsigned char c) { return std::tolower(c); });
        unsetenv("IM2P_FPGA_DEVICE");
        setenv("GEMMINI_RMD_BACKEND", "WS", 1); // RMD OFF does not need a CPU override.
        auto reg = ggml_backend_load(argv[1]);
        require(reg && ggml_backend_reg_dev_count(reg) == 1, "ordinary backend load");
        auto dev = ggml_backend_reg_dev_get(reg, 0);
        auto stats_fn = (ggml_gemmini_fpga_stats_v1_fn) ggml_backend_reg_get_proc_address(reg, "ggml_gemmini_fpga_stats_v1");
        require(stats_fn, "stats ABI");
        auto *ctx = ggml_init({32 * 1024 * 1024, nullptr, false});
        require(ctx, "context allocation");
        size_t cases = 0;
        auto supports = [&](ggml_type type, int64_t m, int64_t n, int64_t k, bool loader, bool malformed = false) {
            ++cases;
            auto *w = ggml_new_tensor_2d(ctx, type, k, n);
            auto *a = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, k, m);
            auto *y = ggml_mul_mat(ctx, w, a);
            if (w->data) std::memset(w->data, 0, ggml_nbytes(w));
            if (malformed) ++w->nb[0];
            void *backing = w->data;
            if (loader) {
                w->data = nullptr;
                w->buffer = ggml_backend_buft_alloc_buffer(ggml_backend_dev_buffer_type(dev), 0);
                require(w->buffer, "loader buffer");
            }
            const bool answer = ggml_backend_dev_supports_op(dev, y);
            if (loader) {
                ggml_backend_buffer_free(w->buffer);
                w->buffer = nullptr;
                w->data = backing;
            }
            return answer;
        };
        for (const char *mode : {"FULL", "STRIPE_PIPELINE"}) {
            setenv("GEMMINI_MATMUL_MODE", mode, 1);
            for (bool loader : {false, true}) {
                require(!supports(GGML_TYPE_Q8_CHANNEL, 16, 16, 64, loader), "IFR3 channel accepted");
                require(!supports(GGML_TYPE_Q8_H1, 16, 16, 64, loader), "IFR3 non-ExSIA accepted");
            }
        }
        // strace in the runner verifies neither marker path is opened.
        for (const char *selection : {"rtl:/nonexistent-im2p-probe.so", "uart4:/nonexistent-im2p-probe-device"}) {
            setenv("IM2P_FPGA_DEVICE", selection, 1);
            const std::string description(ggml_backend_dev_description(dev));
            require(description.find("activation=" + activation) != std::string::npos, "activation identity");
            require(description.find("native=" + std::string(token ? "Q8_CHANNEL" : "Q8_H1,Q8_HP1,Q8_0,Q8_CHANNEL")) != std::string::npos, "native format identity");
            require(description.find(token ? "domains=0 " : "domains=0,1 ") != std::string::npos, "output domain identity");
            require(description.find("RMD=OFF") != std::string::npos, "residual identity");
            for (const char *mode : {"FULL", "STRIPE_PIPELINE"}) {
                setenv("GEMMINI_MATMUL_MODE", mode, 1);
                for (bool loader : {false, true}) {
                    require(supports(GGML_TYPE_Q8_CHANNEL, 35, 49, 193, loader), "channel logical tail rejected");
                    require(supports(GGML_TYPE_Q8_CHANNEL, 1, 17, 7, loader), "channel small K rejected");
                    require(!supports(GGML_TYPE_Q8_CHANNEL, 35, 49, 193, loader, true), "malformed channel accepted");
                    for (auto type : {GGML_TYPE_Q8_H1, GGML_TYPE_Q8_HP1, GGML_TYPE_Q8_0}) {
                        require(supports(type, 35, 49, 192, loader) == !token, "block format eligibility");
                        require(!supports(type, 35, 49, 192, loader, true), "malformed block layout accepted");
                    }
                    for (auto type : {GGML_TYPE_F32, GGML_TYPE_F16, GGML_TYPE_I8, GGML_TYPE_Q8_H2, GGML_TYPE_Q8_HP2})
                        require(!supports(type, 16, 16, 64, loader), "unsupported native format accepted");
                }
            }
        }
        ggml_set_no_alloc(ctx, true);
        require(supports(GGML_TYPE_Q8_CHANNEL, 512, 128256, 2048, true), "model output loader extent");
        require(supports(GGML_TYPE_Q8_CHANNEL, 512, 49, 65569, true), "large channel K loader extent");
        require(!supports(GGML_TYPE_Q8_CHANNEL, 512, (1LL << 31) + 1, 7, true), "unencodable N accepted");
        require(!supports(GGML_TYPE_Q8_CHANNEL, 512, 17, (1LL << 31) + 1, true), "unencodable K accepted");
        ggml_gemmini_fpga_stats_v1 stats{};
        require(stats_fn(&stats, sizeof(stats)), "stats snapshot");
        require(!stats.assigned && !stats.attempted && !stats.completed && !stats.failed, "query executed adapter");
        ggml_free(ctx);
        std::printf("NON_EXSIA_PROBE PASS activation=%s cases=%zu adapter_attempts=0\n", family.c_str(), cases);
        return 0;
    } catch (const std::exception &error) {
        std::fprintf(stderr, "NON_EXSIA_PROBE FAIL %s\n", error.what());
        return 1;
    }
}
