# Evaluation v3 operating-clock boundary

## Confirmed evaluation-only OOC policy

The authoritative current policy is `config/evaluation_ooc_policy.json`:
`xcu250-figd2104-2L-e` as an Alveo U250 **reference part only**, `CLK`/`core_clk`,
post-route closure only, 33 dense INT8 TOPS target, one array and one independent
MAC per PE per cycle for both A4 and A8. No fallback part, board deployment,
bitstream, physical pins, PCIe/DDR constraints or invented I/O delays are allowed.
The earlier generic preflight artifacts remain historical, before this policy.

```sh
python3 -B scripts/evaluation_ooc.py preflight --out /fresh/hardware/ooc-preflight.json
python3 -B scripts/evaluation_ooc.py run \
  --frequencies-hz "$FREQUENCY_1_HZ" "$FREQUENCY_2_HZ" \
  --timeout-seconds 1800 --out /fresh/hardware/ooc-sweep
```

The frequency list is an explicit finite positive integer set (at most 64), not
a hidden frequency heuristic. Every run covers all six profiles sequentially.
Preflight queries the exact part in the installed Vivado when running on Linux;
missing tools, unsupported part or missing finite grid remain `NOT_READY`.
No synthesizable clock is invented on the development Mac.

`evaluation_ooc.py` first uses the official current-source `gemmini_build.py
--stage rtl --top integrated`, preserving before/after hardware-contract hashes
and emitted RTL hashes. It generates a wire-only `PoTalEvaluationOoc` wrapper
which maps the generated production `clock` input to `CLK`; every other port is
forwarded unchanged. The production module includes array, scratchpad,
accumulator, SCU and controller. The currently inspected six-profile RTL exposes
synchronous `reset`, not `RST_N`, so no reset exception is emitted. Only an actual
scalar `RST_N` plus a direct asynchronous event in that core's generated source
permits the optional reset-origin exception.

The separate `fpga/gemmini_hp1/flow/evaluation_ooc.tcl` uses OOC synthesis,
optimization, placement and routing. It whitelists only
`create_clock -name core_clk -period <PERIOD_NS> [get_ports CLK]` and that proven
optional reset exception. It never consumes the generic board manifest.
Internal core-clock register paths are the timing scope. Missing external I/O
delays are counted and reported, not replaced with arbitrary values; they are not
external interface latency certification. Unclocked/unconstrained internal
endpoints, clock ambiguity, combinational loops, negative setup/hold slack,
blocking DRC, missing timing paths and no-fit samples cannot yield a clock.

Raw route results include actual clock period, sampled worst setup/hold paths,
constraint counters and tool version. Resource counts are parsed from the routed
utilization report, with half BRAM tiles represented as integer BRAM18-equivalent
units. Unknown report formats fail closed. Exact source/RTL/wrapper/flow/XDC,
checkpoint and raw report bindings survive normalization. A period mismatch
rejects rather than relabels the achieved frequency.

The raw tool report is now `im2p-ooc-tool-result` v2. Routed WPWS/TPWS,
pulse-width failing endpoints and total analyzed endpoints must be present,
finite and passing. These pin-switching checks include minimum/maximum period,
high/low pulse width and maximum skew, not only setup/hold WNS. The dedicated
`report_pulse_width` output and timing summary are both content-bound. Missing,
unknown, negative or zero-endpoint coverage fails closed; earlier v1 raw reports
cannot satisfy this gate. See AMD's [pin switching limits](https://docs.amd.com/r/en-US/ug906-vivado-design-analysis/Pulse-Width-Area-Pin-Switching-Limits?contentId=k9Gci8iJQWM4M8d9myByIQ).

The current operating-clock artifact is `im2p-operating-clock` **v2** and binds
this policy, every completed route sample and the complete finite sweep record.
The current `load_selection(path, profile)` rejects generic v1 clocks and
synthetic-only artifacts. Post-synthesis estimates remain diagnostic. Tool
protocol tests and Verilator wrapper lint are not Vivado synthesis/route evidence.

