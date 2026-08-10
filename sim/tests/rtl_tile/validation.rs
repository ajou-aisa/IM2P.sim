use im2p_sim::{Im2pSimulator, SimError, TileRequest, VectorOp};

fn valid_request<'a>(
    activations: &'a [i8],
    weights: &'a [i8],
    scales: Option<&'a [i8]>,
    vector_op: VectorOp,
) -> TileRequest<'a> {
    TileRequest {
        activations,
        weights,
        scales,
        valid_m: 2,
        valid_n: 3,
        valid_k: 1,
        accumulate: false,
        vector_op,
    }
}

#[test]
fn bypass_accepts_missing_scales() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let mut output = vec![0_i32; 6];
    simulator.execute_tile(
        &valid_request(&[1, 2], &[1, 2, 3], None, VectorOp::Bypass),
        &mut output,
    )?;
    assert_eq!(output, [1, 2, 3, 2, 4, 6]);
    Ok(())
}

#[test]
fn scaled_operations_require_column_scales() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let mut output = vec![0_i32; 6];
    for operation in [VectorOp::Multiply, VectorOp::Shift] {
        let result = simulator.execute_tile(
            &valid_request(&[1, 2], &[1, 2, 3], None, operation),
            &mut output,
        );
        assert_eq!(result, Err(SimError::MissingScales { operation }));
    }
    Ok(())
}

#[test]
fn column_scale_length_must_equal_valid_n() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let mut output = vec![0_i32; 6];
    for scales in [&[1_i8, 2][..], &[1_i8, 2, 3, 4][..]] {
        let result = simulator.execute_tile(
            &valid_request(&[1, 2], &[1, 2, 3], Some(scales), VectorOp::Multiply),
            &mut output,
        );
        assert_eq!(
            result,
            Err(SimError::InvalidBufferLength {
                name: "scales",
                expected: 3,
                actual: scales.len(),
            }),
        );
    }
    Ok(())
}

#[test]
fn dimensions_above_hardware_tile_return_error() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let mut output = [0_i32; 1];
    for (valid_m, valid_n, valid_k) in [(dim + 1, 1, 1), (1, dim + 1, 1), (1, 1, dim + 1)] {
        let result = simulator.execute_tile(
            &TileRequest {
                activations: &[1],
                weights: &[1],
                scales: None,
                valid_m,
                valid_n,
                valid_k,
                accumulate: false,
                vector_op: VectorOp::Bypass,
            },
            &mut output,
        );
        assert_eq!(result, Err(SimError::InvalidTileShape));
    }
    Ok(())
}

#[test]
fn zero_tile_dimension_returns_error() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let mut output = [0_i32; 1];
    for (valid_m, valid_n, valid_k) in [(0, 1, 1), (1, 0, 1), (1, 1, 0)] {
        let result = simulator.execute_tile(
            &TileRequest {
                activations: &[1],
                weights: &[1],
                scales: None,
                valid_m,
                valid_n,
                valid_k,
                accumulate: false,
                vector_op: VectorOp::Bypass,
            },
            &mut output,
        );
        assert_eq!(result, Err(SimError::InvalidTileShape));
    }
    Ok(())
}

#[test]
fn matrix_buffer_length_mismatches_return_errors() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let activations = [1_i8, 2];
    let weights = [1_i8, 2, 3];
    let scales = [1_i8, 1, 1];
    let mut output = vec![0_i32; 6];

    let activation_result = simulator.execute_tile(
        &valid_request(
            &activations[..1],
            &weights,
            Some(&scales),
            VectorOp::Multiply,
        ),
        &mut output,
    );
    assert_eq!(
        activation_result,
        Err(SimError::InvalidBufferLength {
            name: "activations",
            expected: 2,
            actual: 1,
        }),
    );

    let weight_result = simulator.execute_tile(
        &valid_request(
            &activations,
            &weights[..2],
            Some(&scales),
            VectorOp::Multiply,
        ),
        &mut output,
    );
    assert_eq!(
        weight_result,
        Err(SimError::InvalidBufferLength {
            name: "weights",
            expected: 3,
            actual: 2,
        }),
    );

    let output_result = simulator.execute_tile(
        &valid_request(&activations, &weights, Some(&scales), VectorOp::Multiply),
        &mut output[..5],
    );
    assert_eq!(
        output_result,
        Err(SimError::InvalidBufferLength {
            name: "output",
            expected: 6,
            actual: 5,
        }),
    );
    Ok(())
}
