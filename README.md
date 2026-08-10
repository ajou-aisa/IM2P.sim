# IM2P.sim

Bluespec으로 작성한 **registered weight-stationary systolic NPU RTL 모델**이다. Gemmini의 WS 실행 방식을 참고하지만 `Tile`, `Mesh`, `MeshWithDelays`, DMA, RoCC, ROB 같은 Gemmini generator/SoC 계층은 복제하지 않는다.

```text
InputSkew
    ↓
SystolicArray
    ↓  bottom-row PE의 column별 complete partial sum
VectorUnit
    ↓  runtime-selected contribution
Accumulator
```

## 설계 원칙

### 단일 Core

최상위는 `src/core/IM2PCore.bsv` 하나다.

- format과 precision은 synthesis-time type parameter다.
- INT에서는 `VectorBypass`, `VectorMultiply`, `VectorShift`를 runtime에 선택한다.
- FLOAT는 같은 Core source와 datapath를 사용하지만 transform policy는 Bypass만 제공한다.
- INT의 scale 적용 여부나 Multiply/Shift 선택 때문에 RTL을 다시 합성하지 않는다.

FLOAT instance에 실제 scale multiply/shift 구현은 없지만, 최종 generated RTL에서 관련 연산기가 제거되는지는 `make rtl`과 synthesis report로 확인한다.

### 전형적인 WS SystolicArray

각 PE는 stationary weight를 보유한다. Activation은 오른쪽으로, partial sum은 아래로 이동한다.

```text
A → PE → PE → ...
    ↓    ↓
D → C    C
```

현재 square array 한 execution의 기본 계산 범위는 다음과 같다.

```text
K extent = arrayDim
N extent = arrayDim
M extent = rowCount, 1 <= rowCount <= arrayDim
```

실제 K/N이 `arrayDim`보다 작으면 상위 model이 남는 activation/weight element를 0으로 채운다. 더 큰 GEMM은 상위 scheduler가 여러 execution으로 타일링한다.

### Column, physical vector lane, Accumulator bank

세 용어는 구분한다.

```text
array column
    마지막 PE 행에서 complete partial sum이 나오는 공간적 output 위치

physical vector lane
    한 cycle에 실제 Bypass/Multiply/Shift를 수행하는 연산 경로

Accumulator bank
    한 output column의 모든 logical row를 저장하는 state bank
```

Architectural index는 다음처럼 유지된다.

```text
SystolicArray output column index
= VectorResult sparse index
= Accumulator bank index
```

`vectorLanes < arrayDim`이면 physical vector lane 집합이 array column을 여러 group으로 나누어 처리한다. 현재 구현은 `vectorLanes`가 `arrayDim`의 약수인 구성만 허용한다.

### VectorUnit은 값 변환만 담당

```text
partial + runtime VectorOp + scale
                 ↓
             contribution
```

`VectorUnit`은 다음 정보를 해석하지 않는다.

- Accumulator row address
- 기존 accumulator 값
- `accumulate` 여부
- Accumulator storage

INT reference operation은 다음과 같다.

```text
VectorBypass
    contribution = partial

VectorMultiply
    contribution = partial × signed scale coefficient

VectorShift
    scale >= 0 : contribution = partial << scale
    scale <  0 : contribution = partial >> |scale|
```

현재 integer policy는 two's-complement wrap, arithmetic right shift, rounding 없음, saturation 없음이다.

### Accumulator는 주소와 상태를 담당

Core가 각 valid column의 destination row를 만든다.

```text
destinationRow[column]
    = accumulatorBaseRow + logicalRowOffset[column]
```

Accumulator에서 column index는 bank를, destination row는 해당 bank 내부 위치를 선택한다.

```text
accumulate=False
    bank[column][row] = contribution

accumulate=True
    bank[column][row] = bank[column][row] + contribution
```

Column별 `RegFile`은 `Accumulator` 내부 backend다. 별도 범용 `BankedVectorMem` package는 두지 않는다. `accumulate=True`를 사용하기 전에 대상 accumulator row는 유효한 값으로 초기화되어 있어야 한다.

### Block은 별도 core가 아니라 runtime control

