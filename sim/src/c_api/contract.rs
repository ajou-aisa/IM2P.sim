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
