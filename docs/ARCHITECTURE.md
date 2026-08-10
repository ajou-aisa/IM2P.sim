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

`IM2PCore`는 block-major `scale[b,j]` table과 `block_size`, `total_k`를
runtime control state로 먼저 받는다. 각 hardware execution의 `k_start`에서:

```text
b = floor(k_start / block_size)
selectedScale[column] = scaleTable[b][column]
```

를 계산하고 해당 vector를 execution 동안 고정한다. 한 hardware partial이 두
K-block을 가로지르면 실행을 거부한다.

Execution은 이전 column wavefront, VectorUnit, Accumulator commit이 모두 끝난
뒤에만 교체된다. 따라서 stagger된 column output 모두 같은 execution-latched
scale을 사용한다. 동일 K-block의 여러 hardware fragment는 같은 table row를
재선택하고, 정확한 block boundary에서 다음 row로 전환한다.

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
타일링한다. K progress와 block boundary에 따른 scale table selection은
`IM2PCore`의 runtime control이며 별도 core나 wrapper가 아니다. Core는 DMA,
scratchpad, global scheduler를 모델링하지 않는다.