Array와 VectorUnit, Accumulator에는 block index나 block scheduler가 없다. Block metadata는 `IM2PCore`의 execution control state다.

- 일반 GEMM execution: `VectorBypass`
- coefficient scaling execution: `VectorMultiply`
- power-of-two scaling execution: `VectorShift`

Block-scale workload는 scale이 필요한 execution에만 operation과 scale table을 공급한다. 여러 array execution에 걸친 partial을 VectorUnit 앞에서 재결합하는 구조는 없다. 따라서 하나의 scale을 전체 K 범위에 한 번만 적용해야 하는 경우에는 상위 scheduling이 해당 K 범위와 execution 경계를 맞춰야 한다.

### Partial sum은 도착 즉시 처리

Column output은 systolic timing 때문에 서로 다른 cycle에 도착할 수 있다. `SystolicEngine`은 모든 column을 deskew해 기다리지 않고 현재 Valid인 column을 sparse result로 전달한다.

```text
valids[column]
rowOffsets[column]
partialSums[column]
```

작은 result FIFO는 backpressure를 흡수할 뿐이며 partial 재결합 storage가 아니다. FIFO가 가득 차면 InputSkew와 모든 PE의 `step`을 함께 정지해 wavefront의 상대 timing을 보존한다.

### Scale 선택은 Core runtime control

Multiply/Shift execution에서는 block-major scale table과 block metadata를 먼저 설정한다.

```bsv
configureScaling(blockSize, totalK, blockCount)
loadScaleBlock(columnScales)
startExecution(command, kStart, kCount)
```

Core는 `b = kStart / blockSize`로 scale row를 선택하고 execution drain이 끝날 때까지 고정한다. Bypass에서는 이 configuration이 필요 없고, 남아 있는 table 값도 결과에 영향을 주지 않는다.

```bsv
startExecution(bypassCommand, kStart, kCount)
putActivationRow(activations)
```

`scaleTable`과 `executionScalesReg`는 architectural scale SRAM이 아니라 현재 execution을 위한 control state다.

## Source tree

```text
src/
├── common/
│   ├── Config.bsv
│   ├── Types.bsv
│   └── Arithmetic.bsv
├── array/
│   ├── PE.bsv
│   ├── InputSkew.bsv
│   ├── SystolicArray.bsv
│   └── SystolicEngine.bsv
├── vector/
│   ├── Scale.bsv
│   └── VectorUnit.bsv
├── accumulator/
│   └── Accumulator.bsv
├── control/
│   ├── ExecuteCmd.bsv
│   └── ExecuteController.bsv
└── core/
    └── IM2PCore.bsv
```

`tests/TestVectorUtils.bsv`는 BSV에 존재하지 않는 `vec(...)` literal 대신 테스트에서 사용하는 2/3/4-element Vector helper만 제공한다. RTL source에는 포함되지 않는다.

## External boundary

DMA는 모델링하지 않는다. Testbench 또는 상위 SoC model이 다음 인터페이스를 사용한다.

- stationary weight row preload
- execution 시작
- activation row와 optional scale sideband 공급
- accumulator row 초기화
- accumulator row 읽기

현재 weight preload와 host accumulator access는 기능 검증용 합성 가능 boundary이며, scratchpad/DMA latency나 메모리 대역폭 모델은 아니다.

## Build

```bash
make check
make bsv-test
make rtl
make verilator-lint
make yosys-stat
```

한 번에 BSC 검증과 대표 RTL 생성을 수행하려면 다음을 사용한다.

```bash
make verify
```

개별 항목만 다시 실행할 수도 있다.

```bash
make bsv-test-one TOP=mkTbIM2PCore
make rtl-one TOP=mkSynthInt8
```

`make check`는 BSC 없이 architecture 정적 검사와 C++20 reference self-test를 수행한다.

## 문서

- `PROJECT_REVIEW.md`: 전체 검토 결과, 수정 사항, 남은 리스크
- `docs/ARCHITECTURE.md`: 데이터·주소·제어·backpressure 흐름
- `docs/VERIFICATION.md`: testbench 범위와 검증 상태
- `VALIDATION_REPORT.txt`: 이번 산출물에서 실제 실행한 검사
