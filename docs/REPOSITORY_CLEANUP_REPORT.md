# Repository cleanup report

## 1. Repository state and scope

Starting commit: `820e6d425cba73d219551027f5a06db6c102531c`.
Branch: `gemmini`. Repository: `/Users/zerogod/aisa-lab/DynDNN/IM2P/IM2P.sim`.
This cleanup is **unstaged and uncommitted**. HEAD still names the completed
refactor commit; the deletions and relocations described here are working-tree
changes, not a claim that a new commit was created. No reset, sibling-repository
edit, stage, commit, push, tag, physical-device operation or Vivado run occurred.

Pre-delete inventory: `docs/REPOSITORY_CLEANUP_INVENTORY.json`.
Its original immutable copy, initial Git state and input hashes are under
`/Users/zerogod/aisa-lab/build/im2p-gemmini/cleanup-20260916T161107Z`. The inventory covers all 638 starting tracked paths, with actual
tracked-file path/symbol references and build/test classifications before deletes.
Supplemental review records the small mesh-tag migration and stale test cache paths.
The user-owned `.vscode/` and `im2p-cache-build-scripts/` files remain byte-identical.

## 2. Retained repository responsibilities

A. End-to-end build, current API/C ABI, frontend/host orchestration and numerical
Verilated integrated RTL verification.
B. Value-independent work/fragment/request planning, current hardware/profile
contracts, and exact RTL cycle goldens for a future value-free consumer.
C. Existing generic Gemmini RTL generation, part/board-manifest abstraction and
synthesis flow, retained for future resource/Fmax/TOPS characterization.
D. Explicit LEGACY_BSV reference implementation with reproducible source/build,
unit/numerical checks and a lightweight simulator smoke.

The current overview and authorities are `README.md`, `docs/API_CONTRACT.md`,
`docs/NUMERICAL_CONTRACT.md`, `docs/GEMMINI_ARCHITECTURE.md`,
`docs/RTL_CYCLE_ACCOUNTING.md`, `docs/GEMMINI_CYCLE_SIM_BOUNDARY.md`,
`docs/HOST_STRIPE_CONTRACT.md`, `docs/VERIFICATION.md` and `docs/SYNTHESIS.md`.
The completed refactor report body and its 268-case table are preserved, with only
a phase-scope note so its old repository state is not mistaken for current state.

## 3. Deleted source, experiment assets and historical documents

| Removed tree | Starting tracked files | Reason |
|---|---:|---|
| `fpga/dense_pipeline/` | 69 | Board deployment/provider, replay or migration implementation and its experiment assets. |
| `fpga/full_replay/` | 56 | Board deployment/provider, replay or migration implementation and its experiment assets. |
| `fpga/scu_migration/` | 18 | Board deployment/provider, replay or migration implementation and its experiment assets. |
| `fpga/scu_runtime/` | 43 | Board deployment/provider, replay or migration implementation and its experiment assets. |
| `fpga/scu_block_scale/` | 39 | Board deployment/provider, replay or migration implementation and its experiment assets. |

All five trees are absent. Other deletions include `fpga/main-parity.json`, the
fixed-Arty `scripts/fpga_build.sh` / `synth/flow/ooc.tcl` flow and its shell test,
`FullReplay.bsv`, `DensePipeline.bsv`, `ScuPipeline.bsv`, `WindowBuffer.bsv`, and
the superseded Gemmini standalone implementation/driver described below.

Twenty-three historical top-level documents were deleted after contract review:
board-measurement/preflight/replay and phase reports, Jetson/Nano/IFR deployment
instructions, old physical runtime integration and SCU experiment reports.
Thirty-three SCU snapshot/provider/replay test and data files were removed,
including IFR experiment output vectors and a historical activation-guard
provenance record. Their callers depended on removed frozen physical-provider
implementations, not the retained integrated numerical or current-source guard path.

