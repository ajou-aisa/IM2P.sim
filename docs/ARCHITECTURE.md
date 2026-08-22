# IM2P.sim architecture

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

`IM2PCore`는 데이터 경로를 연결하고 다음 메타데이터의 시점을 맞춘다.

- logical output row offset
- Accumulator destination row
- optional scale sideband
- runtime `VectorOp`
- `accumulate` policy

```text
IM2PCore
├── MatmulScheduler
├── WorkScheduler
├── SystolicEngine
│   ├── ExecuteController
│   ├── InputSkew
│   └── SystolicArray
│       └── PE array
├── VectorUnit
└── Accumulator
```

## 2. PE와 SystolicArray

### PE

각 PE는 stationary weight `B`, horizontal activation `A`, vertical partial `D`를 사용한다.

```text
C = D + A × B
```

`A`와 `C`는 동일한 `peLatency`만큼 레지스터를 거쳐 전달된다. 산술 함수는 입력 단계에서 조합 논리로 계산되므로 `peLatency`만 늘려도 multiplier/adder 내부가 pipeline되지는 않는다.

### InputSkew

PE hop latency가 `L`일 때 경계 지연은 다음과 같다.

```text
activation input row k : k × L
initial partial column j: j × L
```

PE `(k,j)`에서 activation과 partial token이 같은 cycle에 만나게 한다.

### SystolicArray

`arrayDim × arrayDim` PE를 직접 연결한다.

- PE row `k`는 `B[k,*]`를 stationary weight로 보유한다.
- Activation은 오른쪽으로 이동한다.
- Partial은 아래로 이동한다.
- 마지막 PE 행의 각 column 출력이 완성된 partial sum이다.

Weight preload row는 `BoundedIndex#(arrayDim)`로 전달된다. 2의 거듭제곱 크기가 아닌 array에서도 잘못된 bit pattern을 검출할 수 있도록 마지막 유효 index인 `arrayDim-1`과 비교한다.

DIM16/DIM32는 이 flat array를 그대로 사용한다. DIM64는 같은
`SystolicArray` 16×16 instance를 4×4로 배치하고 concrete instance마다
separate synthesis boundary를 유지한다. Tile east/south edge는 기존 마지막
PE의 registered output이므로 경계에서 register나 FIFO를 추가하지 않으며
weight load, dual-bank 전환, PE hop latency도 flat array와 같다.

## 3. SystolicEngine과 output tracking

`SystolicEngine`은 `InputSkew`, `SystolicArray`, input/result FIFO, `ExecuteController`를 묶는다.

Column output은 서로 다른 cycle에 도착할 수 있으므로 sparse result로 표현한다.

```text
valids[column]
rowOffsets[column]
partialSums[column]
```

`rowOffsets[column]`은 해당 column에서 지금까지 발행된 row 수를 기준으로 현재 result의 logical output row를 나타낸다.

`ExecuteController`는 두 counter를 column별로 유지한다.

```text
issuedRows[column]
    array 밖으로 발행된 row 수

committedRows[column]
    Accumulator까지 writeback된 row 수
```

Commit은 이미 issue된 row를 넘을 수 없다. 모든 column에서 `committedRows == rowCount`가 된 뒤에만 execution이 완료된다.

## 4. Column, vector lane, bank 구분

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

`vectorLanes < arrayDim`이면 VectorUnit은 array result를 여러 group으로 처리한 뒤, 각 group 결과를 원래 `arrayDim` 위치의 sparse vector로 복원한다.

## 5. VectorUnit

VectorUnit은 element마다 동일한 runtime operation을 적용한다.

```text
VectorBypass   : P
VectorMultiply : P × S
VectorShift    : shift(P, E)
```

`VectorScaleCapability#(format_t)`는 format의 scale 지원 여부를 나타내며, `VectorTransform#(format_t, acc_t, scale_t)`는 실제 transform을 정의한다.

- Signed INT: Bypass/Multiply/Shift. Production integer synthesis는 signed 64-bit partial, contribution, Accumulator state를 사용한다.
- FLOAT: Bypass behavior only

