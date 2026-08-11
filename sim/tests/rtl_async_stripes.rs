//! Stripe async API contract: deterministic, no threads/sleeps.
pub mod common;
use common::{
    assert_matrix_eq, golden_output, k_fragments, structured_activations, structured_weights, Shape,
};
use im2p_sim::{
    ActivationStripe, Im2pSimulator, MatmulWork, MatrixView, MatrixViewMut, SimError,
    StripeWorkDesc, VectorOp,
};
const CYCLE_BUDGET: u64 = 1;
const MAX_ITERATIONS: usize = 100_000;
const STRIPE_ROWS: usize = 2;
const STRIPE_COUNT: usize = 4;
fn cpu_golden(shape: Shape, activations: &[i8], weights: &[i8], dim: usize) -> Vec<i32> {
    golden_output(
        activations,
        weights,
        shape,
        0,
        shape.n,
        &k_fragments(shape.k, shape.k, dim),
        None,
        VectorOp::Bypass,
    )
}
struct HostMemory {
    activations: Vec<i8>,
    outputs: Vec<i32>,
    shape: Shape,
    activation_reads: Vec<usize>,
    output_writes: Vec<usize>,
}
impl HostMemory {
    fn new(shape: Shape, activations: Vec<i8>) -> Self {
        Self {
            activations,
            outputs: vec![0; shape.m * shape.n],
            shape,
            activation_reads: Vec::new(),
            output_writes: Vec::new(),
        }
    }
    fn activation_row(&mut self, row: usize) -> &[i8] {
        self.activation_reads.push(row);
        &self.activations[row * self.shape.k..][..self.shape.k]
    }
    fn write_output_row(&mut self, row: usize, values: &[i32]) {
        self.output_writes.push(row);
        self.outputs[row * self.shape.n..][..self.shape.n].copy_from_slice(values);
    }
}
fn stripe(i: usize) -> ActivationStripe {
    ActivationStripe {
        stripe_id: i as u32,
        row_begin: i * STRIPE_ROWS,
        row_count: STRIPE_ROWS,
        stripe_context: i as u64 + 1,
    }
}
fn work_desc<'a>(s: Shape, w: &'a [i8]) -> StripeWorkDesc<'a> {
    StripeWorkDesc {
        weights: w,
        scale_matrix: None,
        rows: s.m,
        columns: s.n,
        reduction: s.k,
        vector_op: VectorOp::Bypass,
        work_context: 0,
    }
}
fn tick(job: &mut im2p_sim::StripedMatmul, mem: &mut HostMemory) -> Result<(), SimError> {
    if let Some(row) = job.pending_activation_row() {
        job.supply_activation_row(row, &mem.activation_row(row).to_vec())?;
    }
    if let Some(row) = job.pending_output_row() {
        let v = job.take_output_row(row)?;
        mem.write_output_row(row, &v);
        job.acknowledge_output_row(row)?;
    }
    job.progress(CYCLE_BUDGET)?;
    Ok(())
}
#[test]
fn stripe_queue_applies_finite_backpressure() -> Result<(), SimError> {
    let sh = Shape { m: 8, n: 3, k: 4 };
    let weights = structured_weights(sh);
    let mut job = Im2pSimulator::new()?.begin_striped_matmul(&work_desc(sh, &weights))?;
    let mut n = 0;
    for i in 0..STRIPE_COUNT {
        if job.publish_stripe(stripe(i)).is_ok() {
            n += 1;
        } else {
            break;
        }
    }
    assert!(n >= 1 && n < 4);
    assert_eq!(
        job.publish_stripe(stripe(n)),
        Err(SimError::StripeQueueFull)
    );
    Ok(())
}
#[test]
fn no_activation_read_before_publish() -> Result<(), SimError> {
    let sh = Shape { m: 8, n: 3, k: 4 };
    let act = structured_activations(sh);
    let weights = structured_weights(sh);
    let mut mem = HostMemory::new(sh, act.clone());
    let mut job = Im2pSimulator::new()?.begin_striped_matmul(&work_desc(sh, &weights))?;
    for _ in 0..64 {
        tick(&mut job, &mut mem)?;
    }
    assert!(mem.activation_reads.is_empty());
    job.publish_stripe(stripe(0))?;
    for _ in 0..MAX_ITERATIONS {
        tick(&mut job, &mut mem)?;
        if job.poll_completed().is_some() {
            break;
        }
    }
    assert!(!mem.activation_reads.is_empty() && mem.activation_reads.iter().all(|&r| r < 2));
    Ok(())
}
#[test]
fn host_available_and_npu_ready_are_separate_states() -> Result<(), SimError> {
    let sh = Shape { m: 8, n: 3, k: 4 };
    let weights = structured_weights(sh);
    let mut mem = HostMemory::new(sh, structured_activations(sh));
    let mut job = Im2pSimulator::new()?.begin_striped_matmul(&work_desc(sh, &weights))?;
    assert!(job.npu_ready() && !job.host_available());
    job.publish_stripe(stripe(0))?;
    assert!(job.host_available());
    let mut sat = false;
    for i in 1..STRIPE_COUNT {
        if job.publish_stripe(stripe(i)) == Err(SimError::StripeQueueFull) {
            sat = true;
            break;
        }
    }
    assert!(sat && job.host_available() && !job.npu_ready());
    for _ in 0..MAX_ITERATIONS {
        tick(&mut job, &mut mem)?;
        while job.poll_completed().is_some() {}
        if job.npu_ready() && !job.host_available() {
            break;
        }
    }
    assert!(job.npu_ready() && !job.host_available());
    Ok(())
}
#[test]
fn immediate_publish_matches_execute_matmul() -> Result<(), SimError> {
    let sh = Shape { m: 8, n: 3, k: 4 };
    let act = structured_activations(sh);
    let wt = structured_weights(sh);
    let mut sim = Im2pSimulator::new()?;
    let mut sync = vec![0_i32; 24];
    let activation_view = MatrixView::new(&act, sh.m, sh.k, sh.k)?;
    let weight_view = MatrixView::new(&wt, sh.k, sh.n, sh.n)?;
    let sync_work = MatmulWork {
        activations: activation_view,
        weights: weight_view,
        scales: None,
        vector_op: VectorOp::Bypass,
    };
    let mut output_view = MatrixViewMut::new(&mut sync, sh.m, sh.n, sh.n)?;
    sim.execute_matmul(&sync_work, &mut output_view)?;
    let mut job = sim.begin_striped_matmul(&work_desc(sh, &wt))?;
    let mut mem = HostMemory::new(sh, act.to_vec());
    let mut publish_index = 0;
    for _ in 0..MAX_ITERATIONS {
        while publish_index < STRIPE_COUNT && job.npu_ready() {
            job.publish_stripe(stripe(publish_index))?;
            publish_index += 1;
        }
        tick(&mut job, &mut mem)?;
        if mem.output_writes.len() == 8 {
            break;
        }
    }
    job.finish()?;
    assert_matrix_eq(&mem.outputs, &sync, 8, 3);
    Ok(())
}
#[test]
fn deterministic_cycles_0_80_170() -> Result<(), SimError> {
    let sh = Shape { m: 8, n: 3, k: 4 };
    let act = structured_activations(sh);
    let wt = structured_weights(sh);
    let sim = Im2pSimulator::new()?;
    let exp = cpu_golden(sh, &act, &wt, sim.dim());
    let mut job = sim.begin_striped_matmul(&work_desc(sh, &wt))?;
    let mut mem = HostMemory::new(sh, act.to_vec());
    let cycles = [0u64, 80, 170, 170];
    let mut pub_idx = 0;
    for cyc in 0..(MAX_ITERATIONS as u64) {
        while pub_idx < STRIPE_COUNT && cycles[pub_idx] == cyc {
            job.publish_stripe(stripe(pub_idx))?;
            pub_idx += 1;
        }
        tick(&mut job, &mut mem)?;
        if mem.output_writes.len() == 8 {
            break;
        }
    }
    job.finish()?;
    assert_matrix_eq(&mem.outputs, &exp, 8, 3);
    Ok(())
}
#[test]
fn prior_stripe_compute_not_blocked_by_unpublished_next_stripe() -> Result<(), SimError> {
    let sh = Shape { m: 8, n: 3, k: 4 };
    let act = structured_activations(sh);
    let wt = structured_weights(sh);
    let sim = Im2pSimulator::new()?;
    let exp = cpu_golden(sh, &act, &wt, sim.dim());
    let mut mem = HostMemory::new(sh, act);
    let mut job = sim.begin_striped_matmul(&work_desc(sh, &wt))?;
    job.publish_stripe(stripe(0))?;
    for _ in 0..MAX_ITERATIONS {
        tick(&mut job, &mut mem)?;
        if mem.output_writes.len() == 2 {
            break;
        }
    }
    assert_eq!(mem.output_writes, (0..2).collect::<Vec<_>>());
    assert_matrix_eq(&mem.outputs[..6], &exp[..6], 2, 3);
    job.publish_stripe(stripe(1))?;
    for _ in 0..MAX_ITERATIONS {
        tick(&mut job, &mut mem)?;
        if mem.output_writes.len() == 4 {
            break;
        }
    }
    Ok(())
}
