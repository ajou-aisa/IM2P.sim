# Production RMD-SCU contract

Residual correction uses the same numerical HP1 datapath as dense GEMM. The
production sequence is:

```text
residual selection + balanced-radix packet construction (CPU)
  -> compact residual GEMM (NPU)
  -> normal WS systolic array
  -> normal HP1 SCU carrier application + Sat32
  -> signed32 accumulator Sat32 across hardware fragments
  -> signed32 residual lane result
  -> balanced-radix recomposition (CPU)
  -> column/activation floating reconstruction and merge (CPU)
```

Production residual work is **not** `RMD_RAW`. Its work plan uses
`DENSE_HP1_FINAL`, `rmdRaw=false`, and the normal HP1 scale/carrier path. The
host does not multiply NPU output by an HP1 integer block factor.

## Metadata contract

Every current integrated HP1 resolved profile advertises the production
contract explicitly:

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

`RMD_RAW` remains implemented only as a historical/diagnostic hardware work
kind. It is listed separately so its existence cannot be confused with the
production residual route.

## Runtime boundary

`fpga/gemmini_hp1/host/rmd.cpp` adapts a residual packet to `RmdScuWork`. The
selected production geometry and original block identity are retained, HP1
carriers are read through the same provider scale contract as dense work, and
the resulting `WorkPlanV1` uses `WorkKind::dense_hp1_final`.

The compact logical GEMM is submitted once. Hardware scheduling owns physical K
fragmentation and accumulator contribution ordering. For example, DIM16 with
compact K=31 is one logical residual GEMM whose physical reductions are 16 and
15; the CPU does not submit two residual GEMMs and sum them.

The `RmdRawWork` callback and `execute_rmd_raw_descriptor` entry remain for
frozen diagnostics and historical timing evidence. Production entry points must
fail closed rather than silently fall back to that route or to CPU-direct
execution.

## Numerical reference

The authoritative residual integer reference follows hardware operation order:

```text
per fragment: raw dot -> HP1 SCU -> Sat32
across fragments: signed32 accumulator -> Sat32
after NPU: balanced-radix recomposition on CPU
then: floating scale reconstruction / merge on CPU
```

HP1 metadata is transported as a carrier. `INT16_MIN` maps to the zero sentinel
`0x80000000`; exponents `0..32767` remain carriers. Other negative values are
invalid metadata and are rejected instead of being interpreted as host integer
scale factors.

## Runtime and export evidence

The integrated RTL test emits current RMD-SCU provenance markers:

- `WS_RMD_SCU ... scaled_exact=... high_exponent_scu=1 ...`
- `WS_RMD_BOUND ... dense_calls=... scu_calls=... public_entry=1`

Export verification requires those markers for an `rmd_enabled=true` profile,
requires `rmd_raw=false`, and packages the RMD/HP1 numerical source closure
(including the HP1 SCU/carrier helpers) with the dependency snapshot.

The certified simulator scope covers all six A4W4/A8W8 DIM16/32/64 profiles,
including same-hardware residual/dense pairs and value-free cycle comparison.
This is not a physical FPGA latency or synthesis result.
