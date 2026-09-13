#pragma once
// Test-only transport for the unchanged model replay reader/frontend.
// Included after its Observation definition; no numerical implementation here.
#include <cstdlib>
#include <dlfcn.h>
static bool semantic_trace_plugin_active = false;
extern "C" im2p_sim_t *__real_im2p_sim_create();
extern "C" im2p_sim_t *__wrap_im2p_sim_create() {
    if (semantic_trace_plugin_active) {
        std::fprintf(stderr, "SEMANTIC_TRACE_FORBIDDEN_SIM_CREATE\n");
        std::abort();
    }
    return __real_im2p_sim_create();
}
#if IM2P_MODEL_REPLAY_MAIN_EXTERNAL
#include "rtl_plugin.hpp"
struct SemanticTraceTransport {
    void *library = nullptr, *device = nullptr;
    const im2p_scu_rtl_api_v1 *api = nullptr;
    size_t stripe_rows;
    StreamExecutor executor{this, begin, publish, poll, finish};
    explicit SemanticTraceTransport(const char *path, size_t rows) : stripe_rows(rows) {
        library = dlopen(path, RTLD_NOW | RTLD_LOCAL);
        require(library, "trace RTL plugin load failed");
        const auto get = reinterpret_cast<im2p_scu_rtl_get_api_v1_fn>(dlsym(library, "im2p_scu_rtl_get_api_v1"));
        require(get, "trace RTL plugin API missing");
        api = get();
        require(api && api->struct_size == sizeof(*api) && api->version == 1 && api->abi_version == IM2P_ABI_VERSION &&
                api->activation_bits == 8 && api->weight_bits == 8 && api->dim == 16 &&
                api->vector_op == IM2P_VECTOR_EXTERNAL && api->output_domain == IM2P_OUTPUT_LEGACY_BLOCK,
                "trace RTL plugin contract mismatch");
        device = api->create();
        require(device, "trace RTL plugin create failed");
        semantic_trace_plugin_active = true;
    }
    ~SemanticTraceTransport() { if (device) api->destroy(device); if (library) dlclose(library); }
    static int full(void *opaque, const im2p_matmul_desc_t *desc, im2p_work_stats_extended_t *stats) {
        auto &self = *static_cast<SemanticTraceTransport *>(opaque);
        if (!active || !desc || !active->begin(*desc)) return IM2P_ERROR;
        auto copy = *desc; copy.provider = active->provider();
        return self.api->full(self.device, &copy, stats);
    }
    static int begin(void *opaque, const im2p_stripe_work_desc_t *desc) {
        auto &self = *static_cast<SemanticTraceTransport *>(opaque);
        if (!active || !desc || !active->begin(*desc)) return IM2P_ERROR;
        auto copy = *desc; copy.provider = active->provider();
        return self.api->begin(self.device, &copy, self.stripe_rows);
    }
    static int publish(void *opaque, const im2p_activation_stripe_t *stripe) {
        auto &self = *static_cast<SemanticTraceTransport *>(opaque);
        return self.api->publish(self.device, stripe);
    }
    static int poll(void *opaque, im2p_stripe_completion_extended_t *completion) {
        auto &self = *static_cast<SemanticTraceTransport *>(opaque);
        return self.api->poll(self.device, completion);
    }
    static int finish(void *opaque, im2p_work_stats_extended_t *stats) {
        auto &self = *static_cast<SemanticTraceTransport *>(opaque);
        return self.api->finish(self.device, stats);
    }
    void install(Options &options, Mode mode) {
        if (mode == Mode::full) { options.full_executor = full; options.full_executor_context = this; }
        else options.stream_executor = &executor;
    }
};
#endif
