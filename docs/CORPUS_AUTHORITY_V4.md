# Current isolated GEMM corpus authority v4

Version 4 is an external, reviewed attestation of the immutable trace-ON package at
`/Users/zerogod/aisa-lab/build/im2p-gemmini/production-drained-sequence-20260923T064750Z/certificate/candidate-package-trace-on`.
The package itself still embeds `corpus-authority-v3.json` (SHA-256
`7add3fd188167e11e047f36647e0a28ea567ef8a7dcc3f6c28971c58bac5bb18`).
Version 4 pins that embedded file, the complete package `source-manifest.json`
(`0d04ae5a08428d41d8aaa225f84a5627388822877faf087e52336caef2d09952`),
and `dependency-lock.json`
(`95b38da593920ff79243ee11de1d900506856ea638340d3965c937f3d27f3d93`).
Its producer base HEAD is `0e1c8976d97887ac9494323b99643145e7b5abae`.
Version 3 remains byte-for-byte unchanged and continues to validate its historical
package; the current certificate producer emits the v4 reference only after the
current package passes the v4 checks.

## Reviewed source impact

The v3 producer map is byte-identical to llama commit `f46a31c7` even though
its exported package recorded base HEAD `c61702970df8b287b1ed75b3e51abab559021b51`.
The export permits a tracked source overlay, so base HEAD alone is not the
effective producer source identity. The current `0e1c897` package differs in
exactly one of the 13 v3-pinned producer files:

| File | v3 SHA-256 | v4 SHA-256 |
| --- | --- | --- |
| `ggml/src/ggml-gemmini/residual/rmd/rmd-executor.cpp` | `1b3f95ba2ba1f54dffd104422e384352bf04bbd4dc1dac41c99eb7d88e97b6d0` | `c5c17d819a48129c617ace2fc4aa8c10cd824f1d88dfadb877a84840c784f0c1` |

The `f46a31c7..0e1c897` diff for that file adds 18 lines inside
`#if GGML_GEMMINI_RESIDUAL_METRICS`. With metrics enabled and an evaluation
context, the new code records compact run and row geometry and may return
`execution_failed` if that recording throws. It does not edit the RTL fixture,
case selector, or the run-aware request geometry. The existing A4W4 D16
producer-corpus build has `GGML_GEMMINI_RESIDUAL_METRICS:STRING=0` and
`CYCLE_SIM:STRING=0` in `CMakeCache.txt`; its `rmd-executor.cpp` compile commands
also contain `-DGGML_GEMMINI_RESIDUAL_METRICS=0`. The added block therefore
compiles out in that evidenced producer build. This build uses the live llama
checkout whose 13 reviewed producer bytes match the package; the standalone
package RTL host build does not compile this producer file. A metrics-enabled
producer build requires its own review.

All ten v3 fixture file hashes and the Gemmini selector hash still match the
immutable package/current workspace. Version 4 inherits the v3 fixed input
set unchanged: 15 captured identities plus synthetic K32, K64, K96, K3072,
and K8256 per profile across six profiles and two framings (240 comparisons).
The 27 moved historical residual IDs remain explicitly recorded, not silently
dropped. A fresh passive capture must still observe exactly 15 fixture cases
per profile; v4 validation rejects a coordinated reduction of identities,
counts, and results.

## Package boundary

The v4 manifest is outside this already exported package. The updated
workspace validator can attest the package because it pins the package's
embedded v3 authority, full source manifest, dependency lock, and reviewed
producer/fixture/selector hashes. The package's own copied certificate code
still speaks v3; it is not a self-contained v4 runner. Exporting a new package
with a v4 file would change its source manifest and require a new package
authority decision rather than silently reusing this exact-package v4 pin.

Focused check, from `IM2P.sim`:

```sh
IM2P_V4_CANDIDATE_PACKAGE=/Users/zerogod/aisa-lab/build/im2p-gemmini/production-drained-sequence-20260923T064750Z/certificate/candidate-package-trace-on \
  python3 -B -m unittest sim.tests.cycle.test_corpus_authority_v4 -v
```
