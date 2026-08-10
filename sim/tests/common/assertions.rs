pub fn assert_matrix_eq(actual: &[i32], expected: &[i32], rows: usize, columns: usize) {
    assert_eq!(actual.len(), rows * columns);
    assert_eq!(expected.len(), rows * columns);
    for row in 0..rows {
        for column in 0..columns {
            let index = row * columns + column;
            assert_eq!(
                actual[index], expected[index],
                "mismatch at row={row} column={column}"
            );
        }
    }
}
