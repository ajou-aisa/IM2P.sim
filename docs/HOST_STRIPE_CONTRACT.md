# Host stripe and internal execution contract

The A8/W8 DIM16 FPGA route preserves the public host stripe contract. A software
tile factor is not a submitted partial GEMM. The logical descriptor retains the
whole M/N/K; the existing IM2P schedulers choose output work and K fragments.

| Unit | Meaning and owner | Source |
| --- | --- | --- |
| `tile_I` | Software count of DIM blocks, not rows. | Host `ggml-gemmini-geometry.hpp`, `make_gemmini_geometry` |
| `H = stripe_rows` | `tile_I * DIM` actual rows. Final stripe has `min(H, I-row_begin)` rows. | Geometry above; `ggml-gemmini.cpp` sets `activation_rows_per_stripe` |
| Semantic A stripe | `A[row_begin:row_end, 0:K]`, with original byte stride. It need not fit one packet or device buffer. | Frontend `publish`, `im2p_activation_stripe_t` |
| `tile_i_rows`, `tile_j_columns` | Internal output work caps: `min(tile_factor * DIM, problem_extent, DIM)`. Zero raw factor means one DIM tile. | Frontend `normalize_tile_count`, full/stripe descriptor constructors |
| `K`, `tile_K` | Whole logical reduction length; software `tile_K` is copied metadata, not a K continuation command. | Frontend scalar snapshot and descriptors |
| ExSIA block | Existing `BLOCK_SIZE` local quantization/folding unit; profile uses 32. Stripe preparation spans the full logical K, with existing internal padding. | ExSIA `Quantization::run`, `state_.K_logical`, `state_.K_padded` |
| Internal K fragment | `min(DIM, remaining_K, remaining_scale_block)` when scaled. | `src/control/WorkScheduler.bsv`, `boundedFragmentCount` |
| Provider/refill | Supplies the existing consumer's semantic A/W/S address and extent. Refill does not create a descriptor, stripe, quantization block, or output. | `fpga/scu_block_scale/rtl_plugin.cpp`, `refill` |

The native UART provider currently selects K64/N64 resident windows. Its A
window holds 16 rows × 64 bytes (1,024 bytes), W holds 64 × 64 bytes (4,096 bytes),
and S holds two blocks × 64 columns × four bytes (512 bytes). These are the
64/256/32-word buffers in `ScuPipeline.bsv`, with 128-bit words. Legal alternate
window geometry is K32/K64 and N16/N32/N64. Logical N/K can exceed these windows;
each refill retains the current internal work and block context.

UART transfers whole 128-bit words in bodies of at most 4,096 bytes; packet
splitting does not change a fragment or quantization block. The core's activation
current/lookahead slots, stationary weight banks, accumulator, and pending output
have their own ownership. They are not ExSIA scratch slot ids. The bounded build
removes the legacy whole-matrix A/W/result retention with `IM2P_BOUNDED_ONLY`;
it does not allocate two H×K device buffers.

`FULL` observes post-fold events during preparation and calls frontend `execute`
once after all stripes are prepared. `STRIPE_PIPELINE` calls `execute` once, then
submits each actual `StripeReadyEvent` from `StripeReadySink`. Frontend `publish`
maps its row interval to an A pointer/stride, stripe id, and run context.
`MatmulScheduler::work` keeps the descriptor's reduction count while advancing
J work, then I work within the current stripe. `WorkScheduler` preserves the
route's accumulate/reset/end-block decisions. These roles also exist in main
`6fef5702a9b2b91e66ec7faa43d60fe11a1a042d`; the geometry is identical in compatible
host `7a6ed1e6d98f00e8bb5db4ca235b277260c3d684`.

The main-compatible H1/HP1 route selects `NumericalContract::main_external`.
RTL publishes each block's signed partial. The original frontend reconstructs
H1 with `s_rf * (c_b + R)` and HP1 with the native channel scale/exponent helper,
including the `INT16_MIN` sentinel. It adds block contributions in double and
commits the final float using the original output operation. This is distinct
from the opt-in SCU final-integer contract; neither replaces the other's raw
domain or expected values.

