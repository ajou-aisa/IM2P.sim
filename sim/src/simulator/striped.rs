use std::collections::VecDeque;

use crate::{ffi, ActivationStripe, StripeCompletion, StripeLayout, StripeWorkDesc, WorkStats};

use super::{
    matmul::{ACTIVATION_BASE, OUTPUT_BASE, SCALE_BASE, WEIGHT_BASE},
    Error, Im2pSimulator,
};

mod provider;
mod start;
const STRIPED_TIMEOUT_CYCLES: u64 = 10_000_000;

pub struct StripedMatmul<'a> {
    simulator: Im2pSimulator,
    descriptor: StripeWorkDesc<'a>,
    layout: StripeLayout,
    published: VecDeque<PublishedActivationStripe>,
    completed: VecDeque<StripeCompletion>,
    outstanding_stripes: usize,
    next_stripe_id: u32,
    next_row: usize,
    counters_before: ffi::MatrixCounters,
    scales_before: ffi::ScaleCounters,
    start_cycle: u64,
}

#[derive(Debug, Clone, Copy)]
struct PublishedActivationStripe {
    stripe: ActivationStripe,
    row_stride: usize,
}
impl StripedMatmul<'_> {
    pub fn publish_stripe(&mut self, stripe: ActivationStripe) -> Result<(), Error> {
        self.publish_stripe_layout(stripe, self.descriptor.reduction)
    }

    pub fn publish_stripe_layout(
        &mut self,
        stripe: ActivationStripe,
        activation_row_stride: usize,
    ) -> Result<(), Error> {
        if self.next_row == self.descriptor.rows {
            return Err(Error::LateStripe);
        }
        if stripe.stripe_id < self.next_stripe_id || stripe.row_begin < self.next_row {
            return Err(Error::DuplicateStripe);
        }
        if stripe.stripe_id != self.next_stripe_id
            || stripe.row_begin != self.next_row
            || stripe.row_count == 0
            || stripe
                .row_begin
                .checked_add(stripe.row_count)
                .is_none_or(|end| end > self.descriptor.rows)
        {
            return Err(Error::InvalidStripe);
        }
        if activation_row_stride < self.descriptor.reduction {
            return Err(Error::InvalidActivationStride);
        }
        // SAFETY: scalar metadata is copied synchronously.
        let accepted = unsafe {
            ffi::im2p_publish_activation_stripe(
                self.simulator.handle.as_ptr(),
                stripe.row_begin as u32,
                stripe.row_count as u32,
                activation_row_stride as u64,
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
        self.published.push_back(PublishedActivationStripe {
            stripe,
            row_stride: activation_row_stride,
        });
        Ok(())
    }

    pub fn publish_stripe_layout_at_cycle(
        &mut self,
        stripe: ActivationStripe,
        activation_row_stride: usize,
        target_cycle: u64,
    ) -> Result<(), Error> {
        let elapsed = self.simulator.cycles().saturating_sub(self.start_cycle);
        if elapsed > target_cycle {
            return Err(Error::RtlNotReady {
                operation: "publish_stripe_at_cycle",
            });
        }
        for _ in elapsed..target_cycle {
            self.simulator.tick_raw();
        }
        self.publish_stripe_layout(stripe, activation_row_stride)
    }
    pub fn npu_ready(&self) -> bool {
        // SAFETY: simulator handle remains valid while the job owns it.
        unsafe { ffi::im2p_activation_stripe_ready(self.simulator.handle.as_ptr()) != 0 }
    }
    pub fn host_available(&self) -> bool {
        self.outstanding_stripes != 0
    }

    pub fn prepared_lookahead_stripe_id(&self) -> Option<u32> {
        let debug = self.simulator.matrix_debug();
        (debug.lookahead_prepared != 0).then_some(debug.lookahead_stripe_id)
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

    pub fn finish(self) -> Result<WorkStats, Error> {
        self.finish_recover().map(|(stats, _)| stats)
    }

    pub(crate) fn recover_unfinished(mut self) -> Im2pSimulator {
        self.simulator.reset();
        self.simulator
    }

    pub(crate) fn finish_recover(mut self) -> Result<(WorkStats, Im2pSimulator), Error> {
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
                let stats = self.simulator.work_stats(
                    self.counters_before,
                    self.scales_before,
                    self.start_cycle,
                    completed,
                );
                return Ok((stats, self.simulator));
            }
            self.simulator.tick_raw();
        }
        Err(self
            .simulator
            .matrix_timeout("finish_striped_matmul", STRIPED_TIMEOUT_CYCLES))
    }
}
