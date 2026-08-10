mod rtl;
mod validation;

use std::ffi::c_void;
use std::ptr::NonNull;

use crate::{ffi, TileStats};
use rtl::StartExecution;

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
    AllocationFailed,
    InvalidDimension,
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
    TooManyScaleBlocks {
        maximum: usize,
        actual: usize,
    },
    InvalidAccumulatorRow {
        maximum: usize,
        actual: usize,
    },
    InvalidTileShape,
    RtlNotReady {
        operation: &'static str,
    },
    Timeout {
        operation: &'static str,
        cycles: u64,
    },
}

#[derive(Debug)]
pub struct TileRequest<'a> {
    pub activations: &'a [i8],
    pub weights: &'a [i8],
    /// Block-major signed K-group weight scales.
    ///
    /// Layout is `scales[block * valid_n + column]`. Length must equal
    /// `ceil(total_k / block_size) * valid_n`.
    pub scales: Option<&'a [i8]>,
    pub valid_m: usize,
    pub valid_n: usize,
    pub valid_k: usize,
    /// Global K origin of this hardware partial.
    pub k_start: usize,
    /// Global logical K extent covered by the scale table.
    pub total_k: usize,
    /// K-quant block size used by RTL scale selection.
    pub block_size: usize,
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

    pub fn execute_tile(
        &mut self,
        request: &TileRequest<'_>,
        output: &mut [i32],
    ) -> Result<TileStats, Error> {
        let block_count = validation::validate_tile(request, output, self.dim)?;
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

        let scale_start = self.cycles();
        self.configure_scaling(request.block_size, request.total_k, block_count)?;
        if let Some(scales) = request.scales {
            for block in 0..block_count {
                let mut padded = vec![0_i8; self.dim];
                let begin = block * request.valid_n;
                padded[..request.valid_n].copy_from_slice(&scales[begin..begin + request.valid_n]);
                self.load_scale_block(&padded)?;
            }
            self.wait_scale_load_ready()?;
        }
        let scale_load_cycles = self.cycles() - scale_start;

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
            let mut values = vec![0_i8; self.dim];
            let source = &request.activations[row * request.valid_k..(row + 1) * request.valid_k];
            values[..request.valid_k].copy_from_slice(source);
            self.wait_activation_ready()?;
            self.push_activation_row(&values)?;
        }

        self.wait_execution_done()?;
        let compute_cycles = self.cycles() - compute_start;
        for row in 0..request.valid_m {
            let values = self.read_accumulator_row(row)?;
            output[row * request.valid_n..(row + 1) * request.valid_n]
                .copy_from_slice(&values[..request.valid_n]);
        }
        self.acknowledge_execution()?;
        self.wait_idle()?;

        Ok(TileStats::from_counts(
            weight_load_cycles,
            scale_load_cycles,
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
