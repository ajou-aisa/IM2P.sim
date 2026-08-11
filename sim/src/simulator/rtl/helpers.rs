use std::ptr;

use crate::ffi;

use super::super::{Error, Im2pSimulator, KBlockScaleMatrixView};

impl Im2pSimulator {
    pub(super) fn service_scale_request(
        &mut self,
        matrix: Option<&KBlockScaleMatrixView<'_>>,
    ) -> Result<bool, Error> {
        let descriptor = matrix.map(|matrix| ffi::ScaleMatrixView {
            values: matrix.values.as_ptr(),
            values_len: matrix.values.len(),
            block_size: matrix.block_size,
            total_k: matrix.total_k,
            columns: matrix.columns,
            row_stride: matrix.row_stride,
            column_offset: matrix.column_offset,
            valid_columns: matrix.valid_columns,
            context: matrix.context,
        });
        let descriptor_ptr = descriptor.as_ref().map_or(ptr::null(), ptr::from_ref);

        // SAFETY: validated matrix storage remains borrowed for this synchronous
        // call. C++ bounds-checks every field, copies one row, and retains no
        // descriptor or values pointer after returning.
        match unsafe { ffi::im2p_service_scale_request(self.handle.as_ptr(), descriptor_ptr) } {
            0 => Ok(false),
            1 => Ok(true),
            status => Err(Error::InvalidScaleRequest { status }),
        }
    }

    pub(super) fn wait_until(
        &mut self,
        operation: &'static str,
        matrix: Option<&KBlockScaleMatrixView<'_>>,
        ready: impl Fn(*mut std::ffi::c_void) -> bool,
    ) -> Result<(), Error> {
        const TIMEOUT_CYCLES: u64 = 100_000;
        for _ in 0..TIMEOUT_CYCLES {
            self.service_scale_request(matrix)?;
            if ready(self.handle.as_ptr()) {
                return Ok(());
            }
            self.tick_raw();
        }
        let debug = self.matrix_debug();
        Err(Error::Timeout {
            operation,
            cycles: TIMEOUT_CYCLES,
            matmul_scheduler_state: debug.matmul_scheduler_state,
            work_scheduler_state: debug.work_scheduler_state,
            matrix_core_state: debug.matrix_core_state,
            execution_active: debug.execution_active != 0,
            accepted_rows: debug.accepted_rows,
            configured_rows: debug.configured_rows,
            first_column_issued: debug.first_column_issued,
            first_column_committed: debug.first_column_committed,
            engine_result_valid: debug.engine_result_valid != 0,
            vector_busy: debug.vector_busy != 0,
            activation_request_valid: debug.activation_request_valid != 0,
            weight_request_valid: debug.weight_request_valid != 0,
            scale_request_valid: debug.scale_request_valid != 0,
            output_request_valid: debug.output_request_valid != 0,
            stripe_host_waiting: debug.stripe_host_waiting != 0,
        })
    }

    pub(super) fn require_i8_row(&self, name: &'static str, values: &[i8]) -> Result<(), Error> {
        if values.len() != self.dim {
            return Err(Error::InvalidBufferLength {
                name,
                expected: self.dim,
                actual: values.len(),
            });
        }
        Ok(())
    }

    pub(in crate::simulator) fn require_ready(
        &mut self,
        operation: &'static str,
        ready: i32,
    ) -> Result<(), Error> {
        if ready == 0 {
            return Err(Error::RtlNotReady { operation });
        }
        self.tick_raw();
        Ok(())
    }
}
