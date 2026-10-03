# Current numerical contracts

## Authoritative paths and supported profiles

Gemmini HP1 uses matched A4/W4 and A8/W8 at DIM16, DIM32 and DIM64.
`config/gemmini_hp1_profiles.json`, its selected host-memory contract, the resolver,
`src/gemmini/src/main/scala/im2p/gemmini/SCU.scala`, and
`src/gemmini/control/src/main/scala/im2p/gemmini/UpstreamHp1Writeback.scala`
define the integrated numerical path. A16 and mixed A/W are not Gemmini HP1 profiles.
The retained LEGACY_BSV implementation also supports matched A16/W16 and has
separate legacy operations; its arithmetic is not a substitute for HP1 results.

## Typed scale carriers and ABI

The canonical simulator ABI is version 5 (`sim/include/im2p_sim.h`). Scale elements
are unsigned 32-bit carriers, not byte factors. HP1 operation 5 admits exponents
0 through 32767 and the zero sentinel `0x80000000`; other carriers are invalid.
Stored HP1 `INT16_MIN` maps to the zero sentinel; other negative exponents are not
valid HP1 shifts. Zero scale and exponent zero have different meanings. Zero lanes
retain normal valid, writeback and completion behavior.

The retained BSV path additionally admits operation 4, unsigned H1 multiplication
with beta in 0..65790, and legacy operations 0..3. This is not a claim that the
Gemmini HP1 backend implements H1 operation 4 or External operation 3.
`frontend/src/im2p_gemmini_frontend.cpp`, `sim/src/simulator/validation.rs` and
`sim/src/simulator/matmul/memory.rs` are the metadata admission/translation authorities.
Canonical A/W strides are byte strides; canonical scale stride/column fields use
uint32 element units. The lower-level RTL-facing bridge uses derived byte addresses.
Do not apply the factor-of-four conversion twice. Alignment, bounds and checked
extent arithmetic remain required at the respective API boundaries.

## Fragment order, saturation and final output

For each output coordinate the existing execution order is:

```text
p = exact unscaled signed dot of the current valid fragment
q = Sat32(HP1_exact(p, carrier))
acc = q                              # first contribution, even when q is zero
acc = Sat32(widen(acc) + widen(q))    # each later contribution
```

HP1 zero produces zero; an in-range exponent left-shifts the exact partial.
Large valid shifts clamp by sign/overflow rather than using undefined host-language
shift behavior. SCU saturation precedes accumulator-add saturation. Do not first
sum a K32 block and then apply its scale once. K32 boundaries select metadata but
do not reset a continuing output accumulation. The final valid scalar extent is
M*N, not M*N times the number of K blocks. Intermediate contributions are not
published as final results.

Valid fragment reduction is bounded by DIM, remaining K and the current block32
boundary. DIM16 can have two contributions sharing one K32 scale. DIM64 retains
K32 splitting despite a padded 64-element hardware row. `ScuFragmentPlanner`,
`UpstreamWsControl`, and the pure planner document these execution facts.
Per-profile array partial widths are distinct from the architectural signed32
accumulator and the signed64 provider callback transport. An INT20 local BSV
partial is not an input/output or residual admission restriction.

## Host reconstruction and ownership

Native shared-channel and activation metadata are validated before use: channel
scale must be finite and consistent across its logical K domain; stripe metadata
belongs to the accepted immutable stripe, not an unpublished future stripe.
The existing final-integer frontend reconstructs with the shared channel and
activation scales in binary64 before the final float32 conversion. The separate
legacy External contract instead reconstructs block-raw outputs. Do not conflate
those output domains or reuse the old External expected values for HP1 saturation.

Caller output publication is transactional: a failed provider, bounds check,
composition or fence does not authorize partially committed caller output.
Independent golden checks compare exact integers and the specified floating
reconstruction; changing goldens or tolerances to hide a failure is not allowed.

## RMD production boundary

Production HP1 residual correction uses the normal scaled HP1 datapath, not the
historical raw bypass. `RmdScuWork` carries the selected production geometry and
HP1 carriers into a `DENSE_HP1_FINAL` work plan with `rmdRaw=false`. The WS array,
HP1 SCU and signed32 Sat32 accumulator therefore apply the same numerical order as
main GEMM. Host-side HP1 integer block multiplication is forbidden.

Residual selection, balanced-radix packet construction, radix recomposition and
the final floating reconstruction/merge remain CPU responsibilities. A compact
logical residual GEMM is submitted once; hardware owns DIM/block32 fragmentation
and signed32 accumulation. In particular, DIM16 K31 is one logical call with
physical reductions 16 and 15, not two host-issued residual GEMMs.

`RMD_RAW` remains implemented only for historical/diagnostic checks. Its presence
in RTL metadata must not be interpreted as the production residual contract.
Current export metadata states `rmd_enabled=true`, `rmd_raw=false`, datapath
`NORMAL_HP1_SCALED`, and numerical revision `rmd-hp1-scu-sat32-radix-v1`.

Older frontend/raw failures belong to their archived phase evidence and are not
promoted into current RMD-SCU claims. The completed [refactor report](GEMMINI_REFACTOR_REPORT.md)
records their distinction and the established numerical/cycle evidence.

## Retained executable checks

SCUSpec, ScaleMemorySpec, ScuFragmentPlannerSpec, UpstreamHp1WritebackSpec,
UpstreamWsHp1FullKSpec, `fpga/gemmini_hp1/host/test_ws_rtl.cpp`, and its frontend/RMD
fixtures remain authoritative for Gemmini. BSV `TbScuTypedS1`, `TbScuSaturation`,
`TbScuProfile`, simulator C API tests and the independent
`tests/scu_block_scale/golden.py` retain legacy/reference checks. Committed scalar
golden vectors are test inputs, not physical measurement evidence.
