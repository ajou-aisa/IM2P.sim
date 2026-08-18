use super::{Error, Im2pSimulator, MemoryProvider};
use crate::{ffi, MatmulLayout, MatmulWork, MatrixView, MatrixViewMut, WorkStats};
mod memory;
mod stats;
use memory::{resolve_i8, resolve_scale, validate_work, write_i32};

pub(super) const ACTIVATION_BASE: u64 = 0x1000_0000_0000_0000;
pub(super) const WEIGHT_BASE: u64 = 0x2000_0000_0000_0000;
pub(super) const SCALE_BASE: u64 = 0x3000_0000_0000_0000;
pub(super) const OUTPUT_BASE: u64 = 0x4000_0000_0000_0000;
const MATRIX_TIMEOUT_CYCLES: u64 = 10_000_000;

impl Im2pSimulator {
    pub fn execute_matmul(
        &mut self,
        work: &MatmulWork<'_>,
        output: &mut MatrixViewMut<'_, i32>,
    ) -> Result<WorkStats, Error> {
        let layout = MatmulLayout {
            tile_i_rows: self.dim,
            tile_j_columns: self.dim,
        };
        self.execute_matmul_layout(work, output, layout)
    }

    pub fn execute_matmul_layout(
        &mut self,
        work: &MatmulWork<'_>,
        output: &mut MatrixViewMut<'_, i32>,
        layout: MatmulLayout,
    ) -> Result<WorkStats, Error> {
        validate_work(work, output)?;
        if work.activations.rows > u32::MAX as usize
            || work.activations.columns > u32::MAX as usize
            || work.weights.columns > u32::MAX as usize
        {
            return Err(Error::InvalidDimension);
        }
        if layout.tile_i_rows == 0
            || layout.tile_i_rows > self.dim
            || layout.tile_j_columns == 0
            || layout.tile_j_columns > self.dim
        {
            return Err(Error::InvalidLayout);
        }
        let scale = work.scales;
        let descriptor = ffi::MatmulDescriptor {
            job_id: work.scales.map_or(1, |view| view.context as u32),
            mode: 0,
            activation_base: ACTIVATION_BASE,
            weight_base: WEIGHT_BASE,
            scale_base: SCALE_BASE,
            output_base: OUTPUT_BASE,
            activation_row_stride: work.activations.row_stride as u64,
            weight_row_stride: work.weights.row_stride as u64,
            scale_row_stride: scale.map_or(1, |view| view.row_stride) as u64,
            output_row_stride: (output.row_stride * size_of::<i32>()) as u64,
            row_count: work.activations.rows as u32,
            column_count: work.weights.columns as u32,
            reduction_count: work.activations.columns as u32,
            tile_i_rows: layout.tile_i_rows as u32,
            tile_j_columns: layout.tile_j_columns as u32,
            k_origin: 0,
            scale_total_k: scale.map_or(work.activations.columns, |view| view.total_k) as u32,
            scale_block_size: scale.map_or(work.activations.columns, |view| view.block_size) as u32,
            scale_context: scale.map_or(0, |view| view.context),
            accumulate_first_fragment: 0,
            vector_op: work.vector_op.encoding(),
        };

        // SAFETY: the descriptor is copied synchronously and no pointer is retained.
        let started = unsafe { ffi::im2p_start_matmul(self.handle.as_ptr(), &descriptor) };
        self.require_ready("start_matmul", started)?;

        for _ in 0..MATRIX_TIMEOUT_CYCLES {
            self.service_matrix_reads(work)?;
            self.service_matrix_output(output)?;
            // SAFETY: handle remains valid for the simulator lifetime.
            if unsafe { ffi::im2p_matmul_done(self.handle.as_ptr()) } != 0 {
                // SAFETY: handle remains valid and the action has no pointer arguments.
                let accepted = unsafe { ffi::im2p_acknowledge_matmul(self.handle.as_ptr()) };
                self.require_ready("acknowledge_matmul", accepted)?;
                self.wait_idle()?;
                return Ok(self.work_stats(1));
            }
            self.tick_staged_raw();
        }
        Err(self.matrix_timeout("execute_matmul", MATRIX_TIMEOUT_CYCLES))
    }

