//! Additive scalar-only transport for already-selected production geometry.
//! This module validates units/identity; it never chooses tile factors.

#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ProductionGeometry {
    pub version: u32,
    pub struct_size: u32,
    pub activation_bits: u32,
    pub weight_bits: u32,
    pub dim: u32,
    pub scope: u32,
    pub m: u64,
    pub n: u64,
    pub k: u64,
    pub tile_i_count: u64,
    pub tile_j_count: u64,
    pub tile_k_count: u64,
    pub stripe_rows: u64,
    pub row_begin: u64,
    pub row_count: u64,
    pub stripe_id: u64,
}

pub const GEOMETRY_FULL: u32 = 0;
pub const GEOMETRY_STREAM: u32 = 1;
pub const GEOMETRY_STRIPE: u32 = 2;

impl ProductionGeometry {
    pub(crate) fn validate(&self, m: usize, n: usize, k: usize, scope: u32) -> Result<(), i32> {
        if self.version != 1 || self.struct_size as usize != std::mem::size_of::<Self>() {
            return Err(-4);
        }
        if self.activation_bits != crate::ACTIVATION_BITS as u32
            || self.weight_bits != crate::WEIGHT_BITS as u32
            || self.dim != crate::profile::IM2P_DIM as u32
            || !matches!(self.activation_bits, 4 | 8)
            || self.activation_bits != self.weight_bits
            || !matches!(self.dim, 16 | 32 | 64)
        {
            return Err(-7);
        }
        if self.scope != scope
            || !matches!(scope, GEOMETRY_FULL | GEOMETRY_STREAM | GEOMETRY_STRIPE)
            || (self.m, self.n, self.k) != (m as u64, n as u64, k as u64)
            || m == 0
            || n == 0
            || k == 0
            || [self.m, self.n, self.k, self.stripe_rows]
                .iter()
                .any(|&x| x == 0 || x > u32::MAX as u64)
            || self.tile_i_count == 0
            || self.tile_j_count == 0
            || self.tile_k_count == 0
            || self.tile_i_count > u16::MAX as u64 / self.dim as u64
            || self.tile_j_count > u16::MAX as u64 / self.dim as u64
            || self.tile_k_count > u32::MAX as u64 / self.dim as u64
        {
            return Err(-4);
        }
        if scope == GEOMETRY_STRIPE {
            if self.row_count == 0
                || self.row_count > self.stripe_rows
                || self.row_begin >= self.m
                || self.row_count > self.m - self.row_begin
                || self.stripe_id > u32::MAX as u64
            {
                return Err(-4);
            }
        } else if self.row_begin != 0 || self.row_count != self.m || self.stripe_id != 0 {
            return Err(-4);
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::mem::{offset_of, size_of};

    fn valid() -> ProductionGeometry {
        ProductionGeometry {
            version: 1,
            struct_size: size_of::<ProductionGeometry>() as u32,
            activation_bits: crate::ACTIVATION_BITS as u32,
            weight_bits: crate::WEIGHT_BITS as u32,
            dim: crate::profile::IM2P_DIM as u32,
            scope: GEOMETRY_FULL,
            m: 129,
            n: 129,
            k: 96,
            tile_i_count: 2,
            tile_j_count: 4,
            tile_k_count: 3,
            stripe_rows: 64,
            row_begin: 0,
            row_count: 129,
            stripe_id: 0,
        }
    }
    #[test]
    fn production_geometry_layout_is_additive_and_scalar_only() {
        assert_eq!(size_of::<ProductionGeometry>(), 104);
        assert_eq!(offset_of!(ProductionGeometry, m), 24);
        assert_eq!(offset_of!(ProductionGeometry, tile_i_count), 48);
        assert_eq!(offset_of!(ProductionGeometry, tile_k_count), 64);
        assert_eq!(offset_of!(ProductionGeometry, stripe_id), 96);
    }
    #[test]
    fn production_geometry_verbatim_factors_and_fail_closed_admission() {
        if crate::ACTIVATION_BITS == 16 {
            return;
        }
        let g = valid();
        assert_eq!(g.validate(129, 129, 96, GEOMETRY_FULL), Ok(()));
        assert_eq!((g.tile_i_count, g.tile_j_count, g.tile_k_count), (2, 4, 3));
        let mut bad = g;
        bad.tile_k_count = 0;
        assert_eq!(bad.validate(129, 129, 96, GEOMETRY_FULL), Err(-4));
        bad = g;
        bad.tile_i_count = u64::MAX;
        assert_eq!(bad.validate(129, 129, 96, GEOMETRY_FULL), Err(-4));
        bad = g;
        bad.version = 2;
        assert_eq!(bad.validate(129, 129, 96, GEOMETRY_FULL), Err(-4));
        bad = g;
        bad.struct_size -= 1;
        assert_eq!(bad.validate(129, 129, 96, GEOMETRY_FULL), Err(-4));
        bad = g;
        bad.weight_bits = 16;
        assert_eq!(bad.validate(129, 129, 96, GEOMETRY_FULL), Err(-7));
        assert_eq!(g.validate(128, 129, 96, GEOMETRY_FULL), Err(-4));
        assert_eq!(g.validate(129, 129, 96, GEOMETRY_STREAM), Err(-4));
    }
    #[test]
    fn production_geometry_stripe_identity_has_explicit_bounds() {
        if crate::ACTIVATION_BITS == 16 {
            return;
        }
        let mut g = valid();
        g.scope = GEOMETRY_STRIPE;
        g.row_begin = 128;
        g.row_count = 1;
        g.stripe_id = 2;
        assert_eq!(g.validate(129, 129, 96, GEOMETRY_STRIPE), Ok(()));
        g.row_count = 2;
        assert_eq!(g.validate(129, 129, 96, GEOMETRY_STRIPE), Err(-4));
        g.row_count = 1;
        g.stripe_id = u64::MAX;
        assert_eq!(g.validate(129, 129, 96, GEOMETRY_STRIPE), Err(-4));
    }
}
