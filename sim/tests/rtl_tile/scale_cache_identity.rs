use im2p_sim::{Im2pSimulator, SimError, TileStats, VectorOp};

use crate::block_support::{structured_inputs, BLOCK_SIZE};
use crate::support::{
    assert_matrix_eq, execute, golden_column_multiply, golden_matmul, Execution, KRange, Shape,
};

const SCALES_A: [i8; 8] = [2, -1, 3, 5, -3, 4, -2, 1];
const SCALES_B: [i8; 8] = [-1, -2, -3, -4, 2, 4, 6, 8];

fn execute_first_fragment(
    simulator: &mut Im2pSimulator,
    shape: Shape,
    scales: &[i8],
    total_k: usize,
    block_size: usize,
) -> Result<(Vec<i32>, TileStats), SimError> {
    let (activations, weights) = structured_inputs(shape);
    execute(
        simulator,
        Execution {
            activations: &activations,
            weights: &weights,
            scales: Some(scales),
            shape,
            k_range: KRange {
                start: 0,
                total: total_k,
                block_size,
            },
            accumulate: false,
            vector_op: VectorOp::Multiply,
        },
    )
}

#[test]
fn same_metadata_but_different_scale_values_reloads() -> Result<(), SimError> {
    let shape = Shape { m: 3, n: 4, k: 16 };
    let (activations, weights) = structured_inputs(shape);
    let raw = golden_matmul(&activations, &weights, shape);
    let mut simulator = Im2pSimulator::new()?;

    let (output_a, first_a) =
        execute_first_fragment(&mut simulator, shape, &SCALES_A, 64, BLOCK_SIZE)?;
    let (_, second_a) = execute_first_fragment(&mut simulator, shape, &SCALES_A, 64, BLOCK_SIZE)?;
    let (output_b, first_b) =
        execute_first_fragment(&mut simulator, shape, &SCALES_B, 64, BLOCK_SIZE)?;
    let (_, second_b) = execute_first_fragment(&mut simulator, shape, &SCALES_B, 64, BLOCK_SIZE)?;

    assert!(first_a.scale_load_cycles > 0);
    assert_eq!(second_a.scale_load_cycles, 0);
    assert!(first_b.scale_load_cycles > 0);
    assert_eq!(second_b.scale_load_cycles, 0);
    assert_matrix_eq(
        &output_a,
        &golden_column_multiply(&raw, &SCALES_A, shape),
        shape.m,
        shape.n,
    );
    assert_matrix_eq(
        &output_b,
        &golden_column_multiply(&raw, &SCALES_B, shape),
        shape.m,
        shape.n,
    );
    Ok(())
}

#[test]
fn changed_valid_n_reloads() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let shape4 = Shape { m: 2, n: 4, k: 16 };
    let shape3 = Shape { m: 2, n: 3, k: 16 };
    let scales4 = [2_i8, -1, 3, 5];
    let scales3 = [2_i8, -1, 3];

    let (_, first) = execute_first_fragment(&mut simulator, shape4, &scales4, 32, 32)?;
    let (_, changed) = execute_first_fragment(&mut simulator, shape3, &scales3, 32, 32)?;

    assert!(first.scale_load_cycles > 0);
    assert!(changed.scale_load_cycles > 0);
    Ok(())
}

#[test]
fn changed_block_size_reloads() -> Result<(), SimError> {
    let shape = Shape { m: 2, n: 4, k: 16 };
    let scales = [2_i8, -1, 3, 5];
    let mut simulator = Im2pSimulator::new()?;

    let (_, first) = execute_first_fragment(&mut simulator, shape, &scales, 32, 32)?;
    let (_, changed) = execute_first_fragment(&mut simulator, shape, &scales, 32, 64)?;

    assert!(first.scale_load_cycles > 0);
    assert!(changed.scale_load_cycles > 0);
    Ok(())
}

#[test]
fn changed_total_k_reloads() -> Result<(), SimError> {
    let shape = Shape { m: 2, n: 4, k: 16 };
    let scales = [2_i8, -1, 3, 5];
    let mut simulator = Im2pSimulator::new()?;

    let (_, first) = execute_first_fragment(&mut simulator, shape, &scales, 32, 64)?;
    let (_, changed) = execute_first_fragment(&mut simulator, shape, &scales, 48, 64)?;

    assert!(first.scale_load_cycles > 0);
    assert!(changed.scale_load_cycles > 0);
    Ok(())
}
