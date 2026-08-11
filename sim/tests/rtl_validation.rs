pub mod common;

use common::{scale_view as view, valid_request};
use im2p_sim::{
    Im2pSimulator, KBlockScaleMatrixView, MatmulWork, MatrixView, MatrixViewMut, SimError,
    StripeWorkDesc, VectorOp,
};

#[test]
fn block_size_zero_is_rejected() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let result = simulator.execute_tile(
        &valid_request(
            &[1, 2],
            &[1, 2, 3, 4],
            Some(view(&[1, 2], 0, 2)),
            VectorOp::Multiply,
        ),
        &mut [0; 2],
    );
    assert_eq!(result, Err(SimError::InvalidKRange));
    Ok(())
}

#[test]
fn invalid_global_k_range_is_rejected() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let mut request = valid_request(
        &[1, 2],
        &[1, 2, 3, 4],
        Some(view(&[1, 2], 2, 2)),
        VectorOp::Multiply,
    );
    request.k_start = 2;
    let result = simulator.execute_tile(&request, &mut [0; 2]);
    assert_eq!(result, Err(SimError::InvalidKRange));
    Ok(())
}

#[test]
fn cross_block_fragment_is_rejected() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let values = [1_i8, 2, 3, 4];
    let mut request = valid_request(
        &[1, 2],
        &[1, 2, 3, 4],
        Some(view(&values, 2, 4)),
        VectorOp::Multiply,
    );
    request.k_start = 1;
    assert_eq!(
        simulator.execute_tile(&request, &mut [0; 2]),
        Err(SimError::UnsupportedBlockConfiguration {
            k_start: 1,
            valid_k: 2,
            block_size: 2,
        })
    );
    Ok(())
}

#[test]
fn invalid_stride_is_rejected() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let invalid = KBlockScaleMatrixView {
        values: &[1, 2],
        block_size: 2,
        total_k: 2,
        columns: 2,
        row_stride: 1,
        column_offset: 0,
        valid_columns: 2,
        context: 1,
    };
    assert_eq!(
        simulator.execute_tile(
            &valid_request(&[1, 2], &[1, 2, 3, 4], Some(invalid), VectorOp::Multiply,),
            &mut [0; 2],
        ),
        Err(SimError::InvalidScaleMatrixLayout)
    );
    Ok(())
}

#[test]
fn invalid_column_offset_is_rejected() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let invalid = KBlockScaleMatrixView {
        values: &[1, 2, 3],
        block_size: 2,
        total_k: 2,
        columns: 3,
        row_stride: 3,
        column_offset: 2,
        valid_columns: 2,
        context: 1,
    };
    assert_eq!(
        simulator.execute_tile(
            &valid_request(&[1, 2], &[1, 2, 3, 4], Some(invalid), VectorOp::Shift,),
            &mut [0; 2],
        ),
        Err(SimError::InvalidScaleMatrixLayout)
    );
    Ok(())
}

#[test]
fn short_scale_buffer_is_rejected() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let short = KBlockScaleMatrixView {
        values: &[1, 2],
        block_size: 2,
        total_k: 4,
        columns: 2,
        row_stride: 2,
        column_offset: 0,
        valid_columns: 2,
        context: 1,
    };
    assert_eq!(
        simulator.execute_tile(
            &valid_request(&[1, 2], &[1, 2, 3, 4], Some(short), VectorOp::Multiply,),
            &mut [0; 2],
        ),
        Err(SimError::InvalidScaleMatrixLayout)
    );
    Ok(())
}

#[test]
fn scaled_operation_requires_matrix() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    assert_eq!(
        simulator.execute_tile(
            &valid_request(&[1, 2], &[1, 2, 3, 4], None, VectorOp::Multiply,),
            &mut [0; 2],
        ),
        Err(SimError::MissingScales {
            operation: VectorOp::Multiply,
        })
    );
    Ok(())
}

#[test]
fn bypass_accepts_missing_matrix() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let mut output = [0; 2];
    simulator.execute_tile(
        &valid_request(&[1, 2], &[1, 2, 3, 4], None, VectorOp::Bypass),
        &mut output,
    )?;
    assert_eq!(output, [7, 10]);
    Ok(())
}

