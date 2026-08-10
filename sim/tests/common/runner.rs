use im2p_sim::{Im2pSimulator, SimError, TileRequest, TileStats, VectorOp};

use super::{golden_output, k_fragments, KBlockScaleMatrix, KFragment, Shape};

pub struct RunResult {
    pub output: Vec<i32>,
    pub expected: Vec<i32>,
    pub fragments: Vec<KFragment>,
    pub stats: Vec<TileStats>,
}

pub struct Case<'a> {
    pub shape: Shape,
    pub activations: &'a [i8],
    pub weights: &'a [i8],
    pub scales: Option<&'a KBlockScaleMatrix>,
    pub column_offset: usize,
    pub valid_columns: usize,
    pub context: u64,
    pub operation: VectorOp,
}

pub fn run_case(simulator: &mut Im2pSimulator, case: Case<'_>) -> Result<RunResult, SimError> {
    let Case {
        shape,
        activations,
        weights,
        scales,
        column_offset,
        valid_columns,
        context,
        operation,
    } = case;
    assert_eq!(activations.len(), shape.m * shape.k);
    assert_eq!(weights.len(), shape.k * shape.n);
    let block_size = scales.map_or(shape.k, |matrix| matrix.block_size);
    let fragments = k_fragments(shape.k, block_size, simulator.dim());
    let expected = golden_output(
        activations,
        weights,
        shape,
        column_offset,
        valid_columns,
        &fragments,
        scales,
        operation,
    );
    let mut output = vec![0_i32; shape.m * valid_columns];
    let mut stats = Vec::with_capacity(fragments.len());

    for (execution_index, fragment) in fragments.iter().enumerate() {
        let mut activation_fragment = Vec::with_capacity(shape.m * fragment.count);
        for row in 0..shape.m {
            let start = row * shape.k + fragment.start;
            activation_fragment.extend_from_slice(&activations[start..start + fragment.count]);
        }
        let mut weight_fragment = Vec::with_capacity(fragment.count * valid_columns);
        for k in fragment.start..fragment.start + fragment.count {
            let start = k * shape.n + column_offset;
            weight_fragment.extend_from_slice(&weights[start..start + valid_columns]);
        }
        let scale_matrix = scales.map(|matrix| matrix.view(column_offset, valid_columns, context));
        stats.push(simulator.execute_tile(
            &TileRequest {
                activations: &activation_fragment,
                weights: &weight_fragment,
                scale_matrix,
                valid_m: shape.m,
                valid_n: valid_columns,
                valid_k: fragment.count,
                k_start: fragment.start,
                accumulate: execution_index != 0,
                vector_op: operation,
            },
            &mut output,
        )?);
    }

    Ok(RunResult {
        output,
        expected,
        fragments,
        stats,
    })
}
