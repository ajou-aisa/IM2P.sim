pub mod common;

use common::{
    run_case, structured_activations, structured_weights, Case, KBlockScaleMatrix, Shape,
};
use im2p_sim::{Im2pSimulator, SimError, VectorOp};

fn scaled_result() -> Result<common::RunResult, SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let shape = Shape {
        m: 2,
        n: 4,
        k: 3 * dim,
    };
    let scales = KBlockScaleMatrix::from_fn(shape.k, dim, shape.n, |block, column| {
        (block + column + 1) as i8
    });
    run_case(
        &mut simulator,
        Case {
            shape,
            activations: &structured_activations(shape),
            weights: &structured_weights(shape),
            scales: Some(&scales),
            column_offset: 0,
            valid_columns: shape.n,
            context: 80,
            operation: VectorOp::Multiply,
        },
    )
}

#[test]
fn cycle_fields_are_tile_local_and_nonzero() -> Result<(), SimError> {
    let result = scaled_result()?;
    for stats in &result.stats {
        assert!(stats.weight_load_cycles > 0);
        assert!(stats.compute_cycles > 0);
        assert!(stats.total_cycles >= stats.weight_load_cycles + stats.compute_cycles);
        assert_eq!(
            stats.scale_fetch.scale_transfer_cycles,
            stats.scale_fetch.rows_received
        );
    }
    Ok(())
}

#[test]
fn request_hit_miss_and_wait_stats_are_observable() -> Result<(), SimError> {
    let result = scaled_result()?;
    assert_eq!(result.stats[0].scale_fetch.demand_requests, 1);
    assert_eq!(result.stats[0].scale_fetch.demand_misses, 1);
    assert!(result.stats[0].scale_fetch.scale_wait_cycles > 0);
    assert_eq!(result.stats[1].scale_fetch.next_hits, 1);
    assert_eq!(result.stats[2].scale_fetch.next_hits, 1);
    Ok(())
}

#[test]
fn useful_work_and_utilization_invariants_hold() -> Result<(), SimError> {
    let result = scaled_result()?;
    for (fragment, stats) in result.fragments.iter().zip(&result.stats) {
        let expected_macs = 2_u64 * 4 * fragment.count as u64;
        assert_eq!(stats.useful_macs, expected_macs);
        assert_eq!(stats.useful_ops, 2 * expected_macs);
        assert!(stats.macs_per_cycle >= 0.0);
        assert!(stats.ops_per_cycle >= stats.macs_per_cycle);
        assert!((0.0..=1.0).contains(&stats.utilization));
    }
    Ok(())
}

#[test]
fn bypass_has_zero_scale_fetch_stats() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let shape = Shape { m: 2, n: 3, k: 7 };
    let result = run_case(
        &mut simulator,
        Case {
            shape,
            activations: &structured_activations(shape),
            weights: &structured_weights(shape),
            scales: None,
            column_offset: 0,
            valid_columns: shape.n,
            context: 0,
            operation: VectorOp::Bypass,
        },
    )?;
    assert!(result
        .stats
        .iter()
        .all(|stats| stats.scale_fetch == Default::default()));
    Ok(())
}
