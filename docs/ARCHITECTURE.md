# Architecture

## 1. Top-level data path

```text
logical activation row
        ↓
InputSkew
        ↓
registered WS SystolicArray
        ↓
column-valid complete partial sums
        ↓
VectorUnit
        ↓
column-valid contributions
        ↓
Accumulator
```

`IM2PCore`는 데이터 경로를 연결하면서 다음 metadata를 정렬한다.

- logical output row offset
- Accumulator destination row
- optional scale sideband
- runtime `VectorOp`
- `accumulate` policy

## 2. PE and SystolicArray

### PE

각 PE는 stationary weight `B`, horizontal activation `A`, vertical partial `D`를 사용한다.

```text
C = D + A × B
```

`A`와 `C`는 동일한 `peLatency`만큼 register forwarding된다. 산술 함수는 입력 단계에서 조합적으로 계산되므로 `peLatency`를 늘리는 것만으로 multiplier/adder 내부가 pipeline되지는 않는다.

### InputSkew

PE hop latency가 `L`일 때 boundary delay는 다음과 같다.

```text
activation input row k : k × L
initial partial column j: j × L
```

PE `(k,j)`에서 activation과 partial token이 같은 cycle에 만나도록 한다.

### SystolicArray

`arrayDim × arrayDim` PE를 직접 연결한다.

- PE row `k`는 `B[k,*]`를 stationary weight로 보유한다.
- Activation은 오른쪽으로 이동한다.
- Partial은 아래로 이동한다.
- 마지막 PE 행의 각 column 출력이 complete partial sum이다.

Weight preload row는 `BoundedIndex#(arrayDim)`로 전달된다. Non-power-of-two array에서도 잘못된 bit pattern을 검출하기 위해 마지막 유효 index인 `arrayDim-1`과 비교한다.

## 3. SystolicEngine and output tracking

`SystolicEngine`은 `InputSkew`, `SystolicArray`, input/result FIFO, `ExecuteController`를 묶는다.

Column output은 서로 다른 cycle에 도착할 수 있으므로 sparse result를 사용한다.

```text
valids[column]
rowOffsets[column]
partialSums[column]
```

`rowOffsets[column]`은 해당 column에서 지금까지 발행된 row 수를 기반으로 현재 result의 logical output row를 나타낸다.

`ExecuteController`는 두 counter를 column별로 유지한다.

```text
issuedRows[column]
    array 밖으로 발행된 row 수

committedRows[column]
    Accumulator까지 writeback된 row 수
```

Commit은 이미 issue된 row를 넘을 수 없으며, 모든 column에서 `committedRows == rowCount`가 된 뒤에만 execution이 완료된다.

## 4. Column, vector lane, bank

```text
array column
    spatial output 위치

physical vector lane
    한 cycle의 transform 연산 경로

Accumulator bank
    한 output column의 state storage
```

Architectural index는 유지된다.

```text
array column index = VectorResult index = Accumulator bank index
```

`vectorLanes < arrayDim`이면 VectorUnit은 array result를 여러 group으로 처리하고, 각 group 결과를 원래 `arrayDim` 위치의 sparse vector로 복원한다.

## 5. VectorUnit

VectorUnit은 element마다 동일한 runtime operation을 적용한다.

```text
VectorBypass   : P
VectorMultiply : P × S
VectorShift    : shift(P, E)
```

`VectorScaleCapability#(format_t)`는 format의 scale 지원 여부를 나타내고, `VectorTransform#(format_t, acc_t, scale_t)`는 실제 transform을 정의한다.

- Signed INT: Bypass/Multiply/Shift
- FLOAT: Bypass behavior only

Accumulator 주소, 기존 state, `accumulate` 여부는 VectorUnit interface에 없다.

## 6. Destination address and Accumulator

Core는 valid column마다 다음 주소를 만든다.

```text
destinationRow[column]
    = accumulatorBaseRow + rowOffset[column]
```

Column은 이미 Accumulator bank를 선택하므로 주소는 row만 포함한다.

```text
bank 0 → C[*,0]
bank 1 → C[*,1]
...
```

Accumulator는 다음 연산과 state storage를 소유한다.

