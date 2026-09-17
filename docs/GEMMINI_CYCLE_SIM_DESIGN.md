# Production scheduling authority and cycle-certification boundary

## Scope, baseline and decision

The historical reference is IM2P.sim `7e8932e90d099601e459a48a5b8eca3806a538ca`
(cleanup), following `820e6d425cba73d219551027f5a06db6c102531c` (lowerer extraction).
The user explicitly approved working from current HEAD
`0cda3fdd2841dd5f7d027dca92c4f89f8b0e39f0` plus the twelve uncommitted hardening
files. Those pre-existing changes are preserved, not reset or recommitted.
`sim/common/`, `sim/backends/`, `sim/ffi/`, `frontend/`, `src/gemmini/`, and
`fpga/gemmini_hp1/host/` are unchanged from the historical cleanup reference.

Inspected llama.cpp-gemmini local `develop` and the read-only `git ls-remote`
result for remote `develop` both identify
`280e101093aca1daf872425769b21b2ca835438e`. The software-header repository is
`develop` at `cd2caed8d93e49d3f8873bd6c4e340fcad54b9e0`.
These are source identities, not a claim that every existing binary uses them.
Compiler dependency records must identify the header and generated parameters
actually consumed by each new provenance test.

**Decision: use Option 1, production-selected geometry plus its existing
route-specific submission companion.** Do not introduce a second tiler or a
hardware-work replay API. The deterministic lowerer is already shared by the
integrated runtime and the cycle model. However, the generic public IM2P C API
currently does not propagate that geometry completely. Its production cycle
certificate is therefore **BLOCKED**, not inherited from a fixture certificate.

- **AUTHORITATIVE TILING OWNER:** `ggml::gemmini::gemmini_set_tile_ws` in the
  selected `RISC-V-DynDNN-gemmini-include/gemmini.h`, called by the llama production
  route and, for a new compact residual shape, the HP1 host adapter.
- **CANONICAL TRACE/SUBMISSION BOUNDARY:** the final actually used shape and tile
  factors, profile, mode, strides, and accepted stripe metadata. For the bound
  HP1 route, reuse `WorkPlanV1` at `ggml_gemmini_hp1_executor::prepare`, together
  with the full/stream descriptors and accepted publications. `prepare` alone
  is intent, not proof of execution or admission by RTL.
- **IM2P.sim LOWERING RESPONSIBILITY:** validate supplied geometry and lower it
  into block-bounded `LoopPlan`/`FragmentPlan` objects, offsets and contribution
  flags. Do not choose tile factors, stripe height or publication order.
- **RTL INPUT RESPONSIBILITY:** execute the accepted descriptors derived from
  that same plan, retaining actual slot, scale ownership, generation, numerical
  mode and memory handshake policy.
- **CYCLE MODEL INPUT RESPONSIBILITY:** consume the same validated geometry and
  lowered work under an explicitly named submission and timing policy. An
  arbitrary M/N/K request with default tiles is an estimate, not production
  provenance. The existing scalar-only ABI v1 is not changed by this audit.

## 1. Production call graph and software tiling

Paths in this section are relative to the indicated repository. This is source
archaeology of the compiled-route alternatives, not a claim that all branches
execute in one invocation.

```text
llama.cpp-gemmini/ggml/src/ggml-gemmini/ggml-gemmini.cpp
  ggml_backend_gemmini_graph_compute: GGML_OP_MUL_MAT
    -> ggml_backend_gemmini_mul_mat
       I = ggml_nrows(src1), J = src0->ne[1], K = src1->ne[0]
       -> ggml::gemmini::gemmini_set_tile_ws(&args)
          [selected software gemmini.h]
       -> make_gemmini_geometry({shape, selected factors, DIM})
       -> args.activation_rows_per_stripe = geometry.stripe_rows
       -> route-specific execution below
```

`ggml-gemmini-args.h` documents `tile_I/J/K` as counts in DIM units.
`ggml-gemmini-geometry.hpp::make_gemmini_geometry` performs checked multiplication
and ceiling division: extents are `tile_* * DIM`; outer counts are ceiling
logical size / extent. `stripe_rows = tile_I * DIM`, `stripe_count = outer.i`.
It validates existing factors; it does not select them. The quantization-only
`activation_quant_geometry` fallback is a llama-side convenience, not a valid
basis for silently reconstructing an executed production schedule.

