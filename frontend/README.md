# Optional Gemmini C++ frontend

`frontend/include/im2p_gemmini_frontend.hpp` is an optional, simulator-owned
adapter from `ggml_gemmini_args_t` to the existing IM2P C ABI. It does not alter
the C ABI, stats layouts, RTL, or numerical behavior. The default IM2P build does
not include or require llama headers.

The caller surface has three operations in `im2p::gemmini`:

- `execute(args[, mode, options])` creates a `Run` and copies only the selected
  scalar values and pointer identities listed below. It does not snapshot the
  complete `ggml_gemmini_args_t`. The args object itself is needed only during
  this call; no backing buffer is copied.
- `submit_stripe(run, event)` validates ordered bounds and the first-event
  `run_id`, then copies only `run_id`, `stripe_id`, `slot`, `row_begin`, and
  `row_end`. It does not retain timing/profiling fields, the event, its
  `rmd_packet`, or ExSIA slot ownership. A frontend
  `backpressure` result means the event was not accepted; the caller must retain
  its own data and retry `submit_stripe` after capacity is released.
- `fence(run)` closes submission and returns the sticky final status plus the
  exact `im2p_work_stats_extended_t` produced by the C ABI. It is idempotent and
  safe under concurrent calls.

These three high-level operations are separate from the complete raw low-level
C ABI sequence owned by the worker:

- `execute_matmul` for full-matrix work;
- `begin_striped_matmul` to create a raw stream;
- `publish_stripe` to publish activation availability;
- `progress_stream` to advance logical RTL cycles;
- `poll_completed` to release completed stripe backing storage;
- `finish_stream` to complete the stream and collect statistics.

Only raw-compatible `q8_h0` (`A[M,K]` INT8, `B[K,N]` INT8, full INT32
`C[M,N]`, no transpose, `repeating_bias`, `D` bias, activation, or float
scaling) executes. For full mode, A and B are borrowed input regions that must
remain valid and immutable for the Run lifetime. In pipeline mode, B has the
same rule; an A row may be populated before its stripe is accepted, but the
submitted bytes must then remain valid and immutable through `fence` or Run
destruction. C is a borrowed output region that must remain
valid and exclusively writable by the Run. The caller must not read or write C
concurrently before `fence` returns or the Run is destroyed. The frontend
classifies H1, H2, HP1, HP2,
channel-direct, and channel-sidecar contracts using the authoritative Gemmini
`has_*`/route helpers, preserves selected metadata, and returns
`unsupported_route`; it never transposes, unpacks, dequantizes, or makes a full
operand copy.

The exact scalar selection is `I`, `J`, `K`, `sA`, `sB`, `sC`, `sD`,
`activation_row_offset`, `activation_rows_per_stripe`, `block_size_k`, `tile_I`,
`tile_J`, `tile_K`, `blocks_K`, `blocks_J`, `blocks_I`, `stripe_J`,
`q8_h1_block_count`, `q8_h1_rows`, `blocks_per_row`, `q8_h2_block_count`,
`q8_h2_blocks_per_row`, `q8_hp1_block_count`, `q8_hp1_blocks_per_row`,
`q8_hp2_block_count`, `q8_hp2_blocks_per_row`, `weight_channel_scale_count`,
`q8_channel_row_stride`, `q8_channel_row_count`, `col_stride_f_out`,
`stride_f_out`, `weight_format`, `scale_B`, `scale_D`,
`scale`, `bert_scale`, `transpose_A`, `transpose_B`, `full_C`, `low_D`,
`repeating_bias`, `weight_i8_scale_active`, and `act`. The exact pointer
selection is `A`, `B`, `C`, `D`, `A_fp32`, `B_fp32`, `B_blocks`, `B_scales`,
`weight_channel_scales`, `q8_channel_row_base`, `q8_h1_blocks`,
`q8_h2_blocks`, `q8_hp1_blocks`, `q8_hp2_blocks`, `c_b`, `s_rf`, `R`,
`s_rf_stripe`, `R_stripe`, `f_out`, `model_arch`,
`exsia_stripe_ready_sink`, and `unpacked.blocks`. Pointer selections belonging
to unsupported routes are retained only for route/contract inspection and are
not processed.

`activation_row_offset` is copied as metadata only: the raw ABI descriptor does
not consume it, so `A` must already point at the first activation row to execute.
`tile_I` and `tile_J` are Gemmini tile counts; each is multiplied by `DIM` and
then clamped to both the problem extent and one RTL `DIM` tile (zero means one
count, and multiplication overflow is rejected). `tile_K` is copied metadata
only and does not alter raw-ABI reduction tiling; K execution is determined by
`K` and `block_size_k`.

Pipeline mode has one dedicated worker that exclusively owns all raw simulator
and stream calls. The caller queue is bounded by `Options::queue_capacity` and
returns frontend backpressure. Raw `IM2P_BACKPRESSURE` is handled internally by
retaining the same dense event metadata, advancing one logical cycle, polling
completion, and retrying. One logical cycle is one complete RTL clock period
with one rising edge; `im2p_progress_stream(stream, 1)` advances exactly that
one period in every scheduler state, including simultaneous ready A/W/S/C
responses. The worker never waits while raw work remains in
flight. Permanent lack of logical completion terminates at a conservative
logical bound (at least 65536 cycles, or a larger configured
`Options::max_stalled_cycles`), which covers the proven M=1,N=64,K=4096 RTL run.
The watchdog resets only when the worker's monotonic matched-completion
counter advances; caller refills cannot conceal progress by changing queue
occupancy. `max_stalled_cycles` therefore bounds worker progress iterations
without a matched completion (each such progress call is exactly one logical
cycle), not elapsed host time. No wall-clock sleep participates in scheduling.
The model has no CPU execution, DRAM/cache/scratchpad/DMA/interconnect, or clock
frequency, and host pointer access is zero-time. Frontend/Verilator runtime and
RTL counters cannot establish CPU/NPU common time, physical ns/GHz/Fmax, or
silicon performance.
An incomplete fence fails deterministically and destroys rather than finishes
the raw stream.

## Build and verification

The standalone default remains dependency-free:

```sh
make check
```

Build the focused adapter contract tests only when the sibling Gemmini checkout
and its generated parameter headers are available:

```sh
make gemmini-frontend-test
# or include it in the normal check dependency graph
make check ENABLE_GEMMINI_FRONTEND=1
```

Run the DIM16 full/stripe RTL golden and lookahead check with:

```sh
make gemmini-frontend-real-test
make gemmini-frontend-real-test GEMMINI_FRONTEND_DIM=32
```

Override `GEMMINI_ROOT` and `GEMMINI_PARAMS_ROOT` for non-sibling layouts.
The public header's forward-declaration test is intentionally compiled without
any Gemmini include path.
