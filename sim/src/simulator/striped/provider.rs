use crate::{ffi, StripeCompletion};

use super::{Error, StripedMatmul, ACTIVATION_BASE, OUTPUT_BASE, SCALE_BASE, WEIGHT_BASE};

impl StripedMatmul<'_> {
    pub(super) fn activation_request(
        &self,
    ) -> Result<Option<(usize, usize, ffi::ReadRequest)>, Error> {
        let mut request = ffi::ReadRequest::default();
        // SAFETY: request output and simulator handle remain valid.
        let status = unsafe {
            ffi::im2p_activation_read_request(self.simulator.handle.as_ptr(), &mut request)
        };
        if status == ffi::IM2P_REQUEST_ABSENT {
            return Ok(None);
        }
        if status != ffi::IM2P_REQUEST_PRESENT {
            return Err(Error::RtlNotReady {
                operation: "activation_read_request",
            });
        }
        let lookahead = (request.tag as u32) >= 0x8000_0000;
        let published = self
            .published
            .get(usize::from(lookahead))
            .or_else(|| self.published.front())
            .ok_or(Error::NoPendingActivation)?;
        let stripe_offset = published
            .stripe
            .row_begin
            .checked_mul(self.descriptor.reduction)
            .ok_or(Error::InvalidKRange)? as u64;
        let offset = request
            .address
            .checked_sub(ACTIVATION_BASE + stripe_offset)
            .ok_or(Error::InvalidKRange)? as usize;
        let local_row = offset / published.row_stride;
        let column = offset % published.row_stride;
        if local_row >= published.stripe.row_count
            || column + request.element_count as usize > self.descriptor.reduction
        {
            return Err(Error::InvalidKRange);
        }
        Ok(Some((
            published.stripe.row_begin + local_row,
            column,
            request,
        )))
    }

    pub(super) fn output_request(
        &self,
    ) -> Result<Option<(usize, usize, ffi::WriteRequest, Vec<i32>)>, Error> {
        let mut request = ffi::WriteRequest::default();
        let mut values = vec![0_i32; self.simulator.dim];
        // SAFETY: request and DIM-lane destination remain writable.
        let status = unsafe {
            ffi::im2p_output_write_request(
                self.simulator.handle.as_ptr(),
                &mut request,
                values.as_mut_ptr(),
            )
        };
        if status == ffi::IM2P_REQUEST_ABSENT {
            return Ok(None);
        }
        if status != ffi::IM2P_REQUEST_PRESENT {
            return Err(Error::RtlNotReady {
                operation: "output_write_request",
            });
        }
        let offset = request
            .address
            .checked_sub(OUTPUT_BASE)
            .ok_or(Error::InvalidKRange)? as usize;
        if offset % size_of::<i32>() != 0 {
            return Err(Error::InvalidKRange);
        }
        let element = offset / size_of::<i32>();
        Ok(Some((
            element / self.layout.output_row_stride,
            element % self.layout.output_row_stride,
            request,
            values,
        )))
    }

    pub(super) fn service_static_reads(&mut self) -> Result<(), Error> {
        self.service_weight()?;
        self.service_scale()
    }

    fn service_weight(&mut self) -> Result<(), Error> {
        let mut request = ffi::ReadRequest::default();
        // SAFETY: request output and simulator handle remain valid.
        let status =
            unsafe { ffi::im2p_weight_read_request(self.simulator.handle.as_ptr(), &mut request) };
        let Some((row, column, request)) =
            decode_request(status, request, WEIGHT_BASE, self.layout.weight_row_stride)?
        else {
            return Ok(());
        };
        let count = request.element_count as usize;
        if row >= self.descriptor.reduction || column + count > self.descriptor.columns {
            return Err(Error::InvalidKRange);
        }
        let start = row * self.layout.weight_row_stride + column;
        // SAFETY: descriptor validation and request bounds prove readable lanes.
        let accepted = unsafe {
            ffi::im2p_put_weight_read_response(
                self.simulator.handle.as_ptr(),
                request.tag,
                self.descriptor.weights[start..].as_ptr(),
                request.element_count,
            )
        };
        self.simulator
            .require_ready("weight_read_response", accepted)
    }

    fn service_scale(&mut self) -> Result<(), Error> {
        let mut request = ffi::ReadRequest::default();
        // SAFETY: request output and simulator handle remain valid.
        let status =
            unsafe { ffi::im2p_scale_read_request(self.simulator.handle.as_ptr(), &mut request) };
        if status == ffi::IM2P_REQUEST_ABSENT {
            return Ok(());
        }
        if status != ffi::IM2P_REQUEST_PRESENT {
            return Err(Error::RtlNotReady {
                operation: "scale_read_request",
            });
        }
        let view = self.descriptor.scale_matrix.ok_or(Error::MissingScales {
            operation: self.descriptor.vector_op,
        })?;
        let offset = request
            .address
            .checked_sub(SCALE_BASE)
            .ok_or(Error::InvalidKRange)? as usize;
        let block = offset / view.row_stride;
        let column = offset % view.row_stride;
        let count = request.element_count as usize;
        let column_end = column
            .checked_add(count)
            .ok_or(Error::InvalidScaleMatrixLayout)?;
        let start = block
            .checked_mul(view.row_stride)
            .and_then(|value| value.checked_add(view.column_offset))
            .and_then(|value| value.checked_add(column))
            .ok_or(Error::InvalidScaleMatrixLayout)?;
        let end = start
            .checked_add(count)
            .ok_or(Error::InvalidScaleMatrixLayout)?;
        if column_end > view.valid_columns || end > view.values.len() {
            return Err(Error::InvalidScaleMatrixLayout);
        }
        // SAFETY: validated view contains all requested lanes.
        let accepted = unsafe {
            ffi::im2p_put_scale_read_response(
                self.simulator.handle.as_ptr(),
                request.tag,
                view.values[start..end].as_ptr(),
                request.element_count,
            )
        };
        self.simulator
            .require_ready("scale_read_response", accepted)
    }

    pub(super) fn drain_completion(&mut self) -> Result<(), Error> {
        let mut completion = ffi::StripeCompletion::default();
        // SAFETY: completion output and simulator handle remain valid.
        let status =
            unsafe { ffi::im2p_stripe_completion(self.simulator.handle.as_ptr(), &mut completion) };
        if status == ffi::IM2P_REQUEST_ABSENT {
            return Ok(());
        }
        if status != ffi::IM2P_REQUEST_PRESENT {
            return Err(Error::RtlNotReady {
                operation: "stripe_completion",
            });
        }
        let published = self.published.pop_front().ok_or(Error::InvalidStripe)?;
        if published.stripe.stripe_id != completion.stripe_id
            || published.stripe.row_begin != completion.row_begin as usize
            || published.stripe.row_count != completion.row_count as usize
        {
            return Err(Error::InvalidStripe);
        }
        self.completed.push_back(StripeCompletion {
            stripe_id: completion.stripe_id,
            row_begin: completion.row_begin as usize,
            row_count: completion.row_count as usize,
            stripe_context: published.stripe.stripe_context,
        });
        self.outstanding_stripes -= 1;
        // SAFETY: completion getter established acknowledgement readiness.
        let accepted =
            unsafe { ffi::im2p_acknowledge_stripe_completion(self.simulator.handle.as_ptr()) };
        self.simulator
            .require_ready("acknowledge_stripe_completion", accepted)
    }
}

fn decode_request(
    status: i32,
    request: ffi::ReadRequest,
    base: u64,
    row_stride: usize,
) -> Result<Option<(usize, usize, ffi::ReadRequest)>, Error> {
    if status == ffi::IM2P_REQUEST_ABSENT {
        return Ok(None);
    }
    if status != ffi::IM2P_REQUEST_PRESENT {
        return Err(Error::RtlNotReady {
            operation: "matrix_read_request",
        });
    }
    let offset = request
        .address
        .checked_sub(base)
        .ok_or(Error::InvalidKRange)? as usize;
    Ok(Some((offset / row_stride, offset % row_stride, request)))
}
