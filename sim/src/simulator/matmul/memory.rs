use crate::{ffi, MatmulWork, MatrixView, MatrixViewMut};

use super::{Error, SCALE_BASE};

pub(super) fn validate_work(
    work: &MatmulWork<'_>,
    output: &MatrixViewMut<'_, i32>,
) -> Result<(), Error> {
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
    Ok(())
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
