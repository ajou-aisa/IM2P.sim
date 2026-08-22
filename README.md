# IM2P.sim

Bluespec으로 작성한 **레지스터 기반 weight-stationary systolic NPU RTL 시뮬레이터**다. DIM16/DIM32/DIM64 구성에서 address-driven matrix scheduling, K-block-aware fragmentation, VectorUnit scale path, Accumulator, 비동기 stripe publication, 다음 stripe 선행 준비를 검증한다.

C++ harness가 Verilated RTL clock을 직접 구동하므로 측정 시간은 wall-clock이 아닌 RTL logical cycle이다. Gemmini의 WS 실행 방식은 참고하되 `Tile`, `Mesh`, `MeshWithDelays`, DMA, RoCC, ROB 등의 Gemmini generator/SoC 계층은 복제하지 않는다. DIM64의 16×16 synthesis tile은 BSC scheduling hierarchy만 나누며 별도 Gemmini execution 계층이나 cycle을 추가하지 않는다.

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

## 설계 원칙

### 단일 Core

최상위는 `src/core/IM2PCore.bsv` 하나다.

- format과 precision은 synthesis-time type parameter로 정한다.
- INT에서는 runtime에 `VectorBypass`, `VectorMultiply`, `VectorShift`를 선택한다.
- FLOAT는 같은 Core source와 datapath를 사용하되 transform policy는 Bypass만 제공한다.
- INT의 scale 적용 여부나 Multiply/Shift 선택 때문에 RTL을 다시 합성하지 않는다.
- production ExSIA 경로는 A8/Q8만 지원한다. Matched A4/Q4와 A16/Q16의
  RMD scale integration은 TODO다.

FLOAT instance에는 실제 scale multiply/shift 구현이 없다. 최종 generated RTL에서 관련 연산기가 제거되는지는 `make rtl`과 synthesis report로 확인한다.

### 전형적인 WS SystolicArray

각 PE는 stationary weight를 보유한다. Activation은 오른쪽으로, partial sum은 아래로 이동한다.

```text
A → PE → PE → ...
    ↓    ↓
D → C    C
```

square array가 execution 한 번에 처리하는 기본 범위는 다음과 같다.

```text
K extent = arrayDim
N extent = arrayDim
M extent = rowCount, 1 <= rowCount <= arrayDim
```

K/N이 `arrayDim`보다 작으면 scheduler가 남는 activation/weight element를 0으로 채운다. High-level address-driven 실행에서는 `MatmulScheduler`와 `WorkScheduler`가 큰 M/N/K 연산을 여러 hardware execution으로 분할한다.

### Column, physical vector lane, Accumulator bank 용어

세 용어는 구분한다.

```text
array column
    마지막 PE 행에서 complete partial sum이 나오는 공간적 output 위치

physical vector lane
    한 cycle에 실제 Bypass/Multiply/Shift를 수행하는 연산 경로

Accumulator bank
    한 output column의 모든 logical row를 저장하는 state bank
```

Architectural index의 대응 관계는 다음과 같다.

```text
SystolicArray output column index
= VectorResult sparse index
= Accumulator bank index
```

`vectorLanes < arrayDim`이면 physical vector lane 집합이 array column을 여러 group으로 나눠 처리한다. 구현상 `vectorLanes`가 `arrayDim`의 약수인 구성만 허용한다.

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

INT reference operation은 다음과 같이 정의한다.

```text
VectorBypass
    contribution = partial

VectorMultiply
    contribution = partial × signed scale coefficient

VectorShift
    scale >= 0 : contribution = partial << scale
    scale <  0 : contribution = partial >> |scale|
```

integer policy는 signed 64-bit accumulator/provider transport 안에서 two's-complement arithmetic과 arithmetic right shift를 사용한다. 중간 32-bit narrowing은 없으며 rounding도 적용하지 않는다. 단일 canonical ABI의 provider callback은 signed-64 값을 그대로 받고, raw output의 최종 signed-32 경계에서만 한 번 saturation한다.

### Accumulator는 주소와 상태를 담당

Core가 valid column마다 destination row를 계산한다.

```text
destinationRow[column]
    = accumulatorBaseRow + logicalRowOffset[column]
```

