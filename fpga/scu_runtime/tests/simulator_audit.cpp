// Test-only interposer. Real calls forward unchanged; forbidden calls fail.
#include "im2p_sim.h"
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <dlfcn.h>
#include <unistd.h>

static std::atomic<unsigned> creates{0}, fulls{0}, streams{0};
static void observed(std::atomic<unsigned> &counter, const char *name) {
    ++counter;
    if (std::getenv("IM2P_TEST_SIM_FORBIDDEN")) {
        std::fprintf(stderr, "SIMULATOR_AUDIT forbidden_call=%s\n", name);
        _exit(90);
    }
}
template<class T> static T next(const char *name) {
    auto result = reinterpret_cast<T>(dlsym(RTLD_NEXT, name));
    if (!result) {
        std::fprintf(stderr, "SIMULATOR_AUDIT unresolved=%s\n", name);
        _exit(91);
    }
    return result;
}
extern "C" im2p_sim_t *im2p_sim_create() {
    observed(creates, __func__);
    return next<decltype(&im2p_sim_create)>(__func__)();
}
extern "C" int im2p_execute_matmul_extended(im2p_sim_t *sim,
    const im2p_matmul_desc_t *desc, im2p_work_stats_extended_t *stats) {
    observed(fulls, __func__);
    return next<decltype(&im2p_execute_matmul_extended)>(__func__)(sim, desc, stats);
}
extern "C" int im2p_begin_striped_matmul(im2p_sim_t *sim,
    const im2p_stripe_work_desc_t *desc, im2p_stream_t **stream) {
    observed(streams, __func__);
    return next<decltype(&im2p_begin_striped_matmul)>(__func__)(sim, desc, stream);
}
__attribute__((destructor)) static void report() {
    std::fprintf(stderr, "SIMULATOR_AUDIT creates=%u fulls=%u streams=%u\n",
                 creates.load(), fulls.load(), streams.load());
}