The initial inventory identifies 293 DELETE candidates and eight MOVE candidates.
There are 299 absent original file paths: the deletions plus six relocated originals;
the old cycle-accounting and verification bodies were moved to legacy documentation
while their current paths were rebuilt as concise current guides. This accounting
does not count new current documents as deleted source or inflate validation totals.
No archive/old/deprecated/historical directory was created. Git history is the
archive of discarded experiment assets; this report does not reproduce them.

Actual source inputs remain: profile/memory JSON, generic board schema/template,
upstream lock/vendor manifest, and independent scalar numerical golden vectors.
They are not classified as measurement evidence merely because they are JSON.
New build, binary, export and validation outputs are external to the repository.

## 4. Relocated LEGACY_BSV documentation

| Original | Retained location |
|---|---|
| `ALGORITHM.md` | `docs/legacy/ALGORITHM.md` |
| `docs/ARCHITECTURE.md` | `docs/legacy/ARCHITECTURE.md` |
| `docs/CODE_ANALYSIS_GUIDE.md` | `docs/legacy/CODE_ANALYSIS_GUIDE.md` |
| `docs/NUMERICAL_PROFILE.md` | `docs/legacy/NUMERICAL_PROFILE.md` |
| `docs/RTL_CYCLE_ACCOUNTING.md` | `docs/legacy/RTL_CYCLE_ACCOUNTING.md` |
| `docs/VERIFICATION.md` | `docs/legacy/VERIFICATION.md` |

The BSV-specific portion of the original README is retained in
`docs/legacy/README.md`. Technical reference content was not rewritten as Gemmini
architecture. Scope banners and corrected links distinguish old vector operations,
current ABI5 SCU arithmetic and the integrated Gemmini controller.
`docs/legacy/CONTRACT_NOTES.md` holds only the compact still-valid invariants below.

## 5. Historical SCU / assertion contract disposition

| Reviewed historical material | Normative disposition |
|---|---|
| `SCU_BLOCK_SCALE_CONTRACT.md`, `SCU_BLOCK_SCALE_FIX.md`, `SCU_FULLK_COMMON.md` | Migrate ABI5 unsigned carriers, admitted HP1 shifts/zero sentinel, separate legacy H1 domain, canonical element versus bridge byte units, per-fragment saturation before widened saturating accumulate, final-only output, ownership and fail-closed reconstruction into `docs/NUMERICAL_CONTRACT.md` and `docs/API_CONTRACT.md`. |
| `SCU_BLOCK_SCALE_BOARD_CORRECTNESS.md` | No unique current board measurement is normative. Current carrier/saturation/final-output rules already live in retained source/tests and the consolidated numerical contract; delete the physical correctness campaign report. |
| `PE_LOCAL_PARTIAL_INT20.md` | Retain only exact local partial widths, sign extension and the distinction from architectural/provider/RMD width in `docs/legacy/CONTRACT_NOTES.md`; no historical area/timing figures. |
| `ACTIVATION_PUBLICATION_ASSERTION.md` | Retain the strict internal pending-response publication admission rule, payload/tag lifetime, distinction from external stripe publication, and assertion-build timing caveat in legacy contract notes; keep current-source guard helpers/tests. |
| Remaining final-candidate, Nano and deployment reports | Delete historical evidence and superseded route/deployment claims; do not migrate old blanket residual-unimplemented or wrap-only claims into current HP1 semantics. |

Current integrated SCU/writeback/scale/fragment tests, BSV typed-SCU tests,
scalar golden inputs, canonical API declarations and frontend implementation remain.
No retained numerical behavior, tolerance, expected cycle value or numerical golden was changed.

## 6. Standalone diagnostic decision

**Delete the alternate production `StandaloneTop` and standalone-only elaboration
and export/build selection.** The integrated upstream-WS path is authoritative.
Its six-profile numerical fixture, FullK/K32 split, overlap, zero-replace/accumulate,
scale ownership, late-ROB/writeback and final-store tests cover the alternate top's
required numerical behavior. The redundant standalone full-K integration test is
removed, not counted as a retained passing test.