The shared `gemmini_set_tile_ws` uses padded dimensions, double-buffered
scratchpad and accumulator capacity, then expands feasible J, I and K factors.
The LAYERNORM/SOFTMAX branch is also present. IM2P's lowerer must not reproduce
these heuristics. Generated profile parameters affect the *shared* tiler;
IM2P's profile resolver is not a second tiler.

The header include order matters. llama's Gemmini CMake target uses generated
configuration, then `GEMMINI_SW_PATH`, then `GEMMINI_SW_PATH/include`. `GEM_HOME`
and an explicit `GEMMINI_SW_PATH` can change the selected header. The inspected
root and `include/` copies have different SHA-256 hashes. Do not assume they are
interchangeable, and do not edit either copy to conceal this provenance issue.

### Generic IM2P_SIM route

```text
llama: ggml-gemmini.cpp
  im2p_adapter::run_full / run_stripe_pipeline
  or start_exsia_full_execution / start_exsia_stripe_pipeline
    [ggml-gemmini-im2p.cpp]
      -> im2p::gemmini::execute(runtime_args, Mode, Options)
IM2P.sim: frontend/src/im2p_gemmini_frontend.cpp
  execute -> Run::Impl::run_full / stripe startup
      -> full_descriptor / stripe_descriptor
      -> im2p_execute_matmul_extended / im2p_begin_striped_matmul
IM2P.sim: sim/src/c_api.rs, sim/src/c_api/stream.rs
      -> simulator/matmul.rs / simulator/striped/start.rs
      -> ffi::im2p_start_matmul
IM2P.sim: sim/ffi/im2p_gemmini_integrated.cpp
      -> Runtime::schedule -> plan_loop / plan_fragment / advance_loop
      -> sim/backends/gemmini_hp1/runtime.cpp::drive_work / tick
      -> UpstreamWsHp1Top
```

**Observed gap:** `normalize_tile_count` clamps I/J extents to DIM. The public
`im2p_matmul_desc_t` and `im2p_stripe_work_desc_t` have no DIM-count `tile_K`
companion. No Rust code declares or calls `im2p_configure_work_plan`. Thus a fresh
integrated `Runtime::plan{1,1,1,1}` remains the effective plan in this route; the
private FFI descriptor's bounded I/J extents do not configure its factors.
This is geometry information loss/defaulting, **not** an independent tiling
search. A cycle request using the original larger factors must not be labeled
as timing of that generic runtime invocation.

A concrete counterexample uses A8W8/DIM32, M=129/N=129/K=96. The actual shared
tiler in the new test chooses factors **2/4/3**, lowered to **18** planner loops.
The fresh generic runtime's unconfigured **1/1/1** plan lowers to **75** loops.
These are deterministic lowering counts, not measured generic-runtime cycles.
The dynamic frontend capture also demonstrates that both hardware-valid factor
sets project to the same public descriptor fields. Source inspection confirms
that no Rust companion call restores the lost factors.

The private `im2p_ffi_work_plan_t` plus `im2p_configure_work_plan` already exists.
`tests/gemmini_runtime_fixture.cpp::Session::run` explicitly uses it, but a test
caller is not proof that the normal public Rust/frontend route does so.
Adding a public companion and its Rust/frontend plumbing would be a separately
reviewed production contract change, with affected API/numerical validation;
this audit does not silently change execution or reinterpret legacy fields.

### Bound Gemmini HP1 host route

```text
llama: ggml-gemmini.cpp [FPGA_UART + GEMMINI_HP1 build]
  ggml_gemmini_fpga_execute(args, pipeline_requested, quantize, ...)
IM2P.sim: fpga/gemmini_hp1/host/ggml-gemmini-fpga.cpp
  construct WorkPlanV1 directly from args.I/J/K and tile_I/J/K
  -> executor.prepare(context, plan)
  -> frontend::execute with executor.full or executor.stream
  -> full / begin / publish / poll / finish callbacks
  -> the specifically bound executor's hardware submission
```

This companion preserves selected factors before quantization. The callback
implementation owns the actual descriptor driving policy and must be identified
in a certificate. The normal host adapter has no automatic physical transport:
without a bound executor it rejects; physical transport is not implemented.
The independent `test_ws_rtl.cpp` callback and the private-FFI runtime fixture
are different executor implementations. Do not equate them by their shared
frontend name or by numerical agreement.

### Other Gemmini execution routes

`ggml-gemmini-matmul.cpp::execute_dense` dispatches through the existing physical
Gemmini wrappers, including `tiled_matmul_auto_im2p` for its applicable route.
The header wrapper traverses outer I/J/K using existing factors. This is not the
same branch as the public IM2P simulator adapter.

