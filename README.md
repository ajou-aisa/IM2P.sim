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

Block-scale workload는 scale이 필요한 execution에만 operation과 host-owned
scale matrix view를 공급한다. 여러 array execution에 걸친 partial을
VectorUnit 앞에서 재결합하는 구조는 없다. 각 partial은 자신이 속한
K-block과 J-column의 `S[b,j]`로 변환된 뒤 Accumulator에 반영된다.

### Partial sum은 도착 즉시 처리

Column output은 systolic timing 때문에 서로 다른 cycle에 도착할 수 있다. `SystolicEngine`은 모든 column을 deskew해 기다리지 않고 현재 Valid인 column을 sparse result로 전달한다.

```text
valids[column]
rowOffsets[column]
partialSums[column]
```

작은 result FIFO는 backpressure를 흡수할 뿐이며 partial 재결합 storage가 아니다. FIFO가 가득 차면 InputSkew와 모든 PE의 `step`을 함께 정지해 wavefront의 상대 timing을 보존한다.

### Scale 선택은 Core runtime control

Scale matrix 형상은 `ceil(K / B) × J`다. 전체 matrix는 host memory에 있고,
RTL에는 current/next row만 존재한다. Multiply/Shift execution에서는
metadata와 context를 설정한 뒤 RTL이 필요한 row를 요청한다.

```bsv
configureScaling(blockSize, totalK, context)
startExecution(command, kStart, kCount)
scaleRequestContext
scaleRequestBlock
scaleRequestKind
putScaleRow(context, block, columnScales)
```

Core는 `b = kStart / blockSize`를 계산한다. current hit이면 transfer 없이
reuse하고, next hit이면 promote한다. miss이면 demand request를 발생시키고
response 전까지 execution을 보류한다. current row가 준비되면 마지막 block이
아닌 경우 `b+1`을 prefetch한다.

```bsv
startExecution(bypassCommand, kStart, kCount)
putActivationRow(activations)
```

Execution 시작 시 selected row를 `executionScaleRow`로 고정한다. 현재
architecture는 이전 execution의 모든 column commit이 끝나기 전 다음
execution을 시작하지 않으므로 mixed-block column output이 없다. staggered
wavefront의 column `j`는 고정된 row의 `S[b,j]`를 사용한다. Prefetch response는
현재 execution snapshot을 덮어쓰지 않는다.

Scale row 수에는 synthesis-time 제한이 없다. Row는 host view의
`block * row_stride + column_offset`에서 on demand로 읽고 DIM까지 zero
padding한다. Context가 바뀌면 current/next cache를 무효화한다. Bypass는
request나 matrix 없이 실행하며 cache를 변경하지 않는다.

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
기본 build는 llama.cpp-gemmini header를 요구하지 않는다. 선택형 Gemmini C++
frontend와 실제 RTL golden은 각각 `make gemmini-frontend-test`,
`make gemmini-frontend-real-test`로 검증하며 계약과 lifetime은
`frontend/README.md`에 문서화되어 있다.

## 문서

- `PROJECT_REVIEW.md`: 전체 검토 결과, 수정 사항, 남은 리스크
- `docs/ARCHITECTURE.md`: 데이터·주소·제어·backpressure 흐름
- `docs/VERIFICATION.md`: testbench 범위와 검증 상태
- `VALIDATION_REPORT.txt`: 이번 산출물에서 실제 실행한 검사

## Address-driven full matrix와 stripe scheduling

`IM2PCore` 하나가 `MatmulScheduler`와 `WorkScheduler`를 각각 하나씩
소유한다. 두 scheduler가 M/N tile, K fragment, scale block을 결정하고 A/W/S/C
주소와 tag를 발행한다. Rust는 해당 주소를 host-owned view에 resolve하고
response를 돌려주며, I/J/K scheduling을 수행하지 않는다.

- `execute_matmul`: 전체 matrix descriptor를 한 번 제출한다. A/W/S/C의 모든
  region이 처음부터 이용 가능할 뿐, striped mode와 다른 실행 경로를 만들지
  않는다.
- `begin_striped_matmul`: 정적 W/S/C metadata를 제출하고 activation stripe를
  cooperative하게 publish한다.
- `publish_stripe`: 단순 queue 삽입이 아니라 activation availability event다.
  승인된 publish는 current WS/RC work가 실행 중이어도 바로 다음 stripe의 A,
  W, 필요한 S request/staging과 weight-bank preload 또는 resident reuse 준비를
  시작할 수 있게 한다.
