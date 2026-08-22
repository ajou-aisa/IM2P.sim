use std::ffi::c_void;

const PORT_ACTIVATION_ROW: u32 = 0;
const PORT_ACTIVATION_RESPONSE: u32 = 1;
const PORT_WEIGHT_RESPONSE: u32 = 2;
const PORT_SCALE_RESPONSE: u32 = 3;
const INVALID_ARGUMENT: i32 = -1;

unsafe extern "C" {
    fn im2p_create() -> *mut c_void;
    fn im2p_destroy(handle: *mut c_void);
    fn im2p_reset(handle: *mut c_void);
    fn im2p_compiled_activation_bits() -> u32;
    fn im2p_compiled_dim() -> u32;
    fn im2p_compiled_activation_storage_bytes() -> u32;
    fn im2p_test_drive_port(
        handle: *mut c_void,
        port: u32,
        values: *const c_void,
        count: u32,
    ) -> i32;
    fn im2p_test_copy_port_words(
        handle: *mut c_void,
        port: u32,
        words: *mut u32,
        word_count: u32,
    ) -> i32;
    fn im2p_test_activation_enable_mask(handle: *mut c_void) -> u32;
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

fn configuration() -> (u32, u32, u32) {
    // SAFETY: compile-configuration queries have no pointer arguments.
    unsafe {
        (
            im2p_compiled_activation_bits(),
            im2p_compiled_dim(),
            im2p_compiled_activation_storage_bytes(),
        )
    }
}

fn expected_words(values: &[i32], bits: u32, dim: u32) -> Vec<u32> {
    let mut words = vec![0_u32; (bits * dim).div_ceil(32) as usize];
    let mask = (1_u32 << bits) - 1;
    for (lane, value) in values.iter().enumerate() {
        let bit = lane as u32 * bits;
        words[(bit / 32) as usize] |= ((*value as u32) & mask) << (bit % 32);
    }
    words
}

fn capture(handle: &Handle, port: u32, word_count: usize) -> Vec<u32> {
    let mut words = vec![u32::MAX; word_count];
    // SAFETY: `words` has exactly word_count writable entries.
    let copied =
        unsafe { im2p_test_copy_port_words(handle.0, port, words.as_mut_ptr(), word_count as u32) };
    assert_eq!(copied, 1);
    words
}

fn drive_activations(handle: &Handle, port: u32, values: &[i32], bits: u32) -> i32 {
    // Keep host storage byte-per-value for A4/A8 and two-byte for A16.
    if bits == 16 {
        let stored: Vec<i16> = values.iter().map(|&value| value as i16).collect();
        // SAFETY: storage remains live and aligned for this synchronous FFI call.
        unsafe {
            im2p_test_drive_port(
                handle.0,
                port,
                stored.as_ptr().cast::<c_void>(),
                stored.len() as u32,
            )
        }
    } else {
        let stored: Vec<i8> = values.iter().map(|&value| value as i8).collect();
        // SAFETY: storage remains live for this synchronous FFI call.
        unsafe {
            im2p_test_drive_port(
                handle.0,
                port,
                stored.as_ptr().cast::<c_void>(),
                stored.len() as u32,
            )
        }
    }
}

#[test]
fn verilator_activation_packing_signed_lanes_and_zero_tails() {
    let (bits, dim, storage_bytes) = configuration();
    assert!(matches!(bits, 4 | 8 | 16));
    assert!(matches!(dim, 16 | 32));
    assert_eq!(bits, im2p_sim::ACTIVATION_BITS as u32);
    assert_eq!(storage_bytes, im2p_sim::ACTIVATION_STORAGE_BYTES as u32);
    assert_eq!(storage_bytes, if bits == 16 { 2 } else { 1 });

    let values: Vec<i32> = match bits {
        // Lanes 7/8 straddle a 32-bit word, and include both signed extrema.
        4 => vec![-8, -1, 0, 7, 1, -2, 3, -1, 7],
        8 => vec![-128, -1, 0, 127, 1],
        // Lanes 1/2 cross the first 32-bit word boundary.
        16 => vec![-32768, -1, 0, 32767, 1],
        _ => unreachable!(),
    };
    let expected = expected_words(&values, bits, dim);
    let handle = Handle::new();

    for port in [PORT_ACTIVATION_ROW, PORT_ACTIVATION_RESPONSE] {
        assert_eq!(drive_activations(&handle, port, &values, bits), 1);
        assert_eq!(capture(&handle, port, expected.len()), expected);
    }

    println!(
        "real FFI activation ports bits={bits} dim={dim} extrema={} packed={expected:08x?} zero_tail_bits={}",
        if bits == 4 {
            "[-8,-1,0,7]"
        } else if bits == 16 {
            "[-32768,-1,0,32767]"
        } else {
            "[-128,-1,0,127]"
        },
        bits * (dim - values.len() as u32),
    );
}

#[test]
fn verilator_activation_packing_preserves_i8_weight_and_scale_lanes() {
    let (_, dim, _) = configuration();
    let values = [-128_i8, -1, 0, 127, 0x12, 0x34];
    let expected = expected_words(
        &values
            .iter()
            .map(|&value| i32::from(value))
            .collect::<Vec<_>>(),
        8,
        dim,
    );
    let handle = Handle::new();

    for port in [PORT_WEIGHT_RESPONSE, PORT_SCALE_RESPONSE] {
        // SAFETY: values remains live for the synchronous call.
        assert_eq!(
            unsafe {
                im2p_test_drive_port(
                    handle.0,
                    port,
                    values.as_ptr().cast::<c_void>(),
                    values.len() as u32,
                )
            },
            1
        );
        assert_eq!(capture(&handle, port, expected.len()), expected);
    }

    println!("unchanged i8 weight/scale ports dim={dim} packed={expected:08x?}");
}

#[test]
fn rejects_out_of_range_a4_before_port_enable() {
    let (bits, _, _) = configuration();
    if bits != 4 {
        return;
    }
    let handle = Handle::new();
    let values = [-8_i8, 7, 8];

    for port in [PORT_ACTIVATION_ROW, PORT_ACTIVATION_RESPONSE] {
        // SAFETY: values remains live for the synchronous call.
        assert_eq!(
            unsafe {
                im2p_test_drive_port(
                    handle.0,
                    port,
                    values.as_ptr().cast::<c_void>(),
                    values.len() as u32,
                )
            },
            INVALID_ARGUMENT
        );
        // SAFETY: handle is valid and the query has no pointer argument.
        assert_eq!(unsafe { im2p_test_activation_enable_mask(handle.0) }, 0);
        // SAFETY: reset is used between independent malformed-input probes.
        unsafe { im2p_reset(handle.0) };
    }

    println!("A4 value 8 rejected before activation port enable");
}