`execute_stripe` in the matmul facade copies args, changes I to the stripe size,
invokes the same shared tiler again and restores the metadata `tile_I`; J/K can
therefore change. A future trace for this route must capture the final factors
at dispatch, not the initial full-matrix choice. The generic IM2P and HP1 bound
routes copy a stable companion across stripes instead; do not project this
facade reselection rule onto them.

## 2. Stripe, layout and numerical responsibilities

ExSIA obtains stripe geometry from the selected llama geometry.
`quants/act/exsia/exsia.cpp` publishes `StripeReadyEvent` with run/stripe identity,
row range, metadata snapshot and `slot = stripe_idx % EXSIA_PIPELINE_SLOT_COUNT`.
The generic adapter and HP1 `Invocation::ready` validate ordered contiguous
publication. HP1 also requires slot parity. `im2p_publish_activation_stripe`
validates monotonically published rows and assigns its runtime slot by stripe ID.
Tile factors do not become an independently selected per-stripe IM2P plan.

One GGML matmul can become a full logical work or several stripe logical works,
then several I/J/K loops and block32 fragments, plus separate compact residual
work. Publication/CPU latency is not predicted by the isolated cycle model.

At the host boundary the operands are logical canonical scalar rows; native
block readers unpack Q4 and extract block scales. The frontend's provider
contract fixes activation/weight byte strides, output element stride, block
size, vector operation and output domain. Native weight transpose/layout is
resolved by readers, not an unmodeled transpose flag injected into the cycle
engine. A future trace needs these **resolved** facts, not private tensor pointers.

The integrated runtime determines packed A/B addresses and padding from the
supplied lowerer geometry. `drive_work` adds slot backing bases, output byte
addresses, scale base/generation, host work identity and rmdRaw metadata. The
lowerer provides block boundaries and first/accumulate/final contribution flags.
Full logical K accumulation is not reset at K32, refill or fragment boundaries;
only the final contribution stores output. DIM64 retains K32-valid work. Carrier
encoding, saturating arithmetic, INT32 residual semantics and packed A4 ABI are
unchanged.

K32 responsibility is route-specific. llama-side formats/readers and compact
residual packets already carry block-scale/block identity, while the generic
public dense descriptor does not enumerate physical K32 work: IM2P's shared
lowerer performs that expansion. The separate native `tiled_block_matmul_auto`
wrapper traverses block/tile geometry on the production-header side; the
independent RTL fixture has its own regression framing. `fragment_work` in the
HP1 host validates a contract but does not prove a submitted descriptor sequence.
Thus the repositories share block-scale facts, but they do not all share one
identical submission policy automatically.

## 3. Planner/lowerer audit

**Does IM2P.sim independently select tile factors? NO.** The shared planner in
`sim/common/gemmini_schedule.{hpp,cpp}` is a deterministic hardware lowerer.
There is one important repository-ownership qualification: HP1 host
`rmd.cpp::execute_rmd_raw_descriptor` calls the *same external* `gemmini_set_tile_ws`
for a newly formed compact shape. It invokes the sole tiling authority; it does
not implement another heuristic.

| Operation / source | Class | Responsibility |
|---|---|---|
| External `gemmini_set_tile_ws` | A: production software tiling | Select actual DIM-count factors; no clone in IM2P lowerer |
| `valid_config`, supplied `TilePlan` | B/D | Validate factor widths, shape and supported geometry; no search |
| `plan_loop`, `advance_loop` | B | Traverse supplied I/J/K extents, K fastest then J then I |
| block/tile end, K32 split, `plan_fragment` | C | HP1 fragment limits, output-context order, contribution intent |
| `scale_first_block`, rows/release extents | C | Scale addressing/ownership metadata, not scale arithmetic |
| padding, `activation_read`, `weight_read`, `scale_read` | D | Packed/host extents and offsets; not measured DMA traffic |
| runtime slot/generation/handshakes | D/E | Actual simulation ownership and timing, outside pure lowerer |
| `sim/cycle/scheduled_work.cpp::expand_work` | B/C | Consume same lowerer, optionally adapt to explicitly named regression framing |
| `control_engine`, `execute_engine` | E | Resource/latency transitions; no tile selection or tensor arithmetic |

