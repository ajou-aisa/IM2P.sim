# Optional Gemmini C++ frontend

`frontend/include/im2p_gemmini_frontend.hpp` is an optional, simulator-owned
adapter from `ggml_gemmini_args_t` to the existing IM2P C ABI. It does not alter
the C ABI, stats layouts, RTL, or numerical behavior. The default IM2P build does
not include or require llama headers.

The caller surface has three operations in `im2p::gemmini`:

- `execute(args[, mode, options])` creates a `Run` and snapshots every args
  object field before returning. The args object is needed only during this
  call. Every referenced backing buffer remains borrowed and must stay alive
  and immutable through `fence` or `Run` destruction; operand and metadata
  buffers are not copied.
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
`C[M,N]`, no transpose/bias/activation/float scaling) executes. The frontend
classifies H1, H2, HP1, HP2, channel-direct, and channel-sidecar contracts using
the authoritative Gemmini `has_*`/route helpers, preserves their metadata, and
returns `unsupported_route`; it never transposes, unpacks, dequantizes, or makes
a full operand copy.

Pipeline mode has one dedicated worker that exclusively owns all raw simulator
and stream calls. The caller queue is bounded by `Options::queue_capacity` and
returns frontend backpressure. Raw `IM2P_BACKPRESSURE` is handled internally by
retaining the same dense event metadata, advancing one logical cycle, polling
completion, and retrying. The worker never waits while raw work remains in
flight. Permanent lack of logical completion terminates at a conservative
logical bound (at least 65536 cycles, or a larger configured
`Options::max_stalled_cycles`), which covers the proven M=1,N=64,K=4096 RTL run.
The watchdog resets only when the worker's monotonic matched-completion
counter advances; caller refills cannot conceal progress by changing queue
occupancy. No wall-clock sleep participates in scheduling.
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
