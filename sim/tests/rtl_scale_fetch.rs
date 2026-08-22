pub mod common;

use common::{
    assert_matrix_eq, run_case, structured_activations, structured_weights, Case,
    KBlockScaleMatrix, Shape,
};
use im2p_sim::{Im2pSimulator, SimError, VectorOp};

fn run_scaled(
    simulator: &mut Im2pSimulator,
    shape: Shape,
    scales: &KBlockScaleMatrix,
    context: u64,
) -> Result<common::RunResult, SimError> {
    run_case(
        simulator,
        Case {
            shape,
            activations: &structured_activations(shape),
            weights: &structured_weights(shape),
            scales: Some(scales),
            column_offset: 0,
            valid_columns: shape.n,
            context,
            operation: VectorOp::Multiply,
        },
    )
}

#[test]
fn cold_execution_uses_demand_fetch() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let shape = Shape {
        m: 1,
        n: 4,
        k: simulator.dim(),
    };
    let scales =
        KBlockScaleMatrix::from_fn(shape.k, shape.k, shape.n, |_, column| column as i8 + 1);
    let result = run_scaled(&mut simulator, shape, &scales, 1)?;
    assert_eq!(result.stats[0].scale_fetch.demand_requests, 1);
    assert_eq!(result.stats[0].scale_fetch.demand_misses, 1);
    assert_eq!(result.stats[0].scale_fetch.rows_received, 1);
    Ok(())
}

#[test]
fn same_block_second_fragment_hits_current_row() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let shape = Shape {
        m: 2,
        n: 4,
        k: 2 * dim,
    };
    let scales =
        KBlockScaleMatrix::from_fn(shape.k, shape.k, shape.n, |_, column| [2, -1, 3, 4][column]);
    let result = run_scaled(&mut simulator, shape, &scales, 2)?;
    assert_eq!(result.fragments.len(), 2);
    assert_eq!(result.stats[0].scale_fetch.demand_misses, 1);
    assert_eq!(result.stats[1].scale_fetch.current_hits, 1);
    assert_eq!(result.stats[1].scale_fetch.rows_received, 0);
    assert_matrix_eq(&result.output, &result.expected, shape.m, shape.n);
    Ok(())
}

#[test]
fn same_block_shift_reuses_current_row() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let shape = Shape {
        m: 2,
        n: 4,
        k: 2 * dim,
    };
    let scales = KBlockScaleMatrix::from_fn(shape.k, shape.k, shape.n, |_, column| {
        [-2, -1, 1, 2][column]
    });
    let result = run_case(
        &mut simulator,
        Case {
            shape,
            activations: &structured_activations(shape),
            weights: &structured_weights(shape),
            scales: Some(&scales),
            column_offset: 0,
            valid_columns: shape.n,
            context: 3,
            operation: VectorOp::Shift,
        },
    )?;
    assert_eq!(result.stats[0].scale_fetch.demand_misses, 1);
    assert_eq!(result.stats[1].scale_fetch.current_hits, 1);
    assert_matrix_eq(&result.output, &result.expected, shape.m, shape.n);
    Ok(())
}

#[test]
fn context_change_refetches_same_block() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let shape = Shape {
        m: 1,
        n: 4,
        k: simulator.dim(),
    };
    let scales_a =
        KBlockScaleMatrix::from_fn(shape.k, shape.k, shape.n, |_, column| column as i8 + 1);
    let scales_b =
        KBlockScaleMatrix::from_fn(shape.k, shape.k, shape.n, |_, column| -(column as i8 + 1));
    let first = run_scaled(&mut simulator, shape, &scales_a, 10)?;
    let changed = run_scaled(&mut simulator, shape, &scales_b, 11)?;
    assert_eq!(first.stats[0].scale_fetch.demand_misses, 1);
    assert_eq!(changed.stats[0].scale_fetch.demand_misses, 1);
    assert_ne!(first.output, changed.output);
    Ok(())
}

#[test]
fn reset_invalidates_current_row() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let shape = Shape {
        m: 1,
        n: 3,
        k: simulator.dim(),
    };
    let scales =
        KBlockScaleMatrix::from_fn(shape.k, shape.k, shape.n, |_, column| column as i8 + 2);
    let first = run_scaled(&mut simulator, shape, &scales, 20)?;
    let hit = run_scaled(&mut simulator, shape, &scales, 20)?;
    simulator.reset();
    let after_reset = run_scaled(&mut simulator, shape, &scales, 20)?;
    assert_eq!(first.stats[0].scale_fetch.demand_misses, 1);
    assert_eq!(hit.stats[0].scale_fetch.current_hits, 1);
    assert_eq!(after_reset.stats[0].scale_fetch.demand_misses, 1);
    Ok(())
}

#[test]
fn mutated_host_matrix_is_observed_after_context_change() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let shape = Shape {
        m: 1,
        n: 3,
        k: simulator.dim(),
    };
    let mut scales = KBlockScaleMatrix::from_fn(shape.k, shape.k, shape.n, |_, _| 1);
    let first = run_scaled(&mut simulator, shape, &scales, 30)?;
    scales.values.fill(3);
    let changed = run_scaled(&mut simulator, shape, &scales, 31)?;
    assert_ne!(first.output, changed.output);
    assert_eq!(changed.stats[0].scale_fetch.demand_misses, 1);
    Ok(())
}

#[test]
fn nonsequential_block_jump_uses_demand_fetch() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let total_k = 3 * dim;
    let matrix = KBlockScaleMatrix::from_fn(total_k, dim, 2, |block, column| {
        (block * 3 + column + 1) as i8
    });
    let activations = vec![im2p_sim::parse_activation(1).expect("valid activation"); dim];
    let weights = vec![1_i8; dim * 2];
    let mut output = vec![0_i32; 2];
    for (execution_index, block) in [0, 2].into_iter().enumerate() {
        let stats = simulator.execute_tile(
            &im2p_sim::TileRequest {
                activations: &activations,
                weights: &weights,
                scale_matrix: Some(matrix.view(0, 2, 40)),
                valid_m: 1,
                valid_n: 2,
                valid_k: dim,
                k_start: block * dim,
                accumulate: execution_index != 0,
                vector_op: VectorOp::Multiply,
            },
            &mut output,
        )?;
        assert_eq!(stats.scale_fetch.demand_misses, 1);
    }
    Ok(())
}

#[test]
fn global_j_stride_offset_and_contexts_select_exact_columns() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let shape = Shape {
        m: 2,
        n: 37,
        k: dim,
    };
    let activations = structured_activations(shape);
    let weights = structured_weights(shape);
    let scales =
        KBlockScaleMatrix::from_fn_with_stride(shape.k, shape.k, shape.n, 41, |block, column| {
            ((block + 3 * column) % 11) as i8 - 5
        });

    let mut offset = 0;
    let mut tile_index = 0_u64;
    while offset < shape.n {
        let valid_columns = dim.min(shape.n - offset);
        let result = run_case(
            &mut simulator,
            Case {
                shape,
                activations: &activations,
                weights: &weights,
                scales: Some(&scales),
                column_offset: offset,
                valid_columns,
                context: 1000 + tile_index,
                operation: VectorOp::Multiply,
            },
        )?;
        assert_matrix_eq(&result.output, &result.expected, shape.m, valid_columns);
        assert_eq!(result.stats[0].scale_fetch.demand_misses, 1);
        offset += valid_columns;
        tile_index += 1;
    }
    Ok(())
}
