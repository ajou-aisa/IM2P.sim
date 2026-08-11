//! Actual-Verilated coverage for publish-triggered immediate lookahead.
pub mod common;

use common::{
    assert_matrix_eq, golden_output, k_fragments, structured_activations, structured_weights,
    KBlockScaleMatrix, Shape,
};
use im2p_sim::{ActivationStripe, Im2pSimulator, SimError, StripeLayout, StripeWorkDesc, VectorOp};

const LIMIT: u64 = 2_000;

fn stripe(id: u32, row: usize) -> ActivationStripe {
    ActivationStripe {
        stripe_id: id,
        row_begin: row,
        row_count: 1,
        stripe_context: 100 + id as u64,
    }
}

fn run(
    publish_cycles: &[u64],
    padded: bool,
    reduction: usize,
    tile_j_columns: usize,
    scaled: bool,
    exact_second_publish_cycle: Option<u64>,
) -> Result<(Vec<i32>, im2p_sim::WorkStats, Vec<u32>), SimError> {
    let shape = Shape {
        m: publish_cycles.len(),
        n: 3,
        k: reduction,
    };
    let packed_a = structured_activations(shape);
    let packed_w = structured_weights(shape);
    let scales = KBlockScaleMatrix::from_fn(shape.k, 16, shape.n, |block, column| {
        ((block + column) % 3 + 1) as i8
    });
    let a_stride = if padded { shape.k + 7 } else { shape.k };
    let w_stride = if padded { shape.n + 5 } else { shape.n };
    let c_stride = if padded { shape.n + 4 } else { shape.n };
    let mut activations = vec![0x55; shape.m * a_stride];
    let mut weights = vec![0x33; shape.k * w_stride];
    let mut output = vec![0x5a5a_5a5a; shape.m * c_stride];
    for row in 0..shape.m {
        activations[row * a_stride..][..shape.k]
            .copy_from_slice(&packed_a[row * shape.k..][..shape.k]);
    }
    for row in 0..shape.k {
        weights[row * w_stride..][..shape.n].copy_from_slice(&packed_w[row * shape.n..][..shape.n]);
    }

    let sim = Im2pSimulator::new()?;
    let dim = sim.dim();
    let desc = StripeWorkDesc {
        weights: &weights,
        scale_matrix: scaled.then(|| scales.view(0, shape.n, 77)),
        rows: shape.m,
        columns: shape.n,
        reduction: shape.k,
        vector_op: if scaled {
            VectorOp::Multiply
        } else {
            VectorOp::Bypass
        },
        work_context: 77,
    };
    let layout = StripeLayout {
        weight_row_stride: w_stride,
        output_row_stride: c_stride,
        tile_i_rows: 1,
        tile_j_columns,
    };
    let mut job = sim.begin_striped_matmul_layout(&desc, layout)?;
    let mut next = 0;
    let mut written = 0;
    let mut prepared_ids = Vec::new();
    for cycle in 0..LIMIT {
        while next < publish_cycles.len() && publish_cycles[next] <= cycle && job.npu_ready() {
            if next == 1 {
                if let Some(target) = exact_second_publish_cycle {
                    job.publish_stripe_layout_at_cycle(
                        stripe(next as u32, next),
                        a_stride,
                        target,
                    )?;
                } else {
                    job.publish_stripe_layout(stripe(next as u32, next), a_stride)?;
                }
            } else {
                job.publish_stripe_layout(stripe(next as u32, next), a_stride)?;
            }
            next += 1;
        }
        if let Some(row) = job.pending_activation_row() {
            job.supply_activation_row(row, &activations[row * a_stride..][..shape.k])?;
        }
        if let Some((row, column)) = job.pending_output_region() {
            let values = job.take_output_region(row, column)?;
            output[row * c_stride + column..][..values.len()].copy_from_slice(&values);
            written += values.len();
            job.acknowledge_output_row(row)?;
        }
        job.progress(1)?;
        if let Some(id) = job.prepared_lookahead_stripe_id() {
            if prepared_ids.last() != Some(&id) {
                prepared_ids.push(id);
            }
        }
        if next == shape.m && written == shape.m * shape.n {
            break;
        }
    }
    assert_eq!(next, shape.m, "not every stripe was published");
    assert_eq!(
        written,
        shape.m * shape.n,
        "stream did not finish within {LIMIT} cycles; pending activation={:?}",
        job.pending_activation_row()
    );
    let stats = job.finish()?;
    let mut packed_output = Vec::with_capacity(shape.m * shape.n);
    for row in 0..shape.m {
        packed_output.extend_from_slice(&output[row * c_stride..][..shape.n]);
        assert!(output[row * c_stride + shape.n..][..c_stride - shape.n]
            .iter()
            .all(|&value| value == 0x5a5a_5a5a));
    }
    let golden = golden_output(
        &packed_a,
        &packed_w,
        shape,
        0,
        shape.n,
        &k_fragments(
            shape.k,
            if scaled { scales.block_size } else { shape.k },
            dim,
        ),
        scaled.then_some(&scales),
        if scaled {
            VectorOp::Multiply
        } else {
            VectorOp::Bypass
        },
    );
    assert_matrix_eq(&packed_output, &golden, shape.m, shape.n);
    Ok((packed_output, stats, prepared_ids))
}