Accumulator의 column index는 bank를 선택하고 destination row는 해당 bank 내부 위치를 선택한다.

```text
accumulate=False
    bank[column][row] = contribution

accumulate=True
    bank[column][row] = bank[column][row] + contribution
```

Column별 `RegFile`을 `Accumulator` 내부 backend로 사용하며, 별도 범용 `BankedVectorMem` package는 두지 않는다. `accumulate=True`를 사용하기 전에 대상 accumulator row를 유효한 값으로 초기화해야 한다.

### Block은 별도 core가 아니라 runtime control

Array와 VectorUnit, Accumulator에는 block index나 block scheduler가 없다. Block metadata는 `IM2PCore`의 execution control state다.

- 일반 GEMM execution: `VectorBypass`
- coefficient scaling execution: `VectorMultiply`
- power-of-two scaling execution: `VectorShift`

Block-scale workload는 scale이 필요한 execution에만 operation과 host-owned scale matrix view를 공급한다. 여러 array execution의 partial을 VectorUnit 앞에서 재결합하지 않는다. 각 partial은 소속 K-block과 J-column의 `S[b,j]`로 변환된 뒤 Accumulator에 반영된다.

### Partial sum은 도착 즉시 처리

Column output은 systolic timing에 따라 서로 다른 cycle에 도착할 수 있다. `SystolicEngine`은 모든 column의 deskew를 기다리지 않고 현재 Valid인 column을 sparse result로 전달한다.

```text
valids[column]
rowOffsets[column]
partialSums[column]
```

작은 result FIFO는 backpressure만 흡수하며 partial 재결합 storage로 쓰지 않는다. FIFO가 가득 차면 InputSkew와 모든 PE의 `step`을 함께 정지해 wavefront의 상대 timing을 보존한다.

### Scale 선택은 Core runtime control

Scale matrix 형상은 `ceil(K / B) × J`다. 전체 matrix는 host memory에 있다.

Normal execution cache는 current/next row 두 entry를 유지한다. 실행 중인 row와 immediate lookahead row는 별도의 immutable snapshot으로 보관한다. Multiply/Shift execution에서는 metadata와 context를 설정한 뒤 RTL이 필요한 row를 요청한다.

```bsv
configureScaling(blockSize, totalK, context)
startExecution(command, kStart, kCount)
scaleRequestContext
scaleRequestBlock
scaleRequestKind
putScaleRow(context, block, columnScales)
```

Core는 `b = kStart / blockSize`를 계산한다. current hit은 transfer 없이 reuse하고 next hit은 promote한다.

miss이면 demand request를 발생시키고 response가 올 때까지 execution을 보류한다. current row가 준비되고 마지막 block이 아니면 `b+1`을 prefetch한다.

```bsv
startExecution(bypassCommand, kStart, kCount)
putActivationRow(activations)
```

Execution을 시작할 때 selected row를 `executionScaleRow`로 고정한다. 이 architecture는 이전 execution의 모든 column commit이 끝나기 전에 다음 execution을 시작하지 않으므로 mixed-block column output이 생기지 않는다.

staggered wavefront의 column `j`는 고정된 row의 `S[b,j]`를 사용한다. Prefetch response는 현재 execution snapshot을 덮어쓰지 않는다.

Scale row 수에는 synthesis-time 제한이 없다. Row는 host view의 `block * row_stride + column_offset`에서 on demand로 읽어 DIM까지 zero padding한다.

Context가 바뀌면 current/next cache를 무효화한다. Bypass는 request나 matrix 없이 실행하며 cache를 변경하지 않는다.

## Source tree 구조

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
│   ├── SystolicArrayTiled.bsv
│   ├── SystolicArrayInt{4,8,16}x64.bsv
│   └── SystolicEngine.bsv
├── vector/
│   ├── Scale.bsv
│   └── VectorUnit.bsv
├── accumulator/
│   └── Accumulator.bsv
├── io/
│   └── HostMemoryTypes.bsv
├── control/
│   ├── ExecuteCmd.bsv
│   ├── ExecuteController.bsv
│   ├── WorkTypes.bsv
│   ├── WorkScheduler.bsv
│   └── MatmulScheduler.bsv
└── core/
    └── IM2PCore.bsv
