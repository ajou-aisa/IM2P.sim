use std::cell::RefCell;
use std::ptr;
use std::rc::Rc;

use crate::{Im2pSimulator, WorkStats};

mod helpers;
mod stream;
mod types;

use helpers::{execute_full, write_extended_stats, write_stats};
use types::{MatmulDesc, WorkStatsC, WorkStatsExtendedC};

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

unsafe fn execute_matmul_value(
    sim: *mut SimBox,
    descriptor: *const MatmulDesc,
) -> Result<WorkStats, i32> {
    let Some(owner) = sim.as_mut() else {
        return Err(-1);
    };
    let mut state = owner.simulator.borrow_mut();
    let Some(simulator) = state.as_mut() else {
        return Err(-3);
    };
    let Some(desc) = descriptor.as_ref() else {
        return Err(-1);
    };
    execute_full(simulator, desc).map_err(|_| -4)
}