Accumulator 주소, 기존 state, `accumulate` 여부는 VectorUnit interface에 없다. Integer provider request도 signed 64-bit lane을 유지한다. Raw signed-32 output과 frozen ABI v2 provider callback에서만 final saturation을 수행하며 ABI v3 provider callback은 signed-64 lane을 그대로 전달받는다.

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

Host는 block-major `S[b,j]` matrix view를 소유하며 `block_size`, `total_k`, `context` 메타데이터를 제공한다. 각 hardware execution의 `k_start`에서 Core는

```text
b = floor(k_start / block_size)
request tag = (context + J tile offset, b)
```

를 계산한다. Cache miss가 나면 host가 요청 row의 J tile slice를 응답한다.

Core는 응답받은 `S[b,column]` vector를 execution 동안 고정한다. 한 hardware partial이 두 K-block을 가로지르면 실행을 거부한다.

Execution은 이전 column wavefront, VectorUnit, Accumulator commit이 모두 끝난 뒤에만 교체된다. 따라서 서로 다른 K-block의 column output이 섞이지 않는다. Staggered column output은 모두 execution 시작 시 latch한 `executionScaleRow[j] = S[b,j]`를 사용한다.

전체 `ceil(K/B) × J` matrix는 host memory가 소유한다. Core는 `kStart / blockSize`로 block을 선택하고 `(context + J tile offset, block)` tag의 row를 요청한다. Normal execution cache는 current/next row entry 두 개를 유지한다.

Current hit은 transfer 없이 reuse하고, next hit은 promote하며, miss가 나면 demand response까지 execution을 보류한다. Current row가 준비되면 가능한 `b+1` row를 prefetch한다. 실행 중인 row와 immediate lookahead row는 별도의 immutable snapshot으로 보관한다.

Context 변경과 reset 시 cache entry를 무효화한다. Scale block 수에는 synthesis-time capacity 제한이 없다.

SystolicArray partial은 VectorUnit으로 직접 전달된다. K-block partial을 먼저 재결합하는 stage는 없다.

## 8. Backpressure 처리

VectorUnit이 busy이면 Core는 다음 array result를 받지 않는다. SystolicEngine result FIFO가 가득 차면 `advanceArray` rule이 멈추고 InputSkew와 모든 PE도 함께 정지한다. 따라서 wavefront 내부의 상대 timing은 유지된다.

현재 in-flight 정책은 다음과 같다.

```text
SystolicEngine result FIFO : 여러 sparse result를 완충
VectorUnit                 : full-width result 1개
Accumulator                : group result를 cycle별 commit
```

## 9. Low-level과 high-level execution

Low-level DIM execution은 microkernel/debug/regression primitive다.

```text
1 <= rowCount <= arrayDim
K extent = arrayDim
N extent = arrayDim
vectorLanes divides arrayDim
accRows >= arrayDim
```

High-level address-driven execution에서는 `MatmulScheduler`와 `WorkScheduler`가 큰 M/N/K 문제를 hardware execution 단위로 분할한다. Host는 K fragment나 weight-preload/activation-feed 순서를 생성하지 않는다. `WorkScheduler`의 fragment 규칙은 다음과 같아서 K-block boundary를 넘지 않는다.

```text
remaining_in_block = block_size - (k_start % block_size)
k_count = min(DIM, K - k_start, remaining_in_block)
```

`tile_K`는 Gemmini/host 메타데이터이며 RTL K fragment 크기는 DIM, 남은 K, quantization block boundary가 결정한다. Core에 DMA, scratchpad, memory-system timing model은 없지만 global matrix scheduling 자체는 RTL scheduler stack에 있다.

## 10. Address-driven scheduler stack

`IM2PCore` 내부에는 다음 RTL control module이 한 번씩만 존재한다.

```text
MatmulScheduler: M/N tile, async stripe publication, completion ordering
WorkScheduler:   K fragment, scale block, accumulate-first selection
IM2PCore:        tagged A/W/S/C channels, buffer/bank readiness, execution
```

Full-matrix mode는 descriptor의 전체 M/N 범위를 scheduler 내부에서 순차적으로 traversal하며 current work와 immediate lookahead 하나를 expose한다. Async mode는 publish된 stripe만 scheduling하며, 미공개 stripe 주소는 prefetch하지 않는다. Host publication 가능 여부와 RTL FIFO readiness는 별도 상태다. C++ frontend가 공개하는 mode도 이 두 scheduler 의미에 대응하는 `FULL`과 `PIPELINE`뿐이며 제3 mode는 없다.

