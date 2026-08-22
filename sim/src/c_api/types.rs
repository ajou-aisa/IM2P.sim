use std::cell::RefCell;
use std::ffi::c_void;
use std::rc::Rc;

use crate::{ActivationValue, Im2pSimulator, StripedMatmul, WeightValue};

pub struct MatmulDesc {
    pub activations: *const ActivationValue,
    pub weights: *const WeightValue,
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

pub struct StripeWorkDesc {
    pub weights: *const WeightValue,
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
#[derive(Clone, Copy)]
pub struct ProviderC {
    pub context: *mut c_void,
    pub read_weight_i8: Option<crate::simulator::ReadWeightProviderI8>,
    pub read_weight_i16: Option<crate::simulator::ReadWeightProviderI16>,
    pub read_scale: Option<crate::simulator::ReadProvider>,
    pub write_output: Option<crate::simulator::WriteProvider>,
}

impl ProviderC {
    pub fn selected(self) -> crate::simulator::MemoryProvider {
        let read_weight = if crate::WEIGHT_BITS == 16 {
            self.read_weight_i16
                .map(crate::simulator::ReadWeightProvider::I16)
        } else {
            self.read_weight_i8
                .map(crate::simulator::ReadWeightProvider::I8)
        };
        crate::simulator::MemoryProvider {
            context: self.context,
            read_weight,
            read_scale: self.read_scale,
            write_output: self.write_output,
        }
    }
}

#[repr(C)]
pub struct MatmulDescC {
    pub abi_version: u32,
    pub activation_bits: u32,
    pub activation_storage_bytes: u32,
    pub weight_bits: u32,
    pub weight_storage_bytes: u32,
    pub dim: u32,
    pub activations: *const c_void,
    pub weights: *const c_void,
    pub scales: *const i8,
    pub output: *mut i32,
    pub m: usize,
    pub n: usize,
    pub k: usize,
    pub activation_row_stride_bytes: usize,
    pub weight_row_stride_bytes: usize,
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
    pub provider: ProviderC,
}

#[repr(C)]
pub struct StripeWorkDescC {
    pub abi_version: u32,
    pub activation_bits: u32,
    pub activation_storage_bytes: u32,
    pub weight_bits: u32,
    pub weight_storage_bytes: u32,
    pub dim: u32,
    pub weights: *const c_void,
    pub scales: *const i8,
    pub output: *mut i32,
    pub m: usize,
    pub n: usize,
    pub k: usize,
    pub weight_row_stride_bytes: usize,
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
    pub provider: ProviderC,
}

#[repr(C)]
pub struct ActivationStripeC {
    pub abi_version: u32,
    pub activation_bits: u32,
    pub activation_storage_bytes: u32,
    pub weight_bits: u32,
    pub weight_storage_bytes: u32,
    pub dim: u32,
    pub stripe_id: u32,
    pub i_start: usize,
    pub rows: usize,
    pub activations: *const c_void,
    pub activation_row_stride_bytes: usize,
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
    pub values: *const ActivationValue,
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
    pub failed: bool,
}
