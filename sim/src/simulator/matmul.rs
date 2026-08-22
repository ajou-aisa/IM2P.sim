#[cfg(test)]
use super::ReadWeightProvider;
use super::{Error, Im2pSimulator, MemoryProvider};
use crate::{
    activation::activation_elements_to_address_bytes,
    ffi,
    weight::{weight_byte_indices, weight_elements_to_address_bytes},
    ActivationValue, MatmulLayout, MatmulWork, MatrixView, MatrixViewMut, WeightValue, WorkStats,
};
mod memory;
mod stats;
use memory::{resolve_activation, resolve_scale, resolve_weight, validate_work, write_raw_output};

pub(super) const ACTIVATION_BASE: u64 = 0x1000_0000_0000_0000;
pub(super) const WEIGHT_BASE: u64 = 0x2000_0000_0000_0000;
pub(super) const SCALE_BASE: u64 = 0x3000_0000_0000_0000;
pub(super) const OUTPUT_BASE: u64 = 0x4000_0000_0000_0000;
const MATRIX_TIMEOUT_CYCLES: u64 = 10_000_000;

#[cfg(test)]
static PROVIDER_START_INTERCEPT: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);
#[cfg(test)]
static PROVIDER_START_ATTEMPTS: std::sync::atomic::AtomicUsize =
    std::sync::atomic::AtomicUsize::new(0);
