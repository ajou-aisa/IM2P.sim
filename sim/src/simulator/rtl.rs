use crate::ffi;

use super::{Error, Im2pSimulator, VectorOp};

const TIMEOUT_CYCLES: u64 = 100_000;

pub(super) struct StartExecution {
    pub base_row: usize,
    pub row_count: usize,
    pub accumulate: bool,
    pub vector_op: VectorOp,
    pub k_start: usize,
    pub k_count: usize,
}

impl Im2pSimulator {
    pub fn reset(&mut self) {
        self.loaded_scaling = None;
        // SAFETY: handle is valid and owned by this simulator.
        unsafe { ffi::im2p_reset(self.handle.as_ptr()) };
    }

    fn tick(&mut self) {
        // SAFETY: handle is valid and owned by this simulator.
        unsafe { ffi::im2p_tick(self.handle.as_ptr()) };
    }

    fn wait_until(
        &mut self,
        operation: &'static str,
        ready: impl Fn(*mut std::ffi::c_void) -> bool,
    ) -> Result<(), Error> {
        let start = self.cycles();
        while !ready(self.handle.as_ptr()) {
            if self.cycles() - start >= TIMEOUT_CYCLES {
                return Err(Error::Timeout {
                    operation,
                    cycles: TIMEOUT_CYCLES,
                });
            }
            self.tick();
        }
        Ok(())
    }

    pub(super) fn begin_weight_load(&mut self) -> Result<(), Error> {
        // SAFETY: handle is valid and call has no pointer arguments.
        let ok = unsafe { ffi::im2p_begin_weight_load(self.handle.as_ptr()) };
        self.require_ready("begin_weight_load", ok)
    }

    pub(super) fn wait_load_weight_ready(&mut self) -> Result<(), Error> {
        self.wait_until("load_weight_row", |handle| {
            // SAFETY: callback receives simulator's valid handle.
            unsafe { ffi::im2p_load_weight_ready(handle) != 0 }
        })
    }

    pub(super) fn load_weight_row(&mut self, row: usize, values: &[i8]) -> Result<(), Error> {
        self.require_len("weights", values)?;
        // SAFETY: values contains exactly DIM bytes for synchronous FFI copy.
        let ok =
            unsafe { ffi::im2p_load_weight_row(self.handle.as_ptr(), row as u32, values.as_ptr()) };
        self.require_ready("load_weight_row", ok)
    }

    pub(super) fn wait_weights_ready(&mut self) -> Result<(), Error> {
        self.wait_until("weights_ready", |handle| {
            // SAFETY: callback receives simulator's valid handle.
            unsafe { ffi::im2p_weights_ready(handle) != 0 }
        })
    }

    pub(super) fn configure_scaling(
        &mut self,
        block_size: usize,
        total_k: usize,
        block_count: usize,
    ) -> Result<(), Error> {
        // SAFETY: validated metadata fits C ABI widths.
        let ok = unsafe {
            ffi::im2p_configure_scaling(
                self.handle.as_ptr(),
                block_size as u32,
                total_k as u32,
                block_count as u32,
            )
        };
        self.require_ready("configure_scaling", ok)
    }

    pub(super) fn load_scale_block(&mut self, scales: &[i8]) -> Result<(), Error> {
        self.require_len("scales", scales)?;
        // SAFETY: scales contains exactly DIM bytes for synchronous FFI copy.
        let ok = unsafe { ffi::im2p_load_scale_block(self.handle.as_ptr(), scales.as_ptr()) };
        self.require_ready("load_scale_block", ok)
    }

    pub(super) fn wait_scale_load_ready(&mut self) -> Result<(), Error> {
        self.wait_until("scale_load_ready", |handle| {
            // SAFETY: callback receives simulator's valid handle.
            unsafe { ffi::im2p_scale_load_ready(handle) != 0 }
        })
    }

    pub(super) fn start_execution(&mut self, execution: StartExecution) -> Result<(), Error> {
        // SAFETY: all scalar values were validated before this call.
        let ok = unsafe {
            ffi::im2p_start_execution(
                self.handle.as_ptr(),
                execution.base_row as u32,
                execution.row_count as u32,
                i32::from(execution.accumulate),
                execution.vector_op.encoding(),
                execution.k_start as u32,
                execution.k_count as u32,
            )
        };
        self.require_ready("start_execution", ok)
    }

    pub(super) fn wait_activation_ready(&mut self) -> Result<(), Error> {
        self.wait_until("activation_ready", |handle| {
            // SAFETY: callback receives simulator's valid handle.
            unsafe { ffi::im2p_activation_ready(handle) != 0 }
        })
    }

    pub(super) fn push_activation_row(&mut self, values: &[i8]) -> Result<(), Error> {
        self.require_len("activations", values)?;
        // SAFETY: values contains exactly DIM bytes for synchronous FFI copy.
        let ok = unsafe { ffi::im2p_put_activation_row(self.handle.as_ptr(), values.as_ptr()) };
        self.require_ready("put_activation_row", ok)
    }

    pub(super) fn wait_execution_done(&mut self) -> Result<(), Error> {
        self.wait_until("execution_done", |handle| {
            // SAFETY: callback receives simulator's valid handle.
            unsafe { ffi::im2p_execution_done(handle) != 0 }
        })
    }

    pub(super) fn acknowledge_execution(&mut self) -> Result<(), Error> {
        // SAFETY: handle is valid and call has no pointer arguments.
        let ok = unsafe { ffi::im2p_acknowledge_execution(self.handle.as_ptr()) };
        self.require_ready("acknowledge_execution", ok)
    }

    pub(super) fn wait_idle(&mut self) -> Result<(), Error> {
        self.wait_until("idle", |handle| {
            // SAFETY: callback receives simulator's valid handle.
            unsafe { ffi::im2p_idle(handle) != 0 }
        })
    }

    pub fn write_accumulator_row(&mut self, row: usize, values: &[i32]) -> Result<(), Error> {
        if row >= 256 {
            return Err(Error::InvalidAccumulatorRow {
                maximum: 255,
                actual: row,
            });
        }
        if values.len() != self.dim {
            return Err(Error::InvalidBufferLength {
                name: "accumulator",
                expected: self.dim,
                actual: values.len(),
            });
        }
        // SAFETY: values contains exactly DIM readable i32 elements.
        let ok = unsafe {
            ffi::im2p_write_accumulator_row(self.handle.as_ptr(), row as u32, values.as_ptr())
        };
        self.require_ready("write_accumulator_row", ok)
    }

    pub(super) fn read_accumulator_row(&mut self, row: usize) -> Result<Vec<i32>, Error> {
        let mut values = vec![0_i32; self.dim];
        // SAFETY: values exposes DIM writable i32 elements for synchronous copy.
        let ok = unsafe {
            ffi::im2p_read_accumulator_row(self.handle.as_ptr(), row as u32, values.as_mut_ptr())
        };
        if ok == 0 {
            return Err(Error::RtlNotReady {
                operation: "read_accumulator_row",
            });
        }
        Ok(values)
    }

    fn require_len(&self, name: &'static str, values: &[i8]) -> Result<(), Error> {
        if values.len() != self.dim {
            return Err(Error::InvalidBufferLength {
                name,
                expected: self.dim,
                actual: values.len(),
            });
        }
        Ok(())
    }

    fn require_ready(&mut self, operation: &'static str, ready: i32) -> Result<(), Error> {
        if ready == 0 {
            return Err(Error::RtlNotReady { operation });
        }
        self.tick();
        Ok(())
    }
}
