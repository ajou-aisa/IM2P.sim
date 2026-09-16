# GEMMINI refactor report

> **Completed phase record.** The state and validation below were captured before
> commit `820e6d425cba73d219551027f5a06db6c102531c`. The original report body and
> cycle table are preserved. For the current retained layout and subsequent
> deletion-only validation, see [repository cleanup](REPOSITORY_CLEANUP_REPORT.md).

This report closes the non-regression evidence for the frozen IM2P.sim refactor. It does **not** add a cycle simulator, op trace, synthesis flow, physical-FPGA path, or real Chipyard/SoC integration.

## A. Repository state

- Repository: `/Users/zerogod/aisa-lab/DynDNN/IM2P/IM2P.sim`
- Branch: `gemmini`
- Baseline commit: `43afd5033eac415a32718555a571ed6478c0c717`
- Current HEAD: `43afd5033eac415a32718555a571ed6478c0c717`
- Current HEAD equals the baseline commit; the refactor remains an uncommitted working-tree change.
- Commit performed by this task: **no**
- Stage/index modification performed by this task: **no**
- Push performed by this task: **no**
- Baseline detached worktree: `/Users/zerogod/aisa-lab/build/im2p-gemmini/refactor-baseline-43afd503-20260916T144629Z`
- Fresh final evidence directory: `/Users/zerogod/aisa-lab/build/im2p-gemmini/refactor-final-20260916T144629Z`

`git status --short` at report generation:

```text
 M .gitignore
 M Makefile
 M README.md
 M config/gemmini_hp1_profiles.json
 M scripts/gemmini_build.py
 M scripts/gemmini_resolve_profile.py
 M scripts/gemmini_vendor.py
 M scripts/real_lib_cache.py
 M scripts/real_matrix_fingerprint.py
 M sim/build.rs
 M sim/ffi/im2p_gemmini_integrated.cpp
 D sim/ffi/im2p_gemmini_integrated.h
 M sim/ffi/im2p_integrated_signal.hpp
 M src/gemmini/README.md
 M src/gemmini/build.sbt
 D src/gemmini/control/build.sbt
 D src/gemmini/control/project/build.properties
 D src/gemmini/control/project/plugins.sbt
 M src/gemmini/control/src/main/scala/im2p/gemmini/ElaborateUpstreamWsHp1.scala
 M src/gemmini/control/src/main/scala/im2p/gemmini/HostCommandBridge.scala
 M src/gemmini/control/src/main/scala/im2p/gemmini/ScaleBackingLoader.scala
 M src/gemmini/control/src/main/scala/im2p/gemmini/UpstreamHp1Writeback.scala
 M src/gemmini/control/src/main/scala/im2p/gemmini/UpstreamWsControl.scala
 M src/gemmini/control/src/main/scala/im2p/gemmini/UpstreamWsHp1Top.scala
 M src/gemmini/control/src/test/scala/im2p/gemmini/HostCommandBridgeSpec.scala
 M src/gemmini/patches/0001-packed-input-controller-bytes.patch
 M src/gemmini/src/main/scala/im2p/gemmini/BackingMemoryPort.scala
 M src/gemmini/src/main/scala/im2p/gemmini/StandaloneTop.scala
 M src/gemmini/vendor-manifest.json
 M tests/test_gemmini_build_cli.py
 M tests/test_gemmini_vendor.py
?? .vscode/
?? docs/GEMMINI_CYCLE_SIM_BOUNDARY.md
?? im2p-cache-build-scripts/
?? scripts/gemmini_hardware_contract.py
?? sim/backends/
?? sim/common/
?? src/gemmini/control/src/main/scala/im2p/gemmini/HardwareContract.scala
?? src/gemmini/control/src/main/scala/im2p/gemmini/Hp1LoopMetadata.scala
?? src/gemmini/control/src/test/scala/im2p/gemmini/HardwareContractSpec.scala
?? src/gemmini/patches/0002-loop-head-reset.patch
?? src/gemmini/src/main/scala/im2p/gemmini/ScaleProtocol.scala
?? tests/gemmini_runtime_fixture.cpp
?? tests/gemmini_schedule_test.cpp
?? tests/test_gemmini_schedule.py
```

Tracked diff summary before adding this report:

```text
 .gitignore                                         |   6 +
 Makefile                                           |   6 +
 README.md                                          |  98 ++-
 config/gemmini_hp1_profiles.json                   |   1 +
 scripts/gemmini_build.py                           | 103 +---
 scripts/gemmini_resolve_profile.py                 |  12 +
 scripts/gemmini_vendor.py                          |  38 +-
 scripts/real_lib_cache.py                          |   2 +
 scripts/real_matrix_fingerprint.py                 |  14 +-
 sim/build.rs                                       |  15 +
 sim/ffi/im2p_gemmini_integrated.cpp                | 654 +--------------------
 sim/ffi/im2p_gemmini_integrated.h                  |   6 -
 sim/ffi/im2p_integrated_signal.hpp                 |   6 +-
 src/gemmini/README.md                              |   2 +-
 src/gemmini/build.sbt                              |  88 ++-
 src/gemmini/control/build.sbt                      |  28 -
 src/gemmini/control/project/build.properties       |   1 -
 src/gemmini/control/project/plugins.sbt            |   1 -
 .../im2p/gemmini/ElaborateUpstreamWsHp1.scala      |   1 +
 .../scala/im2p/gemmini/HostCommandBridge.scala     |  29 +-
 .../scala/im2p/gemmini/ScaleBackingLoader.scala    |   2 +-
 .../scala/im2p/gemmini/UpstreamHp1Writeback.scala  |   4 +-
 .../scala/im2p/gemmini/UpstreamWsControl.scala     |   8 +-
 .../main/scala/im2p/gemmini/UpstreamWsHp1Top.scala |   2 +-
 .../scala/im2p/gemmini/HostCommandBridgeSpec.scala |   8 +-
 .../0001-packed-input-controller-bytes.patch       |  14 +-
 .../scala/im2p/gemmini/BackingMemoryPort.scala     |  15 -
 .../main/scala/im2p/gemmini/StandaloneTop.scala    |   4 +-
 src/gemmini/vendor-manifest.json                   |  19 +-
 tests/test_gemmini_build_cli.py                    |  46 +-
 tests/test_gemmini_vendor.py                       |  32 +
 31 files changed, 394 insertions(+), 871 deletions(-)
```

The pre-existing user paths `.vscode/` and `im2p-cache-build-scripts/` were preserved. The production refactor was functionally frozen for this closure pass; only external evidence/probe code and this report were added.

## B. Refactor purpose

The refactor has four purposes: reduce duplicated/obsolete code, separate responsibilities, expose the existing value-independent scheduling/configuration logic for a future value-free NPU cycle simulator, and preserve the existing numerical and logical-cycle semantics. The future performance/cycle simulator itself is intentionally **not implemented**.

## C. Before/after architecture

Before the refactor, `sim/ffi/im2p_gemmini_integrated.cpp` owned most of the integrated simulator concerns in one file: schedule decomposition, loop advancement, data packing, Verilator drive/reset, backing-memory request handling, session/stripe state, counters, and the C ABI.

The current dependency/ownership direction is:

```text
sim/common/gemmini_schedule.{hpp,cpp}       pure value-free planning
                 |
                 v
sim/backends/gemmini_hp1/runtime.{hpp,cpp}  session + RTL drive/state
                 |
                 +--> backing_memory.cpp      backing request/response translation
                 |
                 v
sim/ffi/im2p_gemmini_integrated.cpp          stable C ABI shim
```

`sim/common/operand_packing.hpp` contains the small packing helper used at the host/RTL boundary. The C ABI file now constructs `ScheduleConfig` and delegates operational state to the backend instead of owning the authoritative loop/tile/fragment decomposition. `runtime.cpp` calls `gemmini::plan_loop`, `gemmini::advance_loop`, and the request-extent helpers from the pure planner. This keeps one scheduling authority while leaving RTL stalls, overlap, queueing, backing latency, and logical start/done cycles as measured hardware/runtime facts.

No claim is made that this is a real Chipyard integration. The repository still uses pinned upstream Gemmini/Chipyard source provenance and an explicit standalone integrated RTL path.

## D. SCU boundary

The Scala dependency boundary is explicitly layered in `src/gemmini/build.sbt`:

```text
Layer A: scuCore
  SCU.scala, ScaleMemory.scala, ScaleProtocol.scala,
  SatAccumulatorAdder and small reusable primitives
       |
       v
Layer B: gemminiIntegration
  Hp1LoopMetadata.scala, UpstreamWsConfig.scala,
  UpstreamWsControl.scala, UpstreamHp1Writeback.scala
       |
       v
Layer C: root standalone/orchestration
  HostCommandBridge, ScaleBackingLoader,
  UpstreamWsHp1Top, BackingMemoryPort, elaboration/runtime-facing control
```

`SCU.scala` takes only the raw partial and 32-bit HP1 carrier and produces the saturated 32-bit result. `ScaleProtocol.scala` explicitly has no profile, host-slot, or backing-address ownership. Layer B legitimately depends on Gemmini types/controller semantics. Layer C owns IM2P-specific descriptors, backing-memory addresses, host-slot ownership, and standalone orchestration. Dependency direction is downward only; this does not represent completion of an SoC/Chipyard integration.

## E. Deleted, moved, and merged code

