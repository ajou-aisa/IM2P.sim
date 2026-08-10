use std::ffi::c_void;

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
        block_count: u32,
    ) -> i32;
    pub fn im2p_scale_load_ready(handle: *mut c_void) -> i32;
    pub fn im2p_load_scale_block(handle: *mut c_void, scales: *const i8) -> i32;
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
