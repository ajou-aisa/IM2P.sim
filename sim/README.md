# IM2P RTL 시뮬레이터

이 크레이트는 재생성된 BSC INT8 RTL을 Verilator로 구동합니다.

```text
BSV -> Verilog -> Verilator C++ -> C ABI -> Rust
```

```bash
make sim-test-int8x16
make sim-test-int8x32
```

각 대상은 `sim/tests/*.rs`에서 자동 검색된 모든 통합 테스트를 실행하기 전에 Verilog와 Verilator 모델을 재생성합니다.

## 호스트 소유 K-블록 스케일 행렬

`W: K × J`와 블록 크기 `B`에 대해 스케일 형상은 다음과 같습니다.

```text
S: ceil(K / B) × J
W[k,j] uses S[floor(k / B),j]
```

Rust는 빌린 `KBlockScaleMatrixView`를 노출합니다.

```rust
KBlockScaleMatrixView {
    values,
    block_size,
    total_k,
    columns,
    row_stride,
    column_offset,
    valid_columns,
    context,
}
```

행렬은 호스트 메모리에 유지된다. C ABI mirror는 동일한 pointer와 layout
metadata를 담는다. Low-level synchronous scale request service는 호출 중 요청된
row 하나만 복사하고 pointer를 보관하지 않는다. Full/stripe high-level API의
borrowed pointer lifetime은 아래 C ABI 절에서 별도로 정의한다. `context`는 cache
generation key다. 호출자는 행렬 내용이나 유효 stride/offset/column mapping이
변경될 때마다 이를 변경해야 한다. Host address 계산은 다음과 같다.

```text
block * row_stride + column_offset
```

요청된 `valid_columns` 값은 복사되고, 나머지 RTL 열은 0으로 패딩됩니다.

## RTL 스케일 스트리밍

`IM2PCore`는 `block = kStart / blockSize`를 계산합니다. 스케일된 실행은 하나의 블록 안에 머물러야 합니다. Core는 context 태그가 지정된 demand/prefetch 요청을 노출하고, 일치하는 행 응답 하나를 수락합니다.

Normal demand/prefetch cache에는 두 row entry가 저장된다.

```text
current: (context, block, S[block,:])
next:    (context, block + 1, S[block + 1,:])
```

동일 block fragment는 current에 hit한다. 순차 block은 next를 promote한다. Demand
miss는 row가 도착할 때까지 execution을 정지한다. 마지막 block은 prefetch를
생성하지 않는다. Reset 또는 context 변경은 두 cache entry를 모두 무효화한다.
실행 중인 row와 immediate lookahead row는 별도 immutable snapshot으로 보관한다.
Synthesis-time K-scale block 수 제한은 없다.

현재 스케줄링은 다른 실행을 시작하기 전에 각 실행을 완전히 drain합니다. `executionScaleRow`는 activation 입력 전에 래치되며, 모든 staggered 열 출력 동안 고정됩니다. 따라서 각 부분 열 `j`는 정확히 `S[block,j]`를 사용하며, prefetch가 진행 중인 실행을 덮어쓸 수 없습니다.

`VectorBypass`는 스케일 뷰가 필요하지 않고, 스케일 요청을 내보내지 않으며, 캐시된 행을 무효화하지 않습니다.

## 논리 사이클 모델

하나의 논리 사이클은 정확히 하나의 상승 에지를 포함하는 완전한 시뮬레이션 RTL 클록 주기입니다. reset 에지는 초기 상태를 설정하고 논리 카운터를 0으로 둡니다. 원시 명령/응답 펄스는 하나의 논리 사이클을 소비하며, 조합 `eval()` 및 요청/상태 getter는 소비하지 않습니다. 제공자는 준비된 A/W/S/C 응답을 동시에 stage하고 같은 에지에서 commit할 수 있으므로, 호스트 함수 호출을 직렬화하는 대신 독립적인 RTL 채널을 따릅니다.

`progress(cycle_budget)`와 `im2p_progress_stream(..., cycle_budget)`는 모든 스케줄러 상태에서 정확히 `cycle_budget` 논리 사이클을 진행시킵니다. 예산이 0이면 관찰만 수행하며 모델을 서비스하거나 클록하지 않습니다. 내부 blocking-loop 제한은 watchdog **반복** 횟수입니다. 정상 서비스 반복은 stage된 provider 에지 하나를 commit하며, 설정/final acknowledgement 펄스는 별도 논리 사이클이고 사이클 기반 통계에 포함됩니다.

RTL logical cycle은 host wall-clock이나 physical time이 아니다. Simulator는
on-core scale cache와 resident weight-bank state를 기능적으로 모델링하지만
host/SoC DRAM, cache timing, scratchpad, DMA, interconnect, CPU execution,
clock frequency model은 포함하지 않는다. Host memory dereference는 기능적이며
zero-time이다. 따라서 이 counter로 CPU/NPU common time, ns, GHz, Fmax, silicon
성능을 확립할 수 없다. Verilator 실행 시간은 host simulation 비용일 뿐이다.

