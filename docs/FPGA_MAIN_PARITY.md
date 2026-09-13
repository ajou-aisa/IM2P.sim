# FPGA main parity — continuation 02

> Historical continuation snapshot. Subsequent working-branch implementation,
> stripe/provider semantics, numerical contracts and current build instructions
> are in [HOST_STRIPE_CONTRACT.md](HOST_STRIPE_CONTRACT.md). The earlier failures
> below remain evidence of that run; they are not the current implementation status.

## Status and authority

**Overall parity is NOT complete.** This continuation recovered an unmodified-main
software/frontend reference, identified toolchain mount blockers, and implemented
validation infrastructure in the real repositories. It did not execute fresh
numerical RTL, add the bounded board provider, enable board HP1/residual, run model
inference, or generate a new bitstream. None of those requirements is out of scope.

Implementation worktrees remain `IM2P.sim: fpga/main-parity` and
`llama.cpp-gemmini: fpga`. No staging, commit, push, main/develop ref change,
physical UART/JTAG/CAP, programming, reset, or Flash access was performed.

Evidence root, relative to the common workspace:

```text
.parity/20260911-main-parity-02/evidence/
```

Each completed command has a `.json` record with argv/cwd/selected environment,
exit status, timestamps and a SHA256 of its `.log`. A log without a final exit
record is interrupted/unknown, not PASS. Successful retries have separate names;
failed and interrupted original records are preserved.

## References and telemetry compatibility

| Role | SHA |
|---|---|
| Fixed IM2P main | `6fef5702a9b2b91e66ec7faa43d60fe11a1a042d` |
| Fixed current host/develop | `7a74ac29c9c8fa8fbc5d47cf073b8b9266e3e7a9` |
| Fixed include/develop | `cd2caed8d93e49d3f8873bd6c4e340fcad54b9e0` |
| Native-compatible historical host tested in continuation 02 | `7a6ed1e6d98f00e8bb5db4ca235b277260c3d684` |
| Implementation core HEAD, plus preserved dirty work | `3aeb5feee6872f88ec1f6a5dc0d77fb1bb8babf8` |

The first three refs were remotely verified in the preceding run; this continuation
reused them without moving the reference. The new historical host is an additional
reference, not a replacement for the current implementation host.

Host commit `4cf791ec8d65395bcec0365b5eafc91811e98cfa` removed the raw
`local_start_cycle/local_end_cycle` fields. The nanosecond fields already existed
alongside them. This was **not simply a cycle-to-nanosecond rename**. Its parent,
`7a6ed1e`, retains both sets and now passes the unchanged main frontend tests.
The current host also contains profiling/residual changes beyond those fields.
`telemetry-history.txt` preserves the actual Git history/diffs.

A new detached sibling layout was created at:

```text
.parity/20260911-main-parity-02/reference/
  IM2P.sim                         # main 6fef570, unchanged
  llama.cpp-gemmini                 # historical host 7a6ed1e, unchanged
  RISC-V-DynDNN-gemmini-include      # cd2caed, unchanged
```

This fixes the cache tests' sibling-path assumptions without patching the main
Makefile. No `R0_COMPAT_PATCH` or arithmetic/telemetry shim was needed.
Classification: **R0-native-compatible, software-contract scope**. It does not
prove that `7a6ed1e` was the original historical CI pin or that all native RTL/model
routes are compatible. Fresh RTL and actual model invocation comparisons remain.

## Actual validation in continuation 02

| Record label | Result | Scope |
|---|---|---|
| `r0-native-check` | PASS, exit 0 | Main profile/static/C++/numerical-reference/host-reconstruction checks |
| `r0-native-frontend` | PASS, exit 0 | Main frontend mock ABI and separate HP1 extent case |
| `r0-native-cache` | PASS, exit 0 | PIC/cache/matrix-cache/semantic identity contracts; fake build fixtures |
| `r0-native-real-syntax-retry` | PASS, exit 0 | A4/A16 real-fixture syntax compilation, not runtime |
| `r1-cache-retry` | PASS, exit 0 | Current cache contract suite |
| `r1-check` | PASS, exit 0 | Current check before Makefile added the new infrastructure prerequisite |
| `r1-final-check` | PASS, exit 0 | Final public make check including all 20 new infrastructure tests |
| `r1-frontend-retry` | PASS, exit 0 | Current frontend mock ABI; not actual simulator or board arithmetic |
| `m9-harness-infrastructure` | PASS, 20 unit tests | New source selection, tool discovery, preservation and warning parser |
| `metadata-cpu-configure`, `metadata-cpu-build` | PASS, exit 0 | Ordinary CMake metadata-tool target; no backend execution |
| `metadata-tool-unit` | PASS, 7 unit tests | Native GGUF read-only parser/shape/JSON/error/extent checks |
| `inventory-llama-native` | PASS, exit 0 | Actual Llama file metadata, not a model invocation |
| `r0-fresh-rtl-attempt`, `r1-fresh-rtl-attempt` | BLOCKED, make exit 2 | BSC command not visible; no generated RTL |
| `r1-fresh-m9-attempt` | BLOCKED, exit 2 | Explicit BSC visibility diagnostic before creating a result directory |
| `tool-bootstrap` | BLOCKED, exit 78 | Known BSC/Vivado installations not visible inside CatDesk |