Main's matched H1/HP1 ExSIA routes support CPU-direct or accelerator compact
residual work. H0 requires CPU-direct residual; H2/HP2 ExSIA residual routes are
rejected. Producer scratch reuse, frontend event credit, accepted A backing,
physical refill slots, and residual ownership are separate lifetimes. In
particular, ExSIA releasing a scratch slot does not authorize overwriting the
accepted A bytes.

The shared RMD builder admits every INT32 residual. A8 uses up to five
signed-eight-bit digits with radix 256; A4 uses up to nine radix-16 digits, and
A16 uses up to three radix-65536 digits. The extra lane carries bit 32 through
the existing packing and execution path. Reconstruction outside INT32 still
rejects atomically. Scales, MAC widths, quantization and folding order are
unchanged.

This explicitly extends the historical main host's signed-21 admission policy;
it is not a claim that the immutable R0 quantizer accepted these wider values.
The original Llama layer-1 down input failed before transport at residual
-1,274,088. It decomposes exactly as 24 - 113*256 - 19*256*256, so signed-eight-bit
MAC operands can represent it without changing the hardware. Previously admitted
values retain the identical decomposition body and numerical contract. Current
A4/A8/A16 residual, packet, reconstruction, quantizer and ExSIA unit regressions
pass; actual model/RTL evidence is tracked separately from this admission change.

The same provider also retains main's non-ExSIA routes:

| Activation | Native weights in this A8/W8 DIM16 profile | Residual | Main raw domain |
| --- | --- | --- | --- |
| ExSIA | H1, HP1; Q8_0 through the original row-preparation helper | H1/HP1 CPU-direct or compact accelerator; Q8_0 CPU-direct | 1, block reconstruction |
| TENSOR, BLOCK, STRIPE | Q8_CHANNEL, H1, HP1, Q8_0 | OFF, as required by main | 0 for channel; 1 for block formats |
| TOKEN | Q8_CHANNEL with the original transposed-weight contract | OFF, as required by main | 0 |

Non-ExSIA uses the original activation quantizer once, retaining its A bytes and
metadata until completion. FULL prepares all input before one Run. PIPELINE
submits the original H-row intervals to one Run from immutable prequantized
backing; it is reported as `PREPARED_STRIPES`, not live ExSIA production.
Channel routes allow positive logical K tails; block formats retain K32
alignment. The adapter's explicit block-scaled extent query separates these
contracts without weakening resident buffer bounds. The original four-argument
query ABI remains conservative for block formats. H2/HP2 and the unrelated
I8-plus-scale ABI are not newly admitted by enumerator name alone.

`non-exsia-01` passes 30 ordinary graph cases, each twice, including channel
M35/N49/K193 FULL, M385/N49/K193 four-stripe pipeline, and M1/N17/K7 for all
four families. The three non-TOKEN families also pass H1/HP1/Q8_0 FULL and
pipeline at K192. Original F32 quantization is independently repeated into
fresh storage; native weights, A codes/scales, raw values and f_out match.
`channel-reference-01` replays sixteen identical captures through R0 and R1 in
both modes: each checks 329,280 raw and final values exactly against independent
scalar calculation and frozen R2, and rejects corrupted frozen output.
`current-exsia-01` then passes the shared Q8_0 regressions and nonzero HP1
accelerator residual with natural H128/five-stripe M513/N49/K128. These are
actual synthesis-core RTL checks with UART PHY omitted; SIM substitution and
physical access are zero.

## Capture and replay

`fpga/scu_runtime/tests/model_observer.cpp` reuses the ordinary backend's optional
observation registration. Its boundary observer records the real post-fold
event, copies its valid A bytes, and retains immutable activation metadata and
native weight backing. It verifies these bytes through checked output commit.
The callback has no arithmetic or execution role and is disabled by default.

Capture schema 2 adds actual run/stripe/slot identity, H/K/stride/valid bytes,
metadata, and `begin`/`post_fold`/`execute`/`submit`/`accepted`/`fence`/`fenced`/
`commit` order. FULL has no submit calls. Numerical files are provisional until
the successful commit writes the JSON manifest. Schema 3 additionally preserves
native compact accelerator residual packets and their immutable backing. An
independent radix-dot and merge oracle checks the captured residual contribution.
CPU-direct residual capture remains unsupported and is rejected explicitly.

