//! Valid-region-only output writeback.
//!
//! `execute_matmul` must write exactly `rows * columns` logical elements of the
//! `MatrixViewMut` destination and nothing else. Everything outside that region
//! - the per-row stride gutter, the storage before the first row, and the tail
//! after the last row - must retain its pre-call guard value, including when
//! M, N, and K all have tails and the destination is non-contiguous.
//!
//! Absent-until-implemented API: `MatrixView::new(values, rows, columns,
//! row_stride)`, the `MatrixViewMut` equivalent, `MatmulWork { activations,
//! weights, scales, vector_op }`, and `Im2pSimulator::execute_matmul(
//! &MatmulWork, &mut MatrixViewMut<i32>)`.

pub mod common;

use common::{
    assert_matrix_eq, golden_output, k_fragments, structured_activations, structured_weights,
    KBlockScaleMatrix, Shape,
};
use im2p_sim::{Im2pSimulator, MatmulWork, MatrixView, MatrixViewMut, SimError, VectorOp};

const GUARD: i32 = -0x5EED_BEEF;

/// Destination buffer with a leading guard prefix, a per-row stride gutter, and
/// a trailing guard tail. `offset` is where the logical (0, 0) element lives,
/// and the view is handed the whole remainder so an over-long write is visible.
struct GuardedOutput {
    storage: Vec<i32>,
    offset: usize,
    rows: usize,
    columns: usize,
    row_stride: usize,
}

impl GuardedOutput {
    fn new(rows: usize, columns: usize, row_stride: usize, prefix: usize, tail: usize) -> Self {
        assert!(row_stride >= columns);
        Self {
            storage: vec![GUARD; prefix + rows * row_stride + tail],
            offset: prefix,
            rows,
            columns,
            row_stride,
        }
    }

    /// The only index set the API is permitted to write.
    fn is_valid_index(&self, index: usize) -> bool {
        if index < self.offset {
            return false;
        }
        let local = index - self.offset;
        local / self.row_stride < self.rows && local % self.row_stride < self.columns
    }

    fn packed(&self) -> Vec<i32> {
        let mut packed = Vec::with_capacity(self.rows * self.columns);
        for row in 0..self.rows {
            let start = self.offset + row * self.row_stride;
            packed.extend_from_slice(&self.storage[start..start + self.columns]);
        }
        packed
    }

    fn view_mut(&mut self) -> Result<MatrixViewMut<'_, i32>, SimError> {
        let region = &mut self.storage[self.offset..];
        MatrixViewMut::new(region, self.rows, self.columns, self.row_stride)
    }

    /// Fails on the first element the API touched outside the valid region.
    fn assert_guards_intact(&self) {
        for (index, value) in self.storage.iter().enumerate() {
            if self.is_valid_index(index) {
                continue;
            }
            let local = index.wrapping_sub(self.offset);
            assert_eq!(
                *value,
                GUARD,
                "writeback escaped the valid region at storage[{index}] \
                 (prefix, row {} column {} gutter, or tail)",
                local / self.row_stride,
                local % self.row_stride
            );
        }
    }
}

/// M, N, and K each exceed DIM and none is a multiple of it.
fn tail_shape(dim: usize) -> Shape {
    Shape {
        m: 2 * dim + 3,
        n: dim + 5,
        k: 3 * dim + 7,
    }
}

/// Executes one whole-matrix call into a guarded destination and asserts both
/// exact values inside the region and untouched guards outside it.
fn run_guarded(
    simulator: &mut Im2pSimulator,
    shape: Shape,
    scales: Option<&KBlockScaleMatrix>,
    operation: VectorOp,
    mut output: GuardedOutput,
    context: u64,
) -> Result<GuardedOutput, SimError> {
    assert_eq!((output.rows, output.columns), (shape.m, shape.n));
    let activations = structured_activations(shape);
    let weights = structured_weights(shape);
    let block_size = scales.map_or(shape.k, |matrix| matrix.block_size);
    let expected = golden_output(
        &activations,
        &weights,
        shape,
        0,
        shape.n,
        &k_fragments(shape.k, block_size, simulator.dim()),
        scales,
        operation,
    );

    let work = MatmulWork {
        activations: MatrixView::new(&activations, shape.m, shape.k, shape.k)?,
        weights: MatrixView::new(&weights, shape.k, shape.n, shape.n)?,
        scales: scales.map(|matrix| matrix.view(0, shape.n, context)),
        vector_op: operation,
    };
    simulator.execute_matmul(&work, &mut output.view_mut()?)?;

    assert_matrix_eq(&output.packed(), &expected, shape.m, shape.n);
    output.assert_guards_intact();
    Ok(output)
}

