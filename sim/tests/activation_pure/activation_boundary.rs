use im2p_sim::{
    parse_activation, validate_provider_boundary, validate_stage_boundary,
    validate_supply_boundary, validate_tile_boundary, validate_work_boundary, ActivationValue,
    MatmulWork, MatrixView, MatrixViewMut, SimError, TileRequest, VectorOp, WeightValue,
    ACTIVATION_BITS,
};

#[test]
fn matmul_work_rejects_direct_matrix_view_a4_range_bypass() -> Result<(), SimError> {
    if im2p_sim::ACTIVATION_BITS != 4 {
        return Ok(());
    }

    let activations: [ActivationValue; 2] = [-9, 8];
    let weights = [WeightValue::default(); 2];
    let mut output = [0_i32];
    let work = MatmulWork {
        activations: MatrixView::new(&activations, 1, 2, 2)?,
        weights: MatrixView::new(&weights, 2, 1, 1)?,
        scales: None,
        vector_op: VectorOp::Bypass,
    };
    let output = MatrixViewMut::new(&mut output, 1, 1, 1)?;

    let result = validate_work_boundary(&work, &output);
    assert_eq!(result, Err(SimError::InvalidLayout));
    println!("work_direct_view values=[-9,8] result={result:?}");
    Ok(())
}

#[test]
fn tile_request_rejects_direct_slice_a4_range_bypass() {
    if im2p_sim::ACTIVATION_BITS != 4 {
        return;
    }

    let activations: [ActivationValue; 2] = [-9, 8];
    let weights = [WeightValue::default(); 2];
    let request = TileRequest {
        activations: &activations,
        weights: &weights,
        scale_matrix: None,
        valid_m: 1,
        valid_n: 1,
        valid_k: 2,
        k_start: 0,
        accumulate: false,
        vector_op: VectorOp::Bypass,
    };

    let result = validate_tile_boundary(&request, &[0], 2);
    assert_eq!(result, Err(SimError::InvalidLayout));
    println!("tile_direct_slice values=[-9,8] result={result:?}");
}

#[test]
fn provider_rejects_direct_matrix_view_a4_range_bypass() -> Result<(), SimError> {
    if im2p_sim::ACTIVATION_BITS != 4 {
        return Ok(());
    }

    let activations: [ActivationValue; 2] = [-9, 8];
    let view = MatrixView::new(&activations, 1, 2, 2)?;
    let result = validate_provider_boundary(&view);
    assert_eq!(result, Err(SimError::InvalidLayout));
    println!("provider_direct_view values=[-9,8] result={result:?}");
    Ok(())
}

#[test]
fn supply_row_rejects_a4_range_bypass() {
    if im2p_sim::ACTIVATION_BITS != 4 {
        return;
    }

    let values: [ActivationValue; 2] = [-9, 8];
    let result = validate_supply_boundary(&values);
    assert_eq!(result, Err(SimError::InvalidLayout));
    println!("supply_row values=[-9,8] result={result:?}");
}

#[test]
fn stage_row_rejects_a4_range_bypass() {
    if im2p_sim::ACTIVATION_BITS != 4 {
        return;
    }

    let values: [ActivationValue; 2] = [-9, 8];
    let result = validate_stage_boundary(&values);
    assert_eq!(result, Err(SimError::InvalidLayout));
    println!("stage_row values=[-9,8] result={result:?}");
}

#[test]
fn provider_supply_and_stage_accept_selected_width_extrema() {
    let extrema = match ACTIVATION_BITS {
        4 => [-8, 7],
        8 => [-128, 127],
        16 => [-32_768, 32_767],
        _ => unreachable!("only supported widths compile"),
    };
    let values: Vec<ActivationValue> = extrema
        .into_iter()
        .map(parse_activation)
        .collect::<Result<_, _>>()
        .expect("selected extrema must parse");
    let view = MatrixView::new(&values, 1, 2, 2).expect("valid extrema view");

    assert_eq!(validate_provider_boundary(&view), Ok(()));
    assert_eq!(validate_supply_boundary(&values), Ok(()));
    assert_eq!(validate_stage_boundary(&values), Ok(()));
}
