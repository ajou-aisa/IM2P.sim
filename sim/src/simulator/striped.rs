use std::collections::VecDeque;

use crate::{ffi, ActivationStripe, StripeCompletion, StripeWorkDesc, WorkStats};

use super::{
    matmul::{ACTIVATION_BASE, OUTPUT_BASE, SCALE_BASE, WEIGHT_BASE},
    Error, Im2pSimulator,
};

mod provider;
const STRIPED_TIMEOUT_CYCLES: u64 = 10_000_000;

pub struct StripedMatmul<'a> {
    simulator: Im2pSimulator,
    descriptor: StripeWorkDesc<'a>,
    published: VecDeque<ActivationStripe>,
    completed: VecDeque<StripeCompletion>,
    outstanding_stripes: usize,
    next_stripe_id: u32,
    next_row: usize,
    counters_before: ffi::MatrixCounters,
    scales_before: ffi::ScaleCounters,
    start_cycle: u64,
}

impl Im2pSimulator {
    pub fn begin_striped_matmul<'a>(
        mut self,
        descriptor: &StripeWorkDesc<'a>,
    ) -> Result<StripedMatmul<'a>, Error> {
        validate_descriptor(descriptor)?;
        let counters_before = self.matrix_counters();
        let scales_before = self.scale_counters();
        let start_cycle = self.cycles();
        let scale = descriptor.scale_matrix;
        let rtl_descriptor = ffi::MatmulDescriptor {
            job_id: descriptor.work_context as u32,
            mode: 1,
            activation_base: ACTIVATION_BASE,
            weight_base: WEIGHT_BASE,
            scale_base: SCALE_BASE,
            output_base: OUTPUT_BASE,
            activation_row_stride: descriptor.reduction as u64,
            weight_row_stride: descriptor.columns as u64,
            scale_row_stride: scale.map_or(1, |view| view.row_stride) as u64,
            output_row_stride: (descriptor.columns * size_of::<i32>()) as u64,
            row_count: descriptor.rows as u32,
            column_count: descriptor.columns as u32,
            reduction_count: descriptor.reduction as u32,
            k_origin: 0,
            scale_total_k: scale.map_or(descriptor.reduction, |view| view.total_k) as u32,
            scale_block_size: scale.map_or(descriptor.reduction, |view| view.block_size) as u32,
            scale_context: descriptor.work_context,
            accumulate_first_fragment: 0,
            vector_op: descriptor.vector_op.encoding(),
        };
        // SAFETY: descriptor is copied by the bridge during this call.
        let accepted = unsafe { ffi::im2p_start_matmul(self.handle.as_ptr(), &rtl_descriptor) };
        self.require_ready("start_striped_matmul", accepted)?;
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
            published: VecDeque::new(),
            completed: VecDeque::new(),
            outstanding_stripes: 0,
            next_stripe_id: 0,
            next_row: 0,
            counters_before,
            scales_before,
            start_cycle,
        })
    }
}

