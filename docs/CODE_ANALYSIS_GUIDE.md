# IM2P.sim 코드 분석 가이드

이 문서는 IM2P.sim의 RTL과 simulator 코드를 분석하는 기본 순서와 작성 규칙을 정의한다. 특별한 이유가 없다면 코드 분석 문서도 이 가이드를 따른다. 현재 architecture는 [ARCHITECTURE.md](ARCHITECTURE.md)에서 설명한다.

## 1. 분석 기준과 source identity

분석을 시작하기 전에 다음 정보를 기록한다.

```bash
git branch --show-current
git rev-parse HEAD
git status --short
```

긴 분석 문서의 서두에는 branch, HEAD commit, dirty/clean 상태를 남긴다. 분석 근거는 다음 순서로 우선한다.

```text
1. 현재 source code
2. current tests
3. current documentation
4. 과거 audit/report
```

문서와 코드가 다르면 현재 코드를 우선한다. 과거 보고서에만 있는 기능을 현재 구현된 것처럼 설명하지 않는다.

코드만으로 확인할 수 없는 사항은 `현재 코드만으로는 확인되지 않는다.`라고 쓴다. 근거 없는 미래 계획이나 구현 의도를 추정하지 않는다.

## 2. 기본 분석 방향

파일 tree나 top-down FSM 순서로 읽지 않는다. 작은 데이터패스에서 시작해 scheduling과 외부 실행 계층으로 올라간다.

```text
Config / Types
    ↓
PE
    ↓
InputSkew
    ↓
SystolicArray
    ↓
ExecuteController
    ↓
SystolicEngine
    ↓
Scale / VectorUnit
    ↓
Accumulator
    ↓
Work Types
    ↓
WorkScheduler
    ↓
MatmulScheduler
    ↓
IM2PCore
    ↓
Synthesis Tops
    ↓
Verilator C++ Bridge
    ↓
Rust FFI / RTL Primitive
    ↓
Memory Provider
    ↓
Full / Stripe Simulator
    ↓
Raw C API
    ↓
High-level C++ Frontend
    ↓
Tests
```

`IM2PCore` FSM부터 분석하지 않는다. 하위 모듈의 데이터와 책임을 먼저 확정한 뒤 top-level integration을 다룬다.

## 3. 필수 분석 순서

### 3.1 공통 설정과 타입

먼저 다음 source를 읽는다.

```text
src/common/Config.bsv
src/common/Types.bsv
src/common/Arithmetic.bsv
src/io/HostMemoryTypes.bsv
src/control/ExecuteCmd.bsv
src/control/WorkTypes.bsv
```

다음 항목의 실제 정의와 bit 폭을 확정한다.

- `DIM` / `arrayDim`, `peLatency`
- `input_t`, `weight_t`, `product_t`, `acc_t`, `scale_t`
- `VectorOp`
- `HostAddress`
- A/W/S/C request/response type
- scheduler/work metadata type

이 단계에서는 이후에 등장하는 type, Vector width, index width, request/response의 의미를 먼저 고정한다.

### 3.2 PE

`src/array/PE.bsv`를 읽는다. 먼저 산술 관계를 설명한다.

```text
A : activation
B : stationary weight
D : 위쪽 partial sum
C : D + A × B
```

그다음 activation forwarding, partial-sum forwarding, registered behavior, active weight bank, inactive/preload weight bank, weight bank switching을 실제 method와 register 기준으로 분석한다.

### 3.3 InputSkew

`src/array/InputSkew.bsv`를 읽는다.

```text
logical activation row
        ↓
column별 delay
        ↓
systolic wavefront
```

Activation이 각 PE column에 서로 다른 RTL cycle에 도달하는 이유와, `step`이 정지 시 상대 timing이 어떻게 보존되는지 설명한다.

### 3.4 SystolicArray

`src/array/SystolicArray.bsv`를 읽는다. Scheduling에 앞서 datapath wiring을 설명한다.

- PE array 구성
- activation horizontal forwarding
- partial-sum vertical forwarding
- weight preload와 bank activation
- bottom-column output

K-block이나 matrix traversal 책임을 이 모듈에 부여하지 않는다.

### 3.5 ExecuteController

`src/control/ExecuteController.bsv`를 읽는다. 이 모듈의 책임은 한 번의 SystolicArray execution 진행을 관리하는 데 한정한다.

- row issue
- column output issue
- Accumulator commit 추적
- Done 판정

다음은 `ExecuteController` 책임이 아니다.

- M/N matrix traversal
- K-fragment generation
- stripe scheduling
- scale-block traversal

### 3.6 SystolicEngine

`src/array/SystolicEngine.bsv`를 읽는다.

