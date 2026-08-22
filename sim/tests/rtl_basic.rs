pub mod common;

use common::{assert_matrix_eq, run_case, structured_activations, structured_weights, Case, Shape};
use im2p_sim::{ActivationValue, Im2pSimulator, SimError, VectorOp};

fn run_bypass(
    simulator: &mut Im2pSimulator,
    shape: Shape,
    activations: &[ActivationValue],
    weights: &[i8],
) -> Result<(), SimError> {
    let result = run_case(
        simulator,
        Case {
            shape,
            activations,
            weights,
            scales: None,
            column_offset: 0,
            valid_columns: shape.n,
            context: 0,
            operation: VectorOp::Bypass,
        },
    )?;
    assert_matrix_eq(&result.output, &result.expected, shape.m, shape.n);
    assert!(result
        .stats
        .iter()
        .all(|stats| stats.scale_fetch == Default::default()));
    Ok(())
}

#[test]
fn bypass_basic_matches_cpu() -> Result<(), SimError> {
    let shape = Shape { m: 2, n: 3, k: 4 };
    run_bypass(
        &mut Im2pSimulator::new()?,
        shape,
        &structured_activations(shape),
        &structured_weights(shape),
    )
}

#[test]
fn bypass_signed_inputs_match_cpu() -> Result<(), SimError> {
    let shape = Shape { m: 2, n: 3, k: 4 };
    let activations = [-4, 3, -2, 1, 5, -6, 7, -8];
    let weights = [2, -3, 4, -5, 6, -7, 1, -2, 3, -4, 5, -6];
    run_bypass(&mut Im2pSimulator::new()?, shape, &activations, &weights)
}

#[test]
fn zero_activations_produce_zero() -> Result<(), SimError> {
    let shape = Shape { m: 3, n: 4, k: 7 };
    run_bypass(
        &mut Im2pSimulator::new()?,
        shape,
        &vec![0; shape.m * shape.k],
        &structured_weights(shape),
    )
}

#[test]
fn zero_weights_produce_zero() -> Result<(), SimError> {
    let shape = Shape { m: 3, n: 4, k: 7 };
    run_bypass(
        &mut Im2pSimulator::new()?,
        shape,
        &structured_activations(shape),
        &vec![0; shape.k * shape.n],
    )
}

#[test]
fn full_tile_matches_cpu() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let shape = Shape {
        m: dim,
        n: dim,
        k: dim,
    };
    run_bypass(
        &mut simulator,
        shape,
        &structured_activations(shape),
        &structured_weights(shape),
    )
}

#[test]
fn m_n_k_tails_match_cpu() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let shape = Shape {
        m: 3,
        n: 5,
        k: simulator.dim() + 3,
    };
    run_bypass(
        &mut simulator,
        shape,
        &structured_activations(shape),
        &structured_weights(shape),
    )
}
