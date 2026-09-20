# Value-free Gemmini HP1 cycle model

## Scope and certification

`sim/cycle/` implements a separate single-GEMM logical-cycle model. The public C
interface is `sim/include/im2p_cycle_model.h`; its ABI version is 1. It uses the
existing resolved Gemmini hardware contract and pure schedule planner. There is
no numerical runtime flag, numerical ABI change, tensor pointer, MAC/SCU result
computation, generated RTL class, or Verilator dependency in this library.

The following **historical rtl-regression revision 1 certificate predates the
loop-local scale-cache change at `0782667`**. Its preserved integrated RTL corpus
matched **268/268 logical-work cases**: start/done convention, elapsed cycles,
work/loop counts and load/store/scale request/response counts are exact. The
maximum absolute cycle difference is **0**. The **selected per-cycle event-type
multiset** also matches in all 268 cases. `compare_rtl.py` treats either an
endpoint/counter mismatch or a selected-event mismatch as a hard case failure;
a deliberately shifted passive RTL event is verified to make the comparator exit
non-zero. The selected-event comparison sorts `(cycle, normalized event type)`
pairs. It does not compare every internal signal, payload, event ID, address,
dependency, or same-cycle ordering, so it is not a byte-exact/full-event-stream
claim. This does not mean arbitrary input, timing profile, or submission policy
has been certified.

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
separately.

The historical production `planner-blocks` certificate used an external harness
whose descriptors came directly from `sim/common/gemmini_schedule.cpp`. It
reported 262/262 exact cases and excluded six K8256 cases under the **old global
physical scale-row assumption**. Those exclusions and counts are not evidence
for the current source and must not be carried forward as a current certificate.
`production_block_certificate.py` and its archived inputs describe that earlier
revision; use the current-source procedure below for loop-local hardware.

### Current loop-local scale ownership and recertification

`fragment_base` remains the global logical K-fragment identity for production
planner blocks. It must not be reset to make a large reduction fit the cache.
Physical rows instead begin at the descriptor's `scaleBase`; the isolated slot0
model uses base zero. `ScaleBackingLoader` loads only the current loop's rows,
`UpstreamWsControl` addresses those rows with loop-local K/J indices, and the
accepted generation plus post-completion release governs reuse. The cycle
model's serialized control engine releases the current frame's rows before the
next frame starts. Generation tag values are not an additional timing input.

Admission therefore checks the current frame's `scale_rows <= 256`, independently
of global fragment identity. Scratchpad, accumulator and fragment-width checks
remain unchanged. The numerical RTL test adapter releases the same loop-local
rows as production `drive_release`; it does not derive a physical release address
from `fragmentBase`. No cache enlargement, tile-factor reduction or model-specific
shape exception is used.

`current_rtl_certificate.py` consumes a fresh six-profile `host-test` build. A
passive observer first captures the actual fixture geometry and timing inputs;
after removing its additional summary columns, stdout must match the original
numerical fixture byte-for-byte. Every captured case is then run from fresh reset
under both declared submission framings. The additional K32/64/96/3072/8256
synthetic cases are labeled separately from captured work. No historical shape
count or K8256 exclusion is assumed. Actual endpoint/counter results and selected
per-cycle event-type multisets must match; one unresolved mismatch stops the
certificate. Physical scale rows, generation, release lanes and reuse boundaries
are checked independently. Event-only shift/delete/duplicate mutations must fail
the same hard comparison predicate even when endpoint counters are unchanged.

From `IM2P.sim`, with a new evidence directory outside the repository:

```sh
OUT="$(pwd)/../evidence/model-cycle-replay-$(date -u +%Y%m%dT%H%M%SZ)"
cmake -S sim/cycle -B "$OUT/cycle" -DCMAKE_BUILD_TYPE=Release
cmake --build "$OUT/cycle" --parallel 2
ctest --test-dir "$OUT/cycle" --output-on-failure
python3 -B scripts/gemmini_build.py --matrix a4w4,a8w8 --dims 16,32,64 \
  --scu hp1-left-shift --memory-contract-dir "$PWD/config/gemmini_host_memory_contracts" \
  --stage host-test --out "$OUT/rtl"
python3 -B sim/tests/cycle/current_rtl_certificate.py --build-root "$OUT/rtl" \
  --library "$OUT/cycle/libim2p_cycle_model.dylib" --out "$OUT/certificate"
# Linux uses libim2p_cycle_model.so.
python3 -B sim/tests/cycle/test_current_rtl_certificate.py
```

`current-certificate.json` records the actual attempted/admitted/exact counts,
first mismatch, selected event scope and source/object hashes. A previous
certificate or a passing CTest alone cannot justify
`IM2P_SINGLE_GEMM_CYCLE_MODEL_CURRENT`. These are isolated work certificates, not
whole-model, aggregate PIPELINE or CPU/NPU system timing.

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