각 host request에는 address, element count, tag가 있다. Provider는 address를 borrowed A/W/S/C view로 resolve하고 같은 tag로 응답한다. 최종 output row acknowledgement가 scheduler의 work/stripe completion보다 먼저 완료되어야 한다.

External `start`, work completion, job acknowledgement는 pending register를 거쳐 내부 rule에서 state를 전이한다. 이 one-cycle barrier는 Verilator의 post-edge combinational reevaluation에서도 state register writer가 one-hot임을 보장한다.

Rust layer는 provider, clock 진행, watchdog, counter snapshot만 담당한다. Matrix/fragment/scale-block 선택은 RTL 외부에 복제하지 않는다.

Provider는 같은 combinational state에서 ready인 A/W/S/C response를 함께 stage하고 한 positive edge에서 commit한다. 따라서 host 함수 호출 순서가 독립 RTL channel을 여러 cycle로 직렬화하지 않는다.

## 11. Publish-triggered lookahead

Async `publishStripe`는 queue-only operation이 아니다. 승인된 stripe는 activation host memory를 RTL에서 사용할 수 있음을 알리는 event이며, current WS/RC execution이 계속되는 동안 Core가 즉시 다음 stripe의 첫 A/W/S fragment를 prepare할 수 있게 한다.

Production ExSIA `PIPELINE`에서는 각 stripe의 folding commit 직후 post-fold event를 이 publication path에 전달한다. 전체 activation quantization 종료까지 event를 모아 batch publish하지 않는다. `FULL`은 post-fold event를 collector에 보존하되 전체 quantization 성공 뒤 full descriptor를 한 번 시작한다. 두 mode 모두 NPU output과 8-bit RMD를 frontend-owned staging에서 완료한 뒤에만 caller output을 publish한다.

미공개 activation에는 A read를 발행하지 않는다. Full-matrix mode도 이 scheduler path를 사용하지만 A/W/S/C region 전체가 descriptor 제출 시점부터 available하므로 publication gate가 없다.

```text
CPU / host      publish current A             publish next A
                     |                              |
NPU scheduler    current work ----------------> capture one lookahead
WS/RC engine     [       current stripe executes       ]
A/W/S staging                                   [A fetch][W fetch/S reuse-or-fetch]
PE banks         active bank: current WS       inactive bank: preload if W is not resident
ordered output   C/Accumulator for current ---- promotion ----> C/Accumulator for next
```

`MatmulScheduler`는 current 하나와 immediate lookahead 하나만 expose한다. 이후 publish된 stripe는 2-entry publication FIFO에서 순서를 유지하며 lookahead를 대체하지 않는다.

`IM2PCore`에도 execution engine은 하나이므로 lookahead는 실행/Accumulator/output write를 하지 않는다. A rows, W rows, 그리고 필요한 S row를 외부 staging register에 채우고, 해당 work가 promotion된 뒤에만 engine 및 Accumulator/output path로 넘긴다.

PE weight storage는 두 bank로 유지된다. Current execution은 active bank를 사용한다. Lookahead W의 처리 경로는 다음 중 정확히 하나다.

| Case | Host/PE action |
|---|---|
| nonresident W | host W request를 내고 response를 PE 밖 external lookahead staging rows에 저장한다. |
| exact resident W | capture 시점에 final-current-work safety가 성립하고 base, row stride, J start/count, K start/count가 모두 일치하면 matching resident bank를 reuse한다. 아직 safety가 확보되지 않았으면 보수적으로 host fetch한다. |
| staged nonresident W | current의 마지막 tile/fragment safety point에서만 external staging rows를 inactive bank에 preload한다. Active bank에는 쓰지 않는다. |
| partial W | completion 전에 받은 staging row는 promotion 뒤 inactive-bank load에 직접 주입하고, 아직 없는 row만 host에 요청한다. 이미 받은 row는 다시 fetch하지 않는다. |
| scale | `(context + J offset, block)`가 current 또는 next scale cache와 일치하면 그 row를 reuse한다. 그렇지 않으면 current scale demand/prefetch가 비어 있고 current engine이 active일 때 host S request를 낸다. |