```text
accumulate=False
    bank[column][row] = contribution

accumulate=True
    bank[column][row]
        = bank[column][row] + contribution
```

현재 storage backend는 column별 `mkRegFileFull`이다.

## 7. Block scale selection and alignment

Host는 block-major `S[b,j]` matrix view를 소유하고 `block_size`, `total_k`,
`context` metadata를 제공한다. 각 hardware execution의 `k_start`에서 Core가:

```text
b = floor(k_start / block_size)
request tag = (context, b)
```

를 계산한다. Cache miss이면 host가 요청 row의 J tile slice를 응답한다. Core는
응답한 `S[b,column]` vector를 execution 동안 고정한다. 한 hardware partial이
두 K-block을 가로지르면 실행을 거부한다.

Execution은 이전 column wavefront, VectorUnit, Accumulator commit이 모두 끝난
뒤에만 교체된다. 따라서 mixed K-block column output은 발생하지 않는다.
Staggered column output 모두 execution 시작 시 latch한
`executionScaleRow[j] = S[b,j]`를 사용한다.

전체 `ceil(K/B) × J` matrix는 host memory가 소유한다. Core는
`kStart / blockSize`로 block을 선택하고 `(context, block)` tag의 row를
요청한다. RTL storage는 current/next row뿐이다. Current hit은 transfer 없이
reuse하고, next hit은 promote하며, miss는 demand response까지 execution을
보류한다. Current row가 준비되면 가능한 `b+1` row를 prefetch한다. Context
변경과 reset은 두 row를 무효화한다. Scale block 수에는 synthesis-time
capacity 제한이 없다.

SystolicArray partial은 VectorUnit으로 직접 전달된다. K-block partial을 먼저
재결합하는 stage는 없다.

## 8. Backpressure

VectorUnit이 busy이면 Core는 다음 array result를 받지 않는다. SystolicEngine result FIFO가 가득 차면 `advanceArray` rule이 멈추며, InputSkew와 모든 PE가 함께 정지한다. 따라서 wavefront 내부 상대 timing은 유지된다.

현재 in-flight 정책은 다음과 같다.

```text
SystolicEngine result FIFO : 여러 sparse result를 완충
VectorUnit                 : full-width result 1개
Accumulator                : group result를 cycle별 commit
```

## 9. Execution contract

현재 reference execution은 square array tile을 기준으로 한다.

```text
1 <= rowCount <= arrayDim
K extent = arrayDim
N extent = arrayDim
vectorLanes divides arrayDim
accRows >= arrayDim
```

작은 K/N은 0-padding하며, 큰 M/K/N은 상위 model이 여러 execution으로
타일링한다. K progress와 block boundary에 따른 scale row selection은
`IM2PCore`의 runtime control이며 별도 core나 wrapper가 아니다. Core는 DMA,
scratchpad, global scheduler를 모델링하지 않는다.

## 10. Address-driven scheduler stack

`IM2PCore` 내부에는 다음 RTL control module이 한 번씩만 존재한다.

```text
MatmulScheduler: M/N tile, async stripe publication, completion ordering
WorkScheduler:   K fragment, scale block, accumulate-first selection
IM2PCore:        tagged A/W/S/C channels, buffer/bank readiness, execution
```

Full-matrix mode는 descriptor의 전체 M/N 범위를 즉시 scheduling한다. Async
mode는 published stripe만 scheduling하며, 미공개 stripe 주소를 prefetch하지
않는다. Host publication 가능 여부와 RTL FIFO readiness는 별도 상태다.

각 host request는 address, element count, tag를 가진다. Provider는 address를
borrowed A/W/S/C view로 resolve하고 동일 tag로 응답한다. 최종 output row
acknowledgement가 scheduler의 work/stripe completion보다 먼저 완료되어야 한다.

External `start`, work completion, job acknowledgement는 pending register를 거쳐
내부 rule에서 state transition한다. 이 one-cycle barrier는 Verilator의
post-edge combinational reevaluation에서도 state register writer가 one-hot임을
보장한다.

Rust layer는 provider, clock advance, watchdog, counter snapshot만 담당한다.
Matrix/fragment/scale-block 선택은 RTL 외부에 복제하지 않는다.
