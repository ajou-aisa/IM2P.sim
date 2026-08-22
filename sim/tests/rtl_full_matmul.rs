//! Full-matrix public API: one `execute_matmul` call must reproduce the exact
//! independent CPU golden (`common::golden_output` over `common::k_fragments`,
//! the same host-side arithmetic the tile suite is checked against) for shapes
//! whose M, N, and K all exceed DIM, with simultaneous M/N/K/block tails and
//! non-contiguous A/B/S/C row strides. Rust performs no tiling here.
//!
//! Absent until implemented: `MatrixView::new(values, rows, columns, stride)`,
//! the `MatrixViewMut` equivalent, `MatmulWork { activations, weights, scales,
//! vector_op }`, and `execute_matmul(&MatmulWork, &mut MatrixViewMut<i32>)`.

pub mod common;

use common::{assert_matrix_eq, golden_output, k_fragments, KBlockScaleMatrix, Lcg, Shape};
use im2p_sim::{
    parse_activation, ActivationValue, Im2pSimulator, MatmulWork, MatrixView, MatrixViewMut,
    SimError, TileRequest, VectorOp,
};

/// Row-major matrix with an arbitrary row stride, so the API is handed
/// genuinely non-contiguous storage: `row_stride > columns` leaves a fill
/// gutter after every logical row.
struct Strided<T> {
    values: Vec<T>,
    rows: usize,
    columns: usize,
    row_stride: usize,
}

impl<T: Copy> Strided<T> {
    fn new(rows: usize, columns: usize, row_stride: usize, fill: T) -> Self {
        assert!(row_stride >= columns);
        let values = vec![fill; rows * row_stride];
        Self {
            values,
            rows,
            columns,
            row_stride,
        }
    }

    fn from_fn(
        rows: usize,
        columns: usize,
        stride: usize,
        filler: T,
        mut value: impl FnMut(usize, usize) -> T,
    ) -> Self {
        let mut m = Self::new(rows, columns, stride, filler);
        for row in 0..rows {
            for column in 0..columns {
                m.values[row * stride + column] = value(row, column);
            }
        }
        m
    }

    /// Logical elements only, gutters excluded.
    fn packed(&self) -> Vec<T> {
        let mut p = Vec::with_capacity(self.rows * self.columns);
        for row in 0..self.rows {
            let at = row * self.row_stride;
            p.extend_from_slice(&self.values[at..at + self.columns]);
        }
        p
    }

    fn view(&self) -> Result<MatrixView<'_, T>, SimError> {
        MatrixView::new(&self.values, self.rows, self.columns, self.row_stride)
    }

    fn view_mut(&mut self) -> Result<MatrixViewMut<'_, T>, SimError> {
        MatrixViewMut::new(&mut self.values, self.rows, self.columns, self.row_stride)
    }
}

/// M, N, K each exceed DIM and none is a multiple of it, so every tail (M, N,
/// K, trailing scale block) is short in the same execution.
fn tail_shape(dim: usize) -> Shape {
    let (m, n, k) = (2 * dim + 3, dim + 5, 3 * dim + 7);
    Shape { m, n, k }
}

/// Deterministic signed operand; `seed`/`bound` keep A and B distinct.
fn activation_operand(
    rows: usize,
    cols: usize,
    row_stride: usize,
    seed: u32,
    bound: i8,
) -> Strided<ActivationValue> {
    let mut lcg = Lcg::new(seed);
    Strided::from_fn(
        rows,
        cols,
        row_stride,
        ActivationValue::default(),
        |_, _| parse_activation(i32::from(lcg.signed(-bound, bound))).expect("bounded activation"),
    )
}

fn weight_operand(
    rows: usize,
    cols: usize,
    row_stride: usize,
    seed: u32,
    bound: i8,
) -> Strided<i8> {
    let mut lcg = Lcg::new(seed);
    Strided::from_fn(rows, cols, row_stride, i8::MIN, |_, _| {
        lcg.signed(-bound, bound)
    })
}

/// Block size is not a multiple of DIM and K is not a multiple of the block
/// size, so the last block and the last fragment of every block are short.
fn block_scales(shape: Shape, dim: usize, row_stride: usize) -> KBlockScaleMatrix {
    KBlockScaleMatrix::from_fn_with_stride(
        shape.k,
        dim + 2,
        shape.n,
        row_stride,
        |block, column| ((5 * block + 3 * column) % 7) as i8 - 3,
    )
}

