use std::ptr;

use crate::{ActivationStripe, StripeLayout, StripeWorkDesc, WorkStats};

use super::{
    helpers::{scale_view, service_stream, vector_op, write_extended_stats, write_stats},
    types::{
        ActivationStripeC, PublishedStripe, StreamBox, StripeCompletionC, StripeWorkDescC,
        WorkStatsC, WorkStatsExtendedC,
    },
    SimBox,
};

#[no_mangle]
pub unsafe extern "C" fn im2p_begin_striped_matmul(
    sim: *mut SimBox,
    descriptor: *const StripeWorkDescC,
) -> *mut StreamBox {
    let mut stream = ptr::null_mut();
    if im2p_begin_striped_matmul_ex(sim, descriptor, &mut stream) == 0 {
        stream
    } else {
        ptr::null_mut()
    }
}

#[no_mangle]
pub unsafe extern "C" fn im2p_begin_striped_matmul_ex(
    sim: *mut SimBox,
    descriptor: *const StripeWorkDescC,
    output: *mut *mut StreamBox,
) -> i32 {
    let (Some(owner), Some(desc), Some(output)) =
        (sim.as_mut(), descriptor.as_ref(), output.as_mut())
    else {
        return -1;
    };
    *output = ptr::null_mut();
    let Some(op) = vector_op(desc.vector_op) else {
        return -1;
    };
    if desc.weights.is_null() || desc.output.is_null() {
        return -1;
    }
    if desc.weight_row_stride < desc.n || desc.output_row_stride < desc.n {
        return -4;
    }
    let Some(weight_len) = desc
        .k
        .checked_sub(1)
        .and_then(|rows| rows.checked_mul(desc.weight_row_stride))
        .and_then(|prefix| prefix.checked_add(desc.n))
    else {
        return -4;
    };
    let Some(dim) = owner
        .simulator
        .borrow()
        .as_ref()
        .map(crate::Im2pSimulator::dim)
    else {
        return -3;
    };
    let tile_i_rows = if desc.tile_i_rows == 0 {
        dim
    } else {
        desc.tile_i_rows
    };
    let tile_j_columns = if desc.tile_j_columns == 0 {
        dim
    } else {
        desc.tile_j_columns
    };
    let weights = std::slice::from_raw_parts(desc.weights, weight_len);
    let Ok(scale) = scale_view(
        desc.scales,
        desc.scale_values_len,
        desc.block_size,
        desc.scale_total_k,
        desc.n,
        desc.scale_row_stride,
        desc.scale_column_offset,
        desc.scale_valid_columns,
        desc.work_context,
    ) else {
        return -4;
    };
    let work = StripeWorkDesc {
        weights,
        scale_matrix: scale,
        rows: desc.m,
        columns: desc.n,
        reduction: desc.k,
        vector_op: op,
        work_context: desc.work_context,
    };
    if desc.m == 0
        || desc.n == 0
        || desc.k == 0
        || tile_i_rows == 0
        || tile_i_rows > dim
        || tile_j_columns == 0
        || tile_j_columns > dim
    {
        return -4;
    }
    if op != crate::VectorOp::Bypass && scale.is_none() {
        return -4;
    }
    if let Some(scales) = scale {
        if crate::simulator::validation::validate_scale_matrix(scales, desc.k, desc.n).is_err() {
            return -4;
        }
    }
    let Some(simulator) = owner.simulator.borrow_mut().take() else {
        return -3;
    };
    let layout = StripeLayout {
        weight_row_stride: desc.weight_row_stride,
        output_row_stride: desc.output_row_stride,
        tile_i_rows,
        tile_j_columns,
    };
    match simulator.begin_striped_matmul_layout(&work, layout) {
        Ok(job) => {
            *output = Box::into_raw(Box::new(StreamBox {
                owner: owner.simulator.clone(),
                job: Some(job),
                stripes: Vec::with_capacity(desc.stripe_count),
                output: desc.output,
                output_stride: desc.output_row_stride,
                columns: desc.n,
                reduction: desc.k,
            }));
            0
        }
        Err(_) => -4,
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
    let Some(job) = stream.job.as_mut() else {
        return -6;
    };
    match job.publish_stripe_layout(metadata, stripe.activation_row_stride) {
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
        Err(crate::SimError::DuplicateStripe) => -5,
        Err(crate::SimError::LateStripe) => -6,
        Err(crate::SimError::InvalidActivationStride) => -4,
        Err(_) => -1,
    }
}

#[no_mangle]
pub unsafe extern "C" fn im2p_progress_stream(stream: *mut StreamBox, cycle_budget: u64) -> i32 {
    let Some(stream) = stream.as_mut() else {
        return -1;
    };
    if service_stream(stream).is_err()
        || stream
            .job
            .as_mut()
            .is_none_or(|job| job.progress(cycle_budget).is_err())
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
    let Some(done) = stream.job.as_mut().and_then(|job| job.poll_completed()) else {
        return 0;
    };
    stream
        .stripes
        .retain(|stripe| stripe.row_begin != done.row_begin || stripe.row_count != done.row_count);
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
    match finish_stream_value(stream) {
        Ok(value) => {
            write_stats(stats, value);
            0
        }
        Err(status) => status,
    }
}

#[no_mangle]
pub unsafe extern "C" fn im2p_finish_stream_extended(
    stream: *mut StreamBox,
    stats: *mut WorkStatsExtendedC,
) -> i32 {
    match finish_stream_value(stream) {
        Ok(value) => {
            write_extended_stats(stats, value);
            0
        }
        Err(status) => status,
    }
}

unsafe fn finish_stream_value(stream: *mut StreamBox) -> Result<WorkStats, i32> {
    let Some(stream) = stream.as_mut() else {
        return Err(-1);
    };
    let Some(job) = stream.job.take() else {
        return Err(-1);
    };
    match job.finish_recover() {
        Ok((value, simulator)) => {
            *stream.owner.borrow_mut() = Some(simulator);
            Ok(value)
        }
        Err(_) => Err(-1),
    }
}

#[no_mangle]
pub unsafe extern "C" fn im2p_destroy_stream(stream: *mut StreamBox) {
    if stream.is_null() {
        return;
    }
    let mut stream = Box::from_raw(stream);
    if let Some(job) = stream.job.take() {
        let simulator = job.recover_unfinished();
        *stream.owner.borrow_mut() = Some(simulator);
    }
}
