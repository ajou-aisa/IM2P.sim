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
| `rtl_cycle_accounting.rs` | 2 | exact edge/eval/pulse semantics and concurrent A/W/S/C responses |
| `rtl_random.rs` | 3 | deterministic multi-block Multiply/Shift |
| `rtl_validation.rs` | 14 | ranges, layouts, C-facing scale validation, buffers, response identity |
| `rtl_stats.rs` | 4 | cycle, fetch, work, utilization invariants |
| `rtl_full_matmul.rs` | 7 | oversized full matrices, tails, strides, CPU golden |
| `rtl_writeback.rs` | 6 | valid-region writes and guard/padding preservation |
| `rtl_work_scheduler.rs` | 2 | real RTL multi-I/J/K traversal and simulator reuse |
| `rtl_memory_provider.rs` | 2 | address-backed strided A/W/C and channel counters |
| `rtl_weight_preload.rs` | 2 | inactive-bank preload overlap and bank accounting |
| `rtl_work_stats.rs` | 1 | RTL wait/compute/drain/preload/overlap counters |
| `rtl_async_stripes.rs` | 7 | publish gating, FIFO pressure, readiness, errors, equivalence |
| `rtl_stripe_completion.rs` | 2 | writeback barrier and ordered completion contexts |
| `rtl_async_output_tiles.rs` | 1 | async N-tile output column offsets |
| `rtl_stripe_lookahead.rs` | 5 | publish-triggered A/W/S preparation, delayed/padded and partial stripes, and resident-resource reuse |

`rtl_stripe_lookahead.rs` is auto-discovered with the other `sim/tests/*.rs`
integration tests. Its cases drive the actual Verilated core and cover
publish-to-prepare timing signals, delayed publication with padded A/W/C
layouts, partial weight preparation without refetch, scale misses, and the
single immediate lookahead/resident-resource reuse path. The documentation
does not treat those cases as a separate registry or report a runtime result.

`c_api_smoke.c` is compiled as strict C11 by `make c-api-test`, linked against
the Rust static library, and run through both blocking and cooperative APIs.
The table contains 19 auto-discovered actual-RTL Rust test files and 87 tests;
the C API process check is an additional external-surface acceptance.

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

## Add scheduler/provider coverage

High-level integration tests must exercise the Verilated `IM2PCore`; do not
create a Rust mirror of `MatmulScheduler` or `WorkScheduler`.

- Full-matrix cases construct checked `MatrixView`, `MatrixViewMut`, and
  `MatmulWork` values, then call `execute_matmul` once.
- Stripe cases call `begin_striped_matmul`, publish only CPU-complete rows,
  service pending A/C events, advance logical cycles with `progress`, and
  consume ordered completion events.
- Tests use event/state handshakes. OS threads, async runtimes, sleeps,
  wall-clock deadlines, and Rust I/J/K tiling loops are forbidden.
- `npu_ready()` means RTL publish FIFO capacity. `host_available()` means
  published host data remains associated with incomplete RTL work.
- Output completion is observable only after the matching C request tag is
  acknowledged.

Run both generated dimensions for cross-DIM changes:

```bash
make sim-test-int8x16
make sim-test-int8x32
```