No rename is necessary: keeping the tested `plan_*` API avoids churn. In this
document, “planner” means hardware schedule lowerer, not software auto-tiler.
`normalize_tile_count` and runtime default factors require the route caveat above;
calling them a second optimized tiler would misdiagnose the actual problem.

## 4. Canonical production-schedule contract

Reuse existing types rather than introduce a universal trace schema now:

1. Source identity: llama/header commit and hashes; selected generated profile;
   actual executor identity and build configuration.
2. Already selected geometry: logical shape, DIM, tile counts in DIM units,
   stripe height and mode. In the bound route this is `WorkPlanV1`.
3. Resolved full/stream descriptor: byte/element stride units, operation/output
   contract, K origin, scale layout and initial contribution state.
4. Accepted stripe/work metadata: publication identity/order, actual row range,
   slot and completed callback identity. Prepared work that never executes is
   not an NPU work item.
5. Timing facts for a cycle certificate: acceptance epoch, initial state and
   memory/queue/backpressure policy. The current rtl-regression adapter is not
   the production C ABI's one-outstanding-read service policy.

Option 2 would require a new stable hardware-work emission interface that llama
does not currently own. `LoopPlan` is already shared inside IM2P, so exporting its
private backing addresses upward would add coupling rather than remove an
existing independent tiler. A future test observer can capture accepted
descriptors at `drive_work` without making them a new llama production API.

Never certify M/N/K-only input by silently selecting defaults. A provenance
checker must fail closed on absent final factors, a mismatched profile, changed
stripe mode, a missing companion or a different executor/timing policy. The
standalone cycle API's convenience defaults do not supply this proof.

## 5. RTL and cycle certification

```text
production-selected geometry actually captured at a named route boundary
   + descriptor/stripe/executor provenance
                         |
                  shared HP1 lowerer
                    /           \
              accepted RTL     ScheduledWork -> timing/resource engine
                    \           /
             endpoints, counters, selected per-cycle event-type multisets
```

This flow is valid only for the identified route and timing assumptions. There
are separate levels of evidence: a host prepare-capture test proves geometry
preservation, a shared-lowerer test proves expansion identity, and an RTL test
proves timing under its adapter. Do not collapse these into full GGML inference
or generic public-C-API production certification.

### Retained 268-case certificate

The corpus comes from `fpga/gemmini_hp1/host/test_ws_rtl.cpp` and linked frontend,
RMD and bound-RMD fixtures, recorded by a passive external observer. It is mixed:
initial fast/slow K64 smoke factors are fixture-specified; frontend FULL/stripe
and outer-K cases call the shared Gemmini tiler; compact residual cases use the
host residual path; the extremal K32 pair is explicitly constructed. It is not
a captured full-model GGML operation trace.

Its regression-tiles submission policy coalesces already planned block pieces
within tile K for DIM16/32; DIM64 keeps K32 submissions. Its test callback is not
the private runtime's descriptor framing or memory-service policy. Preserve the
268/268, delta-zero golden and identify it as a regression-fixture certificate.

The prior planner-block certificate uses those shape/tile inputs, fresh reset,
full mode and slot0, with the regression memory adapter. It proves 262/262
representable cases; each profile excludes the K8256 case needing 258 global
scale rows in a 256-entry ScaleMemory. It does not certify a generic llama call,
per-stripe slot behavior, or the production Rust provider latency. No exclusion
is hidden, and no existing golden is overwritten.

### Event comparison gate

The dirty hardening already requires endpoint/counter equality, selected event
multiset equality and a successful model process. Therefore **NO CHANGE REQUIRED**
to its implementation in this task. Re-run it and an event-only mutation test.
The precise comparison is sorted `(cycle, normalized event type)` pairs; it
excludes payloads, IDs, addresses, dependencies and same-cycle ordering. A byte-
identical numerical runtime log is a different check, not a full RTL signal trace.

## 6. RMD boundary

CPU-direct residuals execute through
`residual::execute_direct_stripe` and CPU composition/merge. In
`ggml-gemmini-im2p.cpp::apply_baseline_rmd_full` the direct route creates no
residual simulator. Such computation contributes **no NPU work**, regardless of
any diagnostic RMD label.

Compact residuals go through
`rmd::detail::execute_im2p_compact_dot`: a scalar raw-dot request with no host
scale provider, block_size=1, vector bypass, and shape-specific strides. The
generic route calls `im2p_execute_matmul_extended`; the bound route invokes
`execute_rmd_raw_descriptor`, runs the shared tiler on the compact shape, then
passes `RmdRawWork` to the bound `raw` executor. Full INT32 residuals are represented
by width-native radix packets; CPU radix/block-scale composition remains outside
NPU cycles. There is no signed-21 input restriction.

