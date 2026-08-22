use im2p_sim::{activation_to_i32, weight_to_i32, ActivationValue, VectorOp, WeightValue};

use super::{KBlockScaleMatrix, KFragment, Shape};

pub fn golden_output(
    activations: &[ActivationValue],
    weights: &[WeightValue],
    shape: Shape,
    column_offset: usize,
    valid_columns: usize,
    fragments: &[KFragment],
    scales: Option<&KBlockScaleMatrix>,
    operation: VectorOp,
) -> Vec<i64> {
    let mut output = vec![0_i64; shape.m * valid_columns];
    for fragment in fragments {
        for row in 0..shape.m {
            for local_column in 0..valid_columns {
                let column = column_offset + local_column;
                let mut partial = 0_i64;
                for k in fragment.start..fragment.start + fragment.count {
                    let activation = i64::from(activation_to_i32(activations[row * shape.k + k]));
                    let weight = i64::from(weight_to_i32(weights[k * shape.n + column]));
                    partial = partial.wrapping_add(activation.wrapping_mul(weight));
                }
                let scale = scales.map_or(0, |matrix| matrix.get(fragment.block, column));
                let contribution = transform(partial, scale, operation);
                let index = row * valid_columns + local_column;
                output[index] = output[index].wrapping_add(contribution);
            }
        }
    }
    output
}

fn transform(partial: i64, scale: i8, operation: VectorOp) -> i64 {
    match operation {
        VectorOp::Bypass | VectorOp::External => partial,
        VectorOp::Multiply => partial.wrapping_mul(i64::from(scale)),
        VectorOp::Shift => signed_shift(partial, scale),
    }
}

fn signed_shift(value: i64, exponent: i8) -> i64 {
    let amount = u32::from(exponent.unsigned_abs());
    if exponent < 0 {
        if amount >= i64::BITS {
            if value < 0 {
                -1
            } else {
                0
            }
        } else {
            value >> amount
        }
    } else if amount >= i64::BITS {
        0
    } else {
        value.wrapping_shl(amount)
    }
}
