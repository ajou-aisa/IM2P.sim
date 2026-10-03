#ifndef IM2P_COMPACT_RUNS_H
#define IM2P_COMPACT_RUNS_H

#include <stddef.h>
#include <stdint.h>

#define IM2P_COMPACT_RUNS_VERSION 1u

/* Each nonempty run owns surviving columns from one original 32-K block. */
typedef struct im2p_compact_run {
    uint32_t original_block_id;
    uint32_t original_k_mask;
    uint32_t compact_k_begin;
    uint32_t compact_k_count;
} im2p_compact_run_t;

/* Scalar metadata only; the caller retains this storage until admission. */
typedef struct im2p_compact_runs {
    uint32_t version;
    uint32_t struct_size;
    uint32_t original_k;
    size_t run_count;
    const im2p_compact_run_t *runs;
} im2p_compact_runs_t;

#endif