#[test]
fn invalid_activation_weight_and_output_lengths_are_rejected() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let matrix = [1_i8, 2];
    let request = valid_request(
        &[1],
        &[1, 2, 3, 4],
        Some(view(&matrix, 2, 2)),
        VectorOp::Multiply,
    );
    assert_eq!(
        simulator.execute_tile(&request, &mut [0; 2]),
        Err(SimError::InvalidBufferLength {
            name: "activations",
            expected: 2,
            actual: 1,
        })
    );
    Ok(())
}

#[test]
fn invalid_weight_and_output_lengths_are_rejected() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let matrix = [1_i8, 2];
    let short_weights = valid_request(
        &[1, 2],
        &[1, 2, 3],
        Some(view(&matrix, 2, 2)),
        VectorOp::Multiply,
    );
    assert_eq!(
        simulator.execute_tile(&short_weights, &mut [0; 2]),
        Err(SimError::InvalidBufferLength {
            name: "weights",
            expected: 4,
            actual: 3,
        })
    );

    let valid = valid_request(
        &[1, 2],
        &[1, 2, 3, 4],
        Some(view(&matrix, 2, 2)),
        VectorOp::Multiply,
    );
    assert_eq!(
        simulator.execute_tile(&valid, &mut [0; 1]),
        Err(SimError::InvalidBufferLength {
            name: "output",
            expected: 2,
            actual: 1,
        })
    );
    Ok(())
}

#[test]
fn invalid_tile_shape_is_rejected() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let matrix = [1_i8, 2];
    let mut request = valid_request(
        &[1, 2],
        &[1, 2, 3, 4],
        Some(view(&matrix, 2, 2)),
        VectorOp::Multiply,
    );
    request.valid_m = 0;
    assert_eq!(
        simulator.execute_tile(&request, &mut [0; 2]),
        Err(SimError::InvalidTileShape)
    );
    Ok(())
}

#[test]
fn accumulator_row_address_is_checked() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let values = vec![0_i32; simulator.dim()];
    assert_eq!(
        simulator.write_accumulator_row(256, &values),
        Err(SimError::InvalidAccumulatorRow {
            maximum: 255,
            actual: 256,
        })
    );
    Ok(())
}

#[test]
fn bad_response_identity_is_rejected_by_ffi() {
    common::assert_bad_response_identity_rejected();
}

#[test]
fn invalid_scale_layouts_are_rejected_by_full_and_striped_apis() -> Result<(), SimError> {
    let activations = [1_i8];
    let weights = [1_i8];
    let scales = [1_i8; 4];
    let invalid_views = [
        KBlockScaleMatrixView {
            values: &scales,
            block_size: 1,
            total_k: 1,
            columns: 1,
            row_stride: 0,
            column_offset: 0,
            valid_columns: 1,
            context: 7,
        },
        KBlockScaleMatrixView {
            values: &scales,
            block_size: 1,
            total_k: 1,
            columns: 4,
            row_stride: 4,
            column_offset: usize::MAX - 1,
            valid_columns: 1,
            context: 7,
        },
    ];

    for scales in invalid_views {
        let work = MatmulWork {
            activations: MatrixView::new(&activations, 1, 1, 1)?,
            weights: MatrixView::new(&weights, 1, 1, 1)?,
            scales: Some(scales),
            vector_op: VectorOp::Multiply,
        };
        let mut output = [0_i32];
        let mut output_view = MatrixViewMut::new(&mut output, 1, 1, 1)?;
        assert_eq!(
            Im2pSimulator::new()?.execute_matmul(&work, &mut output_view),
            Err(SimError::InvalidScaleMatrixLayout)
        );

        let striped = StripeWorkDesc {
            weights: &weights,
            scale_matrix: Some(scales),
            rows: 1,
            columns: 1,
            reduction: 1,
            vector_op: VectorOp::Multiply,
            work_context: 7,
        };
        assert!(matches!(
            Im2pSimulator::new()?.begin_striped_matmul(&striped),
            Err(SimError::InvalidScaleMatrixLayout)
        ));
    }
    Ok(())
}
