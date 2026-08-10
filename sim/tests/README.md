# RTL integration tests

Cargo auto-discovers every `sim/tests/*.rs` file. Adding a test file requires
no aggregator or registry edit.

| File | Tests | Responsibility |
|---|---:|---|
| `rtl_basic.rs` | 6 | Bypass, signed/zero data, full tile, M/N/K tails |
| `rtl_scaling.rs` | 6 | Multiply, Shift, column scales, M-row sharing |
| `rtl_k_blocks.rs` | 5 | B8/B16/B32/B64, 9/17/128 blocks, boundaries |
| `rtl_scale_fetch.rs` | 8 | demand, current hit, context/reset, J stride/offset |
| `rtl_prefetch.rs` | 4 | next prefetch/hit, no duplicate, last block |
| `rtl_runtime.rs` | 2 | same-core runtime operation switching |
| `rtl_random.rs` | 3 | deterministic multi-block Multiply/Shift |
| `rtl_validation.rs` | 13 | ranges, layouts, buffers, response identity |
| `rtl_stats.rs` | 4 | cycle, fetch, work, utilization invariants |

`common/` contains shape/fragment types, scale matrix builders, deterministic
fixtures, independent CPU golden arithmetic, runner, and assertions.

## Add arithmetic test

Use `KBlockScaleMatrix::from_fn`, `run_case`, and compare returned
`output`/`expected`.

```rust
#[test]
fn my_k_block_case() -> Result<(), SimError> {
    let scales = KBlockScaleMatrix::from_fn(256, 32, 4, |block, column| {
        ((block + column) % 5) as i8 - 2
    });
    let result = run_case(&mut Im2pSimulator::new()?, Case {
        shape,
        activations: &activations,
        weights: &weights,
        scales: Some(&scales),
        column_offset: 0,
        valid_columns: 4,
        context: 1,
        operation: VectorOp::Multiply,
    })?;
    assert_eq!(result.output, result.expected);
    Ok(())
}
```

Add block arithmetic cases to `rtl_k_blocks.rs`. Add request/cache cases to
`rtl_scale_fetch.rs` or `rtl_prefetch.rs`. Validation-only cases belong in
`rtl_validation.rs`.
