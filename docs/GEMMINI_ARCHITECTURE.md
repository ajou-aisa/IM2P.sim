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
numerical buffers, MAC/SCU oracle or timing policy. This is a deterministic
hardware schedule lowerer, not a software auto-tiler: selected DIM-count factors
belong to the shared production `ggml::gemmini::gemmini_set_tile_ws` function.
The Gemmini runtime consumes that lowerer; `sim/backends/gemmini_hp1/` owns
model/session/stripe state, handshake sequencing and backing-memory translation.
The C ABI shim is `sim/ffi/im2p_gemmini_integrated.cpp`.

[Production scheduling authority](GEMMINI_CYCLE_SIM_DESIGN.md) traces the exact
route boundaries. The selected DIM-count geometry is transported by the additive
`im2p_production_geometry_v1_t` companion through the public C API and Rust
runtime, so `sim/common/gemmini_schedule.*` lowers the actual production tile
factors instead of reconstructing or re-tiling them. FULL and PIPELINE accepted
RTL descriptors are certified against that geometry across all six profiles.

Production RMD follows the same rule: residual identity is host provenance, not a
second NPU datapath. The residual executor submits normal `DENSE_HP1_FINAL` work
with `rmdRaw=false`, HP1 carriers, and the same SCU/Sat32 accumulator semantics as
dense GEMM. Historical `RMD_RAW` support remains diagnostic only.

The existing one-read/one-write runtime ownership, two stripe slots, per-drive
generation advancement, load/execute/store overlap and logical endpoint semantics
are unchanged. [Cycle accounting](RTL_CYCLE_ACCOUNTING.md) distinguishes RTL
endpoints from host scheduling time and planner payload sizes from bus traffic.

## Retained boundaries

The current host/API/numerical code remains under `fpga/gemmini_hp1/host/`.
Generic part/board manifests and the existing synthesis Tcl remain under
`fpga/gemmini_hp1/{boards,flow}/`; see [synthesis](SYNTHESIS.md).
BSV is an explicitly retained [reference implementation](legacy/README.md), not
an obsolete physical-board provider. The shared [planning boundary](GEMMINI_CYCLE_SIM_BOUNDARY.md) now feeds the
separate [single-GEMM cycle model](GEMMINI_CYCLE_MODEL.md). Its regression-profile
certificate is not an arbitrary workload/system timing guarantee.
