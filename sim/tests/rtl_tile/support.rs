use im2p_sim::{Im2pSimulator, SimError, TileRequest, TileStats, VectorOp};

#[derive(Clone, Copy)]
pub struct Shape {
    pub m: usize,
    pub n: usize,
    pub k: usize,
}

pub struct Execution<'a> {
    pub activations: &'a [i8],
    pub weights: &'a [i8],
    pub scales: Option<&'a [i8]>,
    pub shape: Shape,
    pub accumulate: bool,
    pub vector_op: VectorOp,
}

pub fn execute(
    simulator: &mut Im2pSimulator,
    execution: Execution<'_>,
) -> Result<(Vec<i32>, TileStats), SimError> {
    let mut output = vec![0_i32; execution.shape.m * execution.shape.n];
    let stats = simulator.execute_tile(
        &TileRequest {
            activations: execution.activations,
            weights: execution.weights,
            scales: execution.scales,
            valid_m: execution.shape.m,
            valid_n: execution.shape.n,
            valid_k: execution.shape.k,
            accumulate: execution.accumulate,
            vector_op: execution.vector_op,
        },
        &mut output,
    )?;
    assert_stats(&stats, execution.shape);
    Ok((output, stats))
}

pub fn golden_matmul(a: &[i8], b: &[i8], shape: Shape) -> Vec<i32> {
    let mut output = vec![0_i32; shape.m * shape.n];
    for row in 0..shape.m {
        for column in 0..shape.n {
            let mut sum = 0_i32;
            for inner in 0..shape.k {
                let product =
                    i32::from(a[row * shape.k + inner]) * i32::from(b[inner * shape.n + column]);
                sum = sum.wrapping_add(product);
            }
            output[row * shape.n + column] = sum;
        }
    }
    output
}

pub fn golden_column_multiply(raw: &[i32], scales: &[i8], shape: Shape) -> Vec<i32> {
    let mut output = vec![0_i32; shape.m * shape.n];
    for row in 0..shape.m {
        for column in 0..shape.n {
            output[row * shape.n + column] =
                raw[row * shape.n + column].wrapping_mul(i32::from(scales[column]));
        }
    }
    output
}

pub fn golden_column_shift(raw: &[i32], scales: &[i8], shape: Shape) -> Vec<i32> {
    let mut output = vec![0_i32; shape.m * shape.n];
    for row in 0..shape.m {
        for column in 0..shape.n {
            output[row * shape.n + column] =
                signed_shift(raw[row * shape.n + column], scales[column]);
        }
    }
    output
}

pub fn signed_shift(value: i32, exponent: i8) -> i32 {
    if exponent >= 0 {
        let amount = u32::from(exponent.unsigned_abs());
        if amount >= i32::BITS {
            0
        } else {
            value.wrapping_shl(amount)
        }
    } else {
        let amount = u32::from(exponent.unsigned_abs());
        if amount >= i32::BITS {
            i32::from(value < 0).wrapping_neg()
        } else {
            value >> amount
        }
    }
}

pub fn assert_matrix_eq(actual: &[i32], expected: &[i32], m: usize, n: usize) {
    assert_eq!(actual.len(), m * n, "actual matrix length");
    assert_eq!(expected.len(), m * n, "expected matrix length");
    for row in 0..m {
        for column in 0..n {
            let index = row * n + column;
            assert!(
                actual[index] == expected[index],
                "Mismatch at C[{row}, {column}] (flat index {index}):\nexpected = {}\nactual   = {}",
                expected[index],
                actual[index],
            );
        }
    }
}

pub fn assert_stats(stats: &TileStats, shape: Shape) {
    let useful_macs = (shape.m * shape.n * shape.k) as u64;
    assert!(stats.compute_cycles > 0);
    assert!(stats.total_cycles >= stats.compute_cycles);
    assert_eq!(stats.useful_macs, useful_macs);
    assert_eq!(stats.useful_ops, 2 * useful_macs);
    assert!(stats.macs_per_cycle.is_finite());
    assert!(stats.ops_per_cycle.is_finite());
    assert!(stats.utilization.is_finite());
}

pub fn activation_fragment(
    activations: &[i8],
    m: usize,
    total_k: usize,
    start: usize,
    fragment_k: usize,
) -> Vec<i8> {
    let mut fragment = Vec::with_capacity(m * fragment_k);
    for row in 0..m {
        let row_start = row * total_k + start;
        fragment.extend_from_slice(&activations[row_start..row_start + fragment_k]);
    }
    fragment
}

pub fn weight_fragment(weights: &[i8], n: usize, start: usize, fragment_k: usize) -> Vec<i8> {
    let row_start = start * n;
    let row_end = (start + fragment_k) * n;
    weights[row_start..row_end].to_vec()
}

pub struct Lcg(u32);

impl Lcg {
    pub const fn new(seed: u32) -> Self {
        Self(seed)
    }

    pub fn signed(&mut self, minimum: i8, maximum: i8) -> i8 {
        self.0 = self.0.wrapping_mul(1_664_525).wrapping_add(1_013_904_223);
        let width = i16::from(maximum) - i16::from(minimum) + 1;
        let offset = (self.0 % u32::try_from(width).unwrap()) as i16;
        i8::try_from(i16::from(minimum) + offset).unwrap()
    }
}
