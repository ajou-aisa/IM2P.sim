pub mod common;
use common::{golden_output, k_fragments, KBlockScaleMatrix, Shape};
use im2p_sim::profile::IM2P_ACCUMULATOR_BITS;
use im2p_sim::{
    parse_activation, Im2pSimulator, MatmulWork, MatrixView, MatrixViewMut, SimError, VectorOp,
    WeightValue,
};

#[test]
fn full_arithmetic_wraps_at_profile_width_before_final_output() -> Result<(), SimError> {
    // Given contributions that overflow selected signed width in opposite directions.
    let shape = Shape { m: 2, n: 1, k: 2 };
    let activations = [
        parse_activation(1).expect("one is valid at every configured width"),
        parse_activation(1).expect("one is valid at every configured width"),
        parse_activation(-2).expect("negative two is valid at every configured width"),
        parse_activation(-2).expect("negative two is valid at every configured width"),
    ];
    let weights: [WeightValue; 2] = [1, 1];
    let shift = i8::try_from(IM2P_ACCUMULATOR_BITS - 2).unwrap();
    let scales = KBlockScaleMatrix::from_fn(shape.k, 1, shape.n, |_, _| shift);
    let fragments = k_fragments(shape.k, scales.block_size, 16);

    // When the independent golden accumulates with selected signed width wrapping.
    let exact = golden_output(
        &activations,
        &weights,
        shape,
        0,
        shape.n,
        &fragments,
        Some(&scales),
        VectorOp::Shift,
    );

    // Then positive overflow wraps to the selected minimum and negative overflow wraps to zero.
    assert_eq!(
        exact,
        [
            if IM2P_ACCUMULATOR_BITS == 32 {
                i64::from(i32::MIN)
            } else {
                i64::MIN
            },
            0
        ]
    );
    let mut raw = [i32::MAX; 2];
    let work = MatmulWork {
        activations: MatrixView::new(&activations, shape.m, shape.k, shape.k)?,
        weights: MatrixView::new(&weights, shape.k, shape.n, shape.n)?,
        scales: Some(scales.view(0, shape.n, 0x57_52_41_50)),
        vector_op: VectorOp::Shift,
    };
    Im2pSimulator::new()?.execute_matmul(
        &work,
        &mut MatrixViewMut::new(&mut raw, shape.m, shape.n, shape.n)?,
    )?;
    assert_eq!(raw, [i32::MIN, 0]);
    Ok(())
}

#[test]
fn raw_full_output_preserves_profile_wrap_and_final_saturation() -> Result<(), SimError> {
    // Given two rows whose independent mathematical contributions exceed opposite i32 limits.
    let shape = Shape { m: 2, n: 1, k: 2 };
    let activations = [
        parse_activation(1).expect("one is valid at every configured width"),
        parse_activation(1).expect("one is valid at every configured width"),
        parse_activation(-2).expect("negative two is valid at every configured width"),
        parse_activation(-2).expect("negative two is valid at every configured width"),
    ];
    let weights: [WeightValue; 2] = [1, 1];
    let scales = KBlockScaleMatrix::from_fn(shape.k, 1, shape.n, |_, _| 30);
    let fragments = k_fragments(shape.k, scales.block_size, 16);
    let exact = golden_output(
        &activations,
        &weights,
        shape,
        0,
        shape.n,
        &fragments,
        Some(&scales),
        VectorOp::Shift,
    );
    assert_eq!(
        exact,
        if IM2P_ACCUMULATOR_BITS == 32 {
            [i64::from(i32::MIN), 0]
        } else {
            [2_147_483_648, -4_294_967_296]
        }
    );
    let mut raw = [0_i32; 2];
    let work = MatmulWork {
        activations: MatrixView::new(&activations, shape.m, shape.k, shape.k)?,
        weights: MatrixView::new(&weights, shape.k, shape.n, shape.n)?,
        scales: Some(scales.view(0, shape.n, 0x49_36_34)),
        vector_op: VectorOp::Shift,
    };

    // When FULL writes its final raw V2-layout destination.
    Im2pSimulator::new()?.execute_matmul(
        &work,
        &mut MatrixViewMut::new(&mut raw, shape.m, shape.n, shape.n)?,
    )?;

    // Then narrowing occurs once, at the final write, by saturation.
    assert_eq!(
        raw,
        if IM2P_ACCUMULATOR_BITS == 32 {
            [i32::MIN, 0]
        } else {
            [i32::MAX, i32::MIN]
        }
    );
    println!("exact_profile={exact:?} raw_v2={raw:?}");
    Ok(())
}

#[test]
fn shifts_handle_minimum_exponent_and_amounts_beyond_profile_width() -> Result<(), SimError> {
    let exponents = [i8::MIN, -64, -32, -31, 31, 32, 63, 64, i8::MAX];
    let shape = Shape {
        m: 2,
        n: exponents.len(),
        k: 1,
    };
    let activations = [parse_activation(1).unwrap(), parse_activation(-1).unwrap()];
    let weights = vec![im2p_sim::parse_weight(1).unwrap(); shape.n];
    let scales = KBlockScaleMatrix::from_fn(1, 1, shape.n, |_, column| exponents[column]);
    let exact = golden_output(
        &activations,
        &weights,
        shape,
        0,
        shape.n,
        &k_fragments(1, 1, im2p_sim::profile::IM2P_DIM),
        Some(&scales),
        VectorOp::Shift,
    );
    assert_eq!(exact[0], 0);
    assert_eq!(exact[shape.n], -1);
    assert_eq!(exact[7], 0);
    assert_eq!(exact[shape.n + 7], 0);
    let expected: Vec<i32> = exact
        .iter()
        .map(|value| {
            i32::try_from((*value).clamp(i64::from(i32::MIN), i64::from(i32::MAX))).unwrap()
        })
        .collect();
    let work = MatmulWork {
        activations: MatrixView::new(&activations, shape.m, shape.k, shape.k)?,
        weights: MatrixView::new(&weights, shape.k, shape.n, shape.n)?,
        scales: Some(scales.view(0, shape.n, 0x5348494654)),
        vector_op: VectorOp::Shift,
    };
    let mut actual = vec![0_i32; shape.m * shape.n];
    Im2pSimulator::new()?.execute_matmul(
        &work,
        &mut MatrixViewMut::new(&mut actual, shape.m, shape.n, shape.n)?,
    )?;
    assert_eq!(actual, expected);
    Ok(())
}
