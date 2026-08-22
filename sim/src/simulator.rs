pub(crate) mod descriptor;
mod matmul;
mod rtl;
mod striped;
pub(crate) mod validation;

use std::ffi::c_void;
use std::ptr::NonNull;

use crate::{ffi, ActivationValue, ScaleFetchStats, TileStats};
use rtl::StartExecution;
pub use striped::StripedMatmul;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VectorOp {
    Bypass,
    Multiply,
    Shift,
    External,
}

impl VectorOp {
    fn encoding(self) -> u8 {
        match self {
            Self::Bypass => 0,
            Self::Multiply => 1,
            Self::Shift => 2,
            Self::External => 3,
        }
    }
}

pub(crate) type ReadProvider =
    unsafe extern "C" fn(*mut c_void, usize, usize, usize, *mut i8) -> i32;
pub(crate) type WriteProviderV2 =
    unsafe extern "C" fn(*mut c_void, usize, usize, usize, usize, *const i32) -> i32;
pub(crate) type WriteProviderV3 =
    unsafe extern "C" fn(*mut c_void, usize, usize, usize, usize, *const i64) -> i32;

#[derive(Clone, Copy)]
pub(crate) enum WriteProvider {
    V2(WriteProviderV2),
    V3(WriteProviderV3),
}

#[derive(Clone, Copy)]
pub(crate) struct MemoryProvider {
    pub context: *mut c_void,
    pub read_weight: Option<ReadProvider>,
    pub read_scale: Option<ReadProvider>,
    pub write_output: Option<WriteProvider>,
}

impl MemoryProvider {
    pub fn read_weight(self, row: usize, column: usize, values: &mut [i8]) -> Result<(), Error> {
        let callback = self.read_weight.ok_or(Error::ProviderFailure)?;
        if unsafe { callback(self.context, row, column, values.len(), values.as_mut_ptr()) } == 0 {
            Ok(())
        } else {
            Err(Error::ProviderFailure)
        }
    }

    pub fn read_scale(self, row: usize, column: usize, values: &mut [i8]) -> Result<(), Error> {
        let callback = self.read_scale.ok_or(Error::ProviderFailure)?;
        if unsafe { callback(self.context, row, column, values.len(), values.as_mut_ptr()) } == 0 {
            Ok(())
        } else {
            Err(Error::ProviderFailure)
        }
    }