/// Row-stride padding for A, B, S, C respectively; `PACKED` is stride == cols.
struct Pad(usize, usize, usize, usize);
const PACKED: Pad = Pad(0, 0, 0, 0);

/// Runs one whole-matrix call and checks it against the independent golden.
fn run_full(
    simulator: &mut Im2pSimulator,
    shape: Shape,
    scaled: Option<VectorOp>,
    operation: VectorOp,
    pad: Pad,
    context: u64,
) -> Result<(), SimError> {
    let dim = simulator.dim();
    let a = activation_operand(shape.m, shape.k, shape.k + pad.0, 0x5eed_1234, 7);
    let b = weight_operand(shape.k, shape.n, shape.n + pad.1, 0x0bad_c0de, 6);
    let scales = scaled.map(|_| block_scales(shape, dim, shape.n + pad.2));
    let scales = scales.as_ref();
    let block = scales.map_or(shape.k, |matrix| matrix.block_size);
    let fragments = k_fragments(shape.k, block, dim);
    let packed = (a.packed(), b.packed());
    let expected = golden_output(
        &packed.0, &packed.1, shape, 0, shape.n, &fragments, scales, operation,
    );

    let mut out = Strided::new(shape.m, shape.n, shape.n + pad.3, i32::MIN);
    let work = MatmulWork {
        activations: a.view()?,
        weights: b.view()?,
        scales: scales.map(|matrix| matrix.view(0, shape.n, context)),
        vector_op: operation,
    };
    simulator.execute_matmul(&work, &mut out.view_mut()?)?;
    assert_matrix_eq(&out.packed(), &expected, shape.m, shape.n);
    Ok(())
}

#[test]
fn bypass_full_matrix_with_simultaneous_tails_matches_cpu() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let shape = tail_shape(simulator.dim());
    run_full(&mut simulator, shape, None, VectorOp::Bypass, PACKED, 0)
}

#[test]
fn multiply_full_matrix_with_block_tail_matches_cpu() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let shape = tail_shape(simulator.dim());
    let op = VectorOp::Multiply;
    run_full(&mut simulator, shape, Some(op), op, PACKED, 0x4d_554c_54)
}

#[test]
fn shift_full_matrix_with_block_tail_matches_cpu() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let shape = tail_shape(simulator.dim());
    let op = VectorOp::Shift;
    run_full(&mut simulator, shape, Some(op), op, PACKED, 0x53_4849_4654)
}

/// Every operand row is padded by a different amount, so no stride can be
/// inferred from another and a packed-layout assumption cannot pass.
#[test]
fn non_contiguous_activation_weight_output_and_scale_strides_match_cpu() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let shape = tail_shape(simulator.dim());
    let op = VectorOp::Multiply;
    let pad = Pad(3, 5, 7, 11);
    run_full(&mut simulator, shape, Some(op), op, pad, 0x53_5452_4944_45)
}

/// The full API must agree with the low-level `execute_tile` reference on a
/// case small enough for one tile, proving `execute_matmul` is the same
/// datapath rather than a separate arithmetic path.
#[test]
fn full_api_matches_low_level_tile_reference_on_small_case() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let (m, n, k) = (3.min(dim), 4.min(dim), 5.min(dim));
    let a = activation_operand(m, k, k, 0x5eed_1234, 7);
    let b = weight_operand(k, n, n, 0x0bad_c0de, 6);

    let mut reference = vec![0_i32; m * n];
    simulator.execute_tile(
        &TileRequest {
            activations: &a.packed(),
            weights: &b.packed(),
            scale_matrix: None,
            valid_m: m,
            valid_n: n,
            valid_k: k,
            k_start: 0,
            accumulate: false,
            vector_op: VectorOp::Bypass,
        },
        &mut reference,
    )?;

    let mut out = Strided::new(m, n, n, i32::MIN);
    let work = MatmulWork {
        activations: a.view()?,
        weights: b.view()?,
        scales: None,
        vector_op: VectorOp::Bypass,
    };
    simulator.execute_matmul(&work, &mut out.view_mut()?)?;
    assert_matrix_eq(&out.packed(), &reference, m, n);
    Ok(())
}

