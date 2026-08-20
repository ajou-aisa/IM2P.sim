# IM2P RTL 시뮬레이터

이 크레이트는 재생성한 BSC INT8 RTL을 Verilator로 구동한다.

```text
BSV -> Verilog -> Verilator C++ -> C ABI -> Rust
```

```bash
make sim-test-int8x16
make sim-test-int8x32
```

각 대상은 `sim/tests/*.rs`에서 자동 검색한 모든 통합 테스트를 실행하기 전에 Verilog와 Verilator 모델을 재생성한다.

## 호스트 소유 K-블록 스케일 행렬

`W: K × J`와 블록 크기 `B`의 스케일 형상은 다음과 같다.

```text
S: ceil(K / B) × J
W[k,j] uses S[floor(k / B),j]
```

Rust는 차용한 `KBlockScaleMatrixView`를 노출한다.

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

행렬은 호스트 메모리에 유지된다. C ABI mirror에는 같은 pointer와 layout metadata가 들어 있다. Low-level synchronous scale request service는 호출 중 요청받은 row 하나만 복사하고 pointer는 보관하지 않는다.

Full/stripe high-level API가 차용한 pointer의 수명은 아래 C ABI 절에서 별도로 정의한다. `context`는 cache generation key다. 행렬 내용이나 유효한 stride/offset/column mapping이 바뀔 때마다 호출자가 이 값도 변경해야 한다.

Host address는 다음과 같이 계산한다.

```text
block * row_stride + column_offset
```

요청받은 `valid_columns` 값은 복사하고 나머지 RTL 열은 0으로 패딩한다.

## RTL 스케일 스트리밍

`IM2PCore`는 `block = kStart / blockSize`를 계산한다. 스케일을 적용한 실행은 블록 하나의 범위 안에 있어야 한다. Core는 context 태그가 지정된 demand/prefetch 요청을 노출하고 일치하는 행 응답 하나를 수락한다.

Normal demand/prefetch cache는 두 개의 row entry를 저장한다.

```text
current: (context, block, S[block,:])
next:    (context, block + 1, S[block + 1,:])
```

같은 block의 fragment는 current에서 hit한다. 다음 block으로 순차 접근하면 next를 promote한다. Demand miss가 발생하면 row가 도착할 때까지 execution을 멈춘다.

마지막 block에서는 prefetch를 생성하지 않는다. Reset하거나 context를 변경하면 두 cache entry를 모두 무효화한다. 실행 중인 row와 immediate lookahead row는 각각 별도의 immutable snapshot으로 보관한다.

Synthesis-time K-scale block 수에는 제한이 없다.

현재 scheduler는 각 실행을 완전히 drain한 뒤 다음 실행을 시작한다. `executionScaleRow`는 activation 입력 전에 래치되며 모든 staggered 열이 출력되는 동안 고정된다. 따라서 각 부분 열 `j`는 정확히 `S[block,j]`를 사용하며, prefetch가 진행 중인 실행을 덮어쓸 수 없다.

`VectorBypass`에는 스케일 뷰가 필요 없다. 스케일 요청을 내보내지 않으며 캐시된 행도 무효화하지 않는다.

## 논리 사이클 모델

논리 사이클 하나는 상승 에지 하나를 포함하는 완전한 시뮬레이션 RTL 클록 주기다. reset 에지는 초기 상태를 설정하고 논리 카운터를 0으로 둔다. 원시 명령/응답 펄스는 논리 사이클 하나를 소비하지만, 조합 `eval()`과 요청/상태 getter는 소비하지 않는다.

제공자는 준비된 A/W/S/C 응답을 동시에 stage하고 같은 에지에서 commit할 수 있다. 따라서 호스트 함수 호출을 직렬화하지 않고 독립된 RTL 채널을 따른다.

`progress(cycle_budget)`와 `im2p_progress_stream(..., cycle_budget)`는 scheduler의 상태와 관계없이 정확히 `cycle_budget`만큼 논리 사이클을 진행한다. 예산이 0이면 관찰만 수행하며 모델을 서비스하거나 클록을 진행하지 않는다. 내부 blocking-loop 제한값은 watchdog **반복** 횟수다.

정상적인 service 반복은 stage된 provider 에지 하나를 commit한다. 설정/final acknowledgement 펄스는 별도의 논리 사이클이며 사이클 기반 통계에 포함한다.

RTL logical cycle은 host wall-clock이나 physical time이 아니다. Simulator는 on-core scale cache와 resident weight-bank state를 기능적으로 모델링하지만 host/SoC DRAM, cache timing, scratchpad, DMA, interconnect, CPU execution, clock frequency model은 포함하지 않는다. Host memory dereference는 기능적으로 처리하며 zero-time이다.

