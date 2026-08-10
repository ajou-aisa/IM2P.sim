use im2p_sim::KBlockScaleMatrixView;

#[derive(Clone, Debug)]
pub struct KBlockScaleMatrix {
    pub block_size: usize,
    pub total_k: usize,
    pub columns: usize,
    pub row_stride: usize,
    pub values: Vec<i8>,
}

impl KBlockScaleMatrix {
    pub fn from_fn(
        total_k: usize,
        block_size: usize,
        columns: usize,
        mut value: impl FnMut(usize, usize) -> i8,
    ) -> Self {
        Self::from_fn_with_stride(total_k, block_size, columns, columns, |block, column| {
            value(block, column)
        })
    }

    pub fn from_fn_with_stride(
        total_k: usize,
        block_size: usize,
        columns: usize,
        row_stride: usize,
        mut value: impl FnMut(usize, usize) -> i8,
    ) -> Self {
        assert!(total_k > 0);
        assert!(block_size > 0);
        assert!(columns > 0);
        assert!(row_stride >= columns);

        let block_count = total_k.div_ceil(block_size);
        let mut values = vec![0_i8; block_count * row_stride];
        for block in 0..block_count {
            for column in 0..columns {
                values[block * row_stride + column] = value(block, column);
            }
        }
        Self {
            block_size,
            total_k,
            columns,
            row_stride,
            values,
        }
    }

    pub fn get(&self, block: usize, column: usize) -> i8 {
        self.values[block * self.row_stride + column]
    }

    pub fn row(&self, block: usize) -> &[i8] {
        let start = block * self.row_stride;
        &self.values[start..start + self.columns]
    }

    pub fn block_count(&self) -> usize {
        self.total_k.div_ceil(self.block_size)
    }

    pub fn as_slice(&self) -> &[i8] {
        &self.values
    }

    pub fn view(
        &self,
        column_offset: usize,
        valid_columns: usize,
        context: u64,
    ) -> KBlockScaleMatrixView<'_> {
        KBlockScaleMatrixView {
            values: &self.values,
            block_size: self.block_size,
            total_k: self.total_k,
            columns: self.columns,
            row_stride: self.row_stride,
            column_offset,
            valid_columns,
            context,
        }
    }
}
