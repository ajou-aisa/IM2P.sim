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

## Logical-cycle model

One logical cycle is one complete simulated RTL clock period containing exactly
one rising edge. Reset edges establish initial state and leave the logical
counter at zero. A raw command/response pulse consumes one logical cycle;
combinational `eval()` and request/status getters consume none. The provider may
stage simultaneously ready A/W/S/C responses and commit them on the same edge,
matching independent RTL channels rather than serializing host function calls.

`progress(cycle_budget)` and `im2p_progress_stream(..., cycle_budget)` advance
exactly `cycle_budget` logical cycles in every scheduler state. A zero budget is
observational and does not service or clock the model. Internal blocking-loop
limits are watchdog **iterations**; their normal service iteration commits one
staged provider edge, while setup/final acknowledgement pulses are separate
logical cycles and are included by cycle-derived statistics.

Logical cycles are not host wall-clock time or physical time. The simulator has
no DRAM, cache, scratchpad, DMA, interconnect, CPU execution, or clock-frequency
model. Host memory dereferences are functional and zero-time, so these counters
cannot establish CPU/NPU common time, nanoseconds, GHz, Fmax, or silicon
performance. Verilator runtime duration is only host simulation cost.

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

`WorkStats` reports logical RTL counter deltas. In particular,
`cross_stripe_overlap_cycles` counts a cycle only when the
current engine executes while any next-stripe A/W/S fetch or PE preload is
active; `activation_overlap_cycles`, `weight_overlap_cycles`, and
`scale_overlap_cycles` instead report current-work fragment preparation
overlapping compute; they are not components of the cross-stripe aggregate.
Host wall-clock time is excluded. `stripe_host_wait_cycles` is the wait after a current-stripe
transition when the next stripe has not been published; the A/W/S/C channel
waits are reported separately.
`lookahead_ready_cycle` records when first-fragment A/W/S staging and required
PE-bank preload or reuse are all ready.

Lookahead timestamps are per-matmul RTL cycles.
`lookahead_publish_cycle` is the cycle where the second stripe publication is
accepted by RTL. The difference from that cycle to the first nonzero of
`lookahead_first_activation_cycle`, `lookahead_first_weight_cycle`,
`lookahead_weight_preload_cycle`, and `lookahead_scale_cycle` is the
publish-to-first-prepare latency. `lookahead_start_cycle -
current_stripe_completion_cycle` is the completion-to-next-start transition.
`lookahead_weight_requests` versus `lookahead_weight_reuse_hits`, and
`lookahead_scale_requests` versus `lookahead_scale_reuses`, distinguish host
fetches from exact reuse.

The original fixed-size `im2p_work_stats_t` and its entry points retain their
binary layout. Lookahead telemetry is available through
`im2p_work_stats_extended_t`, `im2p_execute_matmul_extended`, and
`im2p_finish_stream_extended`.

## Matrix and cooperative stripe APIs

Blocking Rust:

```rust
simulator.execute_matmul(&work, &mut output)?;
simulator.execute_matmul_layout(&work, &mut output, layout)?;
```

`MatrixView::new(values, rows, columns, row_stride)` and
`MatrixViewMut::new(...)` define A/W/C layouts. Each stride is in elements and
must be at least the logical column count (A: K, W/C: N); the final readable or
writable element is `(rows - 1) * row_stride + columns - 1`. `MatmulLayout`
selects `tile_i_rows` and `tile_j_columns`, each in `1..=sim.dim()`.
`MatmulWork` borrows strided activation, weight, optional scale views, and RTL
supplies every I/J/K fragment and A/W/S/C address.

Cooperative Rust:

```text
begin_striped_matmul[_layout](static W/S/C metadata)
publish_stripe[_layout](completed CPU activation rows)
progress(logical cycle budget)
pending_activation_row / supply_activation_row
pending_output_row / take_output_row / acknowledge_output_row
poll_completed
finish
```

`StripeLayout` supplies W/C row strides and I/J tiling; the default layout is
packed. `publish_stripe_layout` supplies the activation row stride, which must
be at least K. `publish_stripe` is an activation-availability event, not merely
a queue push: acceptance enables immediate next-stripe A/W/S staging and
resident-weight reuse or inactive-bank preload while the one current WS/RC
engine remains ordered. There is one current and one prepared lookahead;
deeper accepted stripes remain FIFO. A lookahead does not write C or update an
Accumulator until promotion. Full-matrix execution uses the same RTL path with
all A/W/S/C regions available at start.

```text
CPU: publish s0 ----------------------- publish s1 ------------------- retain s1 A
NPU: [current WS/RC] ================== [prepare s1: A/W/S + PE bank] -- promote --> [s1 WS/RC]
                                         one engine; output/accumulator only after promotion
```

`npu_ready()` reports RTL publication FIFO readiness. `host_available()`
reports published host data still owned by an incomplete stripe. No activation
read is issued before its stripe is accepted. Completion is observable only
after all matching C writes are acknowledged. No thread, runtime, sleep,
polling delay, or CPU wall clock participates in correctness.

## C ABI and pointer ownership

Public header: `sim/include/im2p_sim.h`. C status values distinguish malformed
contracts (`IM2P_INVALID_LAYOUT`), an already-owned simulator
(`IM2P_UNFINISHED_STREAM`), publication backpressure/duplicate/late events, and
generic runtime execution failure (`IM2P_ERROR`); runtime failures are not
collapsed into layout errors.

`im2p_execute_matmul` borrows full-matrix pointers only for the call.
`im2p_begin_striped_matmul_ex` returns status and writes the stream pointer;
the non-`_ex` form returns that pointer directly. Zero `tile_i_rows` or
`tile_j_columns` in C selects the simulator dimension; nonzero values must fit
it. A/W/C strides are element strides and may include padding: full A stride
must be at least K, W and C strides at least N; striped W/C use the same
contracts and each published stripe A stride must be at least K.

For a stream, W/S/C pointers and their strides remain valid through
`im2p_finish_stream` or `im2p_destroy_stream`. A successful
`im2p_publish_stripe` permits RTL reads on the next logical cycle, so its A
pointer and activation stride must remain valid through the matching completion
returned by `im2p_poll_completed`. The bridge copies descriptors but retains
these borrowed regions for servicing; callers must not move, free, or mutate
them incompatibly before their lifetime ends.
The stream retains shared simulator ownership, so destroying the originating
`im2p_sim_t` handle does not invalidate an in-flight stream.

The PE array still has two weight banks. A nonresident lookahead W is fetched
from the host into external staging outside the PE banks, then preloaded only
into the inactive bank at the safe point. Exact resident match (base, stride, J and K
ranges) reuses a bank without fetch/preload only when the final-current-work
safety predicate already holds at capture; otherwise preparation conservatively
uses host fetch.
If only part of W arrives before promotion, received rows feed the promoted
inactive-bank load directly and only missing rows generate host requests.
A scaled lookahead similarly
reuses only an exact `(context + J offset, block)` current/next cache row;
otherwise it fetches S. Promotion latches an immutable scale execution
snapshot, preventing later responses from changing in-flight staggered output.
