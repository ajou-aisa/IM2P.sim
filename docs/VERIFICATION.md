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

## 4. 확인된 외부 BSC 로그

사용자가 제공한 BSC/Bluesim 로그에서는 다음 단계가 실제 PASS했다.

```text
TbArithmetic
TbPE
TbInputSkew
TbSystolicArray
TbVectorUnit
```

그 뒤 보고된 오류를 기준으로 다음을 수정했다.

- `TbAccumulator`의 Vector 초기화
- `ControllerState` package-scope 이동
- `VectorScaleCapability` 분리
- Core의 `accRows`, count/index, scale capability proviso
- weight-row index 타입과 범위 비교

현재 정리된 전체 revision은 BSC가 설치된 개발 머신에서 `make clean && make bsv-test && make rtl`로 다시 확인해야 한다.

## 5. 이번 환경에서 실행한 검사

- architecture static checker
- C++20 reference build with `-Wall -Wextra -Wpedantic -Werror`
- reference self-test
- AddressSanitizer/UndefinedBehaviorSanitizer
- Python checker syntax

BSC, Verilator, Yosys는 이 환경에 설치되어 있지 않아 직접 실행하지 않았다.

## 6. Synthesis에서 확인할 항목

- FLOAT specialization 후 scale multiplier/shifter 제거 여부
- FP `multFP`/`addFP` 조합 critical path
- `scaleSidebandRows` dynamic mux/register cost
- `mkRegFileFull`의 실제 inference 결과
- `vectorLanes < arrayDim` group mux와 throughput
- dynamic assertions가 synthesis flow에서 처리되는 방식
