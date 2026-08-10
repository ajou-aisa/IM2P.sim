use im2p_sim::{Im2pSimulator, SimError, VectorOp};

use crate::support::{
    assert_matrix_eq, execute, golden_column_multiply, golden_column_shift, golden_matmul,
    Execution, Lcg, Shape,
};

#[test]
fn column_wise_k_quant_multiply_matches_cpu_golden() -> Result<(), SimError> {
    let shape = Shape { m: 3, n: 4, k: 5 };
    let activations = [1, 2, -1, 3, 0, -2, 1, 4, -1, 2, 3, -3, 2, 1, -2];
    let weights = [
        1, -2, 3, 1, 2, 1, -1, 2, -1, 3, 2, -2, 4, -1, 1, 3, 0, 2, -3, 1,
    ];
    let scales = [2, -1, 3, 4];
    let raw = golden_matmul(&activations, &weights, shape);
    let expected = golden_column_multiply(&raw, &scales, shape);
    let mut simulator = Im2pSimulator::new()?;
    let (actual, _) = execute(
        &mut simulator,
        Execution {
            activations: &activations,
            weights: &weights,
            scales: Some(&scales),
            shape,
            accumulate: false,
            vector_op: VectorOp::Multiply,
        },
    )?;
    assert_matrix_eq(&actual, &expected, shape.m, shape.n);
    Ok(())
}

#[test]
fn k_group_scale_is_shared_across_output_rows() -> Result<(), SimError> {
    let shape = Shape { m: 3, n: 4, k: 3 };
    let activations = [1, 2, 3, -2, 1, 4, 3, -1, 2];
    let weights = [1, 2, -1, 3, 2, -1, 4, 1, -3, 2, 1, -2];
    let scales = [1, 2, -2, 3];
    let raw = golden_matmul(&activations, &weights, shape);
    assert_ne!(&raw[0..shape.n], &raw[shape.n..2 * shape.n]);
    assert_ne!(&raw[shape.n..2 * shape.n], &raw[2 * shape.n..3 * shape.n]);
    let expected = golden_column_multiply(&raw, &scales, shape);
    let mut simulator = Im2pSimulator::new()?;
    let (actual, _) = execute(
        &mut simulator,
        Execution {
            activations: &activations,
            weights: &weights,
            scales: Some(&scales),
            shape,
            accumulate: false,
            vector_op: VectorOp::Multiply,
        },
    )?;
    assert_matrix_eq(&actual, &expected, shape.m, shape.n);
    Ok(())
}

#[test]
fn column_wise_shift_matches_signed_bsv_semantics() -> Result<(), SimError> {
    let shape = Shape { m: 2, n: 5, k: 2 };
    let activations = [1, 1, 2, -1];
    let weights = [1, 2, 3, -4, 1, 2, 1, -3, -1, -3];
    let scales = [0, 1, -1, 2, -2];
    let raw = golden_matmul(&activations, &weights, shape);
    assert!(raw.iter().any(|value| *value > 0));
    assert!(raw.iter().any(|value| *value < 0));
    assert!(raw.contains(&0));
    let expected = golden_column_shift(&raw, &scales, shape);
    let mut simulator = Im2pSimulator::new()?;
    let (actual, _) = execute(
        &mut simulator,
        Execution {
            activations: &activations,
            weights: &weights,
            scales: Some(&scales),
            shape,
            accumulate: false,
            vector_op: VectorOp::Shift,
        },
    )?;
    assert_matrix_eq(&actual, &expected, shape.m, shape.n);
    Ok(())
}

#[test]
fn tail_tile_uses_unpadded_column_scales() -> Result<(), SimError> {
    let shape = Shape { m: 7, n: 9, k: 5 };
    let activations: Vec<i8> = (0..shape.m * shape.k)
        .map(|index| (index % 9) as i8 - 4)
        .collect();
    let weights: Vec<i8> = (0..shape.k * shape.n)
        .map(|index| (index % 7) as i8 - 3)
        .collect();
    let scales = [2, -1, 3, 1, -2, 2, -3, 1, 4];
    let raw = golden_matmul(&activations, &weights, shape);
    let expected = golden_column_multiply(&raw, &scales, shape);
    let mut simulator = Im2pSimulator::new()?;
    let (actual, _) = execute(
        &mut simulator,
        Execution {
            activations: &activations,
            weights: &weights,
            scales: Some(&scales),
            shape,
            accumulate: false,
            vector_op: VectorOp::Multiply,
        },
    )?;
    assert_matrix_eq(&actual, &expected, shape.m, shape.n);
    Ok(())
}

#[test]
fn deterministic_pseudo_random_bypass_and_multiply_match() -> Result<(), SimError> {
    let shape = Shape { m: 11, n: 13, k: 9 };
    let mut generator = Lcg::new(0x5eed_1234);
    let activations: Vec<i8> = (0..shape.m * shape.k)
        .map(|_| generator.signed(-8, 7))
        .collect();
    let weights: Vec<i8> = (0..shape.k * shape.n)
        .map(|_| generator.signed(-8, 7))
        .collect();
    let scales: Vec<i8> = (0..shape.n).map(|_| generator.signed(-3, 3)).collect();
    let raw = golden_matmul(&activations, &weights, shape);
    let multiplied = golden_column_multiply(&raw, &scales, shape);
    let mut simulator = Im2pSimulator::new()?;

    for (vector_op, column_scales, expected) in [
        (VectorOp::Bypass, None, raw.as_slice()),
        (
            VectorOp::Multiply,
            Some(scales.as_slice()),
            multiplied.as_slice(),
        ),
    ] {
        let (actual, _) = execute(
            &mut simulator,
            Execution {
                activations: &activations,
                weights: &weights,
                scales: column_scales,
                shape,
                accumulate: false,
                vector_op,
            },
        )?;
        assert_matrix_eq(&actual, expected, shape.m, shape.n);
    }
    Ok(())
}
