#include "im2p_sim.h"

#include <cstddef>
#include <cstdint>
#include <type_traits>

using WriteOutput = int (*)(void *, std::size_t, std::size_t, std::size_t,
                            std::size_t, const std::int64_t *, std::uint32_t);
using Execute = int (*)(im2p_sim_t *, const im2p_matmul_desc_t *,
                        im2p_work_stats_t *);
using Begin = int (*)(im2p_sim_t *, const im2p_stripe_work_desc_t *,
                      im2p_stream_t **);
using Publish = int (*)(im2p_stream_t *, const im2p_activation_stripe_t *);
using PollCompleted = int (*)(im2p_stream_t *, im2p_stripe_completion_t *);
using PollCompletedExtended = int (*)(im2p_stream_t *,
                                      im2p_stripe_completion_extended_t *);

static_assert(IM2P_ABI_VERSION == 5);
static_assert(IM2P_PRODUCTION_GEOMETRY_VERSION == 1);
static_assert(sizeof(im2p_production_geometry_v1_t) == 104);
static_assert(std::is_standard_layout_v<im2p_production_geometry_v1_t>);
static_assert(std::is_trivially_copyable_v<im2p_production_geometry_v1_t>);
static_assert(offsetof(im2p_production_geometry_v1_t, tile_k_count) == 64);
using ExecutePlanned = int (*)(im2p_sim_t *, const im2p_matmul_desc_t *,
    const im2p_production_geometry_v1_t *, im2p_work_stats_extended_t *);
using BeginPlanned = int (*)(im2p_sim_t *, const im2p_stripe_work_desc_t *,
    const im2p_production_geometry_v1_t *, im2p_stream_t **);
using PublishPlanned = int (*)(im2p_stream_t *, const im2p_activation_stripe_t *,
    const im2p_production_geometry_v1_t *);
static_assert(std::is_same_v<decltype(&im2p_execute_matmul_planned), ExecutePlanned>);
static_assert(std::is_same_v<decltype(&im2p_begin_striped_matmul_planned), BeginPlanned>);
static_assert(std::is_same_v<decltype(&im2p_publish_stripe_planned), PublishPlanned>);
static_assert(std::is_same_v<im2p_write_output_fn, WriteOutput>);
using ReadScale = int (*)(void *, std::size_t, std::size_t, std::size_t, std::uint32_t *);
static_assert(std::is_same_v<im2p_read_scale_fn, ReadScale>);
static_assert(std::is_same_v<decltype(im2p_matmul_desc_t::scales), const std::uint32_t *>);
static_assert(std::is_same_v<decltype(im2p_matmul_desc_t::output_domain), std::uint8_t>);
static_assert(std::is_same_v<decltype(im2p_matmul_desc_t::output),
                             std::int32_t *>);
static_assert(std::is_same_v<decltype(im2p_stripe_work_desc_t::output),
                             std::int32_t *>);
static_assert(std::is_same_v<decltype(&im2p_execute_matmul), Execute>);
static_assert(std::is_same_v<decltype(&im2p_begin_striped_matmul), Begin>);
static_assert(std::is_same_v<decltype(&im2p_publish_stripe), Publish>);
static_assert(std::is_same_v<decltype(&im2p_poll_completed), PollCompleted>);
static_assert(std::is_same_v<decltype(&im2p_poll_completed_extended),
                             PollCompletedExtended>);
static_assert(sizeof(im2p_stripe_completion_t) == 32);
static_assert(offsetof(im2p_stripe_completion_t, stripe_id) == 0);
static_assert(offsetof(im2p_stripe_completion_t, i_start) == 8);
static_assert(offsetof(im2p_stripe_completion_t, rows) == 16);
static_assert(offsetof(im2p_stripe_completion_t, context) == 24);
static_assert(offsetof(im2p_stripe_completion_extended_t, base) == 0);
static_assert(offsetof(im2p_stripe_completion_extended_t, publish_cycle) == 32);
static_assert(offsetof(im2p_stripe_completion_extended_t, completion_cycle) == 40);
static_assert(offsetof(im2p_stripe_completion_extended_t,
                       publish_to_completion_cycles) == 48);
static_assert(sizeof(im2p_stripe_completion_extended_t) == 56);

int main() { return 0; }
