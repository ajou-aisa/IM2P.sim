//! Integration coverage for RTL-owned I/J/K work scheduling.
pub mod common;

use common::{
    assert_matrix_eq, golden_output, k_fragments, structured_activations, structured_weights, Shape,
};
use im2p_sim::{Im2pSimulator, MatmulWork, MatrixView, MatrixViewMut, SimError, VectorOp};

#[test]
fn rtl_scheduler_covers_all_i_j_k_fragments() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let shape = Shape {
        m: dim + 3,
        n: dim * 2 + 1,
        k: dim + 5,
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
    assert!(stats.completed_output_tiles >= 6);
    assert!(stats.completed_fragments > stats.completed_output_tiles);
    Ok(())
}

#[test]
fn scheduler_reuses_one_simulator_across_jobs() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    for extra in [1, 3] {
        let shape = Shape {
            m: dim + extra,
            n: dim + 1,
            k: dim + extra,
        };
        let activations = structured_activations(shape);
        let weights = structured_weights(shape);
        let mut actual = vec![0_i32; shape.m * shape.n];
        let work = MatmulWork {
            activations: MatrixView::new(&activations, shape.m, shape.k, shape.k)?,
            weights: MatrixView::new(&weights, shape.k, shape.n, shape.n)?,
            scales: None,
            vector_op: VectorOp::Bypass,
        };
        let mut output = MatrixViewMut::new(&mut actual, shape.m, shape.n, shape.n)?;
        simulator.execute_matmul(&work, &mut output)?;
    }
    Ok(())
}
