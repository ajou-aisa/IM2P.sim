# IM2P.sim

IM2P.sim maintains two active purposes: end-to-end build/API/numerical RTL
verification, and a value-independent boundary for a future NPU cycle simulator.
Existing generic Gemmini synthesis support is retained for later characterization.
The value-free cycle simulator, op-trace replay, Fmax/resource analysis and TOPS
calculation are **not implemented by this cleanup**.

| Responsibility | Current authority |
|---|---|
| Integrated Gemmini HP1 | `src/gemmini/control/`, pinned upstream Gemmini and SCU primitives |
| C ABI / runtime | `sim/include/`, `sim/ffi/`, `sim/backends/gemmini_hp1/` |
| Value-independent scheduling | `sim/common/gemmini_schedule.{hpp,cpp}`; no timing estimator |
| Host/API/numerical fixtures | `frontend/`, `fpga/gemmini_hp1/host/` |
| Generic synthesis | `fpga/gemmini_hp1/{flow,boards}/`, `scripts/gemmini_board.py` |
| Retained BSV reference | `src/` BSV, profile wrappers in `synth/`, `sim/ffi/im2p_verilator.cpp` |

`IM2P_SIM_IMPLEMENTATION` selects `LEGACY_BSV` (the default/reference) or
`GEMMINI_HP1`. Gemmini HP1 supports paired A4/W4 and A8/W8 at DIM16/32/64.
LEGACY_BSV retains its separate matched ExSIA A4/Q4, A8/Q8 and A16/Q16 support;
A16 is not a Gemmini HP1 profile. The numerical contracts and cycle definitions
of these implementations are not interchangeable.

The shared frontend retains FULL/PIPELINE modes and the canonical ABI. Its legacy
matched ExSIA A4/Q4, A8/Q8 and A16/Q16 routes retain fail-closed H2/HP2 and
unsupported mixed precision handling; these are not additional Gemmini profiles.

## Build and validation

Start with [build/verification](docs/VERIFICATION.md), [API/ABI](docs/API_CONTRACT.md),
[the numerical contract](docs/NUMERICAL_CONTRACT.md), and
[Gemmini architecture](docs/GEMMINI_ARCHITECTURE.md). Current Gemmini validation
uses `scripts/gemmini_build.py --top integrated`; the alternate standalone GEMM
top is removed, while lower-level primitive tests remain test-only.
`make gemmini-schedule-test` does not need numerical buffers or Verilator.

The generic frontend-real Gemmini runner has a recorded baseline-existing
stream-start failure. It is not counted as a passing integrated numerical fixture.
The [completed refactor report](docs/GEMMINI_REFACTOR_REPORT.md) records the
established six-profile and 268-case exact cycle evidence. The
[cleanup report](docs/REPOSITORY_CLEANUP_REPORT.md) records the current changes and
validation, with the pre-delete [inventory](docs/REPOSITORY_CLEANUP_INVENTORY.json).

## Boundaries and retained reference

[RTL cycle accounting](docs/RTL_CYCLE_ACCOUNTING.md) and
[cycle-simulation preparation](docs/GEMMINI_CYCLE_SIM_BOUNDARY.md) describe existing
logical endpoints and planning facts, not an implemented CPU/NPU timeline model.
[Export and synthesis](docs/SYNTHESIS.md) retain generic current infrastructure;
there is no physical-board deployment, programming or flash workflow in scope.
The current host `uart.*` files are packet codecs used by API tests, not a physical
device driver.

The [legacy documentation area](docs/legacy/README.md) preserves BSV architecture,
algorithms and verification without presenting BSV as the preferred future
synthesis implementation. Discarded experiment assets remain in Git history;
there is no archive directory. New build, model, evidence and synthesis artifacts
belong outside the repository under a fresh `$HOME/aisa-lab/build/...` directory.