- `npu_ready`: RTL publication FIFO가 새 stripe를 받을 수 있는 상태다.
- `host_available`: publish된 host activation이 아직 stripe completion으로
  반환되지 않은 상태다.
- stripe completion: 마지막 C write response가 acknowledge된 뒤에만 발생한다.

```text
CPU: publish s0 ------------------------ publish s1 ------------------- own s1 A until completion
NPU: [current s0: WS engine + RC] ======>| prepare s1: A/W/S stage, reuse or inactive-bank preload |
                                         |<-- current engine remains the only executor ----------->|
                                         completion s0 -> promote s1 -> [WS engine + RC for s1]
FIFO: current --------------------------- lookahead -------------------- deeper published stripes (FIFO)
```

실행 순서는 하나의 engine으로 유지된다. 현재 stripe와 즉시 다음 stripe만
prepare state를 가질 수 있고, 더 깊은 published stripe는 FIFO 순서를 유지하며
prepare되지 않는다. Lookahead는 A/W/S와 inactive PE bank만 준비한다. output
write와 Accumulator state 갱신은 lookahead가 current로 promotion된 뒤에만
발생한다.

Weight stationary PE bank는 기존 두 개다. 현재 engine은 active bank를 읽고,
lookahead의 host W fetch는 PE 밖 external staging row에 저장된다. capture 시점에
final-current-work safety가 성립하고 정확히 resident로 일치하는 bank가 있으면
host fetch와 preload 없이 reuse한다. safety가 아직 아니거나 일치하지 않으면
scheduler의 final-current-work safety point에서만 staging row를 inactive bank에
preload한다. 일치 조건은 weight base, row stride, J start/count, K start/count
전체이며, 따라서 잘못된 bank reuse가 없다.
completion까지 일부 W row만 도착한 경우 promotion 뒤 받은 row를 inactive-bank
load에 직접 주입하고 아직 없는 row만 host에 요청하므로 중복 fetch가 없다.
Scaled lookahead는 current/next
scale cache의 matching `(context + J offset, block)` row를 reuse하고, 없으면
current scale traffic이 비어 있고 engine이 실행 중일 때 host S request를 낸다.
Promoted execution은 선택한 scale row를 immutable snapshot으로 latch하므로
뒤의 prefetch/response가 staggered column 결과를 바꾸지 못한다.

`WorkStats::cross_stripe_overlap_cycles`는 **current
engine execution이 active인 cycle에 next-stripe A/W/S fetch 또는 PE preload 중
하나라도 active인 cycle** 수다. 기존 `activation_`, `weight_`, `scale_overlap_cycles`
는 current work 내부 fragment 준비와 compute의 overlap이며 이 aggregate의 부분
counter가 아니다. 이 값과 모든 wait/timestamp는 RTL logical cycle이며 host wall-clock
시간은 포함하지 않는다. `stripe_host_wait_cycles`는 current stripe의 transition이
끝났지만 다음 published work가 없어 scheduler가 기다린 cycle이다. `activation_`,
`weight_`, `scale_`, `output_wait_cycles`는 해당 host channel response wait을,
`weight_preload_cycles`는 active execution 중 weight load를 나타낸다.
`lookahead_ready_cycle`은 first-fragment A/W/S staging과 필요한 PE bank
preload/reuse가 모두 완료된 cycle이다.

Lookahead timestamp는 matmul start 기준 RTL cycle number다.
`lookahead_publish_cycle`은 두 번째 stripe publication이 RTL에 accept된 cycle이다.
그 값에서
`lookahead_first_activation_cycle`, `lookahead_first_weight_cycle`,
`lookahead_weight_preload_cycle`, 또는 `lookahead_scale_cycle` 중 0이 아닌 가장
이른 값을 빼면 publish-to-first-prepare cycles를 얻는다.
`lookahead_start_cycle - current_stripe_completion_cycle`은
completion-to-next-start transition cycles다. `lookahead_weight_requests`는 host
W fetch 수이고 `lookahead_weight_reuse_hits`는 exact resident-bank reuse 수다;
scale의 host request/reuse는 `lookahead_scale_requests`와
`lookahead_scale_reuses`로 따로 보고한다.

두 API 모두 같은 core/datapath/scheduler stack을 사용한다. `execute_tile` loop,
OS thread, async runtime, sleep, wall-clock timing은 high-level scheduling에
사용하지 않는다. DMA, scratchpad, TLB, ROB, RoCC, 두 번째 core/datapath 또는
별도 Rust scheduler도 이 모델 범위가 아니다.