## 통계

`TileStats`는 weight, compute, total-cycle, 유효 MAC/Ops, rate 및 utilization 필드를 유지합니다. `ScaleFetchStats`는 타일별로 다음을 보고합니다.

```text
demand_requests
prefetch_requests
current_hits
next_hits
demand_misses
rows_received
scale_transfer_cycles
scale_wait_cycles
```

`scale_transfer_cycles`는 수락된 RTL 행 응답 사이클을 셉니다. `scale_wait_cycles`는 보류 중인 스케일 실행이 있는 RTL 사이클을 셉니다. 호스트 포인터 역참조 시간은 RTL 사이클이 아닙니다.

`WorkStats::work_total_cycles`는 RTL `lastCompletedWorkCycles`를 직접 읽습니다.
Detailed cycle과 request telemetry는 새 work가 accepted될 때 RTL에서 초기화되고
완료 후 직접 전달됩니다. C++ private counter나 host-side before/after delta가
performance source로 사용되지 않습니다. 특히 `cross_stripe_overlap_cycles`는 현재 엔진이 실행되는 동안 다음 stripe의 A/W/S fetch 또는 PE preload 중 하나라도 활성인 사이클만 셉니다. 반면 `activation_overlap_cycles`, `weight_overlap_cycles`, `scale_overlap_cycles`는 현재 작업의 조각 준비가 compute와 겹치는 것을 보고하며, cross-stripe 집계의 구성 요소가 아닙니다. 호스트 wall-clock 시간은 제외됩니다. `stripe_host_wait_cycles`는 현재 stripe 전환 후 다음 stripe가 publish되지 않았을 때의 대기이며, A/W/S/C 채널 대기는 별도로 보고됩니다. `lookahead_ready_cycle`은 첫 조각의 A/W/S staging과 필요한 PE-bank preload 또는 reuse가 모두 준비된 시점을 기록합니다.

Lookahead 타임스탬프는 matmul별 RTL 사이클입니다. `lookahead_publish_cycle`은 두 번째 stripe publish가 RTL에 수락된 사이클입니다. 해당 사이클부터 `lookahead_first_activation_cycle`, `lookahead_first_weight_cycle`, `lookahead_weight_preload_cycle`, `lookahead_scale_cycle`의 첫 0이 아닌 값까지의 차이는 publish-to-first-prepare 지연입니다. `lookahead_start_cycle - current_stripe_completion_cycle`은 completion-to-next-start 전환입니다. `lookahead_weight_requests`와 `lookahead_weight_reuse_hits`, 그리고 `lookahead_scale_requests`와 `lookahead_scale_reuses`는 호스트 fetch와 정확한 reuse를 구분합니다.

원래의 고정 크기 `im2p_work_stats_t`와 그 진입점은 바이너리 레이아웃을 유지합니다. Lookahead telemetry는 `im2p_work_stats_extended_t`, `im2p_execute_matmul_extended`, `im2p_finish_stream_extended`를 통해 사용할 수 있습니다.

전체 source mapping과 start/completion edge convention은
[`docs/RTL_CYCLE_ACCOUNTING.md`](../docs/RTL_CYCLE_ACCOUNTING.md)에 정의합니다.

## 행렬 및 협력적 stripe API

Blocking Rust:

```rust
simulator.execute_matmul(&work, &mut output)?;
simulator.execute_matmul_layout(&work, &mut output, layout)?;
```

`MatrixView::new(values, rows, columns, row_stride)` 및 `MatrixViewMut::new(...)`는 A/W/C 레이아웃을 정의합니다. 각 stride의 단위는 요소이며 논리 열 수(A: K, W/C: N) 이상이어야 합니다. 마지막으로 읽거나 쓸 수 있는 요소는 `(rows - 1) * row_stride + columns - 1`입니다. `MatmulLayout`은 `tile_i_rows`와 `tile_j_columns`을 선택하며 각각 `1..=sim.dim()` 범위입니다. `MatmulWork`는 stride가 적용된 activation, weight, 선택적 scale 뷰를 빌리고, RTL은 모든 I/J/K 조각과 A/W/S/C 주소를 제공합니다.

협력적 Rust:

```text
begin_striped_matmul[_layout](static W/S/C metadata)
publish_stripe[_layout](completed CPU activation rows)
progress(logical cycle budget)
pending_activation_row / supply_activation_row
pending_output_row / take_output_row / acknowledge_output_row
poll_completed
finish
```

`StripeLayout`은 W/C 행 stride와 I/J 타일링을 제공하며, 기본 레이아웃은 packed입니다. `publish_stripe_layout`은 K 이상이어야 하는 activation 행 stride를 제공합니다. `publish_stripe`는 단순한 큐 push가 아니라 activation-availability 이벤트입니다. 수락되면 하나의 현재 WS/RC 엔진이 순서를 유지하는 동안 즉시 다음 stripe의 A/W/S staging과 resident-weight reuse 또는 inactive-bank preload를 활성화합니다. 현재 항목 하나와 준비된 lookahead 하나가 있으며, 그보다 깊은 수락된 stripe는 FIFO에 남습니다. lookahead는 승격 전까지 C에 쓰거나 Accumulator를 갱신하지 않습니다. 전체 행렬 실행은 모든 A/W/S/C 영역이 처음부터 사용 가능한 동일한 RTL 경로를 사용합니다.

