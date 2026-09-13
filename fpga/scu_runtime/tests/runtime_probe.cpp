// Device-free check against the same dlopen'ed backend shipped with llama-cli.
#include "ggml.h"
#include "ggml-backend.h"
#include "ggml-gemmini.h"
#include <cstdio>
#include <cstdlib>
#include <stdexcept>
#include <string_view>

static void require(bool ok, const char * message) {
    if (!ok) throw std::runtime_error(message);
}

int main(int argc, char ** argv) {
    try {
        require(argc >= 2 && argc <= 4,
                "usage: runtime_probe /absolute/path/libggml-gemmini.so [--rmd-on] [--q8_0]");
        bool rmd_on = false, q8_0 = false;
        for (int i = 2; i < argc; ++i) {
            const std::string_view option(argv[i]);
            require(option == "--rmd-on" || option == "--q8_0", "unknown expected profile option");
            if (option == "--rmd-on") rmd_on = true;
            else q8_0 = true;
        }
        unsetenv("IM2P_FPGA_DEVICE");
        unsetenv("IM2P_FPGA_TEST_PRODUCER_OVERLAP");
        unsetenv("IM2P_FPGA_TEST_WAIT_FIRST_READ");
        setenv("GEMMINI_MATMUL_MODE", "FULL", 1);
        auto reg = ggml_backend_load(argv[1]);
        require(reg, "standard backend dynamic load failed");
        require(ggml_backend_reg_dev_count(reg) == 1, "device count");
        auto dev = ggml_backend_reg_dev_get(reg, 0);
        auto stats_fn = (ggml_gemmini_fpga_stats_v1_fn) ggml_backend_reg_get_proc_address(
            reg, "ggml_gemmini_fpga_stats_v1");
        require(stats_fn, "FPGA-specific stats API missing");
        ggml_gemmini_fpga_stats_v1 stats{};
        require(!stats_fn(nullptr, sizeof(stats)), "null stats pointer accepted");
        require(!stats_fn(&stats, sizeof(stats) - 1), "wrong stats ABI size accepted");
        require(stats_fn(&stats, sizeof(stats)), "stats snapshot failed");
        require(stats.assigned == 0 && stats.attempted == 0 && stats.completed == 0 && stats.failed == 0,
                "metadata load executed arithmetic");

        ggml_init_params params{32 * 1024 * 1024, nullptr, false};
        auto * ctx = ggml_init(params);
        require(ctx, "context allocation failed");
        size_t cases = 0;
        auto supports = [&](ggml_type type, int64_t m, int64_t n, int64_t k, bool metadata, bool malformed = false) {
            ++cases;
            auto * w = ggml_new_tensor_2d(ctx, type, k, n);
            auto * a = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, k, m);
            auto * y = ggml_mul_mat(ctx, w, a);
            if (malformed) ++w->nb[0];
            ggml_set_name(y, metadata ? "loader_probe" : "real_graph_probe");
            void * data = w->data;
            if (metadata) {
                w->data = nullptr;
                w->buffer = ggml_backend_buft_alloc_buffer(ggml_backend_dev_buffer_type(dev), 0);
                require(w->buffer, "zero-size loader buffer");
            }
            const bool answer = ggml_backend_dev_supports_op(dev, y);
            if (metadata) {
                ggml_backend_buffer_free(w->buffer);
                w->buffer = nullptr;
                w->data = data;
            }
            return answer;
        };
        // Old FPGA code rejected this before recognizing llama's loader probe.
        require(supports(GGML_TYPE_Q8_H1, 512, 16, 64, true) == !rmd_on, "legacy loader eligibility differs from selected RMD profile");
        require(!supports(GGML_TYPE_Q8_H1, 512, 16, 64, false), "real M512 must stay rejected");
        require(supports(GGML_TYPE_Q8_H1, 16, 16, 64, false) == !rmd_on, "legacy FULL eligibility differs from selected RMD profile");
        require(supports(GGML_TYPE_Q8_H1, 321, 48, 96, false) == !rmd_on, "legacy long-shape eligibility differs from selected RMD profile");
        require(!supports(GGML_TYPE_Q8_H1, 16, 49, 64, true), "loader must reject static N capacity");
        require(!supports(GGML_TYPE_Q8_H1, 16, 16, 128, true), "loader must reject static K capacity");
        require(!supports(GGML_TYPE_Q8_0, 16, 16, 64, true), "Q8_0 must not masquerade as native H1");
        require(!supports(GGML_TYPE_Q8_HP1, 16, 16, 64, true), "FPGA HP1 must stay unsupported");
        setenv("GEMMINI_MATMUL_MODE", "STRIPE_PIPELINE", 1);
        require(supports(GGML_TYPE_Q8_H1, 1, 16, 64, false) == !rmd_on, "legacy live eligibility differs from selected RMD profile");
        // An explicit RTL selection changes logical eligibility without loading
        // the plugin. The deliberately missing file would make execution fail.
        setenv("IM2P_FPGA_DEVICE", "rtl:/nonexistent-im2p-probe.so", 1);
        require(supports(GGML_TYPE_Q8_H1, 512, 96, 192, false), "bounded logical H1 rejected");
        require(supports(GGML_TYPE_Q8_HP1, 16, 96, 192, false), "bounded native HP1 rejected");
        require(supports(GGML_TYPE_Q8_HP1, 512, 96, 192, true), "native HP1 loader rejected");
        if (q8_0) {
            for (const char *mode : {"FULL", "STRIPE_PIPELINE"}) {
                setenv("GEMMINI_MATMUL_MODE", mode, 1);
                setenv("GEMMINI_RMD_BACKEND", "CPU", 1);
                require(supports(GGML_TYPE_Q8_0, 16, 96, 192, true), "Q8_0 loader rejected existing row reprocessing");
                require(supports(GGML_TYPE_Q8_0, 16, 96, 192, false), "Q8_0 backed graph rejected existing row reprocessing");
                require(!supports(GGML_TYPE_Q8_0, 16, 96, 192, true, true), "Q8_0 malformed metadata layout accepted");
                setenv("GEMMINI_RMD_BACKEND", "WS", 1);
                require(supports(GGML_TYPE_Q8_0, 16, 96, 192, true) == !rmd_on, "Q8_0 H0 residual policy changed");
            }
            unsetenv("GEMMINI_RMD_BACKEND");
        }
        // Original Llama tied output: metadata queries need no weight allocation
        // or transport open, even beyond the former logical N/K=65536 cap.
        ggml_set_no_alloc(ctx, true);
        require(supports(GGML_TYPE_Q8_HP1, 512, 128256, 2048, true), "Llama tied output loader rejected");
        require(supports(GGML_TYPE_Q8_H1, 512, 96, 65568, true), "large logical K loader rejected");
        require(!supports(GGML_TYPE_Q8_HP1, 512, (1LL<<31)+1, 32, true), "unencodable N stride accepted");
        require(!supports(GGML_TYPE_Q8_HP1, 512, 16, (1LL<<31)+32, true), "unencodable K stride accepted");
        unsetenv("IM2P_FPGA_DEVICE");
        require(stats_fn(&stats, sizeof(stats)), "final stats snapshot");
        require(stats.assigned == 0 && stats.attempted == 0 && stats.completed == 0 && stats.failed == 0,
                "supports_op queried transport or executed arithmetic");
        ggml_free(ctx);
        std::printf("runtime_probe PASS metadata_cases=%zu load=standard_backend metadata_only=1 adapter_attempts=0 RMD=%s Q8_0=%d\n",
                    cases, rmd_on ? "ON" : "OFF", q8_0);
        return 0;
    } catch (const std::exception & error) {
        std::fprintf(stderr, "runtime_probe FAIL %s\n", error.what());
        return 1;
    }
}
