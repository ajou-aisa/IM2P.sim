use im2p_sim::{
    parse_activation, parse_weight, Im2pSimulator, MatmulWork, MatrixView, MatrixViewMut, SimError,
    VectorOp,
};

#[test]
fn changed_weights_replace_every_row_across_jobs_on_one_simulator() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let rows = 2 * simulator.dim();
    let activations = vec![parse_activation(1).unwrap(); rows];
    let mut weights = [parse_weight(1).unwrap()];
    let mut output = vec![0_i32; rows];
    for (job, expected) in [1, 2, -3, 4, 0, 7].into_iter().enumerate() {
        weights[0] = parse_weight(expected).unwrap();
        let work = MatmulWork {
            activations: MatrixView::new(&activations, rows, 1, 1)?,
            weights: MatrixView::new(&weights, 1, 1, 1)?,
            scales: None,
            vector_op: VectorOp::Bypass,
        };
        let stats =
            simulator.execute_matmul(&work, &mut MatrixViewMut::new(&mut output, rows, 1, 1)?)?;
        for (row, &actual) in output.iter().enumerate() {
            assert_eq!(actual, expected, "job={job} row={row}");
        }
        assert_eq!(stats.output_write_responses, u64::try_from(rows).unwrap());
        assert!((1..=2).contains(&stats.weight_read_requests));
    }
    Ok(())
}

#[test]
fn weight_reuse_remains_within_each_job_after_job_boundary_invalidation() -> Result<(), SimError> {
    let mut simulator = Im2pSimulator::new()?;
    let rows = 4 * simulator.dim();
    let activations = vec![parse_activation(1).unwrap(); rows];
    let mut weights = [parse_weight(1).unwrap()];
    let mut output = vec![0_i32; rows];
    for expected in [1, 2, -3] {
        weights[0] = parse_weight(expected).unwrap();
        let work = MatmulWork {
            activations: MatrixView::new(&activations, rows, 1, 1)?,
            weights: MatrixView::new(&weights, 1, 1, 1)?,
            scales: None,
            vector_op: VectorOp::Bypass,
        };
        let stats =
            simulator.execute_matmul(&work, &mut MatrixViewMut::new(&mut output, rows, 1, 1)?)?;
        assert_eq!(output, vec![expected; rows]);
        assert!(stats.lookahead_weight_reuse_hits > 0);
        assert!(stats.weight_read_requests < 4);
    }
    Ok(())
}
