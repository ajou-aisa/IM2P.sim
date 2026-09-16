# Build and verification

Use fresh external output directories. A generated model, schema check or build
success alone is not numerical/cycle verification. The default implementation is
LEGACY_BSV; explicitly select GEMMINI_HP1 for its simulator library.

The shared frontend retains FULL/PIPELINE modes and the canonical ABI. Its legacy
matched ExSIA A4/Q4, A8/Q8 and A16/Q16 routes retain fail-closed H2/HP2 and
unsupported mixed precision handling; these are not additional Gemmini profiles.

## Current integrated Gemmini

```sh
OUT="$HOME/aisa-lab/build/im2p-gemmini/verify-$(date -u +%Y%m%dT%H%M%SZ)"
python3 -B scripts/gemmini_vendor.py --verify
make gemmini-schedule-test
python3 -B scripts/gemmini_build.py   --a-bits 4 --w-bits 4 --dim 16 --scu hp1-left-shift   --memory-contract config/gemmini_host_memory_contracts/a4w4-d16-hp1.json   --top integrated --stage test --out "$OUT/scala"
python3 -B scripts/gemmini_build.py   --matrix a4w4,a8w8 --dims 16,32,64 --scu hp1-left-shift   --memory-contract-dir config/gemmini_host_memory_contracts   --top integrated --stage host-test --out "$OUT/matrix"
```

The matrix covers elaboration, Verilator lint/build, current host/API CTest and
the independent `test_ws_rtl.cpp` numerical frontend/RMD fixtures. Scala primitive
checks include the retained test-only ScratchpadBank packed-bank/order harness.
The removed standalone selector is rejected. SBT hardware-contract tests need
selected profile and resolved hardware properties; the build script supplies them.

The pure planner test checks captured pre-refactor work/loop/digest equality,
fragment ordering, request/byte extents and signed packing. Exact cycle evidence
uses completed RTL endpoints and request/response counters, not those digests as
a timing proxy. See [cycle accounting](RTL_CYCLE_ACCOUNTING.md).

The six-profile frontend-real runner has an established baseline-existing
`failed to start IM2P stream` failure. Do not count it as a passing integrated
fixture or conflate it with the historical outer-K runtime-fixture failure.

## Retained LEGACY_BSV reference

The legacy matched ExSIA A4/Q4, A8/Q8 and A16/Q16 routes are distinct from Gemmini
HP1's six-profile support. [The legacy verification guide](legacy/VERIFICATION.md)
retains the BSV and simulator reference entry points. A small smoke is:

```sh
OUT="$HOME/aisa-lab/build/im2p-legacy-smoke-new"
make check BUILD_DIR="$OUT"
make c-api-layout-test BUILD_DIR="$OUT"
make bsv-test-one TOP=mkTbHostRowOffset BUILD_DIR="$OUT"
make sim-test-a8-w8-d16 IM2P_SIM_IMPLEMENTATION=LEGACY_BSV   CARGO_TEST_FILTER=raw_clock_periods_are_exact BUILD_DIR="$OUT"
```

This filtered smoke is not the full nine-profile legacy suite. Internal activation
response-publication checks remain in the current-source activation-guard tests;
see [legacy contract notes](legacy/CONTRACT_NOTES.md). Independent SCU scalar
oracle self-checks run with `python3 -B tests/scu_block_scale/golden.py`.

## Export, static checks and synthesis planning

`tests/test_gemmini_export.py` retains source-only and integrated export relocation,
closure, tamper, forbidden-binary and no-simulator host-audit checks.
`gemmini_export.py` remains the supported export entry point; `gemmini_evidence.py`
is now its current runtime-validation helper library rather than a historical
campaign packager. Audit role strings are retained compatibility metadata, not
physical execution. Use the export CLI's create/verify/extract commands and
fresh external destinations.

Run `git diff --check`, vendor verification, Python/static checks on modified
scripts, and the relevant build/export tests. [Generic synthesis](SYNTHESIS.md)
can be checked with board schema and non-Vivado planning tests. Actual synthesis,
Fmax/resource/TOPS characterization and physical execution remain NOT_RUN during
cleanup. Validation results belong in the cleanup report and external evidence.
