# IM2P RTL simulator

This crate drives regenerated BSC INT8 RTL through Verilator:

```text
BSV -> Verilog -> Verilator C++ -> C ABI -> Rust
```

```bash
make sim-test-int8x16
make sim-test-int8x32
```

Each target regenerates Verilog and its Verilated model before running every
auto-discovered integration test in `sim/tests/*.rs`.

## Host-owned K-block scale matrix

For `W: K × J` and block size `B`, scale shape is:

```text
S: ceil(K / B) × J
W[k,j] uses S[floor(k / B),j]
```

Rust exposes a borrowed `KBlockScaleMatrixView`:

```rust
KBlockScaleMatrixView {
    values,
    block_size,
    total_k,
    columns,
    row_stride,
    column_offset,
    valid_columns,
    context,
}
```

The matrix remains in host memory. Its C ABI mirror carries the same pointer
and layout metadata. The bridge receives this view only during a synchronous
request-service call, copies one requested row, and never retains the pointer.
`context` is the cache generation key: callers must change it whenever matrix
contents or the effective stride/offset/column mapping changes.
Host address calculation is:

```text
block * row_stride + column_offset
```

The requested `valid_columns` values are copied and remaining RTL columns are
zero padded.

## RTL scale streaming

`IM2PCore` computes `block = kStart / blockSize`. A scaled execution must stay
inside one block. The Core exposes a context-tagged demand/prefetch request and
accepts one matching row response.

Only two rows are stored:

```text
current: (context, block, S[block,:])
next:    (context, block + 1, S[block + 1,:])
```

Same-block fragments hit current. Sequential blocks promote next. A demand
miss stalls execution until its row arrives. Last block produces no prefetch.
Reset or context change invalidates both rows. There is no synthesis-time
limit on the number of K-scale blocks.

Current scheduling fully drains each execution before another starts.
`executionScaleRow` is latched before activation input and remains fixed
through all staggered column outputs. Therefore each partial column `j` uses
exactly `S[block,j]`; prefetch cannot overwrite an in-flight execution.

`VectorBypass` needs no scale view, emits no scale request, and does not
invalidate cached rows.

## Statistics

`TileStats` retains weight, compute, total-cycle, useful MAC/Ops, rate, and
utilization fields. `ScaleFetchStats` reports tile-local:

```text
demand_requests
prefetch_requests
current_hits
next_hits
demand_misses
rows_received
scale_transfer_cycles
scale_wait_cycles
```

`scale_transfer_cycles` counts accepted RTL row-response cycles.
`scale_wait_cycles` counts RTL cycles with a pending scaled execution. Host
pointer dereference time is not an RTL cycle.

## Matrix and cooperative stripe APIs

Blocking Rust:

```rust
simulator.execute_matmul(&work, &mut output)?;
```

`MatmulWork` borrows strided activation, weight, optional scale views.
`MatrixViewMut` borrows strided output. RTL supplies every I/J/K fragment and
A/W/S/C address; Rust only resolves current requests and advances the model.

Cooperative Rust:

```text
begin_striped_matmul(static W/S/C metadata)
publish_stripe(completed CPU activation rows)
progress(logical cycle budget)
pending_activation_row / supply_activation_row
pending_output_row / take_output_row / acknowledge_output_row
poll_completed
finish
```

`npu_ready()` reports RTL publication FIFO readiness. `host_available()`
reports published host data still owned by an incomplete stripe. Completion is
observable only after all matching C writes are acknowledged. No thread,
runtime, sleep, polling delay, or CPU wall clock participates in correctness.

## C ABI and pointer ownership

Public header: `sim/include/im2p_sim.h`.

Full-matrix pointers are borrowed only during `im2p_execute_matmul`. Striped
W/S/output pointers remain valid through `im2p_finish_stream` or
`im2p_destroy_stream`; each activation stripe pointer remains valid until its
completion event. The C bridge copies descriptors, never retains temporary
descriptor pointers, and rejects null/invalid packed-weight layouts.

`WorkStats` reports request counts, provider wait cycles, compute/drain cycles,
weight preload, current-next overlap by resource, fragments/tiles/stripes, and
bank activations. Values are RTL counter deltas; they do not include host
wall-clock time.
