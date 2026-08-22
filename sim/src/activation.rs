use crate::{MatrixView, SimError};

pub const ACTIVATION_BITS: usize = selected_activation_bits();

const fn selected_activation_bits() -> usize {
    let Some(value) = option_env!("IM2P_ACTIVATION_BITS") else {
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

pub struct ActivationSelection<const BITS: usize>;

pub trait SelectedActivation {
    type Value: Copy + Default + std::fmt::Debug + PartialEq + Eq;

    fn parse(value: i32) -> Result<Self::Value, ActivationError>;
    fn to_i32(value: Self::Value) -> i32;
}

impl SelectedActivation for ActivationSelection<4> {
    type Value = i8;

    fn parse(value: i32) -> Result<Self::Value, ActivationError> {
        parse_in_range(value, -8, 7).map(|value| value as i8)
    }

    fn to_i32(value: Self::Value) -> i32 {
        i32::from(value)
    }
}

impl SelectedActivation for ActivationSelection<8> {
    type Value = i8;

    fn parse(value: i32) -> Result<Self::Value, ActivationError> {
        parse_in_range(value, i32::from(i8::MIN), i32::from(i8::MAX)).map(|value| value as i8)
    }

    fn to_i32(value: Self::Value) -> i32 {
        i32::from(value)
    }
}

impl SelectedActivation for ActivationSelection<16> {
    type Value = i16;

    fn parse(value: i32) -> Result<Self::Value, ActivationError> {
        parse_in_range(value, i32::from(i16::MIN), i32::from(i16::MAX)).map(|value| value as i16)
    }

    fn to_i32(value: Self::Value) -> i32 {
        i32::from(value)
    }
}

pub type ActivationValue = <ActivationSelection<ACTIVATION_BITS> as SelectedActivation>::Value;
pub type ActivationMatrixView<'a> = MatrixView<'a, ActivationValue>;

pub const ACTIVATION_STORAGE_BYTES: usize = size_of::<ActivationValue>();

#[derive(Debug, PartialEq, Eq)]
pub enum ActivationError {
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

pub fn parse_activation(value: i32) -> Result<ActivationValue, ActivationError> {
    <ActivationSelection<ACTIVATION_BITS> as SelectedActivation>::parse(value)
}

pub fn activation_to_i32(value: ActivationValue) -> i32 {
    <ActivationSelection<ACTIVATION_BITS> as SelectedActivation>::to_i32(value)
}

pub fn validate_activation_values(values: &[ActivationValue]) -> Result<(), ActivationError> {
    for &value in values {
        parse_activation(activation_to_i32(value))?;
    }
    Ok(())
}

pub fn activation_elements_to_bytes(elements: usize) -> Result<usize, ActivationError> {
    elements
        .checked_mul(ACTIVATION_STORAGE_BYTES)
        .ok_or(ActivationError::ByteCountOverflow {
            elements,
            storage_bytes: ACTIVATION_STORAGE_BYTES,
        })
}

pub(crate) fn activation_elements_to_address_bytes(
    elements: usize,
) -> Result<u64, ActivationError> {
    let bytes = activation_elements_to_bytes(elements)?;
    u64::try_from(bytes).map_err(|_| ActivationError::ByteCountOverflow {
        elements,
        storage_bytes: ACTIVATION_STORAGE_BYTES,
    })
}

pub(crate) fn activation_byte_indices(
    byte_offset: usize,
    row_stride: usize,
) -> Result<(usize, usize), ActivationError> {
    let row_stride_bytes = activation_elements_to_bytes(row_stride)?;
    let element_offset = activation_bytes_to_elements(byte_offset)?;
    Ok((byte_offset / row_stride_bytes, element_offset % row_stride))
}

pub fn activation_bytes_to_elements(bytes: usize) -> Result<usize, ActivationError> {
    if bytes % ACTIVATION_STORAGE_BYTES != 0 {
        return Err(ActivationError::MisalignedByteCount {
            bytes,
            storage_bytes: ACTIVATION_STORAGE_BYTES,
        });
    }
    Ok(bytes / ACTIVATION_STORAGE_BYTES)
}

pub fn activation_view(
    values: &[ActivationValue],
    rows: usize,
    columns: usize,
    row_stride: usize,
) -> Result<ActivationMatrixView<'_>, ActivationError> {
    let view = MatrixView::new(values, rows, columns, row_stride)
        .map_err(ActivationError::InvalidLayout)?;
    for row in 0..rows {
        let start = row * row_stride;
        validate_activation_values(&values[start..start + columns])?;
    }
    Ok(view)
}

pub fn activation_view_from_bytes(
    values: &[ActivationValue],
    rows: usize,
    columns: usize,
    row_stride_bytes: usize,
) -> Result<ActivationMatrixView<'_>, ActivationError> {
    let row_stride = activation_bytes_to_elements(row_stride_bytes)?;
    activation_view(values, rows, columns, row_stride)
}

fn parse_in_range(value: i32, minimum: i32, maximum: i32) -> Result<i32, ActivationError> {
    if !(minimum..=maximum).contains(&value) {
        return Err(ActivationError::ValueOutOfRange {
            value,
            minimum,
            maximum,
        });
    }
    Ok(value)
}
