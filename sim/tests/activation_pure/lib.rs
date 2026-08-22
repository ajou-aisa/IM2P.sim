//! Pure-Rust compilation surface for the production activation contract.
//!
//! This harness deliberately has no build script or FFI module. It includes the
//! production activation and matrix modules verbatim so every selected width can
//! be type-checked before the width-aware Verilator bridge is available.

#[derive(Debug, PartialEq, Eq)]
pub enum SimError {
    InvalidDimension,
    InvalidTileShape,
    InvalidLayout,
    InvalidBufferLength {
        name: &'static str,
        expected: usize,
        actual: usize,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct KBlockScaleMatrixView<'a> {
    pub values: &'a [i8],
    pub block_size: usize,
    pub total_k: usize,
    pub columns: usize,
    pub row_stride: usize,
    pub column_offset: usize,
    pub valid_columns: usize,
    pub context: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VectorOp {
    Bypass,
}

#[derive(Debug)]
pub struct TileRequest<'a> {
    pub activations: &'a [ActivationValue],
    pub weights: &'a [i8],
    pub scale_matrix: Option<KBlockScaleMatrixView<'a>>,
    pub valid_m: usize,
    pub valid_n: usize,
    pub valid_k: usize,
    pub k_start: usize,
    pub accumulate: bool,
    pub vector_op: VectorOp,
}

#[path = "../../src/activation.rs"]
mod activation;
#[path = "../../src/activation_validation.rs"]
mod activation_validation;
#[path = "../../src/matrix.rs"]
mod matrix;

pub use activation::{
    activation_bytes_to_elements, activation_elements_to_bytes, activation_view,
    activation_view_from_bytes, parse_activation, validate_activation_values, ActivationError,
    ActivationMatrixView, ActivationValue, ACTIVATION_BITS, ACTIVATION_STORAGE_BYTES,
};
pub use matrix::{MatmulLayout, MatmulWork, MatrixView, MatrixViewMut};

pub fn validate_work_boundary(
    work: &MatmulWork<'_>,
    _output: &MatrixViewMut<'_, i32>,
) -> Result<(), SimError> {
    activation_validation::validate_work_activations(work)
}

pub fn validate_tile_boundary(
    request: &TileRequest<'_>,
    _output: &[i32],
    _dim: usize,
) -> Result<(), SimError> {
    activation_validation::validate_tile_activations(request)
}

pub fn validate_provider_boundary(view: &MatrixView<'_, ActivationValue>) -> Result<(), SimError> {
    activation_validation::validate_activation_matrix(view)
}

pub fn validate_supply_boundary(values: &[ActivationValue]) -> Result<(), SimError> {
    activation_validation::validate_activation_row(values)
}

pub fn validate_stage_boundary(values: &[ActivationValue]) -> Result<(), SimError> {
    activation_validation::validate_activation_row(values)
}