Optional diagnostic events contain cycle, event type, logical work ID, submission
and fragment identity, resource, tile coordinates, causal parent and protocol
detail. ABI v1 retains the field name `im2p_cycle_event_t.loop`, but its exact
meaning is the serialized hardware **submission/frame index**, not a pure planner
loop ordinal. JSON diagnostics expose the same value as `submission_index` while
retaining `loop` for ABI compatibility. `planner_loop_count` reports planner
`LoopPlan` count, `loop_count` reports hardware submission/frame count, and
`fragment_count` reports planned fragments. A future ABI revision may rename the
event field; this hardening phase does not bump ABI v1 for cosmetic naming.
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
answers and reports the first divergent selected event. Endpoint/counter equality,
model-process success, and selected per-cycle event-type multiset equality are all
required for a case PASS and for the aggregate PASS. It never feeds expected
durations or event traces to the model and never updates a golden to hide a
mismatch. `production_block_certificate.py` builds an external probe that emits
production planner-block descriptors directly from the shared planner and checks
the separately scoped production certificate. The preserved binaries/fixture used
by these adapters are external evidence, not new runtime dependencies of the
cycle library.

`value_independence.py` separately reuses the original extremal and all-zero
K32 numerical fixtures. It aligns actual acceptance-ready phase in an external
test copy, preserving the original numerical assertions and production RTL.
It compares relative event cycles/counters and repeats the identical scalar-only
model request. This phase-alignment test does not replace the 268-case golden.

## Production RMD-SCU cycle certificate

Production residual work uses the normal HP1 scaled datapath:
`DENSE_HP1_FINAL`, `rmdRaw=false`, HP1 carriers, fragment-local SCU Sat32 and the
same signed32 accumulator semantics as dense GEMM. The host performs balanced-radix
recomposition and final floating reconstruction only; it does not apply an HP1
integer block multiplier after NPU output.

`rmd_scu_certificate.py` captures accepted production residual work across all six
A4W4/A8W8 DIM16/32/64 profiles and pairs it with the same normal HP1 hardware
path. The paired descriptor, scale traffic, SCU/event stream, integer result and
cycle behavior are exact for 72/72 pairs. `rmd_scu_timing.py` then feeds the
accepted scalar geometry/timing facts—not tensor values or RTL duration answers—
into the existing value-free dense HP1 cycle model. The resulting production
residual certificate is **72/72 exact**, maximum cycle delta **0**, with exact
endpoint/counters and selected per-cycle event-type multisets.

A DIM16 compact K31 case remains one logical production residual GEMM while the
hardware performs physical reductions 16 and 15. This certificate therefore
covers the current hardware-owned fragmentation/accumulation boundary rather than
a host-side split-and-sum model.

## Historical RMD_RAW diagnostic timing certificate

`rmdRaw` remains a hardware metadata bit for historical/diagnostic raw work, not
a production residual label and not a cycle-API semantic label. In the current
RTL it does not bypass scale-loading, reservation, writeback-credit,
accumulator, store, or completion structures. `ScaleBackingLoader` still reads
and emits the same scale lanes; `rmdRaw` selects carrier zero, and
`UpstreamHp1Writeback` consumes that carrier through the same timing/control path.

This was verified rather than assumed. `rmd_raw_timing_certificate.py` ran
**24/24 paired RTL cases exact**: all six A4/A8 DIM16/32/64 profiles, M=1, N=3,
and compact K={1,16,31,32}. Each pair starts from a fresh/drained RTL state with
the rtl-regression memory timing and identical descriptor geometry, scale data,
and backing-ready phase; only the `rmdRaw` descriptor bit changes. The non-raw
carrier is valid exponent 0. The certified raw metadata domain is compact K 1..32,
`fragmentBase=0`, `accumulate=false`, `finalFragment=true`. Start/done/elapsed,
load/store/scale request-response counts, context/writeback/commit behavior, and
the selected per-cycle event-type multiset are identical in all 24 pairs.
Therefore no RMD-specific or semantic mode field is added to cycle ABI v1 for
this tested diagnostic domain. This is a historical timing-equivalence
certificate, not the production residual numerical contract, not a
numerical-equivalence claim, and not a claim about raw shapes outside the stated
domain.

## Certified production op-trace entry points

The production JSONL schema is `im2p-production-optrace` **version 2**. The
certificate schema is `im2p-single-gemm-cycle-certificate` **version 1**. Numerical
C ABI v5 and cycle C ABI v1 are unchanged. Historical trace v1 lacks runtime
contract binding and parent declarations: current replay rejects it with a
recollection diagnostic. Do not insert guessed fields or rewrite historical
evidence to promote it to current certification.

Generate a certificate with the current-source command above, then pass its
unmodified output to the official CLI:

```sh
python3 -B sim/cycle/optrace.py "$TRACE" \
  --library "$OUT/cycle/libim2p_cycle_model.dylib" \
  --sources "$PRODUCER_BUILD_MANIFEST" \
  --cycle-certificate "$OUT/certificate/current-certificate.json" \
  --output "$OUT/replay-summary.json"
```

Use the actual producer build's `source-identities.json`, not a manifest
constructed from the trace. Linux libraries use `.so`. Output files are created
exclusively. The production Python entry has the identical admission boundary:

