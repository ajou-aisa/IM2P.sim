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

#[test]
fn gemmini_publications_span_multiple_dim_works_without_k_republication() -> Result<(), SimError> {
    let simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let shape = Shape {
        m: 16 * dim,
        n: 1,
        k: 2 * dim + 1,
    };
    let activations = structured_activations(shape);
    let weights = structured_weights(shape);
    let mut job = simulator.begin_striped_matmul(&descriptor(shape, &weights))?;
    let publication_rows = [5 * dim, 5 * dim, 5 * dim, dim];
    let mut next_publication = 0;
    let mut row_begin = 0;
    let mut completed = Vec::new();

    for _ in 0..MAX_STEPS {
        if next_publication < publication_rows.len() && job.npu_ready() {
            let row_count = publication_rows[next_publication];
            job.publish_stripe(ActivationStripe {
                stripe_id: next_publication as u32,
                row_begin,
                row_count,
                stripe_context: 500 + next_publication as u64,
            })?;
            row_begin += row_count;
            next_publication += 1;
        }
        service(&mut job, &activations, shape, true)?;
        while let Some(event) = job.poll_completed() {
            completed.push(event);
        }
        if completed.len() == publication_rows.len() {
            break;
        }
    }

    assert_eq!(next_publication, publication_rows.len());
    assert_eq!(row_begin, shape.m);
    assert_eq!(
        completed
            .iter()
            .map(|event| event.row_count)
            .collect::<Vec<_>>(),
        publication_rows
    );
    let stats = job.finish()?;
    assert_eq!(stats.completed_output_tiles, 16);
    assert_eq!(stats.completed_fragments, 48);
    assert_eq!(stats.completed_stripes, 4);
    assert_eq!(stats.stripes_published, 4);
    assert_eq!(stats.stripe_rows_published, (16 * dim) as u64);
    Ok(())
}

#[test]
fn malformed_publication_sequences_reject_before_rtl_progress() -> Result<(), SimError> {
    let simulator = Im2pSimulator::new()?;
    let shape = Shape { m: 8, n: 3, k: 4 };
    let weights = structured_weights(shape);
    let mut job = simulator.begin_striped_matmul(&descriptor(shape, &weights))?;

    assert_eq!(
        job.publish_stripe(ActivationStripe {
            stripe_id: 1,
            row_begin: 0,
            row_count: 2,
            stripe_context: 1,
        }),
        Err(SimError::InvalidStripe)
    );
    job.publish_stripe(ActivationStripe {
        stripe_id: 0,
        row_begin: 0,
        row_count: 2,
        stripe_context: 2,
    })?;
    assert_eq!(
        job.publish_stripe(ActivationStripe {
            stripe_id: 1,
            row_begin: 1,
            row_count: 2,
            stripe_context: 3,
        }),
        Err(SimError::DuplicateStripe)
    );
    assert_eq!(
        job.progress_count(),
        0,
        "K fragments cannot publish stripes"
    );
    Ok(())
}
