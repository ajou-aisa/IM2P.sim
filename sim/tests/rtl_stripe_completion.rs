pub mod common;

use common::{structured_activations, structured_weights, Shape};
use im2p_sim::{
    ActivationStripe, ActivationValue, Im2pSimulator, SimError, StripeWorkDesc, StripedMatmul,
    VectorOp, WeightValue,
};

const ROWS_PER_STRIPE: usize = 2;
const STRIPES: usize = 4;
const MAX_STEPS: usize = 100_000;

fn descriptor<'a>(shape: Shape, weights: &'a [WeightValue]) -> StripeWorkDesc<'a> {
    StripeWorkDesc {
        weights,
        scale_matrix: None,
        rows: shape.m,
        columns: shape.n,
        reduction: shape.k,
        vector_op: VectorOp::Bypass,
        work_context: 41,
    }
}

fn stripe(index: usize) -> ActivationStripe {
    ActivationStripe {
        stripe_id: index as u32,
        row_begin: index * ROWS_PER_STRIPE,
        row_count: ROWS_PER_STRIPE,
        stripe_context: 100 + index as u64,
    }
}

fn service(
    job: &mut StripedMatmul<'_>,
    activations: &[ActivationValue],
    shape: Shape,
    acknowledge_output: bool,
) -> Result<Option<usize>, SimError> {
    if let Some(row) = job.pending_activation_row() {
        let start = row * shape.k;
        job.supply_activation_row(row, &activations[start..][..shape.k])?;
    }
    let output_row = job.pending_output_row();
    if let Some(row) = output_row {
        let _ = job.take_output_row(row)?;
        if acknowledge_output {
            job.acknowledge_output_row(row)?;
        }
    }
    job.progress(1)?;
    Ok(output_row)
}

#[test]
fn completion_waits_for_final_output_acknowledgement() -> Result<(), SimError> {
    let shape = Shape { m: 8, n: 3, k: 4 };
    let activations = structured_activations(shape);
    let weights = structured_weights(shape);
    let mut job = Im2pSimulator::new()?.begin_striped_matmul(&descriptor(shape, &weights))?;
    job.publish_stripe(stripe(0))?;

    let mut held = None;
    for _ in 0..MAX_STEPS {
        if let Some(row) = service(&mut job, &activations, shape, false)? {
            if row + 1 == ROWS_PER_STRIPE {
                held = Some(row);
                break;
            }
            job.acknowledge_output_row(row)?;
        }
        assert!(job.poll_completed().is_none());
    }
    assert_eq!(held, Some(ROWS_PER_STRIPE - 1));
    assert!(job.poll_completed().is_none());
    job.acknowledge_output_row(ROWS_PER_STRIPE - 1)?;
    for _ in 0..MAX_STEPS {
        job.progress(1)?;
        if job.poll_completed().is_some() {
            return Ok(());
        }
    }
    panic!("completion not observed after final C acknowledgement");
}

#[test]
fn stripe_completions_preserve_publication_order_and_context() -> Result<(), SimError> {
    let shape = Shape { m: 8, n: 3, k: 4 };
    let activations = structured_activations(shape);
    let weights = structured_weights(shape);
    let mut job = Im2pSimulator::new()?.begin_striped_matmul(&descriptor(shape, &weights))?;
    let mut next_publish = 0;
    let mut completed = Vec::new();
    for _ in 0..MAX_STEPS {
        while next_publish < STRIPES && job.npu_ready() {
            job.publish_stripe(stripe(next_publish))?;
            next_publish += 1;
        }
        service(&mut job, &activations, shape, true)?;
        while let Some(event) = job.poll_completed() {
            completed.push(event);
        }
        if completed.len() == STRIPES {
            break;
        }
    }
    assert_eq!(
        completed
            .iter()
            .map(|event| (event.stripe_id, event.stripe_context))
            .collect::<Vec<_>>(),
        vec![(0, 100), (1, 101), (2, 102), (3, 103)]
    );
    job.finish()?;
    Ok(())
}
