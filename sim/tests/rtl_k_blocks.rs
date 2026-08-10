pub mod common;

use common::{
    assert_matrix_eq, run_case, structured_activations, structured_weights, Case,
    KBlockScaleMatrix, Shape,
};
use im2p_sim::{Im2pSimulator, SimError, VectorOp};

fn patterned_scales(shape: Shape, block_size: usize) -> KBlockScaleMatrix {
    KBlockScaleMatrix::from_fn(shape.k, block_size, shape.n, |block, column| {
        ((3 * block + 5 * column) % 7) as i8 - 3
    })
}

fn verify(shape: Shape, block_size: usize, operation: VectorOp) -> Result<(), SimError> {
    let scales = patterned_scales(shape, block_size);
    let result = run_case(
        &mut Im2pSimulator::new()?,
        Case {
            shape,
            activations: &structured_activations(shape),
            weights: &structured_weights(shape),
            scales: Some(&scales),
            column_offset: 0,
            valid_columns: shape.n,
            context: 0x4b_424c_4f_434b,
            operation,
        },
    )?;
    assert_matrix_eq(&result.output, &result.expected, shape.m, shape.n);
    assert!(result
        .fragments
        .iter()
        .all(|fragment| fragment.start / block_size
            == (fragment.start + fragment.count - 1) / block_size));
    Ok(())
}

#[test]
fn b8_b16_b32_b64_match_cpu() -> Result<(), SimError> {
    let shape = Shape { m: 2, n: 4, k: 128 };
    for block_size in [8, 16, 32, 64] {
        verify(shape, block_size, VectorOp::Multiply)?;
        verify(shape, block_size, VectorOp::Shift)?;
    }
    Ok(())
}

#[test]
fn nine_blocks_exceed_old_capacity() -> Result<(), SimError> {
    verify(Shape { m: 2, n: 4, k: 288 }, 32, VectorOp::Multiply)
}

#[test]
fn seventeen_blocks_match_cpu() -> Result<(), SimError> {
    verify(Shape { m: 2, n: 4, k: 544 }, 32, VectorOp::Multiply)
}

#[test]
fn k4096_b32_128_blocks_match_cpu() -> Result<(), SimError> {
    verify(
        Shape {
            m: 1,
            n: 4,
            k: 4096,
        },
        32,
        VectorOp::Multiply,
    )
}

#[test]
fn block_boundaries_switch_exactly() -> Result<(), SimError> {
    verify(Shape { m: 3, n: 4, k: 96 }, 32, VectorOp::Shift)
}
