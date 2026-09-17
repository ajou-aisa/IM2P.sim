# Value-free Gemmini HP1 cycle model

## Scope and certification

`sim/cycle/` implements a separate single-GEMM logical-cycle model. The public C
interface is `sim/include/im2p_cycle_model.h`; its ABI version is 1. It uses the
existing resolved Gemmini hardware contract and pure schedule planner. There is
no numerical runtime flag, numerical ABI change, tensor pointer, MAC/SCU result
computation, generated RTL class, or Verilator dependency in this library.

For **rtl-regression revision 1**, the preserved current integrated RTL corpus
matches **268/268 logical-work cases**: start/done convention, elapsed cycles,
work/loop counts and load/store/scale request/response counts are exact. The
maximum absolute cycle difference is **0**. The observed per-cycle event-type
multisets also match in all 268 cases. This does not mean every internal RTL
signal, arbitrary input, timing profile, or submission policy has been certified.

| Profile | Exact logical-work cases |
|---|---:|
| a4w4-d16-hp1 | 52/52 |
| a4w4-d32-hp1 | 41/41 |
| a4w4-d64-hp1 | 41/41 |
| a8w8-d16-hp1 | 52/52 |
| a8w8-d32-hp1 | 41/41 |
| a8w8-d64-hp1 | 41/41 |

These are estimates by default and may be called **RTL-exact for this validated
case/timing profile** only with the corresponding comparison evidence. The CLI
therefore returns `classification: cycle model result`, not a universal exactness
flag. The numerical Verilator backend remains the independent numerical and
logical-cycle golden.

This version covers a drained single-work entry and the regression fixture's
serialized descriptor/release policy. It models simultaneous load/execute/store
activity, pipelined command issue, queue credits, resource arbitration, response
reordering and final-store stalls inside that policy. It does **not** expose
arbitrary asynchronous submission of two independent logical works or a shared
multi-request system simulator. The existing numerical dual-loop/slot-overlap
fixture is retained and rerun, but is separate from the 268-case cycle-model
certificate. Its two concurrently accepted logical works are not an additional
cycle-model case. Two model handles are independent, not a shared hardware core.

## Dependency direction and value independence

```text
resolved profile + shared pure planner
                   |                 |
                   v                 v
       numerical RTL backend     cycle backend
       values + Verilator        metadata + timing events
       numerical output          cycles and counters
```

The cycle build compiles `sim/common/gemmini_schedule.cpp` directly. It does not
link the numerical C ABI runtime, frontend, ggml, BSC, SBT, or a Verilated model.
The numerical backend does not import the cycle engine. The optional Python CLI
resolves profiles with the existing `scripts/gemmini_resolve_profile.py`; it is
not a second hardware resolver.

Every field of `im2p_cycle_request_t` is an unsigned scalar. Neither the C nor
JSON interface accepts activation/weight data, numerical outputs, model/layer
labels, CPU timing, or frequency. The array stores timing/tag tokens for complete
rows, not individual PEs or operands. The accumulator stores address hazards and
pending commits, never numerical accumulator contents. SCU arithmetic and
saturation are not evaluated. Current fixed-latency numerical paths share the
same timing when their actual hardware requests are identical.

## Input and C API contract

```c
im2p_cycle_model_config_t config;
im2p_cycle_request_t request;
im2p_cycle_result_t result;
im2p_cycle_model_config_init(&config);
im2p_cycle_request_init(&request);
/* Fill config.hardware from the existing resolved hardware profile. */
im2p_cycle_model_t *model = im2p_cycle_model_create(&config);
/* Fill shape, tile and actual timing facts. No tensor data is supplied. */
int status = im2p_cycle_estimate(model, &request, &result);
im2p_cycle_model_destroy(model);
```

`create` returns null for an invalid configuration; `estimate` returns a stable
status code and exposes a diagnostic through `im2p_cycle_model_error`. It writes
the caller's result only on success. Failure clears stale public event data.
Null destruction is safe. Calls on different handles are independent; callers
must serialize calls on one handle. Configuration and request version/size are
validated. C11 and C++ tests check layout and calls through the actual thin C ABI.

