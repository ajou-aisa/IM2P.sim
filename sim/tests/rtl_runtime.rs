pub mod common;

use common::{
    assert_matrix_eq, run_case, structured_activations, structured_weights, Case,
    KBlockScaleMatrix, Shape,
};
use im2p_sim::{Im2pSimulator, SimError, VectorOp};

fn execute_mode(
    simulator: &mut Im2pSimulator,
    shape: Shape,
    scales: &KBlockScaleMatrix,
    context: u64,
    operation: VectorOp,
) -> Result<common::RunResult, SimError> {
    run_case(
        simulator,
        Case {
            shape,
            activations: &structured_activations(shape),
            weights: &structured_weights(shape),
            scales: (operation != VectorOp::Bypass).then_some(scales),
            column_offset: 0,
            valid_columns: shape.n,
            context,
            operation,
        },
    )
}

#[test]
fn bypass_multiply_shift_bypass_share_one_core() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let shape = Shape { m: 3, n: 4, k: 12 };
    let scales = KBlockScaleMatrix::from_fn(shape.k, shape.k, shape.n, |_, column| {
        [-2, -1, 1, 2][column]
    });
    for operation in [
        VectorOp::Bypass,
        VectorOp::Multiply,
        VectorOp::Shift,
        VectorOp::Bypass,
    ] {
        let result = execute_mode(&mut simulator, shape, &scales, 70, operation)?;
        assert_matrix_eq(&result.output, &result.expected, shape.m, shape.n);
    }
    Ok(())
}

#[test]
fn bypass_does_not_invalidate_scale_context() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let shape = Shape { m: 2, n: 4, k: 10 };
    let scales = KBlockScaleMatrix::from_fn(shape.k, shape.k, shape.n, |_, column| {
        [-2, -1, 1, 2][column]
    });
    let multiply = execute_mode(&mut simulator, shape, &scales, 71, VectorOp::Multiply)?;
    let bypass = execute_mode(&mut simulator, shape, &scales, 0, VectorOp::Bypass)?;
    let shift = execute_mode(&mut simulator, shape, &scales, 71, VectorOp::Shift)?;
    assert_eq!(multiply.stats[0].scale_fetch.demand_misses, 1);
    assert_eq!(bypass.stats[0].scale_fetch, Default::default());
    assert_eq!(shift.stats[0].scale_fetch.current_hits, 1);
    Ok(())
}
