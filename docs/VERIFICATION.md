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
| `TbIM2PLookahead` | publish-triggered cross-stripe A/W preparation과 reuse |
| `TbIM2PLookaheadScale` | nonresident lookahead W/S miss timing |
| `TbIM2PCoreGrouped` | 4-column/2-lane routing과 scale alignment |
| `TbFloatCore` | 동일 Core source의 FLOAT Bypass execution |
| `TbSynthInt8x16` | DIM16 synthesis wrapper smoke |
| `TbSynthInt8x32` | DIM32 synthesis wrapper smoke |

`tests/TestVectorUtils.bsv`는 testbench가 공유하는 고정 길이 Vector 생성 helper이며 synthesis top이 아니다.

## 2. BSC 없이 수행하는 검사

```bash
make check
```

다음을 수행한다.

- exact source/test/synth tree 검사
- package/file 이름 일치 검사
- unresolved internal import와 dependency cycle 검사
- module body 내부 `typedef` 검사
- 존재하지 않는 `vec(...)` constructor 검사
- legacy architecture symbol 재유입 검사
- VectorUnit/Accumulator 책임 경계 검사
- scale capability/transform 분리 검사
- Core의 필수 proviso와 routing state 검사
- BoundedIndex weight-row interface 검사
- commit-before-issue 불변조건 검사
- C++20 reference self-test

## 3. 전체 BSC/RTL 검증

```bash
make clean
make verify
make verilator-lint
make yosys-stat
```

`make verify`는 다음을 수행한다.

```text
make check
make bsv-test
make rtl
```

개별 실패 지점만 재실행할 수 있다.

```bash
make bsv-test-one TOP=mkTbAccumulator
make bsv-test-one TOP=mkTbIM2PCore
make rtl-one TOP=mkSynthInt8
```

## 4. Verilated RTL integration test

`sim/tests/*.rs`는 Cargo가 자동 발견한다. 공통 CPU golden, scale matrix,
fragment generator는 `sim/tests/common/`에 있다.

```bash
make sim-test-int8x16
make sim-test-int8x32
```

각 target은 해당 DIM의 Verilog와 Verilated model을 다시 생성한 뒤 전체 test
binary를 실행한다. Coverage는 다음을 포함한다.

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

파일별 책임과 신규 test 작성 예는
[simulator test 가이드](../sim/tests/README.md)에 있다.

## Scheduler와 host provider coverage

Bluesim:

- `TbMatmulScheduler`: full/stripe extent, queue backpressure, ordering 검증
- `TbWorkScheduler`: K fragment, block boundary, accumulation 검증
- `TbIM2PCoreMatrix`: 지연된 tagged A/W response와 output acknowledgement
- `TbIM2PCoreMatrixScale`: 지연된 S response, reuse/prefetch/snapshot
- activation/weight bank testbench: current/next slot safety

Cargo auto-discovered integration tests:

- `rtl_full_matmul`: oversized/tail/stride/golden/low-level 동등성
- `rtl_memory_provider`: address 기반 non-contiguous A/W/C view
- `rtl_work_scheduler`: 실제 RTL model을 통한 multi-I/J/K scheduling
- `rtl_async_stripes`, `rtl_stripe_completion`: publication gating,
  finite backpressure, 결정론적 RTL logical cycle, completion ordering
- `rtl_async_output_tiles`: async N-tile output column offset
- `rtl_stripe_lookahead`: publish-triggered A/W/S preparation, delayed publish,
  padded layout, partial weight preparation, resident resource reuse 검증
- `rtl_weight_preload`, `rtl_work_stats`: dual-bank overlap과 RTL counter
- `rtl_cycle_accounting`: reset=0, N ticks=N, pulse=1, eval-only=0, C++
  counter/positive-edge equality, concurrent A/W/S/C response edge
- `rtl_writeback`: prefix/tail/row-gutter guard 보존
- `c_api_smoke.c`: blocking/cooperative C ABI, zero-budget 관찰,
  scheduler state별 정확한 `progress_stream(..., 1)` cycle delta

`make sim-test-int8x16`과 `make sim-test-int8x32`가 각 DIM의 모든 Cargo
integration binary를 실행한다. `make c-api-test`는 strict C11 header compile,
static library link, 실제 C driver 실행까지 수행한다.

Architecture 기준은 [architecture 문서](ARCHITECTURE.md), 코드 분석 순서는
[코드 분석 가이드](CODE_ANALYSIS_GUIDE.md)를 따른다.
