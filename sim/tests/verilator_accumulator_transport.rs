use std::ffi::c_void;

const INVALID_ARGUMENT: i32 = -1;
const BOUNDARIES: [i64; 6] = [0, -1, 2_147_483_648, -2_147_483_649, i64::MAX, i64::MIN];

unsafe extern "C" {
    fn im2p_create() -> *mut c_void;
    fn im2p_destroy(handle: *mut c_void);
    fn im2p_compiled_dim() -> u32;
    fn im2p_write_accumulator_row_i64(handle: *mut c_void, row: u32, values: *const i64) -> i32;
    fn im2p_read_accumulator_row_i64(handle: *mut c_void, row: u32, values: *mut i64) -> i32;
    fn im2p_read_accumulator_row(handle: *mut c_void, row: u32, values: *mut i32) -> i32;
    fn im2p_test_accumulator_words(
        handle: *mut c_void,
        values: *const i64,
        count: u32,
        words: *mut u32,
        word_count: u32,
    ) -> i32;
    fn im2p_test_output_writeback(
        handle: *mut c_void,
        words: *const u32,
        word_count: u32,
        exact_values: *mut i64,
        compatibility_values: *mut i32,
        count: u32,
    ) -> i32;
}

struct Handle(*mut c_void);

impl Handle {
    fn new() -> Self {
        // SAFETY: the returned bridge handle is checked and owned by this guard.
        let handle = unsafe { im2p_create() };
        assert!(!handle.is_null());
        Self(handle)
    }
}

impl Drop for Handle {
    fn drop(&mut self) {
        // SAFETY: this guard exclusively owns the live bridge handle.
        unsafe { im2p_destroy(self.0) };
    }
}

fn configuration() -> (u32, usize) {
    // SAFETY: the compile-configuration query has no pointer arguments.
    let dim = unsafe { im2p_compiled_dim() };
    assert!(matches!(dim, 16 | 32 | 64));
    assert!(matches!(im2p_sim::ACTIVATION_BITS, 4 | 8 | 16));
    (dim, usize::try_from(dim).expect("DIM fits usize"))
}

fn boundary_values(dim: usize) -> Vec<i64> {
    (0..dim)
        .map(|lane| BOUNDARIES[lane % BOUNDARIES.len()])
        .collect()
}

fn lane_words(values: &[i64]) -> Vec<u32> {
    values
        .iter()
        .flat_map(|value| {
            let bytes = value.to_le_bytes();
            [
                u32::from_le_bytes(bytes[..4].try_into().expect("low word")),
                u32::from_le_bytes(bytes[4..].try_into().expect("high word")),
            ]
        })
        .collect()
}

#[test]
fn accumulator_transport_round_trips_signed_i64_lanes_with_two_word_stride() {
    // Given exact signed boundary lanes.
    let (dim, dim_usize) = configuration();
    let values = boundary_values(dim_usize);
    let expected_words = lane_words(&values);
    let handle = Handle::new();
    let mut words = vec![u32::MAX; dim_usize * 2];

    // When they cross the generated accumulator input and real accumulator row.
    // SAFETY: all buffers contain the counts passed to the synchronous bridge.
    assert_eq!(
        unsafe {
            im2p_test_accumulator_words(
                handle.0,
                values.as_ptr(),
                dim,
                words.as_mut_ptr(),
                u32::try_from(words.len()).expect("word count fits u32"),
            )
        },
        1
    );
    assert_eq!(
        unsafe { im2p_write_accumulator_row_i64(handle.0, 0, values.as_ptr()) },
        1
    );
    let mut round_trip = vec![0_i64; dim_usize];
    assert_eq!(
        unsafe { im2p_read_accumulator_row_i64(handle.0, 0, round_trip.as_mut_ptr()) },
        1
    );

    // Then each lane has independent low/high words and exact signed value.
    assert_eq!(words, expected_words);
    assert_eq!(round_trip, values);
    println!(
        "exact accumulator dim={dim} lanes={BOUNDARIES:?} word_pairs={:?}",
        &words[..BOUNDARIES.len() * 2]
    );
}

#[test]
fn output_writeback_reconstructs_i64_and_clamps_only_for_i32_compatibility() {
    // Given little-endian generated words for exact signed boundary lanes.
    let (dim, dim_usize) = configuration();
    let values = boundary_values(dim_usize);
    let words = lane_words(&values);
    let expected_compatibility: Vec<i32> = values
        .iter()
        .map(|value| {
            i32::try_from((*value).clamp(i64::from(i32::MIN), i64::from(i32::MAX)))
                .expect("clamped value fits i32")
        })
        .collect();
    let handle = Handle::new();
    let mut exact = vec![0_i64; dim_usize];
    let mut compatibility = vec![0_i32; dim_usize];

    // When exact and compatibility writeback consume the generated output port.
    // SAFETY: all buffers contain the counts passed to the synchronous bridge.
    assert_eq!(
        unsafe {
            im2p_test_output_writeback(
                handle.0,
                words.as_ptr(),
                u32::try_from(words.len()).expect("word count fits u32"),
                exact.as_mut_ptr(),
                compatibility.as_mut_ptr(),
                dim,
            )
        },
        1
    );
    assert_eq!(
        unsafe { im2p_write_accumulator_row_i64(handle.0, 1, values.as_ptr()) },
        1
    );
    let mut compatibility_read = vec![0_i32; dim_usize];
    assert_eq!(
        unsafe { im2p_read_accumulator_row(handle.0, 1, compatibility_read.as_mut_ptr()) },
        1
    );

    // Then exact lanes retain all bits and both compatibility paths clamp once.
    assert_eq!(exact, values);
    assert_eq!(compatibility, expected_compatibility);
    assert_eq!(compatibility_read, expected_compatibility);
    println!(
        "exact output-request dim={dim} lanes={BOUNDARIES:?} word_pairs={:?} compatibility={:?}",
        &words[..BOUNDARIES.len() * 2],
        &compatibility[..BOUNDARIES.len()]
    );
}

#[test]
fn accumulator_transport_rejects_malformed_lane_and_word_counts() {
    // Given buffers large enough to expose malformed logical counts safely.
    let (dim, dim_usize) = configuration();
    let handle = Handle::new();
    let values = vec![0_i64; dim_usize + 1];
    let mut words = vec![0_u32; dim_usize * 2];
    let word_count = u32::try_from(words.len()).expect("word count fits u32");

    // When lane or word counts disagree with the generated DIM contract.
    // SAFETY: backing buffers are deliberately larger than malformed counts.
    let bad_lanes = unsafe {
        im2p_test_accumulator_words(
            handle.0,
            values.as_ptr(),
            dim + 1,
            words.as_mut_ptr(),
            word_count,
        )
    };
    let bad_words = unsafe {
        im2p_test_accumulator_words(
            handle.0,
            values.as_ptr(),
            dim,
            words.as_mut_ptr(),
            word_count - 1,
        )
    };

    // Then both are rejected before signal access.
    assert_eq!(bad_lanes, INVALID_ARGUMENT);
    assert_eq!(bad_words, INVALID_ARGUMENT);
}
