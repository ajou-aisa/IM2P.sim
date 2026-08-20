# IM2P.sim RTL 사이클 측정 구조

## 원칙

성능 cycle의 source of truth는 `IM2PCore` 내부의 RTL telemetry다. C++ 실행 시간, bridge loop 횟수, host progress 반복 횟수, `std::chrono` 값은 NPU cycle로 환산하지 않는다.

```text
External C++ Host
    execute / submit_stripe / fence
                |
                v
Simulation Bridge
    CLK 구동 / eval / RDY-EN / A-W-S-C memory service
                |
                v
Verilated BSV RTL NPU
    rtlCycleCount
    workCycles / lastCompletedWorkCycles
    detailed telemetry / event timestamps
```

Verilator-generated model은 RTL NPU의 executable model이다. External C++ Host와 Simulation Bridge는 같은 계층이 아니다. Host는 작업을 제출하고 완료를 기다린다.

Bridge는 Verilated model의 clock과 I/O를 구동하며 RTL counter를 읽는다. 모든 보고 cycle은 BSV RTL counter 또는 두 RTL timestamp의 차에서 온다.

## Global logical cycle

`rtlCycleCount`는 reset 해제 후 `IM2PCore`에 인가된 정상 clock period 수다. 하나의 logical cycle은 positive edge 하나를 포함하는 완전한 RTL clock period다.

```text
reset 완료                 rtlCycleCount = 0
eval/getter only           delta = 0
첫 정상 clock period       rtlCycleCount = 1
accepted method pulse      delta = 1
progress_stream(stream, N) delta = N
```

Reset 과정에서 발생한 edge는 logical time에 포함하지 않는다. C++의 `debug_positive_edges`는 bridge가 구동한 정상 positive edge를 검사하는 test/debug-only 값이다. Public performance stats의 source가 아니다.

## Work interval

Work는 `startMatmul`이 실제로 accepted/fired한 edge에서 시작한다. Acceptance edge에서는

```text
workActive = True
workCycles = 0
workStartCycle = acceptance edge 직후 rtlCycleCount
```

Acceptance edge 자체는 latency에서 제외한다. 이후 `workActive`인 각 RTL period를 센다.

마지막 C write response가 수락되고 `MatmulScheduler`의 outstanding work/completion 전이가 끝난 뒤 `IM2PCore`가 `MatrixDone`을 확정하는 terminal edge에서 종료한다. Terminal edge는 포함한다.

```text
terminal edge:
    lastCompletedWorkCycles = workCycles + 1
    workCompletionCycle = terminal edge 직후 rtlCycleCount
    workActive = False

lastCompletedWorkCycles
    == workCompletionCycle - workStartCycle
```

Full mode에서는 high-level start acceptance부터 전체 M/N/K scheduling, writeback, terminal completion까지 하나의 interval이다. Stripe mode도 같은 시작과 종료를 사용하며, RTL clock이 진행되는 동안 stripe/resource를 기다린 cycle을 포함한다. `fence()`는 terminal done 뒤 stable한 `lastCompletedWorkCycles`와 detailed telemetry를 읽은 다음 반환한다.

## Stats source mapping

| Public stats/API | RTL source |
|---|---|
| `im2p_cycle_count`, `Im2pSimulator::cycles` | `IM2PCore.rtlCycleCount` |
| `WorkStats.work_total_cycles` | `IM2PCore.lastCompletedWorkCycles` |
| `activation_wait_cycles` | `activationWaitCycles` |
| `weight_wait_cycles` | `weightWaitCycles` |
| `scale_wait_cycles` | `scaleWaitCycles` |
| `output_wait_cycles` | `outputWaitCycles` |
| `stripe_host_wait_cycles` | `stripeHostWaitCycles` |
| `compute_cycles` | `computeCycles` |
| `drain_cycles` | `drainCycles` |
| `weight_preload_cycles` | `weightPreloadCycles` |
| `activation_overlap_cycles` | `activationOverlapCycles` |
| `weight_overlap_cycles` | `weightOverlapCycles` |
| `scale_overlap_cycles` | `scaleOverlapCycles` |
| `overlap_cycles` | `overlapCycles` |
| `cross_stripe_overlap_cycles` | `crossStripeOverlapCycles` |

새 work가 accepted되면 work별 request, wait, compute, drain, preload, overlap, lookahead counter를 RTL에서 0으로 초기화한다. Rust는 완료 후 RTL 값을 직접 전달한다. 이전 work와의 host-side subtraction으로 performance cycle을 계산하지 않는다.

`execute_tile()`의 `weight_load_cycles`, `compute_cycles`, `total_cycles`는 low-level phase 전후 `rtlCycleCount` snapshot의 차다. Snapshot subtraction은 Rust에서 하지만 양쪽 timestamp의 source는 모두 RTL이다.

## Counter semantics

Detailed counter는 registered-state occupancy를 센다. 같은 cycle에 여러 조건이 참일 수 있으므로 counter는 exclusive하지 않다.

```text
total_cycles
!= compute + drain + waits + overlaps
```

`drain_cycles`는 `compute_cycles`의 부분 구간일 수 있다. A/W/S overlap은 동시에 증가할 수 있고 `overlap_cycles`는 그 조건들의 union이다. `cross_stripe_overlap_cycles`는 current engine execution과 next-stripe A/W/S fetch 또는 PE preload가 겹친 cycle이다.

## Event timestamp timebase

다음 값은 모두 `matrixCycle = rtlCycleCount - matrixStartCycle` 기반의 동일한 work-relative RTL timebase를 쓴다.

```text
lookaheadPublishCycle
lookaheadFirstActivationCycle
lookaheadFirstWeightCycle
lookaheadWeightPreloadCycle
lookaheadScaleCycle
currentStripeCompletionCycle
lookaheadReadyCycle
lookaheadStartCycle
```

`0`은 해당 event가 기록되지 않았음을 나타내는 sentinel이다. Lookahead ordering은 다음 관계를 검증한다.

```text
publish(next) <= firstPrepare(next)
firstPrepare(next) < currentCompletion < nextStart
```

## 포함 범위

- RTL scheduler와 registered control transition
- clock이 진행되는 동안의 resource/request wait
- current/next weight preload와 reuse
- SystolicArray compute와 drain
- VectorUnit과 Accumulator 처리
- RTL output write request/response protocol
- lookahead preparation과 overlap

## 제외 범위와 모델 한계

- C++ host wall-clock, busy loop, sleep, thread scheduling
- native provider weight decode/route factor/output reconstruction wall-clock
- CPU ExSIA LA/SF wall-clock
- host pointer dereference wall-clock
- physical DRAM/cache/scratchpad/interconnect latency
- 실제 ns, GHz, Fmax, silicon latency

High-level worker가 다음 host stripe나 raw RTL work가 없는 상태에서 condition variable을 기다리면 RTL clock을 구동하지 않는다. 이 host wall-clock 구간은 RTL logical-cycle 통계에 포함되지 않는다. CPU와 NPU의 common timebase는 제공하지 않는다.

이 프로젝트는 Verilator 범위만 다룬다. Telemetry synthesis-disable option, FPGA/ASIC top, area/Fmax용 counter gate, STA integration은 이 구조의 범위가 아니다.
