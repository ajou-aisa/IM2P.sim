use im2p_sim::{Im2pSimulator, SimError, VectorOp};

use crate::support::{assert_matrix_eq, execute, golden_matmul, single_block, Execution, Shape};

#[test]
fn small_deterministic_bypass_matches_cpu_golden() -> Result<(), SimError> {
    let shape = Shape { m: 2, n: 3, k: 4 };
    let activations = [1, 2, -1, 3, -2, 1, 4, -1];
    let weights = [1, -2, 3, 2, 1, -1, -1, 3, 2, 4, -1, 1];
    let expected = golden_matmul(&activations, &weights, shape);
    let mut simulator = Im2pSimulator::new()?;
    let (actual, _) = execute(
        &mut simulator,
        Execution {
            activations: &activations,
            weights: &weights,
            scales: None,
            shape,
            k_range: single_block(shape),
            accumulate: false,
            vector_op: VectorOp::Bypass,
        },
    )?;
    assert_matrix_eq(&actual, &expected, shape.m, shape.n);
    Ok(())
}

#[test]
fn full_hardware_tile_bypass_matches_cpu_golden() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let shape = Shape {
        m: dim,
        n: dim,
        k: dim,
    };
    let activations: Vec<i8> = (0..dim * dim).map(|index| (index % 7) as i8 - 3).collect();
    let weights: Vec<i8> = (0..dim * dim).map(|index| (index % 5) as i8 - 2).collect();
    let expected = golden_matmul(&activations, &weights, shape);
    let (actual, stats) = execute(
        &mut simulator,
        Execution {
            activations: &activations,
            weights: &weights,
            scales: None,
            shape,
            k_range: single_block(shape),
            accumulate: false,
            vector_op: VectorOp::Bypass,
        },
    )?;
    assert_matrix_eq(&actual, &expected, dim, dim);
    println!(
        "dim={dim} weight_load={} scale_load={} compute={} total={} macs_per_cycle={:.3} ops_per_cycle={:.3} utilization={:.6}",
        stats.weight_load_cycles,
        stats.scale_load_cycles,
        stats.compute_cycles,
        stats.total_cycles,
        stats.macs_per_cycle,
        stats.ops_per_cycle,
        stats.utilization,
    );
    Ok(())
}

#[test]
fn zero_inputs_match_cpu_golden() -> Result<(), SimError> {
    let shape = Shape { m: 3, n: 4, k: 5 };
    let zero_activations = vec![0_i8; shape.m * shape.k];
    let signed_activations: Vec<i8> = (0..shape.m * shape.k)
        .map(|index| (index % 7) as i8 - 3)
        .collect();
    let zero_weights = vec![0_i8; shape.k * shape.n];
    let signed_weights: Vec<i8> = (0..shape.k * shape.n)
        .map(|index| (index % 5) as i8 - 2)
        .collect();
    let mut simulator = Im2pSimulator::new()?;

    for (activations, weights) in [
        (zero_activations.as_slice(), signed_weights.as_slice()),
        (signed_activations.as_slice(), zero_weights.as_slice()),
    ] {
        let expected = golden_matmul(activations, weights, shape);
        let (actual, _) = execute(
            &mut simulator,
            Execution {
                activations,
                weights,
                scales: None,
                shape,
                k_range: single_block(shape),
                accumulate: false,
                vector_op: VectorOp::Bypass,
            },
        )?;
        assert_matrix_eq(&actual, &expected, shape.m, shape.n);
    }
    Ok(())
}

#[test]
fn signed_bypass_includes_cancellation() -> Result<(), SimError> {
    let shape = Shape { m: 2, n: 3, k: 4 };
    let activations = [2, -2, 3, -3, -1, 1, 2, -2];
    let weights = [-2, -2, -2, -2, -2, -2, -2, -2, -1, -2, -1, -2];
    let expected = golden_matmul(&activations, &weights, shape);
    assert!(expected.iter().any(|value| *value > 0));
    assert!(expected.iter().any(|value| *value < 0));
    assert!(expected.contains(&0));
    let mut simulator = Im2pSimulator::new()?;
    let (actual, _) = execute(
        &mut simulator,
        Execution {
            activations: &activations,
            weights: &weights,
            scales: None,
            shape,
            k_range: single_block(shape),
            accumulate: false,
            vector_op: VectorOp::Bypass,
        },
    )?;
    assert_matrix_eq(&actual, &expected, shape.m, shape.n);
    Ok(())
}

#[test]
fn tile_total_cycles_are_not_cumulative() -> Result<(), SimError> {
    let shape = Shape { m: 1, n: 1, k: 1 };
    let mut simulator = Im2pSimulator::new()?;
    let execution = || Execution {
        activations: &[2],
        weights: &[3],
        scales: None,
        shape,
        k_range: single_block(shape),
        accumulate: false,
        vector_op: VectorOp::Bypass,
    };
    let (_, first) = execute(&mut simulator, execution())?;
    let (_, second) = execute(&mut simulator, execution())?;
    assert_eq!(first.total_cycles, second.total_cycles);
    Ok(())
}
