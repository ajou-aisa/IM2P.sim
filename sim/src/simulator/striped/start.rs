use crate::{ffi, StripeLayout, StripeWorkDesc};

use super::{
    Error, Im2pSimulator, MemoryProvider, StripedMatmul, ACTIVATION_BASE, OUTPUT_BASE, SCALE_BASE,
    WEIGHT_BASE,
};

impl Im2pSimulator {
    pub fn begin_striped_matmul<'a>(
        self,
        descriptor: &StripeWorkDesc<'a>,
    ) -> Result<StripedMatmul<'a>, Error> {
        let layout = StripeLayout {
            weight_row_stride: descriptor.columns,
            output_row_stride: descriptor.columns,
            tile_i_rows: self.dim,
            tile_j_columns: self.dim,
        };
        self.begin_striped_matmul_layout(descriptor, layout)
    }

    pub fn begin_striped_matmul_layout<'a>(
        self,
        descriptor: &StripeWorkDesc<'a>,
        layout: StripeLayout,
    ) -> Result<StripedMatmul<'a>, Error> {
        self.begin_striped_matmul_layout_recover(descriptor, layout)
            .map_err(|(error, _)| error)
    }

    pub(crate) fn begin_striped_matmul_layout_recover<'a>(
        self,
        descriptor: &StripeWorkDesc<'a>,
        layout: StripeLayout,
    ) -> Result<StripedMatmul<'a>, (Error, Self)> {
        self.begin_striped_matmul_provider_recover(descriptor, layout, None, None)
    }

    pub(crate) fn begin_striped_matmul_provider_recover<'a>(
        mut self,
        descriptor: &StripeWorkDesc<'a>,
        layout: StripeLayout,
        provider: Option<MemoryProvider>,
        provider_block_size: Option<usize>,
    ) -> Result<StripedMatmul<'a>, (Error, Self)> {
        if let Err(error) = validate_descriptor(
            descriptor,
            layout,
            self.dim,
            provider.is_some(),
            provider_block_size,
        ) {
            return Err((error, self));
        }
        let counters_before = self.matrix_counters();
        let scales_before = self.scale_counters();
        let start_cycle = self.cycles();
        let scale = descriptor.scale_matrix;
        let rtl = ffi::MatmulDescriptor {
            job_id: descriptor.work_context as u32,
            mode: 1,
            activation_base: ACTIVATION_BASE,
            weight_base: WEIGHT_BASE,
            scale_base: SCALE_BASE,
            output_base: OUTPUT_BASE,
            activation_row_stride: descriptor.reduction as u64,
            weight_row_stride: layout.weight_row_stride as u64,
            scale_row_stride: if provider.is_some() {
                descriptor.columns as u64
            } else {
                scale.map_or(1, |view| view.row_stride) as u64
            },
            output_row_stride: (layout.output_row_stride * size_of::<i32>()) as u64,
            row_count: descriptor.rows as u32,
            column_count: descriptor.columns as u32,
            reduction_count: descriptor.reduction as u32,
            tile_i_rows: layout.tile_i_rows as u32,
            tile_j_columns: layout.tile_j_columns as u32,
            k_origin: 0,
            scale_total_k: scale.map_or(descriptor.reduction, |view| view.total_k) as u32,
            scale_block_size: provider_block_size
                .or_else(|| scale.map(|view| view.block_size))
                .unwrap_or(descriptor.reduction) as u32,
            scale_context: descriptor.work_context,
            accumulate_first_fragment: 0,
            vector_op: descriptor.vector_op.encoding(),
        };
        let accepted = unsafe { ffi::im2p_start_matmul(self.handle.as_ptr(), &rtl) };
        if let Err(error) = self.require_ready("start_striped_matmul", accepted) {
            return Err((error, self));
        }
        Ok(StripedMatmul {
            simulator: self,
            descriptor: StripeWorkDesc {
                weights: descriptor.weights,
                scale_matrix: descriptor.scale_matrix,
                rows: descriptor.rows,
                columns: descriptor.columns,
                reduction: descriptor.reduction,
                vector_op: descriptor.vector_op,
                work_context: descriptor.work_context,
            },
            layout,
            provider,
            published: Default::default(),
            completed: Default::default(),
            outstanding_stripes: 0,
            next_stripe_id: 0,
            next_row: 0,
            counters_before,
            scales_before,
            start_cycle,
        })
    }
}

fn validate_descriptor(
    descriptor: &StripeWorkDesc<'_>,
    layout: StripeLayout,
    dim: usize,
    provider: bool,
    provider_block_size: Option<usize>,
) -> Result<(), Error> {
    if descriptor.rows == 0
        || descriptor.columns == 0
        || descriptor.reduction == 0
        || descriptor.rows > u32::MAX as usize
        || descriptor.columns > u32::MAX as usize
        || descriptor.reduction > u32::MAX as usize
    {
        return Err(Error::InvalidDimension);
    }
    if layout.weight_row_stride < descriptor.columns {
        return Err(Error::InvalidWeightStride);
    }
    if layout.output_row_stride < descriptor.columns {
        return Err(Error::InvalidOutputStride);
    }
    if layout.tile_i_rows == 0
        || layout.tile_i_rows > dim
        || layout.tile_j_columns == 0
        || layout.tile_j_columns > dim
    {
        return Err(Error::InvalidLayout);
    }
    let expected = (descriptor.reduction - 1)
        .checked_mul(layout.weight_row_stride)
        .and_then(|prefix| prefix.checked_add(descriptor.columns))
        .ok_or(Error::InvalidDimension)?;
    if !provider && descriptor.weights.len() < expected {
        return Err(Error::InvalidBufferLength {
            name: "weights",
            expected,
            actual: descriptor.weights.len(),
        });
    }
    if !provider
        && descriptor.vector_op != super::super::VectorOp::Bypass
        && descriptor.scale_matrix.is_none()
    {
        return Err(Error::MissingScales {
            operation: descriptor.vector_op,
        });
    }
    if provider
        && descriptor.vector_op != super::super::VectorOp::Bypass
        && provider_block_size.is_none_or(|block_size| block_size == 0)
    {
        return Err(Error::InvalidLayout);
    }
    if let Some(scales) = descriptor.scale_matrix {
        super::super::validation::validate_scale_matrix(
            scales,
            descriptor.reduction,
            descriptor.columns,
        )?;
    }
    Ok(())
}
