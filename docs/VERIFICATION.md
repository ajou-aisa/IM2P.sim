# Verification

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
| `TbIM2PCore` | non-zero base row, `vectorLanes < arrayDim`, 세 runtime operation |
| `TbIM2PCoreGrouped` | 4-column/2-lane routing과 scale alignment |
| `TbFloatCore` | 동일 Core source의 FLOAT Bypass execution |

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

## 4. Verilated RTL integration tests

`sim/tests/*.rs`는 Cargo가 자동 발견한다. 공통 CPU golden, scale matrix,
fragment generator는 `sim/tests/common/`에 있다.

```bash
make sim-test-int8x16
make sim-test-int8x32
```

각 target은 해당 DIM의 Verilog와 Verilated model을 다시 생성한 뒤 전체 test
binary를 실행한다. Coverage는 다음을 포함한다.

- Bypass signed/zero/full/tail
- column-wise Multiply/Shift
- B8/B16/B32/B64
- 9/17/128 K-scale blocks
- K4096/B32
- current reuse, next prefetch, demand miss
- context/reset, J stride/offset
- runtime mode switching
- deterministic random arithmetic
- validation and tile-local statistics

파일별 책임과 신규 test 작성 예는 `sim/tests/README.md`에 있다.
