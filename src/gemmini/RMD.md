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

Each current production packet is block-local: the pinned llama `develop`
producer prunes all-zero `(limb,row)` pairs within an original 32-K block and
emits a separate compact work for each surviving original block. For one such
work, hardware scheduling owns physical K fragmentation and accumulator
contribution ordering. For example, DIM16 with compact K=31 has one block-local
logical work with physical reductions 16 and 15. Multiple original blocks are
not yet submitted as one production logical work.

IM2P also has an additive `im2p_compact_runs_t` version 1 fixture contract. Its
caller provides the already compacted M×K integer matrices, final M/N/K, exact
tile factors, `original_k`, and nonempty runs with original block ID, original
K mask, compact K begin/count. Runs cover compact K contiguously; masks count
survivors; original block IDs increase but may skip blocks. Bad ranges, masks,
order, pointers, sizes, and fragment-base overflow are rejected. The numerical
entry reads weight coordinates in compact K and carriers by run ordinal; the
original block ID selects the hardware scale owner. The shared lowerer ends
physical fragments at run and DIM boundaries without retile, retaining one
logical fixture work. This contract supplies no row-pruning algorithm and
does not derive a run view from a production trace.

The `RmdRawWork` callback and `execute_rmd_raw_descriptor` entry remain for
frozen diagnostics and historical timing evidence. Production entry points must
fail closed rather than silently fall back to that route or to CPU-direct
execution.

## Numerical reference

The authoritative residual integer reference follows hardware operation order:

```text
per fragment: raw dot -> HP1 SCU -> Sat32
across fragments and runs: signed32 accumulator -> Sat32
after NPU: balanced-radix recomposition on CPU
then: floating scale reconstruction / merge on CPU
```

HP1 metadata is transported as a carrier. `INT16_MIN` maps to the zero sentinel
`0x80000000`; exponents `0..32767` remain carriers. Other negative values are
invalid metadata and are rejected instead of being interpreted as host integer
scale factors.

For a run-aware fixture, each original run has its own carrier. The HP1 SCU
applies it before fragment-local signed32 saturation; the accumulator then
saturates after each fragment contribution. The CPU's balanced-radix
recomposition remains the future producer's responsibility. `rmdRaw=false` and
host integer block multiplication remain disabled.

## Runtime and export evidence

The integrated RTL test emits current RMD-SCU provenance markers:

- `WS_RMD_SCU ... scaled_exact=... high_exponent_scu=1 ...`
- `WS_RMD_BOUND ... dense_calls=... scu_calls=... public_entry=1`

Export verification requires those markers for an `rmd_enabled=true` profile,
requires `rmd_raw=false`, and packages the RMD/HP1 numerical source closure
(including the HP1 SCU/carrier helpers) with the dependency snapshot.

The six-profile run-aware corpus is `FIXTURE_ONLY`. The current official
trace-OFF host matrix admitted and numerically matched the independent integer
oracle 48/48. A fresh current-source capture from those official RTL binaries
matched the value-free cycle model's endpoints, counters and selected event
multiset 48/48 with zero cycle delta; two official certificate invocations
produced byte-identical certificate and answer JSON. The earlier fixture
certificate remains historical for its own source bytes.
From `IM2P.sim`, with a new external evidence directory, the fixture certificate
entry is:

```sh
python3 -B sim/tests/cycle/certify_run_aware.py \
  --rtl-root "$RTL_ROOT" --library "$CYCLE_LIBRARY" \
  --task07 "$TASK07_MANIFEST" --task08 "$TASK08_MANIFEST" \
  --out "$OUT/run-certificate"
```

The supplied task manifests must bind that exact cycle library, RTL binaries,
isolated event logs and current source bytes; the archived task-08 manifest
cannot validate a later source revision.

The current comparison's exact source, library and raw-input hashes are in
`.omo/evidence/rmd-cross-block-compact-scu/task-09-current-fixture.json`;
the official host result is in `task-08-official-host.json`. Build input uses a
clean read-only llama `develop` at
`71a8c0328cd436226b8ec5fad03adafac93940ed`: verify that HEAD and an empty
`git status --porcelain=v1` before passing `--llama-root` to the official
`scripts/gemmini_build.py --stage host-test` command documented in
`docs/GEMMINI_CYCLE_MODEL.md`.

The pinned trace-OFF six-profile official host matrix passed 60/60 command
steps, 54/54 CTests and 48/48 numerical run fixtures. The fresh current-source
v2 dense/planner cycle certificate matched 282/282 cases under each submission
framing with zero cycle delta; these are a separate corpus from the 48 run
fixtures. The
fresh legacy block-local RMD-SCU numerical certificate matched
66/66 cases and 72/72 same-hardware pairs. The relocated trace-OFF package's
minimum official host target passed 4/4 CTests on macOS; Linux is `NOT_RUN`.
Trace-ON remains enabled only with a coherent source that has the real optrace
API. The pinned source's absent optrace API is handled by the IM2P trace-OFF
host closure, without fake symbols or changes to the read-only llama source.

The current llama producer still needs a separately authorized
adapter/packet/trace change to emit one cross-block run view. Production
one-logical-cross-block GEMM is `NOT_READY`; a new GPT-2 one-work trace and
parity check are `NOT_RUN`. No CPU/NPU timeline, scheduler, frequency
conversion, synthesis, or physical FPGA result is certified here.