    pub(crate) fn execute_matmul_provider(
        &mut self,
        activations: MatrixView<'_, i8>,
        rows: usize,
        columns: usize,
        reduction: usize,
        weight_row_stride: usize,
        output_row_stride: usize,
        block_size: usize,
        vector_op: crate::VectorOp,
        work_context: u64,
        layout: MatmulLayout,
        provider: MemoryProvider,
    ) -> Result<WorkStats, Error> {
        if rows == 0
            || columns == 0
            || reduction == 0
            || rows > u32::MAX as usize
            || columns > u32::MAX as usize
            || reduction > u32::MAX as usize
            || activations.rows != rows
            || activations.columns != reduction
            || layout.tile_i_rows == 0
            || layout.tile_i_rows > self.dim
            || layout.tile_j_columns == 0
            || layout.tile_j_columns > self.dim
            || provider.read_weight.is_none()
            || provider.write_output.is_none()
            || weight_row_stride < columns
            || output_row_stride < columns
            || (vector_op != crate::VectorOp::Bypass
                && (block_size == 0 || provider.read_scale.is_none()))
        {
            return Err(Error::InvalidLayout);
        }
        let descriptor = ffi::MatmulDescriptor {
            job_id: work_context as u32,
            mode: 0,
            activation_base: ACTIVATION_BASE,
            weight_base: WEIGHT_BASE,
            scale_base: SCALE_BASE,
            output_base: OUTPUT_BASE,
            activation_row_stride: activations.row_stride as u64,
            weight_row_stride: weight_row_stride as u64,
            scale_row_stride: columns as u64,
            output_row_stride: (output_row_stride * size_of::<i32>()) as u64,
            row_count: rows as u32,
            column_count: columns as u32,
            reduction_count: reduction as u32,
            tile_i_rows: layout.tile_i_rows as u32,
            tile_j_columns: layout.tile_j_columns as u32,
            k_origin: 0,
            scale_total_k: reduction as u32,
            scale_block_size: block_size.max(1) as u32,
            scale_context: work_context,
            accumulate_first_fragment: 0,
            vector_op: vector_op.encoding(),
        };
        let started = unsafe { ffi::im2p_start_matmul(self.handle.as_ptr(), &descriptor) };
        self.require_ready("start_matmul", started)?;

        for _ in 0..MATRIX_TIMEOUT_CYCLES {
            self.service_i8_request(
                ffi::im2p_activation_read_request,
                ffi::im2p_stage_activation_read_response,
                &activations,
                ACTIVATION_BASE,
            )?;
            self.service_provider_reads(
                provider,
                weight_row_stride,
                columns,
                reduction,
                vector_op,
            )?;
            self.service_provider_output(provider, rows, columns, output_row_stride, vector_op)?;
            if unsafe { ffi::im2p_matmul_done(self.handle.as_ptr()) } != 0 {
                let accepted = unsafe { ffi::im2p_acknowledge_matmul(self.handle.as_ptr()) };
                self.require_ready("acknowledge_matmul", accepted)?;
                self.wait_idle()?;
                return Ok(self.work_stats(1));
            }
            self.tick_staged_raw();
        }
        Err(self.matrix_timeout("execute_matmul_provider", MATRIX_TIMEOUT_CYCLES))
    }

    fn service_provider_reads(
        &mut self,
        provider: MemoryProvider,
        weight_row_stride: usize,
        columns: usize,
        reduction: usize,
        vector_op: crate::VectorOp,
    ) -> Result<(), Error> {
        self.service_provider_read(true, provider, weight_row_stride, reduction, columns)?;
        if vector_op != crate::VectorOp::Bypass {
            self.service_provider_read(false, provider, columns, usize::MAX, columns)?;
        }
        Ok(())
    }

    fn service_matrix_reads(&mut self, work: &MatmulWork<'_>) -> Result<(), Error> {
        self.service_i8_request(
            ffi::im2p_activation_read_request,
            ffi::im2p_stage_activation_read_response,
            &work.activations,
            ACTIVATION_BASE,
        )?;
        self.service_i8_request(
            ffi::im2p_weight_read_request,
            ffi::im2p_stage_weight_read_response,
            &work.weights,
            WEIGHT_BASE,
        )?;
        let mut request = ffi::ReadRequest::default();
        // SAFETY: request is writable and handle remains valid.
        let status = unsafe { ffi::im2p_scale_read_request(self.handle.as_ptr(), &mut request) };
        if status == ffi::IM2P_REQUEST_PRESENT {
            let scale = work.scales.ok_or(Error::MissingScales {
                operation: work.vector_op,
            })?;
            let values = resolve_scale(scale, request)?;
            // SAFETY: values has request.element_count readable lanes.
            let accepted = unsafe {
                ffi::im2p_stage_scale_read_response(
                    self.handle.as_ptr(),
                    request.tag,
                    values.as_ptr(),
                    request.element_count,
                )
            };
            self.require_staged("scale_read_response", accepted)?;
        } else if status != ffi::IM2P_REQUEST_ABSENT {
            return Err(Error::RtlNotReady {
                operation: "scale_read_request",
            });
        }
        Ok(())
    }

    fn service_i8_request(
        &mut self,
        getter: unsafe extern "C" fn(*mut std::ffi::c_void, *mut ffi::ReadRequest) -> i32,
        responder: unsafe extern "C" fn(*mut std::ffi::c_void, u64, *const i8, u32) -> i32,
        view: &MatrixView<'_, i8>,
        base: u64,
    ) -> Result<(), Error> {
        let mut request = ffi::ReadRequest::default();
        // SAFETY: request is writable and handle remains valid.
        let status = unsafe { getter(self.handle.as_ptr(), &mut request) };
        if status == ffi::IM2P_REQUEST_PRESENT {
            let values = resolve_i8(view, base, request)?;
            // SAFETY: values has request.element_count readable lanes.
            let accepted = unsafe {
                responder(
                    self.handle.as_ptr(),
                    request.tag,
                    values.as_ptr(),
                    request.element_count,
                )
            };
            self.require_staged("matrix_read_response", accepted)?;
        } else if status != ffi::IM2P_REQUEST_ABSENT {
            return Err(Error::RtlNotReady {
                operation: "matrix_read_request",
            });
        }
        Ok(())
    }

