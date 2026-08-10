pub mod common;

use common::{
    assert_matrix_eq, run_case, structured_activations, structured_weights, Case,
    KBlockScaleMatrix, Shape,
};
use im2p_sim::{Im2pSimulator, SimError, VectorOp};

fn run_scaled(
    operation: VectorOp,
    shape: Shape,
    scales: &KBlockScaleMatrix,
) -> Result<(), SimError> {
    let result = run_case(
        &mut Im2pSimulator::new()?,
        Case {
            shape,
            activations: &structured_activations(shape),
            weights: &structured_weights(shape),
            scales: Some(scales),
            column_offset: 0,
            valid_columns: shape.n,
            context: 11,
            operation,
        },
    )?;
    assert_matrix_eq(&result.output, &result.expected, shape.m, shape.n);
    Ok(())
}

#[test]
fn single_block_multiply_matches_cpu() -> Result<(), SimError> {
    let shape = Shape { m: 3, n: 4, k: 8 };
    let scales = KBlockScaleMatrix::from_fn(shape.k, shape.k, shape.n, |_, column| {
        [2, -1, 3, -2][column]
    });
    run_scaled(VectorOp::Multiply, shape, &scales)
}

#[test]
fn single_block_shift_matches_cpu() -> Result<(), SimError> {
    let shape = Shape { m: 3, n: 4, k: 8 };
    let scales = KBlockScaleMatrix::from_fn(shape.k, shape.k, shape.n, |_, column| {
        [-2, -1, 1, 2][column]
    });
    run_scaled(VectorOp::Shift, shape, &scales)
}

#[test]
fn scale_row_maps_distinct_values_to_columns() -> Result<(), SimError> {
    let shape = Shape { m: 2, n: 6, k: 7 };
    let scales = KBlockScaleMatrix::from_fn(shape.k, shape.k, shape.n, |_, column| {
        i8::try_from(column + 1).expect("small column")
    });
    run_scaled(VectorOp::Multiply, shape, &scales)
}

#[test]
fn scale_row_is_shared_across_output_rows() -> Result<(), SimError> {
    let shape = Shape { m: 5, n: 4, k: 9 };
    let scales =
        KBlockScaleMatrix::from_fn(shape.k, shape.k, shape.n, |_, column| [3, -2, 1, 4][column]);
    run_scaled(VectorOp::Multiply, shape, &scales)
}

#[test]
fn positive_negative_and_zero_scales_match_cpu() -> Result<(), SimError> {
    let shape = Shape { m: 3, n: 5, k: 11 };
    let scales = KBlockScaleMatrix::from_fn(shape.k, shape.k, shape.n, |_, column| {
        [-3, -1, 0, 2, 5][column]
    });
    run_scaled(VectorOp::Multiply, shape, &scales)
}

#[test]
fn signed_shift_extremes_match_cpu() -> Result<(), SimError> {
    let shape = Shape { m: 3, n: 5, k: 11 };
    let scales = KBlockScaleMatrix::from_fn(shape.k, shape.k, shape.n, |_, column| {
        [-5, -2, 0, 3, 7][column]
    });
    run_scaled(VectorOp::Shift, shape, &scales)
}