```text
CPU: publish s0 ----------------------- publish s1 ------------------- retain s1 A
NPU: [current WS/RC] ================== [prepare s1: A/W/S + PE bank] -- promote --> [s1 WS/RC]
                                         one engine; output/accumulator only after promotion
```

`npu_ready()`는 RTL publication FIFO 준비 상태를 보고합니다. `host_available()`은 불완료 stripe가 여전히 소유한 publish된 호스트 데이터를 보고합니다. stripe가 수락되기 전에는 activation read가 발생하지 않습니다. 일치하는 모든 C write가 acknowledge된 후에만 completion을 관찰할 수 있습니다. correctness에는 thread, runtime, sleep, polling delay 또는 CPU wall clock이 참여하지 않습니다.

## C ABI 및 포인터 소유권

공개 헤더는 `sim/include/im2p_sim.h`입니다. C 상태 값은 잘못된 contract(`IM2P_INVALID_LAYOUT`), 이미 소유된 simulator(`IM2P_UNFINISHED_STREAM`), publication backpressure/duplicate/late 이벤트 및 일반 runtime 실행 실패(`IM2P_ERROR`)를 구분합니다. runtime 실패는 layout 오류로 축소되지 않습니다.

원시 simulator API는 `execute_matmul` 또는 `begin_striped_matmul`/`publish`/`progress`/`poll`/`finish`입니다. 선택적 C++ frontend는 별도의 `execute`/`submit_stripe`/`fence` surface 뒤에서 이 순서를 소유합니다.

`im2p_execute_matmul`은 호출 동안에만 전체 행렬 포인터를 빌립니다. `im2p_begin_striped_matmul_ex`는 상태를 반환하고 stream 포인터를 씁니다. 비-`_ex` 형식은 해당 포인터를 직접 반환합니다. C에서 `tile_i_rows` 또는 `tile_j_columns`가 0이면 simulator dimension을 선택하며, 0이 아닌 값은 이에 맞아야 합니다. A/W/C stride는 요소 stride이고 padding을 포함할 수 있습니다. 전체 A stride는 K 이상, W와 C stride는 N 이상이어야 하며, striped W/C도 같은 contract를 따르고 publish된 각 stripe의 A stride는 K 이상이어야 합니다. simulator가 owner에서 가져와진 뒤 하위 계층 begin이 거부되면 recovery 경로를 통해 오류와 simulator를 모두 반환합니다. 오류 상태가 반환되기 전에 C owner가 복원되므로 동일한 handle은 이후 begin, execution, finish 및 destruction에도 유효합니다.

stream의 경우 W/S/C 포인터와 그 stride는 `im2p_finish_stream` 또는 `im2p_destroy_stream`까지 유효해야 합니다. 성공한 `im2p_publish_stripe`는 다음 논리 사이클에 RTL read를 허용하므로, A 포인터와 activation stride는 `im2p_poll_completed`가 반환하는 일치하는 completion까지 유효해야 합니다. 브리지는 descriptor를 복사하지만 서비스하기 위해 이 빌린 영역을 보관하므로, 호출자는 수명이 끝나기 전에 이를 이동, 해제하거나 호환되지 않게 변경해서는 안 됩니다. stream은 공유 simulator 소유권을 유지하므로, 원래 `im2p_sim_t` handle을 파괴해도 진행 중인 stream은 무효화되지 않습니다.

PE array에는 여전히 두 개의 weight bank가 있습니다. resident가 아닌 lookahead W는 PE bank 밖의 external staging으로 호스트에서 fetch된 뒤 safe point에서만 inactive bank에 preload됩니다. 정확한 resident match(base, stride, J 및 K 범위)는 final-current-work safety predicate가 capture 시점에 이미 충족될 때에만 fetch/preload 없이 bank를 reuse하며, 그렇지 않으면 준비 과정이 보수적으로 host fetch를 사용합니다. promotion 전에 W의 일부만 도착하면 수신된 행은 promotion된 inactive-bank load에 직접 공급되고, 누락된 행만 host request를 생성합니다. 스케일된 lookahead도 정확한 `(context + J offset, block)` current/next cache 행만 reuse하며, 그 외에는 S를 fetch합니다. promotion은 불변 scale execution snapshot을 래치하여 이후 응답이 진행 중인 staggered output을 변경하지 못하게 합니다.

Integration test 구성은 [simulator test 가이드](tests/README.md), RTL 및 simulator
구조는 [architecture 문서](../docs/ARCHITECTURE.md), high-level adapter는
[C++ frontend](../frontend/README.md)에서 확인한다.
