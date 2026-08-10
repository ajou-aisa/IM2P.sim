pub mod common;

use common::{
    assert_matrix_eq, run_case, structured_activations, structured_weights, Case,
    KBlockScaleMatrix, Shape,
};
use im2p_sim::{Im2pSimulator, SimError, VectorOp};

fn sequential_case(
    simulator: &mut Im2pSimulator,
    blocks: usize,
    context: u64,
) -> Result<common::RunResult, SimError> {
    let dim = simulator.dim();
    let shape = Shape {
        m: 2,
        n: 4,
        k: blocks * dim,
    };
    let scales = KBlockScaleMatrix::from_fn(shape.k, dim, shape.n, |block, column| {
        ((3 * block + column) % 7) as i8 - 3
    });
    run_case(
        simulator,
        Case {
            shape,
            activations: &structured_activations(shape),
            weights: &structured_weights(shape),
            scales: Some(&scales),
            column_offset: 0,
            valid_columns: shape.n,
            context,
            operation: VectorOp::Multiply,
        },
    )
}

#[test]
fn next_blocks_are_prefetched_and_hit() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let result = sequential_case(&mut simulator, 4, 50)?;
    assert_eq!(result.stats[0].scale_fetch.demand_misses, 1);
    for stats in &result.stats[1..] {
        assert_eq!(stats.scale_fetch.next_hits, 1);
        assert_eq!(stats.scale_fetch.demand_misses, 0);
    }
    assert_matrix_eq(&result.output, &result.expected, 2, 4);
    Ok(())
}

#[test]
fn sequential_blocks_transfer_each_row_once() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let result = sequential_case(&mut simulator, 4, 51)?;
    let rows: u64 = result
        .stats
        .iter()
        .map(|stats| stats.scale_fetch.rows_received)
        .sum();
    let demand: u64 = result
        .stats
        .iter()
        .map(|stats| stats.scale_fetch.demand_requests)
        .sum();
    let prefetch: u64 = result
        .stats
        .iter()
        .map(|stats| stats.scale_fetch.prefetch_requests)
        .sum();
    let fetches = result
        .stats
        .iter()
        .map(|stats| stats.scale_fetch)
        .collect::<Vec<_>>();
    assert_eq!(rows, 4, "per-fragment fetches: {fetches:?}");
    assert_eq!(demand, 1);
    assert_eq!(prefetch, 3);
    Ok(())
}

#[test]
fn last_block_does_not_prefetch_past_matrix() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let result = sequential_case(&mut simulator, 3, 52)?;
    assert_eq!(
        result
            .stats
            .last()
            .expect("last block")
            .scale_fetch
            .prefetch_requests,
        0
    );
    Ok(())
}

#[test]
fn context_change_rejects_stale_next_row() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let first = sequential_case(&mut simulator, 2, 60)?;
    let changed = sequential_case(&mut simulator, 2, 61)?;
    assert_eq!(first.stats[0].scale_fetch.demand_misses, 1);
    assert_eq!(changed.stats[0].scale_fetch.demand_misses, 1);
    Ok(())
}
