use crate::{
    activation::{activation_byte_indices, activation_elements_to_address_bytes},
    ffi,
    weight::weight_byte_indices,
    StripeCompletion, WeightValue,
};

use super::{Error, StripedMatmul, ACTIVATION_BASE, OUTPUT_BASE, SCALE_BASE, WEIGHT_BASE};

type OutputRequest = (usize, usize, ffi::WriteRequest, Vec<i64>);

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
            .and_then(|elements| activation_elements_to_address_bytes(elements).ok())
            .ok_or(Error::InvalidKRange)?;
        let stripe_base = ACTIVATION_BASE
            .checked_add(stripe_offset)
            .ok_or(Error::InvalidKRange)?;
        let offset = request
            .address
            .checked_sub(stripe_base)
            .and_then(|bytes| usize::try_from(bytes).ok())
            .ok_or(Error::InvalidKRange)?;
        let (local_row, column) = activation_byte_indices(offset, published.row_stride)
            .map_err(|_| Error::InvalidActivationStride)?;
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

    pub(super) fn output_request(&self) -> Result<Option<OutputRequest>, Error> {
        let mut request = ffi::WriteRequest::default();
        let mut values = vec![0_i64; self.simulator.dim];
        // SAFETY: request and DIM-lane destination remain writable.
        let status = unsafe {
            ffi::im2p_output_write_request_i64(
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
        if !offset.is_multiple_of(size_of::<i32>()) {
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
            decode_weight_request(status, request, WEIGHT_BASE, self.layout.weight_row_stride)?
        else {
            return Ok(());
        };
        let count = request.element_count as usize;
        if row >= self.descriptor.reduction || column + count > self.descriptor.columns {
            return Err(Error::InvalidKRange);
        }
        if let Some(provider) = self.provider {
            let mut values = vec![WeightValue::default(); count];
            provider.read_weight(row, column, &mut values)?;
            let accepted = unsafe {
                ffi::im2p_stage_weight_read_response(
                    self.simulator.handle.as_ptr(),
                    request.tag,
                    values.as_ptr().cast(),
                    request.element_count,
                )
            };
            return self
                .simulator
                .require_staged("weight_read_response", accepted);
        }
        let start = row * self.layout.weight_row_stride + column;
        // SAFETY: descriptor validation and request bounds prove readable lanes.
        let accepted = unsafe {
            ffi::im2p_stage_weight_read_response(
                self.simulator.handle.as_ptr(),
                request.tag,
                self.descriptor.weights[start..].as_ptr().cast(),
                request.element_count,
            )
        };
        self.simulator
            .require_staged("weight_read_response", accepted)
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
        if request.element_count as usize > self.simulator.dim {
            return Err(Error::InvalidScaleMatrixLayout);
        }
        if let Some(provider) = self.provider {
            let offset = request
                .address
                .checked_sub(SCALE_BASE)
                .ok_or(Error::InvalidScaleMatrixLayout)? as usize;
            let block = offset / self.descriptor.columns;
            let column = offset % self.descriptor.columns;
            let count = request.element_count as usize;
            if column + count > self.descriptor.columns {
                return Err(Error::InvalidScaleMatrixLayout);
            }
            let mut values = vec![0_i8; count];
            provider.read_scale(block, column, &mut values)?;
            let accepted = unsafe {
                ffi::im2p_stage_scale_read_response(
                    self.simulator.handle.as_ptr(),
                    request.tag,
                    values.as_ptr(),
                    request.element_count,
                )
            };
            return self
                .simulator
                .require_staged("scale_read_response", accepted);
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
            ffi::im2p_stage_scale_read_response(
                self.simulator.handle.as_ptr(),
                request.tag,
                view.values[start..end].as_ptr(),
                request.element_count,
            )
        };
        self.simulator
            .require_staged("scale_read_response", accepted)
    }

    pub(super) fn service_provider_output(&mut self) -> Result<(), Error> {
        let Some(provider) = self.provider else {
            return Ok(());
        };
        let mut request = ffi::WriteRequest::default();
        let mut values = vec![0_i64; self.simulator.dim];
        let status = unsafe {
            ffi::im2p_output_write_request_i64(
                self.simulator.handle.as_ptr(),
                &mut request,
                values.as_mut_ptr(),
            )
        };
        if status == ffi::IM2P_REQUEST_ABSENT {
            return Ok(());
        }
        if status != ffi::IM2P_REQUEST_PRESENT
            || request.element_count as usize > self.simulator.dim
        {
            return Err(Error::InvalidKRange);
        }
        let offset = request
            .address
            .checked_sub(OUTPUT_BASE)
            .ok_or(Error::InvalidKRange)? as usize;
        let row_stride = self
            .layout
            .output_row_stride
            .checked_mul(size_of::<i32>())
            .ok_or(Error::InvalidKRange)?;
        let (block, row, column) = decode_output_address(
            offset,
            self.descriptor.rows,
            row_stride,
            self.descriptor.vector_op == crate::VectorOp::External,
        )?;
        let count = request.element_count as usize;
        if row >= self.descriptor.rows || column + count > self.descriptor.columns {
            return Err(Error::InvalidKRange);
        }
        provider.write_output(block, row, column, &values[..count])?;
        let accepted = unsafe {
            ffi::im2p_stage_output_write_response(self.simulator.handle.as_ptr(), request.tag)
        };
        self.simulator
            .require_staged("output_write_response", accepted)
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
        let accepted = unsafe {
            ffi::im2p_stage_acknowledge_stripe_completion(self.simulator.handle.as_ptr())
        };
        self.simulator
            .require_staged("acknowledge_stripe_completion", accepted)
    }
}

fn decode_weight_request(
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
        .and_then(|bytes| usize::try_from(bytes).ok())
        .ok_or(Error::InvalidKRange)?;
    let (row, column) =
        weight_byte_indices(offset, row_stride).map_err(|_| Error::InvalidWeightStride)?;
    Ok(Some((row, column, request)))
}

fn decode_output_address(
    offset: usize,
    rows: usize,
    row_stride_bytes: usize,
    external: bool,
) -> Result<(usize, usize, usize), Error> {
    let block_stride = rows
        .checked_mul(row_stride_bytes)
        .filter(|stride| *stride != 0)
        .ok_or(Error::InvalidKRange)?;
    let (block, within) = if external {
        (offset / block_stride, offset % block_stride)
    } else {
        (0, offset)
    };
    if !within.is_multiple_of(size_of::<i32>()) {
        return Err(Error::InvalidKRange);
    }
    Ok((
        block,
        within / row_stride_bytes,
        (within % row_stride_bytes) / size_of::<i32>(),
    ))
}

#[cfg(test)]
mod tests {
    use super::{decode_output_address, Error};

    #[test]
    fn external_output_address_decodes_block_row_and_column() {
        let rows = 3;
        let row_stride_bytes = 5 * size_of::<i32>();
        let offset = 2 * rows * row_stride_bytes + row_stride_bytes + 3 * size_of::<i32>();
        assert_eq!(
            decode_output_address(offset, rows, row_stride_bytes, true),
            Ok((2, 1, 3))
        );
        assert_eq!(
            decode_output_address(offset, rows, row_stride_bytes, false),
            Ok((0, 7, 3))
        );
    }

    #[test]
    fn output_addressing_retains_four_byte_lanes_and_rejects_bad_offsets() {
        // Given the raw int32 layout's exact lane and row byte strides.
        let row_stride_bytes = 3 * size_of::<i32>();

        // When adjacent aligned lanes are decoded.
        let columns = [0, 4, 8].map(|offset| {
            decode_output_address(offset, 2, row_stride_bytes, false)
                .expect("aligned address")
                .2
        });

        // Then addresses advance by four bytes, while misalignment and overflow fail.
        assert_eq!(columns, [0, 1, 2]);
        println!("output_byte_offsets=[0, 4, 8] columns={columns:?}");
        assert_eq!(
            decode_output_address(2, 2, row_stride_bytes, false),
            Err(Error::InvalidKRange)
        );
        assert_eq!(
            decode_output_address(0, usize::MAX, row_stride_bytes, false),
            Err(Error::InvalidKRange)
        );
    }
}
