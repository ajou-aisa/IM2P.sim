pub fn assert_matrix_eq<T>(actual: &[i32], expected: &[T], rows: usize, columns: usize)
where
    T: Copy + Into<i64>,
{
    assert_eq!(actual.len(), rows * columns);
    assert_eq!(expected.len(), rows * columns);
    for row in 0..rows {
        for column in 0..columns {
            let index = row * columns + column;
            assert_eq!(
                i64::from(actual[index]),
                expected[index].into(),
                "mismatch at row={row} column={column}"
            );
        }
    }
}
