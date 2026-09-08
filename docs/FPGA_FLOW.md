# Arty A7-100T core OOC flow

The primary target is A8/W8 DIM16 on `xc7a100tcsg324-1`. This flow builds an
accelerator core; it does not supply a board shell, interface IP, bitstream,
programming operation, or board timing closure. A4/A8/A16 DIM16/32/64 RTL remains
selectable. DIM32/64 functionality does not imply fit on this part.

## Reproduce

Run from the repository root. `--out` must name a new directory; omitting it
creates a unique directory under `build/fpga`. Existing reports and checkpoints
are never loaded or overwritten.

```sh
# Works without Vivado; includes Verilator missing-module/error checks.
scripts/fpga_build.sh --mode rtl --bits 8 --dim 16

# Compare area using the same unclocked OOC conditions, separately by hierarchy.
scripts/fpga_build.sh --mode area --hierarchy rebuilt
scripts/fpga_build.sh --mode area --hierarchy none

# Real CLK port, 10 ns; separate fresh synthesis and opt reports.
scripts/fpga_build.sh --mode timing

# Starts place/route only after post-opt resource fit passes.
scripts/fpga_build.sh --mode route

# Small isolated diagnostic matching engine.activationRows at A8 DIM16.
scripts/fpga_build.sh --mode area --diagnostic fifo --hierarchy none
scripts/fpga_build.sh --mode area --diagnostic fifo --hierarchy rebuilt

# Repeat tool-discovery/failure propagation and FIFO RTL/simulation checks.
bash tests/test_fpga_flow.sh
```

`BSC`, `VIVADO`, `VERILATOR`, and `PYTHON` accept executable paths. The BSC library
path is discovered from `bsc -print-flags`; `BSC_VERILOG` explicitly overrides
its Verilog directory. On the user's Ubuntu host, sourcing its own Vivado
`settings64.sh` is appropriate; that absolute installation path is not embedded
in the flow. `iverilog`, `vvp`, and `tclsh` are needed for the flow check suite.
No package installation is performed.

## Identity and completion

Every run records:

- Git commit, dirty/untracked status, and tracked binary diff.
- A source snapshot used by BSC, including configuration JSON and generated BSV/C
  configuration. `profile.json` is exported by `scripts/im2p_config.py`, which
  rejects stale generated configuration. It records widths, rows, capacity,
  numerical semantics revision, memory backend and latency contract, and config
  fingerprints. The FIFO diagnostic uses this surrounding profile identity but
  does not instantiate an accumulator.
- Source hashes, all generated Verilog hashes, and hashes of copied BSC primitives.
  Required primitives are collected transitively. Every generated Verilog file
  is passed to Verilator and Vivado, including separate DIM64 tile modules.
- Actual commands and working directories, BSC flags/version, Verilator version,
  Vivado version when used, hierarchy option, part, clock constraint, reports,
  and separate synth/opt/place/route checkpoints as applicable.

`set -euo pipefail` preserves compiler/Vivado failure through `tee`. The Tcl
driver exits nonzero on error, unresolved black boxes, missing expected resource
rows, or failed resource fit for route mode. A completion marker is created only
after all requested commands finish. `RTL_COMPLETE` means generation/lint only.
`VIVADO_COMPLETE` means the requested report flow completed; it is not a claim
of clean DRC, timing closure, production numerical safety, or successful board
execution. Read `status.txt` and the reports from the same run directory.

## Area and timing reports

Each stage contains utilization (LUT as Logic, LUT as Memory, registers, DSPs,
RAMB18/RAMB36 and tile totals), full hierarchical utilization, primitive counts,
black-box count, DRC and checkpoint. Timing stages also include clocks,
`check_timing`, unconstrained paths, setup, hold, pulse width and timing summary.
Route mode adds route status and post-route utilization.

Area mode has no clock. Timing/route loads this constraint before synthesis:

```tcl
create_clock -name core_clk -period 10.000 [get_ports CLK]
```

