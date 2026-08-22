use super::Error;

pub(crate) fn u32_field(value: usize) -> Result<u32, Error> {
    u32::try_from(value).map_err(|_| Error::InvalidLayout)
}

pub(crate) const fn job_id(work_context: u64) -> u32 {
    let bytes = work_context.to_le_bytes();
    u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]])
}

pub(crate) fn u64_field(value: usize) -> Result<u64, Error> {
    u64::try_from(value).map_err(|_| Error::InvalidLayout)
}

pub(crate) fn output_row_stride_bytes(value: usize) -> Result<u64, Error> {
    value
        .checked_mul(size_of::<i32>())
        .and_then(|bytes| u64::try_from(bytes).ok())
        .ok_or(Error::InvalidLayout)
}

#[cfg(test)]
mod tests {
    use super::{output_row_stride_bytes, u32_field};
    use crate::SimError;

    #[test]
    fn output_stride_conversion_rejects_byte_count_overflow() {
        // Given a foreign row stride that cannot be represented after byte conversion.
        let stride = usize::MAX;

        // When it crosses the RTL descriptor boundary.
        let result = output_row_stride_bytes(stride);

        // Then conversion fails without wrapping or panicking.
        assert_eq!(result, Err(SimError::InvalidLayout));
    }

    #[test]
    fn rtl_u32_conversion_rejects_oversized_provider_block() {
        // Given a provider block size one past the RTL field maximum.
        let block_size = usize::try_from(u64::from(u32::MAX) + 1).expect("64-bit test target");

        // When it crosses the RTL descriptor boundary.
        let result = u32_field(block_size);

        // Then conversion fails instead of truncating.
        assert_eq!(result, Err(SimError::InvalidLayout));
    }
}