Useful unique lower-level coverage was migrated, not discarded:
`StandaloneLocalMemory` wiring becomes test-only `ScratchpadBankHarness`, and its
two existing tests become `ScratchpadBankHarnessSpec`. They preserve packed A4
rows, current/next A/W isolation, selected-bank conflict exclusion and ordered
responses held under backpressure. The one-bit mesh tag becomes `MeshTestTag`
inside the existing `UpstreamMeshSpec` test. No alternate GEMM controller remains.

`diagnostics / Compile / unmanagedSources` is empty. The build still aggregates
primitive diagnostics. Unused standalone/local-result bundles are removed from
BackingMemoryPort; the retained backing-port declarations and logic are exact.
Mechanical equivalence checks confirm unchanged bank wiring, bank assertions and
mesh tag semantics after symbol renaming. The current Scala run passes **48/48**
(23 diagnostic plus 25 selected-profile control tests), versus the prior 49 that
included the one now-removed redundant standalone test.

## 7. Export and helper dependency decisions

`gemmini_export.py` is retained as the supported source-only/integrated export.
`gemmini_evidence.py` retains its existing current runtime/RMD evidence validators,
layout differential and no-simulator artifact checks used by export/current tests.
The hardcoded historical campaign digest, campaign CLI/main, approval-era collectors
and campaign packaging/status routines are removed. Export imports were exercised,
not guessed from names.

`gemmini_audit_host.py` and `fpga/gemmini_hp1/host/` remain current API/golden
infrastructure. `uart.*` implements a packet codec, not device opening, serial
configuration or board deployment. Compatibility strings `PHYSICAL_HOST` and
FPGA-UART external-executor selectors remain where required by the existing host
link/audit contract; they do not perform or authorize a physical operation.

Retired standalone exports are rejected. Source-only and integrated relocation,
closure, forbidden binary/symlink, tamper and current runtime-evidence tests remain.
The actual fresh six-profile integrated export and relocation verification passed;
its package and logs are external, not repository-resident artifacts.

## 8. Removed stale build and tooling entry points

The old `fpga-rtl`, `fpga-area`, `fpga-timing`, `fpga-route`, `fpga-flow-test`
recipes/declarations and obsolete physical synth-provider entries are removed.
The power-of-two-stride provider-only branch is removed; the existing generic
BSV multiplication remains and its arbitrary-stride test passes. Current
`LEGACY_BSV`/`GEMMINI_HP1` selection, profile RTL, generic FIFO/scheduler diagnostics,
integrated tests, export, and Gemmini synth/route/bitstream entry points remain.

The removed standalone selector and standalone-export acceptance are gone;
negative tests intentionally retain old names to prove rejection. Current
source/static expectations and relocated documentation links are updated.
The separate build-contract test had pre-existing cache assertions missing the
already-established LEGACY_BSV namespace. Baseline and current failures matched
before correction; only expected test paths were corrected, not the production
cache layout or build behavior. The corrected contract test passes.

## 9. Validation results

| Check | Current result | Scope / evidence |
|---|---|---|
| `git diff --check` and new-file hygiene | PASS | No index changes; separate new-file whitespace/binary checks. |
| Vendor verification | PASS | Recorded ordered patches and immutable upstream provenance. |
| Python syntax / pyright | PASS | Modified scripts compile; 0 errors, 0 warnings. |
| Focused Python/static/export/flow/guard suite | 78/78 PASS | `python-final-canonical.log`; canonical `/private/tmp` fixture root. |
| `make check` and C/C++ ABI layout | PASS | `make-check-final.log`; profile config, static, numerical reference, reconstruction and guard infrastructure. |
| Planner regression | PASS | Six exact work/loop/digest profiles, six request/byte extent profiles and both signed packing paths. |
| Scala retained suite | 48/48 PASS | 23 primitive/test-only diagnostic plus 25 control tests; see standalone accounting above. |
| Integrated Gemmini matrix | 6/6 PASS | 54/54 stage commands: elaboration, lint, host/API CTest/audit, numerical RTL build/run. |
| Exact RTL cycles | 268/268 PASS | `max_abs_delta_cycles = 0`; endpoints, request/response and work/loop counts exact; complete logs byte-identical. |
| LEGACY_BSV reference smoke | PASS | Fresh BSC/Verilator/Cargo build, one selected `raw_clock_periods_are_exact` test; not a full nine-profile suite. |
| BSV arbitrary-stride row offset | PASS | `mkTbHostRowOffset` actual Bluesim run. |
| Export smoke / relocation | PASS | Fresh actual integrated export, verify and independent extract; unit tamper/negative cases also pass. |
| Generic synthesis support | PASS checks only | Four board/schema/Tcl validation-only tests; no Vivado invoked. |
| Frontend-real known failure | BASELINE_EXISTING_FAIL, NOT_RERUN | Adopt established six-profile unchanged stream-start failure; never counted as cleanup PASS. |
| Full physical / synthesis / Fmax / resource / TOPS | NOT_RUN | Intentionally excluded. |

