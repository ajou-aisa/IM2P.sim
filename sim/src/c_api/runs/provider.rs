use std::{ffi::c_void, ptr};

use crate::simulator::{MemoryProvider, ReadWeightProvider};

use super::{super::types::MatmulDesc, OwnedRuns};

pub(super) struct RunContext<'a> {
    desc: &'a MatmulDesc,
    provider: MemoryProvider,
    runs: &'a OwnedRuns,
    pub(super) output: Vec<i32>,
    pub(super) seen: Vec<bool>,
}

impl<'a> RunContext<'a> {
    pub(super) fn new(
        desc: &'a MatmulDesc,
        provider: MemoryProvider,
        runs: &'a OwnedRuns,
        output_len: usize,
    ) -> Self {
        Self {
            desc,
            provider,
            runs,
            output: vec![0; output_len],
            seen: vec![false; output_len],
        }
    }

    pub(super) fn callbacks(&mut self) -> MemoryProvider {
        MemoryProvider {
            context: (self as *mut Self).cast(),
            read_weight: Some(ReadWeightProvider::I8(read_weight_i8)),
            read_scale: Some(read_scale),
            write_output: Some(stage_output),
        }
    }
}

unsafe extern "C" fn read_weight_i8(
    context: *mut c_void,
    row: usize,
    column: usize,
    count: usize,
    out: *mut i8,
) -> i32 {
    // SAFETY: Category 8 (FFI boundary): the synchronous simulator owns a
    // live RunContext for the entire callback and supplies writable lanes.
    let context = unsafe { &mut *context.cast::<RunContext<'_>>() };
    if row >= context.desc.k
        || column
            .checked_add(count)
            .is_none_or(|end| end > context.desc.n)
    {
        return -1;
    }
    if let Some(callback) = context.provider.read_weight {
        if let ReadWeightProvider::I8(callback) = callback {
            // SAFETY: Category 8: forward the original caller's callback contract.
            return unsafe { callback(context.provider.context, row, column, count, out) };
        }
        return -1;
    }
    let Some(offset) = row
        .checked_mul(context.desc.weight_row_stride)
        .and_then(|start| start.checked_add(column))
    else {
        return -1;
    };
    // SAFETY: Category 8/10: descriptor matrix extent was checked before RTL start.
    unsafe { ptr::copy_nonoverlapping(context.desc.weights.add(offset), out.cast(), count) };
    0
}

unsafe extern "C" fn read_scale(
    context: *mut c_void,
    block: usize,
    column: usize,
    count: usize,
    out: *mut u32,
) -> i32 {
    // SAFETY: Category 8: callback context remains live until the synchronous run ends.
    let context = unsafe { &mut *context.cast::<RunContext<'_>>() };
    let Some(ordinal) = context.runs.ordinal(block) else {
        return -1;
    };
    if column
        .checked_add(count)
        .is_none_or(|end| end > context.desc.n)
    {
        return -1;
    }
    if let Some(callback) = context.provider.read_scale {
        // SAFETY: Category 8: forward the original caller's callback contract.
        return unsafe { callback(context.provider.context, ordinal, column, count, out) };
    }
    let Some(offset) = ordinal
        .checked_mul(context.desc.scale_row_stride)
        .and_then(|start| start.checked_add(context.desc.scale_column_offset))
        .and_then(|start| start.checked_add(column))
    else {
        return -1;
    };
    // SAFETY: Category 8/10: scale backing extent was checked before RTL start.
    unsafe { ptr::copy_nonoverlapping(context.desc.scales.add(offset), out, count) };
    0
}

unsafe extern "C" fn stage_output(
    context: *mut c_void,
    block: usize,
    row: usize,
    column: usize,
    count: usize,
    values: *const i64,
    domain: u32,
) -> i32 {
    // SAFETY: Category 8: callback context remains live until the synchronous run ends.
    let context = unsafe { &mut *context.cast::<RunContext<'_>>() };
    if block != 0 || domain != 2 || row >= context.desc.m {
        return -1;
    }
    let Some(end_column) = column.checked_add(count) else {
        return -1;
    };
    if end_column > context.desc.n {
        return -1;
    }
    for lane in 0..count {
        // SAFETY: Category 8/10: the provider supplies count readable i64 lanes.
        let value = unsafe { *values.add(lane) };
        let Ok(value) = i32::try_from(value) else {
            return -1;
        };
        let index = row * context.desc.n + column + lane;
        if context.seen[index] {
            return -1;
        }
        context.output[index] = value;
        context.seen[index] = true;
    }
    0
}
