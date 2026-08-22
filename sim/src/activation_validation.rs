use crate::{
    activation_view, validate_activation_values, ActivationValue, MatmulWork, MatrixView, SimError,
    TileRequest,
};

pub(crate) fn validate_work_activations(work: &MatmulWork<'_>) -> Result<(), SimError> {
    validate_activation_matrix(&work.activations)
}

pub(crate) fn validate_tile_activations(request: &TileRequest<'_>) -> Result<(), SimError> {
    validate_activation_values(request.activations).map_err(|_| SimError::InvalidLayout)
}

pub(crate) fn validate_activation_matrix(
    activations: &MatrixView<'_, ActivationValue>,
) -> Result<(), SimError> {
    activation_view(
        activations.values,
        activations.rows,
        activations.columns,
        activations.row_stride,
    )
    .map(|_| ())
    .map_err(|_| SimError::InvalidLayout)
}

pub(crate) fn validate_activation_row(values: &[ActivationValue]) -> Result<(), SimError> {
    validate_activation_values(values).map_err(|_| SimError::InvalidLayout)
}