Command definitions follow AMD's [OOC synthesis](https://docs.amd.com/r/en-US/ug835-vivado-tcl-commands/synth_design),
[timing checks](https://docs.amd.com/r/en-US/ug835-vivado-tcl-commands/check_timing),
and [timing-path query](https://docs.amd.com/r/en-US/ug835-vivado-tcl-commands/get_timing_paths)
documentation. Documentation does not prove the local tool supports the requested
part; the preflight query does that only when it actually runs.

## Retained generic diagnostic import

`scripts/evaluation_clock.py` adds a finite clock selector, not a synthesis tool,
timing model or physical-Fmax measurement. It reuses the existing profile catalog,
board validator and integrated production synthesis flow. No frequency is supplied
by the cycle library or by a CPU-functional collection.

## Preflight

```sh
python3 -B scripts/evaluation_clock.py preflight --out /fresh/hardware/preflight.json
python3 -B scripts/evaluation_clock.py preflight --board "$BOARD" \
  --target-peak-tops "$GPU_PEAK_TOPS" --target-basis "$GPU_PEAK_BASIS" \
  --out /fresh/hardware/configured-preflight.json
```

Exit 2 means `NOT_READY`; the artifact lists missing inputs. An unresolved board
template is not a target. Darwin is not the supported Vivado execution host.
`CONFIGURED_NOT_RUN` means configuration checks passed, never synthesis PASS.
GPU target basis must identify the actual comparison and dense/sparse convention;
neither a GPU peak nor its equivalence to CUDA model throughput is inferred.

For each explicitly chosen finite test frequency, supply the matching XDC and
board manifest to the existing command on a configured Linux synthesis host:

```sh
python3 -B scripts/gemmini_build.py --a-bits 8 --w-bits 8 --dim 16 \
  --scu hp1-left-shift --memory-contract config/gemmini_host_memory_contracts/a8w8-d16-hp1.json \
  --top integrated --stage route --board "$BOARD" --clock-mhz "$CLOCK_MHZ" \
  --out "$FRESH_BUILD"
```

Repeat only the predeclared finite frequency set. The board's frequency and XDC
must agree. Do not rewrite one constraints file while retaining earlier evidence.
`synth` remains supported by the existing builder, but its utilization report
alone provides no valid timing sample. Neither command programs a device.

## Observation import contract

The generic selector imports normalized tool observations, not arbitrary Vivado
text. The current OOC adapter above produces `im2p-clock-tool-report` v1 from its
raw route reports and binds the entire chain. Actual-tool validation remains
`NOT_RUN` without Vivado; synthetic tests use `execution_kind=SYNTHETIC`.

Each `im2p-clock-observation` v1 JSON has these required fields (no unknown fields):

- `execution_kind`: `TOOL_EXECUTION` or `SYNTHETIC`.
- `profile`, `top`, `top_scope=INTEGRATED`, `hardware_contract_sha256`.
- `source`, `netlist`, `tool_report`, `peak_basis`: artifact references with
  `path` and `sha256`; `constraints` is a nonempty list of unique references.
  `source` identifies the actual build-input snapshot, not a later checkout.
- `tool`: nonempty `name` and `version`; `technology`, `memory_implementation`,
  `memory_interface` identify the actual implementation and backing interface.
- `timing_stage`: `POST_SYNTH_ESTIMATE` or `POST_ROUTE`.
- `frequency_hz`, `array_count`, `independent_macs_per_pe_per_cycle` are positive
  integers. `peak_basis` binds the architectural justification for the last two.
- `tool_exit_code`, finite rational-string `setup_slack_ns` and `hold_slack_ns`,
  `timed_paths`, `unconstrained_paths`, boolean `fit`, and integer maps
  `resource_usage` and `resource_limits` with identical nonempty key sets.

The normalized tool-report file has `schema=im2p-clock-tool-report`, `version=1`
and exactly the following fields: `top`, `frequency_hz`, `tool_exit_code`,
`setup_slack_ns`, `hold_slack_ns`, `timed_paths`, `unconstrained_paths`, `fit`,
`resource_usage`, `resource_limits`. Values must equal the observation.
Hash checks establish content binding, not signatures or independent proof of
truth. Report-adapter validation remains an explicit prerequisite.

All frequencies in a selection must share source, profile, complete production
top, hardware contract, tool/version, technology/memory, peak basis and timing
stage. Mixed synthetic/real evidence is rejected. Different netlists and frequency
constraints are expected; every referenced byte remains independently bound.

## Selection and consumption

```sh
python3 -B scripts/evaluation_clock.py select \
  --observations "$OBSERVATION_1" "$OBSERVATION_2" \
  --target-peak-tops "$GPU_PEAK_TOPS" --target-basis "$GPU_PEAK_BASIS" \
  --out /fresh/hardware/clock-selection.json
```

Post-synthesis-only data, negative setup/hold slack, unconstrained/no timed paths, tool failure, no-fit or
over-capacity samples never pass. With a reachable GPU target, choose the passing
frequency with nearest peak, resolving ties toward lower frequency. Otherwise
choose the highest tested passing frequency. With no target or no passing sample,
selected frequency is null. The artifact reports finite bounds and individual
search steps; no interpolation or untested maximum is claimed.

Peak uses exact rational arithmetic:

`2 * array_count * DIM^2 * independent_macs_per_pe_per_cycle * frequency_hz / 10^12`

A4 does not automatically double throughput. `POST_SYNTH_ESTIMATE` never becomes
post-route or physical Fmax. `SYNTHETIC_ONLY` is never a real clock.

`scripts.evaluation_clock.load_selection(path, profile)` now requires the v2
fixed-policy OOC chain before returning frequency, profile, hardware-contract hash
and selection-file hash. Generic `select` results are `DIAGNOSTIC_ONLY` or
`SYNTHETIC_ONLY`, never a current verified clock. Outputs require fresh paths.

The six profile identities are supported by unit/CLI fixtures, not six synthesis
runs. Missing synthesis does not block activation or residual metric collection.
