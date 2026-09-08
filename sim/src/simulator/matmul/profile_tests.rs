use std::{ffi::c_void, slice};

use super::super::{MemoryProvider, ReadWeightProvider};
use crate::{Im2pSimulator, MatmulLayout, MatrixView, SimError, VectorOp};

struct ProviderState {
    weight: i16,
    scale: i8,
    output: Vec<(usize, i64)>,
}

unsafe extern "C" fn weight_i8(
    context: *mut c_void,
    _row: usize,
    _column: usize,
    count: usize,
    values: *mut i8,
) -> i32 {
    // SAFETY: execute_matmul_provider keeps this state and its DIM-lane buffer live.
    let state = unsafe { &*context.cast::<ProviderState>() };
    let Ok(value) = i8::try_from(state.weight) else {
        return -1;
    };
    unsafe { slice::from_raw_parts_mut(values, count) }.fill(value);
    0
}

unsafe extern "C" fn weight_i16(
    context: *mut c_void,
    _row: usize,
    _column: usize,
    count: usize,
    values: *mut i16,
) -> i32 {
    // SAFETY: selected I16 callback receives a live aligned DIM-lane i16 buffer.
    let state = unsafe { &*context.cast::<ProviderState>() };
    unsafe { slice::from_raw_parts_mut(values, count) }.fill(state.weight);
    0
}

unsafe extern "C" fn scale(
    context: *mut c_void,
    _row: usize,
    _column: usize,
    count: usize,
    values: *mut i8,
) -> i32 {
    // SAFETY: provider owns context; bridge lends count writable scale lanes.
    let state = unsafe { &*context.cast::<ProviderState>() };
    unsafe { slice::from_raw_parts_mut(values, count) }.fill(state.scale);
    0
}

unsafe extern "C" fn output(
    context: *mut c_void,
    block: usize,
    row: usize,
    column: usize,
    count: usize,
    values: *const i64,
) -> i32 {
    if row != 0 || column != 0 || count != 1 {
        return -1;
    }
    // SAFETY: callback runs serially, context lives across execute, one lane is readable.
    let state = unsafe { &mut *context.cast::<ProviderState>() };
    state.output.push((block, unsafe { *values }));
    0
}

struct Case {
    reduction: usize,
    block_size: usize,
    input: i16,
    scale: i8,
    operation: VectorOp,
}

fn run(case: Case) -> Result<Vec<(usize, i64)>, SimError> {
    let _guard = super::PROVIDER_BOUNDARY_TEST_LOCK.lock().unwrap();
    let mut state = ProviderState {
        weight: case.input,
        scale: case.scale,
        output: Vec::new(),
    };
    let activation = crate::parse_activation(i32::from(case.input)).unwrap();
    let activations = vec![activation; case.reduction];
    let provider = MemoryProvider {
        context: std::ptr::from_mut(&mut state).cast(),
        read_weight: Some(if crate::WEIGHT_BITS == 16 {
            ReadWeightProvider::I16(weight_i16)
        } else {
            ReadWeightProvider::I8(weight_i8)
        }),
        read_scale: Some(scale),
        write_output: Some(output),
    };
    Im2pSimulator::new()?.execute_matmul_provider(
        MatrixView::new(&activations, 1, case.reduction, case.reduction)?,
        1,
        1,
        case.reduction,
        1,
        1,
        case.block_size,
        case.operation,
        0x4e554d,
        MatmulLayout {
            tile_i_rows: 1,
            tile_j_columns: 1,
        },
        provider,
    )?;
    Ok(state.output)
}

#[test]
fn deliberate_a8_full_k_overflow_wraps_to_int32_min() -> Result<(), SimError> {
    if crate::ACTIVATION_BITS != 8 {
        return Ok(());
    }
    let values = run(Case {
        reduction: 8192,
        block_size: 32,
        input: -128,
        scale: 16,
        operation: VectorOp::Multiply,
    })?;
    assert_eq!(values, [(0, i64::from(i32::MIN))]);
    assert_eq!(i128::from(-128).pow(2) * 8192 * 16, 1_i128 << 31);
    Ok(())
}

#[test]
fn external_block_reset_preserves_raw_blocks_before_reconstruction() -> Result<(), SimError> {
    if crate::ACTIVATION_BITS != 8 {
        return Ok(());
    }
    let values = run(Case {
        reduction: 8192,
        block_size: 32,
        input: -128,
        scale: 16,
        operation: VectorOp::External,
    })?;
    assert_eq!(values.len(), 256);
    for (index, &(block, value)) in values.iter().enumerate() {
        assert_eq!((block, value), (index, 524_288));
    }
    let reconstructed: i128 = values
        .iter()
        .map(|(_, value)| i128::from(*value) * 16)
        .sum();
    assert_eq!(reconstructed, 1_i128 << 31);
    assert!(reconstructed > i128::from(i32::MAX));
    Ok(())
}

#[test]
fn a16_products_accumulate_beyond_int32_without_truncation() -> Result<(), SimError> {
    if crate::ACTIVATION_BITS != 16 {
        return Ok(());
    }
    let values = run(Case {
        reduction: 3,
        block_size: 3,
        input: -32768,
        scale: 1,
        operation: VectorOp::Bypass,
    })?;
    assert_eq!(values, [(0, 3_221_225_472)]);
    Ok(())
}

#[test]
fn oversized_host_addresses_are_rejected_before_start_edge() -> Result<(), SimError> {
    let simulator = Im2pSimulator::new()?;
    let valid = crate::ffi::MatmulDescriptor {
        row_count: 2,
        column_count: 1,
        reduction_count: 2,
        tile_i_rows: 1,
        tile_j_columns: 1,
        activation_row_stride: 2 * u64::try_from(crate::ACTIVATION_STORAGE_BYTES).unwrap(),
        weight_row_stride: u64::try_from(crate::WEIGHT_STORAGE_BYTES).unwrap(),
        output_row_stride: 4,
        ..Default::default()
    };
    let before = simulator.cycles();
    for descriptor in [
        crate::ffi::MatmulDescriptor {
            activation_base: u64::MAX,
            ..valid
        },
        crate::ffi::MatmulDescriptor {
            weight_base: u64::MAX,
            ..valid
        },
        crate::ffi::MatmulDescriptor {
            output_base: u64::MAX - 2,
            ..valid
        },
        crate::ffi::MatmulDescriptor {
            activation_row_stride: u64::MAX,
            ..valid
        },
        crate::ffi::MatmulDescriptor {
            k_origin: u32::MAX,
            ..valid
        },
        crate::ffi::MatmulDescriptor {
            vector_op: 3,
            reduction_count: 1,
            scale_total_k: 1,
            scale_block_size: 1,
            scale_row_stride: 1,
            output_row_stride: 1_u64 << 63,
            ..valid
        },
    ] {
        // SAFETY: simulator and stack descriptor remain live throughout the call.
        assert_eq!(
            unsafe { crate::ffi::im2p_start_matmul(simulator.handle.as_ptr(), &descriptor) },
            -1
        );
        assert_eq!(simulator.cycles(), before);
    }
    Ok(())
}

#[test]
fn negative_bridge_status_does_not_advance_rust_clock() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let before = simulator.cycles();
    assert_eq!(
        simulator.require_ready("rejected", -1),
        Err(SimError::RtlNotReady {
            operation: "rejected"
        })
    );
    assert_eq!(simulator.cycles(), before);
    Ok(())
}
