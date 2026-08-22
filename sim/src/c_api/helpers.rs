use std::{
    mem::{align_of, size_of},
    ptr, slice,
};

use crate::{
    activation_bytes_to_elements, activation_view, validate_activation_values, ActivationError,
    ActivationValue, Im2pSimulator, KBlockScaleMatrixView, MatmulLayout, MatmulWork, MatrixView,
    MatrixViewMut, VectorOp, WorkStats,
};

use super::{
    configuration_matches,
    types::{MatmulDesc, MatmulDescV2, ProviderC, StreamBox, WorkStatsC, WorkStatsExtendedC},
};

pub(super) fn status_for_error(error: crate::SimError) -> i32 {
    use crate::SimError::*;
    match error {
        StripeQueueFull => -2,
        UnfinishedStream => -3,
        DuplicateStripe => -5,
        LateStripe => -6,
        InvalidDimension
        | InvalidScaleMatrixLayout
        | InvalidBufferLength { .. }
        | MissingScales { .. }
        | InvalidKRange
        | UnsupportedBlockConfiguration { .. }
        | InvalidAccumulatorRow { .. }
        | InvalidTileShape
        | InvalidStripe
        | InvalidActivationStride
        | InvalidWeightStride
        | InvalidOutputStride
        | InvalidLayout => -4,
        AllocationFailed
        | InvalidScaleRequest { .. }
        | RtlNotReady { .. }
        | NoPendingActivation
        | NoPendingOutput
        | ProviderFailure
        | Timeout { .. } => -1,
    }
}

pub(super) unsafe fn parse_matmul_v2(
    desc: &MatmulDescV2,
) -> Result<(MatmulDesc, Option<crate::simulator::MemoryProvider>), i32> {
    if !configuration_matches(
        desc.abi_version,
        desc.activation_bits,
        desc.activation_storage_bytes,
        desc.dim,
    ) {
        return Err(-7);
    }
    if desc.activations.is_null()
        || (desc.activations as usize) % align_of::<ActivationValue>() != 0
    {
        return Err(-4);
    }
    let activation_row_stride =
        activation_bytes_to_elements(desc.activation_row_stride_bytes).map_err(|_| -4)?;
    let parsed = MatmulDesc {
        activations: desc.activations.cast(),
        weights: desc.weights,
        scales: desc.scales,
        output: desc.output,
        m: desc.m,
        n: desc.n,
        k: desc.k,
        activation_row_stride,
        weight_row_stride: desc.weight_row_stride,
        output_row_stride: desc.output_row_stride,
        tile_i_rows: desc.tile_i_rows,
        tile_j_columns: desc.tile_j_columns,
        block_size: desc.block_size,
        scale_total_k: desc.scale_total_k,
        scale_row_stride: desc.scale_row_stride,
        scale_column_offset: desc.scale_column_offset,
        scale_valid_columns: desc.scale_valid_columns,
        scale_values_len: desc.scale_values_len,
        vector_op: desc.vector_op,
        work_context: desc.work_context,
    };
    validate_activation_desc(&parsed).map_err(status_for_error)?;
    let provider = provider_from_v2(desc.provider)?;
    if provider.is_none() && (parsed.weights.is_null() || parsed.output.is_null()) {
        return Err(-1);
    }
    if provider.is_some() {
        validate_provider_rtl_fields(&parsed)?;
    }
    Ok((parsed, provider))
}

pub(super) fn validate_provider_rtl_fields(desc: &MatmulDesc) -> Result<(), i32> {
    let tile_i_rows = if desc.tile_i_rows == 0 {
        super::configured_dim() as usize
    } else {
        desc.tile_i_rows
    };
    let tile_j_columns = if desc.tile_j_columns == 0 {
        super::configured_dim() as usize
    } else {
        desc.tile_j_columns
    };
    crate::activation::activation_elements_to_address_bytes(desc.activation_row_stride)
        .map_err(|_| crate::SimError::InvalidLayout)
        .and_then(|_| crate::simulator::descriptor::u64_field(desc.weight_row_stride))
        .and_then(|_| crate::simulator::descriptor::u64_field(desc.n))
        .and_then(|_| crate::simulator::descriptor::output_row_stride_bytes(desc.output_row_stride))
        .and_then(|_| crate::simulator::descriptor::u32_field(desc.m))
        .and_then(|_| crate::simulator::descriptor::u32_field(desc.n))
        .and_then(|_| crate::simulator::descriptor::u32_field(desc.k))
        .and_then(|_| crate::simulator::descriptor::u32_field(tile_i_rows))
        .and_then(|_| crate::simulator::descriptor::u32_field(tile_j_columns))
        .and_then(|_| crate::simulator::descriptor::u32_field(desc.block_size.max(1)))
        .map(|_| ())
        .map_err(|_| -4)
}

