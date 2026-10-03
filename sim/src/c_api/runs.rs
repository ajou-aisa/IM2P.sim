use std::{mem::size_of, ptr, slice};

use crate::{ffi, simulator::MemoryProvider, ActivationValue, Im2pSimulator, WorkStats};

use super::types::MatmulDesc;

mod provider;
use provider::RunContext;

pub(super) struct OwnedRuns {
    original_k: u32,
    entries: Vec<ffi::CompactRun>,
}

impl OwnedRuns {
    pub(super) unsafe fn copy(
        view: *const ffi::CompactRuns,
        compact_k: usize,
    ) -> Result<Self, i32> {
        if view.is_null() || !(view as usize).is_multiple_of(align_of::<ffi::CompactRuns>()) {
            return Err(-4);
        }
        let Some(view) = (unsafe { view.as_ref() }) else {
            return Err(-4);
        };
        if view.version != 1
            || view.struct_size as usize != size_of::<ffi::CompactRuns>()
            || view.original_k == 0
            || view.run_count == 0
            || view.run_count > compact_k
            || view.run_count > isize::MAX as usize / size_of::<ffi::CompactRun>()
            || view.runs.is_null()
            || !(view.runs as usize).is_multiple_of(align_of::<ffi::CompactRun>())
        {
            return Err(-4);
        }
        // SAFETY: Category 8/10 (FFI bounds): the public C contract supplies
        // run_count initialized records at this aligned live pointer.
        let entries = unsafe { slice::from_raw_parts(view.runs, view.run_count) }.to_vec();
        let mut end = 0_u32;
        let mut previous = None;
        let fragments_per_block = 32 / super::configured_dim().min(32) as usize;
        for run in &entries {
            if run.compact_k_count == 0
                || run.compact_k_count > 32
                || run.compact_k_begin != end
                || previous.is_some_and(|block| run.original_block_id <= block)
                || run.original_block_id as usize > u16::MAX as usize / fragments_per_block
                || run.original_k_mask.count_ones() != run.compact_k_count
                || u64::from(run.original_block_id) * 32
                    + u64::from(32 - run.original_k_mask.leading_zeros())
                    > u64::from(view.original_k)
            {
                return Err(-4);
            }
            end = end.checked_add(run.compact_k_count).ok_or(-4)?;
            previous = Some(run.original_block_id);
        }
        if end as usize != compact_k {
            return Err(-4);
        }
        Ok(Self {
            original_k: view.original_k,
            entries,
        })
    }

    fn view(&self) -> ffi::CompactRuns {
        ffi::CompactRuns {
            version: 1,
            struct_size: size_of::<ffi::CompactRuns>() as u32,
            original_k: self.original_k,
            run_count: self.entries.len(),
            runs: self.entries.as_ptr(),
        }
    }

    fn ordinal(&self, original_block: usize) -> Option<usize> {
        self.entries
            .binary_search_by_key(&original_block, |run| run.original_block_id as usize)
            .ok()
    }
}

