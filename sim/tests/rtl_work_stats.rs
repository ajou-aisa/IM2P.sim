pub mod common;

use common::{structured_activations, structured_weights, Shape};
use im2p_sim::{Im2pSimulator, MatmulWork, MatrixView, MatrixViewMut, SimError, VectorOp};

#[test]
fn scheduler_cycle_counters_are_rtl_owned_and_measurable() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let shape = Shape {
        m: dim + 1,
        n: dim + 1,
        k: dim + 3,
    };
    let activations = structured_activations(shape);
    let weights = structured_weights(shape);
    let mut output = vec![0_i32; shape.m * shape.n];
    let work = MatmulWork {
        activations: MatrixView::new(&activations, shape.m, shape.k, shape.k)?,
        weights: MatrixView::new(&weights, shape.k, shape.n, shape.n)?,
        scales: None,
        vector_op: VectorOp::Bypass,
    };
    let mut output_view = MatrixViewMut::new(&mut output, shape.m, shape.n, shape.n)?;
    let stats = simulator.execute_matmul(&work, &mut output_view)?;

    assert!(stats.work_total_cycles > 0);
    assert!(stats.activation_read_requests > 0);
    assert!(stats.weight_read_requests > 0);
    assert!(stats.output_write_requests > 0);
    assert_eq!(stats.output_write_requests, stats.output_write_responses);
    assert!(stats.activation_wait_cycles > 0);
    assert!(stats.weight_wait_cycles > 0);
    assert!(stats.output_wait_cycles > 0);
    assert!(stats.compute_cycles > 0);
    assert!(stats.drain_cycles > 0);
    assert!(stats.completed_fragments > stats.completed_output_tiles);
    assert!(stats.weight_preload_cycles > 0);
    assert!(stats.overlap_cycles > 0);
    assert!(stats.weight_overlap_cycles > 0);
    assert!(stats.overlap_cycles <= stats.work_total_cycles);
    Ok(())
}
