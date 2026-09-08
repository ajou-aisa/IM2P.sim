# Integer profile and memory contract

`config/im2p_profiles.json` is the configuration source. `scripts/im2p_config.py`
generates `src/common/Config.bsv`, `sim/ffi/im2p_config.h`, and Rust build constants.
`--check` rejects stale generated files. Production A/W precision must match.

| A/W | Product bits | PE partial / vector / accumulator bits |
| --- | ---: | ---: |
| 4/4 | 8 | 32 |
| 8/8 | 16 | 32 |
| 16/16 | 32 | 64 |

| DIM | A4/A8 rows | Row address bits | A16 rows | Row address bits | Integer capacity |
| --- | ---: | ---: | ---: | ---: | ---: |
| 16 | 1024 | 10 | 512 | 9 | 65536 bytes |
| 32 | 512 | 9 | 256 | 8 | 65536 bytes |
| 64 | 256 | 8 | 128 | 7 | 65536 bytes |

`rows * DIM * accumulator_bits == 65536 * 8`. Row counts use enough bits to
represent DIM itself. Base/count checks use a widened unsigned subtraction before
packing; the last row remains legal when count is one. The scheduler currently
uses one output tile at a time; full-capacity addressing does not imply a new
multi-tile residency schedule. FP16/FP32 arithmetic and their 256-row defaults are
unchanged. DIM64 retains concrete 16x16 tile synthesis/register boundaries.

Weight-bank reuse is scoped to one startMatmul job. Provider logical addresses may
name different weight data in the next job, so a new job invalidates both residency
metadata entries. The weight storage itself is not reset. Matching and reuse among
current/lookahead tiles inside the same job remain available.

## Arithmetic boundaries

For the selected signed W-bit profile:

- Operand multiplication produces the full A+W-bit signed product.
- Each PE product accumulation wraps modulo 2^W, interpreted as two's complement.
- VectorMultiply keeps the low W bits of the full signed product.
- Accumulator addition wraps modulo 2^W. Replace does not read old memory.
- VectorShift widens the signed exponent before taking its magnitude, including
  INT8_MIN. Right shift is arithmetic; magnitude >= W yields -1 or 0. Left shift
  retains low W bits; magnitude >= W yields zero. Amounts are not reduced modulo W.
- No intermediate rounding or saturation was added. The existing final raw
  signed32 output saturation is retained. A16 provider values can exceed INT32.

The public provider callback remains signed64. A4/A8 RTL lanes are sign-extended;
A16 uses two 32-bit transport words per lane. The unchanged transport ABI does not
restore the previous INT64 numerical result after an INT32 overflow. INT32 preload
rejects any out-of-range lane before writing any lane or firing the method.

VectorExternal requests block metadata but passes raw partials through the vector
unit. The scheduler resets accumulation at actual block boundaries and publishes
each block's output. Frontend `write_output` multiplies the signed64 raw value by
the decoded double weight factor and stored float activation scale, accumulates
blocks in double, casts to float, then merges into staged output. Residual float
merge is another boundary. CPU ExSIA/quantization algorithms are unchanged; dense
and residual still have independent simulator handles and clock domains.

## Reference and oracle

`scripts/numerical_reference.py` performs bit-accurate wrapping at each integer
operation, while recording pre-truncation extrema, overflow count, and first K/block
location for PE, vector and accumulator updates. Route/layer/stripe/row/column
metadata can accompany the input. Inputs must be signed integers in a canonical
operand profile. `diagnostic_width` explicitly selects a 32/64-bit comparison
reference; it does not define a production artifact. Bypass supports block size
zero, matching RTL. External integer-factor reconstruction in this script is
labelled diagnostic, rather than substituted for the real floating host path.

`scripts/host_reconstruction.py` mirrors the double/float32 output and residual
operations, and uses `Fraction` to track exact mathematical values and intermediate
overflow separately. Native C++ tests compare final float bits with unfused
operations. Finite floating output does not prove absence of rounding error.

Required counterexample: A=W=-128, K=8192, scale=16. Accumulating all K yields
mathematical 2^31 and wrapped INT32_MIN. VectorExternal with block size32 instead
publishes 256 raw blocks of 524288; host multiplication by16 reconstructs 2^31.
These routes have different reset/output semantics. A16's three products of
(-32768)*(-32768) total3221225472 and require the retained INT64 profile.

Intentional-overflow tests validate defined behavior, not model quality. Exact
comparison with the old INT64 reference applies only to the observed overflow-free
common input range. Dataset/model checks and their limits must be reported
separately; neither PPL agreement nor final-value range proves no intermediate
overflow. No CPU/INT64 fallback or implicit input-range restriction is introduced.

## Memory and cycle lifecycle

The backend is column-banked synchronous BRAM with one outstanding transaction.
Addresses, valids, contributions and accumulate policy are captured together.
Update/read acceptance at E is followed by writeback or read-response capture at E+1.
Completion/response is held until consumed; earliest consumption at E+2 permits the
next request at E+3. These edges describe the accumulator interface, not host C ACK.
Core first publishes output-valid after response capture; host acknowledgement may
arrive later. Preload writeRow instead completes on its acceptance edge E.
Stalls preserve all metadata and payload. Serialization prevents
stale same-bank/row RMW and undefined read-during-write collisions. Memory contents
are not cleared on reset; only control and pending state reset.

VectorUnit consumes only after request acceptance. Engine commit accounting follows
actual writeback. Output valid requires a captured BRAM response, and output payload,
address, count and tag remain stable through host acknowledgement. Work completes
after the final C acknowledgement. Low-level read has explicit request, pure getter
and consume APIs; its compatibility wrapper runs a transaction and counts its clocks.

Current and lookahead block initialization are independent. A nontrivial quotient
uses32 restoring-division RTL steps; state transfer is another real cycle. Fragment
progress then uses stored block index/remaining values. Prefetch uses a stored33-bit
exclusive block end. Host addresses, contexts, tags, timestamps and cycle counters
retain their original widths. `progress_stream(N)=N`, getter/eval=0, reset edges
excluded, and work interval endpoint equality remain the clock contracts.

## Reproduction

Use a fresh BUILD_DIR to preserve previous experiments:

```sh
make check cache-contract-test BUILD_DIR=build/int32-validation-new
make bsv-test BUILD_DIR=build/int32-validation-new
make sim-test-a8-w8-d16 BUILD_DIR=build/int32-validation-new
python3 scripts/im2p_config.py --profile 8 8 16
python3 scripts/numerical_reference.py --self-test
python3 tests/test_host_reconstruction.py
scripts/fpga_build.sh --mode rtl --bits 8 --dim 16
```

Manifest schema4 requires the full profile and memory contract, semantic revision,
source/generated configuration fingerprints and toolchain primitive hashes.
Missing fields in old manifests are rejected. Clock constraints and synthesis
variants belong to the independent FPGA run fingerprint; see `FPGA_FLOW.md`.
