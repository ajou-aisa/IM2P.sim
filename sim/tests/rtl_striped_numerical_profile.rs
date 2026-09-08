pub mod common;
use common::{golden_output, k_fragments, KBlockScaleMatrix, Shape};
use im2p_sim::profile::IM2P_ACCUMULATOR_BITS;
use im2p_sim::{
    ActivationStripe, Im2pSimulator, MatmulWork, MatrixView, MatrixViewMut, SimError,
    StripeWorkDesc, VectorOp, WeightValue,
};
const CYCLE_BUDGET: u64 = 1;
const MAX_ITERATIONS: usize = 100_000;

#[test]
fn striped_output_sign_extends_profile_accumulators() -> Result<(), SimError> {
    // Given two striped rows whose shifted contributions exceed opposite i32 limits.
    let shape = Shape { m: 2, n: 1, k: 2 };
    let activations = vec![
        im2p_sim::parse_activation(1).expect("one is valid at every configured width"),
        im2p_sim::parse_activation(1).expect("one is valid at every configured width"),
        im2p_sim::parse_activation(-2).expect("negative two is valid at every configured width"),
        im2p_sim::parse_activation(-2).expect("negative two is valid at every configured width"),
    ];
    let weights: [WeightValue; 2] = [1, 1];
    let scales = KBlockScaleMatrix::from_fn(shape.k, 1, shape.n, |_, _| 30);
    let descriptor = StripeWorkDesc {
        weights: &weights,
        scale_matrix: Some(scales.view(0, shape.n, 0x53_49_36_34)),
        rows: shape.m,
        columns: shape.n,
        reduction: shape.k,
        vector_op: VectorOp::Shift,
        work_context: 0x53_49_36_34,
    };
    let mut job = Im2pSimulator::new()?.begin_striped_matmul(&descriptor)?;
    job.publish_stripe(ActivationStripe {
        stripe_id: 0,
        row_begin: 0,
        row_count: shape.m,
        stripe_context: 1,
    })?;
    let mut exact = Vec::with_capacity(shape.m);

    // When the host consumes the internal striped output requests.
    for _ in 0..MAX_ITERATIONS {
        if let Some(row) = job.pending_activation_row() {
            let start = row * shape.k;
            job.supply_activation_row(row, &activations[start..start + shape.k])?;
        }
        if let Some(row) = job.pending_output_row() {
            exact.extend(job.take_output_row_i64(row)?);
            job.acknowledge_output_row(row)?;
        }
        job.progress(CYCLE_BUDGET)?;
        if exact.len() == shape.m {
            break;
        }
    }
    job.finish()?;

    // Then no pre-provider or pre-raw narrowing has occurred.
    assert_eq!(
        exact,
        if IM2P_ACCUMULATOR_BITS == 32 {
            [i64::from(i32::MIN), 0]
        } else {
            [2_147_483_648, -4_294_967_296]
        }
    );
    Ok(())
}

#[test]
fn full_and_striped_share_profile_reference_before_final_saturation() -> Result<(), SimError> {
    // One deterministic matrix covers in-range, positive overflow, and negative
    // overflow across three independently published stripes.
    let shape = Shape { m: 3, n: 2, k: 2 };
    let activations = vec![
        im2p_sim::parse_activation(1).unwrap(),
        im2p_sim::parse_activation(2).unwrap(),
        im2p_sim::parse_activation(1).unwrap(),
        im2p_sim::parse_activation(1).unwrap(),
        im2p_sim::parse_activation(-2).unwrap(),
        im2p_sim::parse_activation(-2).unwrap(),
    ];
    let weights: [WeightValue; 4] = [1, -1, 1, 1];
    let scales = KBlockScaleMatrix::from_fn(shape.k, 1, shape.n, |_, _| 30);
    let fragments = k_fragments(shape.k, scales.block_size, 16);
    let oracle = golden_output(
        &activations,
        &weights,
        shape,
        0,
        shape.n,
        &fragments,
        Some(&scales),
        VectorOp::Shift,
    );
    assert_eq!(
        oracle,
        if IM2P_ACCUMULATOR_BITS == 32 {
            [-1_073_741_824, 1_073_741_824, -2_147_483_648, 0, 0, 0]
        } else {
            [
                3_221_225_472,
                1_073_741_824,
                2_147_483_648,
                0,
                -4_294_967_296,
                0,
            ]
        }
    );

    let work = MatmulWork {
        activations: MatrixView::new(&activations, shape.m, shape.k, shape.k)?,
        weights: MatrixView::new(&weights, shape.k, shape.n, shape.n)?,
        scales: Some(scales.view(0, shape.n, 0x50_41_52_49)),
        vector_op: VectorOp::Shift,
    };
    let mut full = vec![0_i32; shape.m * shape.n];
    Im2pSimulator::new()?.execute_matmul(
        &work,
        &mut MatrixViewMut::new(&mut full, shape.m, shape.n, shape.n)?,
    )?;
    let saturated = oracle
        .iter()
        .map(|&value| value.clamp(i64::from(i32::MIN), i64::from(i32::MAX)) as i32)
        .collect::<Vec<_>>();
    assert_eq!(full, saturated);

    let descriptor = StripeWorkDesc {
        weights: &weights,
        scale_matrix: Some(scales.view(0, shape.n, 0x50_41_52_49)),
        rows: shape.m,
        columns: shape.n,
        reduction: shape.k,
        vector_op: VectorOp::Shift,
        work_context: 0x50_41_52_49,
    };
    let mut striped = Im2pSimulator::new()?.begin_striped_matmul(&descriptor)?;
    let mut exact = vec![0_i64; shape.m * shape.n];
    let mut completed = 0;
    for row in 0..shape.m {
        striped.publish_stripe(ActivationStripe {
            stripe_id: row as u32,
            row_begin: row,
            row_count: 1,
            stripe_context: row as u64 + 1,
        })?;
        for _ in 0..MAX_ITERATIONS {
            if let Some(requested) = striped.pending_activation_row() {
                let start = requested * shape.k;
                striped.supply_activation_row(requested, &activations[start..start + shape.k])?;
            }
            if let Some(requested) = striped.pending_output_row() {
                let values = striped.take_output_row_i64(requested)?;
                exact[requested * shape.n..][..shape.n].copy_from_slice(&values);
                striped.acknowledge_output_row(requested)?;
            }
            striped.progress(CYCLE_BUDGET)?;
            while striped.poll_completed().is_some() {
                completed += 1;
            }
            if completed > row {
                break;
            }
        }
    }
    striped.finish()?;
    assert_eq!(completed, shape.m);
    assert_eq!(exact, oracle);
    println!(
        "FULL={full:?} PIPELINE_I64={exact:?} ORACLE_DELTA=0 V2_EXTREMA=[{},{}]",
        i32::MIN,
        i32::MAX
    );
    Ok(())
}
