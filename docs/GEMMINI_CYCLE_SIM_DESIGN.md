# Production Gemmini scheduling and cycle-simulation design

This document describes the current production boundary. Historical audit states
and superseded migration blockers belong in phase evidence, not in this design.

## 1. Scheduling authority

The production auto-tiler is
`ggml::gemmini::gemmini_set_tile_ws()` from the shared Gemmini include tree. It
selects DIM-count `tile_I`, `tile_J`, and `tile_K` factors.

IM2P does not run a second tiling heuristic. `sim/common/gemmini_schedule.*` is a
pure hardware schedule **lowerer**: it consumes selected geometry and computes
outer traversal, block32 splitting, fragment/contribution flags, scale addressing
and byte/request extents.

The additive `im2p_production_geometry_v1_t` companion carries selected geometry
through the generic frontend/C ABI/Rust/runtime boundary. Admission fails closed
when geometry identity is missing, inconsistent, or changes across a stream.
Existing numerical ABI v5 and cycle ABI v1 remain separate from this companion.

## 2. Production call graph

### Generic IM2P_SIM FULL

```text
llama.cpp-gemmini
  ggml_backend_gemmini_mul_mat
    -> gemmini_set_tile_ws
    -> im2p adapter / production-geometry companion

IM2P.sim frontend
  -> im2p_execute_matmul_extended
  -> C ABI / Rust runtime
  -> integrated Gemmini HP1 runtime
  -> gemmini_schedule lowerer
  -> accepted RTL work
  -> UpstreamWsHp1Top
```

### Generic IM2P_SIM PIPELINE

The same selected geometry is snapshotted per admitted stripe. Stripe identity,
row range and geometry are checked before publication. The runtime preserves the
two host activation slots and the existing FULL/PIPELINE ownership rules.

Aggregate multi-stripe system timing is not part of the single-GEMM cycle API.
Per-work accepted geometry and numerical execution are certified; a CPU/NPU system
timeline is a separate future feature.

### Bound HP1 host

The physical-host boundary retains `WorkPlanV1`, binds the selected executor, and
submits FULL or PIPELINE work without re-tiling. It is also used by integrated
RTL fixtures to prove the host/publication contract.

## 3. Production residual (RMD-SCU)

Residual correction is not a second NPU numerical path. Production residual uses:

```text
host residual selection + balanced-radix packet construction
  -> one logical compact residual GEMM
  -> normal WS systolic array
  -> normal HP1 SCU carrier application + Sat32
  -> signed32 accumulator Sat32 across physical fragments
  -> signed32 residual lane output
  -> host balanced-radix recomposition
  -> host floating reconstruction / merge
```

The production work plan is `DENSE_HP1_FINAL` with `rmdRaw=false`. HP1 carrier
metadata is transported to the NPU. The host does **not** multiply NPU output by
an HP1 integer block factor.

Hardware owns physical fragmentation. A DIM16 compact K=31 residual is one
logical NPU GEMM with physical reductions 16 and 15; it is not two host-issued
residual calls.

`RMD_RAW` and the raw callback remain only for frozen diagnostic/historical
checks. Their existence must not be used as production RMD metadata or as a
fallback for an explicitly selected production HP1 residual route.

## 4. Cycle-model boundary

`sim/cycle/` is a value-free single-GEMM logical-cycle model. Inputs are scalar
shape/profile/geometry/memory-timing facts. It does not consume tensor values,
SCU numerical answers, or recorded RTL durations.

The cycle model reuses the pure schedule lowerer. RTL remains the timing golden.
Event certificates compare selected per-cycle event-type multisets rather than a
claim that every internal RTL event payload/order is modeled.

Current scoped certificates include:

- `regression-tiles`: 268/268 exact, maximum absolute cycle delta 0;
- production planner blocks: 262/262 exact over the representable subset, with
  six explicit K8256 scale-capacity exclusions;
- production RMD-SCU: 72/72 exact across six A4W4/A8W8 DIM16/32/64 profiles,
  with accepted work derived from the production residual path;
- historical/diagnostic RMD_RAW timing: 24/24 paired cases over its documented
  compact domain. This does not describe the production residual route.

The event hard gate is independent of endpoint/counter equality: shifting one
selected event by one cycle must make the comparator fail.

## 5. Production geometry certificate

The production-geometry certificate compares:

1. factors selected by the shared production tiler;
2. factors transported through `im2p_production_geometry_v1_t`;
3. factors consumed by the runtime lowerer; and
4. actual accepted RTL descriptors.

The known A8W8/DIM32 M=129/N=129/K=96 counterexample remains a guard:

```text
selected / transported / lowered tile = 2 / 4 / 3
expected accepted work              = 18
actual accepted work                = 18
descriptor equality                 = true
```

Both FULL and PIPELINE representative runs cover all six profiles.

## 6. Artifact metadata and export provenance

Current resolved profiles advertise RMD capability separately from the numerical
route:

```json
{
  "rmd_enabled": true,
  "rmd_datapath": "NORMAL_HP1_SCALED",
  "rmd_raw": false,
  "rmd_numerical_revision": "rmd-hp1-scu-sat32-radix-v1",
  "host_integer_block_multiply": false,
  "work_kinds": ["DENSE_HP1_FINAL"],
  "diagnostic_work_kinds": ["RMD_RAW"]
}
```

Export verification requires the current `WS_RMD_RUNS_DIAGNOSTIC` and
`WS_RMD_BOUND_RUNS_DIAGNOSTIC` runtime markers for an RMD-enabled profile.
These markers check the host/RTL capability, FULL/PIPELINE execution, and rollback;
they are not a production-generated run-aware cycle certificate. The export also packages the RMD executor/reference,
HP1 SCU/carrier helpers and relevant GGML headers under the dependency source
snapshot, plus exact dependency HEAD/patch/overlay provenance.

`diagnostic_work_kinds` records that raw hardware diagnostics still exist; it is
not a production work-kind list.

## 7. Scope limits

Implemented/certified boundaries above do not imply:

- op-trace or multi-op replay;
- a CPU/NPU system scheduler timeline;
- synthesis, implementation, Fmax, resource or TOPS characterization;
- physical FPGA execution or board latency.

See [cycle-model details](GEMMINI_CYCLE_MODEL.md), the
[cycle/simulation boundary](GEMMINI_CYCLE_SIM_BOUNDARY.md), and
[RTL cycle accounting](RTL_CYCLE_ACCOUNTING.md) for the scoped definitions used
by those certificates.