sim/        Rust simulator와 raw C ABI
frontend/   선택형 Gemmini-compatible C++ frontend
synth/      DIM16/DIM32/DIM64 synthesis top
```

`tests/TestVectorUtils.bsv`는 BSV에 없는 `vec(...)` literal을 대신해 테스트용 2/3/4-element Vector helper만 제공한다. RTL source에는 포함하지 않는다.

## Verilator cycle 측정과 memory model

현재 public simulation path는 low-level `execute_tile`/direct row method와 `MatmulScheduler`/`WorkScheduler`가 발행하는 tagged A/W/S/C address channels와 full-matrix/striped descriptors를 사용한다. 독립 channel response는 같은 RTL edge에 함께 commit할 수 있다. Host wrapper는 동시에 처리할 수 있는 독립 A/W/S/C response를 여러 cycle에 걸쳐 직렬화하지 않는다.

성능 cycle의 기준은 IM2PCore 내부 RTL telemetry다. C++ Verilator bridge는 clock과 I/O를 구동하고 RTL counter를 읽는다. IM2P.sim의 cycle은 Verilator C++ 프로그램 실행시간, bridge loop 횟수, host progress 반복 횟수를 환산한 값이 아니다.

| 동작 | runtime counter 변화 |
|---|---:|
| reset | 0으로 초기화 |
| eval-only | +0 |
| direct tick | +1 |
| accepted pulse | +1 |
| `progress_stream(..., N)` | 정확히 +N |

A/W/S/C interface는 abstract host-memory provider다. DRAM/cache/scratchpad/DMA, interconnect, TLB, RoCC의 physical latency는 포함하지 않는다.

Wait counter는 physical DRAM latency가 아니라 RTL request가 outstanding 상태인 logical cycle을 센다. C++/Rust host pointer dereference의 wall-clock 비용도 logical-cycle statistics에 자동으로 포함되지 않는다.

```text
RTL logical cycle != C++ wall-clock != physical clock period
```

Verilator만으로 GHz, Fmax, ns latency, physical TOPS를 얻을 수 없다. 별도 synthesis/STA가 `f_clock`을 제공한 경우에만 `latency_seconds = rtl_cycles / f_clock`으로 변환한다. Verilator host 실행속도를 `f_clock`으로 사용하지 않는다.

CPU ExSIA LA/SF wall-clock과 NPU Verilated RTL cycle을 자동으로 같은 timebase로 볼 수 없다. 직접 지원하는 범위는 NPU RTL cycle latency, RTL lookahead overlap, RTL wait/stall, deterministic logical-cycle stripe injection이다.

Worker가 제출된 stripe나 raw work 없이 condition variable에서 기다리는 host wall-clock 동안에는 RTL clock도 진행되지 않는다. 따라서 host wait을 real CPU+NPU end-to-end cycle로 해석하지 않는다.

Global logical cycle, per-work interval, detailed counter, event timestamp의 정확한 source와 edge convention은 [RTL 사이클 측정 구조](docs/RTL_CYCLE_ACCOUNTING.md)를 따른다.

## 빌드와 검증

```bash
make check
make bsv-test
make rtl
make verilator-lint
make yosys-stat
```

`make verify`는 BSC 검증과 대표 RTL 생성을 한 번에 수행한다.

```bash
make verify
```

개별 항목은 다음 명령으로 다시 실행한다.

```bash
make bsv-test-one TOP=mkTbIM2PCore
make rtl-one TOP=mkSynthInt8
```

`make check`는 BSC 없이 architecture 정적 검사와 C++20 reference self-test를 수행한다. 기본 build에는 llama.cpp-gemmini header가 필요 없다.

선택형 Gemmini C++ frontend와 실제 RTL golden은 각각 `make gemmini-frontend-test`, `make gemmini-frontend-real-test`로 검증한다. 계약과 lifetime은 `frontend/README.md`에 기술한다.

### High-level C++ API 사용법

optional frontend는 read-only `llama.cpp-gemmini` checkout의 authoritative header로 build한다. Public frontend mode는 정확히 `FULL`과 `PIPELINE` 두 개뿐이다.

Frontend artifact identity는 `a<activation>-w<weight>-d<dim>`이다. 기존 W8
artifact는 DIM16/DIM32 block size 32, DIM64 block size 64를 유지한다. Native
matched A4/W4와 A16/W16 artifact는 GGUF block layout에 맞춰 모든 DIM에서
block size 32를 사용한다.

- `FULL`: ExSIA가 모든 stripe의 quantization/folding을 완료하면서 post-fold event를 collector에 보존한다. Quantization 성공 뒤 full NPU work를 시작하고, fence 뒤 기존 8-bit cpu-direct RMD를 완료한 다음에만 caller output을 publish한다.
- `PIPELINE`: NPU stream을 먼저 시작한다. 각 stripe의 folding commit 직후 생성된 event를 batch의 quantization 종료까지 미루지 않고 즉시 stream에 publish하여 CPU ExSIA와 NPU 실행을 겹친다. Fence와 기존 8-bit cpu-direct RMD가 모두 성공한 다음에만 staged output을 caller에게 publish한다.

두 mode 모두 post-fold metadata와 activation backing을 fence까지 보존한다. 제3 frontend mode, post-quantization batch publish, RMD 이전 output publish는 계약에 없다.

Non-RMD Q4_0/Q4_H1/Q4_HP1은 A4/W4 artifact에서,
Q16_0/Q16_H1/Q16_HP1은 A16/W16 artifact에서 `FULL`과 `PIPELINE` 모두
실행한다. Production ExSIA는 A8/Q8만 허용한다.

```bash
make gemmini-frontend \
  GEMMINI_ROOT=/path/to/llama.cpp-gemmini \
  GEMMINI_PARAMS_ROOT=/path/to/gemmini-include \
  GEMMINI_FRONTEND_DIM=16