This is a real top-level clock, not a virtual unconnected clock. There are no
package-pin, input-delay, output-delay or reset false-path assumptions. External
I/O boundaries therefore remain unconstrained, explicitly reported for later
board-shell integration. A clean internal setup result alone does not verify
100 MHz operation of the complete board. AMD documents these distinctions in
[timing-summary reports](https://docs.amd.com/r/en-US/ug835-vivado-tcl-commands/report_timing_summary)
and [I/O timing constraints](https://docs.amd.com/r/2024.1-English/ug945-vivado-using-constraints-tutorial/Step-7-Timing-Summary-Report).

Post-opt limits are 63,400 LUTs, 126,800 FFs, 240 DSPs and 135 RAMB36-equivalent
tiles. The route guard uses the actual utilization report and rejects unknown
report formats. The approximate 50k core LUT goal reserves interface margin;
exceeding it emits a warning, not a manipulated pass/fail threshold. Fitting
just below 63,400 LUTs leaves inadequate integration margin. Resource fit does
not guarantee routability. See AMD's
[utilization command](https://docs.amd.com/r/2025.2-English/ug835-vivado-tcl-commands/report_utilization)
for stage and hierarchy interpretation.

## FIFO attribution

The local BSC 2026.01 primitive was inspected at
`/opt/homebrew/Cellar/bsc/2026.01/libexec/lib/Verilog/FIFO2.v`. Its SHA-256 was
`bae0481cc69aabde2254b50e3d80bf0a7b6af6ccf27cd981c4b1154c7a848384`.
This is observed local evidence, not a required installation path/hash.

Both the pre-change full-core generated activation FIFO and the new standalone
`SynthActivationFIFO` elaborate to `FIFO2` with `width=128`, `guarded=1`.
The BSV wrapper uses the same `mkGFIFOF(False, True)` as the engine. `FIFO2.v`
has two 128-bit data registers and two occupancy flags; its `guarded` parameter
controls illegal-operation diagnostics, not additional hardware acceptance
logic. The engine's explicit `notFull` check must still prevent illegal enqueue.
Full FIFO plus simultaneous enqueue/dequeue request incurs the existing guarded
enqueue bubble. Empty dequeue remains guarded; undefined empty data is never a
valid payload. The direct primitive test exercises these conditions, replacement
at occupancy one, clear, draining and stalled-payload stability.

The Tcl flow records matching cells and input fan-in startpoints after synth and
opt. `-flatten_hierarchy none` preserves surviving Verilog module boundaries;
`rebuilt` may reassign logic across them. Neither option recreates BSV modules
already inlined by BSC. Isolated FIFO area omits its full-core enable/data cone;
it must not be subtracted as though it were the full-core hierarchy attribution.
The full-system wavefront and backpressure regressions remain necessary. No FIFO
kind/depth replacement was made, and no 10k LUT savings is predicted.

## Evidence and limits

User-provided historical DIM16 **post-opt** results, not newly measured results:

| Profile | Rows | Internal bits | LUT | FF | DSP | BRAM tiles |
|---|---:|---:|---:|---:|---:|---:|
| Baseline | 256 | 64 | 112732 | 45453 | 160 | 0 |
| Diagnostic 1 | 16 | 64 | 105060 | 45346 | 160 | 0 |
| Diagnostic 2 | 16 | 32 | 71216 | 35387 | 129 | 0 |
| Diagnostic 3 | 16 | 20 | 66152 | 31912 | 97 | 0 |

The 64-to-32-bit diagnostic changed the whole old accumulator-width path, not
only PE partial sums. INT20 and rows=16 are diagnostic settings, not final
profiles. The reported activationRows ~10.4k LUT/258 FF is a hierarchy attribution,
not an independent FIFO measurement.

Local checks executed with BSC 2026.01, Verilator 5.046 and Icarus Verilog:
standalone FIFO RTL generation/lint, exact local primitive simulation, CLI
validation, existing-directory rejection, explicit missing-tool errors, Tcl
failure exit, injected compiler exit 71 and Vivado stub exit 72 propagated
through the log pipeline, and rejection of a successful process without the
expected Tcl completion marker.
These checks do not simulate Vivado or establish resource usage.

Vivado is absent from this macOS environment. New LUT/FF/DSP/BRAM measurements,
synth/opt deltas, placement, routing and timing closure remain **unexecuted**.
For the A8 DIM16 1024×32 column banks, a straightforward single-port mapping
would use sixteen RAMB36 tiles for the accumulator payload alone. This is an
unmeasured estimate; actual inference, banking, control and other memories must
be checked in the new report. It is not evidence that the whole core fits.

## Tcl reference validation

Report options were cross-checked against AMD's Vivado 2025.2 command reference:
[hierarchical utilization](https://docs.amd.com/r/2025.2-English/ug835-vivado-tcl-commands/report_utilization),
[timing summary](https://docs.amd.com/r/2025.2-English/ug835-vivado-tcl-commands/report_timing_summary),
and [pulse width](https://docs.amd.com/r/2025.2-English/ug835-vivado-tcl-commands/report_pulse_width).
Documentation checks do not substitute for executing Vivado on the target part.
