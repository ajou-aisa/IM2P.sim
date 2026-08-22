# IM2P.sim 검증

## 1. BSV testbench

| Testbench | 검증 대상 |
|---|---|
| `TbArithmetic` | INT operand/product/accumulator width, FP arithmetic |
| `TbPE` | stationary weight와 registered A/C forwarding |
| `TbInputSkew` | `boundaryIndex × peLatency` delay |
| `TbSystolicArray` | 2×2 conventional WS matrix multiplication |
| `TbVectorUnit` | 동일 INT unit의 Bypass/Multiply/Shift, sparse Valid, empty group, grouped physical-lane 처리 |
| `TbAccumulator` | per-column row address, valid mask, replace/add semantics |
| `TbExecuteController` | staggered issue/commit tracking과 Done 조건 |
| `TbWorkScheduler` | K fragment/block 경계, accumulation, prepared lookahead promotion |
| `TbMatmulScheduler` | full/async extent, bounded publication queue, ordering |
| `TbMatmulLookahead` | current + immediate lookahead visibility와 deeper FIFO progression |
| `TbSystolicArrayWeightBanks` | dual stationary-weight bank load/switch |
| `TbSystolicEngineWeightBanks` | active execution 중 inactive-bank preload |
| `TbIM2PCore` | non-zero base row, `vectorLanes < arrayDim`, 세 runtime operation |
| `TbIM2PCoreActivationBuffer` | current/next K-fragment activation slot 격리 |
| `TbIM2PCoreMatrix` | tagged A/W/C host traffic와 output acknowledgement |
| `TbIM2PCoreMatrixScale` | tagged S demand/reuse/prefetch와 immutable snapshot |
| `TbIM2PCoreOutputAddressing` | signed-64 provider lane과 raw/V2 signed-32 byte addressing 경계 |
| `TbIM2PLookahead` | publish-triggered cross-stripe A/W preparation과 reuse |
| `TbIM2PLookaheadScale` | nonresident lookahead W/S miss timing |
| `TbIM2PCoreGrouped` | 4-column/2-lane routing과 scale alignment |
| `TbFloatCore` | 동일 Core source의 FLOAT Bypass execution |
| `TbSynthInt8x16` | DIM16 synthesis wrapper smoke |
| `TbSynthInt8x32` | DIM32 synthesis wrapper smoke |
| `TbSynthInt8x64` | DIM64 4×4 tile hierarchy와 15/16, 31/32, 47/48 seam smoke |

`tests/TestVectorUtils.bsv`는 testbench에서 공유하는 고정 길이 Vector 생성 helper이며 synthesis top이 아니다.

## 2. BSC 없이 수행하는 검사

```bash
make check
```

다음 항목을 검사한다.

- exact source/test/synth tree
- package/file 이름 일치
- unresolved internal import와 dependency cycle
- module body 내부 `typedef`
- 존재하지 않는 `vec(...)` constructor
- legacy architecture symbol 재유입
- VectorUnit/Accumulator 책임 경계
- scale capability/transform 분리
- Core의 필수 proviso와 routing state
- BoundedIndex weight-row interface
- commit-before-issue 불변조건
- C++20 reference self-test
- frontend mode가 `FULL`/`PIPELINE` 두 개뿐인지 검사
- signed-64 Accumulator/provider transport와 canonical callback 검사
- raw output final signed-32 saturation 경계 검사
- non-RMD matched A4/Q4·A16/Q16 FULL/PIPELINE, production A8/Q8 ExSIA 및 RMD TODO 계약 검사

## 3. 전체 BSC/RTL 검증

```bash
make clean
make verify
make verilator-lint
make yosys-stat
```

`make verify`는 다음 순서로 실행된다.

```text
make check
make bsv-test
make rtl
```

실패한 지점만 개별적으로 다시 실행할 수 있다.

```bash
make bsv-test-one TOP=mkTbAccumulator
make bsv-test-one TOP=mkTbIM2PCore
make rtl-one TOP=mkSynthInt8
```

## 4. Verilated RTL integration test

Cargo는 `sim/tests/*.rs`를 자동으로 발견한다. 공통 CPU golden, scale matrix, fragment generator는 `sim/tests/common/`에 있다.

```bash
make sim-test-int8x16
make sim-test-int8x32
make sim-test-int8x64
```

각 target은 해당 DIM의 Verilog와 Verilated model을 다시 생성한 뒤 전체 test binary를 실행한다. 검증 범위는 다음과 같다.

- Bypass 부호/zero/full/tail
- Column별 Multiply/Shift
- B8/B16/B32/B64
- 9/17/128 K-scale block
- K4096/B32
- current reuse, next prefetch, demand miss
- context/reset과 J stride/offset
- runtime mode 전환
- 결정론적 random arithmetic
- validation과 tile-local statistics
- async output-tile column offset
- publish-triggered A/W/S lookahead, resident reuse, partial weight 준비

파일별 책임과 신규 test 작성 예는 [simulator test 가이드](../sim/tests/README.md)에 있다.

## Scheduler와 host provider 검증 범위

Bluesim:

- `TbMatmulScheduler`: full/stripe extent, queue backpressure, ordering
- `TbWorkScheduler`: K fragment, block boundary, accumulation
- `TbIM2PCoreMatrix`: 지연된 tagged A/W response와 output acknowledgement
- `TbIM2PCoreMatrixScale`: 지연된 S response, reuse/prefetch/snapshot
- activation/weight bank testbench: current/next slot safety

Cargo auto-discovered integration tests:

- `rtl_full_matmul`: oversized/tail/stride/golden/low-level 동등성
- `rtl_memory_provider`: address 기반 non-contiguous A/W/C view
- `rtl_work_scheduler`: 실제 RTL model을 통한 multi-I/J/K scheduling
- `rtl_async_stripes`, `rtl_stripe_completion`: publication gating, finite backpressure, 결정론적 RTL logical cycle, completion ordering
- `rtl_async_output_tiles`: async N-tile output column offset
- `rtl_stripe_lookahead`: publish-triggered A/W/S preparation, delayed publish, padded layout, partial weight preparation, resident resource reuse
- `rtl_weight_preload`, `rtl_work_stats`: dual-bank overlap과 RTL counter
- `rtl_cycle_accounting`: reset=0, N ticks=N, pulse=1, eval-only=0, C++ counter/positive-edge equality, concurrent A/W/S/C response edge
- `rtl_writeback`: prefix/tail/row-gutter guard 보존
- `c_api_smoke.c`: blocking/cooperative C ABI, zero-budget 관찰, scheduler state별 정확한 `progress_stream(..., 1)` cycle delta

`make sim-test-int8x16`, `make sim-test-int8x32`, `make sim-test-int8x64`는 각 DIM의 모든 Cargo integration binary를 실행한다. `make c-api-test`는 strict C11 header compile, static library link, 실제 C driver 실행까지 수행한다.

## ExSIA frontend lifecycle 및 sanitizer

Production ExSIA는 A8/Q8을 지원하며 public frontend state는 다음 두 개다.

| mode | 검증할 lifecycle |
|---|---|
| `FULL` | post-fold event 수집, quantization 성공 뒤 NPU 시작, fence -> 8-bit cpu-direct RMD -> caller output publish |
| `PIPELINE` | NPU 선시작, 각 folding commit 직후 post-fold immediate publication, producer/worker overlap, fence -> 8-bit cpu-direct RMD -> caller output publish |

`PIPELINE`은 quantization 전체가 끝난 뒤 stripe를 batch publish하지 않는다.
Non-RMD A4/Q4와 A16/Q16도 canonical typed stripe provider로 같은 native stream
lifecycle을 사용한다. Matched ExSIA RMD scale integration, Q8 H2/HP2와
mixed precision은 worker 시작, deferred queue, fallback 없이 거부한다.

Frontend lifecycle은 다음 target으로 검증한다.

```bash
make gemmini-frontend-test
make gemmini-frontend-test-sanitized
make gemmini-frontend-tsan-test
```

ASan+UBSan target은 ownership, producer/worker, 실패 및 teardown lifecycle을 실행한다. TSan target은 producer/worker 동시 lifecycle을 실행한다. Host/toolchain이 TSan runtime을 지원하지 않으면 compiler/runtime의 정확한 진단을 기록하고, mutex/condition-variable lifecycle 계약 테스트와 ASan+UBSan 전체 lifecycle 결과를 대체 race evidence로 함께 남긴다.

## Signed output width 및 ABI 경계

Production integer RTL의 partial, Accumulator, bridge output request와 Rust provider service는 signed 64-bit다. 단일 canonical ABI의 provider callback은 signed-64 lane을 그대로 받는다. Raw output은 signed 32-bit이며 최종 write 경계에서만 saturation한다. Stripe completion이나 quantization/RMD staging 전에는 narrowing하지 않는다.

W8 frontend artifact는 DIM16/DIM32에서 block size 32, DIM64에서 64를
사용한다. Matched W4/W16 artifact는 GGUF block layout 때문에 모든 DIM에서
32를 사용한다. Llama configure도 `a<activation>-w<weight>-d<dim>` identity와
같은 mapping을 요구한다.

관련 검증은 다음과 같다.

```bash
make c-api-layout-test
make c-api-test IM2P_ACTIVATION_BITS=8 IM2P_DIM=16
make gemmini-frontend-real-test IM2P_ACTIVATION_BITS=4 IM2P_WEIGHT_BITS=4 IM2P_DIM=16
make gemmini-frontend-real-test IM2P_ACTIVATION_BITS=16 IM2P_WEIGHT_BITS=16 IM2P_DIM=16
cargo test --manifest-path sim/Cargo.toml
make bsv-test-one TOP=mkTbIM2PCoreOutputAddressing
```

`c-api-layout-test`는 단일 canonical ABI의 FULL/PIPELINE activation/weight
identity 및 typed i8/i16 callback을 확인한다. Rust/RTL test는 signed-32
범위를 넘는 누산이 provider까지 보존되고 raw output 최종 경계에서만
saturation하는지 확인한다.

Architecture의 기준은 [architecture 문서](ARCHITECTURE.md)에 있으며, 코드 분석 순서는 [코드 분석 가이드](CODE_ANALYSIS_GUIDE.md)를 따른다.