#[cfg(test)]
static PROVIDER_BOUNDARY_TEST_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

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
        let job_id = super::descriptor::job_id(scale.map_or(1, |view| view.context));
        let weight_row_stride = weight_elements_to_address_bytes(work.weights.row_stride)
            .map_err(|_| Error::InvalidWeightStride)?;
        let scale_row_stride =
            super::descriptor::u64_field(scale.map_or(1, |view| view.row_stride))?;
        let output_row_stride = super::descriptor::output_row_stride_bytes(output.row_stride)?;
        let row_count = super::descriptor::u32_field(work.activations.rows)?;
        let column_count = super::descriptor::u32_field(work.weights.columns)?;
        let reduction_count = super::descriptor::u32_field(work.activations.columns)?;
        let tile_i_rows = super::descriptor::u32_field(layout.tile_i_rows)?;
        let tile_j_columns = super::descriptor::u32_field(layout.tile_j_columns)?;
        let scale_total_k = super::descriptor::u32_field(
            scale.map_or(work.activations.columns, |view| view.total_k),
        )?;
        let scale_block_size = super::descriptor::u32_field(
            scale.map_or(work.activations.columns, |view| view.block_size),
        )?;
        let descriptor = ffi::MatmulDescriptor {
            job_id,
            mode: 0,
            activation_base: ACTIVATION_BASE,
            weight_base: WEIGHT_BASE,
            scale_base: SCALE_BASE,
            output_base: OUTPUT_BASE,
            activation_row_stride: activation_elements_to_address_bytes(
                work.activations.row_stride,
            )
            .map_err(|_| Error::InvalidActivationStride)?,
            weight_row_stride,
            scale_row_stride,
            output_row_stride,
            row_count,
            column_count,
            reduction_count,
            tile_i_rows,
            tile_j_columns,
            k_origin: 0,
            scale_total_k,
            scale_block_size,
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
        activations: MatrixView<'_, ActivationValue>,
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
        crate::activation_validation::validate_activation_matrix(&activations)?;
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
        let job_id = super::descriptor::job_id(work_context);
        let rtl_weight_row_stride = weight_elements_to_address_bytes(weight_row_stride)
            .map_err(|_| Error::InvalidWeightStride)?;
        let rtl_scale_row_stride = super::descriptor::u64_field(columns)?;
        let rtl_output_row_stride = super::descriptor::output_row_stride_bytes(output_row_stride)?;
        let rtl_row_count = super::descriptor::u32_field(rows)?;
        let rtl_column_count = super::descriptor::u32_field(columns)?;
        let rtl_reduction_count = super::descriptor::u32_field(reduction)?;
        let rtl_tile_i_rows = super::descriptor::u32_field(layout.tile_i_rows)?;
        let rtl_tile_j_columns = super::descriptor::u32_field(layout.tile_j_columns)?;
        let rtl_scale_block_size = super::descriptor::u32_field(block_size.max(1))?;
        let descriptor = ffi::MatmulDescriptor {
            job_id,
            mode: 0,
            activation_base: ACTIVATION_BASE,
            weight_base: WEIGHT_BASE,
            scale_base: SCALE_BASE,
            output_base: OUTPUT_BASE,
            activation_row_stride: activation_elements_to_address_bytes(activations.row_stride)
                .map_err(|_| Error::InvalidActivationStride)?,
            weight_row_stride: rtl_weight_row_stride,
            scale_row_stride: rtl_scale_row_stride,
            output_row_stride: rtl_output_row_stride,
            row_count: rtl_row_count,
            column_count: rtl_column_count,
            reduction_count: rtl_reduction_count,
            tile_i_rows: rtl_tile_i_rows,
            tile_j_columns: rtl_tile_j_columns,
            k_origin: 0,
            scale_total_k: rtl_reduction_count,
            scale_block_size: rtl_scale_block_size,
            scale_context: work_context,
            accumulate_first_fragment: 0,
            vector_op: vector_op.encoding(),
        };
        #[cfg(test)]
        if PROVIDER_START_INTERCEPT.load(std::sync::atomic::Ordering::SeqCst) {
            PROVIDER_START_ATTEMPTS.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            return Err(Error::ProviderFailure);
        }
        let started = unsafe { ffi::im2p_start_matmul(self.handle.as_ptr(), &descriptor) };
        self.require_ready("start_matmul", started)?;

        for _ in 0..MATRIX_TIMEOUT_CYCLES {
            self.service_activation_request(&activations)?;
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
        self.service_activation_request(&work.activations)?;
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

    fn service_activation_request(
        &mut self,
        view: &MatrixView<'_, ActivationValue>,
    ) -> Result<(), Error> {
        let mut request = ffi::ReadRequest::default();
        // SAFETY: request is writable and handle remains valid.
        let status =
            unsafe { ffi::im2p_activation_read_request(self.handle.as_ptr(), &mut request) };
        if status == ffi::IM2P_REQUEST_PRESENT {
            let values = resolve_activation(view, ACTIVATION_BASE, request)?;
            // SAFETY: values has request.element_count readable activation elements.
            let accepted = unsafe {
                ffi::im2p_stage_activation_read_response(
                    self.handle.as_ptr(),
                    request.tag,
                    values.as_ptr().cast::<i8>(),
                    request.element_count,
                )
            };
            self.require_staged("activation_read_response", accepted)?;
        } else if status != ffi::IM2P_REQUEST_ABSENT {
            return Err(Error::RtlNotReady {
                operation: "activation_read_request",
            });
        }
        Ok(())
    }

    fn service_i8_request(
        &mut self,
        getter: unsafe extern "C" fn(*mut std::ffi::c_void, *mut ffi::ReadRequest) -> i32,
        responder: unsafe extern "C" fn(
            *mut std::ffi::c_void,
            u64,
            *const std::ffi::c_void,
            u32,
        ) -> i32,
        view: &MatrixView<'_, WeightValue>,
        base: u64,
    ) -> Result<(), Error> {
        let mut request = ffi::ReadRequest::default();
        // SAFETY: request is writable and handle remains valid.
        let status = unsafe { getter(self.handle.as_ptr(), &mut request) };
        if status == ffi::IM2P_REQUEST_PRESENT {
            let values = resolve_weight(view, base, request)?;
            // SAFETY: values has request.element_count readable selected-width lanes.
            let accepted = unsafe {
                responder(
                    self.handle.as_ptr(),
                    request.tag,
                    values.as_ptr().cast(),
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
        let (getter, base) = if weight {
            (ffi::im2p_weight_read_request as Getter, WEIGHT_BASE)
        } else {
            (ffi::im2p_scale_read_request as Getter, SCALE_BASE)
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
            .and_then(|bytes| usize::try_from(bytes).ok())
            .ok_or(Error::InvalidKRange)?;
        let (row, column) = if weight {
            weight_byte_indices(offset, row_stride).map_err(|_| Error::InvalidWeightStride)?
        } else {
            (offset / row_stride, offset % row_stride)
        };
        let count = request.element_count as usize;
        if row >= row_limit || column + count > column_limit {
            return Err(Error::InvalidKRange);
        }
        let accepted = if weight {
            let mut values = vec![WeightValue::default(); count];
            provider.read_weight(row, column, &mut values)?;
            unsafe {
                ffi::im2p_stage_weight_read_response(
                    self.handle.as_ptr(),
                    request.tag,
                    values.as_ptr().cast(),
                    request.element_count,
                )
            }
        } else {
            let mut values = vec![0_i8; count];
            provider.read_scale(row, column, &mut values)?;
            unsafe {
                ffi::im2p_stage_scale_read_response(
                    self.handle.as_ptr(),
                    request.tag,
                    values.as_ptr(),
                    request.element_count,
                )
            }
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
        let mut values = vec![0_i64; self.dim];
        let status = unsafe {
            ffi::im2p_output_write_request_i64(
                self.handle.as_ptr(),
                &mut request,
                values.as_mut_ptr(),
            )
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
        if !within.is_multiple_of(size_of::<i32>()) {
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
        let mut values = vec![0_i64; self.dim];
        // SAFETY: request and DIM-lane values buffer are writable.
        let status = unsafe {
            ffi::im2p_output_write_request_i64(
                self.handle.as_ptr(),
                &mut request,
                values.as_mut_ptr(),
            )
        };
        if status == ffi::IM2P_REQUEST_PRESENT {
            write_raw_output(output, OUTPUT_BASE, request, &values)?;
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

#[cfg(test)]
mod activation_boundary_tests {
    use std::ffi::c_void;
    use std::mem::ManuallyDrop;
    use std::ptr::NonNull;
    use std::sync::atomic::Ordering;

    use super::{
        Error, Im2pSimulator, MatmulLayout, MatrixView, MemoryProvider,
        PROVIDER_BOUNDARY_TEST_LOCK, PROVIDER_START_ATTEMPTS, PROVIDER_START_INTERCEPT,
    };
    use crate::{parse_activation, ActivationValue, VectorOp, ACTIVATION_BITS};

    fn selected_extrema() -> [ActivationValue; 2] {
        let extrema = match ACTIVATION_BITS {
            4 => [-8, 7],
            8 => [-128, 127],
            16 => [-32_768, 32_767],
            _ => unreachable!("supported widths are compile-time selected"),
        };
        extrema.map(|value| parse_activation(value).expect("selected-width extrema"))
    }

    unsafe extern "C" fn read_provider(
        _context: *mut c_void,
        _row: usize,
        _column: usize,
        _count: usize,
        _values: *mut i8,
    ) -> i32 {
        0
    }

    unsafe extern "C" fn write_provider(
        _context: *mut c_void,
        _block: usize,
        _row: usize,
        _column: usize,
        _count: usize,
        _values: *const i64,
    ) -> i32 {
        0
    }

    #[test]
    fn production_activation_boundary_provider_rejects_malformed_a4_before_start() {
        if ACTIVATION_BITS != 4 {
            return;
        }
        let _guard = PROVIDER_BOUNDARY_TEST_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let values: [ActivationValue; 2] = [-9, 8];
        let activations = MatrixView::new(&values, 1, 2, 2).expect("shape-only view");
        let mut simulator = ManuallyDrop::new(Im2pSimulator {
            handle: NonNull::<u8>::dangling().cast(),
            dim: 2,
        });
        let provider = MemoryProvider {
            context: std::ptr::null_mut(),
            read_weight: Some(super::ReadWeightProvider::I8(read_provider)),
            read_scale: None,
            write_output: Some(write_provider),
        };
        PROVIDER_START_ATTEMPTS.store(0, Ordering::SeqCst);
        PROVIDER_START_INTERCEPT.store(true, Ordering::SeqCst);

        let result = simulator.execute_matmul_provider(
            activations,
            1,
            1,
            2,
            1,
            1,
            0,
            VectorOp::Bypass,
            0,
            MatmulLayout {
                tile_i_rows: 1,
                tile_j_columns: 1,
            },
            provider,
        );

        PROVIDER_START_INTERCEPT.store(false, Ordering::SeqCst);
        assert_eq!(result, Err(Error::InvalidLayout));
        assert_eq!(PROVIDER_START_ATTEMPTS.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn production_activation_boundary_provider_accepts_selected_extrema_before_start() {
        let _guard = PROVIDER_BOUNDARY_TEST_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let values = selected_extrema();
        let activations = MatrixView::new(&values, 1, 2, 2).expect("valid extrema view");
        let mut simulator = ManuallyDrop::new(Im2pSimulator {
            handle: NonNull::<u8>::dangling().cast(),
            dim: 2,
        });
        let provider = MemoryProvider {
            context: std::ptr::null_mut(),
            read_weight: Some(super::ReadWeightProvider::I8(read_provider)),
            read_scale: None,
            write_output: Some(write_provider),
        };
        PROVIDER_START_ATTEMPTS.store(0, Ordering::SeqCst);
        PROVIDER_START_INTERCEPT.store(true, Ordering::SeqCst);

        let result = simulator.execute_matmul_provider(
            activations,
            1,
            1,
            2,
            1,
            1,
            0,
            VectorOp::Bypass,
            0,
            MatmulLayout {
                tile_i_rows: 1,
                tile_j_columns: 1,
            },
            provider,
        );

        PROVIDER_START_INTERCEPT.store(false, Ordering::SeqCst);
        assert_eq!(result, Err(Error::ProviderFailure));
        assert_eq!(PROVIDER_START_ATTEMPTS.load(Ordering::SeqCst), 1);
    }
}