The request supplies M/N/K, positive tile counts, an accepted-cycle epoch,
optional host row strides, diagnostic identity, submission framing, and initial
scratchpad/accumulator halves. Zero strides derive canonical minimum strides;
zero shape/tile counts are rejected. Host A4 scalar storage uses one byte per
logical element before the existing packed backing layout is constructed. Output
strides are bytes and must be four-byte aligned; scale stride is in uint32
elements. Strides describe source extents, not new physical DRAM timing.

Current profiles require paired A4/W4 or A8/W8, DIM16/32/64, block32, signed32
architectural accumulation, four scratchpad banks, 256 KiB scratchpad, 64 KiB
accumulator, scratchpad delay 4 and accumulator latency 2. Different fixed
hardware configurations are rejected, not silently assigned this certificate.
Extents, products, accepted epoch, field widths, half-buffer capacities, scale
entries and fragment identity are checked. Software cycle/fragment/trace limits
bound resource use; they are admission limits, not modeled hardware queue depths.

## Single planner; two explicit submission framings

`expand_work` calls only the existing `plan_loop`, `plan_fragment` and
`advance_loop` for padding, I/J/K decomposition, block32 boundaries, contribution
intent, and extents. Its retained fragment sequence is checked field-for-field
against the planner. The planner remains cycle-agnostic and unchanged.

There is an important pre-existing difference between the production C ABI
runtime and the independent integrated numerical regression harness:

* `planner-blocks` / `IM2P_CYCLE_BLOCK_SUBMISSIONS`: each planner `LoopPlan` becomes
  one descriptor. This is the default and preserves the planner's block-bounded
  framing.
* `regression-tiles` / `IM2P_CYCLE_TILE_SUBMISSIONS`: consecutive already-planned
  block pieces in the same I/J and requested tile-K range are grouped into one
  descriptor, as the independent `test_ws_rtl.cpp` fixture does for DIM16/32.
  DIM64 continues to submit K32 pieces. No fragments are regenerated or retiled.

For example, DIM32/K64/tile-K2 has two planner loops but one hardware descriptor
in `regression-tiles`. The result reports `planner_loop_count` and `loop_count`
separately. Only the regression framing has the complete 268-case certificate;
block framing has unit/planner checks and must not inherit that certificate by
renaming counts. Neither framing changes the numerical backend's schedule.

Commands are expanded from those planned fragments into A/B row loads, preload,
compute and final-store tokens. Deduplicating the fragments' required A/B tiles
is a timing-traffic expansion, not an independent GEMM tiler. Repeated bus traffic
is computed by controllers and credits; unique planner payload bytes are not
misreported as total bus traffic. Packed A4 row sizes follow the resolved profile.

## Timing profile and source authority

`RtlTimingProfile` names source-derived constants. They are not per-shape fitted
latencies. All hardware and source hashes are recorded in external
`timing-profile.json`; the upstream pins remain in `src/gemmini/UPSTREAM.lock.json`.

| Timing/resource fact | Value / rule | Authoritative source |
|---|---|---|
| Scratchpad response pipeline | 4 | profile catalog, `UpstreamWsConfig`, pinned `ExecuteController` |
| Accumulator commit latency | 2 | profile catalog, `UpstreamWsConfig`, `AccumulatorMem` |
| Array token pipeline | `DIM + 1 + (DIM - 1) = 2*DIM` | `MeshWithDelays`, pinned `Mesh`, selected tile latency 0 and mesh output delay 1 |
| Input/loop/unrolled/transpose queues | 2 each | `UpstreamWsControl`, `LoopMatmul`, pinned `TransposePreloadUnroller` |
| Load / execute / store command queues | 8 / 8 / 2 | pinned `GemminiConfigs` / controller defaults |
| Reservation entries, load / execute / store | 8 / 16 / 4 | `UpstreamWsConfig` and pinned reservation station |
| Load/store DMA tracker commands | `16 / DIM + 1` | pinned `GemminiConfigs`, `LoadController`, `StoreController` |
| Integrated operand read IDs / queue | 8 / 8 | `UpstreamWsMemory` |
| Top backing request queue | 1, non-flow/non-pipelined | `UpstreamWsHp1Top` backing-port composition |
| Scale priority / metadata load | scale before operand requests; one lane per cycle | `UpstreamWsHp1Top`, `ScaleBackingLoader`, `ScaleMemory` |
| Writeback contexts / credit | 16 / `16*DIM` rows | `UpstreamWsControl`, `UpstreamHp1Writeback`, `ScuWritebackQueue` |
| Completion queues | 64 immediate and delayed | `UpstreamHp1Writeback` |
| Array tag identities / queues | 5 identities / 6 entries | `MeshWithDelays` |
| Minimum shortened compute rows | 4 | pinned `ExecuteController` garbage-D, non-transpose path |
| Descriptor command sequence | 11 commands before dataflow | `HostCommandBridge` |
| Backing read / even-ID extra delay | 3 / 13 by default | `test_ws_rtl.cpp::Adapter` |
| Additional scale delay | 17 by default | same regression adapter |
| Final backing write delay | 11 by default | same regression adapter |
| Read ready cadence | stall when backing cycle modulo 5 is zero | same regression adapter |
| Harness/RTL clock offset | 5 reset edges, configurable input fact | regression reset/clock driver |

