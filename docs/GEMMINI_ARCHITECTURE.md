# Current Gemmini HP1 architecture

The authoritative hardware is the integrated upstream weight-stationary path,
`UpstreamWsHp1Top`, using the pinned Gemmini controllers, memory, array and SCU
writeback. The repository provides a standalone simulation/elaboration boundary
around that integrated hardware, not a completed Chipyard SoC integration.
There is no alternate `StandaloneTop` GEMM implementation in the active sources.

## Scala responsibility and compile source sets

`src/gemmini/build.sbt` selects sources explicitly. `scuCore` contains numerical
SCU/scale and reusable primitives without a host or upstream-controller dependency.
`gemminiIntegration` depends on those primitives and upstream Gemmini, exposing
execution metadata/writeback/control. The root project owns IM2P descriptors,
HostCommandBridge, ScaleBackingLoader, backing ports and integrated elaboration.
`diagnostics` is a test-only project with no production compilation sources;
its ScratchpadBankHarness retains packed-bank isolation and response-order tests.

Dependency consumers point toward the primitives; host descriptors and backing
addresses do not become SCU inputs. Vendor snapshots and lock/manifests preserve
upstream provenance; the ordered patches produce the explicitly substituted
upstream source set, never a second compiled copy of a class.

## Runtime and pure planner

`sim/common/gemmini_schedule.{hpp,cpp}` owns the existing value-independent
loop/fragment decomposition and request/byte extents. It has no Verilator model,
numerical buffers, MAC/SCU oracle or timing policy. The Gemmini runtime consumes
that one planning authority; `sim/backends/gemmini_hp1/` owns model/session/stripe
state, handshake sequencing and backing-memory translation. The C ABI shim is
`sim/ffi/im2p_gemmini_integrated.cpp`.

The existing one-read/one-write runtime ownership, two stripe slots, per-drive
generation advancement, load/execute/store overlap and logical endpoint semantics
are unchanged. [Cycle accounting](RTL_CYCLE_ACCOUNTING.md) distinguishes RTL
endpoints from host scheduling time and planner payload sizes from bus traffic.

## Retained boundaries

The current host/API/numerical code remains under `fpga/gemmini_hp1/host/`.
Generic part/board manifests and the existing synthesis Tcl remain under
`fpga/gemmini_hp1/{boards,flow}/`; see [synthesis](SYNTHESIS.md).
BSV is an explicitly retained [reference implementation](legacy/README.md), not
an obsolete physical-board provider. The future value-free simulator is only a
prepared [planning boundary](GEMMINI_CYCLE_SIM_BOUNDARY.md), not an implementation.
