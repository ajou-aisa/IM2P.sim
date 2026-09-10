# Exact local partial width experiment

## Baseline

- Source branch: `fpga/arty-a7-100t`.
- Local and fetched remote SHA: `dc4a1a621f63834d64df42ae8c24152747d97971`.
- Initial working tree: clean, including untracked files.
- Experiment branch: `exp/pe-local-partial-int20`, created from the remote baseline.
- Target: A8/W8 DIM16, PE latency 1, `xc7a100tcsg324-1`, Vivado 2025.2, OOC area, rebuilt hierarchy.
- Original FPGA artifact: `build/fpga/a8-w8-d16-int32-bram-area-20260908-225141`.
- New FPGA artifact: `build/fpga/a8-w8-d16-partial20-area-20260909-015233`.

The original FPGA artifact records an older commit with a dirty snapshot. Its 21 source files, 14 synthesis files, 13 scripts, and profile JSON are byte-identical to the archived baseline commit. The comparison is saved in `build/experiments/partial20/baseline-fpga-source-comparison.txt`.

## Architectural proof

1. `InputSkew.step` creates each valid top partial with `accumulatorZero()`. No architectural accumulator value enters this boundary.
2. `SystolicArray.step` sends each partial down exactly one PE row per hop. `PE.step` adds one full signed product. By induction, row r contains at most r+1 products; DIM16 contains at most 16.
3. Matrix loading pads weight rows from KCount to DIM with zero. This also applies to preload and lookahead. Host activation responses zero lanes beyond KCount. The low-level row interface retains its existing caller-managed padding contract.
4. Engine results pass through VectorUnit, then `Accumulator.commit`. Cross-fragment addition occurs in the architectural BRAM RMW using `accumulatorAdd`. Only committed valid bits return to the engine.
5. Every engine start requires idle control and empty input/result FIFOs, then clears InputSkew and every PE pipeline. Fragment completion waits for engine completion and architectural commit. Scale-block changes use the same start path.

Signed INT8 products range from -16256 to 16384. At most 16 terms therefore lie in [-260096, 262144], including every intermediate partial. Signed INT19 ends at 262143 and fails for sixteen (-128)×(-128) products. Signed INT20 covers [-524288, 524287]. No rounding, saturation, quantization, or wrap-policy change is introduced. Removed upper bits are redundant sign bits, including ones for negative values.

## Code changes

- `scripts/im2p_config.py`, generated `src/common/Config.bsv`: define `IntegerPartialWidth(dim, productWidth) = productWidth + TLog(dim)`.
- Generated `sim/ffi/im2p_config.h`: configuration fingerprint refresh only. Public `IM2P_PARTIAL_BITS` remains the architectural engine-boundary width, 32 for A8; it does not describe the new local PE storage.
- `src/common/Arithmetic.bsv`: infer the local type with `SystolicPartial`; widen signed integers with `signExtend` and an `Add` proviso requiring accumulator width at least partial width. Homogeneous floating point uses identity conversion.
- `src/array/PE.bsv`, `InputSkew.bsv`, `SystolicArray.bsv`, `SystolicArrayTiled.bsv`: name the existing local type parameter `partial_t`; preserve all methods, control, and pipeline behavior.
- `src/array/SystolicEngine.bsv`: use local partials inside the array; widen before enqueuing the unchanged architectural result FIFO.
- `src/core/IM2PCore.bsv`: constructor types/provisos only. Public interface, runtime actions, VectorUnit, and accumulator remain unchanged.
- `src/array/SystolicArray{A4W4D64,Int8x64,A16W16D64}.bsv`: use the full DIM64 width in every DIM16 tile because partials cross tile boundaries: 14, 22, and 38 bits respectively.
- `tests/TbProfileConfig.bsv`: assert all nine local widths and existing architectural widths/capacities.
- `scripts/static_check.py`, `tests/test_static_check.py`: preserve the tiled-width check using the new full DIM64 type; reject accidentally using DIM16 width.
- `sim/tests/rtl_numerical_profile.rs`: one RTL test with 24 direct vectors and cycle telemetry, including totals outside signed INT20.

