use std::cell::RefCell;
use std::mem::align_of;
use std::ptr;
use std::rc::Rc;

use crate::{
    activation_bytes_to_elements, weight_bytes_to_elements, ActivationValue, Im2pSimulator,
    WeightValue, WorkStats,
};

mod contract;
#[cfg(all(test, im2p_gemmini_integrated))]
mod geometry_tests;
mod helpers;
mod stream;
mod types;

use contract::{
    provider_requested, require_identity, require_output_domain, selected_weight_callback, Identity,
};
use helpers::{
    execute_full, execute_full_provider, status_for_error, validate_provider_rtl_fields,
    write_extended_stats, write_stats,
};
use types::{MatmulDesc, MatmulDescC, WorkStatsC, WorkStatsExtendedC};

const ABI_VERSION: u32 = 5;
const CONFIGURATION_MISMATCH: i32 = -7;

fn configured_dim() -> u32 {
    option_env!("IM2P_DIM")
        .and_then(|value| value.parse().ok())
        .unwrap_or(16)
}

#[no_mangle]
pub extern "C" fn im2p_sim_abi_version() -> u32 {
    ABI_VERSION
}

#[no_mangle]
pub extern "C" fn im2p_sim_activation_bits() -> u32 {
    crate::ACTIVATION_BITS as u32
}

#[no_mangle]
pub extern "C" fn im2p_sim_activation_storage_bytes() -> u32 {
    crate::ACTIVATION_STORAGE_BYTES as u32
}

#[no_mangle]
pub extern "C" fn im2p_sim_weight_bits() -> u32 {
    crate::WEIGHT_BITS as u32
}

#[no_mangle]
pub extern "C" fn im2p_sim_weight_storage_bytes() -> u32 {
    crate::WEIGHT_STORAGE_BYTES as u32
}

#[no_mangle]
pub extern "C" fn im2p_sim_dim() -> u32 {
    configured_dim()
}

pub struct SimBox {
    pub(super) simulator: Rc<RefCell<Option<Im2pSimulator>>>,
}

#[no_mangle]
pub extern "C" fn im2p_sim_create() -> *mut SimBox {
    Im2pSimulator::new()
        .map(|simulator| {
            Box::into_raw(Box::new(SimBox {
                simulator: Rc::new(RefCell::new(Some(simulator))),
            }))
        })
        .unwrap_or(ptr::null_mut())
}

#[no_mangle]
pub unsafe extern "C" fn im2p_sim_destroy(sim: *mut SimBox) {
    if !sim.is_null() {
        drop(Box::from_raw(sim));
    }
}

#[no_mangle]
pub unsafe extern "C" fn im2p_execute_matmul(
    sim: *mut SimBox,
    descriptor: *const MatmulDescC,
    stats: *mut WorkStatsC,
) -> i32 {
    match execute_matmul_value(sim, descriptor, None) {
        Ok(value) => {
            write_stats(stats, value);
            0
        }
        Err(status) => status,
    }
}

#[no_mangle]
pub unsafe extern "C" fn im2p_execute_matmul_extended(
    sim: *mut SimBox,
    descriptor: *const MatmulDescC,
    stats: *mut WorkStatsExtendedC,
) -> i32 {
    match execute_matmul_value(sim, descriptor, None) {
        Ok(value) => {
            write_extended_stats(stats, value);
            0
        }
        Err(status) => status,
    }
}

#[no_mangle]
pub unsafe extern "C" fn im2p_execute_matmul_planned(
    sim: *mut SimBox,
    descriptor: *const MatmulDescC,
    geometry: *const crate::production_geometry::ProductionGeometry,
    stats: *mut WorkStatsExtendedC,
) -> i32 {
    let Some(geometry) = geometry.as_ref() else {
        return -4;
    };
    match execute_matmul_value(sim, descriptor, Some(geometry)) {
        Ok(value) => {
            write_extended_stats(stats, value);
            0
        }
        Err(status) => status,
    }
}

unsafe fn execute_matmul_value(
    sim: *mut SimBox,
    descriptor: *const MatmulDescC,
    geometry: Option<&crate::production_geometry::ProductionGeometry>,
) -> Result<WorkStats, i32> {
    let Some(desc) = descriptor.as_ref() else {
        return Err(-4);
    };
    require_identity(Identity::from_matmul(desc))?;
    if let Some(geometry) = geometry {
        if !cfg!(im2p_gemmini_integrated) {
            return Err(CONFIGURATION_MISMATCH);
        }
        geometry.validate(
            desc.m,
            desc.n,
            desc.k,
            crate::production_geometry::GEOMETRY_FULL,
        )?;
    }
    require_output_domain(
        desc.vector_op,
        desc.output_domain,
        provider_requested(desc.provider),
    )?;
    if (!desc.scales.is_null() && !(desc.scales as usize).is_multiple_of(align_of::<u32>()))
        || desc.activations.is_null()
        || !(desc.activations as usize).is_multiple_of(align_of::<ActivationValue>())
        || (!desc.weights.is_null()
            && !(desc.weights as usize).is_multiple_of(align_of::<WeightValue>()))
    {
        return Err(-4);
    }
    let activation_row_stride =
        activation_bytes_to_elements(desc.activation_row_stride_bytes).map_err(|_| -4)?;
    let weight_row_stride =
        weight_bytes_to_elements(desc.weight_row_stride_bytes).map_err(|_| -4)?;
    let parsed = MatmulDesc {
        activations: desc.activations.cast(),
        weights: desc.weights.cast(),
        scales: desc.scales,
        output: desc.output,
        m: desc.m,
        n: desc.n,
        k: desc.k,
        activation_row_stride,
        weight_row_stride,
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
    let any_provider = provider_requested(desc.provider);
    if any_provider
        && (!selected_weight_callback(desc.provider) || desc.provider.write_output.is_none())
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
        execute_full_provider(simulator, &parsed, desc.provider.selected(), geometry)
    } else {
        execute_full(simulator, &parsed, geometry)
    };
    result.map_err(|error| {
        simulator.reset();
        status_for_error(error)
    })
}
