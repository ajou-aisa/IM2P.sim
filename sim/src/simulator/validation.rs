use super::{Error, TileRequest, VectorOp};

const MAX_SCALE_BLOCKS: usize = 8;

pub(super) fn validate_tile(
    request: &TileRequest<'_>,
    output: &[i32],
    dim: usize,
) -> Result<usize, Error> {
    if request.valid_m == 0
        || request.valid_n == 0
        || request.valid_k == 0
        || request.valid_m > dim
        || request.valid_n > dim
        || request.valid_k > dim
    {
        return Err(Error::InvalidTileShape);
    }
    validate_k_range(request)?;
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

    let block_count = request.total_k.div_ceil(request.block_size);
    if block_count > MAX_SCALE_BLOCKS {
        return Err(Error::TooManyScaleBlocks {
            maximum: MAX_SCALE_BLOCKS,
            actual: block_count,
        });
    }
    if let Some(scales) = request.scales {
        let expected = block_count
            .checked_mul(request.valid_n)
            .ok_or(Error::InvalidKRange)?;
        require_len("scales", expected, scales.len())?;
    } else if request.vector_op != VectorOp::Bypass {
        return Err(Error::MissingScales {
            operation: request.vector_op,
        });
    }
    Ok(block_count)
}

fn validate_k_range(request: &TileRequest<'_>) -> Result<(), Error> {
    if request.block_size == 0
        || request.total_k == 0
        || request.k_start >= request.total_k
        || request.block_size > u32::MAX as usize
        || request.total_k > u32::MAX as usize
        || request.k_start > u32::MAX as usize
    {
        return Err(Error::InvalidKRange);
    }
    let k_end = request
        .k_start
        .checked_add(request.valid_k)
        .ok_or(Error::InvalidKRange)?;
    if k_end > request.total_k {
        return Err(Error::InvalidKRange);
    }
    if request.k_start / request.block_size != (k_end - 1) / request.block_size {
        return Err(Error::UnsupportedBlockConfiguration {
            k_start: request.k_start,
            valid_k: request.valid_k,
            block_size: request.block_size,
        });
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
