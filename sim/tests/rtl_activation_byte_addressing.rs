pub mod common;

use im2p_sim::{
    activation_to_i32, parse_activation, ActivationStripe, ActivationValue, Im2pSimulator,
    MatmulWork, MatrixView, MatrixViewMut, SimError, StripeWorkDesc, VectorOp,
};

const M: usize = 3;
const N: usize = 2;
const K: usize = 3;
const A_STRIDE: usize = 5;
const MAX_STEPS: usize = 100_000;

fn activation(value: i32) -> ActivationValue {
    parse_activation(value).expect("test activation fits every selected width")
}

fn operands() -> (Vec<ActivationValue>, Vec<i8>) {
    let logical = [[-7, 2, 5], [6, -3, 1], [4, 7, -2]];
    let mut activations = vec![activation(0); M * A_STRIDE];
    for row in 0..M {
        for column in 0..K {
            activations[row * A_STRIDE + column] = activation(logical[row][column]);
        }
        activations[row * A_STRIDE + K..(row + 1) * A_STRIDE].fill(activation(-8));
    }
    (activations, vec![3, -2, -4, 5, 6, 1])
}

fn oracle(activations: &[ActivationValue], weights: &[i8]) -> Vec<i32> {
    let mut output = vec![0_i32; M * N];
    for row in 0..M {
        for column in 0..N {
            let sum = (0..K).fold(0_i64, |sum, k| {
                sum + i64::from(activation_to_i32(activations[row * A_STRIDE + k]))
                    * i64::from(weights[k * N + column])
            });
            output[row * N + column] = i32::try_from(sum).expect("small independent oracle");
        }
    }
    output
}

fn run_full(activations: &[ActivationValue], weights: &[i8]) -> Result<Vec<i32>, SimError> {
    let mut output = vec![i32::MIN; M * N];
    let work = MatmulWork {
        activations: MatrixView::new(activations, M, K, A_STRIDE)?,
        weights: MatrixView::new(weights, K, N, N)?,
        scales: None,
        vector_op: VectorOp::Bypass,
    };
    Im2pSimulator::new()?.execute_matmul(&work, &mut MatrixViewMut::new(&mut output, M, N, N)?)?;
    Ok(output)
}

fn run_striped(activations: &[ActivationValue], weights: &[i8]) -> Result<Vec<i32>, SimError> {
    let descriptor = StripeWorkDesc {
        weights,
        scale_matrix: None,
        rows: M,
        columns: N,
        reduction: K,
        vector_op: VectorOp::Bypass,
        work_context: 0x4131_365f_4259_5445,
    };
    let mut job = Im2pSimulator::new()?.begin_striped_matmul(&descriptor)?;
    let mut output = vec![i32::MIN; M * N];
    let stripes = [(0, 1), (1, 2)];
    let mut next_stripe = 0;
    let mut written = 0;
    for _ in 0..MAX_STEPS {
        while next_stripe < stripes.len() && job.npu_ready() {
            let (row_begin, row_count) = stripes[next_stripe];
            job.publish_stripe_layout(
                ActivationStripe {
                    stripe_id: next_stripe as u32,
                    row_begin,
                    row_count,
                    stripe_context: next_stripe as u64 + 1,
                },
                A_STRIDE,
            )?;
            next_stripe += 1;
        }
        if let Some(row) = job.pending_activation_row() {
            job.supply_activation_row(row, &activations[row * A_STRIDE..][..K])?;
        }
        if let Some((row, column)) = job.pending_output_region() {
            let values = job.take_output_region(row, column)?;
            output[row * N + column..][..values.len()].copy_from_slice(&values);
            written += values.len();
            job.acknowledge_output_row(row)?;
        }
        job.progress(1)?;
        while job.poll_completed().is_some() {}
        if next_stripe == stripes.len() && written == M * N {
            break;
        }
    }
    assert_eq!(written, M * N, "striped output did not complete");
    job.finish()?;
    Ok(output)
}

#[test]
fn full_and_stripe_k3_stride5_gutters_match_i64_oracle() -> Result<(), SimError> {
    let (activations, weights) = operands();
    let expected = oracle(&activations, &weights);
    let full = run_full(&activations, &weights)?;
    let striped = run_striped(&activations, &weights)?;
    println!(
        "activation byte addressing: storage_bytes={} logical_stride={} byte_stride={} full={full:?} striped={striped:?} oracle={expected:?}",
        size_of::<ActivationValue>(),
        A_STRIDE,
        A_STRIDE * size_of::<ActivationValue>(),
    );
    assert_eq!(full, expected);
    assert_eq!(striped, expected);
    Ok(())
}
