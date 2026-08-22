pub mod common;

use common::{assert_matrix_eq, run_case, Case, KBlockScaleMatrix, Lcg, Shape};
use im2p_sim::{parse_activation, Im2pSimulator, SimError, VectorOp};

fn random_case(operation: VectorOp, block_size: usize, seed: u32) -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let shape = Shape {
        m: 3,
        n: 5,
        k: simulator.dim() * 3 + 5,
    };
    let mut random = Lcg::new(seed);
    let activations = (0..shape.m * shape.k)
        .map(|_| parse_activation(i32::from(random.signed(-7, 7))).expect("bounded activation"))
        .collect::<Vec<_>>();
    let weights = (0..shape.k * shape.n)
        .map(|_| random.signed(-6, 6))
        .collect::<Vec<_>>();
    let scales =
        KBlockScaleMatrix::from_fn(shape.k, block_size, shape.n, |_, _| random.signed(-3, 3));
    let result = run_case(
        &mut simulator,
        Case {
            shape,
            activations: &activations,
            weights: &weights,
            scales: Some(&scales),
            column_offset: 0,
            valid_columns: shape.n,
            context: u64::from(seed),
            operation,
        },
    )?;
    assert_matrix_eq(&result.output, &result.expected, shape.m, shape.n);
    Ok(())
}

#[test]
fn deterministic_random_multiply_matches_cpu() -> Result<(), SimError> {
    random_case(VectorOp::Multiply, 8, 0x1234_5678)
}

#[test]
fn deterministic_random_shift_matches_cpu() -> Result<(), SimError> {
    random_case(VectorOp::Shift, 32, 0x8765_4321)
}

#[test]
fn deterministic_random_large_blocks_match_cpu() -> Result<(), SimError> {
    random_case(VectorOp::Multiply, 64, 0x0bad_f00d)
}
