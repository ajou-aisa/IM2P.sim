use crate::{MatrixView, SimError};

pub const WEIGHT_BITS: usize = selected_weight_bits();

const fn selected_weight_bits() -> usize {
    let Some(value) = option_env!("IM2P_WEIGHT_BITS") else {
        return 8;
    };
    let bytes = value.as_bytes();
    if bytes.len() == 1 && bytes[0] == b'4' {
        4
    } else if bytes.len() == 1 && bytes[0] == b'8' {
        8
    } else if bytes.len() == 2 && bytes[0] == b'1' && bytes[1] == b'6' {
        16
    } else {
        0
    }
}

pub struct WeightSelection<const BITS: usize>;

pub trait SelectedWeight {
    type Value: Copy + Default + std::fmt::Debug + PartialEq + Eq;

    fn parse(value: i32) -> Result<Self::Value, WeightError>;
    fn to_i32(value: Self::Value) -> i32;
}

macro_rules! selected_weight {
    ($bits:literal, $ty:ty, $min:expr, $max:expr) => {
        impl SelectedWeight for WeightSelection<$bits> {
            type Value = $ty;

            fn parse(value: i32) -> Result<Self::Value, WeightError> {
                if !($min..=$max).contains(&value) {
                    return Err(WeightError::ValueOutOfRange {
                        value,
                        minimum: $min,
                        maximum: $max,
                    });
                }
                Ok(value as $ty)
            }

            fn to_i32(value: Self::Value) -> i32 {
                i32::from(value)
            }
        }
    };
}

selected_weight!(4, i8, -8, 7);
selected_weight!(8, i8, i8::MIN as i32, i8::MAX as i32);
selected_weight!(16, i16, i16::MIN as i32, i16::MAX as i32);

pub type WeightValue = <WeightSelection<WEIGHT_BITS> as SelectedWeight>::Value;
pub type WeightMatrixView<'a> = MatrixView<'a, WeightValue>;
pub const WEIGHT_STORAGE_BYTES: usize = size_of::<WeightValue>();

#[derive(Debug, PartialEq, Eq)]
pub enum WeightError {
    ValueOutOfRange {
        value: i32,
        minimum: i32,
        maximum: i32,
    },
    ByteCountOverflow {
        elements: usize,
        storage_bytes: usize,
    },
    MisalignedByteCount {
        bytes: usize,
        storage_bytes: usize,
    },
    InvalidLayout(SimError),
}

pub fn parse_weight(value: i32) -> Result<WeightValue, WeightError> {
    <WeightSelection<WEIGHT_BITS> as SelectedWeight>::parse(value)
}

pub fn weight_to_i32(value: WeightValue) -> i32 {
    <WeightSelection<WEIGHT_BITS> as SelectedWeight>::to_i32(value)
}

pub fn validate_weight_values(values: &[WeightValue]) -> Result<(), WeightError> {
    for &value in values {
        parse_weight(weight_to_i32(value))?;
    }
    Ok(())
}

pub fn weight_elements_to_bytes(elements: usize) -> Result<usize, WeightError> {
    elements
        .checked_mul(WEIGHT_STORAGE_BYTES)
        .ok_or(WeightError::ByteCountOverflow {
            elements,
            storage_bytes: WEIGHT_STORAGE_BYTES,
        })
}

pub(crate) fn weight_elements_to_address_bytes(elements: usize) -> Result<u64, WeightError> {
    let bytes = weight_elements_to_bytes(elements)?;
    u64::try_from(bytes).map_err(|_| WeightError::ByteCountOverflow {
        elements,
        storage_bytes: WEIGHT_STORAGE_BYTES,
    })
}

pub fn weight_bytes_to_elements(bytes: usize) -> Result<usize, WeightError> {
    if !bytes.is_multiple_of(WEIGHT_STORAGE_BYTES) {
        return Err(WeightError::MisalignedByteCount {
            bytes,
            storage_bytes: WEIGHT_STORAGE_BYTES,
        });
    }
    Ok(bytes / WEIGHT_STORAGE_BYTES)
}

pub(crate) fn weight_byte_indices(
    byte_offset: usize,
    row_stride: usize,
) -> Result<(usize, usize), WeightError> {
    let row_stride_bytes = weight_elements_to_bytes(row_stride)?;
    let element_offset = weight_bytes_to_elements(byte_offset)?;
    Ok((byte_offset / row_stride_bytes, element_offset % row_stride))
}

pub fn weight_view(
    values: &[WeightValue],
    rows: usize,
    columns: usize,
    row_stride: usize,
) -> Result<WeightMatrixView<'_>, WeightError> {
    let view =
        MatrixView::new(values, rows, columns, row_stride).map_err(WeightError::InvalidLayout)?;
    for row in 0..rows {
        let start = row * row_stride;
        validate_weight_values(&values[start..start + columns])?;
    }
    Ok(view)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn selected_weight_identity_and_extrema_are_consistent() {
        let (minimum, maximum) = match WEIGHT_BITS {
            4 => (-8, 7),
            8 => (-128, 127),
            16 => (-32_768, 32_767),
            _ => unreachable!(),
        };
        assert_eq!(WEIGHT_STORAGE_BYTES, size_of::<WeightValue>());
        assert_eq!(weight_to_i32(parse_weight(minimum).unwrap()), minimum);
        assert_eq!(weight_to_i32(parse_weight(maximum).unwrap()), maximum);
        assert!(parse_weight(minimum - 1).is_err());
        assert!(parse_weight(maximum + 1).is_err());
    }

    #[test]
    fn weight_byte_addressing_uses_selected_storage_width() {
        assert_eq!(
            weight_elements_to_bytes(3).unwrap(),
            3 * WEIGHT_STORAGE_BYTES
        );
        assert_eq!(
            weight_byte_indices(6 * WEIGHT_STORAGE_BYTES, 5).unwrap(),
            (1, 1)
        );
        if WEIGHT_STORAGE_BYTES == 2 {
            assert!(weight_bytes_to_elements(3).is_err());
        }
    }
}
