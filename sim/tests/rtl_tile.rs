use im2p_sim::{Im2pSimulator, SimError, TileRequest, VectorOp};

fn product(a: &[i8], b: &[i8], m: usize, n: usize, k: usize) -> Vec<i32> {
    let mut output = vec![0_i32; m * n];
    for row in 0..m {
        for column in 0..n {
            output[row * n + column] = (0..k)
                .map(|inner| i32::from(a[row * k + inner]) * i32::from(b[inner * n + column]))
                .sum();
        }
    }
    output
}

fn run(
    activations: &[i8],
    weights: &[i8],
    m: usize,
    n: usize,
    k: usize,
) -> Result<(Vec<i32>, im2p_sim::TileStats), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let mut output = vec![0_i32; m * n];
    let stats = simulator.execute_tile(
        &TileRequest {
            activations,
            weights,
            scales: None,
            valid_m: m,
            valid_n: n,
            valid_k: k,
            accumulate: false,
            vector_op: VectorOp::Bypass,
        },
        &mut output,
    )?;
    Ok((output, stats))
}

#[test]
fn full_tile_matches_int32_golden() -> Result<(), SimError> {
    let dim = Im2pSimulator::new()?.dim();
    let activations: Vec<i8> = (0..dim * dim).map(|index| (index % 7) as i8 - 3).collect();
    let weights: Vec<i8> = (0..dim * dim).map(|index| (index % 5) as i8 - 2).collect();
    let expected = product(&activations, &weights, dim, dim, dim);
    let (actual, stats) = run(&activations, &weights, dim, dim, dim)?;
    assert_eq!(actual, expected);
    println!(
        "dim={dim} weight_load={} compute={} total={} macs_per_cycle={:.3} ops_per_cycle={:.3} utilization={:.6}",
        stats.weight_load_cycles,
        stats.compute_cycles,
        stats.total_cycles,
        stats.macs_per_cycle,
        stats.ops_per_cycle,
        stats.utilization,
    );
    assert_eq!(stats.useful_macs, (dim * dim * dim) as u64);
    assert!(stats.total_cycles >= stats.compute_cycles);
    Ok(())
}

#[test]
fn tail_tile_matches_int32_golden() -> Result<(), SimError> {
    let dim = Im2pSimulator::new()?.dim();
    let m = 7;
    let n = dim.saturating_sub(3);
    let k = 10;
    let activations: Vec<i8> = (0..m * k).map(|index| (index % 9) as i8 - 4).collect();
    let weights: Vec<i8> = (0..k * n).map(|index| (index % 7) as i8 - 3).collect();
    let expected = product(&activations, &weights, m, n, k);
    let (actual, _) = run(&activations, &weights, m, n, k)?;
    assert_eq!(actual, expected);
    Ok(())
}

#[test]
fn accumulation_matches_two_k_tiles() -> Result<(), SimError> {
    let dim = Im2pSimulator::new()?.dim();
    let m = 2;
    let n = 3;
    let k = dim / 2;
    let first_a = vec![2_i8; m * k];
    let first_b = vec![3_i8; k * n];
    let second_a = vec![-1_i8; m * k];
    let second_b = vec![4_i8; k * n];
    let mut simulator = Im2pSimulator::new()?;
    let mut output = vec![0_i32; m * n];
    let first = simulator.execute_tile(
        &TileRequest {
            activations: &first_a,
            weights: &first_b,
            scales: None,
            valid_m: m,
            valid_n: n,
            valid_k: k,
            accumulate: false,
            vector_op: VectorOp::Bypass,
        },
        &mut output,
    )?;
    assert_eq!(output, vec![6 * k as i32; m * n]);
    let second = simulator.execute_tile(
        &TileRequest {
            activations: &second_a,
            weights: &second_b,
            scales: None,
            valid_m: m,
            valid_n: n,
            valid_k: k,
            accumulate: true,
            vector_op: VectorOp::Bypass,
        },
        &mut output,
    )?;
    assert_eq!(output, vec![2 * k as i32; m * n]);
    assert!(second.total_cycles > 0 && first.total_cycles > 0);
    Ok(())
}

#[test]
fn multiply_applies_signed_scales() -> Result<(), SimError> {
    let dim = Im2pSimulator::new()?.dim();
    let m = 1;
    let n = 2;
    let k = 2;
    let activations = vec![2_i8, 3];
    let weights = vec![3_i8, 4, 1, 2];
    let scales = vec![2_i8, -1];
    let mut simulator = Im2pSimulator::new()?;
    let mut output = vec![0_i32; n];
    simulator.execute_tile(
        &TileRequest {
            activations: &activations,
            weights: &weights,
            scales: Some(&scales),
            valid_m: m,
            valid_n: n,
            valid_k: k,
            accumulate: false,
            vector_op: VectorOp::Multiply,
        },
        &mut output,
    )?;
    assert_eq!(output, vec![18, -14]);
    assert_eq!(simulator.dim(), dim);
    Ok(())
}

#[test]
fn shift_handles_positive_and_negative_exponents() -> Result<(), SimError> {
    let dim = Im2pSimulator::new()?.dim();
    let activations = vec![8_i8];
    let weights = vec![2_i8, 4];
    let scales = vec![1_i8, -1];
    let mut simulator = Im2pSimulator::new()?;
    let mut output = vec![0_i32; 2];
    simulator.execute_tile(
        &TileRequest {
            activations: &activations,
            weights: &weights,
            scales: Some(&scales),
            valid_m: 1,
            valid_n: 2,
            valid_k: 1,
            accumulate: false,
            vector_op: VectorOp::Shift,
        },
        &mut output,
    )?;
    assert_eq!(output, vec![32, 16]);
    assert!(dim == 16 || dim == 32);
    Ok(())
}

#[test]
fn invalid_tile_shape_returns_error() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let mut output = vec![0_i32; 1];
    let result = simulator.execute_tile(
        &TileRequest {
            activations: &[1],
            weights: &[1],
            scales: None,
            valid_m: 0,
            valid_n: 1,
            valid_k: 1,
            accumulate: false,
            vector_op: VectorOp::Bypass,
        },
        &mut output,
    );
    assert_eq!(result, Err(SimError::InvalidTileShape));
    Ok(())
}