```text
ExecuteController
        +
InputSkew
        +
SystolicArray
        ↓
one array execution
```

execution start, activation acceptance, InputSkew progression, systolic wavefront, bottom output, drain, completion의 순서로 설명한다.

### 3.7 Scale / VectorUnit

`src/vector/Scale.bsv`와 `src/vector/VectorUnit.bsv`를 읽는다. `VectorBypass`, `VectorMultiply`, `VectorShift`의 runtime behavior를 구분한다.

```text
SystolicArray bottom-column partial
        ↓
VectorUnit
        ↓
scaled/transformed K-fragment contribution
```

K-fragment scale 적용을 다음처럼 잘못 설명하지 않는다.

```text
P0 + P1
   ↓
scale
```

실제 구조는 fragment별 변환이다.

```text
P0
 ↓
VectorUnit
 ↓
Accumulator

P1
 ↓
VectorUnit
 ↓
Accumulator
```

같은 K block에서 같은 scale을 재사용하더라도 각 hardware fragment의 partial은 각각 `VectorUnit`을 통과한다.

### 3.8 Accumulator

`src/accumulator/Accumulator.bsv`를 읽는다.

```text
VectorUnit contribution
        ↓
replace 또는 accumulate
        ↓
Accumulator state
```

첫 K fragment의 replace/init semantics와 이후 K fragment의 accumulate semantics를 구분한다. Column index는 bank를 선택하고 destination row는 bank 내 row를 선택한다는 점도 함께 설명한다.

### 3.9 Work-related types

`WorkScheduler`를 읽기 전에 `src/control/WorkTypes.bsv`와 `src/control/ExecuteCmd.bsv`를 읽는다.

- I/J/K region, `kStart`, `kCount`, `blockIndex`
- current fragment와 next fragment
- resource readiness
- `accumulate`, first/last, lookahead

Metadata의 생성자와 소비자를 함께 추적한다.

### 3.10 WorkScheduler

`src/control/WorkScheduler.bsv`를 한 덩어리로 설명하지 않는다. 실제 rule/method를 기준으로 다음 순서로 나눈다.

#### A. K-fragment 생성

```text
remaining_in_block =
    block_size - (k_start % block_size)

k_count =
    min(
        DIM,
        K - k_start,
        remaining_in_block
    )
```

Scale을 사용하지 않는 경로에도 block 경계 제한이 적용되는지 source에서 별도로 확인한다.

#### B. Current fragment 준비

```text
A fetch
W fetch/preload
S fetch/reuse
```

#### C. Current execution

```text
resource ready
    ↓
startExecution
```

#### D. Next K-fragment preparation

```text
execute current
+
prepare next K fragment
```

#### E. Cross-stripe lookahead

```text
execute current stripe
+
prepare next published stripe
```

#### F. Fragment promotion/accumulation

현재 fragment의 완료, 다음 fragment의 promotion, first-fragment 상태 변경, accumulate 결정, work 완료를 각각 실제 state transition에 연결한다.

### 3.11 MatmulScheduler

`src/control/MatmulScheduler.bsv`를 읽는다.

- I/J traversal
- full-matrix work traversal
- current stripe
- immediate lookahead stripe
- publication FIFO
- stripe completion

Full mode와 Stripe mode를 같은 scheduler 관점에서 비교한다.

```text
Full:
    activation 전체가 시작부터 available

Stripe:
    submit에 따라 availability 증가
```

### 3.12 IM2PCore

하위 모듈을 모두 분석한 뒤 `src/core/IM2PCore.bsv`를 읽는다. 다음 구성요소의 top-level integration과 request/response identity를 설명한다.

- `MatmulScheduler`
- `WorkScheduler`
- `SystolicEngine`
- `VectorUnit`
- `Accumulator`
- Activation buffers
- Weight preload/staging
- Scale current/next/snapshot
- A/W/S/C request/response
- Output writeback

### 3.13 Synthesis tops

`synth/`의 DIM16/DIM32/DIM64 top을 읽고 synthesis-time parameter가 어떻게 확정되는지 확인한다.

- `DIM`
- data type
- scale type
- accumulator type
- `peLatency`

Architecture 설명을 synth wrapper부터 시작하지 않는다.

### 3.14 Verilator C++ bridge

RTL 분석을 마친 다음 `sim/ffi/im2p_verilator.cpp`를 읽는다.

```text
1. Verilated model ownership
2. reset
3. eval
4. CLK low-high-low
5. tick
6. pulse / RDY-EN handshake
7. cycle counter
8. positive-edge counter
9. same-edge A/W/S/C response batching
```

Cycle 설명은 다음 세 항목을 엄격히 구분한다.

```text
RTL logical cycle
!= C++ wall-clock execution time
!= physical clock period
```