/// `row_stride == columns`, so only the prefix and tail guard the region.
#[test]
fn contiguous_output_leaves_prefix_and_tail_intact() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let shape = tail_shape(simulator.dim());
    let output = GuardedOutput::new(shape.m, shape.n, shape.n, 4, 9);
    run_guarded(&mut simulator, shape, None, VectorOp::Bypass, output, 0)?;
    Ok(())
}

/// A gutter after every logical row catches any full-stride row write.
#[test]
fn strided_output_leaves_row_gutters_intact() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let shape = tail_shape(simulator.dim());
    let output = GuardedOutput::new(shape.m, shape.n, shape.n + 6, 3, 13);
    run_guarded(&mut simulator, shape, None, VectorOp::Bypass, output, 0)?;
    Ok(())
}

/// A DIM-wide accumulator row must not be flushed whole: with `n` below DIM,
/// the padding columns of the last J tile must stay guarded.
#[test]
fn column_tail_padding_is_not_written() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let shape = Shape {
        m: dim + 3,
        n: dim - 1,
        k: 2 * dim + 5,
    };
    let output = GuardedOutput::new(shape.m, shape.n, shape.n + dim, 2, dim);
    run_guarded(&mut simulator, shape, None, VectorOp::Bypass, output, 0)?;
    Ok(())
}

/// A short final I tile must not write the accumulator rows belonging to the
/// padded remainder of that tile; the tail holds those phantom rows.
#[test]
fn row_tail_padding_is_not_written() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let shape = Shape {
        m: dim + 1,
        n: dim + 5,
        k: 2 * dim + 5,
    };
    let row_stride = shape.n + 4;
    let output = GuardedOutput::new(shape.m, shape.n, row_stride, 1, dim * row_stride);
    run_guarded(&mut simulator, shape, None, VectorOp::Bypass, output, 0)?;
    Ok(())
}

#[test]
fn scaled_writeback_respects_valid_region() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let shape = tail_shape(dim);
    let scales = KBlockScaleMatrix::from_fn_with_stride(
        shape.k,
        dim + 2,
        shape.n,
        shape.n + 5,
        |block, column| ((5 * block + 3 * column) % 7) as i8 - 3,
    );

    for (index, operation) in [VectorOp::Multiply, VectorOp::Shift]
        .into_iter()
        .enumerate()
    {
        let output = GuardedOutput::new(shape.m, shape.n, shape.n + 7, 5, 17);
        run_guarded(
            &mut simulator,
            shape,
            Some(&scales),
            operation,
            output,
            0x57_4249_4b + index as u64,
        )?;
    }
    Ok(())
}

/// Consecutive calls into the same destination must overwrite the valid region
/// completely - no residue from the previous result may survive - while the
/// guards stay intact across both calls.
#[test]
fn repeated_calls_overwrite_region_without_touching_guards() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let dim = simulator.dim();
    let shape = tail_shape(dim);
    let scales = KBlockScaleMatrix::from_fn(shape.k, dim + 2, shape.n, |block, column| {
        ((block + column) % 5) as i8 - 2
    });

    let first = run_guarded(
        &mut simulator,
        shape,
        Some(&scales),
        VectorOp::Multiply,
        GuardedOutput::new(shape.m, shape.n, shape.n + 3, 6, 11),
        0x52_4550_31,
    )?;

    // Same destination buffer, different operation: every logical element must
    // be replaced, so the Bypass golden must hold with no Multiply residue.
    let second = run_guarded(&mut simulator, shape, None, VectorOp::Bypass, first, 0)?;
    second.assert_guards_intact();
    Ok(())
}