`model_replay.cpp` validates schema 2/3, restores original stride and event
identity, and uses one original frontend Run. The immutable R0 and current R1
frontends use separate ABI builds. Changing replay mode is reported as
`MODE_REPLAY_ONLY`, not identical original API order. Legacy schema 1 captures
remain numerical replay inputs with `publication_identity=NOT_CAPTURED`; their
constructed event ids are never reported as captured publications.

The shared schema check is runnable without a simulator:

```sh
c++ -std=c++20 -Wall -Wextra -Werror -I ../llama.cpp-gemmini/common \
  fpga/scu_runtime/tests/model_capture_test.cpp -o /tmp/model_capture_test
/tmp/model_capture_test
```

It checks FULL and five-stripe PIPELINE, H16/K192, a final row tail, row padding,
full-width run identity, stale/duplicate identities, metadata/extent mutations,
missing calls/completion, and legacy classification. This is a schema/lifetime
regression, not proof of RTL arithmetic or model execution; those require the
separate actual provider and model qualification runs.

`compare_semantic_trace.py` additionally compares the same actual GPT-2 HP1
capture through R0/R1/R2 against independently enumerated work/fragment/block
geometry. The M165/H80/N2304/K768 three-stripe capture produces 1,584 work
items, 76,032 fragments and 570,240 successful provider output callbacks in
each route. Work, reset/end-block order and successful output completion match;
all six missing/duplicate/order/reset mutations are rejected. Physical cycles
and prefetch counts may differ. Evidence is `semantic-trace-05`; tracing is
disabled in the production build.

## Bounded provider area changes (2026-09-13)

`synth/WindowBuffer.bsv` computes the power-of-two row/word masks once at
configuration. Requests keep 32-bit global origins and use an 8-bit local
address. The two masked local terms occupy disjoint bits and their OR equals
the original address sum: configuration requires at most 256 words. Row limits,
refill generation, sequential loading, complete commit, and pending-response
ownership are unchanged. A standalone actual RTL regression is available:

```sh
bash fpga/scu_block_scale/test_window_buffer.sh /mnt/fpga-build/im2p/<fresh-run>
```

It exercises all 32 valid configurations, every local address, high global
origins, row tails, 64-bit tag wrap, and refill/response backpressure: 5,895
requests, 320 refills, seven invalid configuration/generation/index/commit/bounds
cases. The old and new RTL both pass the same independent global-word oracle.

`src/common/Arithmetic.bsv` uses a native multiply after sign extending both
operands to the existing product width. An n-bit signed integer times an m-bit
signed integer fits n+m bits; the low n+m two's-complement product is therefore
exact. This avoids `signedMul`'s generated absolute-value and sign-restoration
logic. Product width, PE-local INT20, architectural accumulator INT32/INT64,
pipeline registers, valid/ready, and fragment/block reset are unchanged.

Both INT8 forms pass all 65,536 pairs in actual RTL. Current A4 passes all 256
pairs, A8 all 65,536, and A16 1,458,752 boundary and deterministic random pairs
against independent native integer multiplication. Existing Arithmetic, PE,
array/engine weight banks, CoreMultiwidth, SCU typed metadata and saturation
Bluesim tests also pass; `TbArithmetic` retains explicit A4/A8/A16 extrema.

Measured `mkScuPipeline`, A8/W8 DIM16, `xc7a100tcsg324-1`, Vivado 2025.2,
OOC area, rebuilt hierarchy, Default directive, `opt-utilization.rpt`:

| Snapshot | LUT | FF | RAMB36 | DSP |
|---|---:|---:|---:|---:|
| Original bounded provider | 65,132 | 33,964 | 22 | 103 |
| Window masks only | 64,106 | 33,984 | 22 | 103 |
| Native multiply additionally | 60,478 | 33,994 | 22 | 87 |

Evidence is under `/mnt/fpga-build/im2p/stripe-contract-20260913T064058Z/`:
`area-provider-01`, `area-window-mask-01`, and `area-native-multiply-01`.
Each latter source comparison records exactly one implementation-file change.
These runs preserve the original provider source snapshot for isolation; later
logical-extent changes require an integrated fresh qualification. The final
core has 2,922 LUT headroom and zero blackboxes. This is not a UART4 board-top
fit, timing, routing, bitstream, physical-board, or wall-clock speed claim.
Historical INT20 savings in `PE_LOCAL_PARTIAL_INT20.md` are a separate result.

