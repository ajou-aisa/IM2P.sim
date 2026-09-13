use super::{
    types::{MatmulDescC, ProviderC, StripeWorkDescC},
    ABI_VERSION, CONFIGURATION_MISMATCH,
};

#[derive(Clone, Copy)]
pub(super) struct Identity {
    abi_version: u32,
    activation_bits: u32,
    activation_storage_bytes: u32,
    weight_bits: u32,
    weight_storage_bytes: u32,
    dim: u32,
}

impl Identity {
    pub(super) fn from_matmul(desc: &MatmulDescC) -> Self {
        Self {
            abi_version: desc.abi_version,
            activation_bits: desc.activation_bits,
            activation_storage_bytes: desc.activation_storage_bytes,
            weight_bits: desc.weight_bits,
            weight_storage_bytes: desc.weight_storage_bytes,
            dim: desc.dim,
        }
    }

    pub(super) fn from_striped(desc: &StripeWorkDescC) -> Self {
        Self {
            abi_version: desc.abi_version,
            activation_bits: desc.activation_bits,
            activation_storage_bytes: desc.activation_storage_bytes,
            weight_bits: desc.weight_bits,
            weight_storage_bytes: desc.weight_storage_bytes,
            dim: desc.dim,
        }
    }

    pub(super) fn from_fields(
        abi_version: u32,
        activation_bits: u32,
        activation_storage_bytes: u32,
        weight_bits: u32,
        weight_storage_bytes: u32,
        dim: u32,
    ) -> Self {
        Self {
            abi_version,
            activation_bits,
            activation_storage_bytes,
            weight_bits,
            weight_storage_bytes,
            dim,
        }
    }
}

pub(super) fn require_identity(identity: Identity) -> Result<(), i32> {
    if identity.abi_version == ABI_VERSION
        && identity.activation_bits == crate::ACTIVATION_BITS as u32
        && identity.activation_storage_bytes == crate::ACTIVATION_STORAGE_BYTES as u32
        && identity.weight_bits == crate::WEIGHT_BITS as u32
        && identity.weight_storage_bytes == crate::WEIGHT_STORAGE_BYTES as u32
        && identity.dim == super::configured_dim()
    {
        Ok(())
    } else {
        Err(CONFIGURATION_MISMATCH)
    }
}

pub(super) fn provider_requested(provider: ProviderC) -> bool {
    provider.read_weight_i8.is_some()
        || provider.read_weight_i16.is_some()
        || provider.read_scale.is_some()
        || provider.write_output.is_some()
}

pub(super) fn selected_weight_callback(provider: ProviderC) -> bool {
    if crate::WEIGHT_BITS == 16 {
        provider.read_weight_i16.is_some() && provider.read_weight_i8.is_none()
    } else {
        provider.read_weight_i8.is_some() && provider.read_weight_i16.is_none()
    }
}

/// The callback meaning is explicit even though RTL derives it from vector_op.
pub(super) fn require_output_domain(
    vector_op: u8,
    output_domain: u8,
    provider: bool,
) -> Result<(), i32> {
    let op = super::helpers::vector_op(vector_op).ok_or(-4)?;
    if output_domain != op.output_domain() as u8 {
        return Err(-4);
    }
    if !provider {
        crate::simulator::validation::reject_scu_i32_output(op).map_err(|_| -4)?;
    }
    Ok(())
}

#[cfg(test)]
mod scu_contract_tests {
    use super::{require_identity, require_output_domain, Identity};

    #[test]
    fn stale_abi4_is_rejected_with_matching_profile() {
        let identity = Identity::from_fields(
            4,
            crate::ACTIVATION_BITS as u32,
            crate::ACTIVATION_STORAGE_BYTES as u32,
            crate::WEIGHT_BITS as u32,
            crate::WEIGHT_STORAGE_BYTES as u32,
            super::super::configured_dim(),
        );
        assert_eq!(require_identity(identity), Err(-7));
    }

    #[test]
    fn output_domain_matches_operation_and_exact_a16_storage() {
        for (op, domain) in [(0, 0), (1, 0), (2, 0), (3, 1), (4, 2), (5, 2)] {
            assert_eq!(require_output_domain(op, domain, true), Ok(()));
            for wrong in [0, 1, 2, 255] {
                if wrong != domain {
                    assert_eq!(require_output_domain(op, wrong, true), Err(-4));
                }
            }
            let expected = if op >= 4 && crate::profile::IM2P_ACCUMULATOR_BITS == 64 {
                Err(-4)
            } else {
                Ok(())
            };
            assert_eq!(require_output_domain(op, domain, false), expected);
        }
        assert_eq!(require_output_domain(6, 2, true), Err(-4));
    }
}