fn provider_from_v2(provider: ProviderC) -> Result<Option<crate::simulator::MemoryProvider>, i32> {
    let any = provider.read_weight.is_some()
        || provider.read_scale.is_some()
        || provider.write_output.is_some();
    if !any {
        return Ok(None);
    }
    if provider.read_weight.is_none() || provider.write_output.is_none() {
        return Err(-4);
    }
    Ok(Some(provider.into()))
}

unsafe fn validate_activation_desc(desc: &MatmulDesc) -> Result<(), crate::SimError> {
    let activation_len = matrix_len(
        desc.m,
        desc.k,
        desc.activation_row_stride,
        size_of::<ActivationValue>(),
    )?;
    let activations = slice::from_raw_parts(desc.activations, activation_len);
    activation_view(activations, desc.m, desc.k, desc.activation_row_stride)
        .map(|_| ())
        .map_err(activation_error)
}

fn activation_error(error: ActivationError) -> crate::SimError {
    match error {
        ActivationError::InvalidLayout(error) => error,
        ActivationError::ValueOutOfRange { .. }
        | ActivationError::ByteCountOverflow { .. }
        | ActivationError::MisalignedByteCount { .. } => crate::SimError::InvalidLayout,
    }
}

pub(super) unsafe fn execute_full(
    simulator: &mut Im2pSimulator,
    desc: &MatmulDesc,
) -> Result<WorkStats, crate::SimError> {
    let op = vector_op(desc.vector_op).ok_or(crate::SimError::InvalidDimension)?;
    if desc.activations.is_null() || desc.weights.is_null() || desc.output.is_null() {
        return Err(crate::SimError::InvalidDimension);
    }
    let activation_len = matrix_len(
        desc.m,
        desc.k,
        desc.activation_row_stride,
        size_of::<ActivationValue>(),
    )?;
    let weight_len = matrix_len(desc.k, desc.n, desc.weight_row_stride, 1)?;
    let output_len = matrix_len(desc.m, desc.n, desc.output_row_stride, size_of::<i32>())?;
    let activations = slice::from_raw_parts(desc.activations, activation_len);
    let weights = slice::from_raw_parts(desc.weights, weight_len);
    let output = slice::from_raw_parts_mut(desc.output, output_len);
    let work = MatmulWork {
        activations: activation_view(activations, desc.m, desc.k, desc.activation_row_stride)
            .map_err(activation_error)?,
        weights: MatrixView::new(weights, desc.k, desc.n, desc.weight_row_stride)?,
        scales: scale_view(
            desc.scales,
            desc.scale_values_len,
            desc.block_size,
            desc.scale_total_k,
            desc.n,
            desc.scale_row_stride,
            desc.scale_column_offset,
            desc.scale_valid_columns,
            desc.work_context,
        )?,
        vector_op: op,
    };
    let mut output = MatrixViewMut::new(output, desc.m, desc.n, desc.output_row_stride)?;
    simulator.execute_matmul_layout(
        &work,
        &mut output,
        MatmulLayout {
            tile_i_rows: if desc.tile_i_rows == 0 {
                simulator.dim()
            } else {
                desc.tile_i_rows
            },
            tile_j_columns: if desc.tile_j_columns == 0 {
                simulator.dim()
            } else {
                desc.tile_j_columns
            },
        },
    )
}

pub(super) unsafe fn execute_full_provider(
    simulator: &mut Im2pSimulator,
    desc: &MatmulDesc,
    provider: crate::simulator::MemoryProvider,
) -> Result<WorkStats, crate::SimError> {
    let op = vector_op(desc.vector_op).ok_or(crate::SimError::InvalidDimension)?;
    if desc.activations.is_null() {
        return Err(crate::SimError::InvalidDimension);
    }
    let activation_len = matrix_len(
        desc.m,
        desc.k,
        desc.activation_row_stride,
        size_of::<ActivationValue>(),
    )?;
    let activations = slice::from_raw_parts(desc.activations, activation_len);
    let activations = activation_view(activations, desc.m, desc.k, desc.activation_row_stride)
        .map_err(activation_error)?;
    simulator.execute_matmul_provider(
        activations,
        desc.m,
        desc.n,
        desc.k,
        desc.weight_row_stride,
        desc.output_row_stride,
        desc.block_size,
        op,
        desc.work_context,
        MatmulLayout {
            tile_i_rows: if desc.tile_i_rows == 0 {
                simulator.dim()
            } else {
                desc.tile_i_rows
            },
            tile_j_columns: if desc.tile_j_columns == 0 {
                simulator.dim()
            } else {
                desc.tile_j_columns
            },
        },
        provider,
    )
}

