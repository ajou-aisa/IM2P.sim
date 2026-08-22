use std::collections::VecDeque;

use crate::{
    activation::activation_elements_to_address_bytes, ffi, ActivationStripe, ActivationValue,
    StripeCompletion, StripeLayout, StripeWorkDesc, WorkStats,
};

use super::{
    matmul::{ACTIVATION_BASE, OUTPUT_BASE, SCALE_BASE, WEIGHT_BASE},
    Error, Im2pSimulator, MemoryProvider,
};

mod provider;
mod start;
const STRIPED_TIMEOUT_CYCLES: u64 = 10_000_000;

#[cfg(test)]
static ACTIVATION_REQUEST_INTERCEPT: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);
#[cfg(test)]
static SUPPLY_REQUEST_ATTEMPTS: std::sync::atomic::AtomicUsize =
    std::sync::atomic::AtomicUsize::new(0);
#[cfg(test)]
static STAGE_REQUEST_ATTEMPTS: std::sync::atomic::AtomicUsize =
    std::sync::atomic::AtomicUsize::new(0);
#[cfg(test)]
static ACTIVATION_BOUNDARY_TEST_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

pub struct StripedMatmul<'a> {
    simulator: Im2pSimulator,
    descriptor: StripeWorkDesc<'a>,
    layout: StripeLayout,
    provider: Option<MemoryProvider>,
    published: VecDeque<PublishedActivationStripe>,
    completed: VecDeque<StripeCompletion>,
    outstanding_stripes: usize,
    next_stripe_id: u32,
    next_row: usize,
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
                activation_elements_to_address_bytes(activation_row_stride)
                    .map_err(|_| Error::InvalidActivationStride)?,
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
            self.service_provider_output()?;
            self.drain_completion()?;
            self.simulator.tick_staged_raw();
        }
        Ok(())
    }

    pub fn cycles(&self) -> u64 {
        self.simulator.cycles().saturating_sub(self.start_cycle)
    }

    pub fn pending_activation_row(&self) -> Option<usize> {
        self.activation_request()
            .ok()
            .flatten()
            .map(|(row, _, _)| row)
    }

    pub fn supply_activation_row(
        &mut self,
        row: usize,
        values: &[ActivationValue],
    ) -> Result<(), Error> {
        crate::activation_validation::validate_activation_row(values)?;
        #[cfg(test)]
        if ACTIVATION_REQUEST_INTERCEPT.load(std::sync::atomic::Ordering::SeqCst) {
            SUPPLY_REQUEST_ATTEMPTS.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            return Err(Error::ProviderFailure);
        }
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
                values[column..].as_ptr().cast::<i8>(),
                request.element_count,
            )
        };
        self.simulator
            .require_ready("activation_read_response", accepted)
    }
    pub(crate) fn stage_activation_row(
        &mut self,
        row: usize,
        values: &[ActivationValue],
    ) -> Result<(), Error> {
        crate::activation_validation::validate_activation_row(values)?;
        #[cfg(test)]
        if ACTIVATION_REQUEST_INTERCEPT.load(std::sync::atomic::Ordering::SeqCst) {
            STAGE_REQUEST_ATTEMPTS.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            return Err(Error::ProviderFailure);
        }
        let (expected_row, column, request) = self
            .activation_request()?
            .ok_or(Error::NoPendingActivation)?;
        if row != expected_row || values.len() < column + request.element_count as usize {
            return Err(Error::NoPendingActivation);
        }
        let accepted = unsafe {
            ffi::im2p_stage_activation_read_response(
                self.simulator.handle.as_ptr(),
                request.tag,
                values[column..].as_ptr().cast::<i8>(),
                request.element_count,
            )
        };
        self.simulator
            .require_staged("activation_read_response", accepted)
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

    pub(crate) fn stage_output_row(&mut self, row: usize) -> Result<(), Error> {
        let (expected_row, _, request, _) = self.output_request()?.ok_or(Error::NoPendingOutput)?;
        if row != expected_row {
            return Err(Error::NoPendingOutput);
        }
        let accepted = unsafe {
            ffi::im2p_stage_output_write_response(self.simulator.handle.as_ptr(), request.tag)
        };
        self.simulator
            .require_staged("output_write_response", accepted)
    }

    pub(crate) fn provider_handles_output(&self) -> bool {
        self.provider.is_some()
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
            self.service_provider_output()?;
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
                let stats = self.simulator.work_stats(completed);
                return Ok((stats, self.simulator));
            }
            self.simulator.tick_staged_raw();
        }
        Err(self
            .simulator
            .matrix_timeout("finish_striped_matmul", STRIPED_TIMEOUT_CYCLES))
    }
}