준비된 scaled work는 promotion 때 해당 scale row를 execution snapshot으로 latch한다. Execution이 drain될 때까지 이 snapshot은 immutable이므로 이후 scale prefetch/response가 column별 staggered output에 섞일 수 없다. 마찬가지로 output write와 Accumulator update는 promotion 이후에만 허용된다.

### RTL logical cycle accounting

Logical cycle은 positive edge 하나를 포함하는 RTL clock period 하나다. Reset edge는 counter를 0으로 초기화하며 logical time에는 포함하지 않는다. Raw pulse는 1 cycle, tick N회는 N cycle이며 combinational eval/getter는 0 cycle이다.

`progress_stream(stream, N)`은 idle, host-wait, fetch, compute, drain 중 어느 state에서도 정확히 N cycle을 진행한다. N=0이면 response service도 edge도 없다.

Watchdog limit은 host wall-clock timeout이 아니라 service-loop iteration 수다. 정상 progress iteration 하나가 staged provider edge 하나를 commit하지만 setup과 final acknowledgement pulse도 별도 logical cycle이므로 cycle statistic은 단순 watchdog iteration count와 동일하다고 가정하면 안 된다.

`WorkStats::cross_stripe_overlap_cycles`는 current engine execution과 next-stripe A/W/S fetch 또는 PE preload가 동시에 active인 logical cycle 수다. `activation_overlap_cycles`, `weight_overlap_cycles`, `scale_overlap_cycles`는 current work 내부 fragment 준비/compute overlap이며 cross-stripe aggregate에 속하는 부분 counter가 아니다.

Host pointer dereference, CPU thread scheduling, sleep, 기타 wall-clock은 이 counter에 포함되지 않는다. `lookahead_ready_cycle`은 first-fragment A/W/S staging과 필요한 PE bank preload/reuse가 모두 완료된 cycle snapshot이다.

`lookahead_publish_cycle`은 matmul start를 기준으로 두 번째 stripe publication이 RTL에 accept된 cycle이다. 이 값과 0이 아닌 가장 이른 `lookahead_first_activation_cycle`, `lookahead_first_weight_cycle`, `lookahead_weight_preload_cycle`, `lookahead_scale_cycle`의 차가 publish-to-first-prepare cycles다. `current_stripe_completion_cycle`에서 `lookahead_start_cycle`까지의 차는 completion-to-next-start transition cycles다.

`stripe_host_wait_cycles`는 current stripe transition 뒤 다음 stripe가 publish되지 않아 scheduler가 기다린 cycle이며, A/W/S/C channel wait은 각각 `activation_wait_cycles`, `weight_wait_cycles`, `scale_wait_cycles`, `output_wait_cycles`다. `lookahead_weight_requests`/`lookahead_weight_reuse_hits`와 `lookahead_scale_requests`/`lookahead_scale_reuses`는 host fetch와 exact reuse를 분리해 보고한다.

이 timebase는 기능 RTL time만 나타낸다. On-core scale cache와 resident weight-bank state는 기능적으로 모델링하지만 host/SoC DRAM, cache timing, scratchpad, DMA, interconnect, CPU execution, OS scheduling, clock frequency는 모델링하지 않는다.

Host pointer access도 zero-time이다. 따라서 CPU와 NPU의 common time 비교나 physical ns/GHz/Fmax 또는 silicon 성능을 이 counter나 Verilator host runtime에서 도출할 수 없다.

현재 production ExSIA format은 A8/Q8이다. A4/Q4, A16/Q16, Q8 H2/HP2와 mixed precision은 TODO이고 지원되는 route로 defer하거나 다른 format으로 fallback하지 않는다. ABI v3는 이 지원 범위를 넓히는 format ABI가 아니라 signed-64 provider output transport를 추가한 ABI이며, ABI v2 layout과 final signed-32 saturation 경계는 유지한다.

코드 분석 순서와 작성 규칙은 [코드 분석 가이드](CODE_ANALYSIS_GUIDE.md), public simulator 계약은 [simulator 사용법](../sim/README.md)에서 확인한다.
