use im2p_sim::{Im2pSimulator, SimError, TileStats, VectorOp};

use crate::support::{
    activation_fragment, execute, signed_shift, weight_fragment, Execution, KRange, Shape,
};

pub const BLOCK_SIZE: usize = 32;

pub fn structured_inputs(shape: Shape) -> (Vec<i8>, Vec<i8>) {
    let activations = (0..shape.m * shape.k)
        .map(|index| {
            let row = index / shape.k;
            let k = index % shape.k;
            ((17 * row + 13 * k + 5) % 15) as i8 - 7
        })
        .collect();
    let weights = (0..shape.k * shape.n)
        .map(|index| {
            let k = index / shape.n;
            let column = index % shape.n;
            ((11 * k + 7 * column + 3) % 13) as i8 - 6
        })
        .collect();
    (activations, weights)
}

pub fn patterned_scales(shape: Shape, block_size: usize, operation: VectorOp) -> Vec<i8> {
    let blocks = shape.k.div_ceil(block_size);
    let multiply = [-5_i8, 3, -2, 7, 1, -4, 6, -1];
    let shift = [-3_i8, 2, -1, 0, 3, 1, -2];
    let mut scales = Vec::with_capacity(blocks * shape.n);
    for block in 0..blocks {
        for column in 0..shape.n {
            let scale = match operation {
                VectorOp::Multiply => multiply[(3 * block + 5 * column) % multiply.len()],
                VectorOp::Shift => shift[(2 * block + 3 * column) % shift.len()],
                VectorOp::Bypass => 0,
            };
            scales.push(scale);
        }
    }
    scales
}

pub fn golden_fragmentwise(
    activations: &[i8],
    weights: &[i8],
    scales: &[i8],
    shape: Shape,
    dim: usize,
    block_size: usize,
    operation: VectorOp,
) -> Vec<i32> {
    let mut output = vec![0_i32; shape.m * shape.n];
    let mut fragment_start = 0;
    while fragment_start < shape.k {
        let block = fragment_start / block_size;
        let block_end = ((block + 1) * block_size).min(shape.k);
        let fragment_end = (fragment_start + dim).min(block_end);
        for row in 0..shape.m {
            for column in 0..shape.n {
                let mut partial = 0_i32;
                for k in fragment_start..fragment_end {
                    partial = partial.wrapping_add(
                        i32::from(activations[row * shape.k + k])
                            * i32::from(weights[k * shape.n + column]),
                    );
                }
                let scale = scales[block * shape.n + column];
                let contribution = transform(partial, scale, operation);
                let index = row * shape.n + column;
                output[index] = output[index].wrapping_add(contribution);
            }
        }
        fragment_start = fragment_end;
    }
    output
}

pub fn execute_fragmentwise(
    simulator: &mut Im2pSimulator,
    activations: &[i8],
    weights: &[i8],
    scales: &[i8],
    shape: Shape,
    block_size: usize,
    operation: VectorOp,
) -> Result<(Vec<i32>, Vec<TileStats>), SimError> {
    let dim = simulator.dim();
    let mut actual = Vec::new();
    let mut stats = Vec::new();
    let mut execution_index = 0;
    for block_start in (0..shape.k).step_by(block_size) {
        let block_end = (block_start + block_size).min(shape.k);
        for fragment_start in (block_start..block_end).step_by(dim) {
            let fragment_k = dim.min(block_end - fragment_start);
            let fragment_a =
                activation_fragment(activations, shape.m, shape.k, fragment_start, fragment_k);
            let fragment_b = weight_fragment(weights, shape.n, fragment_start, fragment_k);
            let (fragment_output, fragment_stats) = execute(
                simulator,
                Execution {
                    activations: &fragment_a,
                    weights: &fragment_b,
                    scales: Some(scales),
                    shape: Shape {
                        k: fragment_k,
                        ..shape
                    },
                    k_range: KRange {
                        start: fragment_start,
                        total: shape.k,
                        block_size,
                    },
                    accumulate: execution_index != 0,
                    vector_op: operation,
                },
            )?;
            actual = fragment_output;
            stats.push(fragment_stats);
            execution_index += 1;
        }
    }

    let total_macs: u64 = stats.iter().map(|entry| entry.useful_macs).sum();
    let total_ops: u64 = stats.iter().map(|entry| entry.useful_ops).sum();
    assert_eq!(total_macs, (shape.m * shape.n * shape.k) as u64);
    assert_eq!(total_ops, 2 * total_macs);
    Ok((actual, stats))
}

pub fn assert_block_matrix_eq(
    actual: &[i32],
    expected: &[i32],
    activations: &[i8],
    weights: &[i8],
    scales: &[i8],
    shape: Shape,
    dim: usize,
    block_size: usize,
    operation: VectorOp,
) {
    assert_eq!(actual.len(), shape.m * shape.n);
    assert_eq!(expected.len(), shape.m * shape.n);
    for row in 0..shape.m {
        for column in 0..shape.n {
            let index = row * shape.n + column;
            if actual[index] == expected[index] {
                continue;
            }
            let mut detail = String::new();
            let mut running = 0_i32;
            let mut fragment = 0;
            let mut start = 0;
            while start < shape.k {
                let block = start / block_size;
                let end = (start + dim).min((block + 1) * block_size).min(shape.k);
                let mut partial = 0_i32;
                for k in start..end {
                    partial = partial.wrapping_add(
                        i32::from(activations[row * shape.k + k])
                            * i32::from(weights[k * shape.n + column]),
                    );
                }
                let scale = scales[block * shape.n + column];
                let contribution = transform(partial, scale, operation);
                running = running.wrapping_add(contribution);
                detail.push_str(&format!(
                    "\nfragment={fragment} K=[{start},{end}) block={block} scale={scale} partial={partial} contribution={contribution} running={running}"
                ));
                start = end;
                fragment += 1;
            }
            panic!(
                "Mismatch at C[{row}, {column}] (flat index {index}): expected={} actual={} DIM={dim} block_size={block_size} operation={operation:?}{detail}",
                expected[index], actual[index],
            );
        }
    }
}

fn transform(partial: i32, scale: i8, operation: VectorOp) -> i32 {
    match operation {
        VectorOp::Bypass => partial,
        VectorOp::Multiply => partial.wrapping_mul(i32::from(scale)),
        VectorOp::Shift => signed_shift(partial, scale),
    }
}