#[cfg(test)]
mod activation_boundary_tests {
    use std::collections::VecDeque;
    use std::mem::ManuallyDrop;
    use std::ptr::NonNull;
    use std::sync::atomic::Ordering;

    use super::{
        Error, Im2pSimulator, StripeLayout, StripeWorkDesc, StripedMatmul,
        ACTIVATION_BOUNDARY_TEST_LOCK, ACTIVATION_REQUEST_INTERCEPT, STAGE_REQUEST_ATTEMPTS,
        SUPPLY_REQUEST_ATTEMPTS,
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

    fn job<'a>(weights: &'a [i8]) -> ManuallyDrop<StripedMatmul<'a>> {
        ManuallyDrop::new(StripedMatmul {
            simulator: Im2pSimulator {
                handle: NonNull::<u8>::dangling().cast(),
                dim: 2,
            },
            descriptor: StripeWorkDesc {
                weights,
                scale_matrix: None,
                rows: 1,
                columns: 1,
                reduction: 2,
                vector_op: VectorOp::Bypass,
                work_context: 0,
            },
            layout: StripeLayout {
                weight_row_stride: 1,
                output_row_stride: 1,
                tile_i_rows: 1,
                tile_j_columns: 1,
            },
            provider: None,
            published: VecDeque::new(),
            completed: VecDeque::new(),
            outstanding_stripes: 0,
            next_stripe_id: 0,
            next_row: 0,
            start_cycle: 0,
        })
    }

    #[test]
    fn production_activation_boundary_supply_rejects_malformed_a4_before_request() {
        if ACTIVATION_BITS != 4 {
            return;
        }
        let _guard = ACTIVATION_BOUNDARY_TEST_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let values: [ActivationValue; 2] = [-9, 8];
        let weights = [1_i8, 1];
        let mut job = job(&weights);
        SUPPLY_REQUEST_ATTEMPTS.store(0, Ordering::SeqCst);
        ACTIVATION_REQUEST_INTERCEPT.store(true, Ordering::SeqCst);

        let result = job.supply_activation_row(0, &values);

        ACTIVATION_REQUEST_INTERCEPT.store(false, Ordering::SeqCst);
        assert_eq!(result, Err(Error::InvalidLayout));
        assert_eq!(SUPPLY_REQUEST_ATTEMPTS.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn production_activation_boundary_stage_rejects_malformed_a4_before_request() {
        if ACTIVATION_BITS != 4 {
            return;
        }
        let _guard = ACTIVATION_BOUNDARY_TEST_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let values: [ActivationValue; 2] = [-9, 8];
        let weights = [1_i8, 1];
        let mut job = job(&weights);
        STAGE_REQUEST_ATTEMPTS.store(0, Ordering::SeqCst);
        ACTIVATION_REQUEST_INTERCEPT.store(true, Ordering::SeqCst);

        let result = job.stage_activation_row(0, &values);

        ACTIVATION_REQUEST_INTERCEPT.store(false, Ordering::SeqCst);
        assert_eq!(result, Err(Error::InvalidLayout));
        assert_eq!(STAGE_REQUEST_ATTEMPTS.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn production_activation_boundary_supply_accepts_selected_extrema_before_request() {
        let _guard = ACTIVATION_BOUNDARY_TEST_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let values = selected_extrema();
        let weights = [1_i8, 1];
        let mut job = job(&weights);
        SUPPLY_REQUEST_ATTEMPTS.store(0, Ordering::SeqCst);
        ACTIVATION_REQUEST_INTERCEPT.store(true, Ordering::SeqCst);

        let result = job.supply_activation_row(0, &values);

        ACTIVATION_REQUEST_INTERCEPT.store(false, Ordering::SeqCst);
        assert_eq!(result, Err(Error::ProviderFailure));
        assert_eq!(SUPPLY_REQUEST_ATTEMPTS.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn production_activation_boundary_stage_accepts_selected_extrema_before_request() {
        let _guard = ACTIVATION_BOUNDARY_TEST_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let values = selected_extrema();
        let weights = [1_i8, 1];
        let mut job = job(&weights);
        STAGE_REQUEST_ATTEMPTS.store(0, Ordering::SeqCst);
        ACTIVATION_REQUEST_INTERCEPT.store(true, Ordering::SeqCst);

        let result = job.stage_activation_row(0, &values);

        ACTIVATION_REQUEST_INTERCEPT.store(false, Ordering::SeqCst);
        assert_eq!(result, Err(Error::ProviderFailure));
        assert_eq!(STAGE_REQUEST_ATTEMPTS.load(Ordering::SeqCst), 1);
    }
}
