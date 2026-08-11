use std::cell::RefCell;
use std::rc::Rc;

use crate::Im2pSimulator;
use crate::StripedMatmul;

#[repr(C)]
pub struct MatmulDesc {
    pub activations: *const i8,
    pub weights: *const i8,
    pub scales: *const i8,
    pub output: *mut i32,
    pub m: usize,
    pub n: usize,
    pub k: usize,
    pub activation_row_stride: usize,
    pub weight_row_stride: usize,
    pub output_row_stride: usize,
    pub tile_i_rows: usize,
    pub tile_j_columns: usize,
    pub block_size: usize,
    pub scale_total_k: usize,
    pub scale_row_stride: usize,
    pub scale_column_offset: usize,
    pub scale_valid_columns: usize,
    pub scale_values_len: usize,
    pub vector_op: u8,
    pub work_context: u64,
}

#[repr(C)]
pub struct StripeWorkDescC {
    pub weights: *const i8,
    pub scales: *const i8,
    pub output: *mut i32,
    pub m: usize,
    pub n: usize,
    pub k: usize,
    pub weight_row_stride: usize,
    pub output_row_stride: usize,
    pub tile_i_rows: usize,
    pub tile_j_columns: usize,
    pub block_size: usize,
    pub scale_total_k: usize,
    pub scale_row_stride: usize,
    pub scale_column_offset: usize,
    pub scale_valid_columns: usize,
    pub scale_values_len: usize,
    pub stripe_count: usize,
    pub vector_op: u8,
    pub work_context: u64,
}

#[repr(C)]
pub struct ActivationStripeC {
    pub stripe_id: u32,
    pub i_start: usize,
    pub rows: usize,
    pub activations: *const i8,
    pub activation_row_stride: usize,
    pub context: u64,
}

#[repr(C)]
pub struct StripeCompletionC {
    pub stripe_id: u32,
    pub i_start: usize,
    pub rows: usize,
    pub context: u64,
}

#[repr(C)]
#[derive(Default)]
pub struct WorkStatsC {
    pub work_total_cycles: u64,
    pub activation_read_requests: u64,
    pub weight_read_requests: u64,
    pub scale_read_requests: u64,
    pub output_write_requests: u64,
    pub output_write_responses: u64,
    pub activation_wait_cycles: u64,
    pub weight_wait_cycles: u64,
    pub scale_wait_cycles: u64,
    pub output_wait_cycles: u64,
    pub stripe_host_wait_cycles: u64,
    pub drain_cycles: u64,
    pub weight_preload_cycles: u64,
    pub same_block_scale_hits: u64,
    pub next_scale_hits: u64,
    pub scale_demand_misses: u64,
    pub compute_cycles: u64,
    pub overlap_cycles: u64,
    pub activation_overlap_cycles: u64,
    pub weight_overlap_cycles: u64,
    pub scale_overlap_cycles: u64,
    pub completed_fragments: u64,
    pub completed_output_tiles: u64,
    pub completed_stripes: u64,
    pub stripes_published: u64,
    pub stripe_rows_published: u64,
    pub weight_bank_activations: u64,
}

#[repr(C)]
#[derive(Default)]
pub struct WorkStatsExtendedC {
    pub base: WorkStatsC,
    pub cross_stripe_overlap_cycles: u64,
    pub lookahead_prepared: u64,
    pub lookahead_publish_cycle: u64,
    pub lookahead_first_activation_cycle: u64,
    pub lookahead_first_weight_cycle: u64,
    pub lookahead_weight_preload_cycle: u64,
    pub lookahead_weight_requests: u64,
    pub lookahead_weight_reuse_hits: u64,
    pub lookahead_scale_cycle: u64,
    pub lookahead_scale_requests: u64,
    pub lookahead_scale_reuses: u64,
    pub current_stripe_completion_cycle: u64,
    pub lookahead_ready_cycle: u64,
    pub lookahead_start_cycle: u64,
}

pub struct PublishedStripe {
    pub row_begin: usize,
    pub row_count: usize,
    pub values: *const i8,
    pub row_stride: usize,
}

pub struct StreamBox {
    pub owner: Rc<RefCell<Option<Im2pSimulator>>>,
    pub job: Option<StripedMatmul<'static>>,
    pub stripes: Vec<PublishedStripe>,
    pub output: *mut i32,
    pub output_stride: usize,
    pub columns: usize,
    pub reduction: usize,
}
