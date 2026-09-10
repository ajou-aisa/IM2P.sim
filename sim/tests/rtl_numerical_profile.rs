pub mod common;
use common::{golden_output, k_fragments, KBlockScaleMatrix, Lcg, Shape};
use im2p_sim::profile::IM2P_ACCUMULATOR_BITS;
use im2p_sim::{
    parse_activation, Im2pSimulator, MatmulWork, MatrixView, MatrixViewMut, SimError, VectorOp,
    WeightValue,
};

#[test]
fn a8_local_partial_extrema_padding_and_fragments_are_exact() -> Result<(), SimError> {
    if im2p_sim::ACTIVATION_BITS != 8 || im2p_sim::profile::IM2P_DIM != 16 {
        return Ok(());
    }
    let alternating: Vec<i32> = (0..16)
        .map(|index| if index % 2 == 0 { -128 } else { 127 })
        .collect();
    let mut cases = vec![
        (
            "min_min".to_owned(),
            vec![-128; 16],
            vec![-128; 16],
            262_144,
        ),
        (
            "min_max".to_owned(),
            vec![-128; 16],
            vec![127; 16],
            -260_096,
        ),
        ("max_max".to_owned(), vec![127; 16], vec![127; 16], 258_064),
        (
            "alternating".to_owned(),
            alternating.clone(),
            alternating,
            260_104,
        ),
        (
            "padding_k7".to_owned(),
            vec![-128; 7],
            vec![-128; 7],
            114_688,
        ),
        // Both totals exceed signed INT20: accumulation must occur after widening.
        (
            "fragments_k32".to_owned(),
            vec![-128; 32],
            vec![-128; 32],
            524_288,
        ),
        (
            "fragments_k33".to_owned(),
            vec![-128; 33],
            vec![-128; 33],
            540_672,
        ),
        (
            "negative_k33".to_owned(),
            vec![-128; 33],
            vec![127; 33],
            -536_448,
        ),
    ];
    let mut random = Lcg::new(0x20_08_16);
    for index in 0..16 {
        let k = [7, 16, 32, 33][index % 4];
        let activations: Vec<i32> = (0..k)
            .map(|_| i32::from(random.signed(i8::MIN, i8::MAX)))
            .collect();
        let weights: Vec<i32> = (0..k)
            .map(|_| i32::from(random.signed(i8::MIN, i8::MAX)))
            .collect();
        let expected = activations.iter().zip(&weights).map(|(a, w)| a * w).sum();
        cases.push((format!("random_{index:02}"), activations, weights, expected));
    }
    for (name, activations, weights, expected) in cases {
        let k = activations.len();
        let activations: Vec<_> = activations
            .into_iter()
            .map(|value| parse_activation(value).unwrap())
            .collect();
        let weights: Vec<_> = weights
            .into_iter()
            .map(|value| im2p_sim::parse_weight(value).unwrap())
            .collect();
        let work = MatmulWork {
            activations: MatrixView::new(&activations, 1, k, k)?,
            weights: MatrixView::new(&weights, k, 1, 1)?,
            scales: None,
            vector_op: VectorOp::Bypass,
        };
        let mut simulator = Im2pSimulator::new()?;
        let before = simulator.cycles();
        let mut output = [0_i32];
        let stats =
            simulator.execute_matmul(&work, &mut MatrixViewMut::new(&mut output, 1, 1, 1)?)?;
        assert_eq!(output, [expected], "{name}, K={k}");
        assert_eq!(stats.completed_fragments, k.div_ceil(16) as u64, "{name}");
        let (start, completion) = simulator.work_interval();
        println!(
            "PARTIAL_VECTOR name={name} k={k} output={expected} total={} work={} compute={} drain={} preload={} fragments={} start={start} completion={completion}",
            simulator.cycles() - before,
            stats.work_total_cycles,
            stats.compute_cycles,
            stats.drain_cycles,
            stats.weight_preload_cycles,
            stats.completed_fragments,
        );
    }
    Ok(())
}

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
