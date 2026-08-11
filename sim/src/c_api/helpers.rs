use std::{mem::size_of, ptr, slice};

use crate::{
    Im2pSimulator, KBlockScaleMatrixView, MatmulLayout, MatmulWork, MatrixView, MatrixViewMut,
    VectorOp, WorkStats,
};

use super::types::{MatmulDesc, StreamBox, WorkStatsC, WorkStatsExtendedC};

pub(super) unsafe fn execute_full(
    simulator: &mut Im2pSimulator,
    desc: &MatmulDesc,
) -> Result<WorkStats, crate::SimError> {
    let op = vector_op(desc.vector_op).ok_or(crate::SimError::InvalidDimension)?;
    if desc.activations.is_null() || desc.weights.is_null() || desc.output.is_null() {
        return Err(crate::SimError::InvalidDimension);
    }
    let activation_len = matrix_len(desc.m, desc.k, desc.activation_row_stride, 1)?;
    let weight_len = matrix_len(desc.k, desc.n, desc.weight_row_stride, 1)?;
    let output_len = matrix_len(desc.m, desc.n, desc.output_row_stride, size_of::<i32>())?;
    let activations = slice::from_raw_parts(desc.activations, activation_len);
    let weights = slice::from_raw_parts(desc.weights, weight_len);
    let output = slice::from_raw_parts_mut(desc.output, output_len);
    let work = MatmulWork {
        activations: MatrixView::new(activations, desc.m, desc.k, desc.activation_row_stride)?,
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
        job.supply_activation_row(row, values)?;
    }
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
        job.acknowledge_output_row(row)?;
    }
    Ok(())
}

pub(super) fn vector_op(value: u8) -> Option<VectorOp> {
    match value {
        0 => Some(VectorOp::Bypass),
        1 => Some(VectorOp::Multiply),
        2 => Some(VectorOp::Shift),
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
