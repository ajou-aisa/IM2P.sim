pub mod common;

use std::ffi::c_void;

use common::{structured_activations, structured_weights, KBlockScaleMatrix, Shape};
use im2p_sim::{Im2pSimulator, MatmulWork, MatrixView, MatrixViewMut, SimError, VectorOp};

unsafe extern "C" {
    fn im2p_create() -> *mut c_void;
    fn im2p_destroy(handle: *mut c_void);
    fn im2p_reset(handle: *mut c_void);
    fn im2p_eval(handle: *mut c_void);
    fn im2p_tick(handle: *mut c_void);
    fn im2p_cycle_count(handle: *mut c_void) -> u64;
    fn im2p_positive_edge_count(handle: *mut c_void) -> u64;
    fn im2p_begin_weight_load(handle: *mut c_void) -> i32;
}

#[test]
fn address_channels_commit_concurrently_without_serial_edges() -> Result<(), SimError> {
    let shape = Shape { m: 4, n: 4, k: 20 };
    let activations = structured_activations(shape);
    let weights = structured_weights(shape);
    let scales = KBlockScaleMatrix::from_fn(shape.k, 5, shape.n, |block, column| {
        ((block + column) % 3 + 1) as i8
    });
    let mut simulator = Im2pSimulator::new()?;
    let work = MatmulWork {
        activations: MatrixView::new(&activations, shape.m, shape.k, shape.k)?,
        weights: MatrixView::new(&weights, shape.k, shape.n, shape.n)?,
        scales: Some(scales.view(0, shape.n, 0xCA)),
        vector_op: VectorOp::Multiply,
    };
    let mut output = vec![0_i32; shape.m * shape.n];
    simulator.execute_matmul(
        &work,
        &mut MatrixViewMut::new(&mut output, shape.m, shape.n, shape.n)?,
    )?;
    let (observed, maximum) = simulator.response_concurrency();
    assert_eq!(
        observed, 0x0f,
        "A/W/S/C responses must all use staged edges"
    );
    assert!(maximum >= 2, "independent ready channels were serialized");
    Ok(())
}

#[test]
fn raw_clock_periods_are_exact() {
    // SAFETY: this test exclusively owns the raw handle until destroy.
    unsafe {
        let handle = im2p_create();
        assert!(!handle.is_null());
        im2p_reset(handle);
        assert_eq!(im2p_cycle_count(handle), 0, "reset is outside logical time");
        assert_eq!(im2p_positive_edge_count(handle), 0);

        for _ in 0..5 {
            im2p_eval(handle);
        }
        assert_eq!(im2p_cycle_count(handle), 0, "eval-only calls have no edge");
        assert_eq!(im2p_positive_edge_count(handle), 0);

        const TICKS: u64 = 11;
        for expected in 1..=TICKS {
            im2p_tick(handle);
            assert_eq!(im2p_cycle_count(handle), expected);
            assert_eq!(im2p_positive_edge_count(handle), expected);
        }

        let before = im2p_cycle_count(handle);
        assert_eq!(im2p_begin_weight_load(handle), 1);
        assert_eq!(
            im2p_cycle_count(handle) - before,
            1,
            "one pulse is one period"
        );
        assert_eq!(im2p_cycle_count(handle), im2p_positive_edge_count(handle));

        let before_host_work = im2p_cycle_count(handle);
        let mut checksum = 0_u64;
        for value in 0..100_000 {
            checksum = checksum.wrapping_add(value);
        }
        std::hint::black_box(checksum);
        assert_eq!(im2p_cycle_count(handle), before_host_work);

        std::thread::sleep(std::time::Duration::from_millis(5));
        assert_eq!(im2p_cycle_count(handle), before_host_work);
        im2p_destroy(handle);
    }
}