The retained slow fixture uses read/even-ID/scale/write delays 11/31/29/37 and
ready period 3. Period zero means always ready; period one is rejected because
it would never accept requests. Responses select the newest eligible request,
matching the adapter's reverse scan. A delay change outside the certified input
set produces an estimate, not a new validated timing profile automatically.

One subtle source rule is deliberately preserved: pinned `LoopMatmulExecute`
checks `ld_ka` in the same-K part of `ldb_ahead`, rather than `ld_kb`. Replacing
that expression with a seemingly cleaner rule changed early load issue events
in 16 cases even though their elapsed cycles were unchanged. The cycle model now
matches the actual pinned expression; the RTL was not edited.

## Event graph, resources and deterministic engine

`TimingEvent` stores acceptance, earliest eligible completion, minimum initiation
interval, type and resource. `ScheduledWork` expands incrementally into these
scheduled edges plus command/resource dependencies. It is not an instruction
trace replay. Resource readiness is computed from a pre-edge state snapshot and
committed in one deterministic transition, preserving registered queue bubbles.

The engine tracks reservation dependencies, queue occupancy, outstanding read
IDs, DMA completion credits, independent pipeline stages, array tags, writeback
reservations and accumulator address hazards. A response can become eligible
before arbitration consumes it. Pipelined resources can accept another token
before an earlier token completes: latency is not treated as an exclusive busy
interval. There is no one-multiplication-per-MAC or per-PE simulation.

Load/execute/store commands can issue concurrently when their dependencies and
credits permit. Scratchpad bank conflicts and read-response backpressure are
explicit. Array fill/drain uses row tokens. SCU rows become accumulator writes,
then delayed commits and reservation completions. Stores wait for address
hazards and backing acknowledgements. Scale release occurs after the current
frame's completion before the next serialized descriptor.

Optional diagnostic events contain cycle, event type, logical work ID, loop and
fragment identity, resource, tile coordinates, causal parent and protocol detail.
The engine's reservation dependency bitsets and credits are separate from the
single diagnostic parent field; that field is not a complete serialized DAG.
Stable event IDs and pre-edge traversal order make repeated diagnostic results
byte-identical. Disabling diagnostics does not change timing.

## Exact logical boundary and accepted epoch

Start is the existing `io.work.fire && io.work.bits.firstLoop` event. Done is
`bridge.io.logicalDone.valid`, sampled by `MatmulCycleCounter`. The duration is
`done_cycle - start_cycle`, after execution, scale/writeback, accumulator hazard
stalls, final backing-store completion and controller drain.

`accepted_cycle` supplies the actual work-acceptance epoch. It is not a predicted
host arrival time. Comparing at the same absolute epoch preserves backing-ready
phase. The model calculates elapsed cycles and the done endpoint independently;
it does not receive the RTL elapsed/done value. The default epoch zero gives an
isolated-work estimate under the declared clock offset. Changing the ready phase
can legitimately change latency for the same shape.

No CPU quantization/composition, host wall-clock, transport upload, host thread
wait, clock frequency or physical delay conversion is included. Multiple stripes
in the corpus are separately accepted logical works, not a CPU/NPU timeline.
The production C ABI runtime's backing-memory policy is not silently substituted
for the independent regression adapter's policy.

