use im2p_sim::{Im2pSimulator, SimError, VectorOp};

use crate::support::{
    activation_fragment, assert_matrix_eq, execute, golden_column_multiply, golden_column_shift,
    golden_matmul, weight_fragment, Execution, Shape,
};

const K_GROUP: usize = 32;

#[test]
fn k_group_32_multiply_matches_across_hardware_fragments() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let shape = Shape {
        m: 3,
        n: 5,
        k: K_GROUP,
    };
    let activations: Vec<i8> = (0..shape.m * shape.k)
        .map(|index| (index % 7) as i8 - 3)
        .collect();
    let weights: Vec<i8> = (0..shape.k * shape.n)
        .map(|index| (index % 5) as i8 - 2)
        .collect();
    let scales = [2, -1, 3, -2, 1];
    let raw = golden_matmul(&activations, &weights, shape);
    let expected = golden_column_multiply(&raw, &scales, shape);
    let mut actual = Vec::new();

    for start in (0..K_GROUP).step_by(dim) {
        let fragment_k = dim.min(K_GROUP - start);
        let fragment_shape = Shape {
            k: fragment_k,
            ..shape
        };
        let fragment_a = activation_fragment(&activations, shape.m, K_GROUP, start, fragment_k);
        let fragment_b = weight_fragment(&weights, shape.n, start, fragment_k);
        (actual, _) = execute(
            &mut simulator,
            Execution {
                activations: &fragment_a,
                weights: &fragment_b,
                scales: Some(&scales),
                shape: fragment_shape,
                accumulate: start != 0,
                vector_op: VectorOp::Multiply,
            },
        )?;
    }
    assert_matrix_eq(&actual, &expected, shape.m, shape.n);
    Ok(())
}

#[test]
fn dim16_fragmented_shift_matches_fragment_not_full_group_semantics() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    if simulator.dim() != 16 {
        return Ok(());
    }

    let shape = Shape {
        m: 1,
        n: 1,
        k: K_GROUP,
    };
    let activations = vec![1_i8; K_GROUP];
    let mut weights = vec![0_i8; K_GROUP];
    weights[0] = 1;
    weights[16] = 1;
    let scales = [-1_i8];
    let mut actual = Vec::new();
    let mut fragment_expected = vec![0_i32; 1];

    for start in [0, 16] {
        let fragment_shape = Shape { k: 16, ..shape };
        let fragment_a = activation_fragment(&activations, 1, K_GROUP, start, 16);
        let fragment_b = weight_fragment(&weights, 1, start, 16);
        let raw_fragment = golden_matmul(&fragment_a, &fragment_b, fragment_shape);
        let shifted_fragment = golden_column_shift(&raw_fragment, &scales, fragment_shape);
        fragment_expected[0] = fragment_expected[0].wrapping_add(shifted_fragment[0]);
        (actual, _) = execute(
            &mut simulator,
            Execution {
                activations: &fragment_a,
                weights: &fragment_b,
                scales: Some(&scales),
                shape: fragment_shape,
                accumulate: start != 0,
                vector_op: VectorOp::Shift,
            },
        )?;
    }

    let full_raw = golden_matmul(&activations, &weights, shape);
    let full_group_expected = golden_column_shift(&full_raw, &scales, shape);
    assert_matrix_eq(&actual, &fragment_expected, 1, 1);
    assert_ne!(actual, full_group_expected);
    println!(
        "DIM16 K-group32 shift: RTL={} fragment=(1 >> 1)+(1 >> 1)={} full=(1+1)>>1={}",
        actual[0], fragment_expected[0], full_group_expected[0],
    );
    Ok(())
}

#[test]
fn multiple_k_quant_groups_use_distinct_column_scales() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let shape = Shape { m: 2, n: 4, k: 64 };
    let activations: Vec<i8> = (0..shape.m * shape.k)
        .map(|index| (index % 9) as i8 - 4)
        .collect();
    let weights: Vec<i8> = (0..shape.k * shape.n)
        .map(|index| (index % 7) as i8 - 3)
        .collect();
    let group_scales = [[2_i8, -1, 3, 1], [-2_i8, 4, 1, -3]];
    let mut expected = vec![0_i32; shape.m * shape.n];
    let mut actual = Vec::new();
    let mut execution_index = 0;

    for (group, scales) in group_scales.iter().enumerate() {
        let group_start = group * K_GROUP;
        let group_a = activation_fragment(&activations, shape.m, shape.k, group_start, K_GROUP);
        let group_b = weight_fragment(&weights, shape.n, group_start, K_GROUP);
        let group_shape = Shape {
            k: K_GROUP,
            ..shape
        };
        let raw_group = golden_matmul(&group_a, &group_b, group_shape);
        let scaled_group = golden_column_multiply(&raw_group, scales, group_shape);
        for (sum, contribution) in expected.iter_mut().zip(scaled_group) {
            *sum = sum.wrapping_add(contribution);
        }

        for offset in (0..K_GROUP).step_by(dim) {
            let fragment_k = dim.min(K_GROUP - offset);
            let fragment_start = group_start + offset;
            let fragment_a =
                activation_fragment(&activations, shape.m, shape.k, fragment_start, fragment_k);
            let fragment_b = weight_fragment(&weights, shape.n, fragment_start, fragment_k);
            (actual, _) = execute(
                &mut simulator,
                Execution {
                    activations: &fragment_a,
                    weights: &fragment_b,
                    scales: Some(scales),
                    shape: Shape {
                        k: fragment_k,
                        ..shape
                    },
                    accumulate: execution_index != 0,
                    vector_op: VectorOp::Multiply,
                },
            )?;
            execution_index += 1;
        }
    }
    assert_matrix_eq(&actual, &expected, shape.m, shape.n);
    Ok(())
}