The final machine-readable summary is built from completed command records, not
from banner counts or the number of declared tests. In particular, frontend
`numerical_rtl_evidence=0` remains zero. No current-run raw/f_out RTL exact count,
G0117 count/delta, runtime assertion count, fresh Fmax or bitstream is available.

The original `r0-native-real-syntax`, `r1-cache`, `r1-frontend` and combined
`model-inventory` jobs ended without a recorded final exit. Their earlier completed
subcommands retain their own evidence, but the combined jobs are not PASS. The
actual cause of the interruption was not established. Retries were started only
after the original jobs had reached a terminal state and cancellation was requested.

## Environment: not merely PATH and not proof of missing host installation

Explicit checks of the user-provided paths failed **inside the CatDesk command
filesystem**. `/proc/self/mountinfo` shows selected tool directories, notably only
`oss-cad-suite/bin`, but not the complete BSC/Vivado/OSS CAD installation roots.

| Resource | Observed command-environment state |
|---|---|
| `/home/youngshin/.local/opt/bsc/bin/bsc` | Not visible, ENOENT |
| `/tools/Xilinx/2025.2/Vivado/settings64.sh` and `bin/vivado` | Not visible; `/tools` itself not visible |
| `oss-cad-suite/bin/verilator` | Found, but `verilator -V` fails |
| `oss-cad-suite/share/verilator`, `oss-cad-suite/lib` | Not visible; inherited VERILATOR_ROOT is unusable |
| `/mnt/fpga-build` | Not visible |
| Workspace filesystem | About 11 GiB free at initial inspection, 98% used |
| `/usr/bin/rustc` | 1.75.0 |
| `$HOME/.cargo/bin/rustc` | Existing 1.91.1 executable works |
| GCC / CMake / Python | 11.4.0 / 3.22.1 / 3.10.12 |
| NumPy in the visible Python | Not available; no install performed |

`evidence/bootstrap.sh` only checks and activates the existing installations. It
fails closed rather than claiming a reinstall is necessary. The accompanying
`vivado-version-smoke.tcl` only queries version/identity and does not open hardware;
it has not run because the installation is inaccessible.

To unblock fresh RTL and implementation, the CatDesk configuration must expose the
existing BSC prefix and real symlink targets/primitives, the full Vivado runtime,
and OSS CAD runtime share/lib dependencies. A sufficiently large writable build
filesystem also needs to be available. No mount/security configuration was changed
or bypassed from this workspace. PATH changes alone cannot expose missing mounts.

## Source changes in the real implementation worktrees

### Current-source M9 harness

`tests/scu_block_scale/run_activation_guard.py` now accepts either historical
`--snapshot` or current `--source`. The current-source mode forbids
`--production-build`; it must compile both production and asserted variants fresh.
It uses the existing numerical DUT and independent BSV pack oracle, not a new CPU
oracle or a renamed old generated RTL artifact.

The new `activation_guard_support.py` implements complete source-input hashing,
addition/change/deletion detection, BSC primitive-directory discovery, explicit
tool realpaths and diagnostic inventory including G0117. Known source bytes,
runner/helper bytes, executable and primitive hashes are retained in a successful
preflight output. No warnings are suppressed. Source selection and Python `-O`
are guarded, failed tool discovery creates no partial success directory, and command
exceptions are recorded. The pre-existing hardcoded decoder is still checked by
the independent BSV oracle; no new offsets or packing claims were introduced.

