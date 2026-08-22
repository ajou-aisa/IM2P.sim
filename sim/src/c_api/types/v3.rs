use std::ffi::c_void;

pub type WriteOutputV3 = crate::simulator::WriteProviderV3;

#[repr(C)]
#[derive(Clone, Copy)]
pub struct ProviderV3 {
    pub context: *mut c_void,
    pub read_weight: Option<crate::simulator::ReadProvider>,
    pub read_scale: Option<crate::simulator::ReadProvider>,
    pub write_output: Option<WriteOutputV3>,
}

impl From<ProviderV3> for crate::simulator::MemoryProvider {
    fn from(value: ProviderV3) -> Self {
        Self {
            context: value.context,
            read_weight: value.read_weight,
            read_scale: value.read_scale,
            write_output: value.write_output.map(crate::simulator::WriteProvider::V3),
        }
    }
}

#[repr(C)]
pub struct MatmulDescV3 {
    pub abi_version: u32,
    pub activation_bits: u32,
    pub activation_storage_bytes: u32,
    pub dim: u32,
    pub activations: *const c_void,
    pub weights: *const i8,
    pub scales: *const i8,
    pub output: *mut i32,
    pub m: usize,
    pub n: usize,
    pub k: usize,
    pub activation_row_stride_bytes: usize,
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
    pub provider: ProviderV3,
}

#[repr(C)]
pub struct StripeWorkDescV3 {
    pub abi_version: u32,
    pub activation_bits: u32,
    pub activation_storage_bytes: u32,
    pub dim: u32,
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
    pub provider: ProviderV3,
}

#[repr(C)]
pub struct ActivationStripeV3 {
    pub abi_version: u32,
    pub activation_bits: u32,
    pub activation_storage_bytes: u32,
    pub dim: u32,
    pub stripe_id: u32,
    pub i_start: usize,
    pub rows: usize,
    pub activations: *const c_void,
    pub activation_row_stride_bytes: usize,
    pub context: u64,
}