```python
from pathlib import Path
from sim.cycle.optrace import ReplayArtifacts, replay

summary = replay(Path(trace), ReplayArtifacts(Path(library), Path(sources), Path(certificate)))
```

The shared certificate validator requires complete, nonempty six-profile and
two-framing coverage, independently fixed expected case identities, exact
attempted/admitted/endpoint/counter/event aggregates, ownership checks, mutation
gates, hardware contracts and the actual library SHA256. Only the validated
generator emits `IM2P_SINGLE_GEMM_CYCLE_MODEL_CURRENT`; adding that string by hand
is insufficient. `_replay_fixture` is internal, explicitly
`UNCERTIFIED_SYNTHETIC_FIXTURE`, and cannot confer producer provenance.

Retained raw evidence may be reaggregated without rerunning RTL only when source,
artifact, request/result and raw-event checks all succeed against the unchanged
compiled library:

```sh
python3 -B sim/tests/cycle/current_rtl_certificate.py \
  --reuse-verified-evidence "$PREVIOUS_EVIDENCE" \
  --library "$PREVIOUS_LIBRARY" --out "$NEW_CERTIFICATE_DIR"
```

This produces `REAGGREGATED_FROM_VERIFIED_EVIDENCE`, not a fresh RTL run. Changing
an old certificate's library hash is not re-certification. Corpus exactness also
does not mean every subsequently admitted production work was compared to RTL.

### Producer/model compatibility and memory assumptions

Three independent checks are required: trace source identities equal the
producer build manifest; the loaded model library equals its certificate hash;
and producer/model hardware-lowering contracts match. The single canonical
implementation is `scripts/gemmini_replay_contract.py`, imported by the producer
build-info generator. It covers profile/packing/stride units, block32 and Sat32
ordering, memory capacities and fixed latencies, lowering/tile units and
loop-local scale generation/release semantics. SHA256 uses sorted, compact ASCII
JSON, excluding its own hash field, with explicit revision and authoritative
source-content hashes. Whole Git HEAD equality is not the compatibility test.

The producer binding comes from its selected runtime cache manifest and actual
frontend/runtime archive hashes. A custom cache builder needs verified original
source/artifact proof (`--runtime-source-proof`) before it may stamp a reused
runtime contract. Latest checkout hashes alone cannot certify an old archive.
Unbound runtimes remain usable with tracing OFF; explicit tracing fails clearly.
Hash binding is not a third-party signature or protection from forged evidence.

Reference-memory identity is separate from hardware compatibility and source
identity. Accounting still uses `rtl-regression`, independently drained
`accepted_cycle=1`, `planner-blocks`, and the existing byte/uint32 stride units.
The producer's actual backing-provider latency need not equal that reference.
The per-work software cycle limit is not a hardware capacity or latency.

### Parent/stripe integrity and build support

Production owns parent IDs and emits begin/end records with descriptor shape,
mode and geometry. Each accepted work carries that ID. Validation checks declared
and live phase membership, slots 0/1, ordered unique stripe IDs, row ranges and
nonoverlapping complete row coverage at successful completion. Separate parents
may interleave and may have identical layer/shape/ranges. PIPELINE final stripe
tile counts may legally differ from initial parent factors: replay preserves
each final descriptor's factors. FULL and compact parents require exact factors.
Residual parents describe each actual compact dispatch separately; source stripe
rows do not impose dense coverage on compact row stacking. K31 at DIM16 remains
one logical GEMM, with hardware-owned [16, 15] fragmentation.

Independent accepted-dispatch counts remain required. Count equality does not
prove stripe integrity. Parent checks do not prove buffer-release lifetimes or
cross-work scheduling. A writer mutex serializes output, not thread scheduling.
Neither isolated cycle sums nor per-stripe accounting are system latency.

Official generic HP1 CMake links `ggml-gemmini-utils`; trace OFF and ON use the
same implementation. The external-executor-only host also links that utility
target, with tracing OFF, without numerical simulator or Verilator dependencies.
Explicit tracing remains unsupported for that path. No fake Session symbols or
ignored undefined references are used. Git/build identity generation is not
required for a trace-OFF external host. See [export verification](VERIFICATION.md)
and generated `LINUX_BUILD.md` for pinned dependency restoration and the isolated
minimum host build. Package checksums and source-closure validation are separate
gates; a checksum-consistent package can still lack required sources.

Small v2 FULL/PIPELINE/residual fixtures exercise the official CLI. They are not
whole-model certification. Unless separately recorded for a new complete run,
`full_model_on_new_trace_schema: NOT_RUN` applies; historical v1 GPT-2 replay is
not current v2 evidence.

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

Multi-operation/system timelines, CPU/NPU co-simulation,
frequency conversion, synthesis, Fmax/resource/TOPS work, new transport, physical
FPGA execution and actual Chipyard integration are outside this implementation.
Source and evidence must not be committed automatically by the implementation
workflow. External `final.json` records the actual final gates and known limits.
