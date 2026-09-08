use std::ffi::c_void;

const INVALID_ARGUMENT: i32 = -1;
const BOUNDARIES: [i64; 6] = if im2p_sim::profile::IM2P_ACCUMULATOR_BITS == 32 {
    [0, -1, 2_147_483_647, -2_147_483_648, 1_234_567, -7_654_321]
} else {
    [0, -1, 2_147_483_648, -2_147_483_649, i64::MAX, i64::MIN]
};
const WORDS_PER_LANE: usize = im2p_sim::profile::IM2P_ACCUMULATOR_BITS / 32;

unsafe extern "C" {
    fn im2p_create() -> *mut c_void;
    fn im2p_destroy(handle: *mut c_void);
    fn im2p_compiled_dim() -> u32;
    fn im2p_cycle_count(handle: *mut c_void) -> u64;
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

#[test]
fn int32_preload_rejects_out_of_range_before_any_edge() {
    if im2p_sim::ACTIVATION_BITS == 16 {
        return;
    }
    // Given an invalid final lane after otherwise valid preload data.
    let (_, dim) = configuration();
    let handle = Handle::new();
    let original = vec![9_i64; dim];
    // SAFETY: handle and complete DIM-lane payload remain live for these calls.
    assert_eq!(
        unsafe { im2p_write_accumulator_row_i64(handle.0, 0, original.as_ptr()) },
        1
    );
    let before = unsafe { im2p_cycle_count(handle.0) };
    for invalid in [i64::from(i32::MAX) + 1, i64::from(i32::MIN) - 1] {
        let mut values = vec![17_i64; dim];
        values[dim - 1] = invalid;
        let status = unsafe { im2p_write_accumulator_row_i64(handle.0, 0, values.as_ptr()) };
        // Then rejection leaves logical time and the entire stored row unchanged.
        assert_eq!(status, INVALID_ARGUMENT);
        assert_eq!(unsafe { im2p_cycle_count(handle.0) }, before);
    }
    let mut stored = vec![0; dim];
    assert_eq!(
        unsafe { im2p_read_accumulator_row_i64(handle.0, 0, stored.as_mut_ptr()) },
        1
    );
    assert_eq!(stored, original);
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
        .map(|lane| {
            BOUNDARIES
                .get(lane)
                .copied()
                .unwrap_or(-1000 - i64::try_from(lane).unwrap())
        })
        .collect()
}

fn lane_words(values: &[i64]) -> Vec<u32> {
    values
        .iter()
        .flat_map(|value| {
            let bytes = value.to_le_bytes();
            let words = [
                u32::from_le_bytes(bytes[..4].try_into().expect("low word")),
                u32::from_le_bytes(bytes[4..].try_into().expect("high word")),
            ];
            words.into_iter().take(WORDS_PER_LANE)
        })
        .collect()
}

#[test]
fn accumulator_transport_round_trips_profile_width_signed_lanes() {
    // Given exact signed boundary lanes.
    let (dim, dim_usize) = configuration();
    let values = boundary_values(dim_usize);
    let expected_words = lane_words(&values);
    let handle = Handle::new();
    let mut words = vec![u32::MAX; dim_usize * WORDS_PER_LANE];

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

    // Then every lane follows its profile word stride and exact signed value.
    assert_eq!(words, expected_words);
    assert_eq!(round_trip, values);
    println!(
        "exact accumulator dim={dim} lanes={BOUNDARIES:?} lane_words={:?}",
        &words[..BOUNDARIES.len() * WORDS_PER_LANE]
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
        "exact output-request dim={dim} lanes={BOUNDARIES:?} lane_words={:?} compatibility={:?}",
        &words[..BOUNDARIES.len() * WORDS_PER_LANE],
        &compatibility[..BOUNDARIES.len()]
    );
}

#[test]
fn accumulator_transport_rejects_malformed_lane_and_word_counts() {
    // Given buffers large enough to expose malformed logical counts safely.
    let (dim, dim_usize) = configuration();
    let handle = Handle::new();
    let values = vec![0_i64; dim_usize + 1];
    let mut words = vec![0_u32; dim_usize * WORDS_PER_LANE];
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
