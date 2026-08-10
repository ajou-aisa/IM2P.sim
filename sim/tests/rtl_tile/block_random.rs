use im2p_sim::{Im2pSimulator, SimError, VectorOp};

use crate::block_support::{
    assert_block_matrix_eq, execute_fragmentwise, golden_fragmentwise, BLOCK_SIZE,
};
use crate::support::{Lcg, Shape};

#[test]
fn deterministic_random_block_aware_multiply_and_shift() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    for k in [48, 96] {
        let shape = Shape { m: 11, n: 13, k };
        for operation in [VectorOp::Multiply, VectorOp::Shift] {
            let seed = 0xb320_0000
                ^ ((dim as u32) << 16)
                ^ ((k as u32) << 4)
                ^ match operation {
                    VectorOp::Bypass => 0,
                    VectorOp::Multiply => 1,
                    VectorOp::Shift => 2,
                };
            let mut generator = Lcg::new(seed);
            let activations = (0..shape.m * shape.k)
                .map(|_| generator.signed(-8, 7))
                .collect::<Vec<_>>();
            let weights = (0..shape.k * shape.n)
                .map(|_| generator.signed(-8, 7))
                .collect::<Vec<_>>();
            let mut scales = (0..shape.k.div_ceil(BLOCK_SIZE) * shape.n)
                .map(|_| match operation {
                    VectorOp::Multiply => generator.signed(-5, 5),
                    VectorOp::Shift => generator.signed(-3, 3),
                    VectorOp::Bypass => 0,
                })
                .collect::<Vec<_>>();
            make_adjacent_blocks_distinct(&mut scales, shape.n);

            let expected = golden_fragmentwise(
                &activations,
                &weights,
                &scales,
                shape,
                dim,
                BLOCK_SIZE,
                operation,
            );
            let (actual, _) = execute_fragmentwise(
                &mut simulator,
                &activations,
                &weights,
                &scales,
                shape,
                BLOCK_SIZE,
                operation,
            )?;
            assert_block_matrix_eq(
                &actual,
                &expected,
                &activations,
                &weights,
                &scales,
                shape,
                dim,
                BLOCK_SIZE,
                operation,
            );
        }
    }
    Ok(())
}

fn make_adjacent_blocks_distinct(scales: &mut [i8], n: usize) {
    for index in n..scales.len() {
        if scales[index] == scales[index - n] {
            scales[index] = if scales[index] == 3 {
                -3
            } else {
                scales[index] + 1
            };
        }
    }
}
