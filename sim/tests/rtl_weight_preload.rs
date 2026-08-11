//! Dual-bank weight preload behavior through the real RTL scheduler.
pub mod common;

use common::{
    assert_matrix_eq, golden_output, k_fragments, structured_activations, structured_weights, Shape,
};
use im2p_sim::{Im2pSimulator, MatmulWork, MatrixView, MatrixViewMut, SimError, VectorOp};

#[test]
fn inactive_weight_preload_overlaps_without_corrupting_current_compute() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let shape = Shape {
        m: dim + 1,
        n: dim * 2 + 1,
        k: dim + 3,
    };
    let activations = structured_activations(shape);
    let weights = structured_weights(shape);
    let expected = golden_output(
        &activations,
        &weights,
        shape,
        0,
        shape.n,
        &k_fragments(shape.k, shape.k, dim),
        None,
        VectorOp::Bypass,
    );
    let mut actual = vec![0_i32; shape.m * shape.n];
    let work = MatmulWork {
        activations: MatrixView::new(&activations, shape.m, shape.k, shape.k)?,
        weights: MatrixView::new(&weights, shape.k, shape.n, shape.n)?,
        scales: None,
        vector_op: VectorOp::Bypass,
    };
    let mut output = MatrixViewMut::new(&mut actual, shape.m, shape.n, shape.n)?;
    let stats = simulator.execute_matmul(&work, &mut output)?;

    assert_matrix_eq(&actual, &expected, shape.m, shape.n);
    assert!(stats.weight_bank_activations > 1);
    assert!(stats.weight_preload_cycles > 0);
    assert!(stats.weight_overlap_cycles > 0);
    Ok(())
}

#[test]
fn repeated_jobs_keep_weight_bank_activation_accounting_local() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let shape = Shape {
        m: dim,
        n: dim + 1,
        k: dim + 1,
    };
    let activations = structured_activations(shape);
    let weights = structured_weights(shape);
    for _ in 0..2 {
        let mut output = vec![0_i32; shape.m * shape.n];
        let work = MatmulWork {
            activations: MatrixView::new(&activations, shape.m, shape.k, shape.k)?,
            weights: MatrixView::new(&weights, shape.k, shape.n, shape.n)?,
            scales: None,
            vector_op: VectorOp::Bypass,
        };
        let mut output_view = MatrixViewMut::new(&mut output, shape.m, shape.n, shape.n)?;
        let stats = simulator.execute_matmul(&work, &mut output_view)?;
        assert!(stats.weight_bank_activations > 0);
        assert!(stats.weight_bank_activations <= stats.completed_fragments);
    }
    Ok(())
}