pub(super) unsafe fn service_stream(stream: &mut StreamBox) -> Result<(), crate::SimError> {
    let job = stream
        .job
        .as_mut()
        .ok_or(crate::SimError::InvalidDimension)?;
    if let Some(row) = job.pending_activation_row() {
        let stripe = stream
            .stripes
            .iter()
            .find(|stripe| row >= stripe.row_begin && row < stripe.row_begin + stripe.row_count)
            .ok_or(crate::SimError::NoPendingActivation)?;
        let local = row - stripe.row_begin;
        let values = slice::from_raw_parts(
            stripe.values.add(local * stripe.row_stride),
            stream.reduction,
        );
        validate_activation_values(values).map_err(activation_error)?;
        job.stage_activation_row(row, values)?;
    }
    if !job.provider_handles_output() {
        if let Some((row, column)) = job.pending_output_region() {
            if column >= stream.columns {
                return Err(crate::SimError::InvalidLayout);
            }
            let values = job.take_output_region(row, column)?;
            ptr::copy_nonoverlapping(
                values.as_ptr(),
                stream.output.add(row * stream.output_stride + column),
                values.len().min(stream.columns - column),
            );
            job.stage_output_row(row)?;
        }
    }
    Ok(())
}

pub(super) fn vector_op(value: u8) -> Option<VectorOp> {
    match value {
        0 => Some(VectorOp::Bypass),
        1 => Some(VectorOp::Multiply),
        2 => Some(VectorOp::Shift),
        3 => Some(VectorOp::External),
        _ => None,
    }
}

pub(super) unsafe fn scale_view(
    values: *const i8,
    len: usize,
    block_size: usize,
    total_k: usize,
    columns: usize,
    row_stride: usize,
    column_offset: usize,
    valid_columns: usize,
    context: u64,
) -> Result<Option<KBlockScaleMatrixView<'static>>, crate::SimError> {
    if len > isize::MAX as usize {
        return Err(crate::SimError::InvalidScaleMatrixLayout);
    }
    Ok((!values.is_null()).then(|| KBlockScaleMatrixView {
        values: slice::from_raw_parts(values, len),
        block_size,
        total_k,
        columns,
        row_stride,
        column_offset,
        valid_columns,
        context,
    }))
}

fn matrix_len(
    rows: usize,
    columns: usize,
    row_stride: usize,
    element_size: usize,
) -> Result<usize, crate::SimError> {
    if rows == 0 || columns == 0 || row_stride < columns {
        return Err(crate::SimError::InvalidDimension);
    }
    let len = (rows - 1)
        .checked_mul(row_stride)
        .and_then(|prefix| prefix.checked_add(columns))
        .ok_or(crate::SimError::InvalidDimension)?;
    if len > isize::MAX as usize / element_size {
        return Err(crate::SimError::InvalidDimension);
    }
    Ok(len)
}

pub(super) fn write_stats(output: *mut WorkStatsC, value: WorkStats) {
    let Some(output) = (unsafe { output.as_mut() }) else {
        return;
    };
    *output = WorkStatsC {
        work_total_cycles: value.work_total_cycles,
        activation_read_requests: value.activation_read_requests,
        weight_read_requests: value.weight_read_requests,
        scale_read_requests: value.scale_read_requests,
        output_write_requests: value.output_write_requests,
        output_write_responses: value.output_write_responses,
        activation_wait_cycles: value.activation_wait_cycles,
        weight_wait_cycles: value.weight_wait_cycles,
        scale_wait_cycles: value.scale_wait_cycles,
        output_wait_cycles: value.output_wait_cycles,
        stripe_host_wait_cycles: value.stripe_host_wait_cycles,
        drain_cycles: value.drain_cycles,
        weight_preload_cycles: value.weight_preload_cycles,
        same_block_scale_hits: value.same_block_scale_hits,
        next_scale_hits: value.next_scale_hits,
        scale_demand_misses: value.scale_demand_misses,
        compute_cycles: value.compute_cycles,
        overlap_cycles: value.overlap_cycles,
        activation_overlap_cycles: value.activation_overlap_cycles,
        weight_overlap_cycles: value.weight_overlap_cycles,
        scale_overlap_cycles: value.scale_overlap_cycles,
        completed_fragments: value.completed_fragments,
        completed_output_tiles: value.completed_output_tiles,
        completed_stripes: value.completed_stripes,
        stripes_published: value.stripes_published,
        stripe_rows_published: value.stripe_rows_published,
        weight_bank_activations: value.weight_bank_activations,
    };
}