/// One simulator instance must serve Bypass, Multiply, Shift, and Bypass again
/// through `execute_matmul`, each matching its own golden.
#[test]
fn bypass_multiply_shift_reuse_one_simulator() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let shape = tail_shape(simulator.dim());
    for (index, op) in [
        VectorOp::Bypass,
        VectorOp::Multiply,
        VectorOp::Shift,
        VectorOp::Bypass,
    ]
    .into_iter()
    .enumerate()
    {
        let scaled = (op != VectorOp::Bypass).then_some(op);
        let context = 0x52_5553_45 + index as u64;
        run_full(&mut simulator, shape, scaled, op, PACKED, context)?;
    }
    Ok(())
}

/// Single-row and single-column matrices still exceed DIM in K, so degenerate
/// I/J traversal must not skip K fragmentation.
#[test]
fn degenerate_row_and_column_extents_match_cpu() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let k = 3 * dim + 7;
    for (m, n) in [(1, dim + 5), (2 * dim + 3, 1)] {
        let shape = Shape { m, n, k };
        run_full(&mut simulator, shape, None, VectorOp::Bypass, PACKED, 0)?;
    }
    Ok(())
}

#[test]
fn full_arithmetic_wraps_at_signed_i64_width_before_final_output() -> Result<(), SimError> {
    // Given contributions that overflow signed i64 in opposite directions.
    let shape = Shape { m: 2, n: 1, k: 2 };
    let activations = [
        parse_activation(1).expect("one is valid at every configured width"),
        parse_activation(1).expect("one is valid at every configured width"),
        parse_activation(-2).expect("negative two is valid at every configured width"),
        parse_activation(-2).expect("negative two is valid at every configured width"),
    ];
    let weights = [1_i8, 1];
    let scales = KBlockScaleMatrix::from_fn(shape.k, 1, shape.n, |_, _| 62);
    let fragments = k_fragments(shape.k, scales.block_size, 16);

    // When the independent golden accumulates with signed i64 wrapping.
    let exact = golden_output(
        &activations,
        &weights,
        shape,
        0,
        shape.n,
        &fragments,
        Some(&scales),
        VectorOp::Shift,
    );

    // Then positive overflow wraps to i64::MIN and negative overflow wraps to zero.
    assert_eq!(exact, [i64::MIN, 0]);
    let mut raw = [i32::MAX; 2];
    let work = MatmulWork {
        activations: MatrixView::new(&activations, shape.m, shape.k, shape.k)?,
        weights: MatrixView::new(&weights, shape.k, shape.n, shape.n)?,
        scales: Some(scales.view(0, shape.n, 0x57_52_41_50)),
        vector_op: VectorOp::Shift,
    };
    Im2pSimulator::new()?.execute_matmul(
        &work,
        &mut MatrixViewMut::new(&mut raw, shape.m, shape.n, shape.n)?,
    )?;
    assert_eq!(raw, [i32::MIN, 0]);
    Ok(())
}

#[test]
fn raw_full_output_saturates_positive_and_negative_i64_accumulators() -> Result<(), SimError> {
    // Given two rows whose independent i64 contributions exceed opposite i32 limits.
    let shape = Shape { m: 2, n: 1, k: 2 };
    let activations = [
        parse_activation(1).expect("one is valid at every configured width"),
        parse_activation(1).expect("one is valid at every configured width"),
        parse_activation(-2).expect("negative two is valid at every configured width"),
        parse_activation(-2).expect("negative two is valid at every configured width"),
    ];
    let weights = [1_i8, 1];
    let scales = KBlockScaleMatrix::from_fn(shape.k, 1, shape.n, |_, _| 30);
    let fragments = k_fragments(shape.k, scales.block_size, 16);
    let exact = golden_output(
        &activations,
        &weights,
        shape,
        0,
        shape.n,
        &fragments,
        Some(&scales),
        VectorOp::Shift,
    );
    assert_eq!(exact, [2_147_483_648, -4_294_967_296]);
    let mut raw = [0_i32; 2];
    let work = MatmulWork {
        activations: MatrixView::new(&activations, shape.m, shape.k, shape.k)?,
        weights: MatrixView::new(&weights, shape.k, shape.n, shape.n)?,
        scales: Some(scales.view(0, shape.n, 0x49_36_34)),
        vector_op: VectorOp::Shift,
    };

    // When FULL writes its final raw V2-layout destination.
    Im2pSimulator::new()?.execute_matmul(
        &work,
        &mut MatrixViewMut::new(&mut raw, shape.m, shape.n, shape.n)?,
    )?;

    // Then narrowing occurs once, at the final write, by saturation.
    assert_eq!(raw, [i32::MAX, i32::MIN]);
    println!("exact_i64={exact:?} raw_v2={raw:?}");
    Ok(())
}
