#include "im2p_sim.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>

// Raw-core guard regression, not a native Q8_H1 K-alignment claim.
int main(void) {
    if (strcmp(im2p_compiled_numerical_semantics_revision(), IM2P_SCU_NUMERICAL_REVISION)) return 2;
    im2p_sim_t *sim = im2p_sim_create();
    if (!sim) return 2;
    int8_t a[9], w = 3;
    int32_t output[9];
    im2p_matmul_desc_t d = {
        .abi_version = IM2P_ABI_VERSION, .activation_bits = 8, .activation_storage_bytes = 1,
        .weight_bits = 8, .weight_storage_bytes = 1, .dim = 16,
        .activations = a, .weights = &w, .output = output,
        .m = 9, .n = 1, .k = 1, .activation_row_stride_bytes = 1,
        .weight_row_stride_bytes = 1, .output_row_stride = 1,
        .tile_i_rows = 9, .tile_j_columns = 1, .block_size = 1,
        .vector_op = IM2P_VECTOR_BYPASS, .output_domain = IM2P_OUTPUT_LEGACY_FINAL,
    };
    for (unsigned job = 0; job < 4; ++job) {
        for (unsigned row = 0; row < 9; ++row) { a[row] = (int)row - 4 + (int)job; output[row] = 0x13579; }
        d.work_context = job + 1;
        im2p_work_stats_extended_t stats = {0};
        if (im2p_execute_matmul_extended(sim, &d, &stats) != IM2P_OK) return 3;
        for (unsigned row = 0; row < 9; ++row) if (output[row] != 3 * (int)a[row]) return 4;
        if (stats.base.completed_fragments != 1 || stats.base.completed_output_tiles != 1 ||
            stats.base.output_write_requests != 9 || stats.base.output_write_responses != 9) return 5;
        printf("RAW_M9_JOB_PASS job=%u scalars=9 fragments=1 works=1 writes=9 acks=9 cycles=%llu\n",
               job, (unsigned long long)stats.base.work_total_cycles);
    }
    im2p_sim_destroy(sim);
    puts("RAW_M9_PASS logical=4 raw=36 native_Q8_H1=false passive_monitor=false");
    return 0;
}