## Ordinary current-source build

The working `IM2P.sim/fpga/main-parity` and `llama.cpp-gemmini/fpga` trees own
the implementation. No historical source02 overlay is applied. In the sibling
checkout layout, the ordinary host script can provision its native dependency
from the selected current source after resolving the FPGA profile:

```sh
BUILD_DIR=/mnt/fpga-build/im2p/<fresh-run>/host BUILD_JOBS=2 \
  bash ../llama.cpp-gemmini/build-x86.sh \
  -DGGML_GEMMINI_EXECUTION_BACKEND=FPGA_UART \
  -DGGML_GEMMINI_ENABLE_RMD=ON \
  -DIM2P_SIM_ROOT="$PWD" \
  -DGEMMINI_SW_PATH="$PWD/../RISC-V-DynDNN-gemmini-include" \
  -DGGML_BACKEND_DL=ON -DBUILD_SHARED_LIBS=ON -DGGML_NATIVE=OFF
```

Run this from the IM2P.sim root with the existing BSC/Verilator/Cargo dependencies
available. Generated native files remain under `BUILD_DIR/im2p-native`. An
explicit immutable `GGML_GEMMINI_FPGA_SIM_MANIFEST` remains supported; it is not
required for automatic provisioning. The script's existing user defaults are
preserved. Native ARM64 uses `build-arm64.sh` on ARM64, not an x86 artifact; that
target execution is not yet qualified.

Install the built runtime subset with `cmake --install <build> --prefix <new>
--component FPGA_UART_RUNTIME`. Help, device listing, and supports queries do
not open a device. Execution requires both explicit GEMMINI graph placement and
an explicit `IM2P_FPGA_DEVICE`: `rtl:<absolute-plugin-path>` selects the fast
synthesis-core harness; `uart4:<absolute-PTY-path>` selects the actual UART shell
in simulation. No default device probing or numerical fallback is added.

IFR3 remains a separate sealed-fixture contract with its cycle whitelist.
Bounded IFR4/main_external checks logical layout, padded address span, resident
transfer bounds, tags, order, counts, and completion. It does not use that
fixture whitelist as a model shape limit. H1/HP1 retain native packed weights;
Q8_0 uses the host's existing preparation helper and CPU-direct ExSIA residual
policy. Nonzero H1/HP1 accelerator residual shares the single core. Runtime
and physical qualification remain separate; the current work opens only PTYs.

## UART RX word assembly

`scu_window_uart.sv` assembles 16 little-endian bytes before writing a full
128-bit `rx_words` entry. After bytes b0 through b14 the 120-bit prefix contains
b14 through b0; accepting b15 writes `{b15, prefix}`. Every complete word replaces
all prefix bytes before use, so the prefix needs no reset. The 48-byte header
keeps body words aligned. This replaces the dynamic byte-lane write that Vivado
mapped to 128 RAMB18s in the integrated UART4 board candidate.

The word buffer remains tentative until CHECK validates CRC, frame identity,
generation, sequential window index, and `length == word_count * 16`. Only that
path reaches LOAD_READ. A partial final word cannot become a core load; CRC,
sequence, generation, index, and partial-word errors all produce their exact
existing status and zero LOADs. Request lifetime, complete-window commit,
backpressure, output acknowledgement and watchdogs are unchanged.

Fresh provider-03 plus this shell passes actual UART-bit simulation at DIV25:
M3/N17/K64 with 4,096-byte versus 16-byte refill packets has identical block raw
values and output order. H1 and native HP1 M3/N49/K192 pass independent scalar
oracles under the explicit `signed-scu-sat-v2` contract. Separately, ordinary
native-library H1 FULL and HP1 PIPELINE M3/N17/K64 pass the `main_external`
contract, with 102 block raw values and 51 final floats each, and zero simulator
substitute calls. The new ELF SHA256 is
`44a6fb172e6171c867d09968b5201c88655257315083f52b36ef4691a328a01b`.
Evidence: `uart4-rx-assembly-01`, `uart4-rx-scaled-01`,
`uart4-rx-main-h1-01` and `uart4-rx-main-hp1-01` under the run directory above.
These are software/RTL results; physical access remains zero.

