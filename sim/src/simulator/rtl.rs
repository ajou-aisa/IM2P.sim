use crate::ffi;

use super::{Error, Im2pSimulator, KBlockScaleMatrixView, VectorOp};

mod accumulator;
mod helpers;

pub(super) struct StartExecution {
    pub base_row: usize,
    pub row_count: usize,
    pub accumulate: bool,
    pub vector_op: VectorOp,
    pub k_start: usize,
    pub k_count: usize,
}

impl Im2pSimulator {
    pub(super) fn matrix_debug(&self) -> ffi::MatrixDebug {
        let mut debug = ffi::MatrixDebug::default();
        // SAFETY: handle and output pointer remain valid for this call.
        unsafe { ffi::im2p_matrix_debug(self.handle.as_ptr(), &mut debug) };
        debug
    }

    pub fn reset(&mut self) {
        // SAFETY: handle is valid and owned by this simulator.
        unsafe { ffi::im2p_reset(self.handle.as_ptr()) };
    }

    pub(super) fn tick_raw(&mut self) {
        // SAFETY: handle is valid and owned by this simulator.
        unsafe { ffi::im2p_tick(self.handle.as_ptr()) };
    }

    pub(super) fn tick_staged_raw(&mut self) {
        // SAFETY: all staged inputs borrow storage through this edge only.
        unsafe { ffi::im2p_tick_staged(self.handle.as_ptr()) };
    }

    pub(super) fn begin_weight_load(&mut self) -> Result<(), Error> {
        // SAFETY: handle is valid and call has no pointer arguments.
        let ready = unsafe { ffi::im2p_begin_weight_load(self.handle.as_ptr()) };
        self.require_ready("begin_weight_load", ready)
    }

    pub(super) fn wait_load_weight_ready(&mut self) -> Result<(), Error> {
        self.wait_until("load_weight_row", None, |handle| {
            // SAFETY: callback receives simulator's valid handle.
            unsafe { ffi::im2p_load_weight_ready(handle) != 0 }
        })
    }

    pub(super) fn load_weight_row(&mut self, row: usize, values: &[i8]) -> Result<(), Error> {
        self.require_i8_row("weights", values)?;
        let row = u32::try_from(row).map_err(|_| Error::InvalidKRange)?;
        // SAFETY: values contains exactly DIM readable i8 elements for this call.
        let ready =
            unsafe { ffi::im2p_load_weight_row(self.handle.as_ptr(), row, values.as_ptr()) };
        self.require_ready("load_weight_row", ready)
    }

    pub(super) fn wait_weights_ready(&mut self) -> Result<(), Error> {
        self.wait_until("weights_ready", None, |handle| {
            // SAFETY: callback receives simulator's valid handle.
            unsafe { ffi::im2p_weights_ready(handle) != 0 }
        })
    }

    pub(super) fn configure_scaling(
        &mut self,
        block_size: usize,
        total_k: usize,
        context: u64,
    ) -> Result<(), Error> {
        let block_size = u32::try_from(block_size).map_err(|_| Error::InvalidKRange)?;
        let total_k = u32::try_from(total_k).map_err(|_| Error::InvalidKRange)?;
        // SAFETY: validated metadata fits generated RTL ports.
        let ready = unsafe {
            ffi::im2p_configure_scaling(self.handle.as_ptr(), block_size, total_k, context)
        };
        self.require_ready("configure_scaling", ready)
    }

    pub(super) fn start_execution(&mut self, execution: StartExecution) -> Result<(), Error> {
        let base_row =
            u32::try_from(execution.base_row).map_err(|_| Error::InvalidAccumulatorRow {
                maximum: 255,
                actual: execution.base_row,
            })?;
        let row_count = u32::try_from(execution.row_count).map_err(|_| Error::InvalidTileShape)?;
        let k_start = u32::try_from(execution.k_start).map_err(|_| Error::InvalidKRange)?;
        let k_count = u32::try_from(execution.k_count).map_err(|_| Error::InvalidKRange)?;
        // SAFETY: all scalar arguments were validated and fit their C ABI ports.
        let ready = unsafe {
            ffi::im2p_start_execution(
                self.handle.as_ptr(),
                base_row,
                row_count,
                i32::from(execution.accumulate),
                execution.vector_op.encoding(),
                k_start,
                k_count,
            )
        };
        self.require_ready("start_execution", ready)
    }

    pub(super) fn wait_activation_ready(
        &mut self,
        matrix: Option<&KBlockScaleMatrixView<'_>>,
    ) -> Result<(), Error> {
        self.wait_until("activation_ready", matrix, |handle| {
            // SAFETY: callback receives simulator's valid handle.
            unsafe { ffi::im2p_activation_ready(handle) != 0 }
        })
    }

    pub(super) fn push_activation_row(
        &mut self,
        values: &[i8],
        matrix: Option<&KBlockScaleMatrixView<'_>>,
    ) -> Result<(), Error> {
        self.require_i8_row("activations", values)?;
        self.service_scale_request(matrix)?;
        // SAFETY: values contains exactly DIM readable i8 elements for this call.
        let ready = unsafe { ffi::im2p_put_activation_row(self.handle.as_ptr(), values.as_ptr()) };
        self.require_ready("put_activation_row", ready)
    }

    pub(super) fn wait_execution_done(
        &mut self,
        matrix: Option<&KBlockScaleMatrixView<'_>>,
    ) -> Result<(), Error> {
        self.wait_until("execution_done", matrix, |handle| {
            // SAFETY: callback receives simulator's valid handle.
            unsafe { ffi::im2p_execution_done(handle) != 0 }
        })
    }

    pub(super) fn flush_scale_requests(
        &mut self,
        matrix: Option<&KBlockScaleMatrixView<'_>>,
    ) -> Result<(), Error> {
        for _ in 0..2 {
            if !self.service_scale_request(matrix)? {
                return Ok(());
            }
        }
        if self.service_scale_request(matrix)? {
            let debug = self.matrix_debug();
            return Err(Error::Timeout {
                operation: "flush_scale_requests",
                cycles: 2,
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
            });
        }
        Ok(())
    }

    pub(super) fn scale_counters(&self) -> ffi::ScaleCounters {
        let mut counters = ffi::ScaleCounters::default();
        // SAFETY: handle and output pointer remain valid for this call.
        unsafe { ffi::im2p_scale_counters(self.handle.as_ptr(), &mut counters) };
        counters
    }

    pub(super) fn acknowledge_execution(&mut self) -> Result<(), Error> {
        // SAFETY: handle is valid and call has no pointer arguments.
        let ready = unsafe { ffi::im2p_acknowledge_execution(self.handle.as_ptr()) };
        self.require_ready("acknowledge_execution", ready)
    }

    pub(super) fn wait_idle(&mut self) -> Result<(), Error> {
        self.wait_until("idle", None, |handle| {
            // SAFETY: callback receives simulator's valid handle.
            unsafe { ffi::im2p_idle(handle) != 0 }
        })
    }
}