`Runtime::schedule` selects `split_at_block=false` and synthesized scale reads
for raw work; the dense cycle expander uses block lowering. They coincide only
when the supplied geometry produces the same valid compact descriptor sequence.
For DIM16 K31/K32, for example, splitting a raw descriptor into two independently
accumulated submissions would violate rawShapeValid. Equal physical mesh alone
is not sufficient proof.

The existing paired certificate tested all six profiles, M=1/N=3,
K={1,16,31,32}, fragmentBase=0, accumulate=false, finalFragment=true, a fresh/drained
state, valid exponent-zero non-raw scales, and identical regression memory timing.
It showed equal selected control timing for 24 pairs. It is not an exhaustive
all-shape/all-timing proof. Physical raw scale loads still occur; the integrated
host may synthesize their responses differently from dense providers. Preserve
ABI v1 and do not invent a semantic RMD timing field or claim general equivalence.

## 7. Future tracing and remaining blockers

No op-trace recorder/replayer, multi-operation timeline, estimator redesign or
new numerical behavior is implemented here. Future traces must be emitted from
actual final dispatch/accepted work, not reconstructed from model-layer names.

The generic public-C-API route needs an explicit versioned/additive work-plan
companion wired through frontend and Rust, or an identified existing executor
that already preserves the companion. Its current default plan must not be
silently treated as the original auto-selected plan. That production change
requires a separately reviewed ABI/compatibility strategy and route-specific
numerical/cycle tests, not updates to the 268 golden.

The bound route still needs per-executor descriptor/publication provenance for
production execution, beyond `prepare`. The facade route needs capture after
per-stripe reselection. Deployment binary identity, changed environment-selected
headers, asynchronous slot state and production backing timing remain separate
certification dimensions. This document resolves ownership and chooses the
boundary; it does not falsely close those execution-provenance gaps.

## 8. New bounded authority tests and reproduction

`sim/tests/cycle/schedule_authority_probe.cpp` invokes the real shared tiler and
real `ggml_gemmini_fpga_execute` prepare callback. Four **fixture-provided** shapes
(M/N/K = 1/1/32, 2/3/64, 65/67/96, 129/129/96) are tested in FULL and PIPELINE mode
for each hardware profile. The selected factors, stripe height and mode survive
`WorkPlanV1` capture and codec round-trip exactly. `expand_work` is compared
field-for-field with the shared lowerer using the captured factors, without
copying the tiling heuristic. That check expands the prepared whole shape; it
is not a replay of actual published PIPELINE stripes.

The callback deliberately rejects after `prepare`, before quantization and
numerical submission. The 48 captures are therefore a test of real production
functions, **not 48 completed production NPU invocations**. A second test compares
the selected nontrivial factors with the hardware-valid 1/1/1 default plan and
observes the public descriptor collision in all six profiles.

`check_schedule_authority.py` builds fresh host libraries and CTests, records
compiler dependency files and effective software-header/profile hashes, and runs
those captures. Its optional `--with-rtl` projects the **24 FULL captures only**
into the unchanged integrated RTL under the existing reference memory adapter,
and compares the separate model with the same inputs. The actual generic
Rust/provider route is not used by this projection. No full GGML graph is run.
`test_schedule_authority.py` has 10 tests rejecting missing factors, missing or
inconsistent stripe metadata, profile mismatch, unproven capture flags, and
PIPELINE intent being relabeled as a FULL certificate. This is certification
input validation, not a change to the existing scalar cycle API defaults.

Example (all outputs must be new external paths):

```sh
G="$HOME/aisa-lab/build/im2p-gemmini/cleanup-20260916T161107Z"
E="$HOME/aisa-lab/build/im2p-gemmini/new-authority-check"
make cycle-model-test IM2P_CYCLE_BUILD_DIR="$E/cycle-build"
python3 -B sim/tests/cycle/test_schedule_authority.py
python3 -B sim/tests/cycle/check_schedule_authority.py \
  --golden-root "$G" --cycle-build "$E/cycle-build" \
  --out "$E/captured-geometry" --with-rtl
```

## 9. Current-run validation and evidence

Evidence root:
`$HOME/aisa-lab/build/im2p-gemmini/schedule-authority-20260917T033724Z/`.
Every attempt uses a new log/output path. `commands.jsonl` records actual command
return codes, including expected negative results and initial development errors.
No earlier golden or failed-attempt evidence was overwritten.