Cycle을 분석할 때는 다음 네 계층도 구분한다.

```text
External C++ Host
    execute / submit_stripe / fence

Simulation Bridge
    Verilated model clock / RDY-EN / memory service

Verilated RTL Model
    BSV state와 scheduler/datapath

RTL Telemetry
    global/work/detailed/event counters
```

성능 cycle의 source of truth는 `IM2PCore` RTL telemetry다. C++의 private edge counter, bridge loop count, progress 호출 횟수, wall-clock을 NPU cycle이라고 설명하지 않는다.

`im2p_cycle_count()`는 RTL `rtlCycleCount`를 읽는다. Simulation Bridge는 CLK를 토글하고 I/O를 service할 뿐, performance counter를 재구성하지 않는다.

### 3.15 Rust FFI와 RTL primitive

`sim/src/`에서 FFI 선언과 RTL primitive wrapper를 찾아 다음 순서로 읽는다.

```text
FFI
RTL primitive wrapper
reset
tick
ready
pulse
cycle count
```

이 계층이 RTL scheduler나 performance counter를 대신한다고 설명해서는 안 된다. `WorkStats::work_total_cycles`는 RTL `lastCompletedWorkCycles`에서 온다.

Detailed cycle은 새 work acceptance 때 RTL에서 초기화되고 완료 뒤 직접 전달된다. Low-level `execute_tile()` phase latency는 실행 전후 RTL global-cycle snapshot의 차다.

### 3.16 Memory provider

Host-memory provider를 다음 흐름으로 분석한다.

```text
RTL request
    ↓
Rust/C++ memory provider
    ↓
host address/view resolve
    ↓
response
    ↓
RTL
```

Host pointer dereference에 걸리는 wall-clock과 RTL logical response timing을 구분한다. 현재 구현에 없는 DRAM latency, cache latency, DMA latency, TLB, RoCC, physical bandwidth를 임의로 추정하지 않는다.

모델링하지 않는 항목은 limitation으로 명시한다. Wait cycle을 실제 DRAM latency라고 표현하지 않는다.

### 3.17 Full / Stripe simulator path

Rust simulator의 high-level execution은 Full부터 분석한다.

```text
Full:
    start
    RTL scheduling
    memory request/response
    completion

Stripe:
    begin
    publish
    progress
    poll
    finish
```

Full execution이 Rust 반복문으로 RTL I/J/K scheduler를 우회하는지는 source에서 확인한다. 확인 없이 `execute_tile()` 반복이라고 단정하지 않는다.

### 3.18 Raw C API

`sim/include/im2p_sim.h`와 `sim/src/c_api/`를 읽는다.

- ownership
- error mapping
- stream lifecycle
- progress semantics
- stats ABI

### 3.19 High-level C++ frontend

`frontend/`는 마지막 execution layer로 읽는다.

```text
ggml_gemmini_args_t
        ↓
execute
        ↓
Run

StripeReadyEvent
        ↓
submit_stripe

Run
        ↓
fence
```

Bounded submit queue, dedicated NPU worker, raw API single-thread ownership, backpressure, `run_id`, sticky error, `fence`, destruction을 설명한다.

Numerical route는 current source의 classification을 따른다. 현재 문서에는 최소한 다음 상태를 일관되게 유지한다.

```text
q8_0_unpacked_to_h1, q8_h0, q8_h1, q8_hp1
    supported

q8_channel, q8_channel_dense_sidecar
    supported
    RTL VectorBypass
    channel scale은 host output에서 한 번 적용

q8_h2
    Deprecated

q8_hp2
    Unsupported
```

Native/provider route가 전체 tensor를 materialization하지 않고 요청된 logical fragment를 제공하고 RTL scheduling을 유지하는지 확인한다. `q8_h2`를 향후 지원이나 일시 미지원으로 표현하지 않으며 `q8_hp2`와 같은 상태로 합치지 않는다.

### 3.20 Tests

구현 분석을 마친 뒤 테스트를 읽는다. 테스트에서 architecture를 먼저 추론하지 않는다.

```text
1. PE / InputSkew / Array unit tests
2. VectorUnit / Accumulator tests
3. WorkScheduler tests
4. MatmulScheduler tests
5. K-block / scale tests
6. weight preload tests
7. full matrix integration
8. stripe integration
9. lookahead tests
10. cycle-accounting tests
11. C ABI tests
12. high-level frontend tests
```

Cycle test에서는 다음 edge contract를 확인한다.

```text
reset = 0
eval-only = +0
tick/pulse = +1
progress(N) = +N

startMatmul acceptance:
    workCycles = 0

terminal MatrixDone edge:
    lastCompletedWorkCycles latch
    workCompletionCycle - workStartCycle
        == lastCompletedWorkCycles
```