impl StripedMatmul<'_> {
    pub fn publish_stripe(&mut self, stripe: ActivationStripe) -> Result<(), Error> {
        if stripe.stripe_id != self.next_stripe_id
            || stripe.row_begin != self.next_row
            || stripe.row_count == 0
            || stripe.row_begin + stripe.row_count > self.descriptor.rows
        {
            return Err(Error::InvalidStripe);
        }
        // SAFETY: scalar metadata is copied synchronously.
        let accepted = unsafe {
            ffi::im2p_publish_activation_stripe(
                self.simulator.handle.as_ptr(),
                stripe.row_begin as u32,
                stripe.row_count as u32,
            )
        };
        if accepted == 0 {
            return Err(Error::StripeQueueFull);
        }
        self.simulator
            .require_ready("publish_activation_stripe", accepted)?;
        self.next_stripe_id += 1;
        self.next_row += stripe.row_count;
        self.outstanding_stripes += 1;
        self.published.push_back(stripe);
        Ok(())
    }
    pub fn npu_ready(&self) -> bool {
        // SAFETY: simulator handle remains valid while the job owns it.
        unsafe { ffi::im2p_activation_stripe_ready(self.simulator.handle.as_ptr()) != 0 }
    }
    pub fn host_available(&self) -> bool {
        self.outstanding_stripes != 0
    }

    pub fn progress(&mut self, cycle_budget: u64) -> Result<(), Error> {
        for _ in 0..cycle_budget {
            self.service_static_reads()?;
            self.drain_completion()?;
            self.simulator.tick_raw();
        }
        Ok(())
    }

    pub fn pending_activation_row(&self) -> Option<usize> {
        self.activation_request()
            .ok()
            .flatten()
            .map(|(row, _, _)| row)
    }

    pub fn supply_activation_row(&mut self, row: usize, values: &[i8]) -> Result<(), Error> {
        let (expected_row, column, request) = self
            .activation_request()?
            .ok_or(Error::NoPendingActivation)?;
        if row != expected_row || values.len() < column + request.element_count as usize {
            return Err(Error::NoPendingActivation);
        }
        // SAFETY: selected slice contains the requested readable lane count.
        let accepted = unsafe {
            ffi::im2p_put_activation_read_response(
                self.simulator.handle.as_ptr(),
                request.tag,
                values[column..].as_ptr(),
                request.element_count,
            )
        };
        self.simulator
            .require_ready("activation_read_response", accepted)
    }
    pub fn pending_output_row(&self) -> Option<usize> {
        self.output_request()
            .ok()
            .flatten()
            .map(|(row, _, _, _)| row)
    }
    pub fn pending_output_region(&self) -> Option<(usize, usize)> {
        self.output_request()
            .ok()
            .flatten()
            .map(|(row, column, _, _)| (row, column))
    }

    pub fn take_output_row(&self, row: usize) -> Result<Vec<i32>, Error> {
        let (expected_row, column, _, _) = self.output_request()?.ok_or(Error::NoPendingOutput)?;
        if row != expected_row {
            return Err(Error::NoPendingOutput);
        }
        self.take_output_region(row, column)
    }

    pub fn take_output_region(&self, row: usize, column: usize) -> Result<Vec<i32>, Error> {
        let (expected_row, expected_column, request, values) =
            self.output_request()?.ok_or(Error::NoPendingOutput)?;
        if row != expected_row || column != expected_column {
            return Err(Error::NoPendingOutput);
        }
        Ok(values[..request.element_count as usize].to_vec())
    }

    pub fn acknowledge_output_row(&mut self, row: usize) -> Result<(), Error> {
        let (expected_row, _, request, _) = self.output_request()?.ok_or(Error::NoPendingOutput)?;
        if row != expected_row {
            return Err(Error::NoPendingOutput);
        }
        // SAFETY: acknowledgement echoes the currently presented request tag.
        let accepted = unsafe {
            ffi::im2p_put_output_write_response(self.simulator.handle.as_ptr(), request.tag)
        };
        self.simulator
            .require_ready("output_write_response", accepted)
    }

    pub fn poll_completed(&mut self) -> Option<StripeCompletion> {
        self.completed.pop_front()
    }

    pub fn finish(mut self) -> Result<WorkStats, Error> {
        for _ in 0..STRIPED_TIMEOUT_CYCLES {
            self.service_static_reads()?;
            self.drain_completion()?;
            // SAFETY: simulator handle remains valid.
            if unsafe { ffi::im2p_matmul_done(self.simulator.handle.as_ptr()) } != 0 {
                let completed = self.next_stripe_id as u64;
                // SAFETY: guarded by matmul_done.
                let accepted =
                    unsafe { ffi::im2p_acknowledge_matmul(self.simulator.handle.as_ptr()) };
                self.simulator
                    .require_ready("acknowledge_matmul", accepted)?;
                self.simulator.wait_idle()?;
                return Ok(self.simulator.work_stats(
                    self.counters_before,
                    self.scales_before,
                    self.start_cycle,
                    completed,
                ));
            }
            self.simulator.tick_raw();
        }
        Err(self
            .simulator
            .matrix_timeout("finish_striped_matmul", STRIPED_TIMEOUT_CYCLES))
    }
}

fn validate_descriptor(descriptor: &StripeWorkDesc<'_>) -> Result<(), Error> {
    if descriptor.rows == 0 || descriptor.columns == 0 || descriptor.reduction == 0 {
        return Err(Error::InvalidDimension);
    }
    let expected = descriptor
        .reduction
        .checked_mul(descriptor.columns)
        .ok_or(Error::InvalidDimension)?;
    if descriptor.weights.len() < expected {
        return Err(Error::InvalidBufferLength {
            name: "weights",
            expected,
            actual: descriptor.weights.len(),
        });
    }
    if descriptor.vector_op != super::VectorOp::Bypass && descriptor.scale_matrix.is_none() {
        return Err(Error::MissingScales {
            operation: descriptor.vector_op,
        });
    }
    Ok(())
}
