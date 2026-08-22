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

#[repr(C)]
#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub struct ReadRequest {
    pub tag: u64,
    pub address: u64,
    pub element_count: u32,
}

#[repr(C)]
#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub struct WriteRequest {
    pub tag: u64,
    pub address: u64,
    pub element_count: u32,
}

#[repr(C)]
#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub struct StripeCompletion {
    pub stripe_id: u32,
    pub row_begin: u32,
    pub row_count: u32,
    pub stripe_context: u64,
}

#[repr(C)]
#[derive(Clone, Copy, Default, Debug)]
pub struct MatmulDescriptor {
    pub job_id: u32,
    pub mode: u8,
    pub activation_base: u64,
    pub weight_base: u64,
    pub scale_base: u64,
    pub output_base: u64,
    pub activation_row_stride: u64,
    pub weight_row_stride: u64,
    pub scale_row_stride: u64,
    pub output_row_stride: u64,
    pub row_count: u32,
    pub column_count: u32,
    pub reduction_count: u32,
    pub tile_i_rows: u32,
    pub tile_j_columns: u32,
    pub k_origin: u32,
    pub scale_total_k: u32,
    pub scale_block_size: u32,
    pub scale_context: u64,
    pub accumulate_first_fragment: i32,
    pub vector_op: u8,
}

#[repr(C)]
#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub struct MatrixCounters {
    pub fragments_completed: u64,
    pub works_completed: u64,
    pub stripes_published: u64,
    pub stripe_rows_published: u64,
    pub activation_read_requests: u64,
    pub weight_read_requests: u64,
    pub scale_read_requests: u64,
    pub output_write_requests: u64,
    pub output_write_responses: u64,
    pub weight_bank_activations: u64,
    pub activation_wait_cycles: u64,
    pub weight_wait_cycles: u64,
    pub output_wait_cycles: u64,
    pub stripe_host_wait_cycles: u64,
    pub compute_cycles: u64,
    pub drain_cycles: u64,
    pub weight_preload_cycles: u64,
    pub activation_overlap_cycles: u64,
    pub weight_overlap_cycles: u64,
    pub scale_overlap_cycles: u64,
    pub overlap_cycles: u64,
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

#[repr(C)]
#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub struct MatrixDebug {
    pub matmul_scheduler_state: u8,
    pub work_scheduler_state: u8,
    pub matrix_core_state: u8,
    pub active_weight_bank: i32,
    pub inactive_weight_bank_loading: i32,
    pub execution_active: i32,
    pub accepted_rows: u32,
    pub configured_rows: u32,
    pub first_column_issued: u32,
    pub first_column_committed: u32,
    pub engine_result_valid: i32,
    pub vector_busy: i32,
    pub activation_request_valid: i32,
    pub weight_request_valid: i32,
    pub scale_request_valid: i32,
    pub output_request_valid: i32,
    pub stripe_host_waiting: i32,
    pub lookahead_prepared: i32,
    pub lookahead_stripe_id: u32,
}

pub const IM2P_REQUEST_ABSENT: i32 = 0;
pub const IM2P_REQUEST_PRESENT: i32 = 1;

unsafe extern "C" {
    pub fn im2p_create() -> *mut c_void;
    pub fn im2p_destroy(handle: *mut c_void);
    pub fn im2p_reset(handle: *mut c_void);
    pub fn im2p_tick(handle: *mut c_void);
    pub fn im2p_tick_staged(handle: *mut c_void);
    pub fn im2p_cycle_count(handle: *mut c_void) -> u64;
    pub fn im2p_work_active(handle: *mut c_void) -> i32;
    pub fn im2p_work_cycle_count(handle: *mut c_void) -> u64;
    pub fn im2p_last_completed_work_cycles(handle: *mut c_void) -> u64;
    pub fn im2p_work_start_cycle(handle: *mut c_void) -> u64;
    pub fn im2p_work_completion_cycle(handle: *mut c_void) -> u64;
    pub fn im2p_observed_response_mask(handle: *mut c_void) -> u32;
    pub fn im2p_max_concurrent_responses(handle: *mut c_void) -> u32;
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
    pub fn im2p_write_accumulator_row_i64(handle: *mut c_void, row: u32, values: *const i64)
        -> i32;
    pub fn im2p_read_accumulator_row_i64(handle: *mut c_void, row: u32, values: *mut i64) -> i32;

    pub fn im2p_start_matmul(handle: *mut c_void, descriptor: *const MatmulDescriptor) -> i32;
    pub fn im2p_publish_activation_stripe(
        handle: *mut c_void,
        row_begin: u32,
        row_count: u32,
        row_stride: u64,
    ) -> i32;
    pub fn im2p_activation_stripe_ready(handle: *mut c_void) -> i32;
    pub fn im2p_matmul_done(handle: *mut c_void) -> i32;
    pub fn im2p_acknowledge_matmul(handle: *mut c_void) -> i32;

    pub fn im2p_activation_read_request(handle: *mut c_void, request: *mut ReadRequest) -> i32;
    pub fn im2p_weight_read_request(handle: *mut c_void, request: *mut ReadRequest) -> i32;
    pub fn im2p_scale_read_request(handle: *mut c_void, request: *mut ReadRequest) -> i32;

    pub fn im2p_stage_activation_read_response(
        handle: *mut c_void,
        tag: u64,
        values: *const i8,
        count: u32,
    ) -> i32;
    pub fn im2p_put_activation_read_response(
        handle: *mut c_void,
        tag: u64,
        values: *const i8,
        count: u32,
    ) -> i32;
    pub fn im2p_stage_weight_read_response(
        handle: *mut c_void,
        tag: u64,
        values: *const i8,
        count: u32,
    ) -> i32;
    pub fn im2p_stage_scale_read_response(
        handle: *mut c_void,
        tag: u64,
        values: *const i8,
        count: u32,
    ) -> i32;

    pub fn im2p_output_write_request_i64(
        handle: *mut c_void,
        request: *mut WriteRequest,
        values: *mut i64,
    ) -> i32;
    pub fn im2p_stage_output_write_response(handle: *mut c_void, tag: u64) -> i32;
    pub fn im2p_put_output_write_response(handle: *mut c_void, tag: u64) -> i32;
    pub fn im2p_stripe_completion(handle: *mut c_void, completion: *mut StripeCompletion) -> i32;
    pub fn im2p_stage_acknowledge_stripe_completion(handle: *mut c_void) -> i32;

    pub fn im2p_matrix_counters(handle: *mut c_void, counters: *mut MatrixCounters);
    pub fn im2p_matrix_debug(handle: *mut c_void, debug: *mut MatrixDebug);
}
