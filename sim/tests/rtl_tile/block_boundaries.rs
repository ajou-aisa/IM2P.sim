use im2p_sim::{Im2pSimulator, SimError, VectorOp};

use crate::block_support::{
    assert_block_matrix_eq, execute_fragmentwise, golden_fragmentwise, patterned_scales,
    structured_inputs, BLOCK_SIZE,
};
use crate::support::Shape;

#[test]
fn block32_multiply_matches_fragmentwise_golden() -> Result<(), SimError> {
    run_sizes(VectorOp::Multiply)
}

#[test]
fn block32_shift_matches_fragmentwise_golden() -> Result<(), SimError> {
    run_sizes(VectorOp::Shift)
}

#[test]
fn block32_switches_exactly_across_three_blocks() -> Result<(), SimError> {
    let shape = Shape { m: 3, n: 4, k: 96 };
    let (activations, weights) = boundary_inputs(shape);
    let mut simulator = Im2pSimulator::new()?;
    for operation in [VectorOp::Multiply, VectorOp::Shift] {
        let scales = patterned_scales(shape, BLOCK_SIZE, operation);
        run_case(
            &mut simulator,
            &activations,
            &weights,
            &scales,
            shape,
            operation,
        )?;
    }
    Ok(())
}

#[test]
fn block32_column_scales_share_across_output_rows() -> Result<(), SimError> {
    let shape = Shape { m: 4, n: 4, k: 32 };
    let mut activations = vec![0_i8; shape.m * shape.k];
    for (row, value) in [1_i8, -2, 3, -4].into_iter().enumerate() {
        activations[row * shape.k] = value;
    }
    let mut weights = vec![0_i8; shape.k * shape.n];
    for column in 0..shape.n {
        weights[column] = ((5 * column + 1) % 127) as i8 + 1;
    }
    let scales = [2_i8, -3, 5, -1];
    let mut simulator = Im2pSimulator::new()?;
    run_case(
        &mut simulator,
        &activations,
        &weights,
        &scales,
        shape,
        VectorOp::Multiply,
    )
}

#[test]
fn block32_k48_tail_uses_second_scale_block() -> Result<(), SimError> {
    let shape = Shape { m: 4, n: 9, k: 48 };
    let (activations, weights) = structured_inputs(shape);
    let mut simulator = Im2pSimulator::new()?;
    for operation in [VectorOp::Multiply, VectorOp::Shift] {
        let scales = patterned_scales(shape, BLOCK_SIZE, operation);
        run_case(
            &mut simulator,
            &activations,
            &weights,
            &scales,
            shape,
            operation,
        )?;
    }
    Ok(())
}

fn run_sizes(operation: VectorOp) -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    for k in [32, 48, 64, 96] {
        let shape = Shape { m: 5, n: 4, k };
        let (activations, weights) = structured_inputs(shape);
        let scales = patterned_scales(shape, BLOCK_SIZE, operation);
        run_case(
            &mut simulator,
            &activations,
            &weights,
            &scales,
            shape,
            operation,
        )?;
    }
    Ok(())
}

fn run_case(
    simulator: &mut Im2pSimulator,
    activations: &[i8],
    weights: &[i8],
    scales: &[i8],
    shape: Shape,
    operation: VectorOp,
) -> Result<(), SimError> {
    let dim = simulator.dim();
    let expected = golden_fragmentwise(
        activations,
        weights,
        scales,
        shape,
        dim,
        BLOCK_SIZE,
        operation,
    );
    let (actual, stats) = execute_fragmentwise(
        simulator,
        activations,
        weights,
        scales,
        shape,
        BLOCK_SIZE,
        operation,
    )?;
    if shape.k == 64 {
        let weight_load: u64 = stats.iter().map(|entry| entry.weight_load_cycles).sum();
        let scale_load: u64 = stats.iter().map(|entry| entry.scale_load_cycles).sum();
        let compute: u64 = stats.iter().map(|entry| entry.compute_cycles).sum();
        let total: u64 = stats.iter().map(|entry| entry.total_cycles).sum();
        println!(
            "block-aware dim={dim} K=64 operation={operation:?} executions={} weight_load={weight_load} scale_load={scale_load} compute={compute} total={total}",
            stats.len(),
        );
    }
    assert_block_matrix_eq(
        &actual,
        &expected,
        activations,
        weights,
        scales,
        shape,
        dim,
        BLOCK_SIZE,
        operation,
    );
    Ok(())
}

fn boundary_inputs(shape: Shape) -> (Vec<i8>, Vec<i8>) {
    let mut activations = vec![0_i8; shape.m * shape.k];
    let mut weights = vec![0_i8; shape.k * shape.n];
    let positions = [30_usize, 31, 32, 33, 62, 63, 64, 65];
    let rows = [
        [1_i8, 1, 1, 1, 1, 1, 1, 1],
        [1_i8, -1, 2, -2, 3, -3, 4, -4],
        [-2_i8, 3, -4, 5, -6, 7, -8, 9],
    ];
    for row in 0..shape.m {
        for (slot, k) in positions.into_iter().enumerate() {
            activations[row * shape.k + k] = rows[row][slot];
        }
    }
    for (slot, k) in positions.into_iter().enumerate() {
        for column in 0..shape.n {
            weights[k * shape.n + column] = ((slot + 2 * column + 1) % 11 + 1) as i8;
        }
    }
    (activations, weights)
}