    fn service_provider_read(
        &mut self,
        weight: bool,
        provider: MemoryProvider,
        row_stride: usize,
        row_limit: usize,
        column_limit: usize,
    ) -> Result<(), Error> {
        let mut request = ffi::ReadRequest::default();
        type Getter = unsafe extern "C" fn(*mut std::ffi::c_void, *mut ffi::ReadRequest) -> i32;
        type Responder = unsafe extern "C" fn(*mut std::ffi::c_void, u64, *const i8, u32) -> i32;
        let (getter, responder, base) = if weight {
            (
                ffi::im2p_weight_read_request as Getter,
                ffi::im2p_stage_weight_read_response as Responder,
                WEIGHT_BASE,
            )
        } else {
            (
                ffi::im2p_scale_read_request as Getter,
                ffi::im2p_stage_scale_read_response as Responder,
                SCALE_BASE,
            )
        };
        let status = unsafe { getter(self.handle.as_ptr(), &mut request) };
        if status == ffi::IM2P_REQUEST_ABSENT {
            return Ok(());
        }
        if status != ffi::IM2P_REQUEST_PRESENT || request.element_count as usize > self.dim {
            return Err(Error::InvalidKRange);
        }
        let offset = request
            .address
            .checked_sub(base)
            .ok_or(Error::InvalidKRange)? as usize;
        let row = offset / row_stride;
        let column = offset % row_stride;
        let count = request.element_count as usize;
        if row >= row_limit || column + count > column_limit {
            return Err(Error::InvalidKRange);
        }
        let mut values = vec![0_i8; count];
        if weight {
            provider.read_weight(row, column, &mut values)?;
        } else {
            provider.read_scale(row, column, &mut values)?;
        }
        let accepted = unsafe {
            responder(
                self.handle.as_ptr(),
                request.tag,
                values.as_ptr(),
                request.element_count,
            )
        };
        self.require_staged("provider_read_response", accepted)
    }

    fn service_provider_output(
        &mut self,
        provider: MemoryProvider,
        rows: usize,
        columns: usize,
        output_row_stride: usize,
        vector_op: crate::VectorOp,
    ) -> Result<(), Error> {
        let mut request = ffi::WriteRequest::default();
        let mut values = vec![0_i32; self.dim];
        let status = unsafe {
            ffi::im2p_output_write_request(self.handle.as_ptr(), &mut request, values.as_mut_ptr())
        };
        if status == ffi::IM2P_REQUEST_ABSENT {
            return Ok(());
        }
        if status != ffi::IM2P_REQUEST_PRESENT || request.element_count as usize > self.dim {
            return Err(Error::InvalidKRange);
        }
        let offset = request
            .address
            .checked_sub(OUTPUT_BASE)
            .ok_or(Error::InvalidKRange)? as usize;
        let row_stride = output_row_stride
            .checked_mul(size_of::<i32>())
            .ok_or(Error::InvalidKRange)?;
        let block_stride = rows.checked_mul(row_stride).ok_or(Error::InvalidKRange)?;
        let (block, within) = if vector_op == crate::VectorOp::External {
            (offset / block_stride, offset % block_stride)
        } else {
            (0, offset)
        };
        if within % size_of::<i32>() != 0 {
            return Err(Error::InvalidKRange);
        }
        let row = within / row_stride;
        let column = (within % row_stride) / size_of::<i32>();
        let count = request.element_count as usize;
        if row >= rows || column + count > columns {
            return Err(Error::InvalidKRange);
        }
        provider.write_output(block, row, column, &values[..count])?;
        let accepted =
            unsafe { ffi::im2p_stage_output_write_response(self.handle.as_ptr(), request.tag) };
        self.require_staged("provider_output_write_response", accepted)
    }

    fn service_matrix_output(&mut self, output: &mut MatrixViewMut<'_, i32>) -> Result<(), Error> {
        let mut request = ffi::WriteRequest::default();
        let mut values = vec![0_i32; self.dim];
        // SAFETY: request and DIM-lane values buffer are writable.
        let status = unsafe {
            ffi::im2p_output_write_request(self.handle.as_ptr(), &mut request, values.as_mut_ptr())
        };
        if status == ffi::IM2P_REQUEST_PRESENT {
            write_i32(output, OUTPUT_BASE, request, &values)?;
            // SAFETY: host memory was updated before acknowledging the matching tag.
            let accepted =
                unsafe { ffi::im2p_stage_output_write_response(self.handle.as_ptr(), request.tag) };
            self.require_staged("output_write_response", accepted)?;
        } else if status != ffi::IM2P_REQUEST_ABSENT {
            return Err(Error::RtlNotReady {
                operation: "output_write_request",
            });
        }
        Ok(())
    }
}
