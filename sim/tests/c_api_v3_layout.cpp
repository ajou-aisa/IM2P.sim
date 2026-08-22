#include "im2p_sim.h"

#include <cstddef>
#include <cstdint>
#include <type_traits>

using WriteOutputV3 = int (*)(void *, std::size_t, std::size_t, std::size_t,
                              std::size_t, const std::int64_t *);

static_assert(IM2P_ABI_VERSION_3 == 3);
static_assert(std::is_same_v<im2p_write_output_v3_fn, WriteOutputV3>);
static_assert(std::is_same_v<decltype(im2p_matmul_desc_v3_t::output),
                             std::int32_t *>);
static_assert(std::is_same_v<decltype(im2p_stripe_work_desc_v3_t::output),
                             std::int32_t *>);
static_assert(sizeof(im2p_matmul_desc_v2_t) == 208);
static_assert(offsetof(im2p_matmul_desc_v2_t, provider) == 176);
static_assert(sizeof(im2p_stripe_work_desc_v2_t) == 200);
static_assert(offsetof(im2p_stripe_work_desc_v2_t, provider) == 168);
static_assert(sizeof(im2p_activation_stripe_v2_t) == 64);

using ExecuteV3 = int (*)(im2p_sim_t *, const im2p_matmul_desc_v3_t *,
                          im2p_work_stats_t *);
using BeginV3 = int (*)(im2p_sim_t *, const im2p_stripe_work_desc_v3_t *,
                        im2p_stream_t **);
using PublishV3 = int (*)(im2p_stream_t *,
                          const im2p_activation_stripe_v3_t *);
static_assert(std::is_same_v<decltype(&im2p_execute_matmul_v3), ExecuteV3>);
static_assert(std::is_same_v<decltype(&im2p_begin_striped_matmul_v3), BeginV3>);
static_assert(std::is_same_v<decltype(&im2p_publish_stripe_v3), PublishV3>);

int main() { return 0; }
