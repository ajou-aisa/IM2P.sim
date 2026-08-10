use im2p_sim::{Im2pSimulator, SimError, VectorOp};

use crate::block_support::{structured_inputs, BLOCK_SIZE};
use crate::support::{
    activation_fragment, assert_matrix_eq, execute, signed_shift, weight_fragment, Execution,
    KRange, Shape,
};

const MULTIPLY_SCALES: [i8; 4] = [2, -1, 3, 1];
const SHIFT_SCALES: [i8; 4] = [1, -1, 2, 0];

/// Runs every hardware fragment of one K range on the given simulator handle.
fn run_range(
    simulator: &mut Im2pSimulator,
    activations: &[i8],
    weights: &[i8],
    scales: Option<&[i8]>,
    shape: Shape,
    range: (usize, usize),
    operation: VectorOp,
) -> Result<Vec<i32>, SimError> {
    let dim = simulator.dim();
    let (range_start, range_end) = range;
    let mut actual = Vec::new();
    let mut fragment_index = 0;
    let mut fragment_start = range_start;
    while fragment_start < range_end {
        let fragment_k = dim.min(range_end - fragment_start);
        let fragment_a =
            activation_fragment(activations, shape.m, shape.k, fragment_start, fragment_k);
        let fragment_w = weight_fragment(weights, shape.n, fragment_start, fragment_k);
        let (output, _) = execute(
            simulator,
            Execution {
                activations: &fragment_a,
                weights: &fragment_w,
                scales,
                shape: Shape {
                    k: fragment_k,
                    ..shape
                },
                k_range: KRange {
                    start: fragment_start,
                    total: shape.k,
                    block_size: BLOCK_SIZE,
                },
                accumulate: fragment_index != 0,
                vector_op: operation,
            },
        )?;
        actual = output;
        fragment_start += fragment_k;
        fragment_index += 1;
    }
    Ok(actual)
}

/// Independent CPU model: each hardware fragment is transformed before it is
/// added into the accumulator.
fn golden_range(
    activations: &[i8],
    weights: &[i8],
    scales: &[i8],
    shape: Shape,
    dim: usize,
    range: (usize, usize),
    operation: VectorOp,
) -> Vec<i32> {
    let (range_start, range_end) = range;
    let mut output = vec![0_i32; shape.m * shape.n];
    let mut fragment_start = range_start;
    while fragment_start < range_end {
        let fragment_end = (fragment_start + dim).min(range_end);
        let block = fragment_start / BLOCK_SIZE;
        for row in 0..shape.m {
            for column in 0..shape.n {
                let mut partial = 0_i32;
                for k in fragment_start..fragment_end {
                    partial = partial.wrapping_add(
                        i32::from(activations[row * shape.k + k])
                            * i32::from(weights[k * shape.n + column]),
                    );
                }
                let scale = scales[block * shape.n + column];
                let contribution = match operation {
                    VectorOp::Bypass => partial,
                    VectorOp::Multiply => partial.wrapping_mul(i32::from(scale)),
                    VectorOp::Shift => signed_shift(partial, scale),
                };
                let index = row * shape.n + column;
                output[index] = output[index].wrapping_add(contribution);
            }
        }
        fragment_start = fragment_end;
    }
    output
}

#[test]
fn same_core_switches_bypass_multiply_shift_and_bypass() -> Result<(), SimError> {
    let shape = Shape { m: 3, n: 4, k: 32 };
    let (activations, weights) = structured_inputs(shape);
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let range = (0, shape.k);

    // One simulator handle, one Verilated IM2PCore instance, four runtime ops.
    for (operation, scales) in [
        (VectorOp::Bypass, None),
        (VectorOp::Multiply, Some(MULTIPLY_SCALES.as_slice())),
        (VectorOp::Shift, Some(SHIFT_SCALES.as_slice())),
        (VectorOp::Bypass, None),
    ] {
        let expected = golden_range(
            &activations,
            &weights,
            scales.unwrap_or(&[0; 4]),
            shape,
            dim,
            range,
            operation,
        );
        let actual = run_range(
            &mut simulator,
            &activations,
            &weights,
            scales,
            shape,
            range,
            operation,
        )?;
        assert_matrix_eq(&actual, &expected, shape.m, shape.n);
    }
    Ok(())
}

#[test]
fn bypass_after_multiply_ignores_stale_scale_state() -> Result<(), SimError> {
    let shape = Shape { m: 3, n: 4, k: 32 };
    let (activations, weights) = structured_inputs(shape);
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let range = (0, shape.k);

    let scaled = run_range(
        &mut simulator,
        &activations,
        &weights,
        Some(MULTIPLY_SCALES.as_slice()),
        shape,
        range,
        VectorOp::Multiply,
    )?;
    let scaled_expected = golden_range(
        &activations,
        &weights,
        &MULTIPLY_SCALES,
        shape,
        dim,
        range,
        VectorOp::Multiply,
    );
    assert_matrix_eq(&scaled, &scaled_expected, shape.m, shape.n);

    let raw_expected = golden_range(
        &activations,
        &weights,
        &[0; 4],
        shape,
        dim,
        range,
        VectorOp::Bypass,
    );
    assert_ne!(scaled_expected, raw_expected);

    let bypassed = run_range(
        &mut simulator,
        &activations,
        &weights,
        None,
        shape,
        range,
        VectorOp::Bypass,
    )?;
    assert_matrix_eq(&bypassed, &raw_expected, shape.m, shape.n);
    Ok(())
}

#[test]
fn scaled_executions_keep_op_and_block_pairing() -> Result<(), SimError> {
    let shape = Shape { m: 3, n: 4, k: 64 };
    let (activations, weights) = structured_inputs(shape);
    let scales: Vec<i8> = vec![2, -1, 3, 5, -3, 4, -2, 1];
    let shifts: Vec<i8> = vec![1, -1, 2, 0, 2, 0, -2, 1];
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();

    for (operation, table) in [(VectorOp::Multiply, &scales), (VectorOp::Shift, &shifts)] {
        for block in 0..2 {
            let range = (block * BLOCK_SIZE, (block + 1) * BLOCK_SIZE);
            let expected =
                golden_range(&activations, &weights, table, shape, dim, range, operation);
            let actual = run_range(
                &mut simulator,
                &activations,
                &weights,
                Some(table),
                shape,
                range,
                operation,
            )?;
            assert_matrix_eq(&actual, &expected, shape.m, shape.n);
        }
    }
    Ok(())
}