| old path/symbol | action | reason | replacement / remaining owner | validation |
|---|---|---|---|---|
| `sim/ffi/im2p_gemmini_integrated.cpp` scheduling/runtime portions | MOVE/SPLIT | monolithic FFI file was the only owner of reusable scheduling and several unrelated runtime concerns | `sim/common/gemmini_schedule.{hpp,cpp}`, `sim/backends/gemmini_hp1/runtime.{hpp,cpp}`, `backing_memory.cpp`; C ABI remains in the original `.cpp` | six-profile integrated RTL PASS; 268 baseline/current logical-cycle cases exact; planner digest/extent regression exact |
| `sim/ffi/im2p_gemmini_integrated.h` | DELETE | redundant integrated-only header | stable declarations remain through `sim/ffi/im2p_verilator.h` / public simulator headers | six-profile host/simulator builds PASS; ABI fixture/build paths PASS |
| `src/gemmini/control/build.sbt` | MERGE | duplicate SBT build authority | `src/gemmini/build.sbt` projects/source sets | corrected aggregate Scala suite 49/49 PASS |
| `src/gemmini/control/project/build.properties` | MERGE | duplicate SBT version authority | `src/gemmini/project/build.properties` | corrected aggregate Scala suite 49/49 PASS |
| `src/gemmini/control/project/plugins.sbt` | MERGE | duplicate plugin authority | `src/gemmini/project/plugins.sbt` | corrected aggregate Scala suite 49/49 PASS |
| mixed responsibilities in old `0001-packed-input-controller-bytes.patch` | SPLIT | packed-byte accounting and loop-head reset are unrelated changes | `0001-packed-input-controller-bytes.patch` + `0002-loop-head-reset.patch` | fresh vendor verify PASS; effective patched upstream outputs byte-identical to pre-split result |
| portable scale ownership protocol formerly coupled to broader Scala sources | MOVE/EXTRACT | keep scalar SCU ownership types independent from host/orchestration state | `src/gemmini/src/main/scala/im2p/gemmini/ScaleProtocol.scala` | Scala suite PASS; six-profile integrated RTL PASS |

No production source was deleted merely for line-count reduction; the deletion/merge evidence is preserved in `/Users/zerogod/aisa-lab/build/im2p-gemmini/refactor-20260916T133723Z/deleted-source-evidence.json`.

## F. Build/vendor cleanup

`src/gemmini/build.sbt` is the single explicit SBT build authority. It defines `scuCore`, `gemminiIntegration`, the integrated standalone `root`, and test-only `diagnostics` source sets. The source lists make clear which IM2P sources compile at each layer and prevent original and patched copies of the same upstream class from being compiled together.

Pinned upstream provenance remains immutable and vendor verification is reproducible. Fresh verification completed successfully with `uv run scripts/gemmini_vendor.py --verify`.

The patch split is explicit in `vendor-manifest.json`:

- `0001-packed-input-controller-bytes.patch`: packed input bit-to-byte accounting.
- `0002-loop-head-reset.patch`: deterministic `LoopMatmul` head reset.

Effective pre/post split upstream overlay proof:

| upstream file | byte-identical | SHA-256 |
|---|---|---|
| `GemminiConfigs.scala` | PASS | `b5a34017a505501e7b0b0664cc87e59702f627516c8a0bb96a781e713876b111` |
| `LoadController.scala` | PASS | `fac877008b1c246c648c4752d8374e7ede3ff933cbc6cf37334e703b42560617` |
| `LoopMatmul.scala` | PASS | `da289d432fa38a2e9917210520703c3e13f5b2b161d6b991dcaa19229d90296c` |
| `StoreController.scala` | PASS | `4e2110f1ad97f23caa9a863678f4fb99f09506c03043181b959b782453cba810` |

## G. Schedule planner

The pure API is `sim/common/gemmini_schedule.hpp`. Its inputs are `GemmShape`, `HardwareShape`, `TilePlan`, `Layout`, and `ScheduleConfig`; its outputs include `LoopPlan`, `FragmentPlan`, and `ReadExtent`.

It owns only value-independent facts: padding, I/J/K loop decomposition, block-32 splitting, logical order, first/last and replace/accumulate intent, scale-domain planning, packed/host byte extents, fragment identity, and request extents. It does **not** read activation or weight values, compute MAC/SCU results, instantiate Verilator, call the C ABI, or implement a timing policy.

The integrated backend consumes this planner directly. Planner regression against the pre-refactor loop implementation is exact for all six profiles: work/case count, loop count, and schedule digest all match, and request/byte extent tests pass 6/6.

## H. Test summary

Final-gate tests/checks are classified separately from known baseline failures and from one preserved test-runner setup mistake.

| validation | executions / tests | PASS | FAIL | BASELINE_EXISTING_FAIL | NOT_RUN / note |
|---|---:|---:|---:|---:|---|
| integrated Gemmini profile matrix (`A4W4/A8W8 x DIM16/32/64`) | 6 profiles, 9 stages/profile = 54 stage commands | 54 | 0 | 0 | elaboration, lint, host build/CTest/audit, Verilated numerical RTL build+run |
| pure schedule regression | 6 profiles | 6 | 0 | 0 | work count, loop count, digest exact |
| request/byte extent regression | 6 profiles | 6 | 0 | 0 | exact |
| RTL logical-cycle probe build+run | 12 profile-side executions | 12 | 0 | 0 | baseline + current, six profiles each |
| RTL logical-cycle case comparison | 268 logical work cases | 268 | 0 | 0 | start/done/elapsed and request/response counters exact |
| Scala aggregate suite, corrected properties | 49 tests (24 + 25) | 49 | 0 | 0 | complete current suite PASS |
| Python focused pytest | 22 tests | 22 | 0 | 0 | `test_gemmini_build_cli.py`, `test_gemmini_vendor.py`, `test_gemmini_schedule.py` |
| `pyright` | 1 invocation | 1 | 0 | 0 | 0 errors, 0 warnings |
| Python bytecode compile | 1 invocation | 1 | 0 | 0 | modified/new Python files |
| `git diff --check` | 1 invocation | 1 | 0 | 0 | PASS |
| vendor verification | 1 fresh invocation | 1 | 0 | 0 | PASS |
| patch effective-output equivalence | 4 upstream files | 4 | 0 | 0 | all byte-identical |
| LEGACY_BSV lightweight smoke | 1 smoke flow | 1 | 0 | 0 | profile config/C API/RTL target/build resolution; not claimed as full legacy suite |
| frontend-real | 6 baseline + 6 current profile runs | 0 | 0 | 6 | baseline/current signature exact for every profile |
| initial Scala aggregate attempt without required test properties | 56 test bodies encountered (54 succeeded, 2 setup failures) | 54 | 2 setup | 0 | preserved as invalid harness invocation; corrected full rerun above is authoritative |

The invalid Scala attempt failed only because `HardwareContractSpec` requires `im2p.testProfile` and `im2p.resolvedHardware`; both test bodies passed in the corrected targeted rerun and the corrected full aggregate suite passed 49/49.

### frontend-real classification

Actual frontend-real baseline/current comparison:

| profile | baseline | current | signature |
|---|---|---|---|
| `a4w4-d16-hp1` | FAIL / `fixture-run` / 1 | FAIL / `fixture-run` / 1 | exact |
| `a4w4-d32-hp1` | FAIL / `fixture-run` / 1 | FAIL / `fixture-run` / 1 | exact |
| `a4w4-d64-hp1` | FAIL / `fixture-run` / 1 | FAIL / `fixture-run` / 1 | exact |
| `a8w8-d16-hp1` | FAIL / `fixture-run` / 1 | FAIL / `fixture-run` / 1 | exact |
| `a8w8-d32-hp1` | FAIL / `fixture-run` / 1 | FAIL / `fixture-run` / 1 | exact |
| `a8w8-d64-hp1` | FAIL / `fixture-run` / 1 | FAIL / `fixture-run` / 1 | exact |

Classification: **`BASELINE_EXISTING_FAIL`**, `introduced_by_refactor=false`, all six baseline/current signatures exact. For A4 the actual frontend-real runner signature is `dual-context execute failed route=q4_hp1: failed to start IM2P stream`; A8 uses the corresponding `q8_hp1` route. The previously recorded string `frontend WS outer-K oracle or coverage mismatch` belongs to a separate historical integrated runtime fixture, not the frontend-real runner itself. This distinction is preserved rather than rewriting either failure.

## I. RTL logical-cycle regression

Gate result: **PASS**.

- Compared profiles: all six `A4W4/A8W8 x DIM16/32/64`.
- Compared logical work cases: **268**.
- Every baseline/current full runtime log is byte-for-byte identical per profile.
- `max_abs_delta_cycles = 0`.
- Every case has exact `start_cycle`, `done_cycle`, `elapsed_cycles`, work count, loop/issuance count, load request/response count, store request/response count, and scale request/response count.
- Logical done remains tied to final required backing-store completion as exercised by the existing fixture.

Coverage map:

| required condition | profile | case |
|---|---|---|
| A4 packed path | `a4w4-d16-hp1` | `full-m2-r0-n3-k64-01` |
| A8 path | `a8w8-d16-hp1` | `full-m2-r0-n3-k64-01` |
| DIM64 K32 split | `a4w4-d64-hp1` | `full-m2-r0-n3-k64-01` |
| K <= DIM | `a4w4-d16-hp1` | `full-m81-r0-n3-k16-01` |
| K > DIM | `a4w4-d16-hp1` | `full-m2-r0-n3-k64-01` |
| block32 crossing | `a4w4-d16-hp1` | `full-m2-r0-n3-k64-01` |
| final store completion | `a4w4-d16-hp1` | `full-m2-r0-n3-k64-01` |
| multiple I tiles or stripes | `a4w4-d16-hp1` | `full-m129-r0-n129-k96-01` |
| multiple J tiles | `a4w4-d16-hp1` | `full-m129-r0-n129-k96-01` |
| replace -> accumulate | `a4w4-d16-hp1` | `full-m2-r0-n3-k64-01` |

Full exact comparison table:

| profile | case | baseline start | baseline done | baseline cycles | current start | current done | current cycles | delta | work | loops | load req/resp | store req/resp | scale req/resp |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| a4w4-d16-hp1 | full-m2-r0-n3-k64-01 | 422 | 832 | 410 | 422 | 832 | 410 | 0 | 1 | 1 | 72/72 | 2/2 | 2/2 |
| a4w4-d16-hp1 | full-m2-r0-n3-k64-02 | 866 | 1474 | 608 | 866 | 1474 | 608 | 0 | 1 | 1 | 72/72 | 2/2 | 2/2 |
| a4w4-d16-hp1 | full-m129-r0-n129-k96-01 | 1508 | 32179 | 30671 | 1508 | 32179 | 30671 | 0 | 1 | 4 | 3276/3276 | 1161/1161 | 54/54 |
| a4w4-d16-hp1 | pipeline-m64-r0-n129-k96-01 | 32325 | 47598 | 15273 | 32325 | 47598 | 15273 | 0 | 1 | 2 | 1632/1632 | 576/576 | 27/27 |
| a4w4-d16-hp1 | pipeline-m64-r64-n129-k96-01 | 47743 | 63015 | 15272 | 47743 | 63015 | 15272 | 0 | 1 | 2 | 1632/1632 | 576/576 | 27/27 |
| a4w4-d16-hp1 | pipeline-m1-r128-n129-k96-01 | 63160 | 67038 | 3878 | 63160 | 67038 | 3878 | 0 | 1 | 2 | 876/876 | 9/9 | 27/27 |
| a4w4-d16-hp1 | full-m1-r0-n1-k8256-01 | 67184 | 108436 | 41252 | 67184 | 108436 | 41252 | 0 | 1 | 2 | 8772/8772 | 1/1 | 258/258 |
| a4w4-d16-hp1 | full-m81-r0-n3-k16-01 | 108470 | 109918 | 1448 | 108470 | 109918 | 1448 | 0 | 1 | 1 | 97/97 | 81/81 | 1/1 |
| a4w4-d16-hp1 | full-m81-r0-n3-k16-02 | 109936 | 111383 | 1447 | 109936 | 111383 | 1447 | 0 | 1 | 1 | 97/97 | 81/81 | 1/1 |
| a4w4-d16-hp1 | full-m27-r0-n3-k5-01 | 111401 | 112038 | 637 | 111401 | 112038 | 637 | 0 | 1 | 1 | 43/43 | 27/27 | 1/1 |
| a4w4-d16-hp1 | full-m27-r0-n3-k16-01 | 112056 | 112693 | 637 | 112056 | 112693 | 637 | 0 | 1 | 1 | 43/43 | 27/27 | 1/1 |
| a4w4-d16-hp1 | full-m27-r0-n3-k16-02 | 112711 | 113348 | 637 | 112711 | 113348 | 637 | 0 | 1 | 1 | 43/43 | 27/27 | 1/1 |
| a4w4-d16-hp1 | full-m9-r0-n3-k5-01 | 113366 | 113710 | 344 | 113366 | 113710 | 344 | 0 | 1 | 1 | 25/25 | 9/9 | 1/1 |
| a4w4-d16-hp1 | full-m27-r0-n3-k16-03 | 113728 | 114365 | 637 | 113728 | 114365 | 637 | 0 | 1 | 1 | 43/43 | 27/27 | 1/1 |
| a4w4-d16-hp1 | full-m27-r0-n3-k16-04 | 114383 | 115020 | 637 | 114383 | 115020 | 637 | 0 | 1 | 1 | 43/43 | 27/27 | 1/1 |
| a4w4-d16-hp1 | full-m9-r0-n3-k5-02 | 115038 | 115383 | 345 | 115038 | 115383 | 345 | 0 | 1 | 1 | 25/25 | 9/9 | 1/1 |
| a4w4-d16-hp1 | full-m27-r0-n3-k16-05 | 115401 | 116038 | 637 | 115401 | 116038 | 637 | 0 | 1 | 1 | 43/43 | 27/27 | 1/1 |
| a4w4-d16-hp1 | full-m27-r0-n3-k16-06 | 116056 | 116693 | 637 | 116056 | 116693 | 637 | 0 | 1 | 1 | 43/43 | 27/27 | 1/1 |
| a4w4-d16-hp1 | full-m9-r0-n3-k5-03 | 116711 | 117055 | 344 | 116711 | 117055 | 344 | 0 | 1 | 1 | 25/25 | 9/9 | 1/1 |
| a4w4-d16-hp1 | full-m81-r0-n3-k16-03 | 117073 | 118520 | 1447 | 117073 | 118520 | 1447 | 0 | 1 | 1 | 97/97 | 81/81 | 1/1 |
| a4w4-d16-hp1 | full-m81-r0-n3-k16-04 | 118538 | 119985 | 1447 | 118538 | 119985 | 1447 | 0 | 1 | 1 | 97/97 | 81/81 | 1/1 |
| a4w4-d16-hp1 | full-m81-r0-n3-k16-05 | 120003 | 121450 | 1447 | 120003 | 121450 | 1447 | 0 | 1 | 1 | 97/97 | 81/81 | 1/1 |
| a4w4-d16-hp1 | full-m81-r0-n3-k16-06 | 121468 | 122915 | 1447 | 121468 | 122915 | 1447 | 0 | 1 | 1 | 97/97 | 81/81 | 1/1 |
| a4w4-d16-hp1 | full-m81-r0-n3-k16-07 | 122933 | 124380 | 1447 | 122933 | 124380 | 1447 | 0 | 1 | 1 | 97/97 | 81/81 | 1/1 |
| a4w4-d16-hp1 | full-m1-r0-n1-k32-01 | 124398 | 124653 | 255 | 124398 | 124653 | 255 | 0 | 1 | 1 | 34/34 | 1/1 | 1/1 |
| a4w4-d16-hp1 | full-m1-r0-n1-k32-02 | 124671 | 124926 | 255 | 124671 | 124926 | 255 | 0 | 1 | 1 | 34/34 | 1/1 | 1/1 |
| a4w4-d16-hp1 | full-m9-r0-n3-k64-01 | 124944 | 125512 | 568 | 124944 | 125512 | 568 | 0 | 1 | 1 | 100/100 | 9/9 | 2/2 |
| a4w4-d16-hp1 | full-m27-r0-n3-k16-07 | 125546 | 126183 | 637 | 125546 | 126183 | 637 | 0 | 1 | 1 | 43/43 | 27/27 | 1/1 |
| a4w4-d16-hp1 | full-m27-r0-n3-k16-08 | 126201 | 126838 | 637 | 126201 | 126838 | 637 | 0 | 1 | 1 | 43/43 | 27/27 | 1/1 |
| a4w4-d16-hp1 | full-m9-r0-n3-k5-04 | 126856 | 127200 | 344 | 126856 | 127200 | 344 | 0 | 1 | 1 | 25/25 | 9/9 | 1/1 |
| a4w4-d16-hp1 | full-m27-r0-n3-k16-09 | 127218 | 127855 | 637 | 127218 | 127855 | 637 | 0 | 1 | 1 | 43/43 | 27/27 | 1/1 |
| a4w4-d16-hp1 | full-m27-r0-n3-k16-10 | 127873 | 128510 | 637 | 127873 | 128510 | 637 | 0 | 1 | 1 | 43/43 | 27/27 | 1/1 |
| a4w4-d16-hp1 | full-m9-r0-n3-k5-05 | 128528 | 128873 | 345 | 128528 | 128873 | 345 | 0 | 1 | 1 | 25/25 | 9/9 | 1/1 |
| a4w4-d16-hp1 | full-m27-r0-n3-k16-11 | 128891 | 129528 | 637 | 128891 | 129528 | 637 | 0 | 1 | 1 | 43/43 | 27/27 | 1/1 |
| a4w4-d16-hp1 | full-m27-r0-n3-k16-12 | 129546 | 130183 | 637 | 129546 | 130183 | 637 | 0 | 1 | 1 | 43/43 | 27/27 | 1/1 |
| a4w4-d16-hp1 | full-m9-r0-n3-k5-06 | 130201 | 130545 | 344 | 130201 | 130545 | 344 | 0 | 1 | 1 | 25/25 | 9/9 | 1/1 |
| a4w4-d16-hp1 | pipeline-m3-r0-n3-k64-01 | 130563 | 131005 | 442 | 130563 | 131005 | 442 | 0 | 1 | 1 | 76/76 | 3/3 | 2/2 |
| a4w4-d16-hp1 | full-m27-r0-n3-k16-13 | 131039 | 131675 | 636 | 131039 | 131675 | 636 | 0 | 1 | 1 | 43/43 | 27/27 | 1/1 |
| a4w4-d16-hp1 | full-m27-r0-n3-k16-14 | 131693 | 132330 | 637 | 131693 | 132330 | 637 | 0 | 1 | 1 | 43/43 | 27/27 | 1/1 |
| a4w4-d16-hp1 | full-m9-r0-n3-k5-07 | 132348 | 132693 | 345 | 132348 | 132693 | 345 | 0 | 1 | 1 | 25/25 | 9/9 | 1/1 |
| a4w4-d16-hp1 | pipeline-m3-r3-n3-k64-01 | 132710 | 133150 | 440 | 132710 | 133150 | 440 | 0 | 1 | 1 | 76/76 | 3/3 | 2/2 |
| a4w4-d16-hp1 | full-m27-r0-n3-k16-15 | 133184 | 133820 | 636 | 133184 | 133820 | 636 | 0 | 1 | 1 | 43/43 | 27/27 | 1/1 |
| a4w4-d16-hp1 | full-m27-r0-n3-k16-16 | 133838 | 134475 | 637 | 133838 | 134475 | 637 | 0 | 1 | 1 | 43/43 | 27/27 | 1/1 |
| a4w4-d16-hp1 | full-m9-r0-n3-k5-08 | 134493 | 134838 | 345 | 134493 | 134838 | 345 | 0 | 1 | 1 | 25/25 | 9/9 | 1/1 |
| a4w4-d16-hp1 | pipeline-m3-r6-n3-k64-01 | 134856 | 135295 | 439 | 134856 | 135295 | 439 | 0 | 1 | 1 | 76/76 | 3/3 | 2/2 |
| a4w4-d16-hp1 | full-m27-r0-n3-k16-17 | 135329 | 135965 | 636 | 135329 | 135965 | 636 | 0 | 1 | 1 | 43/43 | 27/27 | 1/1 |
| a4w4-d16-hp1 | full-m27-r0-n3-k16-18 | 135983 | 136620 | 637 | 135983 | 136620 | 637 | 0 | 1 | 1 | 43/43 | 27/27 | 1/1 |
| a4w4-d16-hp1 | full-m9-r0-n3-k5-09 | 136638 | 136983 | 345 | 136638 | 136983 | 345 | 0 | 1 | 1 | 25/25 | 9/9 | 1/1 |
| a4w4-d16-hp1 | full-m9-r0-n3-k64-02 | 137001 | 137567 | 566 | 137001 | 137567 | 566 | 0 | 1 | 1 | 100/100 | 9/9 | 2/2 |
| a4w4-d16-hp1 | full-m27-r0-n3-k16-19 | 137601 | 138238 | 637 | 137601 | 138238 | 637 | 0 | 1 | 1 | 43/43 | 27/27 | 1/1 |
| a4w4-d16-hp1 | pipeline-m3-r0-n3-k64-02 | 138256 | 138695 | 439 | 138256 | 138695 | 439 | 0 | 1 | 1 | 76/76 | 3/3 | 2/2 |
| a4w4-d16-hp1 | full-m27-r0-n3-k16-20 | 138729 | 139365 | 636 | 138729 | 139365 | 636 | 0 | 1 | 1 | 43/43 | 27/27 | 1/1 |
| a4w4-d32-hp1 | full-m2-r0-n3-k64-01 | 589 | 1092 | 503 | 589 | 1092 | 503 | 0 | 1 | 1 | 68/68 | 2/2 | 2/2 |
| a4w4-d32-hp1 | full-m2-r0-n3-k64-02 | 1158 | 1888 | 730 | 1158 | 1888 | 730 | 0 | 1 | 1 | 68/68 | 2/2 | 2/2 |
| a4w4-d32-hp1 | full-m129-r0-n129-k96-01 | 1954 | 22851 | 20897 | 1954 | 22851 | 20897 | 0 | 1 | 6 | 2214/2214 | 645/645 | 45/45 |
| a4w4-d32-hp1 | pipeline-m64-r0-n129-k96-01 | 22949 | 31779 | 8830 | 22949 | 31779 | 8830 | 0 | 1 | 2 | 864/864 | 320/320 | 15/15 |
| a4w4-d32-hp1 | pipeline-m64-r64-n129-k96-01 | 31876 | 40704 | 8828 | 31876 | 40704 | 8828 | 0 | 1 | 2 | 864/864 | 320/320 | 15/15 |
| a4w4-d32-hp1 | pipeline-m1-r128-n129-k96-01 | 40801 | 43846 | 3045 | 40801 | 43846 | 3045 | 0 | 1 | 2 | 486/486 | 5/5 | 15/15 |
| a4w4-d32-hp1 | full-m1-r0-n1-k8256-01 | 43944 | 96404 | 52460 | 43944 | 96404 | 52460 | 0 | 1 | 3 | 8514/8514 | 1/1 | 258/258 |
| a4w4-d32-hp1 | full-m81-r0-n3-k32-01 | 96470 | 98093 | 1623 | 96470 | 98093 | 1623 | 0 | 1 | 1 | 113/113 | 81/81 | 1/1 |
| a4w4-d32-hp1 | full-m27-r0-n3-k5-01 | 98127 | 98919 | 792 | 98127 | 98919 | 792 | 0 | 1 | 1 | 59/59 | 27/27 | 1/1 |
| a4w4-d32-hp1 | full-m27-r0-n3-k32-01 | 98953 | 99746 | 793 | 98953 | 99746 | 793 | 0 | 1 | 1 | 59/59 | 27/27 | 1/1 |
| a4w4-d32-hp1 | full-m9-r0-n3-k5-01 | 99780 | 100238 | 458 | 99780 | 100238 | 458 | 0 | 1 | 1 | 41/41 | 9/9 | 1/1 |
| a4w4-d32-hp1 | full-m27-r0-n3-k32-02 | 100272 | 101064 | 792 | 100272 | 101064 | 792 | 0 | 1 | 1 | 59/59 | 27/27 | 1/1 |
| a4w4-d32-hp1 | full-m9-r0-n3-k5-02 | 101098 | 101558 | 460 | 101098 | 101558 | 460 | 0 | 1 | 1 | 41/41 | 9/9 | 1/1 |
| a4w4-d32-hp1 | full-m27-r0-n3-k32-03 | 101592 | 102384 | 792 | 101592 | 102384 | 792 | 0 | 1 | 1 | 59/59 | 27/27 | 1/1 |
| a4w4-d32-hp1 | full-m9-r0-n3-k5-03 | 102418 | 102878 | 460 | 102418 | 102878 | 460 | 0 | 1 | 1 | 41/41 | 9/9 | 1/1 |
| a4w4-d32-hp1 | full-m81-r0-n3-k32-02 | 102912 | 104536 | 1624 | 102912 | 104536 | 1624 | 0 | 1 | 1 | 113/113 | 81/81 | 1/1 |
| a4w4-d32-hp1 | full-m81-r0-n3-k32-03 | 104570 | 106193 | 1623 | 104570 | 106193 | 1623 | 0 | 1 | 1 | 113/113 | 81/81 | 1/1 |
| a4w4-d32-hp1 | full-m81-r0-n3-k32-04 | 106227 | 107851 | 1624 | 106227 | 107851 | 1624 | 0 | 1 | 1 | 113/113 | 81/81 | 1/1 |
| a4w4-d32-hp1 | full-m81-r0-n3-k32-05 | 107885 | 109508 | 1623 | 107885 | 109508 | 1623 | 0 | 1 | 1 | 113/113 | 81/81 | 1/1 |
| a4w4-d32-hp1 | full-m1-r0-n1-k32-01 | 109542 | 109858 | 316 | 109542 | 109858 | 316 | 0 | 1 | 1 | 33/33 | 1/1 | 1/1 |
| a4w4-d32-hp1 | full-m1-r0-n1-k32-02 | 109892 | 110208 | 316 | 109892 | 110208 | 316 | 0 | 1 | 1 | 33/33 | 1/1 | 1/1 |
| a4w4-d32-hp1 | full-m9-r0-n3-k64-01 | 110242 | 110887 | 645 | 110242 | 110887 | 645 | 0 | 1 | 1 | 82/82 | 9/9 | 2/2 |
| a4w4-d32-hp1 | full-m27-r0-n3-k32-04 | 110953 | 111746 | 793 | 110953 | 111746 | 793 | 0 | 1 | 1 | 59/59 | 27/27 | 1/1 |
| a4w4-d32-hp1 | full-m9-r0-n3-k5-04 | 111780 | 112238 | 458 | 111780 | 112238 | 458 | 0 | 1 | 1 | 41/41 | 9/9 | 1/1 |
| a4w4-d32-hp1 | full-m27-r0-n3-k32-05 | 112272 | 113064 | 792 | 112272 | 113064 | 792 | 0 | 1 | 1 | 59/59 | 27/27 | 1/1 |
| a4w4-d32-hp1 | full-m9-r0-n3-k5-05 | 113098 | 113558 | 460 | 113098 | 113558 | 460 | 0 | 1 | 1 | 41/41 | 9/9 | 1/1 |
| a4w4-d32-hp1 | full-m27-r0-n3-k32-06 | 113592 | 114384 | 792 | 113592 | 114384 | 792 | 0 | 1 | 1 | 59/59 | 27/27 | 1/1 |
| a4w4-d32-hp1 | full-m9-r0-n3-k5-06 | 114418 | 114878 | 460 | 114418 | 114878 | 460 | 0 | 1 | 1 | 41/41 | 9/9 | 1/1 |
| a4w4-d32-hp1 | pipeline-m3-r0-n3-k64-01 | 114912 | 115441 | 529 | 114912 | 115441 | 529 | 0 | 1 | 1 | 70/70 | 3/3 | 2/2 |
| a4w4-d32-hp1 | full-m27-r0-n3-k32-07 | 115507 | 116299 | 792 | 115507 | 116299 | 792 | 0 | 1 | 1 | 59/59 | 27/27 | 1/1 |
| a4w4-d32-hp1 | full-m9-r0-n3-k5-07 | 116333 | 116793 | 460 | 116333 | 116793 | 460 | 0 | 1 | 1 | 41/41 | 9/9 | 1/1 |
| a4w4-d32-hp1 | pipeline-m3-r3-n3-k64-01 | 116826 | 117353 | 527 | 116826 | 117353 | 527 | 0 | 1 | 1 | 70/70 | 3/3 | 2/2 |
| a4w4-d32-hp1 | full-m27-r0-n3-k32-08 | 117419 | 118211 | 792 | 117419 | 118211 | 792 | 0 | 1 | 1 | 59/59 | 27/27 | 1/1 |
| a4w4-d32-hp1 | full-m9-r0-n3-k5-08 | 118245 | 118703 | 458 | 118245 | 118703 | 458 | 0 | 1 | 1 | 41/41 | 9/9 | 1/1 |
| a4w4-d32-hp1 | pipeline-m3-r6-n3-k64-01 | 118737 | 119266 | 529 | 118737 | 119266 | 529 | 0 | 1 | 1 | 70/70 | 3/3 | 2/2 |
| a4w4-d32-hp1 | full-m27-r0-n3-k32-09 | 119332 | 120124 | 792 | 119332 | 120124 | 792 | 0 | 1 | 1 | 59/59 | 27/27 | 1/1 |
| a4w4-d32-hp1 | full-m9-r0-n3-k5-09 | 120158 | 120618 | 460 | 120158 | 120618 | 460 | 0 | 1 | 1 | 41/41 | 9/9 | 1/1 |
| a4w4-d32-hp1 | full-m9-r0-n3-k64-02 | 120652 | 121297 | 645 | 120652 | 121297 | 645 | 0 | 1 | 1 | 82/82 | 9/9 | 2/2 |
| a4w4-d32-hp1 | full-m27-r0-n3-k32-10 | 121363 | 122156 | 793 | 121363 | 122156 | 793 | 0 | 1 | 1 | 59/59 | 27/27 | 1/1 |
| a4w4-d32-hp1 | pipeline-m3-r0-n3-k64-02 | 122190 | 122718 | 528 | 122190 | 122718 | 528 | 0 | 1 | 1 | 70/70 | 3/3 | 2/2 |
| a4w4-d32-hp1 | full-m27-r0-n3-k32-11 | 122784 | 123576 | 792 | 122784 | 123576 | 792 | 0 | 1 | 1 | 59/59 | 27/27 | 1/1 |
| a4w4-d64-hp1 | full-m2-r0-n3-k64-01 | 929 | 2035 | 1106 | 929 | 2035 | 1106 | 0 | 1 | 2 | 132/132 | 2/2 | 2/2 |
| a4w4-d64-hp1 | full-m2-r0-n3-k64-02 | 2101 | 3469 | 1368 | 2101 | 3469 | 1368 | 0 | 1 | 2 | 132/132 | 2/2 | 2/2 |
| a4w4-d64-hp1 | full-m129-r0-n129-k96-01 | 3535 | 24729 | 21194 | 3535 | 24729 | 21194 | 0 | 1 | 18 | 2502/2502 | 387/387 | 27/27 |
| a4w4-d64-hp1 | pipeline-m64-r0-n129-k96-01 | 24795 | 33152 | 8357 | 24795 | 33152 | 8357 | 0 | 1 | 6 | 960/960 | 192/192 | 9/9 |
| a4w4-d64-hp1 | pipeline-m64-r64-n129-k96-01 | 33217 | 41577 | 8360 | 33217 | 41577 | 8360 | 0 | 1 | 6 | 960/960 | 192/192 | 9/9 |
| a4w4-d64-hp1 | pipeline-m1-r128-n129-k96-01 | 41642 | 45989 | 4347 | 41642 | 45989 | 4347 | 0 | 1 | 6 | 582/582 | 3/3 | 9/9 |
| a4w4-d64-hp1 | full-m1-r0-n1-k8256-01 | 46055 | 193064 | 147009 | 46055 | 193064 | 147009 | 0 | 1 | 258 | 16770/16770 | 1/1 | 258/258 |
| a4w4-d64-hp1 | full-m81-r0-n3-k32-01 | 193130 | 195063 | 1933 | 193130 | 195063 | 1933 | 0 | 1 | 1 | 145/145 | 81/81 | 1/1 |
| a4w4-d64-hp1 | full-m27-r0-n3-k5-01 | 195129 | 196125 | 996 | 195129 | 196125 | 996 | 0 | 1 | 1 | 91/91 | 27/27 | 1/1 |
| a4w4-d64-hp1 | full-m27-r0-n3-k32-01 | 196191 | 197185 | 994 | 196191 | 197185 | 994 | 0 | 1 | 1 | 91/91 | 27/27 | 1/1 |
| a4w4-d64-hp1 | full-m9-r0-n3-k5-01 | 197251 | 197912 | 661 | 197251 | 197912 | 661 | 0 | 1 | 1 | 73/73 | 9/9 | 1/1 |
| a4w4-d64-hp1 | full-m27-r0-n3-k32-02 | 197978 | 198975 | 997 | 197978 | 198975 | 997 | 0 | 1 | 1 | 91/91 | 27/27 | 1/1 |
| a4w4-d64-hp1 | full-m9-r0-n3-k5-02 | 199041 | 199702 | 661 | 199041 | 199702 | 661 | 0 | 1 | 1 | 73/73 | 9/9 | 1/1 |
| a4w4-d64-hp1 | full-m27-r0-n3-k32-03 | 199768 | 200765 | 997 | 199768 | 200765 | 997 | 0 | 1 | 1 | 91/91 | 27/27 | 1/1 |
| a4w4-d64-hp1 | full-m9-r0-n3-k5-03 | 200831 | 201492 | 661 | 200831 | 201492 | 661 | 0 | 1 | 1 | 73/73 | 9/9 | 1/1 |
| a4w4-d64-hp1 | full-m81-r0-n3-k32-02 | 201558 | 203493 | 1935 | 201558 | 203493 | 1935 | 0 | 1 | 1 | 145/145 | 81/81 | 1/1 |
| a4w4-d64-hp1 | full-m81-r0-n3-k32-03 | 203559 | 205493 | 1934 | 203559 | 205493 | 1934 | 0 | 1 | 1 | 145/145 | 81/81 | 1/1 |
| a4w4-d64-hp1 | full-m81-r0-n3-k32-04 | 205559 | 207493 | 1934 | 205559 | 207493 | 1934 | 0 | 1 | 1 | 145/145 | 81/81 | 1/1 |
| a4w4-d64-hp1 | full-m81-r0-n3-k32-05 | 207559 | 209493 | 1934 | 207559 | 209493 | 1934 | 0 | 1 | 1 | 145/145 | 81/81 | 1/1 |
| a4w4-d64-hp1 | full-m1-r0-n1-k32-01 | 209559 | 210079 | 520 | 209559 | 210079 | 520 | 0 | 1 | 1 | 65/65 | 1/1 | 1/1 |
| a4w4-d64-hp1 | full-m1-r0-n1-k32-02 | 210145 | 210664 | 519 | 210145 | 210664 | 519 | 0 | 1 | 1 | 65/65 | 1/1 | 1/1 |
| a4w4-d64-hp1 | full-m9-r0-n3-k64-01 | 210730 | 211982 | 1252 | 210730 | 211982 | 1252 | 0 | 1 | 2 | 146/146 | 9/9 | 2/2 |
| a4w4-d64-hp1 | full-m27-r0-n3-k32-04 | 212048 | 213045 | 997 | 212048 | 213045 | 997 | 0 | 1 | 1 | 91/91 | 27/27 | 1/1 |
| a4w4-d64-hp1 | full-m9-r0-n3-k5-04 | 213111 | 213772 | 661 | 213111 | 213772 | 661 | 0 | 1 | 1 | 73/73 | 9/9 | 1/1 |
| a4w4-d64-hp1 | full-m27-r0-n3-k32-05 | 213838 | 214835 | 997 | 213838 | 214835 | 997 | 0 | 1 | 1 | 91/91 | 27/27 | 1/1 |
| a4w4-d64-hp1 | full-m9-r0-n3-k5-05 | 214901 | 215562 | 661 | 214901 | 215562 | 661 | 0 | 1 | 1 | 73/73 | 9/9 | 1/1 |
| a4w4-d64-hp1 | full-m27-r0-n3-k32-06 | 215628 | 216625 | 997 | 215628 | 216625 | 997 | 0 | 1 | 1 | 91/91 | 27/27 | 1/1 |
| a4w4-d64-hp1 | full-m9-r0-n3-k5-06 | 216691 | 217352 | 661 | 216691 | 217352 | 661 | 0 | 1 | 1 | 73/73 | 9/9 | 1/1 |
| a4w4-d64-hp1 | pipeline-m3-r0-n3-k64-01 | 217418 | 218551 | 1133 | 217418 | 218551 | 1133 | 0 | 1 | 2 | 134/134 | 3/3 | 2/2 |
| a4w4-d64-hp1 | full-m27-r0-n3-k32-07 | 218617 | 219612 | 995 | 218617 | 219612 | 995 | 0 | 1 | 1 | 91/91 | 27/27 | 1/1 |
| a4w4-d64-hp1 | full-m9-r0-n3-k5-07 | 219678 | 220342 | 664 | 219678 | 220342 | 664 | 0 | 1 | 1 | 73/73 | 9/9 | 1/1 |
| a4w4-d64-hp1 | pipeline-m3-r3-n3-k64-01 | 220407 | 221538 | 1131 | 220407 | 221538 | 1131 | 0 | 1 | 2 | 134/134 | 3/3 | 2/2 |
| a4w4-d64-hp1 | full-m27-r0-n3-k32-08 | 221604 | 222600 | 996 | 221604 | 222600 | 996 | 0 | 1 | 1 | 91/91 | 27/27 | 1/1 |
| a4w4-d64-hp1 | full-m9-r0-n3-k5-08 | 222666 | 223327 | 661 | 222666 | 223327 | 661 | 0 | 1 | 1 | 73/73 | 9/9 | 1/1 |
| a4w4-d64-hp1 | pipeline-m3-r6-n3-k64-01 | 223393 | 224526 | 1133 | 223393 | 224526 | 1133 | 0 | 1 | 2 | 134/134 | 3/3 | 2/2 |
| a4w4-d64-hp1 | full-m27-r0-n3-k32-09 | 224592 | 225587 | 995 | 224592 | 225587 | 995 | 0 | 1 | 1 | 91/91 | 27/27 | 1/1 |
| a4w4-d64-hp1 | full-m9-r0-n3-k5-09 | 225653 | 226317 | 664 | 225653 | 226317 | 664 | 0 | 1 | 1 | 73/73 | 9/9 | 1/1 |
| a4w4-d64-hp1 | full-m9-r0-n3-k64-02 | 226383 | 227637 | 1254 | 226383 | 227637 | 1254 | 0 | 1 | 2 | 146/146 | 9/9 | 2/2 |
| a4w4-d64-hp1 | full-m27-r0-n3-k32-10 | 227703 | 228700 | 997 | 227703 | 228700 | 997 | 0 | 1 | 1 | 91/91 | 27/27 | 1/1 |
| a4w4-d64-hp1 | pipeline-m3-r0-n3-k64-02 | 228766 | 229896 | 1130 | 228766 | 229896 | 1130 | 0 | 1 | 2 | 134/134 | 3/3 | 2/2 |
| a4w4-d64-hp1 | full-m27-r0-n3-k32-11 | 229962 | 230957 | 995 | 229962 | 230957 | 995 | 0 | 1 | 1 | 91/91 | 27/27 | 1/1 |
| a8w8-d16-hp1 | full-m2-r0-n3-k64-01 | 422 | 832 | 410 | 422 | 832 | 410 | 0 | 1 | 1 | 72/72 | 2/2 | 2/2 |
| a8w8-d16-hp1 | full-m2-r0-n3-k64-02 | 866 | 1474 | 608 | 866 | 1474 | 608 | 0 | 1 | 1 | 72/72 | 2/2 | 2/2 |
| a8w8-d16-hp1 | full-m129-r0-n129-k96-01 | 1508 | 32179 | 30671 | 1508 | 32179 | 30671 | 0 | 1 | 4 | 3276/3276 | 1161/1161 | 54/54 |
| a8w8-d16-hp1 | pipeline-m64-r0-n129-k96-01 | 32325 | 47598 | 15273 | 32325 | 47598 | 15273 | 0 | 1 | 2 | 1632/1632 | 576/576 | 27/27 |
| a8w8-d16-hp1 | pipeline-m64-r64-n129-k96-01 | 47743 | 63015 | 15272 | 47743 | 63015 | 15272 | 0 | 1 | 2 | 1632/1632 | 576/576 | 27/27 |
| a8w8-d16-hp1 | pipeline-m1-r128-n129-k96-01 | 63160 | 67038 | 3878 | 63160 | 67038 | 3878 | 0 | 1 | 2 | 876/876 | 9/9 | 27/27 |
| a8w8-d16-hp1 | full-m1-r0-n1-k8256-01 | 67184 | 108511 | 41327 | 67184 | 108511 | 41327 | 0 | 1 | 3 | 8772/8772 | 1/1 | 258/258 |
| a8w8-d16-hp1 | full-m45-r0-n3-k16-01 | 108545 | 109453 | 908 | 108545 | 109453 | 908 | 0 | 1 | 1 | 61/61 | 45/45 | 1/1 |
| a8w8-d16-hp1 | full-m45-r0-n3-k16-02 | 109471 | 110378 | 907 | 109471 | 110378 | 907 | 0 | 1 | 1 | 61/61 | 45/45 | 1/1 |
| a8w8-d16-hp1 | full-m27-r0-n3-k5-01 | 110396 | 111033 | 637 | 110396 | 111033 | 637 | 0 | 1 | 1 | 43/43 | 27/27 | 1/1 |
| a8w8-d16-hp1 | full-m15-r0-n3-k16-01 | 111051 | 111506 | 455 | 111051 | 111506 | 455 | 0 | 1 | 1 | 31/31 | 15/15 | 1/1 |
| a8w8-d16-hp1 | full-m15-r0-n3-k16-02 | 111524 | 111979 | 455 | 111524 | 111979 | 455 | 0 | 1 | 1 | 31/31 | 15/15 | 1/1 |
| a8w8-d16-hp1 | full-m9-r0-n3-k5-01 | 111997 | 112343 | 346 | 111997 | 112343 | 346 | 0 | 1 | 1 | 25/25 | 9/9 | 1/1 |
| a8w8-d16-hp1 | full-m15-r0-n3-k16-03 | 112361 | 112816 | 455 | 112361 | 112816 | 455 | 0 | 1 | 1 | 31/31 | 15/15 | 1/1 |
| a8w8-d16-hp1 | full-m15-r0-n3-k16-04 | 112834 | 113289 | 455 | 112834 | 113289 | 455 | 0 | 1 | 1 | 31/31 | 15/15 | 1/1 |
| a8w8-d16-hp1 | full-m9-r0-n3-k5-02 | 113307 | 113653 | 346 | 113307 | 113653 | 346 | 0 | 1 | 1 | 25/25 | 9/9 | 1/1 |
| a8w8-d16-hp1 | full-m15-r0-n3-k16-05 | 113671 | 114126 | 455 | 113671 | 114126 | 455 | 0 | 1 | 1 | 31/31 | 15/15 | 1/1 |
| a8w8-d16-hp1 | full-m15-r0-n3-k16-06 | 114144 | 114599 | 455 | 114144 | 114599 | 455 | 0 | 1 | 1 | 31/31 | 15/15 | 1/1 |
| a8w8-d16-hp1 | full-m9-r0-n3-k5-03 | 114617 | 114963 | 346 | 114617 | 114963 | 346 | 0 | 1 | 1 | 25/25 | 9/9 | 1/1 |
| a8w8-d16-hp1 | full-m45-r0-n3-k16-03 | 114981 | 115888 | 907 | 114981 | 115888 | 907 | 0 | 1 | 1 | 61/61 | 45/45 | 1/1 |
| a8w8-d16-hp1 | full-m45-r0-n3-k16-04 | 115906 | 116813 | 907 | 115906 | 116813 | 907 | 0 | 1 | 1 | 61/61 | 45/45 | 1/1 |
| a8w8-d16-hp1 | full-m45-r0-n3-k16-05 | 116831 | 117738 | 907 | 116831 | 117738 | 907 | 0 | 1 | 1 | 61/61 | 45/45 | 1/1 |
| a8w8-d16-hp1 | full-m45-r0-n3-k16-06 | 117756 | 118663 | 907 | 117756 | 118663 | 907 | 0 | 1 | 1 | 61/61 | 45/45 | 1/1 |
| a8w8-d16-hp1 | full-m45-r0-n3-k16-07 | 118681 | 119588 | 907 | 118681 | 119588 | 907 | 0 | 1 | 1 | 61/61 | 45/45 | 1/1 |
| a8w8-d16-hp1 | full-m1-r0-n1-k32-01 | 119606 | 119861 | 255 | 119606 | 119861 | 255 | 0 | 1 | 1 | 34/34 | 1/1 | 1/1 |
| a8w8-d16-hp1 | full-m1-r0-n1-k32-02 | 119879 | 120133 | 254 | 119879 | 120133 | 254 | 0 | 1 | 1 | 34/34 | 1/1 | 1/1 |
| a8w8-d16-hp1 | full-m9-r0-n3-k64-01 | 120151 | 120717 | 566 | 120151 | 120717 | 566 | 0 | 1 | 1 | 100/100 | 9/9 | 2/2 |
| a8w8-d16-hp1 | full-m15-r0-n3-k16-07 | 120751 | 121206 | 455 | 120751 | 121206 | 455 | 0 | 1 | 1 | 31/31 | 15/15 | 1/1 |
| a8w8-d16-hp1 | full-m15-r0-n3-k16-08 | 121224 | 121679 | 455 | 121224 | 121679 | 455 | 0 | 1 | 1 | 31/31 | 15/15 | 1/1 |
| a8w8-d16-hp1 | full-m9-r0-n3-k5-04 | 121697 | 122043 | 346 | 121697 | 122043 | 346 | 0 | 1 | 1 | 25/25 | 9/9 | 1/1 |
| a8w8-d16-hp1 | full-m15-r0-n3-k16-09 | 122061 | 122516 | 455 | 122061 | 122516 | 455 | 0 | 1 | 1 | 31/31 | 15/15 | 1/1 |
| a8w8-d16-hp1 | full-m15-r0-n3-k16-10 | 122534 | 122989 | 455 | 122534 | 122989 | 455 | 0 | 1 | 1 | 31/31 | 15/15 | 1/1 |
| a8w8-d16-hp1 | full-m9-r0-n3-k5-05 | 123007 | 123353 | 346 | 123007 | 123353 | 346 | 0 | 1 | 1 | 25/25 | 9/9 | 1/1 |
| a8w8-d16-hp1 | full-m15-r0-n3-k16-11 | 123371 | 123826 | 455 | 123371 | 123826 | 455 | 0 | 1 | 1 | 31/31 | 15/15 | 1/1 |
| a8w8-d16-hp1 | full-m15-r0-n3-k16-12 | 123844 | 124299 | 455 | 123844 | 124299 | 455 | 0 | 1 | 1 | 31/31 | 15/15 | 1/1 |
| a8w8-d16-hp1 | full-m9-r0-n3-k5-06 | 124317 | 124663 | 346 | 124317 | 124663 | 346 | 0 | 1 | 1 | 25/25 | 9/9 | 1/1 |
| a8w8-d16-hp1 | pipeline-m3-r0-n3-k64-01 | 124681 | 125120 | 439 | 124681 | 125120 | 439 | 0 | 1 | 1 | 76/76 | 3/3 | 2/2 |
| a8w8-d16-hp1 | full-m15-r0-n3-k16-13 | 125154 | 125609 | 455 | 125154 | 125609 | 455 | 0 | 1 | 1 | 31/31 | 15/15 | 1/1 |
| a8w8-d16-hp1 | full-m15-r0-n3-k16-14 | 125627 | 126084 | 457 | 125627 | 126084 | 457 | 0 | 1 | 1 | 31/31 | 15/15 | 1/1 |
| a8w8-d16-hp1 | full-m9-r0-n3-k5-07 | 126102 | 126448 | 346 | 126102 | 126448 | 346 | 0 | 1 | 1 | 25/25 | 9/9 | 1/1 |
| a8w8-d16-hp1 | pipeline-m3-r3-n3-k64-01 | 126465 | 126905 | 440 | 126465 | 126905 | 440 | 0 | 1 | 1 | 76/76 | 3/3 | 2/2 |
| a8w8-d16-hp1 | full-m15-r0-n3-k16-15 | 126939 | 127394 | 455 | 126939 | 127394 | 455 | 0 | 1 | 1 | 31/31 | 15/15 | 1/1 |
| a8w8-d16-hp1 | full-m15-r0-n3-k16-16 | 127412 | 127869 | 457 | 127412 | 127869 | 457 | 0 | 1 | 1 | 31/31 | 15/15 | 1/1 |
| a8w8-d16-hp1 | full-m9-r0-n3-k5-08 | 127887 | 128233 | 346 | 127887 | 128233 | 346 | 0 | 1 | 1 | 25/25 | 9/9 | 1/1 |
| a8w8-d16-hp1 | pipeline-m3-r6-n3-k64-01 | 128251 | 128690 | 439 | 128251 | 128690 | 439 | 0 | 1 | 1 | 76/76 | 3/3 | 2/2 |
| a8w8-d16-hp1 | full-m15-r0-n3-k16-17 | 128724 | 129179 | 455 | 128724 | 129179 | 455 | 0 | 1 | 1 | 31/31 | 15/15 | 1/1 |
| a8w8-d16-hp1 | full-m15-r0-n3-k16-18 | 129197 | 129654 | 457 | 129197 | 129654 | 457 | 0 | 1 | 1 | 31/31 | 15/15 | 1/1 |
| a8w8-d16-hp1 | full-m9-r0-n3-k5-09 | 129672 | 130018 | 346 | 129672 | 130018 | 346 | 0 | 1 | 1 | 25/25 | 9/9 | 1/1 |
| a8w8-d16-hp1 | full-m9-r0-n3-k64-02 | 130036 | 130602 | 566 | 130036 | 130602 | 566 | 0 | 1 | 1 | 100/100 | 9/9 | 2/2 |
| a8w8-d16-hp1 | full-m15-r0-n3-k16-19 | 130636 | 131091 | 455 | 130636 | 131091 | 455 | 0 | 1 | 1 | 31/31 | 15/15 | 1/1 |
| a8w8-d16-hp1 | pipeline-m3-r0-n3-k64-02 | 131109 | 131550 | 441 | 131109 | 131550 | 441 | 0 | 1 | 1 | 76/76 | 3/3 | 2/2 |
| a8w8-d16-hp1 | full-m15-r0-n3-k16-20 | 131584 | 132039 | 455 | 131584 | 132039 | 455 | 0 | 1 | 1 | 31/31 | 15/15 | 1/1 |
| a8w8-d32-hp1 | full-m2-r0-n3-k64-01 | 589 | 1092 | 503 | 589 | 1092 | 503 | 0 | 1 | 1 | 68/68 | 2/2 | 2/2 |
| a8w8-d32-hp1 | full-m2-r0-n3-k64-02 | 1158 | 1888 | 730 | 1158 | 1888 | 730 | 0 | 1 | 1 | 68/68 | 2/2 | 2/2 |
| a8w8-d32-hp1 | full-m129-r0-n129-k96-01 | 1954 | 22851 | 20897 | 1954 | 22851 | 20897 | 0 | 1 | 6 | 2214/2214 | 645/645 | 45/45 |
| a8w8-d32-hp1 | pipeline-m64-r0-n129-k96-01 | 22949 | 31779 | 8830 | 22949 | 31779 | 8830 | 0 | 1 | 2 | 864/864 | 320/320 | 15/15 |
| a8w8-d32-hp1 | pipeline-m64-r64-n129-k96-01 | 31876 | 40704 | 8828 | 31876 | 40704 | 8828 | 0 | 1 | 2 | 864/864 | 320/320 | 15/15 |
| a8w8-d32-hp1 | pipeline-m1-r128-n129-k96-01 | 40801 | 43846 | 3045 | 40801 | 43846 | 3045 | 0 | 1 | 2 | 486/486 | 5/5 | 15/15 |
| a8w8-d32-hp1 | full-m1-r0-n1-k8256-01 | 43944 | 96671 | 52727 | 43944 | 96671 | 52727 | 0 | 1 | 5 | 8514/8514 | 1/1 | 258/258 |
| a8w8-d32-hp1 | full-m45-r0-n3-k32-01 | 96737 | 97820 | 1083 | 96737 | 97820 | 1083 | 0 | 1 | 1 | 77/77 | 45/45 | 1/1 |
| a8w8-d32-hp1 | full-m27-r0-n3-k5-01 | 97854 | 98646 | 792 | 97854 | 98646 | 792 | 0 | 1 | 1 | 59/59 | 27/27 | 1/1 |
| a8w8-d32-hp1 | full-m15-r0-n3-k32-01 | 98680 | 99252 | 572 | 98680 | 99252 | 572 | 0 | 1 | 1 | 47/47 | 15/15 | 1/1 |
| a8w8-d32-hp1 | full-m9-r0-n3-k5-01 | 99286 | 99746 | 460 | 99286 | 99746 | 460 | 0 | 1 | 1 | 41/41 | 9/9 | 1/1 |
| a8w8-d32-hp1 | full-m15-r0-n3-k32-02 | 99780 | 100352 | 572 | 99780 | 100352 | 572 | 0 | 1 | 1 | 47/47 | 15/15 | 1/1 |
| a8w8-d32-hp1 | full-m9-r0-n3-k5-02 | 100386 | 100846 | 460 | 100386 | 100846 | 460 | 0 | 1 | 1 | 41/41 | 9/9 | 1/1 |
| a8w8-d32-hp1 | full-m15-r0-n3-k32-03 | 100880 | 101452 | 572 | 100880 | 101452 | 572 | 0 | 1 | 1 | 47/47 | 15/15 | 1/1 |
| a8w8-d32-hp1 | full-m9-r0-n3-k5-03 | 101486 | 101946 | 460 | 101486 | 101946 | 460 | 0 | 1 | 1 | 41/41 | 9/9 | 1/1 |
| a8w8-d32-hp1 | full-m45-r0-n3-k32-02 | 101980 | 103062 | 1082 | 101980 | 103062 | 1082 | 0 | 1 | 1 | 77/77 | 45/45 | 1/1 |
| a8w8-d32-hp1 | full-m45-r0-n3-k32-03 | 103096 | 104180 | 1084 | 103096 | 104180 | 1084 | 0 | 1 | 1 | 77/77 | 45/45 | 1/1 |
| a8w8-d32-hp1 | full-m45-r0-n3-k32-04 | 104214 | 105297 | 1083 | 104214 | 105297 | 1083 | 0 | 1 | 1 | 77/77 | 45/45 | 1/1 |
| a8w8-d32-hp1 | full-m45-r0-n3-k32-05 | 105331 | 106415 | 1084 | 105331 | 106415 | 1084 | 0 | 1 | 1 | 77/77 | 45/45 | 1/1 |
| a8w8-d32-hp1 | full-m1-r0-n1-k32-01 | 106449 | 106765 | 316 | 106449 | 106765 | 316 | 0 | 1 | 1 | 33/33 | 1/1 | 1/1 |
| a8w8-d32-hp1 | full-m1-r0-n1-k32-02 | 106799 | 107115 | 316 | 106799 | 107115 | 316 | 0 | 1 | 1 | 33/33 | 1/1 | 1/1 |
| a8w8-d32-hp1 | full-m9-r0-n3-k64-01 | 107149 | 107794 | 645 | 107149 | 107794 | 645 | 0 | 1 | 1 | 82/82 | 9/9 | 2/2 |
| a8w8-d32-hp1 | full-m15-r0-n3-k32-04 | 107860 | 108432 | 572 | 107860 | 108432 | 572 | 0 | 1 | 1 | 47/47 | 15/15 | 1/1 |
| a8w8-d32-hp1 | full-m9-r0-n3-k5-04 | 108466 | 108926 | 460 | 108466 | 108926 | 460 | 0 | 1 | 1 | 41/41 | 9/9 | 1/1 |
| a8w8-d32-hp1 | full-m15-r0-n3-k32-05 | 108960 | 109532 | 572 | 108960 | 109532 | 572 | 0 | 1 | 1 | 47/47 | 15/15 | 1/1 |
| a8w8-d32-hp1 | full-m9-r0-n3-k5-05 | 109566 | 110026 | 460 | 109566 | 110026 | 460 | 0 | 1 | 1 | 41/41 | 9/9 | 1/1 |
| a8w8-d32-hp1 | full-m15-r0-n3-k32-06 | 110060 | 110632 | 572 | 110060 | 110632 | 572 | 0 | 1 | 1 | 47/47 | 15/15 | 1/1 |
| a8w8-d32-hp1 | full-m9-r0-n3-k5-06 | 110666 | 111126 | 460 | 110666 | 111126 | 460 | 0 | 1 | 1 | 41/41 | 9/9 | 1/1 |
| a8w8-d32-hp1 | pipeline-m3-r0-n3-k64-01 | 111160 | 111688 | 528 | 111160 | 111688 | 528 | 0 | 1 | 1 | 70/70 | 3/3 | 2/2 |
| a8w8-d32-hp1 | full-m15-r0-n3-k32-07 | 111754 | 112327 | 573 | 111754 | 112327 | 573 | 0 | 1 | 1 | 47/47 | 15/15 | 1/1 |
| a8w8-d32-hp1 | full-m9-r0-n3-k5-07 | 112361 | 112821 | 460 | 112361 | 112821 | 460 | 0 | 1 | 1 | 41/41 | 9/9 | 1/1 |
| a8w8-d32-hp1 | pipeline-m3-r3-n3-k64-01 | 112854 | 113383 | 529 | 112854 | 113383 | 529 | 0 | 1 | 1 | 70/70 | 3/3 | 2/2 |
| a8w8-d32-hp1 | full-m15-r0-n3-k32-08 | 113449 | 114022 | 573 | 113449 | 114022 | 573 | 0 | 1 | 1 | 47/47 | 15/15 | 1/1 |
| a8w8-d32-hp1 | full-m9-r0-n3-k5-08 | 114056 | 114516 | 460 | 114056 | 114516 | 460 | 0 | 1 | 1 | 41/41 | 9/9 | 1/1 |
| a8w8-d32-hp1 | pipeline-m3-r6-n3-k64-01 | 114550 | 115078 | 528 | 114550 | 115078 | 528 | 0 | 1 | 1 | 70/70 | 3/3 | 2/2 |
| a8w8-d32-hp1 | full-m15-r0-n3-k32-09 | 115144 | 115717 | 573 | 115144 | 115717 | 573 | 0 | 1 | 1 | 47/47 | 15/15 | 1/1 |
| a8w8-d32-hp1 | full-m9-r0-n3-k5-09 | 115751 | 116211 | 460 | 115751 | 116211 | 460 | 0 | 1 | 1 | 41/41 | 9/9 | 1/1 |
| a8w8-d32-hp1 | full-m9-r0-n3-k64-02 | 116245 | 116889 | 644 | 116245 | 116889 | 644 | 0 | 1 | 1 | 82/82 | 9/9 | 2/2 |
| a8w8-d32-hp1 | full-m15-r0-n3-k32-10 | 116955 | 117527 | 572 | 116955 | 117527 | 572 | 0 | 1 | 1 | 47/47 | 15/15 | 1/1 |
| a8w8-d32-hp1 | pipeline-m3-r0-n3-k64-02 | 117561 | 118088 | 527 | 117561 | 118088 | 527 | 0 | 1 | 1 | 70/70 | 3/3 | 2/2 |
| a8w8-d32-hp1 | full-m15-r0-n3-k32-11 | 118154 | 118727 | 573 | 118154 | 118727 | 573 | 0 | 1 | 1 | 47/47 | 15/15 | 1/1 |
| a8w8-d64-hp1 | full-m2-r0-n3-k64-01 | 929 | 2035 | 1106 | 929 | 2035 | 1106 | 0 | 1 | 2 | 132/132 | 2/2 | 2/2 |
| a8w8-d64-hp1 | full-m2-r0-n3-k64-02 | 2101 | 3469 | 1368 | 2101 | 3469 | 1368 | 0 | 1 | 2 | 132/132 | 2/2 | 2/2 |
| a8w8-d64-hp1 | full-m129-r0-n129-k96-01 | 3535 | 24729 | 21194 | 3535 | 24729 | 21194 | 0 | 1 | 18 | 2502/2502 | 387/387 | 27/27 |
| a8w8-d64-hp1 | pipeline-m64-r0-n129-k96-01 | 24795 | 33152 | 8357 | 24795 | 33152 | 8357 | 0 | 1 | 6 | 960/960 | 192/192 | 9/9 |
| a8w8-d64-hp1 | pipeline-m64-r64-n129-k96-01 | 33217 | 41577 | 8360 | 33217 | 41577 | 8360 | 0 | 1 | 6 | 960/960 | 192/192 | 9/9 |
| a8w8-d64-hp1 | pipeline-m1-r128-n129-k96-01 | 41642 | 45989 | 4347 | 41642 | 45989 | 4347 | 0 | 1 | 6 | 582/582 | 3/3 | 9/9 |
| a8w8-d64-hp1 | full-m1-r0-n1-k8256-01 | 46055 | 193064 | 147009 | 46055 | 193064 | 147009 | 0 | 1 | 258 | 16770/16770 | 1/1 | 258/258 |
| a8w8-d64-hp1 | full-m45-r0-n3-k32-01 | 193130 | 194458 | 1328 | 193130 | 194458 | 1328 | 0 | 1 | 1 | 109/109 | 45/45 | 1/1 |
| a8w8-d64-hp1 | full-m27-r0-n3-k5-01 | 194524 | 195520 | 996 | 194524 | 195520 | 996 | 0 | 1 | 1 | 91/91 | 27/27 | 1/1 |
| a8w8-d64-hp1 | full-m15-r0-n3-k32-01 | 195586 | 196360 | 774 | 195586 | 196360 | 774 | 0 | 1 | 1 | 79/79 | 15/15 | 1/1 |
| a8w8-d64-hp1 | full-m9-r0-n3-k5-01 | 196426 | 197087 | 661 | 196426 | 197087 | 661 | 0 | 1 | 1 | 73/73 | 9/9 | 1/1 |
| a8w8-d64-hp1 | full-m15-r0-n3-k32-02 | 197153 | 197930 | 777 | 197153 | 197930 | 777 | 0 | 1 | 1 | 79/79 | 15/15 | 1/1 |
| a8w8-d64-hp1 | full-m9-r0-n3-k5-02 | 197996 | 198657 | 661 | 197996 | 198657 | 661 | 0 | 1 | 1 | 73/73 | 9/9 | 1/1 |
| a8w8-d64-hp1 | full-m15-r0-n3-k32-03 | 198723 | 199500 | 777 | 198723 | 199500 | 777 | 0 | 1 | 1 | 79/79 | 15/15 | 1/1 |
| a8w8-d64-hp1 | full-m9-r0-n3-k5-03 | 199566 | 200227 | 661 | 199566 | 200227 | 661 | 0 | 1 | 1 | 73/73 | 9/9 | 1/1 |
| a8w8-d64-hp1 | full-m45-r0-n3-k32-02 | 200293 | 201623 | 1330 | 200293 | 201623 | 1330 | 0 | 1 | 1 | 109/109 | 45/45 | 1/1 |
| a8w8-d64-hp1 | full-m45-r0-n3-k32-03 | 201689 | 203018 | 1329 | 201689 | 203018 | 1329 | 0 | 1 | 1 | 109/109 | 45/45 | 1/1 |
| a8w8-d64-hp1 | full-m45-r0-n3-k32-04 | 203084 | 204413 | 1329 | 203084 | 204413 | 1329 | 0 | 1 | 1 | 109/109 | 45/45 | 1/1 |
| a8w8-d64-hp1 | full-m45-r0-n3-k32-05 | 204479 | 205808 | 1329 | 204479 | 205808 | 1329 | 0 | 1 | 1 | 109/109 | 45/45 | 1/1 |
| a8w8-d64-hp1 | full-m1-r0-n1-k32-01 | 205874 | 206394 | 520 | 205874 | 206394 | 520 | 0 | 1 | 1 | 65/65 | 1/1 | 1/1 |
| a8w8-d64-hp1 | full-m1-r0-n1-k32-02 | 206460 | 206979 | 519 | 206460 | 206979 | 519 | 0 | 1 | 1 | 65/65 | 1/1 | 1/1 |
| a8w8-d64-hp1 | full-m9-r0-n3-k64-01 | 207045 | 208297 | 1252 | 207045 | 208297 | 1252 | 0 | 1 | 2 | 146/146 | 9/9 | 2/2 |
| a8w8-d64-hp1 | full-m15-r0-n3-k32-04 | 208363 | 209140 | 777 | 208363 | 209140 | 777 | 0 | 1 | 1 | 79/79 | 15/15 | 1/1 |
| a8w8-d64-hp1 | full-m9-r0-n3-k5-04 | 209206 | 209867 | 661 | 209206 | 209867 | 661 | 0 | 1 | 1 | 73/73 | 9/9 | 1/1 |
| a8w8-d64-hp1 | full-m15-r0-n3-k32-05 | 209933 | 210710 | 777 | 209933 | 210710 | 777 | 0 | 1 | 1 | 79/79 | 15/15 | 1/1 |
| a8w8-d64-hp1 | full-m9-r0-n3-k5-05 | 210776 | 211437 | 661 | 210776 | 211437 | 661 | 0 | 1 | 1 | 73/73 | 9/9 | 1/1 |
| a8w8-d64-hp1 | full-m15-r0-n3-k32-06 | 211503 | 212280 | 777 | 211503 | 212280 | 777 | 0 | 1 | 1 | 79/79 | 15/15 | 1/1 |
| a8w8-d64-hp1 | full-m9-r0-n3-k5-06 | 212346 | 213007 | 661 | 212346 | 213007 | 661 | 0 | 1 | 1 | 73/73 | 9/9 | 1/1 |
| a8w8-d64-hp1 | pipeline-m3-r0-n3-k64-01 | 213073 | 214206 | 1133 | 213073 | 214206 | 1133 | 0 | 1 | 2 | 134/134 | 3/3 | 2/2 |
| a8w8-d64-hp1 | full-m15-r0-n3-k32-07 | 214272 | 215048 | 776 | 214272 | 215048 | 776 | 0 | 1 | 1 | 79/79 | 15/15 | 1/1 |
| a8w8-d64-hp1 | full-m9-r0-n3-k5-07 | 215114 | 215777 | 663 | 215114 | 215777 | 663 | 0 | 1 | 1 | 73/73 | 9/9 | 1/1 |
| a8w8-d64-hp1 | pipeline-m3-r3-n3-k64-01 | 215842 | 216973 | 1131 | 215842 | 216973 | 1131 | 0 | 1 | 2 | 134/134 | 3/3 | 2/2 |
| a8w8-d64-hp1 | full-m15-r0-n3-k32-08 | 217039 | 217815 | 776 | 217039 | 217815 | 776 | 0 | 1 | 1 | 79/79 | 15/15 | 1/1 |
| a8w8-d64-hp1 | full-m9-r0-n3-k5-08 | 217881 | 218542 | 661 | 217881 | 218542 | 661 | 0 | 1 | 1 | 73/73 | 9/9 | 1/1 |
| a8w8-d64-hp1 | pipeline-m3-r6-n3-k64-01 | 218608 | 219741 | 1133 | 218608 | 219741 | 1133 | 0 | 1 | 2 | 134/134 | 3/3 | 2/2 |
| a8w8-d64-hp1 | full-m15-r0-n3-k32-09 | 219807 | 220583 | 776 | 219807 | 220583 | 776 | 0 | 1 | 1 | 79/79 | 15/15 | 1/1 |
| a8w8-d64-hp1 | full-m9-r0-n3-k5-09 | 220649 | 221312 | 663 | 220649 | 221312 | 663 | 0 | 1 | 1 | 73/73 | 9/9 | 1/1 |
| a8w8-d64-hp1 | full-m9-r0-n3-k64-02 | 221378 | 222632 | 1254 | 221378 | 222632 | 1254 | 0 | 1 | 2 | 146/146 | 9/9 | 2/2 |
| a8w8-d64-hp1 | full-m15-r0-n3-k32-10 | 222698 | 223475 | 777 | 222698 | 223475 | 777 | 0 | 1 | 1 | 79/79 | 15/15 | 1/1 |
| a8w8-d64-hp1 | pipeline-m3-r0-n3-k64-02 | 223541 | 224671 | 1130 | 223541 | 224671 | 1130 | 0 | 1 | 2 | 134/134 | 3/3 | 2/2 |
| a8w8-d64-hp1 | full-m15-r0-n3-k32-11 | 224737 | 225513 | 776 | 224737 | 225513 | 776 | 0 | 1 | 1 | 79/79 | 15/15 | 1/1 |

## J. Remaining issues / intentionally out of scope

- `frontend-real`: **FAIL**, classified `BASELINE_EXISTING_FAIL`; no fix was attempted in this closure task.
- value-free cycle simulator: **NOT_IMPLEMENTED**.
- `op-trace` / `op-trace.jsonl`: **NOT_IMPLEMENTED**.
- synthesis: **NOT_RUN**.
- Fmax: **NOT_RUN**.
- FPGA resource estimation: **NOT_RUN**.
- TOPS calculation: **NOT_RUN**.
- physical FPGA access: **NOT_RUN**.
- real Chipyard/SoC integration: **NOT_IMPLEMENTED**.
- llama.cpp-gemmini: **not modified by this task**.

## Final gate

All requested refactor-closure gates are satisfied: integrated 6-profile verification passes, schedule and byte/request planning regressions are exact, vendor/static/Scala/legacy-smoke gates pass, all 268 RTL logical-cycle cases have zero delta, frontend-real is proven baseline-existing with unchanged signature, and this report plus fresh `final.json` evidence are produced.

**IM2P_SIM_REFACTOR_READY**