이 counter만으로는 CPU/NPU common time, ns, GHz, Fmax, silicon 성능을 확립할 수 없다. Verilator 실행 시간은 host simulation 비용일 뿐이다.

## 통계

`TileStats`는 weight, compute, total-cycle, 유효 MAC/Ops, rate, utilization 필드를 유지한다. `ScaleFetchStats`는 타일별로 다음 항목을 보고한다.

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

`scale_transfer_cycles`는 수락한 RTL 행 응답 사이클 수다. `scale_wait_cycles`는 보류 중인 스케일 실행이 있는 RTL 사이클 수다. 호스트 포인터 역참조 시간은 RTL 사이클이 아니다.

`WorkStats::work_total_cycles`는 RTL `lastCompletedWorkCycles`를 직접 읽는다. Detailed cycle과 request telemetry는 새 work가 accepted될 때 RTL에서 초기화하고 완료 후 직접 전달한다. C++ private counter나 host-side before/after delta는 performance source로 사용하지 않는다.

특히 `cross_stripe_overlap_cycles`는 현재 엔진이 실행되는 동안 다음 stripe의 A/W/S fetch 또는 PE preload 중 하나라도 활성화된 사이클만 센다. 반면 `activation_overlap_cycles`, `weight_overlap_cycles`, `scale_overlap_cycles`는 현재 작업의 조각 준비와 compute가 겹치는 구간을 보고하며 cross-stripe 집계에는 포함하지 않는다. 호스트 wall-clock 시간도 제외한다.

`stripe_host_wait_cycles`는 현재 stripe로 전환한 후 다음 stripe가 publish되지 않아 대기한 시간이며, A/W/S/C 채널 대기는 따로 보고한다. `lookahead_ready_cycle`은 첫 조각의 A/W/S staging과 필요한 PE-bank preload 또는 reuse가 모두 준비된 시점을 기록한다.

Lookahead 타임스탬프는 matmul별 RTL 사이클이다. `lookahead_publish_cycle`은 두 번째 stripe publish를 RTL이 수락한 사이클이다. 이 사이클부터 `lookahead_first_activation_cycle`, `lookahead_first_weight_cycle`, `lookahead_weight_preload_cycle`, `lookahead_scale_cycle`에서 처음으로 0이 아닌 값이 나올 때까지의 차이가 publish-to-first-prepare 지연이다.

`lookahead_start_cycle - current_stripe_completion_cycle`은 completion-to-next-start 전환을 나타낸다. `lookahead_weight_requests`와 `lookahead_weight_reuse_hits`, `lookahead_scale_requests`와 `lookahead_scale_reuses`는 호스트 fetch와 정확한 reuse를 구분한다.

기존 고정 크기 `im2p_work_stats_t`와 관련 진입점은 바이너리 레이아웃을 유지한다. Lookahead telemetry는 `im2p_work_stats_extended_t`, `im2p_execute_matmul_extended`, `im2p_finish_stream_extended`를 통해 사용할 수 있다.

전체 source mapping과 start/completion edge convention은 [`docs/RTL_CYCLE_ACCOUNTING.md`](../docs/RTL_CYCLE_ACCOUNTING.md)에 정의되어 있다.

## 행렬 및 협력적 stripe API

Blocking Rust:

```rust
simulator.execute_matmul(&work, &mut output)?;
simulator.execute_matmul_layout(&work, &mut output, layout)?;
```

`MatrixView::new(values, rows, columns, row_stride)`와 `MatrixViewMut::new(...)`는 A/W/C 레이아웃을 정의한다. 각 stride의 단위는 요소이며 논리 열 수(A: K, W/C: N) 이상이어야 한다. 마지막으로 읽거나 쓸 수 있는 요소는 `(rows - 1) * row_stride + columns - 1`이다.

`MatmulLayout`은 `tile_i_rows`와 `tile_j_columns`을 선택하며 각각 `1..=sim.dim()` 범위에 있어야 한다. `MatmulWork`는 stride를 적용한 activation, weight, 선택적 scale 뷰를 구성하고, RTL은 모든 I/J/K 조각과 A/W/S/C 주소를 제공한다.

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

`StripeLayout`은 W/C 행 stride와 I/J 타일링을 제공하며 기본 레이아웃은 packed다. `publish_stripe_layout`은 K 이상인 activation 행 stride를 제공한다. `publish_stripe`는 단순한 큐 push가 아니라 activation-availability 이벤트다.

이벤트를 수락하면 현재 WS/RC 엔진 하나가 순서를 유지하는 동안 다음 stripe의 A/W/S staging과 resident-weight reuse 또는 inactive-bank preload를 즉시 활성화한다. 현재 항목 하나와 준비된 lookahead 하나를 두며, 이보다 뒤에 수락한 stripe는 FIFO에 남는다. lookahead는 승격되기 전까지 C에 쓰거나 Accumulator를 갱신하지 않는다.

