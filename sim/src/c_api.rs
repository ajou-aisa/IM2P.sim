use std::cell::RefCell;
use std::ptr;
use std::rc::Rc;

use crate::{Im2pSimulator, WorkStats};

mod helpers;
mod stream;
mod types;

use helpers::{
    execute_full, execute_full_provider, parse_matmul_v2, status_for_error, write_extended_stats,
    write_stats,
};
use types::{MatmulDesc, MatmulDescV1, MatmulDescV2, WorkStatsC, WorkStatsExtendedC};

const PROVIDER_VERSION_1: u32 = 1;
const ABI_VERSION_2: u32 = 2;
const CONFIGURATION_MISMATCH: i32 = -7;

fn configured_dim() -> u32 {
    option_env!("IM2P_DIM")
        .and_then(|value| value.parse().ok())
        .unwrap_or(16)
}

pub(super) fn configuration_matches(
    abi_version: u32,
    activation_bits: u32,
    activation_storage_bytes: u32,
    dim: u32,
) -> bool {
    abi_version == ABI_VERSION_2
        && activation_bits == crate::ACTIVATION_BITS as u32
        && activation_storage_bytes == crate::ACTIVATION_STORAGE_BYTES as u32
        && dim == configured_dim()
}

#[no_mangle]
pub extern "C" fn im2p_sim_abi_version() -> u32 {
    ABI_VERSION_2
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
    descriptor: *const MatmulDesc,
    stats: *mut WorkStatsC,
) -> i32 {
    match execute_matmul_value(sim, descriptor) {
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
    descriptor: *const MatmulDesc,
    stats: *mut WorkStatsExtendedC,
) -> i32 {
    match execute_matmul_value(sim, descriptor) {
        Ok(value) => {
            write_extended_stats(stats, value);
            0
        }
        Err(status) => status,
    }
}

#[no_mangle]
pub unsafe extern "C" fn im2p_execute_matmul_ex(
    sim: *mut SimBox,
    descriptor: *const MatmulDescV1,
    stats: *mut WorkStatsC,
) -> i32 {
    if crate::ACTIVATION_BITS != 8 {
        return CONFIGURATION_MISMATCH;
    }
    let (Some(owner), Some(desc)) = (sim.as_mut(), descriptor.as_ref()) else {
        return -1;
    };
    if desc.version != PROVIDER_VERSION_1 {
        return -4;
    }
    let mut state = owner.simulator.borrow_mut();
    let Some(simulator) = state.as_mut() else {
        return -3;
    };
    match execute_full_provider(simulator, &desc.legacy, desc.provider.into()) {
        Ok(value) => {
            write_stats(stats, value);
            0
        }
        Err(error) => {
            simulator.reset();
            status_for_error(error)
        }
    }
}

#[no_mangle]
pub unsafe extern "C" fn im2p_execute_matmul_extended_ex(
    sim: *mut SimBox,
    descriptor: *const MatmulDescV1,
    stats: *mut WorkStatsExtendedC,
) -> i32 {
    if crate::ACTIVATION_BITS != 8 {
        return CONFIGURATION_MISMATCH;
    }
    let (Some(owner), Some(desc)) = (sim.as_mut(), descriptor.as_ref()) else {
        return -1;
    };
    if desc.version != PROVIDER_VERSION_1 {
        return -4;
    }
    let mut state = owner.simulator.borrow_mut();
    let Some(simulator) = state.as_mut() else {
        return -3;
    };
    match execute_full_provider(simulator, &desc.legacy, desc.provider.into()) {
        Ok(value) => {
            write_extended_stats(stats, value);
            0
        }
        Err(error) => {
            simulator.reset();
            status_for_error(error)
        }
    }
}

#[no_mangle]
pub unsafe extern "C" fn im2p_execute_matmul_v2(
    sim: *mut SimBox,
    descriptor: *const MatmulDescV2,
    stats: *mut WorkStatsC,
) -> i32 {
    match execute_matmul_v2_value(sim, descriptor) {
        Ok(value) => {
            write_stats(stats, value);
            0
        }
        Err(status) => status,
    }
}

#[no_mangle]
pub unsafe extern "C" fn im2p_execute_matmul_extended_v2(
    sim: *mut SimBox,
    descriptor: *const MatmulDescV2,
    stats: *mut WorkStatsExtendedC,
) -> i32 {
    match execute_matmul_v2_value(sim, descriptor) {
        Ok(value) => {
            write_extended_stats(stats, value);
            0
        }
        Err(status) => status,
    }
}

unsafe fn execute_matmul_v2_value(
    sim: *mut SimBox,
    descriptor: *const MatmulDescV2,
) -> Result<WorkStats, i32> {
    let Some(desc) = descriptor.as_ref() else {
        return Err(-1);
    };
    let (parsed, provider) = parse_matmul_v2(desc)?;
    let Some(owner) = sim.as_mut() else {
        return Err(-1);
    };
    let mut state = owner.simulator.borrow_mut();
    let Some(simulator) = state.as_mut() else {
        return Err(-3);
    };
    let result = match provider {
        Some(provider) => execute_full_provider(simulator, &parsed, provider),
        None => execute_full(simulator, &parsed),
    };
    result.map_err(|error| {
        simulator.reset();
        status_for_error(error)
    })
}

unsafe fn execute_matmul_value(
    sim: *mut SimBox,
    descriptor: *const MatmulDesc,
) -> Result<WorkStats, i32> {
    if crate::ACTIVATION_BITS != 8 {
        return Err(CONFIGURATION_MISMATCH);
    }
    let Some(desc) = descriptor.as_ref() else {
        return Err(-1);
    };
    let Some(owner) = sim.as_mut() else {
        return Err(-1);
    };
    let mut state = owner.simulator.borrow_mut();
    let Some(simulator) = state.as_mut() else {
        return Err(-3);
    };
    execute_full(simulator, desc).map_err(status_for_error)
}
