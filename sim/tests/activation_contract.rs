use im2p_sim::{
    activation_elements_to_bytes, activation_view_from_bytes, parse_activation, ActivationError,
    ActivationValue, MatrixView, ACTIVATION_BITS, ACTIVATION_STORAGE_BYTES,
};

#[test]
fn baseline_signed_int8_matrix_view_preserves_values_and_element_stride() {
    let values = [-128_i8, 127, 99, -1, 0, 42];
    let view = MatrixView::new(&values, 2, 2, 3).expect("valid characterization layout");

    assert_eq!(view.values, &values);
    assert_eq!(view.row_stride, 3);
    assert_eq!(view.values[view.row_stride], -1);
}

#[test]
fn activation_extrema_and_storage_match_the_selected_width() -> Result<(), ActivationError> {
    let (minimum, maximum) = match ACTIVATION_BITS {
        4 => (-8, 7),
        8 => (-128, 127),
        16 => (-32_768, 32_767),
        _ => unreachable!("the build contract permits only 4, 8, or 16 bits"),
    };

    let values: Vec<ActivationValue> = [minimum, maximum]
        .into_iter()
        .map(parse_activation)
        .collect::<Result<_, _>>()?;
    assert_eq!(ACTIVATION_STORAGE_BYTES, size_of::<ActivationValue>());
    assert_eq!(
        activation_elements_to_bytes(values.len())?,
        2 * ACTIVATION_STORAGE_BYTES
    );
    Ok(())
}

#[test]
fn activation_a4_rejects_values_outside_signed_nibble_range() {
    if ACTIVATION_BITS != 4 {
        return;
    }

    assert_eq!(
        parse_activation(-9),
        Err(ActivationError::ValueOutOfRange {
            value: -9,
            minimum: -8,
            maximum: 7,
        })
    );
    assert_eq!(
        parse_activation(8),
        Err(ActivationError::ValueOutOfRange {
            value: 8,
            minimum: -8,
            maximum: 7,
        })
    );
}

#[test]
fn activation_a16_converts_element_counts_to_bytes_with_overflow_checks() {
    if ACTIVATION_BITS != 16 {
        return;
    }

    assert_eq!(activation_elements_to_bytes(3), Ok(6));
    assert_eq!(
        activation_elements_to_bytes(usize::MAX),
        Err(ActivationError::ByteCountOverflow {
            elements: usize::MAX,
            storage_bytes: 2,
        })
    );
}

#[test]
fn activation_a16_rejects_misaligned_storage_stride_before_layout_validation() {
    if ACTIVATION_BITS != 16 {
        return;
    }

    let values = [ActivationValue::default(); 6];
    assert_eq!(
        activation_view_from_bytes(&values, 2, 2, 3),
        Err(ActivationError::MisalignedByteCount {
            bytes: 3,
            storage_bytes: 2,
        })
    );
}

#[test]
fn manual_activation_contract_surface() -> Result<(), ActivationError> {
    let (minimum, maximum) = match ACTIVATION_BITS {
        4 => (-8, 7),
        8 => (-128, 127),
        16 => (-32_768, 32_767),
        _ => unreachable!("the build contract permits only 4, 8, or 16 bits"),
    };
    let values = [minimum, maximum, 0, minimum, maximum]
        .map(parse_activation)
        .into_iter()
        .collect::<Result<Vec<_>, _>>()?;
    let stride_bytes = activation_elements_to_bytes(3)?;
    let view = activation_view_from_bytes(&values, 2, 2, stride_bytes)?;

    assert_eq!(view.row_stride, 3);
    let rejects_eight = ACTIVATION_BITS == 4 && parse_activation(8).is_err();
    if ACTIVATION_BITS == 4 {
        assert!(rejects_eight);
    }
    println!(
        "bits={ACTIVATION_BITS} extrema=[{minimum},{maximum}] storage_bytes={ACTIVATION_STORAGE_BYTES} stride_elements={} stride_bytes={stride_bytes} rejects_8={rejects_eight}",
        view.row_stride,
    );
    Ok(())
}