전체 행렬 실행도 모든 A/W/S/C 영역을 처음부터 사용할 수 있는 같은 RTL 경로를 사용한다.

```text
CPU: publish s0 ----------------------- publish s1 ------------------- retain s1 A
NPU: [current WS/RC] ================== [prepare s1: A/W/S + PE bank] -- promote --> [s1 WS/RC]
                                         one engine; output/accumulator only after promotion
```

`npu_ready()`는 RTL publication FIFO의 준비 상태를 보고한다. `host_available()`은 완료되지 않은 stripe가 여전히 소유한 publish된 호스트 데이터를 보고한다. stripe를 수락하기 전에는 activation read가 발생하지 않는다.

일치하는 모든 C write를 acknowledge한 뒤에만 completion을 관찰할 수 있다. correctness에는 thread, runtime, sleep, polling delay, CPU wall clock이 관여하지 않는다.

## C ABI 및 포인터 소유권

공개 헤더는 `sim/include/im2p_sim.h`다. C 상태 값은 잘못된 contract(`IM2P_INVALID_LAYOUT`), 이미 소유된 simulator(`IM2P_UNFINISHED_STREAM`), publication backpressure/duplicate/late 이벤트, 일반 runtime 실행 실패(`IM2P_ERROR`)를 구분한다. runtime 실패를 layout 오류로 축소하지 않는다.

원시 simulator API는 `execute_matmul` 또는 `begin_striped_matmul`/`publish`/`progress`/`poll`/`finish`다. 선택적 C++ frontend는 별도의 `execute`/`submit_stripe`/`fence` 인터페이스 뒤에서 이 순서를 관리한다.

`im2p_execute_matmul`은 호출하는 동안에만 전체 행렬 포인터를 차용한다. `im2p_begin_striped_matmul_ex`는 상태를 반환하고 stream 포인터를 쓴다. 비-`_ex` 형식은 이 포인터를 직접 반환한다.

C에서 `tile_i_rows` 또는 `tile_j_columns`가 0이면 simulator dimension을 선택하며, 0이 아닌 값은 이 dimension과 일치해야 한다. A/W/C stride의 단위는 요소이며 padding을 포함할 수 있다. 전체 A stride는 K 이상이고 W와 C stride는 N 이상이어야 한다.

striped W/C에도 같은 contract를 적용하며, publish한 각 stripe의 A stride는 K 이상이어야 한다. simulator의 소유권을 owner에서 가져온 뒤 하위 계층이 begin을 거부하면 recovery 경로가 오류와 simulator를 모두 반환한다. 오류 상태를 반환하기 전에 C owner를 복원하므로, 같은 handle은 이후 begin, execution, finish, destruction에도 유효하다.

stream에서는 W/S/C 포인터와 각 stride가 `im2p_finish_stream` 또는 `im2p_destroy_stream`을 호출할 때까지 유효해야 한다. 성공한 `im2p_publish_stripe`는 다음 논리 사이클부터 RTL read를 허용한다. 따라서 A 포인터와 activation stride는 `im2p_poll_completed`가 일치하는 completion을 반환할 때까지 유효해야 한다.

브리지는 descriptor를 복사하지만 서비스를 위해 차용한 영역을 보관한다. 호출자는 수명이 끝나기 전에 이 영역을 이동하거나 해제하거나 호환되지 않는 방식으로 변경해서는 안 된다. stream은 simulator의 공유 소유권을 유지하므로 원래 `im2p_sim_t` handle을 파괴해도 진행 중인 stream은 무효화되지 않는다.

PE array에는 weight bank가 두 개 있다. resident가 아닌 lookahead W는 호스트에서 PE bank 밖의 external staging으로 fetch한 뒤 safe point에서만 inactive bank에 preload한다. 정확한 resident match(base, stride, J 및 K 범위)는 capture 시점에 final-current-work safety predicate를 이미 충족한 경우에만 fetch/preload 없이 bank를 reuse한다.

그렇지 않으면 준비 과정에서 보수적으로 host fetch를 사용한다. promotion 전에 W의 일부만 도착하면 수신한 행은 promotion된 inactive-bank load에 직접 공급하고, 누락된 행에 대해서만 host request를 생성한다. 스케일을 적용한 lookahead도 정확한 `(context + J offset, block)` current/next cache 행만 reuse하고 나머지는 S를 fetch한다.

promotion은 불변 scale execution snapshot을 래치하므로 이후 응답이 진행 중인 staggered output을 변경할 수 없다.

Integration test 구성은 [simulator test 가이드](tests/README.md), RTL 및 simulator 구조는 [architecture 문서](../docs/ARCHITECTURE.md), high-level adapter는 [C++ frontend](../frontend/README.md)에서 확인한다.