Detailed counter는 registered-state occupancy를 나타내며 서로 exclusive하지 않다. `total = compute + drain + waits + overlaps` equality를 가정하지 않는다. Back-to-back work에서 work cycle, detailed counter, event timestamp가 새 acceptance 때 reset되는지 확인한다.

## 4. 코드 제시 규칙

### 4.1 완전한 코드 단위

함수, 구조체, interface, enum, rule, method, module은 이유 없이 여러 코드 블록으로 쪼개지 않는다.

```text
15줄 미만
    하나의 완전한 코드 단위로 제시

15줄 이상
    기능적으로 의미 있는 하위 단위로 분할 가능
```

15줄 미만인 단위는 전체를 한 코드 블록에 제시한 뒤 바로 아래에서 분석한다. 중간 일부만 잘라 설명하지 않는다.

15줄 이상인 코드는 앞 10줄/뒤 10줄처럼 기계적으로 자르지 않는다. 상태/register 선언, request 처리 rule, execution-start rule, completion/promotion rule처럼 기능 단위로 나눈다.

긴 코드를 나눈 경우 다음 순서를 지킨다.

```text
코드 블록 1
↓
블록 1 분석

코드 블록 2
↓
블록 2 분석
```

여러 코드 블록을 먼저 나열한 뒤 한꺼번에 설명하지 않는다.

### 4.2 코드 생략과 원문 보존

다음 항목을 `...`로 생략하지 않는다.

- guard
- state update
- method side effect
- rule condition
- address calculation
- tag validation
- bank switch
- scale selection

분할할 때는 실제 source에서 연속되며 의미가 완결된 부분을 사용한다. 원래 BSV를 pseudo-BSV로 다시 쓰거나 guard, 변수명, type을 단순화하지 않는다. Pseudocode가 필요하면 원문 분석 뒤 별도 블록에 제시하고 `pseudocode`임을 명시한다.

## 5. 코드 단위 분석 형식

분석은 가능하면 다음 순서를 따른다.

```text
1. 역할
2. 입력/출력
3. 내부 상태
4. guard / 실행 조건
5. 한 RTL cycle에서 발생하는 상태 변화
6. 다른 module과의 연결
7. architecture상 의미
```

BSV rule/method를 software 함수처럼 설명하지 않는다. 실제 코드에서 의미가 있다면 guard, ready condition, `Action` / `ActionValue`, state mutation, atomicity, rule firing, method enable, 동일 RTL cycle concurrency를 설명한다.

Cycle은 `동일 RTL cycle`, `다음 RTL cycle`, `execution drain 이후`, `response acceptance edge`, `method firing edge`처럼 기준을 명시한다. Source나 test에서 확인하지 않은 latency를 `1 cycle later`, `2 cycles later`라고 쓰지 않는다.

RTL만 보고 multiply가 느리다, 500 MHz가 가능하다, critical path가 길다, N ns가 걸린다고 단정하지 않는다. Physical timing은 synthesis/STA 근거가 있을 때만 설명한다.

## 6. 용어와 책임 가드

### 6.1 Scheduler 책임

다음 표현을 사용하지 않는다.

```text
SystolicArray가 K block을 판단한다.
VectorUnit이 memory address를 선택한다.
ExecuteController가 matrix tiling을 수행한다.
Rust가 K-fragment를 scheduling한다.
```

실제 판단과 상태를 소유한 module을 source에 근거해 명시한다.

### 6.2 Scale

Workload 차원의 scale을 단순히 per-lane scale이라고 부르지 않는다.

```text
S[b,j]
K-block × output-column
```

Physical Vector lane mapping을 설명할 때만 lane이라는 용어를 사용한다.

### 6.3 Partial sum

다음 값을 구분한다.

```text
PE partial sum
SystolicArray bottom-column partial
K-fragment contribution
Accumulator state
final output
```

모두를 `output`이라고 부르지 않는다.

### 6.4 Current / Next / Lookahead

다음 항목은 서로 다른 scheduling level에 속한다.

```text
current K fragment
next K fragment
next/lookahead stripe
```

`next`만 써서 어느 계층인지 불분명하게 만들지 않는다.

## 7. 긴 분석 문서의 권장 장 구성

전체 프로젝트 분석은 다음 구성을 기본으로 한다. 파일별 문서에는 필요한 부분만 적용할 수 있다.

```text
1. 기본 타입과 데이터패스
2. Systolic 단일 실행
3. Vector 처리와 누산
4. Matrix/Stripe Scheduling
5. IM2PCore 통합
6. Verilator/Rust Simulation
7. External API
8. Verification
```
