use super::{Error, Im2pSimulator};
use crate::{ffi, MatmulWork, MatrixView, MatrixViewMut, WorkStats};
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
        validate_work(work, output)?;
        let counters_before = self.matrix_counters();
        let scales_before = self.scale_counters();
        let start_cycle = self.cycles();
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
                return Ok(self.work_stats(counters_before, scales_before, start_cycle, 1));
            }
            self.tick_raw();
        }
        Err(self.matrix_timeout("execute_matmul", MATRIX_TIMEOUT_CYCLES))
    }

    fn service_matrix_reads(&mut self, work: &MatmulWork<'_>) -> Result<(), Error> {
        self.service_i8_request(
            ffi::im2p_activation_read_request,
            ffi::im2p_put_activation_read_response,
            &work.activations,
            ACTIVATION_BASE,
        )?;
        self.service_i8_request(
            ffi::im2p_weight_read_request,
            ffi::im2p_put_weight_read_response,
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
                ffi::im2p_put_scale_read_response(
                    self.handle.as_ptr(),
                    request.tag,
                    values.as_ptr(),
                    request.element_count,
                )
            };
            self.require_ready("scale_read_response", accepted)?;
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
            self.require_ready("matrix_read_response", accepted)?;
        } else if status != ffi::IM2P_REQUEST_ABSENT {
            return Err(Error::RtlNotReady {
                operation: "matrix_read_request",
            });
        }
        Ok(())
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
                unsafe { ffi::im2p_put_output_write_response(self.handle.as_ptr(), request.tag) };
            self.require_ready("output_write_response", accepted)?;
        } else if status != ffi::IM2P_REQUEST_ABSENT {
            return Err(Error::RtlNotReady {
                operation: "output_write_request",
            });
        }
        Ok(())
    }
}