Cycle comparison uses the preserved post-refactor current logs, whose provenance
and SHA-256 are recorded, against newly elaborated/built cleanup RTL driven by the
same non-invasive fixture probe. No wall-clock comparison or golden update occurs.
The full table and request counters are in external `cycle-regression.json` and
`cycle-regression-table.md`.

| Profile | Exact cases | Status | Maximum absolute delta | Runtime log |
|---|---:|---|---:|---|
| `a4w4-d16-hp1` | 52 | PASS | 0 | byte-identical |
| `a4w4-d32-hp1` | 41 | PASS | 0 | byte-identical |
| `a4w4-d64-hp1` | 41 | PASS | 0 | byte-identical |
| `a8w8-d16-hp1` | 52 | PASS | 0 | byte-identical |
| `a8w8-d32-hp1` | 41 | PASS | 0 | byte-identical |
| `a8w8-d64-hp1` | 41 | PASS | 0 | byte-identical |

Earlier unsuccessful validation attempts are preserved separately, not silently
counted as PASS: a missed test-only mesh-tag dependency was migrated; an export
test was retargeted to the existing per-profile filelist; a renamed test entry was
corrected; the wrong test filename in one invocation ran no tests. The original
build-contract path failure was proven baseline-existing before fixing test-only
expectations. Unchanged activation-guard infrastructure tests exposed macOS
`/var` versus `/private/var` aliases and an absolute `/build/` substring assumption;
running them from canonical `/private/tmp` resolves those fixture-environment
failures without weakening an assertion or changing production code. An extra
export verification initially targeted the extraction parent instead of its
package-root child; verification of the actual package root passed without any
file changes. Retained scalar case/expected files also reproduce byte-exactly;
checksum manifests have identical keys and values despite different JSON key order.
Those oracle-reproduction checks are not reported as DUT numerical executions.

## 10. Intentionally retained limitations and technical debt

The generic Gemmini frontend-real runner's `failed to start IM2P stream` failure
remains baseline-existing. A separate historical runtime fixture's outer-K oracle
failure is not relabeled as that frontend-real signature. Neither was fixed here.
The current-source activation guard and its optional sealed-snapshot reader are
retained reference verification, not a physical provider or a new archive tree.
Full dynamic guard campaigns and the complete legacy profile matrix were not rerun.

Compatibility filenames/audit role strings are retained where current host/API and
export depend on them. Existing upstream compile/elaboration warnings are not a
claim of warning-free Scala synthesis. Real Chipyard/SoC integration, a value-free
cycle estimator, op-trace replay, a CPU/NPU timeline model, new transport, Fmax
extraction, resource parsing and TOPS calculation remain unimplemented by cleanup.
Existing generic synthesis code was preserved, not executed or redesigned.

## Evidence and delivery state

External evidence: `/Users/zerogod/aisa-lab/build/im2p-gemmini/cleanup-20260916T161107Z`.
The final external summary is `final.json`; original refactor evidence is untouched.
The cleanup inventory is a path/dependency decision record, not a copy of removed
historical experiment content. The working tree intentionally remains dirty and
unstaged for user review. **Synthesis: NOT_RUN. Commit/stage/push: NOT_RUN.**