| Current check | Result | Evidence / scope |
|---|---|---|
| Shared-tiler / bound-prepare capture | PASS, 48/48 | `authority-six-valid-default/authority-check.json`; 6 profiles, 4 fixture shapes, FULL/PIPELINE companion capture |
| Generic public descriptor collision | REPRODUCED, 6/6 | Both selected and default geometries are hardware-valid; **production certification BLOCKED** |
| Captured FULL reference-memory projection | PASS, 24/24, max absolute delta 0 | Same artifact; 4 FULL shapes/profile, isolated slot0/reference timing, endpoints/counters/selected-event multisets exact |
| Fresh host CTest matrix | PASS, 18/18 | Three existing host tests per profile; fresh source builds under `authority-six-valid-default/` |
| New authority admission tests | PASS, 10/10 | `logs/authority-unit.log` |
| Preserved numerical integrated fixture rerun | PASS, 6/6 | `observer/`; relinked preserved RTL objects, complete runtime logs byte-identical; not a new six-profile Scala/RTL clean build |
| Existing regression-tiles certificate | PASS, 268/268, max absolute delta 0 | `regression-tiles/cycle-model-vs-rtl.json`; endpoints/counters and selected-event multisets exact; first mismatch null |
| Event-only mutation negative | PASS: comparator exits 1 | `event-gate-proof.json`; 267 exact, one event mismatch despite exact endpoints/counters; original observer/golden untouched |
| RMD_RAW timing pairs | PASS, 24/24 | `rmd-timing/rmd-raw-timing-equivalence.json`; exact tested domain in section 6, not all valid shapes/timing |
| Cycle CTest / CLI | PASS, 2/2 and 11/11 | `logs/cycle-release.log`, `logs/cycle-cli.log` |
| Scala retained suite | PASS, 48/48 | `scala/logs/02.log`: 23 diagnostic + 25 control tests, selected A8W8D16 profile |
| Existing selected Python contracts | PASS, 27/27 | Build CLI/vendor/evidence tests; `logs/python-contracts.log`; not the historical full 78-test selection |
| C/C++ API layout, planner/extent | PASS | `logs/abi-and-lowerer.log`; all six packing/schedule/extent profiles |
| Vendor verification | PASS | `logs/vendor.log` |
| Cycle Python pyright | PASS, 0 errors / 0 warnings | `logs/final-cycle-python.log` |
| New C++ probe format | PASS | `logs/final-probe-format.log` |
| LEGACY_BSV clock smoke | PASS, one selected test | `logs/legacy-clock-smoke.log`; fresh BSC/Verilator/Cargo build; `raw_clock_periods_are_exact` actually executed |
| `make check` | **FAIL** | `logs/make-check.log`: 8/20 activation-guard infrastructure failures comparing `/var/folders/...` with resolved `/private/var/folders/...`; source unchanged |
| `git diff --check` | PASS | `logs/final-diff-check.log` |

The first new-probe compile failed because include sorting placed `gemmini.h`
before the generated parameter header, selecting its DIM16 same-directory
fallback. Only the new test's include order was corrected; the failed log remains
in `authority-smoke/`. The new Python checker initially had two type-narrowing
errors, corrected explicitly in that checker; its original pyright log remains.
The later counterexample was strengthened to use the valid 1/1/1 plan rather
than arbitrarily incremented factors; the final six-profile run is independently
retained in `authority-six-valid-default/`.

The unrelated `make check` path-comparison failures were **not** repaired or
retried with a changed TMPDIR. The later API/planner tests are separate targets
that the failed aggregate command had not reached. This is not an all-green
repository validation claim.

NOT_RUN this phase: the complete historical 78-Python-test selection, a new
six-profile all-stage clean matrix, generic frontend-real, generic production
end-to-end timing, full-model GGML/operation traces, and sanitizer comparison.
The previous frontend-real stream-start failure is historical context, not a
fresh passing or failing execution in this audit.

Synthesis, Fmax, resource characterization, TOPS, physical FPGA access, CPU/NPU
system simulation, stage, commit and push are NOT_RUN. The design/audit is
complete at the proven boundaries; the overall production-schedule certification
remains **NOT_READY** because the generic companion is missing and actual
executor acceptance/production timing is not certified. This status does not
invalidate the explicitly scoped, preserved 268-case regression certificate.
