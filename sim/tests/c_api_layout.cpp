#include "im2p_sim.h"

#include <cstddef>
#include <cstdint>
#include <type_traits>

using WriteOutput = int (*)(void *, std::size_t, std::size_t, std::size_t,
                            std::size_t, const std::int64_t *);
using Execute = int (*)(im2p_sim_t *, const im2p_matmul_desc_t *,
                        im2p_work_stats_t *);
using Begin = int (*)(im2p_sim_t *, const im2p_stripe_work_desc_t *,
                      im2p_stream_t **);
using Publish = int (*)(im2p_stream_t *, const im2p_activation_stripe_t *);

static_assert(IM2P_ABI_VERSION == 4);
static_assert(std::is_same_v<im2p_write_output_fn, WriteOutput>);
static_assert(std::is_same_v<decltype(im2p_matmul_desc_t::output),
                             std::int32_t *>);
static_assert(std::is_same_v<decltype(im2p_stripe_work_desc_t::output),
                             std::int32_t *>);
static_assert(std::is_same_v<decltype(&im2p_execute_matmul), Execute>);
static_assert(std::is_same_v<decltype(&im2p_begin_striped_matmul), Begin>);
static_assert(std::is_same_v<decltype(&im2p_publish_stripe), Publish>);

int main() { return 0; }
