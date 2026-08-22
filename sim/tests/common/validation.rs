use im2p_sim::{ActivationValue, KBlockScaleMatrixView, TileRequest, VectorOp};

pub fn valid_request<'a>(
    activations: &'a [ActivationValue],
    weights: &'a [i8],
    matrix: Option<KBlockScaleMatrixView<'a>>,
    operation: VectorOp,
) -> TileRequest<'a> {
    TileRequest {
        activations,
        weights,
        scale_matrix: matrix,
        valid_m: 1,
        valid_n: 2,
        valid_k: 2,
        k_start: 0,
        accumulate: false,
        vector_op: operation,
    }
}

pub fn scale_view(values: &[i8], block_size: usize, total_k: usize) -> KBlockScaleMatrixView<'_> {
    KBlockScaleMatrixView {
        values,
        block_size,
        total_k,
        columns: 2,
        row_stride: 2,
        column_offset: 0,
        valid_columns: 2,
        context: 1,
    }
}
