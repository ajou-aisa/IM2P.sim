use std::ptr;

use crate::{ActivationStripe, Im2pSimulator, StripeWorkDesc};

mod helpers;
mod types;

use helpers::{execute_full, scale_view, service_stream, vector_op, write_stats};
use types::{
    ActivationStripeC, MatmulDesc, PublishedStripe, StreamBox, StripeCompletionC, StripeWorkDescC,
    WorkStatsC,
};

pub struct SimBox(Option<Im2pSimulator>);

#[no_mangle]
pub extern "C" fn im2p_sim_create() -> *mut SimBox {
    Im2pSimulator::new()
        .map(|sim| Box::into_raw(Box::new(SimBox(Some(sim)))))
        .unwrap_or(ptr::null_mut())
}

#[no_mangle]
pub unsafe extern "C" fn im2p_sim_destroy(sim: *mut SimBox) {
    if !sim.is_null() {
        drop(Box::from_raw(sim));
    }
}

#[no_mangle]
pub unsafe extern "C" fn im2p_execute_matmul(
    sim: *mut SimBox,
    descriptor: *const MatmulDesc,
    stats: *mut WorkStatsC,
) -> i32 {
    let Some(simulator) = sim.as_mut().and_then(|value| value.0.as_mut()) else {
        return -1;
    };
    let Some(desc) = descriptor.as_ref() else {
        return -1;
    };
    match execute_full(simulator, desc) {
        Ok(value) => {
            write_stats(stats, value);
            0
        }
        Err(_) => -1,
    }
}

#[no_mangle]
pub unsafe extern "C" fn im2p_begin_striped_matmul(
    sim: *mut SimBox,
    descriptor: *const StripeWorkDescC,
) -> *mut StreamBox {
    let Some(owner) = sim.as_mut() else {
        return ptr::null_mut();
    };
    let Some(simulator) = owner.0.take() else {
        return ptr::null_mut();
    };
    let Some(desc) = descriptor.as_ref() else {
        return ptr::null_mut();
    };
    let Some(op) = vector_op(desc.vector_op) else {
        return ptr::null_mut();
    };
    if desc.weights.is_null()
        || desc.output.is_null()
        || desc.weight_row_stride != desc.n
        || desc.output_row_stride < desc.n
    {
        return ptr::null_mut();
    }
    let weights = std::slice::from_raw_parts(desc.weights, desc.k * desc.weight_row_stride);
    let scale = scale_view(
        desc.scales,
        desc.scale_values_len,
        desc.block_size,
        desc.scale_total_k,
        desc.n,
        desc.scale_row_stride,
        desc.scale_column_offset,
        desc.scale_valid_columns,
        desc.work_context,
    );
    let work = StripeWorkDesc {
        weights,
        scale_matrix: scale,
        rows: desc.m,
        columns: desc.n,
        reduction: desc.k,
        vector_op: op,
        work_context: desc.work_context,
    };
    match simulator.begin_striped_matmul(&work) {
        Ok(job) => Box::into_raw(Box::new(StreamBox {
            job: Some(job),
            stripes: Vec::with_capacity(desc.stripe_count),
            output: desc.output,
            output_stride: desc.output_row_stride,
            columns: desc.n,
            reduction: desc.k,
        })),
        Err(_) => ptr::null_mut(),
    }
}

#[no_mangle]
pub unsafe extern "C" fn im2p_publish_stripe(
    stream: *mut StreamBox,
    stripe: *const ActivationStripeC,
) -> i32 {
    let Some(stream) = stream.as_mut() else {
        return -1;
    };
    let Some(stripe) = stripe.as_ref() else {
        return -1;
    };
    if stripe.activations.is_null() {
        return -1;
    }
    let metadata = ActivationStripe {
        stripe_id: stripe.stripe_id,
        row_begin: stripe.i_start,
        row_count: stripe.rows,
        stripe_context: stripe.context,
    };
    match stream.job.as_mut().unwrap().publish_stripe(metadata) {
        Ok(()) => {
            stream.stripes.push(PublishedStripe {
                row_begin: stripe.i_start,
                row_count: stripe.rows,
                values: stripe.activations,
                row_stride: stripe.activation_row_stride,
            });
            0
        }
        Err(crate::SimError::StripeQueueFull) => -2,
        Err(_) => -1,
    }
}

#[no_mangle]
pub unsafe extern "C" fn im2p_progress_stream(stream: *mut StreamBox, cycle_budget: u64) -> i32 {
    let Some(stream) = stream.as_mut() else {
        return -1;
    };
    if service_stream(stream).is_err()
        || stream.job.as_mut().unwrap().progress(cycle_budget).is_err()
    {
        -1
    } else {
        0
    }
}

#[no_mangle]
pub unsafe extern "C" fn im2p_poll_completed(
    stream: *mut StreamBox,
    completion: *mut StripeCompletionC,
) -> i32 {
    let Some(stream) = stream.as_mut() else {
        return -1;
    };
    let Some(output) = completion.as_mut() else {
        return -1;
    };
    let Some(done) = stream.job.as_mut().unwrap().poll_completed() else {
        return 0;
    };
    *output = StripeCompletionC {
        stripe_id: done.stripe_id,
        i_start: done.row_begin,
        rows: done.row_count,
        context: done.stripe_context,
    };
    1
}

#[no_mangle]
pub unsafe extern "C" fn im2p_finish_stream(stream: *mut StreamBox, stats: *mut WorkStatsC) -> i32 {
    let Some(stream) = stream.as_mut() else {
        return -1;
    };
    let Some(job) = stream.job.take() else {
        return -1;
    };
    match job.finish() {
        Ok(value) => {
            write_stats(stats, value);
            0
        }
        Err(_) => -1,
    }
}

#[no_mangle]
pub unsafe extern "C" fn im2p_destroy_stream(stream: *mut StreamBox) {
    if !stream.is_null() {
        drop(Box::from_raw(stream));
    }
}
