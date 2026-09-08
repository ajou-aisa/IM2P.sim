use std::ffi::c_void;

use im2p_sim::profile::{IM2P_ACCUMULATOR_BITS, IM2P_ACCUMULATOR_ROWS, IM2P_DIM};

unsafe extern "C" {
    fn im2p_create() -> *mut c_void;
    fn im2p_destroy(handle: *mut c_void);
    fn im2p_tick(handle: *mut c_void);
    fn im2p_cycle_count(handle: *mut c_void) -> u64;
    fn im2p_compiled_accumulator_bits() -> u32;
    fn im2p_compiled_accumulator_rows() -> u32;
    fn im2p_write_accumulator_row_i64(handle: *mut c_void, row: u32, values: *const i64) -> i32;
    fn im2p_read_accumulator_row_i64(handle: *mut c_void, row: u32, values: *mut i64) -> i32;
    fn im2p_request_accumulator_row_read(handle: *mut c_void, row: u32) -> i32;
    fn im2p_accumulator_row_read_response(handle: *mut c_void, values: *mut i64) -> i32;
    fn im2p_consume_accumulator_row_read_response(handle: *mut c_void) -> i32;
    fn im2p_begin_weight_load(handle: *mut c_void) -> i32;
    fn im2p_load_weight_row(handle: *mut c_void, row: u32, values: *const c_void) -> i32;
    fn im2p_weights_ready(handle: *mut c_void) -> i32;
    fn im2p_start_execution(
        handle: *mut c_void,
        base: u32,
        count: u32,
        accumulate: i32,
        operation: u8,
        k_start: u32,
        k_count: u32,
    ) -> i32;
    fn im2p_activation_ready(handle: *mut c_void) -> i32;
    fn im2p_put_activation_row(handle: *mut c_void, values: *const c_void) -> i32;
    fn im2p_execution_done(handle: *mut c_void) -> i32;
}

struct Handle(*mut c_void);
impl Handle {
    fn new() -> Self {
        let handle = unsafe { im2p_create() };
        assert!(!handle.is_null());
        Self(handle)
    }
    fn cycles(&self) -> u64 {
        unsafe { im2p_cycle_count(self.0) }
    }
    fn tick_until(&self, ready: unsafe extern "C" fn(*mut c_void) -> i32) {
        for _ in 0..10_000 {
            if unsafe { ready(self.0) } != 0 {
                return;
            }
            unsafe { im2p_tick(self.0) };
        }
        panic!("RTL transaction did not finish");
    }
    fn write(&self, row: u32, value: i64) {
        let values = vec![value; IM2P_DIM];
        assert_eq!(
            unsafe { im2p_write_accumulator_row_i64(self.0, row, values.as_ptr()) },
            1
        );
    }
    fn read(&self, row: u32) -> Vec<i64> {
        let mut values = vec![0_i64; IM2P_DIM];
        assert_eq!(
            unsafe { im2p_read_accumulator_row_i64(self.0, row, values.as_mut_ptr()) },
            1
        );
        values
    }
}
impl Drop for Handle {
    fn drop(&mut self) {
        unsafe { im2p_destroy(self.0) };
    }
}

#[test]
fn generated_profile_and_all_address_boundaries_agree() {
    let handle = Handle::new();
    let rows = u32::try_from(IM2P_ACCUMULATOR_ROWS).unwrap();
    assert_eq!(
        unsafe { im2p_compiled_accumulator_bits() },
        u32::try_from(IM2P_ACCUMULATOR_BITS).unwrap()
    );
    assert_eq!(unsafe { im2p_compiled_accumulator_rows() }, rows);
    assert_eq!(
        IM2P_ACCUMULATOR_ROWS * IM2P_DIM * IM2P_ACCUMULATOR_BITS,
        65536 * 8
    );
    for row in 0..rows {
        handle.write(row, -i64::from(row) - 100);
    }
    for row in 0..rows {
        assert_eq!(handle.read(row), vec![-i64::from(row) - 100; IM2P_DIM]);
    }
    let before = handle.cycles();
    let values = vec![0_i64; IM2P_DIM];
    for row in [rows, u32::MAX, 255, 256]
        .into_iter()
        .filter(|row| *row >= rows)
    {
        assert_eq!(
            unsafe { im2p_write_accumulator_row_i64(handle.0, row, values.as_ptr()) },
            -1
        );
        assert_eq!(
            unsafe { im2p_request_accumulator_row_read(handle.0, row) },
            -1
        );
    }
    assert_eq!(handle.cycles(), before);
}

