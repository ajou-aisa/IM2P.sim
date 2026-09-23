# Existing generic Gemmini synthesis support

The retained synthesis path is `scripts/gemmini_build.py` (or its shell wrapper),
`scripts/gemmini_board.py`, `fpga/gemmini_hp1/boards/` and
`fpga/gemmini_hp1/flow/vivado_flow.tcl`. It consumes the current integrated Gemmini
RTL and explicit part/clock/constraint metadata. Profiles and memory contracts
remain authoritative source inputs, not measured result JSON.

Use an actual board/part manifest matching the existing schema; the template is
not a verified physical board. For example, after supplying BOARD and CLOCK_MHZ:

```sh
python3 -B scripts/gemmini_board.py --manifest "$BOARD"
python3 -B scripts/gemmini_build.py   --a-bits 4 --w-bits 4 --dim 16 --scu hp1-left-shift   --memory-contract config/gemmini_host_memory_contracts/a4w4-d16-hp1.json   --top integrated --stage synth --board "$BOARD" --clock-mhz "$CLOCK_MHZ"   --dry-run --out "$HOME/aisa-lab/build/im2p-gemmini/synthesis-plan-new"
```

The existing hardware-stage platform/dependency guard remains in force. A dry run
or a schema test is not a Vivado run and must not be reported as synthesis PASS.
Existing synth/route/bitstream entry points are retained, but no programming,
JTAG, flash or device communication workflow is provided by this cleanup.

All actual generated RTL, tool logs, synthesis reports/checkpoints and export
packages should be written under a fresh external build directory. Do not commit
them. [Evaluation clock selection](EVALUATION_CLOCK.md) provides fail-closed
preflight and finite, artifact-bound selection from normalized timing/resource
observations. Actual tool-report extraction/validation remains required; neither
that selector nor synthetic tests establish Fmax. No new synthesis architecture
or optimization was made.

The separately confirmed evaluation-only OOC flow is
`scripts/evaluation_ooc.py` / `flow/evaluation_ooc.tcl`. It uses only reference
part `xcu250-figd2104-2L-e`, no board pin/I/O-delay/PCIe/DDR constraints and no
bitstream. It emits source-bound post-route observations for the fixed 33 dense
INT8 TOPS policy; actual Vivado execution remains required before selecting a
real clock. See the evaluation-clock document for the finite sweep and v2 artifact.

LEGACY_BSV source and `make rtl-one`/simulation tests remain for reference. The
retired fixed-Arty OOC wrapper and board-provider experiments are not its supported
synthesis path. No Vivado synthesis or physical execution is required or performed
by repository cleanup.