```

Full mode는 `execute` 성공 직후 NPU work를 시작한다. Caller는 `fence`에서 완료 상태와 extended statistics를 받는다.

```cpp
#include "im2p_gemmini_frontend.hpp"

using namespace im2p::gemmini;

ExecuteResult started = execute(&args, Mode::full);
if (!started.status.ok()) {
    // Invalid or unsupported arguments.
}

FenceResult completed = fence(*started.run);
if (!completed.status.ok()) {
    // Worker or RTL execution failure.
}

const im2p_work_stats_extended_t &stats = completed.stats;
```

Stripe mode도 같은 borrowed `ggml_gemmini_args_t`를 사용한다. `StripeReadyEvent`마다 activation row availability를 publish한다. Explicit backpressure가 발생하면 accepted되지 않은 event를 그대로 retry한다.

```cpp
ExecuteResult started = execute(&args, Mode::stripe_pipeline);
if (!started.status.ok()) {
    // Stream startup failure.
}

for (const auto &event : ready_events) {
    Status status;
    do {
        status = submit_stripe(*started.run, event);
    } while (status.code == StatusCode::backpressure);

    if (!status.ok()) {
        // Ordering, run ID, row range, or worker failure.
    }
}

FenceResult completed = fence(*started.run);
```

`ggml_gemmini_args_t`는 matrix shape/layout, activation, weight-format, scale/reconstruction metadata, output metadata, tile metadata를 담는 external work descriptor다. `execute`는 필요한 scalar fields와 pointer identities를 snapshot하고 backing storage는 borrow한다.

Referenced input buffers는 `fence`가 반환되거나 `Run` destruction이 끝날 때까지 alive/immutable 상태여야 한다. output `C`도 같은 기간 alive/exclusively writable 상태여야 한다.

numerical route 상태는 다음과 같다. High-level caller는 raw `progress`/`poll`을 호출하지 않는다. 표는 generic non-RMD frontend route 능력이며 production ExSIA는 A8/Q8만 선택한다.

| route | 상태 |
|---|---|
| `q8_h0` | 지원, raw-compatible numerical execution |
| `q8_0_unpacked_to_h1`, `q8_h1`, `q8_hp1` | 지원, provider 기반 numerical execution |
| `q8_channel`, `q8_channel_dense_sidecar` | 지원, RTL `VectorBypass` 이후 host output에서 channel scale을 한 번 적용 |
| `q8_h2` | **Deprecated**; numerical fallback 없이 `q8_h2 is deprecated` 반환 |
| `q8_hp2` | **Unsupported**; numerical fallback 없이 `q8_hp2 is unsupported` 반환 |

Provider route는 요청된 logical fragment만 native storage에서 읽는다. 전체 weight tensor를 unpack, transpose, materialize하지 않는다. M/N tile, K fragment, block boundary, accumulate 결정은 계속 RTL scheduler가 담당한다.

ExSIA production에서 A4/Q4와 A16/Q16의 RMD scale integration, Q8 H2/HP2,
mixed precision은 TODO이며 fail closed한다. 이를 deferred work queue나 다른
format으로 fallback하지 않는다. Generic frontend의 `q8_h2` Deprecated 및
`q8_hp2` Unsupported 상태도 그대로 유지된다.

## 문서

- [Architecture](docs/ARCHITECTURE.md): 현재 RTL 및 simulator architecture
- [코드 분석 가이드](docs/CODE_ANALYSIS_GUIDE.md): 코드 분석 순서와 작성 규칙
- [검증 가이드](docs/VERIFICATION.md): testbench 범위와 검증 상태
- [Simulator 사용법](sim/README.md): Rust simulator와 raw C ABI
- [C++ frontend](frontend/README.md): Gemmini-compatible high-level frontend
- [SRMD algorithm](ALGORITHM.md): residual GEMM compaction과 row packing

코드 구조의 분석·문서화 순서는 [코드 분석 가이드](docs/CODE_ANALYSIS_GUIDE.md)를 따른다.

## Address-driven full matrix와 stripe scheduling

`IM2PCore` 하나가 `MatmulScheduler`와 `WorkScheduler`를 하나씩 소유한다. 두 scheduler가 M/N tile, K fragment, scale block을 결정하고 A/W/S/C 주소와 tag를 발행한다. Rust는 이 주소를 host-owned view에 resolve해 response를 반환하며 I/J/K scheduling에는 관여하지 않는다.

- `MatmulScheduler`: 전체 matrix/stripe traversal, I/J work 선택, current 및 lookahead stripe, publication FIFO, stripe completion을 관리한다.
- `WorkScheduler`: accumulation work 하나의 K progression과 K fragment, A/W/S preparation, current/next fragment, next-stripe lookahead, accumulate control을 관리한다.
- `ExecuteController`: SystolicArray execution 하나의 row issue, column commit, done을 관리한다.

K fragment는 quantization block boundary를 넘지 않는다.

```text
remaining_in_block = block_size - (k_start % block_size)
k_count = min(DIM, K - k_start, remaining_in_block)
```

`tile_K`는 Gemmini/host metadata다. 실제 RTL K fragment는 `WorkScheduler`가 DIM과 quantization block boundary를 기준으로 결정한다.

전체 행렬 실행에서는 처음부터 모든 activation을 사용할 수 있다.

```text
execute
  -> MatmulScheduler
  -> WorkScheduler
  -> K-fragment generation
  -> A/W/S request
  -> WS execution
  -> Accumulator
  -> output writeback