#[cfg(test)]
mod tests {
    use std::{ffi::c_void, ptr};

    use super::{parse_matmul_v2, status_for_error};
    use crate::c_api::{
        configured_dim,
        types::{MatmulDescV2, ProviderC},
        ABI_VERSION_2,
    };
    use crate::{ActivationValue, SimError, ACTIVATION_BITS, ACTIVATION_STORAGE_BYTES};

    fn v2_descriptor(
        activations: &[ActivationValue],
        weights: &[i8],
        output: &mut [i32],
    ) -> MatmulDescV2 {
        MatmulDescV2 {
            abi_version: ABI_VERSION_2,
            activation_bits: ACTIVATION_BITS as u32,
            activation_storage_bytes: ACTIVATION_STORAGE_BYTES as u32,
            dim: configured_dim(),
            activations: activations.as_ptr().cast::<c_void>(),
            weights: weights.as_ptr(),
            scales: ptr::null(),
            output: output.as_mut_ptr(),
            m: 2,
            n: 2,
            k: 3,
            activation_row_stride_bytes: 4 * ACTIVATION_STORAGE_BYTES,
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
            vector_op: 0,
            work_context: 1,
            provider: ProviderC {
                context: ptr::null_mut(),
                read_weight: None,
                read_scale: None,
                write_output: None,
            },
        }
    }

    #[test]
    fn v2_parser_checks_identity_before_pointers_and_converts_byte_stride() {
        let activations = [ActivationValue::default(); 8];
        let weights = [0i8; 6];
        let mut output = [0i32; 4];
        let mut desc = v2_descriptor(&activations, &weights, &mut output);

        let (parsed, provider) = unsafe { parse_matmul_v2(&desc) }.unwrap();
        assert_eq!(parsed.activation_row_stride, 4);
        assert!(provider.is_none());

        for field in 0..4 {
            let saved = [
                desc.abi_version,
                desc.activation_bits,
                desc.activation_storage_bytes,
                desc.dim,
            ];
            match field {
                0 => desc.abi_version += 1,
                1 => desc.activation_bits = if ACTIVATION_BITS == 4 { 8 } else { 4 },
                2 => desc.activation_storage_bytes += 1,
                3 => desc.dim = if configured_dim() == 16 { 32 } else { 16 },
                _ => unreachable!(),
            }
            desc.activations = ptr::null();
            assert!(matches!(unsafe { parse_matmul_v2(&desc) }, Err(-7)));
            desc.abi_version = saved[0];
            desc.activation_bits = saved[1];
            desc.activation_storage_bytes = saved[2];
            desc.dim = saved[3];
            desc.activations = activations.as_ptr().cast();
        }

        if ACTIVATION_STORAGE_BYTES == 2 {
            desc.activation_row_stride_bytes = 7;
            assert!(matches!(unsafe { parse_matmul_v2(&desc) }, Err(-4)));
        }
    }

    #[test]
    fn c_status_preserves_contract_ownership_and_runtime_classes() {
        assert_eq!(status_for_error(SimError::InvalidDimension), -4);
        assert_eq!(status_for_error(SimError::UnfinishedStream), -3);
        assert_eq!(
            status_for_error(SimError::RtlNotReady {
                operation: "runtime",
            }),
            -1
        );
    }
}

pub(super) fn write_extended_stats(output: *mut WorkStatsExtendedC, value: WorkStats) {
    let Some(output) = (unsafe { output.as_mut() }) else {
        return;
    };
    let mut base = WorkStatsC::default();
    write_stats(&mut base, value.clone());
    *output = WorkStatsExtendedC {
        base,
        cross_stripe_overlap_cycles: value.cross_stripe_overlap_cycles,
        lookahead_prepared: u64::from(value.lookahead_prepared),
        lookahead_publish_cycle: value.lookahead_publish_cycle,
        lookahead_first_activation_cycle: value.lookahead_first_activation_cycle,
        lookahead_first_weight_cycle: value.lookahead_first_weight_cycle,
        lookahead_weight_preload_cycle: value.lookahead_weight_preload_cycle,
        lookahead_weight_requests: value.lookahead_weight_requests,
        lookahead_weight_reuse_hits: value.lookahead_weight_reuse_hits,
        lookahead_scale_cycle: value.lookahead_scale_cycle,
        lookahead_scale_requests: value.lookahead_scale_requests,
        lookahead_scale_reuses: value.lookahead_scale_reuses,
        current_stripe_completion_cycle: value.current_stripe_completion_cycle,
        lookahead_ready_cycle: value.lookahead_ready_cycle,
        lookahead_start_cycle: value.lookahead_start_cycle,
    };
}