Generated A8/D16 RTL has identical public ports and 2246 register names. Only 256 PE partialPipe register widths change, from 33 to 21 bits including the Maybe valid bit. All 20 FIFO2/BRAM1 instances retain identical parameters and wiring. Engine results remain 608 bits. All 16 BRAM banks retain DATA_WIDTH=32, MEMSIZE=1024, PIPELINED=0. See `build/experiments/partial20/rtl-width-comparison.txt`.

## Numerical verification

- `git diff --check`: PASS.
- `make check`: PASS (architecture static checks, C++ reference, numerical reference, host reconstruction).
- `python3 tests/test_profile_config.py`: PASS, all nine profiles and generated C++/Rust contracts.
- `python3 tests/test_static_check.py`: PASS, including rejection of a DIM64 tile narrowed to DIM16 width.
- `make bsv-test-one TOP=mkTbProfileConfig`: PASS, all nine local widths.
- `make bsv-test-one TOP=mkTbFloatCore BUILD_DIR=build/experiments/partial20/compat`: PASS, existing homogeneous floating-point behavior.
- `make rtl-one TOP=mkSynthA4W4D64 BUILD_DIR=build/experiments/partial20/compat-d64`: PASS; repeated with `TOP=mkSynthA8W8D64` and `TOP=mkSynthA16W16D64`, also PASS. Specialized tile, D64 wrapper, and Core top elaborate with INT14/22/38 local partials.
- `rustfmt --check --edition 2021 sim/tests/rtl_numerical_profile.rs`: PASS.
- `make sim-test-a8-w8-d16`: PASS, 154 tests, 0 failures, 0 ignored, exit 0.
- Fresh archived baseline with the same added test: PASS, 154 tests, 0 failures, 0 ignored, exit 0. Original cached regression: 153 PASS.

| Vector | K | Exact result |
|---|---:|---:|
| -128 × -128 | 16 | 262144 |
| -128 × 127 | 16 | -260096 |
| 127 × 127 | 16 | 258064 |
| Both vectors alternate -128,127 | 16 | 260104 |
| -128 × -128 with zero padding | 7 | 114688 |
| -128 × -128, two fragments | 32 | 524288 |
| -128 × -128, three fragments | 33 | 540672 |
| -128 × 127, three fragments | 33 | -536448 |
| 16 deterministic full-range random vectors | 7/16/32/33 | All exact |

K32 and K33 positive totals and the K33 negative total exceed signed INT20. Their exact architectural results verify accumulation after widening. Existing scaled/wrap/output tests remain unchanged and pass.

## Cycle verification

All 24 records match between a fresh baseline build and INT20, including numerical result, total/work/compute/drain/preload cycles, fragment count, and work start/completion cycles.

| Transaction | Total before/after | Work before/after | Compute before/after |
|---|---:|---:|---:|
| K7 | 108 / 108 | 105 / 105 | 68 / 68 |
| K16 | 117 / 117 | 114 / 114 | 68 / 68 |
| K32 | 190 / 190 | 187 / 187 | 136 / 136 |
| K33 | 263 / 263 | 260 / 260 | 204 / 204 |

Evidence: `build/experiments/partial20/{baseline-fresh-sim-test.log,sim-int20-build.log,cycle-comparison.txt}`. This is RTL cycle telemetry, not host wall-clock timing.

## FPGA resource comparison

| Resource | Baseline | INT20 | Delta |
|---|---:|---:|---:|
| Slice LUT | 62476 | 57782 | -4694 |
| Logic LUT | 62388 | 57694 | -4694 |
| LUT as Memory | 88 | 88 | 0 |
| FF | 37638 | 34363 | -3275 |
| DSP48E1 | 128 | 128 | 0 |
| RAMB36E1 | 16 | 16 | 0 |
| RAMB18 | 0 | 0 | 0 |
| Blackboxes | 0 | 0 | 0 |

