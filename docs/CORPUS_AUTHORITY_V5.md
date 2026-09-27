# Isolated GEMM package authority v5

V5 attests one immutable package within the base240 source scope. It inherits
the complete v4 input authority and changes only the recorded package identity.
V1 through v4 and their SHA-256 pins remain unchanged. Select v5 explicitly with
`--corpus-revision v5`; the fresh-run default remains v4. Verified historical
reaggregation keeps its existing route and does not accept the v5 selector.

| Bound input | SHA-256 or base HEAD |
| --- | --- |
| V5 authority | `512de77742562bea32103caaca185b0c4a9c6c714965dd1def8d9cdf20107e14` |
| Inherited v4 authority | `1d260ac7ff24ead5796fba52f9b47b7fe7f983dfed2650b706c67bb775b10dbf` |
| Producer base HEAD | `cbe3530468c1e3d50aca3b4b3bd817fc6ea72158` |
| Dependency lock | `42fde25a10dd6f861deb6fa7b9be60f9e47b59ee466c170b965fc1476119a034` |
| Full source manifest | `8a85d43fe694b12d46bd1d7588c00dc115c41950b17555493a8f7affd79a8da8` |
| Embedded v3 authority | `7add3fd188167e11e047f36647e0a28ea567ef8a7dcc3f6c28971c58bac5bb18` |

The independently audited package is the `todo22-export-current-20260926T162102Z`
export consumed by `todo22-host-test-current-20260926T164900Z`. Its ten fixture
files, thirteen producer files, and one live Gemmini selector match the v4
source pins byte-for-byte. The v4 metrics guard remains inherited with its
original constraint; v5 introduces no producer, numerical, tiling, or timing
change. V5 checks actual package fixture bytes as well as the package manifest,
producer, current fixture and selector bytes. Each verified RTL build must name
the exact admitted package identity.

The unchanged inputs are 15 captured cases plus K32/K64/K96/K3072/K8256 for each
of six profiles and two framings: exactly 240 comparisons. All shapes, tiles,
timing inputs, provenance and the 27 moved historical residual IDs are inherited.
No measured results define this set. Wrong package identities, changed source,
changed geometry and coordinated denominator reductions remain rejected.

This is an external authority: the consumed package is immutable and still
embeds v3. It is not a self-contained v5 runner. Whole-package freshness is
explicitly excluded: two stateful certificate Python files were already stale
at the host audit, and later provider edits also lie outside the audited
host/base dependency graph. A newly exported package requires a separately
reviewed identity; it cannot silently replace this package. The exporter must
include `corpus_package.py`, `corpus-authority-v5.json`, and `current_corpus.py`
when shipping this reader revision.

From `IM2P.sim`, with the immutable host matrix and actual shared library selected:

```sh
python3 -B sim/tests/cycle/current_rtl_certificate.py --help
python3 -B sim/tests/cycle/current_rtl_certificate.py \
  --build-root "$IM2P_V5_BUILD_ROOT" --library "$IM2P_CYCLE_LIBRARY" \
  --out "$IM2P_V5_PREFLIGHT_OUT" --corpus-revision v5 --preflight
```

Preflight runs the real package gate and all six `verify_build` checks. It writes
`corpus-preflight.json` with `PREFLIGHT_ONLY`, 240 planned and zero attempted
cases; it publishes no certificate and makes no RTL/model exactness claim.
After independent source review, omit `--preflight` and use a fresh external
output directory for the official passive captures and 240 RTL/model comparisons.
Those measurements, event mutation gates, and final strict admission are still
required for a current certificate. Validator-only changes do not require an
HDL rebuild when all actual host/RTL source and artifact bindings still verify.