Each accepted IFR4 START now resets the output-batch identity to one, matching
the host's per-job counter. Previously it retained the dense job's final batch
counter, so the next compact residual job failed the host's strict batch check.
Run/generation/sequence checks remain intact; old-run ACKs are rejected. This
resets a new job's transport counter, never a running K reduction on refill.

The corrected shell's actual UART ELF is
`b70dc54297c2df2723544a69269bec53327c48e72aefaf37c71f44445abe7f5d`.
`uart4-batch-generation-02` passes the full quick regression and repeated
dense-to-BYPASS jobs on one device. `uart4-batch-residual-hp1-anchor-01` passes
the original forced nonzero HP1 PIPELINE fixture: 94 nonzero residual entries,
102 dense block raw values and 51 final floats exact, one dense and eight
compact jobs, nine actual RTL launches, 331 UART packets, zero simulator
substitute calls. Its 1,471.54-second UART-bit simulation completed within the
original 1,800-second limit; this is not a hardware performance measurement.

The integrated board's Default placement failed despite its 62,996 LUT count
being below the device limit: the remaining macros required 10,259 slices while
only 9,096 were available. `ExploreArea` removed only 34 LUTs and was not applied
to the production flow. `LogicCompaction` also failed placement.

The window-mode route now selects Vivado 2025.2 `AreaMultThresholdDSP`, allowing
eligible small multipliers to use available DSPs. The following comparison uses
identical current RTL, including the per-run UART batch reset; only `route.tcl`
differs between source inventories:

| UART4 synthesis setting | LUT | FF | RAMB36 | RAMB18 | DSP | Control sets |
|---|---:|---:|---:|---:|---:|---:|
| LogicCompaction | 62,955 | 37,065 | 24 | 0 | 87 | 618 |
| AreaMultThresholdDSP | 53,646 | 37,053 | 24 | 0 | 223 | 616 |

Evidence: `board-logiccompact-area-01` and `board-dsp-threshold-area-01` under
the run directory above. The 9,309 LUT reduction changes resource mapping only;
it does not change arithmetic, pipeline latency, precision, DIM, accumulator
width, or the supported numerical contracts. These are post-opt synthesis
measurements, not a completed route or physical qualification.

Fresh `board-route25-dsp-01` subsequently completed synthesis, optimization,
placement, routing and bitstream generation for `xc7a100tcsg324-1`. Routed use:
52,442 LUT, 37,053 FF, 24 RAMB36, zero RAMB18, 223 DSP, 616 control sets.
Occupied slices are 15,719/15,850 (99.17%); only 131 remain, despite LUT headroom.
At the constrained 25 MHz core clock, setup WNS is 10.910 ns, hold WHS 0.015 ns,
and minimum pulse slack 3.000 ns; all three total-negative slacks are zero.
All 87,070 routable nets are routed, with zero routing errors, black boxes or
unconstrained internal endpoints. Maximum nonclock fanout is 7,736, with
12.679 ns slack. No congestion window exceeds level five.

DRC has zero errors/critical warnings, but is not warning-free: DSP input,
output and multiplier pipeline recommendations remain (271/221/221). A separate
uncapped report finds 30 `REQP-1839` warnings: the asynchronously asserted
MMCM-lock reset reaches BRAM controls. Default timing does not analyze that
assertion. Reset clears ownership and window validity; new use requires complete
validated packets/refills. This source-level lifetime argument is not physical
reset qualification. No waiver, false path or multicycle exception was added.
The asynchronous UART input and UART/LED outputs have no external I/O delay
constraints; the CDC report's safe-path result does not qualify those external
interfaces. Physical access remains zero.

The fresh bitstream SHA256 is
`4c82bef5c55d229f1c711178e69b6fba355dd4bf824da5a5e9f6e027d90f63a6`;
the routed DCP is
`4544e3ee4f015044cb9ee54d2c83d8a36d77ac920935b666093276106154080c`.
The run's source inventory ties the numerical provider-03 and batch-corrected
UART shell to this build. Detailed report hashes and limitations are in
`.parity/stripe-contract-20260913T064058Z/board-route25-qualification.json`
under the project parent. This is a routed candidate, not a programmed board.
