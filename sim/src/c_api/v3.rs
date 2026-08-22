use std::{mem::align_of, ptr};

use crate::{activation_bytes_to_elements, ActivationValue, WorkStats};

use super::{
    helpers::{
        execute_full, execute_full_provider, status_for_error, validate_provider_rtl_fields,
        write_extended_stats, write_stats,
    },
    stream::{begin_striped_matmul_value, publish_versioned_stripe, VersionedStripe},
    types::{
        ActivationStripeV3, MatmulDesc, MatmulDescV3, StreamBox, StripeWorkDescC, StripeWorkDescV3,
        WorkStatsC, WorkStatsExtendedC,
    },
    SimBox, ABI_VERSION_3, CONFIGURATION_MISMATCH,
};

#[derive(Clone, Copy)]
struct DescriptorIdentity {
    abi_version: u32,
    activation_bits: u32,
    activation_storage_bytes: u32,
    dim: u32,
}

impl DescriptorIdentity {
    fn matches_runtime(self) -> bool {
        self.abi_version == ABI_VERSION_3
            && self.activation_bits == crate::ACTIVATION_BITS as u32
            && self.activation_storage_bytes == crate::ACTIVATION_STORAGE_BYTES as u32
            && self.dim == super::configured_dim()
    }
}

#[no_mangle]
pub unsafe extern "C" fn im2p_execute_matmul_v3(
    sim: *mut SimBox,
    descriptor: *const MatmulDescV3,
    stats: *mut WorkStatsC,
) -> i32 {
    match execute_matmul_v3_value(sim, descriptor) {
        Ok(value) => {
            write_stats(stats, value);
            0
        }
        Err(status) => status,
    }
}

#[no_mangle]
pub unsafe extern "C" fn im2p_execute_matmul_extended_v3(
    sim: *mut SimBox,
    descriptor: *const MatmulDescV3,
    stats: *mut WorkStatsExtendedC,
) -> i32 {
    match execute_matmul_v3_value(sim, descriptor) {
        Ok(value) => {
            write_extended_stats(stats, value);
            0
        }
        Err(status) => status,
    }
}

unsafe fn execute_matmul_v3_value(
    sim: *mut SimBox,
    descriptor: *const MatmulDescV3,
) -> Result<WorkStats, i32> {
    let Some(desc) = descriptor.as_ref() else {
        return Err(-4);
    };
    require_identity(identity_from_matmul(desc))?;
    if desc.activations.is_null()
        || !(desc.activations as usize).is_multiple_of(align_of::<ActivationValue>())
    {
        return Err(-4);
    }
    let activation_row_stride =
        activation_bytes_to_elements(desc.activation_row_stride_bytes).map_err(|_| -4)?;
    let parsed = MatmulDesc {
        activations: desc.activations.cast(),
        weights: desc.weights,
        scales: desc.scales,
        output: desc.output,
        m: desc.m,
        n: desc.n,
        k: desc.k,
        activation_row_stride,
        weight_row_stride: desc.weight_row_stride,
        output_row_stride: desc.output_row_stride,
        tile_i_rows: desc.tile_i_rows,
        tile_j_columns: desc.tile_j_columns,
        block_size: desc.block_size,
        scale_total_k: desc.scale_total_k,
        scale_row_stride: desc.scale_row_stride,
        scale_column_offset: desc.scale_column_offset,
        scale_valid_columns: desc.scale_valid_columns,
        scale_values_len: desc.scale_values_len,
        vector_op: desc.vector_op,
        work_context: desc.work_context,
    };
    let any_provider = desc.provider.read_weight.is_some()
        || desc.provider.read_scale.is_some()
        || desc.provider.write_output.is_some();
    if any_provider && (desc.provider.read_weight.is_none() || desc.provider.write_output.is_none())
    {
        return Err(-4);
    }
    if !any_provider && (parsed.weights.is_null() || parsed.output.is_null()) {
        return Err(-1);
    }
    if any_provider {
        validate_provider_rtl_fields(&parsed)?;
    }
    let Some(owner) = sim.as_mut() else {
        return Err(-1);
    };
    let mut state = owner.simulator.borrow_mut();
    let Some(simulator) = state.as_mut() else {
        return Err(-3);
    };
    let result = if any_provider {
        execute_full_provider(simulator, &parsed, desc.provider.into())
    } else {
        execute_full(simulator, &parsed)
    };
    result.map_err(|error| {
        simulator.reset();
        status_for_error(error)
    })
}

