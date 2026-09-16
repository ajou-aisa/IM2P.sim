# Retained BSV contract notes

These small invariants remain required by the reference implementation. They are
not board measurements, migration instructions or new Gemmini numerical rules.

## Exact PE-local partials

`src/common/Config.bsv` derives local integer partial width from product width
plus log2(DIM). A8 uses 20/21/22 bits for DIM16/32/64; the DIM64 tiled array uses
the full DIM64 partial width through tile boundaries. `Arithmetic.bsv` sign-extends
local partials before the architectural boundary. Local INT20 is not an ABI output
width or a residual input limitation. `TbProfileConfig.bsv`, static width checks
and `sim/tests/rtl_numerical_profile.rs` retain the exact extrema/tail/multi-fragment
checks. No area, Fmax or historical before/after performance figures are migrated.

## Internal activation-response publication

`IM2PCore.putActivationReadResponse` must not accept a new response while the
previous internal response-to-slot publication remains pending. Pending payload,
tag and identity survive until publication. This is distinct from external
activation stripe publication. The source guard and dynamic assertion are retained;
test-only ResidentP0, work-layout and passive activation monitor fixtures under
`tests/scu_block_scale/activation_guard/` preserve the current-source checks.

The strict contract is accepted_response implies no old pending publication.
A general elastic simultaneous turnover argument must not silently replace that
assertion. BSC assertion-enabled elaboration can affect scheduling; Verilator
`--assert` alone does not reintroduce assertions omitted by BSC. Assertion-build
and normal-build timing therefore require their own accurately identified evidence.

## Numerical scope

Legacy vector operations 0..3 preserve their defined wrapping, External block
output and host reconstruction. ABI5 BSV operations 4/5 use their existing SCU
fragment saturation/final-integer policy. Do not apply old general wrap-only prose
to those newer typed operations. The source and current
[numerical contract](../NUMERICAL_CONTRACT.md) take precedence for that boundary.
Current BSV configuration, scalar golden inputs, C API layout/runtime tests and
lightweight smoke remain; obsolete physical provider/capture campaigns are deleted.
