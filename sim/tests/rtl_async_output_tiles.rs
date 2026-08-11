pub mod common;

use common::{
    assert_matrix_eq, golden_output, k_fragments, structured_activations, structured_weights, Shape,
};
use im2p_sim::{ActivationStripe, Im2pSimulator, SimError, StripeWorkDesc, VectorOp};

#[test]
fn async_output_regions_preserve_column_tile_offsets() -> Result<(), SimError> {
    let simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let shape = Shape {
        m: 2,
        n: dim + 3,
        k: 4,
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
    let descriptor = StripeWorkDesc {
        weights: &weights,
        scale_matrix: None,
        rows: shape.m,
        columns: shape.n,
        reduction: shape.k,
        vector_op: VectorOp::Bypass,
        work_context: 73,
    };
    let mut job = simulator.begin_striped_matmul(&descriptor)?;
    job.publish_stripe(ActivationStripe {
        stripe_id: 0,
        row_begin: 0,
        row_count: shape.m,
        stripe_context: 91,
    })?;
    let mut output = vec![0_i32; shape.m * shape.n];
    let mut completion_seen = false;
    for _ in 0..100_000 {
        if let Some(row) = job.pending_activation_row() {
            let start = row * shape.k;
            job.supply_activation_row(row, &activations[start..][..shape.k])?;
        }
        if let Some((row, column)) = job.pending_output_region() {
            let values = job.take_output_region(row, column)?;
            output[row * shape.n + column..][..values.len()].copy_from_slice(&values);
            job.acknowledge_output_row(row)?;
        }
        job.progress(1)?;
        completion_seen |= job.poll_completed().is_some();
        if completion_seen {
            break;
        }
    }
    assert!(completion_seen);
    job.finish()?;
    assert_matrix_eq(&output, &expected, shape.m, shape.n);
    Ok(())
}
