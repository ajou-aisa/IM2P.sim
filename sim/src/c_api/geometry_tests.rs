use super::*;
use crate::production_geometry::{ProductionGeometry, GEOMETRY_FULL, GEOMETRY_STREAM};
use std::mem::{size_of, MaybeUninit};

fn geometry(scope: u32) -> ProductionGeometry {
    ProductionGeometry {
        version: 1,
        struct_size: size_of::<ProductionGeometry>() as u32,
        activation_bits: crate::ACTIVATION_BITS as u32,
        weight_bits: crate::WEIGHT_BITS as u32,
        dim: crate::profile::IM2P_DIM as u32,
        scope,
        m: 2,
        n: 3,
        k: 64,
        tile_i_count: 1,
        tile_j_count: 1,
        tile_k_count: 2,
        stripe_rows: 2,
        row_begin: 0,
        row_count: 2,
        stripe_id: 0,
    }
}

unsafe fn descriptor() -> MatmulDescC {
    let mut d: MatmulDescC = std::mem::zeroed();
    d.abi_version = ABI_VERSION;
    d.activation_bits = crate::ACTIVATION_BITS as u32;
    d.activation_storage_bytes = 1;
    d.weight_bits = crate::WEIGHT_BITS as u32;
    d.weight_storage_bytes = 1;
    d.dim = configured_dim();
    d.m = 2;
    d.n = 3;
    d.k = 64;
    d
}

#[test]
fn production_geometry_invalid_companions_preserve_output_and_owner() {
    unsafe {
        let sim = im2p_sim_create();
        assert!(!sim.is_null());
        let d = descriptor();
        let g = geometry(GEOMETRY_FULL);
        let mut cases = Vec::new();
        let mut bad = g;
        bad.tile_i_count = 0;
        cases.push((bad, -4));
        bad = g;
        bad.tile_j_count = 0;
        cases.push((bad, -4));
        bad = g;
        bad.tile_k_count = 0;
        cases.push((bad, -4));
        bad = g;
        bad.tile_k_count = u64::MAX;
        cases.push((bad, -4));
        bad = g;
        bad.tile_i_count = u64::MAX;
        cases.push((bad, -4));
        bad = g;
        bad.version = 2;
        cases.push((bad, -4));
        bad = g;
        bad.struct_size -= 1;
        cases.push((bad, -4));
        bad = g;
        bad.m += 1;
        cases.push((bad, -4));
        bad = g;
        bad.k += 1;
        cases.push((bad, -4));
        bad = g;
        bad.scope = GEOMETRY_STREAM;
        cases.push((bad, -4));
        bad = g;
        bad.stripe_rows = 0;
        cases.push((bad, -4));
        bad = g;
        bad.row_count = 1;
        cases.push((bad, -4));
        bad = g;
        bad.weight_bits = 16;
        cases.push((bad, -7));
        bad = g;
        bad.dim = 8;
        cases.push((bad, -7));
        for (bad, expected) in cases {
            let mut output = MaybeUninit::<WorkStatsExtendedC>::uninit();
            ptr::write_bytes(output.as_mut_ptr(), 0x5a, 1);
            assert_eq!(
                im2p_execute_matmul_planned(sim, &d, &bad, output.as_mut_ptr()),
                expected
            );
            assert!(std::slice::from_raw_parts(
                output.as_ptr().cast::<u8>(),
                size_of::<WorkStatsExtendedC>()
            )
            .iter()
            .all(|&b| b == 0x5a));
            assert!((*sim).simulator.borrow().is_some());
        }
        assert_eq!(
            im2p_execute_matmul_planned(sim, &d, ptr::null(), ptr::null_mut()),
            -4
        );
        im2p_sim_destroy(sim);
    }
}

#[test]
fn production_geometry_bad_stream_keeps_simulator_and_clears_output() {
    unsafe {
        let sim = im2p_sim_create();
        assert!(!sim.is_null());
        let base = descriptor();
        let mut d: types::StripeWorkDescC = std::mem::zeroed();
        d.abi_version = base.abi_version;
        d.activation_bits = base.activation_bits;
        d.activation_storage_bytes = base.activation_storage_bytes;
        d.weight_bits = base.weight_bits;
        d.weight_storage_bytes = base.weight_storage_bytes;
        d.dim = base.dim;
        d.m = 2;
        d.n = 3;
        d.k = 64;
        d.stripe_count = 1;
        let mut g = geometry(GEOMETRY_STREAM);
        g.tile_k_count = 0;
        let mut output = ptr::dangling_mut::<types::StreamBox>();
        assert_eq!(
            stream::im2p_begin_striped_matmul_planned(sim, &d, &g, &mut output),
            -4
        );
        assert!(output.is_null());
        assert!((*sim).simulator.borrow().is_some());
        im2p_sim_destroy(sim);
    }
}