#[no_mangle]
pub unsafe extern "C" fn im2p_begin_striped_matmul_v3(
    sim: *mut SimBox,
    descriptor: *const StripeWorkDescV3,
    output: *mut *mut StreamBox,
) -> i32 {
    let Some(output_ref) = output.as_mut() else {
        return -1;
    };
    *output_ref = ptr::null_mut();
    let Some(desc) = descriptor.as_ref() else {
        return -4;
    };
    if let Err(status) = require_identity(identity_from_striped(desc)) {
        return status;
    }
    let any_provider = desc.provider.read_weight.is_some()
        || desc.provider.read_scale.is_some()
        || desc.provider.write_output.is_some();
    if any_provider && (desc.provider.read_weight.is_none() || desc.provider.write_output.is_none())
    {
        return -4;
    }
    let parsed = StripeWorkDescC {
        weights: desc.weights,
        scales: desc.scales,
        output: desc.output,
        m: desc.m,
        n: desc.n,
        k: desc.k,
        weight_row_stride: desc.weight_row_stride,
        output_row_stride: desc.output_row_stride,
        tile_i_rows: desc.tile_i_rows,
        tile_j_columns: desc.tile_j_columns,
        block_size: desc.block_size,
        scale_total_k: desc.scale_total_k,
        scale_row_stride: desc.scale_row_stride,
        scale_column_offset: desc.scale_column_offset,
        scale_valid_columns: desc.scale_valid_columns,
        scale_values_len: desc.scale_values_len,
        stripe_count: desc.stripe_count,
        vector_op: desc.vector_op,
        work_context: desc.work_context,
    };
    begin_striped_matmul_value(
        sim,
        &parsed,
        output,
        any_provider.then(|| desc.provider.into()),
        ABI_VERSION_3,
    )
}

#[no_mangle]
pub unsafe extern "C" fn im2p_publish_stripe_v3(
    stream: *mut StreamBox,
    stripe: *const ActivationStripeV3,
) -> i32 {
    let Some(stripe) = stripe.as_ref() else {
        return -4;
    };
    publish_versioned_stripe(
        stream,
        VersionedStripe {
            identity: (
                stripe.abi_version,
                stripe.activation_bits,
                stripe.activation_storage_bytes,
                stripe.dim,
            ),
            expected_abi: ABI_VERSION_3,
            stripe_id: stripe.stripe_id,
            row_begin: stripe.i_start,
            row_count: stripe.rows,
            activations: stripe.activations,
            row_stride_bytes: stripe.activation_row_stride_bytes,
            context: stripe.context,
        },
    )
}

fn identity_from_matmul(desc: &MatmulDescV3) -> DescriptorIdentity {
    DescriptorIdentity {
        abi_version: desc.abi_version,
        activation_bits: desc.activation_bits,
        activation_storage_bytes: desc.activation_storage_bytes,
        dim: desc.dim,
    }
}

fn identity_from_striped(desc: &StripeWorkDescV3) -> DescriptorIdentity {
    DescriptorIdentity {
        abi_version: desc.abi_version,
        activation_bits: desc.activation_bits,
        activation_storage_bytes: desc.activation_storage_bytes,
        dim: desc.dim,
    }
}

fn require_identity(identity: DescriptorIdentity) -> Result<(), i32> {
    if identity.matches_runtime() {
        Ok(())
    } else {
        Err(CONFIGURATION_MISMATCH)
    }
}

#[cfg(test)]
mod tests {
    use super::{require_identity, DescriptorIdentity};
    use crate::c_api::{configured_dim, ABI_VERSION_2, ABI_VERSION_3, CONFIGURATION_MISMATCH};

    fn runtime_identity(abi_version: u32) -> DescriptorIdentity {
        DescriptorIdentity {
            abi_version,
            activation_bits: crate::ACTIVATION_BITS as u32,
            activation_storage_bytes: crate::ACTIVATION_STORAGE_BYTES as u32,
            dim: configured_dim(),
        }
    }

    #[test]
    fn v3_identity_rejects_v2_descriptor_version() {
        // Given a runtime-compatible descriptor carrying the V2 discriminator.
        let mixed = runtime_identity(ABI_VERSION_2);

        // When the V3 boundary parses its identity.
        let status = require_identity(mixed);

        // Then dispatch rejects the mixed version with the typed status.
        assert_eq!(status, Err(CONFIGURATION_MISMATCH));
    }

    #[test]
    fn v3_identity_rejects_foreign_runtime_layout() {
        // Given a V3 descriptor for a different generated DIM.
        let mut mixed = runtime_identity(ABI_VERSION_3);
        mixed.dim = if configured_dim() == 16 { 32 } else { 16 };

        // When the V3 boundary parses its identity.
        let status = require_identity(mixed);

        // Then dispatch rejects the mixed layout with the typed status.
        assert_eq!(status, Err(CONFIGURATION_MISMATCH));
    }
}
