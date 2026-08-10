use im2p_sim::VectorOp;

use super::{KBlockScaleMatrix, KFragment, Shape};

pub fn golden_output(
    activations: &[i8],
    weights: &[i8],
    shape: Shape,
    column_offset: usize,
    valid_columns: usize,
    fragments: &[KFragment],
    scales: Option<&KBlockScaleMatrix>,
    operation: VectorOp,
) -> Vec<i32> {
    let mut output = vec![0_i32; shape.m * valid_columns];
    for fragment in fragments {
        for row in 0..shape.m {
            for local_column in 0..valid_columns {
                let column = column_offset + local_column;
                let mut partial = 0_i32;
                for k in fragment.start..fragment.start + fragment.count {
                    partial = partial.wrapping_add(
                        i32::from(activations[row * shape.k + k])
                            .wrapping_mul(i32::from(weights[k * shape.n + column])),
                    );
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

fn transform(partial: i32, scale: i8, operation: VectorOp) -> i32 {
    match operation {
        VectorOp::Bypass => partial,
        VectorOp::Multiply => partial.wrapping_mul(i32::from(scale)),
        VectorOp::Shift => signed_shift(partial, scale),
    }
}

fn signed_shift(value: i32, exponent: i8) -> i32 {
    let amount = u32::from(exponent.unsigned_abs());
    if exponent < 0 {
        if amount >= i32::BITS {
            if value < 0 {
                -1
            } else {
                0
            }
        } else {
            value >> amount
        }
    } else if amount >= i32::BITS {
        0
    } else {
        value.wrapping_shl(amount)
    }
}
