use im2p_sim::{Im2pSimulator, SimError, TileRequest, VectorOp};

#[test]
fn multi_block_scale_table_requires_exact_length() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let activations = vec![1_i8; 16];
    let weights = vec![1_i8; 32];
    let mut output = vec![0_i32; 2];
    for scales in [vec![1_i8; 3], vec![1_i8; 5]] {
        let result = simulator.execute_tile(
            &TileRequest {
                activations: &activations,
                weights: &weights,
                scales: Some(&scales),
                valid_m: 1,
                valid_n: 2,
                valid_k: 16,
                k_start: 0,
                total_k: 64,
                block_size: 32,
                accumulate: false,
                vector_op: VectorOp::Multiply,
            },
            &mut output,
        );
        assert_eq!(
            result,
            Err(SimError::InvalidBufferLength {
                name: "scales",
                expected: 4,
                actual: scales.len(),
            }),
        );
    }
    Ok(())
}

#[test]
fn hardware_partial_crossing_block_boundary_is_rejected() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let activations = vec![1_i8; 16];
    let weights = vec![1_i8; 32];
    let scales = vec![1_i8; 4];
    let mut output = vec![0_i32; 2];
    let result = simulator.execute_tile(
        &TileRequest {
            activations: &activations,
            weights: &weights,
            scales: Some(&scales),
            valid_m: 1,
            valid_n: 2,
            valid_k: 16,
            k_start: 24,
            total_k: 64,
            block_size: 32,
            accumulate: false,
            vector_op: VectorOp::Multiply,
        },
        &mut output,
    );
    assert_eq!(
        result,
        Err(SimError::UnsupportedBlockConfiguration {
            k_start: 24,
            valid_k: 16,
            block_size: 32,
        }),
    );
    Ok(())
}

#[test]
fn scaled_invalid_global_k_ranges_are_rejected() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let mut output = [0_i32; 1];
    let scales = [1_i8];
    for (block_size, total_k, k_start, valid_k) in
        [(0, 1, 0, 1), (1, 0, 0, 1), (1, 1, 1, 1), (4, 4, 3, 2)]
    {
        let activations = vec![1_i8; valid_k];
        let weights = vec![1_i8; valid_k];
        let result = simulator.execute_tile(
            &TileRequest {
                activations: &activations,
                weights: &weights,
                scales: Some(&scales),
                valid_m: 1,
                valid_n: 1,
                valid_k,
                k_start,
                total_k,
                block_size,
                accumulate: false,
                vector_op: VectorOp::Multiply,
            },
            &mut output,
        );
        assert_eq!(result, Err(SimError::InvalidKRange));
    }
    Ok(())
}

#[test]
fn scale_table_capacity_is_enforced() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let mut output = [0_i32; 1];
    let scales = [1_i8; 9];
    let result = simulator.execute_tile(
        &TileRequest {
            activations: &[1],
            weights: &[1],
            scales: Some(&scales),
            valid_m: 1,
            valid_n: 1,
            valid_k: 1,
            k_start: 0,
            total_k: 288,
            block_size: 32,
            accumulate: false,
            vector_op: VectorOp::Multiply,
        },
        &mut output,
    );
    assert_eq!(
        result,
        Err(SimError::TooManyScaleBlocks {
            maximum: 8,
            actual: 9,
        }),
    );
    Ok(())
}

#[test]
fn accumulator_row_address_must_fit_rtl_storage() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let values = vec![0_i32; simulator.dim()];
    assert_eq!(
        simulator.write_accumulator_row(1_usize << 32, &values),
        Err(SimError::InvalidAccumulatorRow {
            maximum: 255,
            actual: 1_usize << 32,
        }),
    );
    Ok(())
}