#[test]
fn pending_read_is_observational_until_explicit_edges() {
    let handle = Handle::new();
    handle.write(0, -913);
    let before = handle.cycles();
    assert_eq!(unsafe { im2p_request_accumulator_row_read(handle.0, 0) }, 1);
    assert_eq!(handle.cycles(), before + 1);
    let mut values = vec![0_i64; IM2P_DIM];
    for _ in 0..8 {
        let before = handle.cycles();
        let ready = unsafe { im2p_accumulator_row_read_response(handle.0, values.as_mut_ptr()) };
        assert_eq!(handle.cycles(), before);
        if ready == 1 {
            break;
        }
        unsafe { im2p_tick(handle.0) };
    }
    let before = handle.cycles();
    for _ in 0..7 {
        assert_eq!(
            unsafe { im2p_accumulator_row_read_response(handle.0, values.as_mut_ptr()) },
            1
        );
        assert_eq!(values, vec![-913; IM2P_DIM]);
        assert_eq!(handle.cycles(), before);
    }
    assert_eq!(
        unsafe { im2p_consume_accumulator_row_read_response(handle.0) },
        1
    );
    assert_eq!(handle.cycles(), before + 1);
    assert_eq!(
        unsafe { im2p_accumulator_row_read_response(handle.0, values.as_mut_ptr()) },
        0
    );
}

#[test]
fn execution_base_and_count_pack_at_last_row_and_full_dim() {
    let rows = u32::try_from(IM2P_ACCUMULATOR_ROWS).unwrap();
    let dim = u32::try_from(IM2P_DIM).unwrap();
    for (base, count) in [(rows - 1, 1), (rows - dim, dim)] {
        let handle = Handle::new();
        let weights = vec![im2p_sim::parse_weight(0).unwrap(); IM2P_DIM];
        let activations = vec![im2p_sim::parse_activation(0).unwrap(); IM2P_DIM];
        handle.write(base - 1, 193);
        for row in base..base + count {
            handle.write(row, -357);
        }
        assert_eq!(unsafe { im2p_begin_weight_load(handle.0) }, 1);
        for row in 0..dim {
            assert_eq!(
                unsafe { im2p_load_weight_row(handle.0, row, weights.as_ptr().cast()) },
                1
            );
        }
        handle.tick_until(im2p_weights_ready);
        let before = handle.cycles();
        for (bad_base, bad_count) in [
            (rows - 1, 2),
            (rows, 1),
            (u32::MAX, dim),
            (0, 0),
            (0, dim + 1),
        ] {
            assert_eq!(
                unsafe { im2p_start_execution(handle.0, bad_base, bad_count, 0, 0, 0, 1) },
                -1
            );
        }
        assert_eq!(handle.cycles(), before);
        assert_eq!(
            unsafe { im2p_start_execution(handle.0, base, count, 0, 0, 0, 1) },
            1
        );
        for _ in 0..count {
            handle.tick_until(im2p_activation_ready);
            assert_eq!(
                unsafe { im2p_put_activation_row(handle.0, activations.as_ptr().cast()) },
                1
            );
        }
        handle.tick_until(im2p_execution_done);
        assert_eq!(handle.read(base - 1), vec![193; IM2P_DIM]);
        for row in base..base + count {
            assert_eq!(handle.read(row), vec![0; IM2P_DIM]);
        }
    }
}
