#ifndef IM2P_GEOMETRY_H
#define IM2P_GEOMETRY_H

#include <stdint.h>

/* Additive geometry contract. Does not change numerical ABI v5 or cycle ABI v1.
 * Values are selected by the caller's production tiler, never by IM2P.
 * No tensor pointers, timing assumptions, or FPGA transport types belong here.
 */
#define IM2P_PRODUCTION_GEOMETRY_VERSION 1u

enum {
    IM2P_GEOMETRY_FULL = 0,
    IM2P_GEOMETRY_STREAM = 1,
    IM2P_GEOMETRY_STRIPE = 2,
};

typedef struct im2p_production_geometry_v1 {
    uint32_t version;
    uint32_t struct_size;
    uint32_t activation_bits;
    uint32_t weight_bits;
    uint32_t dim;
    uint32_t scope;
    /* Whole operation shape, in elements, equal to the numerical descriptor. */
    uint64_t m, n, k;
    /* Positive, already-selected factors; each unit is DIM elements. */
    uint64_t tile_i_count, tile_j_count, tile_k_count;
    /* Explicit producer stripe height in rows. No zero/default convention. */
    uint64_t stripe_rows;
    /* FULL/STREAM: row_begin=0, row_count=m, stripe_id=0.
     * STRIPE: the exact publication range/id within the whole operation.
     * Every STRIPE carries its final dispatch factors, even when unchanged.
     */
    uint64_t row_begin, row_count, stripe_id;
} im2p_production_geometry_v1_t;

/* Layout and strides remain in the accompanying numerical descriptor / stripe:
 * activation/weight strides are bytes; output/scale strides count elements.
 * The runtime validates them there. These legacy fields are NOT tile factors.
 * Version 1 is admitted only by the integrated Gemmini HP1 A4/A8 DIM16/32/64
 * implementation. Legacy APIs without a companion retain their old behavior
 * and do not establish production-schedule provenance.
 */
#endif
