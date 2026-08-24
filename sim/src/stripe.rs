use crate::{KBlockScaleMatrixView, VectorOp, WeightValue};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ActivationStripe {
    pub stripe_id: u32,
    pub row_begin: usize,
    pub row_count: usize,
    pub stripe_context: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct StripeCompletion {
    pub stripe_id: u32,
    pub row_begin: usize,
    pub row_count: usize,
    pub stripe_context: u64,
    pub publish_cycle: u64,
    pub completion_cycle: u64,
}

impl StripeCompletion {
    pub const fn publish_to_completion_cycles(&self) -> u64 {
        self.completion_cycle.wrapping_sub(self.publish_cycle)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct StripeLayout {
    pub weight_row_stride: usize,
    pub output_row_stride: usize,
    pub tile_i_rows: usize,
    pub tile_j_columns: usize,
}

#[derive(Debug)]
pub struct StripeWorkDesc<'a> {
    pub weights: &'a [WeightValue],
    pub scale_matrix: Option<KBlockScaleMatrixView<'a>>,
    pub rows: usize,
    pub columns: usize,
    pub reduction: usize,
    pub vector_op: VectorOp,
    pub work_context: u64,
}
