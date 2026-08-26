#include "im2p_gemmini_frontend.hpp"

#include <type_traits>

static_assert(!std::is_copy_constructible_v<im2p::gemmini::Run>);
static_assert(std::is_move_constructible_v<im2p::gemmini::ExecuteResult>);

constexpr im2p::gemmini::Options legacy_options{65536};
static_assert(legacy_options.max_stalled_cycles == 65536);
static_assert(legacy_options.residual_stage_mode ==
              im2p::gemmini::ResidualStageMode::none);
static_assert(legacy_options.residual_stage_context == nullptr);
static_assert(legacy_options.residual_stage_fn == nullptr);
static_assert(std::is_nothrow_invocable_r_v<
              im2p::gemmini::Status, im2p::gemmini::ResidualStageFn, void *,
              im2p_sim_t *,
              const ggml::gemmini::quants::act::exsia::StripeReadyEvent &,
              im2p::gemmini::ResidualStageView,
              im2p::gemmini::ResidualStripeStats &>);

int main() { return 0; }
