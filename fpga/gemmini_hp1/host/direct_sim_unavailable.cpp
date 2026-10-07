// llama declares its RMD executor only under GGML_GEMMINI_EXECUTION_BACKEND_IM2P_SIM,
// which also compiles its direct-simulator fallbacks. The HP1 host always passes an
// external executor and links no simulator, so those fallbacks are unreachable here.
// Reaching one fails closed instead of running anything.
#include <im2p_sim.h>

int im2p_execute_matmul_extended(im2p_sim_t *, const im2p_matmul_desc_t *,
                                 im2p_work_stats_extended_t *) {
  return IM2P_ERROR;
}

int im2p_execute_matmul_planned(im2p_sim_t *, const im2p_matmul_desc_t *,
                                const im2p_production_geometry_v1_t *,
                                im2p_work_stats_extended_t *) {
  return IM2P_ERROR;
}

int im2p_execute_matmul_planned_runs(im2p_sim_t *, const im2p_matmul_desc_t *,
                                     const im2p_production_geometry_v1_t *,
                                     const im2p_compact_runs_t *,
                                     im2p_work_stats_extended_t *) {
  return IM2P_ERROR;
}