```

Host는 K loop, weight preload, activation feed를 직접 scheduling하지 않는다.

- `execute_matmul`: 전체 matrix descriptor를 한 번 제출한다. A/W/S/C의 모든 region을 처음부터 사용할 수 있을 뿐, striped mode와 다른 실행 경로를 만들지 않는다.
- `begin_striped_matmul`: 정적 W/S/C metadata를 제출하고 activation stripe를 cooperative하게 publish한다.
- `publish_stripe`: 단순 queue 삽입이 아니라 activation availability event다. 승인된 publish를 받으면 current WS/RC work가 실행 중이어도 바로 다음 stripe의 A, W, 필요한 S request/staging과 weight-bank preload 또는 resident reuse 준비를 시작할 수 있다.
- `npu_ready`: RTL publication FIFO가 새 stripe를 받을 수 있는 상태다.
- `host_available`: publish된 host activation이 아직 stripe completion으로 반환되지 않은 상태다.
- stripe completion: 마지막 C write response가 acknowledge된 뒤에만 발생한다.

```text
CPU                              NPU

LA/SF(s0)
       publish s0 -----------> prepare / execute s0

      LA/SF(s1)
             publish s1 ----> prepare s1
                              || overlap
                           execute s0
                              ||
                              \/
                           execute s1
