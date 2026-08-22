use super::{Error, KBlockScaleMatrixView, TileRequest, VectorOp};

pub(super) fn validate_tile<'a>(
    request: &TileRequest<'a>,
    output: &[i32],
    dim: usize,
) -> Result<Option<KBlockScaleMatrixView<'a>>, Error> {
    if request.valid_m == 0
        || request.valid_n == 0
        || request.valid_k == 0
        || request.valid_m > dim
        || request.valid_n > dim
        || request.valid_k > dim
    {
        return Err(Error::InvalidTileShape);
    }
    validate_execution_range(request)?;
    require_len(
        "activations",
        request.valid_m * request.valid_k,
        request.activations.len(),
    )?;
    require_len(
        "weights",
        request.valid_k * request.valid_n,
        request.weights.len(),
    )?;
    require_len("output", request.valid_m * request.valid_n, output.len())?;
    crate::activation_validation::validate_tile_activations(request)?;

    if request.vector_op == VectorOp::Bypass {
        return Ok(None);
    }

    let matrix = request.scale_matrix.ok_or(Error::MissingScales {
        operation: request.vector_op,
    })?;
    validate_scaling_range(request, matrix)?;
    Ok(Some(matrix))
}

fn validate_execution_range(request: &TileRequest<'_>) -> Result<(), Error> {
    if request.k_start > u32::MAX as usize {
        return Err(Error::InvalidKRange);
    }
    request
        .k_start
        .checked_add(request.valid_k)
        .ok_or(Error::InvalidKRange)?;
    Ok(())
}

fn validate_scaling_range(
    request: &TileRequest<'_>,
    matrix: KBlockScaleMatrixView<'_>,
) -> Result<(), Error> {
    validate_scale_matrix(matrix, matrix.total_k, request.valid_n)?;
    if matrix.block_size == 0
        || matrix.total_k == 0
        || request.k_start >= matrix.total_k
        || matrix.block_size > u32::MAX as usize
        || matrix.total_k > u32::MAX as usize
    {
        return Err(Error::InvalidKRange);
    }
    let k_end = request.k_start + request.valid_k;
    if k_end > matrix.total_k {
        return Err(Error::InvalidKRange);
    }
    if request.k_start / matrix.block_size != (k_end - 1) / matrix.block_size {
        return Err(Error::UnsupportedBlockConfiguration {
            k_start: request.k_start,
            valid_k: request.valid_k,
            block_size: matrix.block_size,
        });
    }
    let block = request.k_start / matrix.block_size;
    if block > u32::MAX as usize {
        return Err(Error::InvalidKRange);
    }
    Ok(())
}

pub(crate) fn validate_scale_matrix(
    matrix: KBlockScaleMatrixView<'_>,
    required_k: usize,
    required_columns: usize,
) -> Result<(), Error> {
    if matrix.block_size == 0
        || matrix.total_k == 0
        || matrix.total_k < required_k
        || matrix.block_size > u32::MAX as usize
        || matrix.total_k > u32::MAX as usize
    {
        return Err(Error::InvalidKRange);
    }
    if matrix.columns == 0
        || matrix.valid_columns == 0
        || matrix.valid_columns != required_columns
        || matrix.row_stride < matrix.columns
    {
        return Err(Error::InvalidScaleMatrixLayout);
    }
    let columns_end = matrix
        .column_offset
        .checked_add(matrix.valid_columns)
        .ok_or(Error::InvalidScaleMatrixLayout)?;
    if columns_end > matrix.columns {
        return Err(Error::InvalidScaleMatrixLayout);
    }
    let block_count = matrix.total_k.div_ceil(matrix.block_size);
    if block_count > u32::MAX as usize {
        return Err(Error::InvalidKRange);
    }
    let final_row = (block_count - 1)
        .checked_mul(matrix.row_stride)
        .and_then(|value| value.checked_add(matrix.column_offset))
        .ok_or(Error::InvalidScaleMatrixLayout)?;
    let required_len = final_row
        .checked_add(matrix.valid_columns)
        .ok_or(Error::InvalidScaleMatrixLayout)?;
    if required_len > matrix.values.len() {
        return Err(Error::InvalidScaleMatrixLayout);
    }
    Ok(())
}

fn require_len(name: &'static str, expected: usize, actual: usize) -> Result<(), Error> {
    if actual != expected {
        return Err(Error::InvalidBufferLength {
            name,
            expected,
            actual,
        });
    }
    Ok(())
}

#[cfg(test)]
mod activation_boundary_tests {
    use super::{validate_tile, Error};
    use crate::{parse_activation, ActivationValue, TileRequest, VectorOp, ACTIVATION_BITS};

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
    fn production_activation_boundary_validate_tile_rejects_malformed_a4() {
        if ACTIVATION_BITS != 4 {
            return;
        }
        let activations: [ActivationValue; 2] = [-9, 8];
        let weights = [1_i8, 1];
        let request = TileRequest {
            activations: &activations,
            weights: &weights,
            scale_matrix: None,
            valid_m: 1,
            valid_n: 1,
            valid_k: 2,
            k_start: 0,
            accumulate: false,
            vector_op: VectorOp::Bypass,
        };

        assert_eq!(validate_tile(&request, &[0], 2), Err(Error::InvalidLayout));
    }

    #[test]
    fn production_activation_boundary_validate_tile_accepts_selected_extrema() {
        let activations = selected_extrema();
        let weights = [1_i8, 1];
        let request = TileRequest {
            activations: &activations,
            weights: &weights,
            scale_matrix: None,
            valid_m: 1,
            valid_n: 1,
            valid_k: 2,
            k_start: 0,
            accumulate: false,
            vector_op: VectorOp::Bypass,
        };

        assert_eq!(validate_tile(&request, &[0], 2), Ok(None));
    }
}
