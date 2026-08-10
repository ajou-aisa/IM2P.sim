use std::ffi::c_void;

#[repr(C)]
pub struct ScaleMatrixView {
    pub values: *const i8,
    pub values_len: usize,
    pub block_size: usize,
    pub total_k: usize,
    pub columns: usize,
    pub row_stride: usize,
    pub column_offset: usize,
    pub valid_columns: usize,
    pub context: u64,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct ScaleCounters {
    pub demand_requests: u64,
    pub prefetch_requests: u64,
    pub current_hits: u64,
    pub next_hits: u64,
    pub demand_misses: u64,
    pub rows_received: u64,
    pub wait_cycles: u64,
}

unsafe extern "C" {
    pub fn im2p_create() -> *mut c_void;
    pub fn im2p_destroy(handle: *mut c_void);
    pub fn im2p_reset(handle: *mut c_void);
    pub fn im2p_tick(handle: *mut c_void);
    pub fn im2p_cycle_count(handle: *mut c_void) -> u64;
    pub fn im2p_weights_ready(handle: *mut c_void) -> i32;
    pub fn im2p_load_weight_ready(handle: *mut c_void) -> i32;
    pub fn im2p_activation_ready(handle: *mut c_void) -> i32;
    pub fn im2p_execution_done(handle: *mut c_void) -> i32;
    pub fn im2p_idle(handle: *mut c_void) -> i32;
    pub fn im2p_begin_weight_load(handle: *mut c_void) -> i32;
    pub fn im2p_load_weight_row(handle: *mut c_void, row: u32, values: *const i8) -> i32;
    pub fn im2p_configure_scaling(
        handle: *mut c_void,
        block_size: u32,
        total_k: u32,
        context: u64,
    ) -> i32;
    pub fn im2p_service_scale_request(handle: *mut c_void, view: *const ScaleMatrixView) -> i32;
    pub fn im2p_scale_counters(handle: *mut c_void, counters: *mut ScaleCounters);
    pub fn im2p_start_execution(
        handle: *mut c_void,
        base_row: u32,
        row_count: u32,
        accumulate: i32,
        vector_op: u8,
        k_start: u32,
        k_count: u32,
    ) -> i32;
    pub fn im2p_put_activation_row(handle: *mut c_void, values: *const i8) -> i32;
    pub fn im2p_acknowledge_execution(handle: *mut c_void) -> i32;
    pub fn im2p_write_accumulator_row(handle: *mut c_void, row: u32, values: *const i32) -> i32;
    pub fn im2p_read_accumulator_row(handle: *mut c_void, row: u32, values: *mut i32) -> i32;
}