```

실행 순서는 단일 engine으로 유지한다. current stripe와 바로 다음 stripe만 prepare state를 가질 수 있다.

더 뒤의 published stripe는 FIFO 순서만 유지하며 prepare하지 않는다. Lookahead는 A/W/S와 inactive PE bank만 준비한다. output write와 Accumulator state는 lookahead가 current로 promotion된 뒤에만 갱신한다.

Early-publish regression은 `publish(next) <= firstPrepare(next) < currentCompletion`을 확인한다. 특정 cycle 값은 README에 고정하지 않고 새로 실행한 test 결과에서 관리한다.

Weight stationary PE bank는 두 개다. engine은 active bank를 읽고 lookahead의 host W fetch는 PE 외부의 external staging row에 저장한다.

capture 시점에 final-current-work safety가 성립하고 resident 상태가 정확히 일치하는 bank가 있으면 host fetch와 preload 없이 reuse한다. 아직 safety가 성립하지 않거나 일치하는 bank가 없으면 scheduler의 final-current-work safety point에서만 staging row를 inactive bank에 preload한다. weight base, row stride, J start/count, K start/count가 모두 일치해야 하므로 잘못된 bank를 reuse하지 않는다.

completion까지 일부 W row만 도착했다면 promotion 뒤 도착한 row는 inactive-bank load에 직접 주입하고, 아직 없는 row만 host에 요청해 중복 fetch를 막는다.

Scaled lookahead는 current/next scale cache에서 `(context + J offset, block)`이 일치하는 row를 reuse한다. 일치하는 row가 없으면 current scale traffic이 비어 있고 engine이 실행 중일 때 host S request를 낸다. Promoted execution은 선택한 scale row를 immutable snapshot으로 latch하므로 이후의 prefetch/response가 staggered column 결과를 바꾸지 못한다.

`WorkStats::cross_stripe_overlap_cycles`는 **current engine execution이 active인 cycle에 next-stripe A/W/S fetch 또는 PE preload 중 하나라도 active인 cycle**을 센다. 기존 `activation_`, `weight_`, `scale_overlap_cycles`는 current work 내부의 fragment 준비와 compute가 겹친 시간이며 이 aggregate의 부분 counter가 아니다.

이 값과 모든 wait/timestamp는 RTL logical cycle이고 host wall-clock 시간은 포함하지 않는다. `stripe_host_wait_cycles`는 current stripe의 transition이 끝났지만 다음 published work가 없어 scheduler가 기다린 cycle이다.

`activation_`, `weight_`, `scale_`, `output_wait_cycles`는 각 host channel response wait을, `weight_preload_cycles`는 active execution 중 weight load를 나타낸다. `lookahead_ready_cycle`은 first-fragment A/W/S staging과 필요한 PE bank preload/reuse가 모두 끝난 cycle이다.

Lookahead timestamp는 matmul start를 기준으로 한 RTL cycle number다. `lookahead_publish_cycle`은 두 번째 stripe publication이 RTL에 accept된 cycle이다.

`lookahead_first_activation_cycle`, `lookahead_first_weight_cycle`, `lookahead_weight_preload_cycle`, 또는 `lookahead_scale_cycle` 중 0이 아닌 가장 이른 값에서 `lookahead_publish_cycle`을 빼면 publish-to-first-prepare cycles가 된다. `lookahead_start_cycle - current_stripe_completion_cycle`은 completion-to-next-start transition cycles다.

`lookahead_weight_requests`는 host W fetch 수이고 `lookahead_weight_reuse_hits`는 exact resident-bank reuse 수다. scale의 host request/reuse는 `lookahead_scale_requests`와 `lookahead_scale_reuses`로 구분해 보고한다.


두 API 모두 같은 core/datapath/scheduler stack을 사용한다. `execute_tile` loop, OS thread, async runtime, sleep, wall-clock timing은 high-level scheduling에 사용하지 않는다. DMA, scratchpad, TLB, ROB, RoCC, 두 번째 core/datapath 또는 별도 Rust scheduler도 이 모델 범위가 아니다.
