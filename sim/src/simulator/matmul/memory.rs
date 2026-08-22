use crate::{
    activation::activation_byte_indices, ffi, ActivationValue, MatmulWork, MatrixView,
    MatrixViewMut,
};

use super::{Error, SCALE_BASE};

pub(super) fn validate_work(
    work: &MatmulWork<'_>,
    output: &MatrixViewMut<'_, i32>,
) -> Result<(), Error> {
    crate::activation_validation::validate_work_activations(work)?;
    let m = work.activations.rows;
    let k = work.activations.columns;
    let n = work.weights.columns;
    if work.weights.rows != k || output.rows != m || output.columns != n {
        return Err(Error::InvalidTileShape);
    }
    if work.vector_op != crate::VectorOp::Bypass && work.scales.is_none() {
        return Err(Error::MissingScales {
            operation: work.vector_op,
        });
    }
    if let Some(scales) = work.scales {
        super::super::validation::validate_scale_matrix(scales, k, n)?;
    }
    Ok(())
}

pub(super) fn resolve_activation(
    view: &MatrixView<'_, ActivationValue>,
    base: u64,
    request: ffi::ReadRequest,
) -> Result<Vec<ActivationValue>, Error> {
    let byte_offset = request
        .address
        .checked_sub(base)
        .and_then(|bytes| usize::try_from(bytes).ok())
        .ok_or(Error::InvalidKRange)?;
    let (row, column) = activation_byte_indices(byte_offset, view.row_stride)
        .map_err(|_| Error::InvalidActivationStride)?;
    let count = request.element_count as usize;
    if row >= view.rows || column + count > view.columns {
        return Err(Error::InvalidKRange);
    }
    Ok(view.values[row * view.row_stride + column..][..count].to_vec())
}

pub(super) fn resolve_i8(
    view: &MatrixView<'_, i8>,
    base: u64,
    request: ffi::ReadRequest,
) -> Result<Vec<i8>, Error> {
    let offset = request
        .address
        .checked_sub(base)
        .ok_or(Error::InvalidKRange)? as usize;
    let row = offset / view.row_stride;
    let column = offset % view.row_stride;
    let count = request.element_count as usize;
    if row >= view.rows || column + count > view.columns {
        return Err(Error::InvalidKRange);
    }
    Ok(view.values[row * view.row_stride + column..][..count].to_vec())
}

pub(super) fn resolve_scale(
    view: crate::KBlockScaleMatrixView<'_>,
    request: ffi::ReadRequest,
) -> Result<Vec<i8>, Error> {
    let offset = request
        .address
        .checked_sub(SCALE_BASE)
        .ok_or(Error::InvalidKRange)? as usize;
    let block = offset / view.row_stride;
    let column = offset % view.row_stride;
    let count = request.element_count as usize;
    if column + count > view.valid_columns {
        return Err(Error::InvalidScaleMatrixLayout);
    }
    let start = block * view.row_stride + view.column_offset + column;
    let end = start
        .checked_add(count)
        .ok_or(Error::InvalidScaleMatrixLayout)?;
    if end > view.values.len() {
        return Err(Error::InvalidScaleMatrixLayout);
    }
    Ok(view.values[start..end].to_vec())
}

pub(super) fn write_i32(
    output: &mut MatrixViewMut<'_, i32>,
    base: u64,
    request: ffi::WriteRequest,
    values: &[i32],
) -> Result<(), Error> {
    let byte_offset = request
        .address
        .checked_sub(base)
        .ok_or(Error::InvalidKRange)? as usize;
    if byte_offset % size_of::<i32>() != 0 {
        return Err(Error::InvalidKRange);
    }
    let offset = byte_offset / size_of::<i32>();
    let row = offset / output.row_stride;
    let column = offset % output.row_stride;
    let count = request.element_count as usize;
    if row >= output.rows || column + count > output.columns {
        return Err(Error::InvalidKRange);
    }
    output.values[row * output.row_stride + column..][..count].copy_from_slice(&values[..count]);
    Ok(())
}

#[cfg(test)]
mod activation_boundary_tests {
    use super::{resolve_activation, validate_work, Error};
    use crate::{
        ffi, parse_activation, ActivationValue, MatmulWork, MatrixView, MatrixViewMut, VectorOp,
        ACTIVATION_BITS, ACTIVATION_STORAGE_BYTES,
    };

    const BASE: u64 = 0x1000;

    fn selected_extrema() -> [ActivationValue; 2] {
        let extrema = match ACTIVATION_BITS {
            4 => [-8, 7],
            8 => [-128, 127],
            16 => [-32_768, 32_767],
            _ => unreachable!("supported widths are compile-time selected"),
        };
        extrema.map(|value| parse_activation(value).expect("selected-width extrema"))
    }

    #[test]
    fn activation_byte_addressing_decodes_stride_five_and_nonzero_row_origin() {
        let values = (0..15)
            .map(|value| parse_activation(value - 7).expect("small activation"))
            .collect::<Vec<_>>();
        let view = MatrixView::new(&values, 3, 3, 5).expect("strided activation view");
        let request = ffi::ReadRequest {
            address: BASE + (5 * ACTIVATION_STORAGE_BYTES + ACTIVATION_STORAGE_BYTES) as u64,
            element_count: 2,
            tag: 9,
        };

        assert_eq!(
            resolve_activation(&view, BASE, request),
            Ok(vec![values[6], values[7]])
        );
    }

    #[test]
    fn odd_a16_activation_byte_address_is_rejected_before_response() {
        if ACTIVATION_BITS != 16 {
            return;
        }
        let values = [ActivationValue::default(); 3];
        let view = MatrixView::new(&values, 1, 3, 3).expect("valid activation view");
        let request = ffi::ReadRequest {
            address: BASE + 1,
            element_count: 1,
            tag: 11,
        };

        assert_eq!(
            resolve_activation(&view, BASE, request),
            Err(Error::InvalidActivationStride)
        );
    }

    #[test]
    fn production_activation_boundary_validate_work_rejects_malformed_a4() {
        if ACTIVATION_BITS != 4 {
            return;
        }
        let activations: [ActivationValue; 2] = [-9, 8];
        let weights = [1_i8, 1];
        let mut output = [0_i32];
        let work = MatmulWork {
            activations: MatrixView::new(&activations, 1, 2, 2).expect("shape-only view"),
            weights: MatrixView::new(&weights, 2, 1, 1).expect("valid weights"),
            scales: None,
            vector_op: VectorOp::Bypass,
        };
        let output = MatrixViewMut::new(&mut output, 1, 1, 1).expect("valid output");

        assert_eq!(validate_work(&work, &output), Err(Error::InvalidLayout));
    }

    #[test]
    fn production_activation_boundary_validate_work_accepts_selected_extrema() {
        let activations = selected_extrema();
        let weights = [1_i8, 1];
        let mut output = [0_i32];
        let work = MatmulWork {
            activations: MatrixView::new(&activations, 1, 2, 2).expect("valid activations"),
            weights: MatrixView::new(&weights, 2, 1, 1).expect("valid weights"),
            scales: None,
            vector_op: VectorOp::Bypass,
        };
        let output = MatrixViewMut::new(&mut output, 1, 1, 1).expect("valid output");

        assert_eq!(validate_work(&work, &output), Ok(()));
    }
}
