use im2p_sim::{Im2pSimulator, SimError, TileStats, VectorOp};

use crate::block_support::{
    execute_fragmentwise, golden_fragmentwise, structured_inputs, BLOCK_SIZE,
};
use crate::support::{
    assert_matrix_eq, execute, golden_column_multiply, golden_column_shift, golden_matmul,
    Execution, KRange, Shape,
};

const SCALES_A: [i8; 8] = [2, -1, 3, 5, -3, 4, -2, 1];

fn execute_first_fragment(
    simulator: &mut Im2pSimulator,
    shape: Shape,
    scales: Option<&[i8]>,
    vector_op: VectorOp,
    total_k: usize,
    block_size: usize,
) -> Result<(Vec<i32>, TileStats), SimError> {
    let (activations, weights) = structured_inputs(shape);
    execute(
        simulator,
        Execution {
            activations: &activations,
            weights: &weights,
            scales,
            shape,
            k_range: KRange {
                start: 0,
                total: total_k,
                block_size,
            },
            accumulate: false,
            vector_op,
        },
    )
}

#[test]
fn same_scale_table_is_loaded_only_once_across_fragments() -> Result<(), SimError> {
    let shape = Shape { m: 3, n: 4, k: 64 };
    let (activations, weights) = structured_inputs(shape);

    for operation in [VectorOp::Multiply, VectorOp::Shift] {
        let mut simulator = Im2pSimulator::new()?;
        let expected = golden_fragmentwise(
            &activations,
            &weights,
            &SCALES_A,
            shape,
            simulator.dim(),
            BLOCK_SIZE,
            operation,
        );
        let (actual, stats) = execute_fragmentwise(
            &mut simulator,
            &activations,
            &weights,
            &SCALES_A,
            shape,
            BLOCK_SIZE,
            operation,
        )?;
        let scale_cycles: Vec<u64> = stats.iter().map(|entry| entry.scale_load_cycles).collect();
        println!(
            "scale-reuse dim={} operation={operation:?} fragments={scale_cycles:?}",
            simulator.dim()
        );
        assert!(scale_cycles[0] > 0);
        assert!(scale_cycles[1..].iter().all(|cycles| *cycles == 0));
        assert_matrix_eq(&actual, &expected, shape.m, shape.n);
    }
    Ok(())
}

#[test]
fn same_scales_are_reused_across_multiply_bypass_shift() -> Result<(), SimError> {
    let shape = Shape { m: 3, n: 4, k: 16 };
    let (activations, weights) = structured_inputs(shape);
    let raw = golden_matmul(&activations, &weights, shape);
    let mut simulator = Im2pSimulator::new()?;
    let range = KRange {
        start: 0,
        total: 64,
        block_size: BLOCK_SIZE,
    };

    let (multiplied, multiply_stats) = execute(
        &mut simulator,
        Execution {
            activations: &activations,
            weights: &weights,
            scales: Some(&SCALES_A),
            shape,
            k_range: range,
            accumulate: false,
            vector_op: VectorOp::Multiply,
        },
    )?;
    let (bypassed, bypass_stats) = execute(
        &mut simulator,
        Execution {
            activations: &activations,
            weights: &weights,
            scales: None,
            shape,
            k_range: range,
            accumulate: false,
            vector_op: VectorOp::Bypass,
        },
    )?;
    let (shifted, shift_stats) = execute(
        &mut simulator,
        Execution {
            activations: &activations,
            weights: &weights,
            scales: Some(&SCALES_A),
            shape,
            k_range: range,
            accumulate: false,
            vector_op: VectorOp::Shift,
        },
    )?;

    assert!(multiply_stats.scale_load_cycles > 0);
    assert_eq!(bypass_stats.scale_load_cycles, 0);
    assert_eq!(shift_stats.scale_load_cycles, 0);
    assert_matrix_eq(
        &multiplied,
        &golden_column_multiply(&raw, &SCALES_A, shape),
        shape.m,
        shape.n,
    );
    assert_matrix_eq(&bypassed, &raw, shape.m, shape.n);
    assert_matrix_eq(
        &shifted,
        &golden_column_shift(&raw, &SCALES_A, shape),
        shape.m,
        shape.n,
    );
    Ok(())
}

#[test]
fn bypass_then_scaled_operation_loads_when_cache_empty() -> Result<(), SimError> {
    let shape = Shape { m: 2, n: 4, k: 16 };
    let mut simulator = Im2pSimulator::new()?;

    let (_, bypass) = execute_first_fragment(&mut simulator, shape, None, VectorOp::Bypass, 0, 0)?;
    let (_, multiply) = execute_first_fragment(
        &mut simulator,
        shape,
        Some(&SCALES_A),
        VectorOp::Multiply,
        64,
        BLOCK_SIZE,
    )?;

    assert_eq!(bypass.scale_load_cycles, 0);
    assert!(multiply.scale_load_cycles > 0);
    Ok(())
}

#[test]
fn reset_invalidates_scale_cache() -> Result<(), SimError> {
    let shape = Shape { m: 2, n: 4, k: 16 };
    let mut simulator = Im2pSimulator::new()?;

    let (_, first) = execute_first_fragment(
        &mut simulator,
        shape,
        Some(&SCALES_A),
        VectorOp::Multiply,
        64,
        BLOCK_SIZE,
    )?;
    let (_, reused) = execute_first_fragment(
        &mut simulator,
        shape,
        Some(&SCALES_A),
        VectorOp::Multiply,
        64,
        BLOCK_SIZE,
    )?;
    simulator.reset();
    let (_, after_reset) = execute_first_fragment(
        &mut simulator,
        shape,
        Some(&SCALES_A),
        VectorOp::Multiply,
        64,
        BLOCK_SIZE,
    )?;

    assert!(first.scale_load_cycles > 0);
    assert_eq!(reused.scale_load_cycles, 0);
    assert!(after_reset.scale_load_cycles > 0);
    Ok(())
}