Physical LUT/FF/DSP/BRAM values come from Vivado `reports/opt-utilization.rpt`. Status is `VIVADO_COMPLETE`, resource-fit is `fits=1`, and `reports/opt-blackboxes.txt` reports `count=0`. The final BRAM mapping table in `vivado.log` lists banks 0–15 as 1K×32 WRITE_FIRST, one RAMB36 and no RAMB18 each.

The fresh run used:

```bash
OUT="$PWD/build/fpga/a8-w8-d16-partial20-area-20260909-015233"
VIVADO=/tools/Xilinx/2025.2/Vivado/bin/vivado \
  scripts/fpga_build.sh --mode area --bits 8 --dim 16 \
  --hierarchy rebuilt --out "$OUT"
```

For a rerun, select a new output directory. The flow records its source snapshot, diff, tool versions, command lines, reports, and checkpoints.

## PE diagnostic comparison

| Diagnostic | Baseline | INT20 | Delta |
|---|---:|---:|---:|
| processingElements LUT/MUX | 49019 | 46111 | -2908 |
| processingElements FF | 14449 | 11387 | -3062 |
| processingElements CARRY4 | 4544 | 3824 | -720 |
| partialPipe LUT/MUX | 38072 | 35874 | -2198 |
| partialPipe FF | 8208 | 5328 | -2880 |
| partialPipe CARRY4 | 4544 | 3824 | -720 |
| weightRegs LUT/MUX | 10937 | 10225 | -712 |
| weightRegs FF | 4096 | 4096 | 0 |

Both processingElements and partialPipe retain zero DSP48E1 instances. Total optimized-design CARRY4 count changes from 6314 to 5578 (-736).

The original diagnostic Tcl runs on the new `checkpoints/opt.dcp` using identical primitive filters: LUT1–6/MUXF7–8, FD*, DSP48E1, and CARRY4. Name filters are `*processingElements*`, `*processingElements*partialPipe*`, and `*processingElements*weightRegs*`. Groups overlap; do not sum them. Diagnostic LUT/MUX totals are not physical Slice LUT totals.

Reproduction:

```bash
IM2P_OPT_DCP="$OUT/checkpoints/opt.dcp" \
  /tools/Xilinx/2025.2/Vivado/bin/vivado -mode batch -nojournal \
  -log "$OUT/reports/pe-attribution.log" -source "$OUT/pe-attribution.tcl"
```

The original diagnostic Tcl/log are also preserved under `build/experiments/partial20/`.

## Interpretation

Local partial narrowing reduces physical Slice LUT use by 4694 (7.51%) and FF use by 3275 (8.70%). The partialPipe attribution shows 2880 fewer FFs (35.09%) and 720 fewer CARRY4s (15.85%), directly supporting the intended mechanism. Source and generated-RTL checks identify no second architectural optimization.

Weight register FF count remains 4096. Its name-based LUT/MUX attribution decreases by 712 (6.51%) even though the weight RTL/control is unchanged. ActivationPipe and loadedRows attribution also change under synthesis optimization. These diagnostic name groups cannot isolate a physical LUT saving per source register. The 4694-LUT result is the measured whole-core delta for the single width change, including consequent synthesis optimization.

Device LUT margin increases from 924 to 5618. The core still exceeds the 50000-LUT integration-margin goal by 7782. Numerical exactness, tested cycle behavior, BRAM architecture, and DSP mapping policy are preserved. The experiment validates a useful area reduction without claiming timing or board closure.

Follow-up candidates, recorded only: dual-bank weight datapath, common active-bank control, engine_results wide FIFO, remaining PE-local duplicated control, scheduler/address logic, selective DSP mapping.

## Remaining issues

- BSV `-check-assert`: existing flow unchanged; no assertion-flow fix included.
- G0117 action-shadowing: existing warnings remain; no suppression or scheduling change.
- Timing and route closure: not evaluated by this OOC area experiment.
- Board shell/system integration: not performed.

## Git state

Work remains uncommitted on `exp/pe-local-partial-int20`. Baseline branch remains at `dc4a1a621f63834d64df42ae8c24152747d97971`. No commit, push, merge, rebase, or PR creation is performed.
