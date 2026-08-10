use std::ffi::c_void;

#[repr(C)]
#[derive(Clone, Copy)]
struct RawScaleView {
    values: *const i8,
    values_len: usize,
    block_size: usize,
    total_k: usize,
    columns: usize,
    row_stride: usize,
    column_offset: usize,
    valid_columns: usize,
    context: u64,
}

unsafe extern "C" {
    fn im2p_create() -> *mut c_void;
    fn im2p_destroy(handle: *mut c_void);
    fn im2p_reset(handle: *mut c_void);
    fn im2p_begin_weight_load(handle: *mut c_void) -> i32;
    fn im2p_load_weight_row(handle: *mut c_void, row: u32, values: *const i8) -> i32;
    fn im2p_configure_scaling(
        handle: *mut c_void,
        block_size: u32,
        total_k: u32,
        context: u64,
    ) -> i32;
    fn im2p_start_execution(
        handle: *mut c_void,
        base_row: u32,
        row_count: u32,
        accumulate: i32,
        vector_op: u8,
        k_start: u32,
        k_count: u32,
    ) -> i32;
    fn im2p_service_scale_request(handle: *mut c_void, view: *const RawScaleView) -> i32;
}

pub fn assert_bad_response_identity_rejected() {
    let dim = option_env!("IM2P_DIM")
        .unwrap_or("16")
        .parse::<usize>()
        .expect("valid test dimension");
    let weights = vec![0_i8; dim];
    let values = [1_i8, 2];

    // SAFETY: handle is owned for this test, all arrays remain live during
    // synchronous calls, and destroy runs after the final assertion.
    unsafe {
        let handle = im2p_create();
        assert!(!handle.is_null());
        im2p_reset(handle);
        assert_eq!(im2p_begin_weight_load(handle), 1);
        for row in 0..dim {
            assert_eq!(
                im2p_load_weight_row(handle, row as u32, weights.as_ptr()),
                1
            );
        }
        assert_eq!(im2p_configure_scaling(handle, 1, 2, 100), 1);
        assert_eq!(im2p_start_execution(handle, 0, 1, 0, 1, 1, 1), 1);
        let wrong = RawScaleView {
            values: values.as_ptr(),
            values_len: values.len(),
            block_size: 1,
            total_k: 2,
            columns: 1,
            row_stride: 1,
            column_offset: 0,
            valid_columns: 1,
            context: 101,
        };
        assert_eq!(im2p_service_scale_request(handle, &wrong), -3);

        let wrong_block_range = RawScaleView {
            context: 100,
            total_k: 1,
            ..wrong
        };
        assert_eq!(im2p_service_scale_request(handle, &wrong_block_range), -5);

        let correct = RawScaleView {
            context: 100,
            total_k: 2,
            ..wrong
        };
        assert_eq!(im2p_service_scale_request(handle, &correct), 1);
        im2p_destroy(handle);
    }
}