    pub fn write_output(
        self,
        block: usize,
        row: usize,
        column: usize,
        values: &[i64],
    ) -> Result<(), Error> {
        let callback = self.write_output.ok_or(Error::ProviderFailure)?;
        let status = match callback {
            WriteProvider::V2(callback) => {
                let narrowed = values
                    .iter()
                    .copied()
                    .map(crate::matrix::saturating_i64_to_i32)
                    .collect::<Vec<_>>();
                // SAFETY: Category 8 (FFI boundary): the provider contract guarantees the
                // callback accepts this live context and readable narrowed slice for the call.
                unsafe {
                    callback(
                        self.context,
                        block,
                        row,
                        column,
                        narrowed.len(),
                        narrowed.as_ptr(),
                    )
                }
            }
            // SAFETY: Category 8 (FFI boundary): the provider contract guarantees the callback
            // accepts this live context and readable exact-width slice for the call.
            WriteProvider::V3(callback) => unsafe {
                callback(
                    self.context,
                    block,
                    row,
                    column,
                    values.len(),
                    values.as_ptr(),
                )
            },
        };
        if status == 0 {
            Ok(())
        } else {
            Err(Error::ProviderFailure)
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum Error {
    AllocationFailed,
    InvalidDimension,
    InvalidScaleMatrixLayout,
    InvalidScaleRequest {
        status: i32,
    },
    InvalidBufferLength {
        name: &'static str,
        expected: usize,
        actual: usize,
    },
    MissingScales {
        operation: VectorOp,
    },
    InvalidKRange,
    UnsupportedBlockConfiguration {
        k_start: usize,
        valid_k: usize,
        block_size: usize,
    },
    InvalidAccumulatorRow {
        maximum: usize,
        actual: usize,
    },
    InvalidTileShape,
    RtlNotReady {
        operation: &'static str,
    },
    StripeQueueFull,
    DuplicateStripe,
    LateStripe,
    InvalidStripe,
    InvalidActivationStride,
    InvalidWeightStride,
    InvalidOutputStride,
    InvalidLayout,
    UnfinishedStream,
    NoPendingActivation,
    NoPendingOutput,
    ProviderFailure,
    Timeout {
        operation: &'static str,
        cycles: u64,
        matmul_scheduler_state: u8,
        work_scheduler_state: u8,
        matrix_core_state: u8,
        execution_active: bool,
        accepted_rows: u32,
        configured_rows: u32,
        first_column_issued: u32,
        first_column_committed: u32,
        engine_result_valid: bool,
        vector_busy: bool,
        activation_request_valid: bool,
        weight_request_valid: bool,
        scale_request_valid: bool,
        output_request_valid: bool,
        stripe_host_waiting: bool,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct KBlockScaleMatrixView<'a> {
    /// Host-owned block-major matrix storage.
    pub values: &'a [i8],
    pub block_size: usize,
    pub total_k: usize,
    pub columns: usize,
    pub row_stride: usize,
    pub column_offset: usize,
    pub valid_columns: usize,
    /// Cache identity for matrix contents and effective J-tile mapping.
    ///
    /// Callers must use a new value whenever `values`, `columns`,
    /// `row_stride`, `column_offset`, or `valid_columns` changes semantically.
    pub context: u64,
}

#[derive(Debug)]
pub struct TileRequest<'a> {
    pub activations: &'a [ActivationValue],
    pub weights: &'a [i8],
    pub scale_matrix: Option<KBlockScaleMatrixView<'a>>,
    pub valid_m: usize,
    pub valid_n: usize,
    pub valid_k: usize,
    /// Global K origin of this hardware partial.
    pub k_start: usize,
    pub accumulate: bool,
    pub vector_op: VectorOp,
}

pub struct Im2pSimulator {
    handle: NonNull<c_void>,
    dim: usize,
}

impl Im2pSimulator {
    pub fn new() -> Result<Self, Error> {
        // SAFETY: `im2p_create` has no preconditions and returns an owned handle.
        let handle = NonNull::new(unsafe { ffi::im2p_create() }).ok_or(Error::AllocationFailed)?;
        let dim = option_env!("IM2P_DIM")
            .unwrap_or("16")
            .parse::<usize>()
            .map_err(|_| Error::InvalidDimension)?;
        let mut simulator = Self { handle, dim };
        simulator.reset();
        Ok(simulator)
    }

    pub fn dim(&self) -> usize {
        self.dim
    }

    pub fn cycles(&self) -> u64 {
        // SAFETY: handle remains valid until `Drop`.
        unsafe { ffi::im2p_cycle_count(self.handle.as_ptr()) }
    }

    pub fn work_active(&self) -> bool {
        // SAFETY: handle remains valid and the getter is observational.
        unsafe { ffi::im2p_work_active(self.handle.as_ptr()) != 0 }
    }

    pub fn work_cycles(&self) -> u64 {
        // SAFETY: handle remains valid and the getter is observational.
        unsafe { ffi::im2p_work_cycle_count(self.handle.as_ptr()) }
    }

    pub fn last_completed_work_cycles(&self) -> u64 {
        // SAFETY: handle remains valid and the getter is observational.
        unsafe { ffi::im2p_last_completed_work_cycles(self.handle.as_ptr()) }
    }

    pub fn work_interval(&self) -> (u64, u64) {
        // SAFETY: handle remains valid and both getters are observational.
        unsafe {
            (
                ffi::im2p_work_start_cycle(self.handle.as_ptr()),
                ffi::im2p_work_completion_cycle(self.handle.as_ptr()),
            )
        }
    }

    /// Test/diagnostic telemetry for A/W/S/C responses committed on RTL edges.
    pub fn response_concurrency(&self) -> (u8, u8) {
        // SAFETY: handle remains valid and both getters are observational.
        unsafe {
            (
                ffi::im2p_observed_response_mask(self.handle.as_ptr()) as u8,
                ffi::im2p_max_concurrent_responses(self.handle.as_ptr()) as u8,
            )
        }
    }

    pub fn execute_tile(
        &mut self,
        request: &TileRequest<'_>,
        output: &mut [i32],
    ) -> Result<TileStats, Error> {
        let scale_matrix = validation::validate_tile(request, output, self.dim)?;
        let counters_before = self.scale_counters();
        let tile_start = self.cycles();

        let weight_start = self.cycles();
        self.begin_weight_load()?;
        for row in 0..self.dim {
            let mut values = vec![0_i8; self.dim];
            if row < request.valid_k {
                let source = &request.weights[row * request.valid_n..(row + 1) * request.valid_n];
                values[..request.valid_n].copy_from_slice(source);
            }
            self.wait_load_weight_ready()?;
            self.load_weight_row(row, &values)?;
        }
        let weight_load_cycles = self.cycles() - weight_start;
        self.wait_weights_ready()?;

        if let Some(matrix) = scale_matrix {
            self.configure_scaling(matrix.block_size, matrix.total_k, matrix.context)?;
        }
        self.start_execution(StartExecution {
            base_row: 0,
            row_count: request.valid_m,
            accumulate: request.accumulate,
            vector_op: request.vector_op,
            k_start: request.k_start,
            k_count: request.valid_k,
        })?;
        let compute_start = self.cycles();
        for row in 0..request.valid_m {
            let mut values = vec![ActivationValue::default(); self.dim];
            let source = &request.activations[row * request.valid_k..(row + 1) * request.valid_k];
            values[..request.valid_k].copy_from_slice(source);
            self.wait_activation_ready(scale_matrix.as_ref())?;
            self.push_activation_row(&values, scale_matrix.as_ref())?;
        }

        self.wait_execution_done(scale_matrix.as_ref())?;
        self.flush_scale_requests(scale_matrix.as_ref())?;
        let compute_cycles = self.cycles() - compute_start;
        for row in 0..request.valid_m {
            let values = self.read_accumulator_row(row)?;
            for (destination, value) in output[row * request.valid_n..(row + 1) * request.valid_n]
                .iter_mut()
                .zip(values)
            {
                *destination = crate::matrix::saturating_i64_to_i32(value);
            }
        }
        self.acknowledge_execution()?;
        self.wait_idle()?;
        let counters_after = self.scale_counters();
        let scale_fetch = ScaleFetchStats {
            demand_requests: counters_after
                .demand_requests
                .wrapping_sub(counters_before.demand_requests),
            prefetch_requests: counters_after
                .prefetch_requests
                .wrapping_sub(counters_before.prefetch_requests),
            current_hits: counters_after
                .current_hits
                .wrapping_sub(counters_before.current_hits),
            next_hits: counters_after
                .next_hits
                .wrapping_sub(counters_before.next_hits),
            demand_misses: counters_after
                .demand_misses
                .wrapping_sub(counters_before.demand_misses),
            rows_received: counters_after
                .rows_received
                .wrapping_sub(counters_before.rows_received),
            scale_transfer_cycles: counters_after
                .rows_received
                .wrapping_sub(counters_before.rows_received),
            scale_wait_cycles: counters_after
                .wait_cycles
                .wrapping_sub(counters_before.wait_cycles),
        };

        Ok(TileStats::from_counts(
            weight_load_cycles,
            scale_fetch,
            compute_cycles,
            self.cycles() - tile_start,
            request.valid_m,
            request.valid_n,
            request.valid_k,
            self.dim,
        ))
    }
}

impl Drop for Im2pSimulator {
    fn drop(&mut self) {
        // SAFETY: handle is uniquely owned and destroyed exactly once.
        unsafe { ffi::im2p_destroy(self.handle.as_ptr()) };
    }
}