The 20 infrastructure tests are available through
`make activation-guard-infrastructure-test` and are now a prerequisite of `make check`.
They never invoke their test executable markers as numerical compilers.
The old runner bytes are saved as `evidence/run_activation_guard.before.py`.

After tool access is restored, from the actual IM2P repository:

```bash
python3 tests/scu_block_scale/run_activation_guard.py \
  --source "$PWD" --out /absolute/fresh-writable-output/m9-current --jobs 2
```

This is an ABI5 SCU **raw-core M9/N1/K1** test, not an unmodified ABI4 main M9 test,
native HP1 proof, or a full UART/production-board-shell test. Its fresh numerical
execution is still blocked. The source-mode addition has infrastructure test evidence
only; it does not make the future BSC compile a known PASS.

### Read-only native model inventory

`llama.cpp-gemmini/tools/im2p-model-inventory/` is a normal root-CMake tool,
`llama-im2p-model-inventory`. It uses the repository's GGUF reader with `no_alloc`,
checks all tensor file extents, and emits staged JSON. No new format decoder,
quantization, backend initialization, or model evaluation was introduced. The built
executable's dynamic dependencies contain `libggml-base`, not `libggml-gemmini`.

Current stored metadata was read without converting either model:

| Model | Tensors | Types | Representative stored `ne[0],ne[1]` |
|---|---:|---|---|
| GPT-2 | 148 | HP1 49, F32 99 | attention QKV 768,2304; FFN up 768,3072; down 3072,768 |
| Llama3.2-1B | 147 | HP1 113, F32 34 | attention Q 2048,2048; FFN up/gate 2048,8192; down 8192,2048 |

`model-metadata-summary.json` and the native stdout records contain the complete
stored tensor information. These axes are **not captured runtime M/N/K invocations**;
M, prefill/decode activations and runtime dispatch counts were not obtained.
GPT-2 has a completed before/after SHA match in `inventory-gpt2.status.json`.
The combined audit was interrupted during the Llama phase; its whole audit is not
PASS. Llama metadata was successfully reread with a separate exit-0 command.

A separate existing build defect was exposed by configuring
`GGML_GEMMINI=OFF, LLAMA_BUILD_TESTS=ON`: `tests/CMakeLists.txt:865` calls a profile
helper only defined by the enabled backend. Its failure is preserved as
`metadata-configure.log` and remains unresolved. The metadata target was instead
built using the existing CPU+HARDWARE configuration with tests still ON. That is a
valid software-tool build, not a fix for the backend-disabled configuration and
not FPGA inference. No tests were removed or globally disabled.

## Numerical policy and remaining parity matrix

Main's original block/raw reconstruction and the historical SCU final-integer
saturation contract are not assumed equivalent. Equivalent semantic boundaries
and final f_out must be compared with an independent R0 result. Old board output
is never used as the new main golden. No arithmetic, quantizer, frontend routing,
RTL, provider, wire protocol, residual policy, or user model was changed in this
continuation.

| Requirement | R0 evidence now | R1 now | R2 / general CLI / physical implementation |
|---|---|---|---|
| A8/W8/D16 H1 FULL/PIPELINE | Frontend contracts pass; fresh RTL blocked | Mock contracts pass; fresh RTL blocked | Board main-equivalence still pending |
| HP1 and other main-supported families | Main software/extent contracts available | Current software/extent contracts pass | HP1 board numerical implementation/validation still missing |
| Nonzero accelerator residual | Lifecycle/ownership mock evidence only | Same limitation | Shared-core accelerator execution still missing |
| Logical M/N/K beyond resident capacity | Main descriptor contract, no new large RTL run | Not yet differentially executed | Bounded provider/refill/continuation still missing |
| GPT-2 / Llama original full-size invocations | Stored metadata only | No capture/replay | No full-size numerical or standard CLI/PPL proof |
| Other A4/A16, DIM32/64 profiles | Configuration and limited syntax evidence | No new profile RTL | Separate physical profile requirements retained |
| Fresh 25 MHz Artix-7 route | Not applicable | Not applicable | Toolchain inaccessible; no new routed candidate/bitstream |

The next implementation stage remains the prompt's fresh R0/R1/R2 numerical
baseline and M9 gate, then H1 bounded windows, HP1, accelerator residual, model
invocations, general CLI/PPL, resource probes, and 25 MHz implementation. Physical
approval is not yet requested: its numerical/model/route prerequisites are unmet.
