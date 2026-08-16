use std::ptr;

use crate::{ActivationStripe, StripeLayout, StripeWorkDesc, WorkStats};

use super::{
    helpers::{
        scale_view, service_stream, status_for_error, vector_op, write_extended_stats, write_stats,
    },
    types::{
        ActivationStripeC, PublishedStripe, StreamBox, StripeCompletionC, StripeWorkDescC,
        StripeWorkDescV1, WorkStatsC, WorkStatsExtendedC,
    },
    SimBox, PROVIDER_VERSION_1,
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
    begin_striped_matmul_value(sim, descriptor, output, None)
}

#[no_mangle]
pub unsafe extern "C" fn im2p_begin_striped_matmul_v1_ex(
    sim: *mut SimBox,
    descriptor: *const StripeWorkDescV1,
    output: *mut *mut StreamBox,
) -> i32 {
    let Some(descriptor) = descriptor.as_ref() else {
        return -1;
    };
    if descriptor.version != PROVIDER_VERSION_1 {
        if let Some(output) = output.as_mut() {
            *output = ptr::null_mut();
        }
        return -4;
    }
    begin_striped_matmul_value(
        sim,
        &descriptor.legacy,
        output,
        Some(descriptor.provider.into()),
    )
}

unsafe fn begin_striped_matmul_value(
    sim: *mut SimBox,
    descriptor: *const StripeWorkDescC,
    output: *mut *mut StreamBox,
    provider: Option<crate::simulator::MemoryProvider>,
) -> i32 {
    let (Some(owner), Some(desc), Some(output)) =
        (sim.as_mut(), descriptor.as_ref(), output.as_mut())
    else {
        return -1;
    };
    *output = ptr::null_mut();
    let Some(op) = vector_op(desc.vector_op) else {
        return status_for_error(crate::SimError::InvalidDimension);
    };
    if provider.is_some_and(|provider| {
        provider.read_weight.is_none()
            || provider.write_output.is_none()
            || (op != crate::VectorOp::Bypass && provider.read_scale.is_none())
    }) {
        return status_for_error(crate::SimError::InvalidLayout);
    }
    if provider.is_none() && (desc.weights.is_null() || desc.output.is_null()) {
        return -1;
    }
    if desc.weight_row_stride < desc.n {
        return status_for_error(crate::SimError::InvalidWeightStride);
    }
    if desc.output_row_stride < desc.n {
        return status_for_error(crate::SimError::InvalidOutputStride);
    }
    let Some(weight_len) = desc
        .k
        .checked_sub(1)
        .and_then(|rows| rows.checked_mul(desc.weight_row_stride))
        .and_then(|prefix| prefix.checked_add(desc.n))
    else {
        return status_for_error(crate::SimError::InvalidDimension);
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
    let weights = if provider.is_some() {
        &[]
    } else {
        std::slice::from_raw_parts(desc.weights, weight_len)
    };
    let scale = if provider.is_some() {
        None
    } else {
        match scale_view(
            desc.scales,
            desc.scale_values_len,
            desc.block_size,
            desc.scale_total_k,
            desc.n,
            desc.scale_row_stride,
            desc.scale_column_offset,
            desc.scale_valid_columns,
            desc.work_context,
        ) {
            Ok(scale) => scale,
            Err(error) => return status_for_error(error),
        }
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
        return status_for_error(crate::SimError::InvalidDimension);
    }
    if provider.is_none() && op != crate::VectorOp::Bypass && scale.is_none() {
        return status_for_error(crate::SimError::MissingScales { operation: op });
    }
    if let Some(scales) = scale {
        if let Err(error) =
            crate::simulator::validation::validate_scale_matrix(scales, desc.k, desc.n)
        {
            return status_for_error(error);
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
    let provider_block_size = provider.map(|_| desc.block_size);
    match simulator.begin_striped_matmul_provider_recover(
        &work,
        layout,
        provider,
        provider_block_size,
    ) {
        Ok(job) => {
            *output = Box::into_raw(Box::new(StreamBox {
                owner: owner.simulator.clone(),
                job: Some(job),
                stripes: Vec::with_capacity(desc.stripe_count),
                output: desc.output,
                output_stride: desc.output_row_stride,
                columns: desc.n,
                reduction: desc.k,
                failed: false,
            }));
            0
        }
        Err((error, simulator)) => {
            *owner.simulator.borrow_mut() = Some(simulator);
            status_for_striped_begin_error(error)
        }
    }
}

fn status_for_striped_begin_error(error: crate::SimError) -> i32 {
    status_for_error(error)
}

#[cfg(test)]
mod tests {
    use std::{cell::RefCell, ptr, rc::Rc};

    use super::{
        im2p_begin_striped_matmul_ex, im2p_destroy_stream, im2p_publish_stripe,
        status_for_striped_begin_error,
    };
    use crate::c_api::types::{ActivationStripeC, StripeWorkDescC};
    use crate::c_api::SimBox;
    use crate::{Im2pSimulator, SimError};

    fn descriptor(weights: &[i8], output: &mut [i32]) -> StripeWorkDescC {
        StripeWorkDescC {
            weights: weights.as_ptr(),
            scales: ptr::null(),
            output: output.as_mut_ptr(),
            m: 2,
            n: 2,
            k: 3,
            weight_row_stride: 2,
            output_row_stride: 2,
            tile_i_rows: 1,
            tile_j_columns: 1,
            block_size: 3,
            scale_total_k: 0,
            scale_row_stride: 0,
            scale_column_offset: 0,
            scale_valid_columns: 0,
            scale_values_len: 0,
            stripe_count: 1,
            vector_op: 0,
            work_context: 1,
        }
    }

    #[test]
    fn striped_begin_preserves_layout_and_runtime_status_classes() {
        assert_eq!(status_for_striped_begin_error(SimError::InvalidLayout), -4);
        assert_eq!(
            status_for_striped_begin_error(SimError::RtlNotReady {
                operation: "start_striped_matmul",
            }),
            -1
        );
    }

    #[test]
    fn rejected_begin_restores_simulator_and_contract_errors_remain_layout() {
        let weights = [1, 0, 0, 1, 1, 1];
        let mut output = [0; 4];
        let mut owner = SimBox {
            simulator: Rc::new(RefCell::new(Some(Im2pSimulator::new().unwrap()))),
        };
        let mut stream = ptr::null_mut();

        let mut invalid_op = descriptor(&weights, &mut output);
        invalid_op.vector_op = u8::MAX;
        assert_eq!(
            unsafe { im2p_begin_striped_matmul_ex(&mut owner, &invalid_op, &mut stream) },
            -4
        );
        assert!(stream.is_null());

        let mut oversized = descriptor(&weights, &mut output);
        oversized.m = u32::MAX as usize + 1;
        assert_eq!(
            unsafe { im2p_begin_striped_matmul_ex(&mut owner, &oversized, &mut stream) },
            -4
        );
        assert!(stream.is_null());

        let valid = descriptor(&weights, &mut output);
        assert_eq!(
            unsafe { im2p_begin_striped_matmul_ex(&mut owner, &valid, &mut stream) },
            0
        );
        assert!(!stream.is_null());

        let activations = [1, 2, 3, 4, 5, 6];
        let invalid_stripe = ActivationStripeC {
            stripe_id: 1,
            i_start: 0,
            rows: 2,
            activations: activations.as_ptr(),
            activation_row_stride: 3,
            context: 2,
        };
        assert_eq!(unsafe { im2p_publish_stripe(stream, &invalid_stripe) }, -4);
        unsafe { im2p_destroy_stream(stream) };
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
        Err(error) => status_for_error(error),
    }
}

#[no_mangle]
pub unsafe extern "C" fn im2p_progress_stream(stream: *mut StreamBox, cycle_budget: u64) -> i32 {
    let Some(stream) = stream.as_mut() else {
        return -1;
    };
    if stream.failed {
        return -1;
    }
    for _ in 0..cycle_budget {
        if let Err(error) = service_stream(stream) {
            stream.failed = true;
            return status_for_error(error);
        }
        let Some(job) = stream.job.as_mut() else {
            return -1;
        };
        if let Err(error) = job.progress(1) {
            stream.failed = true;
            return status_for_error(error);
        }
    }
    0
}

#[no_mangle]
pub unsafe extern "C" fn im2p_stream_cycle_count(stream: *const StreamBox) -> u64 {
    stream
        .as_ref()
        .and_then(|stream| stream.job.as_ref())
        .map_or(0, |job| job.cycles())
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
    if stream.failed {
        return Err(-1);
    }
    let Some(job) = stream.job.take() else {
        return Err(-1);
    };
    match job.finish_recover() {
        Ok((value, simulator)) => {
            *stream.owner.borrow_mut() = Some(simulator);
            Ok(value)
        }
        Err(error) => Err(status_for_error(error)),
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