#[test]
fn publish_starts_immediate_a_w_preparation_before_current_completion() -> Result<(), SimError> {
    let (_, stats, _) = run(&[0, 13], false, 35, 2, false, Some(40))?;
    let (_, repeated, _) = run(&[0, 13], false, 35, 2, false, Some(40))?;
    assert!(stats.current_stripe_completion_cycle > 100);
    assert_eq!(stats.lookahead_publish_cycle, 40);
    assert!(stats.lookahead_publish_cycle <= stats.lookahead_first_activation_cycle);
    assert!(stats.lookahead_publish_cycle <= stats.lookahead_first_weight_cycle);
    assert!(stats.lookahead_first_activation_cycle < stats.current_stripe_completion_cycle);
    assert!(stats.lookahead_first_weight_cycle < stats.current_stripe_completion_cycle);
    assert!((40..100).contains(&stats.lookahead_first_activation_cycle));
    assert!((40..100).contains(&stats.lookahead_first_weight_cycle));
    assert!(stats.lookahead_ready_cycle > 0);
    assert!(stats.lookahead_ready_cycle <= stats.current_stripe_completion_cycle);
    assert!(stats.cross_stripe_overlap_cycles > 0);
    assert_eq!(
        stats.lookahead_start_cycle - stats.current_stripe_completion_cycle,
        3
    );
    assert_eq!(
        (
            stats.lookahead_publish_cycle,
            stats.lookahead_first_activation_cycle,
            stats.lookahead_first_weight_cycle,
            stats.lookahead_weight_preload_cycle,
            stats.lookahead_ready_cycle,
            stats.current_stripe_completion_cycle,
            stats.lookahead_start_cycle,
            stats.cross_stripe_overlap_cycles,
        ),
        (
            repeated.lookahead_publish_cycle,
            repeated.lookahead_first_activation_cycle,
            repeated.lookahead_first_weight_cycle,
            repeated.lookahead_weight_preload_cycle,
            repeated.lookahead_ready_cycle,
            repeated.current_stripe_completion_cycle,
            repeated.lookahead_start_cycle,
            repeated.cross_stripe_overlap_cycles,
        )
    );
    assert!(stats.lookahead_prepared);
    println!(
        "early publish={} a={} w={} preload={} scale={} ready={} complete={} start={} overlap={}",
        stats.lookahead_publish_cycle,
        stats.lookahead_first_activation_cycle,
        stats.lookahead_first_weight_cycle,
        stats.lookahead_weight_preload_cycle,
        stats.lookahead_scale_cycle,
        stats.lookahead_ready_cycle,
        stats.current_stripe_completion_cycle,
        stats.lookahead_start_cycle,
        stats.cross_stripe_overlap_cycles
    );
    Ok(())
}

