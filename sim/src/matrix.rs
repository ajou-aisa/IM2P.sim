use crate::{ActivationValue, KBlockScaleMatrixView, SimError, VectorOp};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MatrixView<'a, T> {
    pub values: &'a [T],
    pub rows: usize,
    pub columns: usize,
    pub row_stride: usize,
}

impl<'a, T> MatrixView<'a, T> {
    pub fn new(
        values: &'a [T],
        rows: usize,
        columns: usize,
        row_stride: usize,
    ) -> Result<Self, SimError> {
        validate_layout(values.len(), rows, columns, row_stride)?;
        Ok(Self {
            values,
            rows,
            columns,
            row_stride,
        })
    }
}

#[derive(Debug)]
pub struct MatrixViewMut<'a, T> {
    pub values: &'a mut [T],
    pub rows: usize,
    pub columns: usize,
    pub row_stride: usize,
}

impl<'a, T> MatrixViewMut<'a, T> {
    pub fn new(
        values: &'a mut [T],
        rows: usize,
        columns: usize,
        row_stride: usize,
    ) -> Result<Self, SimError> {
        validate_layout(values.len(), rows, columns, row_stride)?;
        Ok(Self {
            values,
            rows,
            columns,
            row_stride,
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MatmulLayout {
    pub tile_i_rows: usize,
    pub tile_j_columns: usize,
}

#[derive(Debug)]
pub struct MatmulWork<'a> {
    pub activations: MatrixView<'a, ActivationValue>,
    pub weights: MatrixView<'a, i8>,
    pub scales: Option<KBlockScaleMatrixView<'a>>,
    pub vector_op: VectorOp,
}

/// Narrows an exact accumulator only at an int32 output boundary.
pub(crate) const fn saturating_i64_to_i32(value: i64) -> i32 {
    if value > 2_147_483_647 {
        i32::MAX
    } else if value < -2_147_483_648 {
        i32::MIN
    } else {
        value as i32
    }
}

fn validate_layout(
    values_len: usize,
    rows: usize,
    columns: usize,
    row_stride: usize,
) -> Result<(), SimError> {
    if rows == 0 || columns == 0 {
        return Err(SimError::InvalidDimension);
    }
    if row_stride < columns {
        return Err(SimError::InvalidTileShape);
    }

    let required = rows
        .checked_sub(1)
        .and_then(|last_row| last_row.checked_mul(row_stride))
        .and_then(|prefix| prefix.checked_add(columns))
        .ok_or(SimError::InvalidBufferLength {
            name: "matrix",
            expected: usize::MAX,
            actual: values_len,
        })?;

    if values_len < required {
        return Err(SimError::InvalidBufferLength {
            name: "matrix",
            expected: required,
            actual: values_len,
        });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::saturating_i64_to_i32;

    #[test]
    fn final_output_narrowing_saturates_both_i32_boundaries() {
        // Given exact values immediately inside and outside the final i32 range.
        let values = [
            i64::from(i32::MIN) - 1,
            i64::from(i32::MIN),
            i64::from(i32::MAX),
            i64::from(i32::MAX) + 1,
        ];

        // When the final output boundary narrows them.
        let narrowed = values.map(saturating_i64_to_i32);

        // Then only out-of-range values saturate.
        assert_eq!(narrowed, [i32::MIN, i32::MIN, i32::MAX, i32::MAX]);
    }
}
