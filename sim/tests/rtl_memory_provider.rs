//! Address-backed host provider integration through RTL request channels.
pub mod common;

use common::{
    assert_matrix_eq, golden_output, k_fragments, structured_activations, structured_weights, Shape,
};
use im2p_sim::{Im2pSimulator, MatmulWork, MatrixView, MatrixViewMut, SimError, VectorOp};

fn strided<T: Copy + Default>(packed: &[T], rows: usize, columns: usize, stride: usize) -> Vec<T> {
    let mut values = vec![T::default(); rows * stride];
    for row in 0..rows {
        values[row * stride..row * stride + columns]
            .copy_from_slice(&packed[row * columns..][..columns]);
    }
    values
}

#[test]
fn provider_resolves_non_contiguous_a_w_c_addresses() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let shape = Shape {
        m: dim + 1,
        n: dim + 2,
        k: dim + 3,
    };
    let packed_a = structured_activations(shape);
    let packed_w = structured_weights(shape);
    let expected = golden_output(
        &packed_a,
        &packed_w,
        shape,
        0,
        shape.n,
        &k_fragments(shape.k, shape.k, dim),
        None,
        VectorOp::Bypass,
    );
    let a_stride = shape.k + 5;
    let w_stride = shape.n + 7;
    let c_stride = shape.n + 9;
    let activations = strided(&packed_a, shape.m, shape.k, a_stride);
    let weights = strided(&packed_w, shape.k, shape.n, w_stride);
    let mut output = vec![i32::MIN; shape.m * c_stride];
    let work = MatmulWork {
        activations: MatrixView::new(&activations, shape.m, shape.k, a_stride)?,
        weights: MatrixView::new(&weights, shape.k, shape.n, w_stride)?,
        scales: None,
        vector_op: VectorOp::Bypass,
    };
    let mut output_view = MatrixViewMut::new(&mut output, shape.m, shape.n, c_stride)?;
    simulator.execute_matmul(&work, &mut output_view)?;

    let mut packed = Vec::with_capacity(shape.m * shape.n);
    for row in 0..shape.m {
        packed.extend_from_slice(&output[row * c_stride..][..shape.n]);
        assert!(output[row * c_stride + shape.n..(row + 1) * c_stride]
            .iter()
            .all(|value| *value == i32::MIN));
    }
    assert_matrix_eq(&packed, &expected, shape.m, shape.n);
    Ok(())
}

#[test]
fn provider_counters_match_completed_channel_transactions() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let shape = Shape {
        m: dim + 1,
        n: dim + 1,
        k: dim + 1,
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
    assert!(stats.activation_read_requests > 0);
    assert!(stats.weight_read_requests > 0);
    assert_eq!(stats.scale_read_requests, 0);
    assert_eq!(stats.output_write_requests, stats.output_write_responses);
    Ok(())
}