pub(super) unsafe fn execute(
    simulator: &mut Im2pSimulator,
    desc: &MatmulDesc,
    provider: MemoryProvider,
    geometry: &crate::production_geometry::ProductionGeometry,
    runs: &OwnedRuns,
) -> Result<WorkStats, i32> {
    if desc.vector_op != 5 || desc.block_size != 32 || desc.activations.is_null() {
        return Err(-4);
    }
    if desc.output_row_stride < desc.n
        || (!desc.output.is_null() && !(desc.output as usize).is_multiple_of(align_of::<i32>()))
    {
        return Err(-4);
    }
    if provider.read_weight.is_none() && desc.weights.is_null()
        || provider.read_scale.is_none() && desc.scales.is_null()
        || provider.write_output.is_none() && desc.output.is_null()
    {
        return Err(-4);
    }
    let output_len = desc.m.checked_mul(desc.n).ok_or(-4)?;
    let output_extent = (desc.m - 1)
        .checked_mul(desc.output_row_stride)
        .and_then(|value| value.checked_add(desc.n))
        .ok_or(-4)?;
    if output_len > isize::MAX as usize / size_of::<i32>()
        || output_extent > isize::MAX as usize / size_of::<i32>()
    {
        return Err(-4);
    }
    let activation_len = (desc.m - 1)
        .checked_mul(desc.activation_row_stride)
        .and_then(|value| value.checked_add(desc.k))
        .ok_or(-4)?;
    if activation_len > isize::MAX as usize / size_of::<ActivationValue>() {
        return Err(-4);
    }
    if provider.read_weight.is_none()
        && (desc.weight_row_stride < desc.n
            || (desc.k - 1)
                .checked_mul(desc.weight_row_stride)
                .and_then(|value| value.checked_add(desc.n))
                .is_none_or(|length| {
                    length > isize::MAX as usize / size_of::<crate::WeightValue>()
                }))
    {
        return Err(-4);
    }
    if provider.read_scale.is_none()
        && (desc.scale_valid_columns < desc.n
            || desc.scale_column_offset > desc.scale_row_stride
            || desc.n > desc.scale_row_stride - desc.scale_column_offset
            || (runs.entries.len() - 1)
                .checked_mul(desc.scale_row_stride)
                .and_then(|value| value.checked_add(desc.scale_column_offset))
                .and_then(|value| value.checked_add(desc.n))
                .is_none_or(|length| {
                    length > desc.scale_values_len
                        || length > isize::MAX as usize / size_of::<u32>()
                }))
    {
        return Err(-4);
    }
    let mut context = RunContext::new(desc, provider, runs, output_len);
    let callback_provider = context.callbacks();
    let view = runs.view();
    // SAFETY: Category 8: compact A backing and copied run view remain live
    // during synchronous RTL execution; all matrix extents were checked above.
    let activations = unsafe { slice::from_raw_parts(desc.activations, activation_len) };
    let activation_view =
        crate::activation_view(activations, desc.m, desc.k, desc.activation_row_stride)
            .map_err(|_| -4)?;
    let stats = simulator
        .execute_matmul_provider_with_geometry_runs(
            activation_view,
            desc.m,
            desc.n,
            desc.k,
            desc.weight_row_stride,
            desc.output_row_stride,
            desc.block_size,
            crate::VectorOp::LeftShift,
            desc.work_context,
            crate::MatmulLayout {
                tile_i_rows: desc.tile_i_rows,
                tile_j_columns: desc.tile_j_columns,
            },
            callback_provider,
            geometry,
            &view,
        )
        .map_err(super::helpers::status_for_error)?;
    if context.seen.iter().any(|seen| !seen) {
        return Err(-1);
    }
    if let Some(callback) = provider.write_output {
        let values: Vec<i64> = context
            .output
            .iter()
            .map(|&value| i64::from(value))
            .collect();
        // SAFETY: Category 8: one final callback receives a live contiguous
        // final output and the caller's original context.
        if unsafe { callback(provider.context, 0, 0, 0, values.len(), values.as_ptr(), 2) } != 0 {
            return Err(-1);
        }
    } else {
        for row in 0..desc.m {
            // SAFETY: Category 8/10: validated output stride and caller-owned
            // backing cover every row before this single commit phase.
            unsafe {
                ptr::copy_nonoverlapping(
                    context.output.as_ptr().add(row * desc.n),
                    desc.output.add(row * desc.output_row_stride),
                    desc.n,
                )
            };
        }
    }
    Ok(stats)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn copied_run_view_survives_mutation_and_rejects_gap() {
        let mut entries = [
            ffi::CompactRun {
                original_block_id: 0,
                original_k_mask: 0xfff,
                compact_k_begin: 0,
                compact_k_count: 12,
            },
            ffi::CompactRun {
                original_block_id: 3,
                original_k_mask: 0x3ff,
                compact_k_begin: 12,
                compact_k_count: 10,
            },
        ];
        let view = ffi::CompactRuns {
            version: 1,
            struct_size: size_of::<ffi::CompactRuns>() as u32,
            original_k: 128,
            run_count: 2,
            runs: entries.as_ptr(),
        };
        let owned = unsafe { OwnedRuns::copy(&view, 22) }.expect("valid view");
        entries[1].original_block_id = 4;
        assert_eq!(entries[1].original_block_id, 4);
        assert_eq!(owned.ordinal(3), Some(1));
        assert_eq!(owned.ordinal(4), None);
        let invalid_entries = [
            owned.entries[0],
            ffi::CompactRun {
                compact_k_begin: 13,
                ..owned.entries[1]
            },
        ];
        let invalid = ffi::CompactRuns {
            runs: invalid_entries.as_ptr(),
            ..view
        };
        assert!(unsafe { OwnedRuns::copy(&invalid, 22) }.is_err());
    }
}