## Build, single-request CLI and tests

```sh
OUT="$HOME/aisa-lab/build/im2p-gemmini/cycle-model-new"
make cycle-model-test IM2P_CYCLE_BUILD_DIR="$OUT"
# Or, without the repository Makefile and its other tool probes:
cmake -S sim/cycle -B "$OUT" -DCMAKE_BUILD_TYPE=Release
cmake --build "$OUT" --parallel 2
ctest --test-dir "$OUT" --output-on-failure
```

This produces static/shared `im2p_cycle_model` libraries and the C/C++ tests. The
optional CLI loads the shared library (`.dylib` on this tested macOS host, `.so`
on Linux). A single-request JSON example is:

```json
{
  "profile": "a8w8-d32-hp1",
  "timing_profile": "rtl-regression",
  "request": {
    "m": 2, "n": 3, "k": 64,
    "tile_i": 1, "tile_j": 1, "tile_k": 2,
    "accepted_cycle": 589,
    "submission": "regression-tiles",
    "record_events": 1
  }
}
```

```sh
python3 -B sim/cycle/cli.py --library "$OUT/libim2p_cycle_model.dylib" \
  --request /path/to/request.json --out /new/path/result.json
IM2P_CYCLE_LIBRARY="$OUT/libim2p_cycle_model.dylib" \
  python3 -B sim/tests/cycle/test_cli.py
```

This example reproduces the certified 503-cycle K64 request at its declared
acceptance epoch. The JSON adapter rejects unknown fields, numerical inputs,
booleans/floats where integers are required, incompatible profile files and
invalid timing/stride facts. Existing output files are never overwritten.
`record_events` is integer 0/1. The standalone C API is usable without Python.

C/C++ tests cover API admission and ABI layout, failed-result preservation,
profile mismatch, overflow/capacity limits, planner reuse, all six profiles,
trace ordering/determinism, trace-off invariance and independent parallel handles.
The optional build flag `-DIM2P_CYCLE_SANITIZE=ON` enables AddressSanitizer and
UndefinedBehaviorSanitizer. The full 268-case comparison is also run against that
instrumented model, not only the release build.

`sim/tests/cycle/rtl_observer.py` relinks a passive observer with preserved,
unchanged real RTL objects. In normal observer mode, each original complete
numerical runtime log must remain byte-identical. `compare_rtl.py` passes only
request/timing/profile facts to the separate model executable, then compares
answers and reports the first divergent event. It never feeds expected durations
or event traces to the model and never updates a golden to hide a mismatch.
The preserved binaries/fixture used by this adapter are external evidence, not
new runtime dependencies of the cycle library.

`value_independence.py` separately reuses the original extremal and all-zero
K32 numerical fixtures. It aligns actual acceptance-ready phase in an external
test copy, preserving the original numerical assertions and production RTL.
It compares relative event cycles/counters and repeats the identical scalar-only
model request. This phase-alignment test does not replace the 268-case golden.

## Limitations and preserved paths

Certified framing/timing and input bounds are explicit above. Independent
asynchronous logical-work concurrency, arbitrary ISA operations, accumulator
preload inputs, non-HP1 profiles, changed fixed hardware delays, physical memory
models, and globally exact arbitrary-GEMM predictions are not certified. No
whole-model or floating numerical accuracy claim follows from cycle agreement.
The first build and runtime validation were performed on the current macOS
checkout; cross-platform runtime validation must be reported separately.

The retained numerical matrix/API, Scala tests, Python tests, vendor provenance,
LEGACY_BSV smoke and export/relocation remain independent regression gates. The
known generic frontend-real `failed to start IM2P stream` failure remains
`BASELINE_EXISTING_FAIL` and is not repaired or counted as PASS here. The separate
historical outer-K oracle failure must not be relabeled as that signature.

Op-trace replay, multi-operation/system timelines, CPU/NPU co-simulation,
frequency conversion, synthesis, Fmax/resource/TOPS work, new transport, physical
FPGA execution and actual Chipyard integration are outside this implementation.
Source and evidence must not be committed automatically by the implementation
workflow. External `final.json` records the actual final gates and known limits.
