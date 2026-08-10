use crate::ffi;
use crate::TileStats;
use std::ffi::c_void;
use std::ptr::NonNull;

const TIMEOUT_CYCLES: u64 = 100_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VectorOp {
    Bypass,
    Multiply,
    Shift,
}

impl VectorOp {
    fn encoding(self) -> u8 {
        match self {
            Self::Bypass => 0,
            Self::Multiply => 1,
            Self::Shift => 2,
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum Error {
    VerilatorUnavailable,
    InvalidDimension,
    InvalidBufferLength {
        name: &'static str,
        expected: usize,
        actual: usize,
    },
    MissingScales {
        operation: VectorOp,
    },
    InvalidTileShape,
    RtlTimeout {
        operation: &'static str,
        cycle: u64,
        dim: usize,
    },
    RtlNotReady {
        operation: &'static str,
    },
    FfiFailure {
        operation: &'static str,
    },
}

pub struct TileRequest<'a> {
    pub activations: &'a [i8],
    pub weights: &'a [i8],
    /// Signed K-group weight scale for each valid output column.
    ///
    /// Length must equal `valid_n`. Every output row shares this same
    /// column-wise scale vector.
    pub scales: Option<&'a [i8]>,
    pub valid_m: usize,
    pub valid_n: usize,
    pub valid_k: usize,
    pub accumulate: bool,
    pub vector_op: VectorOp,
}

pub struct Im2pSimulator {
    handle: NonNull<c_void>,
    dim: usize,
}

impl Im2pSimulator {
    pub fn new() -> Result<Self, Error> {
        let dim = compiled_dim();
        let handle = unsafe { ffi::im2p_create() };
        let handle = NonNull::new(handle).ok_or(Error::VerilatorUnavailable)?;
        Ok(Self { handle, dim })
    }

    pub const fn dim(&self) -> usize {
        self.dim
    }

    pub fn cycles(&self) -> u64 {
        unsafe { ffi::im2p_cycle_count(self.handle.as_ptr()) }
    }

    pub fn reset(&mut self) {
        unsafe { ffi::im2p_reset(self.handle.as_ptr()) };
    }

    pub fn tick(&mut self) {
        unsafe { ffi::im2p_tick(self.handle.as_ptr()) };
    }

    pub fn begin_weight_load(&mut self) -> Result<(), Error> {
        self.require_ready(ffi::im2p_begin_weight_load, "begin_weight_load")
    }

    pub fn load_weight_row(&mut self, row: usize, values: &[i8]) -> Result<(), Error> {
        self.require_len("weights", values)?;
        if row >= self.dim {
            return Err(Error::InvalidTileShape);
        }
        let ok =
            unsafe { ffi::im2p_load_weight_row(self.handle.as_ptr(), row as u32, values.as_ptr()) };
        if ok == 0 {
            Err(Error::RtlNotReady {
                operation: "load_weight_row",
            })
        } else {
            Ok(())
        }
    }

    pub fn start_execution(
        &mut self,
        base_row: usize,
        row_count: usize,
        accumulate: bool,
        vector_op: VectorOp,
    ) -> Result<(), Error> {
        let ok = unsafe {
            ffi::im2p_start_execution(
                self.handle.as_ptr(),
                base_row as u32,
                row_count as u32,
                i32::from(accumulate),
                vector_op.encoding(),
            )
        };
        if ok == 0 {
            Err(Error::RtlNotReady {
                operation: "start_execution",
            })
        } else {
            Ok(())
        }
    }

    pub fn push_activation_row(
        &mut self,
        values: &[i8],
        scales: Option<&[i8]>,
    ) -> Result<(), Error> {
        self.require_len("activations", values)?;
        if let Some(scale_values) = scales {
            self.require_len("scales", scale_values)?;
        }
        let (scale_ptr, valid) =
            scales.map_or((std::ptr::null(), 0), |values| (values.as_ptr(), 1));
        let ok = unsafe {
            ffi::im2p_put_activation_row(self.handle.as_ptr(), values.as_ptr(), scale_ptr, valid)
        };
        if ok == 0 {
            Err(Error::RtlNotReady {
                operation: "put_activation_row",
            })
        } else {
            Ok(())
        }
    }

    pub fn wait_execution_done(&mut self) -> Result<(), Error> {
        self.wait_until("execution_done", |handle| unsafe {
            ffi::im2p_execution_done(handle) != 0
        })
    }

    pub fn acknowledge_execution(&mut self) -> Result<(), Error> {
        self.require_ready(ffi::im2p_acknowledge_execution, "acknowledge_execution")
    }

    pub fn write_accumulator_row(&mut self, row: usize, values: &[i32]) -> Result<(), Error> {
        self.require_len("accumulator", values)?;
        let ok = unsafe {
            ffi::im2p_write_accumulator_row(self.handle.as_ptr(), row as u32, values.as_ptr())
        };
        if ok == 0 {
            Err(Error::RtlNotReady {
                operation: "write_accumulator_row",
            })
        } else {
            Ok(())
        }
    }

    pub fn read_accumulator_row(&mut self, row: usize, values: &mut [i32]) -> Result<(), Error> {
        self.require_len("accumulator", values)?;
        let ok = unsafe {
            ffi::im2p_read_accumulator_row(self.handle.as_ptr(), row as u32, values.as_mut_ptr())
        };
        if ok == 0 {
            Err(Error::RtlNotReady {
                operation: "read_accumulator_row",
            })
        } else {
            Ok(())
        }
    }

    pub fn execute_tile(
        &mut self,
        request: &TileRequest<'_>,
        output: &mut [i32],
    ) -> Result<TileStats, Error> {
        self.validate_tile(request, output)?;
        let start = self.cycles();
        self.wait_until("idle", |handle| unsafe { ffi::im2p_idle(handle) != 0 })?;
        self.begin_weight_load()?;
        let weight_start = self.cycles();
        for row in 0..self.dim {
            let mut values = vec![0_i8; self.dim];
            if row < request.valid_k {
                let source = &request.weights[row * request.valid_n..(row + 1) * request.valid_n];
                values[..request.valid_n].copy_from_slice(source);
            }
            self.wait_until("load_weight_row", |handle| unsafe {
                ffi::im2p_load_weight_ready(handle) != 0
            })?;
            self.load_weight_row(row, &values)?;
        }
        let weight_cycles = self.cycles() - weight_start;
        self.wait_until("weights_ready", |handle| unsafe {
            ffi::im2p_weights_ready(handle) != 0
        })?;
        self.start_execution(0, request.valid_m, request.accumulate, request.vector_op)?;
        let compute_start = self.cycles();
        let padded_scales = request.scales.map(|source| {
            let mut scales = vec![0_i8; self.dim];
            scales[..request.valid_n].copy_from_slice(source);
            scales
        });
        for row in 0..request.valid_m {
            let mut values = vec![0_i8; self.dim];
            let source = &request.activations[row * request.valid_k..(row + 1) * request.valid_k];
            values[..request.valid_k].copy_from_slice(source);
            self.wait_until("activation_ready", |handle| unsafe {
                ffi::im2p_activation_ready(handle) != 0
            })?;
            self.push_activation_row(&values, padded_scales.as_deref())?;
        }
        self.wait_execution_done()?;
        let compute_cycles = self.cycles() - compute_start;
        for row in 0..request.valid_m {
            let mut values = vec![0_i32; self.dim];
            self.read_accumulator_row(row, &mut values)?;
            output[row * request.valid_n..(row + 1) * request.valid_n]
                .copy_from_slice(&values[..request.valid_n]);
        }
        self.acknowledge_execution()?;
        Ok(TileStats::from_counts(
            weight_cycles,
            compute_cycles,
            self.cycles() - start,
            request.valid_m,
            request.valid_n,
            request.valid_k,
            self.dim,
        ))
    }

    fn wait_until<F>(&mut self, operation: &'static str, mut ready: F) -> Result<(), Error>
    where
        F: FnMut(*mut c_void) -> bool,
    {
        let start = self.cycles();
        while !ready(self.handle.as_ptr()) {
            if self.cycles() - start >= TIMEOUT_CYCLES {
                return Err(Error::RtlTimeout {
                    operation,
                    cycle: self.cycles(),
                    dim: self.dim,
                });
            }
            self.tick();
        }
        Ok(())
    }

    fn require_ready(
        &mut self,
        operation: unsafe extern "C" fn(*mut c_void) -> i32,
        name: &'static str,
    ) -> Result<(), Error> {
        if unsafe { operation(self.handle.as_ptr()) } == 0 {
            Err(Error::RtlNotReady { operation: name })
        } else {
            Ok(())
        }
    }

    fn require_len<T>(&self, name: &'static str, values: &[T]) -> Result<(), Error> {
        if values.len() == self.dim {
            Ok(())
        } else {
            Err(Error::InvalidBufferLength {
                name,
                expected: self.dim,
                actual: values.len(),
            })
        }
    }

    fn validate_tile(&self, request: &TileRequest<'_>, output: &[i32]) -> Result<(), Error> {
        if request.valid_m == 0
            || request.valid_n == 0
            || request.valid_k == 0
            || request.valid_m > self.dim
            || request.valid_n > self.dim
            || request.valid_k > self.dim
        {
            return Err(Error::InvalidTileShape);
        }
        let expected_activations = request.valid_m * request.valid_k;
        let expected_weights = request.valid_k * request.valid_n;
        let expected_output = request.valid_m * request.valid_n;
        if request.activations.len() != expected_activations {
            return Err(Error::InvalidBufferLength {
                name: "activations",
                expected: expected_activations,
                actual: request.activations.len(),
            });
        }
        if request.weights.len() != expected_weights {
            return Err(Error::InvalidBufferLength {
                name: "weights",
                expected: expected_weights,
                actual: request.weights.len(),
            });
        }
        if output.len() != expected_output {
            return Err(Error::InvalidBufferLength {
                name: "output",
                expected: expected_output,
                actual: output.len(),
            });
        }
        if let Some(scales) = request.scales {
            if scales.len() != request.valid_n {
                return Err(Error::InvalidBufferLength {
                    name: "scales",
                    expected: request.valid_n,
                    actual: scales.len(),
                });
            }
        } else if request.vector_op != VectorOp::Bypass {
            return Err(Error::MissingScales {
                operation: request.vector_op,
            });
        }
        Ok(())
    }
}

impl Drop for Im2pSimulator {
    fn drop(&mut self) {
        unsafe { ffi::im2p_destroy(self.handle.as_ptr()) };
    }
}

fn compiled_dim() -> usize {
    option_env!("IM2P_DIM")
        .and_then(|value| value.parse().ok())
        .unwrap_or(16)
}