#[test]
fn delayed_publish_and_padded_guards_match_cpu() -> Result<(), SimError> {
    let (padded, stats, _) = run(&[0, 37, 151, 200], true, 35, 2, false, None)?;
    let (packed, _, _) = run(&[0, 1, 2, 3], false, 35, 2, false, None)?;
    assert_eq!(padded, packed);
    assert_eq!(stats.stripe_host_wait_cycles, 8);
    assert!(stats.lookahead_start_cycle >= stats.current_stripe_completion_cycle);
    println!(
        "delayed complete={} start={} host_wait={}",
        stats.current_stripe_completion_cycle,
        stats.lookahead_start_cycle,
        stats.stripe_host_wait_cycles
    );
    Ok(())
}

#[test]
fn only_one_immediate_stripe_is_prepared_and_weights_are_reused() -> Result<(), SimError> {
    let (_, stats, prepared_ids) = run(&[0, 20, 21, 22], false, 16, 3, true, None)?;
    assert_eq!(stats.stripes_published, 4);
    assert!(stats.lookahead_weight_reuse_hits > 0);
    assert!(stats.lookahead_weight_requests < stats.weight_read_requests);
    assert_eq!(stats.lookahead_scale_requests, 0);
    assert!(stats.lookahead_scale_reuses > 0);
    assert!(stats.cross_stripe_overlap_cycles > 0);
    assert_eq!(prepared_ids, vec![1, 2, 3]);
    println!(
        "reuse w_requests={} w_hits={} s_requests={} s_hits={} overlap={}",
        stats.lookahead_weight_requests,
        stats.lookahead_weight_reuse_hits,
        stats.lookahead_scale_requests,
        stats.lookahead_scale_reuses,
        stats.cross_stripe_overlap_cycles
    );
    Ok(())
}

#[test]
fn scale_miss_is_requested_before_current_completion() -> Result<(), SimError> {
    let (_, stats, _) = run(&[0, 300], false, 35, 2, true, None)?;
    assert_eq!(stats.lookahead_scale_requests, 1);
    assert!(stats.lookahead_scale_cycle > stats.lookahead_publish_cycle);
    assert!(stats.lookahead_scale_cycle < stats.current_stripe_completion_cycle);
    println!(
        "scale miss publish={} request={} complete={}",
        stats.lookahead_publish_cycle,
        stats.lookahead_scale_cycle,
        stats.current_stripe_completion_cycle
    );
    Ok(())
}

#[test]
fn partial_preparation_reuses_every_fetched_weight_row() -> Result<(), SimError> {
    let partial_publish = if option_env!("IM2P_DIM") == Some("32") {
        455
    } else {
        282
    };
    let (_, partial, _) = run(&[0, partial_publish], false, 35, 2, false, None)?;
    let (_, complete, _) = run(&[0, 13], false, 35, 2, false, None)?;
    let dim = option_env!("IM2P_DIM")
        .unwrap_or("16")
        .parse::<u64>()
        .expect("valid test dimension");
    println!(
        "partial prefetch={} total={} full_total={} first_w={} complete={}",
        partial.lookahead_weight_requests,
        partial.weight_read_requests,
        complete.weight_read_requests,
        partial.lookahead_first_weight_cycle,
        partial.current_stripe_completion_cycle
    );
    assert!(partial.lookahead_first_weight_cycle < partial.current_stripe_completion_cycle);
    assert!(partial.lookahead_weight_requests > 0);
    assert!(partial.lookahead_weight_requests < dim);
    assert_eq!(partial.weight_read_requests, complete.weight_read_requests);
    Ok(())
}
