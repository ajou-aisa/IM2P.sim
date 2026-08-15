# 선택적 Gemmini C++ 프런트엔드

`frontend/include/im2p_gemmini_frontend.hpp`는 `ggml_gemmini_args_t`를 기존
IM2P C ABI에 연결하는, 시뮬레이터가 소유하는 선택적 어댑터입니다. C ABI, 통계
레이아웃, RTL 또는 수치 동작은 변경하지 않습니다. 기본 IM2P 빌드는 llama 헤더를
포함하거나 요구하지 않습니다.

호출자 표면은 `im2p::gemmini`에서 다음 세 작업으로 구성됩니다.

- `execute(args[, mode, options])`는 `Run`을 만들고 아래에 나열한 선택된 스칼라
  값과 포인터 정체성만 복사합니다. 완전한 `ggml_gemmini_args_t`를 스냅샷하지
  않습니다. args 객체 자체는 이 호출 중에만 필요하며, 백킹 버퍼는 복사하지
  않습니다.
- `submit_stripe(run, event)`는 순서가 있는 범위와 첫 이벤트의 `run_id`를
  검증한 뒤, `run_id`, `stripe_id`, `slot`, `row_begin`, `row_end`만 복사합니다.
  타이밍/프로파일링 필드, 이벤트, 해당 `rmd_packet` 또는 ExSIA 슬롯 소유권은
  보존하지 않습니다. 프런트엔드 `backpressure` 결과는 이벤트가 수락되지 않았음을
  의미합니다. 호출자는 자체 데이터를 보유하고, 용량이 해제된 후
  `submit_stripe`를 재시도해야 합니다.
- `fence(run)`는 제출을 닫고 고정된 최종 상태와 C ABI가 생성한 정확한
  `im2p_work_stats_extended_t`를 반환합니다. 이 함수는 멱등적이며 동시 호출에도
  안전합니다.

이 세 고수준 작업은 worker가 소유하는 완전한 원시 저수준 C ABI 시퀀스와
분리되어 있습니다.

- 전체 행렬 작업용 `execute_matmul`
- 원시 스트림 생성용 `begin_striped_matmul`
- activation 가용성 공개용 `publish_stripe`
- 논리 RTL 사이클 진행용 `progress_stream`
- 완료된 stripe 백킹 스토리지 해제용 `poll_completed`
- 스트림 완료 및 통계 수집용 `finish_stream`

원시 ABI와 호환되는 `q8_h0`(`A[M,K]` INT8, `B[K,N]` INT8, 완전한 INT32
`C[M,N]`, 전치 없음, `repeating_bias`, `D` bias, activation 또는 float scaling
없음)만 실행됩니다. 전체 모드에서 A와 B는 Run 수명 동안 유효하고 변경되지 않아야
하는 차용 입력 영역입니다. 파이프라인 모드에서 B도 같은 규칙을 따릅니다. A 행은
해당 stripe가 수락되기 전에 채울 수 있지만, 제출된 바이트는 이후 `fence` 또는 Run
소멸 시점까지 유효하고 변경되지 않아야 합니다. C는 Run이 차용하는 출력 영역으로,
유효해야 하며 Run만 단독으로 쓸 수 있어야 합니다. `fence`가 반환되거나 Run이
소멸되기 전에는 호출자가 C를 동시에 읽거나 써서는 안 됩니다. 프런트엔드는
권위 있는 Gemmini `has_*`/route helper를 사용해 H1, HP1, HP2, channel-direct 및
channel-sidecar 계약을 분류하고 선택된 메타데이터를 보존한 뒤 `unsupported_route`를
반환합니다. H2 메타데이터도 internal/test route-contract 검사에 맞게 분류 및
보존하지만 public inspection API는 제공하지 않는다. `q8_h2`는
**Deprecated**이며 진단 `q8_h2 is deprecated`와 함께 `unsupported_route`를
반환합니다. 지원되지 않거나 **Deprecated** 상태인 어떤 경로도 worker를 시작하거나
원시 `q8_h0`로 대체 실행하지 않습니다. 프런트엔드는 전치, 언패킹, 역양자화 또는
전체 operand 복사를 수행하지 않습니다.

정확히 선택되는 스칼라는 `I`, `J`, `K`, `sA`, `sB`, `sC`, `sD`,
`activation_row_offset`, `activation_rows_per_stripe`, `block_size_k`, `tile_I`,
`tile_J`, `tile_K`, `blocks_K`, `blocks_J`, `blocks_I`, `stripe_J`,
`q8_h1_block_count`, `q8_h1_rows`, `blocks_per_row`, `q8_h2_block_count`,
`q8_h2_blocks_per_row`, `q8_hp1_block_count`, `q8_hp1_blocks_per_row`,
`q8_hp2_block_count`, `q8_hp2_blocks_per_row`, `weight_channel_scale_count`,
`q8_channel_row_stride`, `q8_channel_row_count`, `col_stride_f_out`,
`stride_f_out`, `weight_format`, `scale_B`, `scale_D`,
`scale`, `bert_scale`, `transpose_A`, `transpose_B`, `full_C`, `low_D`,
`repeating_bias`, `weight_i8_scale_active`, `act`입니다. 정확히 선택되는 포인터는
`A`, `B`, `C`, `D`, `A_fp32`, `B_fp32`, `B_blocks`, `B_scales`,
`weight_channel_scales`, `q8_channel_row_base`, `q8_h1_blocks`,
`q8_h2_blocks`, `q8_hp1_blocks`, `q8_hp2_blocks`, `c_b`, `s_rf`, `R`,
`s_rf_stripe`, `R_stripe`, `f_out`, `model_arch`,
`exsia_stripe_ready_sink`, `unpacked.blocks`입니다. 지원되지 않거나 **Deprecated**
상태인 경로에 속한 pointer selection은 internal/test route-contract 검사
목적으로만 보존되며 처리되지 않는다.

`activation_row_offset`는 메타데이터로만 복사됩니다. 원시 ABI descriptor는 이를
사용하지 않으므로, 실행하려면 A가 이미 첫 activation 행을 가리켜야 합니다.
`tile_I`와 `tile_J`는 Gemmini tile 수입니다. 각각 `DIM`을 곱한 뒤 문제 범위와 RTL
`DIM` tile 하나로 제한합니다(0은 하나의 수를 의미하며, 곱셈 오버플로는 거부됩니다).
`tile_K`는 메타데이터로만 복사되며 원시 ABI의 reduction tiling을 변경하지 않습니다.
K 실행은 `K`와 `block_size_k`로 결정됩니다.

파이프라인 모드에는 모든 원시 simulator 및 stream 호출을 단독으로 소유하는 전용
worker 하나가 있습니다. 호출자 큐는 `Options::queue_capacity`로 제한되며
프런트엔드 backpressure를 반환합니다. 원시 `IM2P_BACKPRESSURE`는 동일한 밀집 이벤트
메타데이터를 유지하고 RTL logical cycle 하나를 진행한 뒤 완료를 poll하고
재시도하는 방식으로 내부 처리됩니다. RTL logical cycle 하나는 상승 에지 하나를
포함하는 완전한 RTL clock period 하나다. `im2p_progress_stream(stream, 1)`는
동시에 준비된 A/W/S/C
응답을 포함한 모든 scheduler 상태에서 정확히 그 주기 하나를 진행합니다. 원시 작업이
flight 상태로 남아 있는 동안 worker는 대기하지 않습니다. 논리 완료가 영구적으로
부족한 경우, 보수적인 논리 한도(최소 65536 사이클 또는 그보다 큰 구성값
`Options::max_stalled_cycles`)에서 종료합니다. 이 한도는 검증된
M=1,N=64,K=4096 RTL 실행을 포괄합니다. watchdog은 worker의 단조 증가하는 matched
completion 카운터가 진행될 때만 재설정됩니다. 호출자가 큐 점유를 변경해도 진행을
숨길 수 없습니다. 따라서 `max_stalled_cycles`는 matched completion 없이 worker가
진행하는 반복 횟수를 제한하며(각 `progress` 호출은 정확히 논리 사이클 하나), 경과한
호스트 시간을 제한하지 않습니다. 스케줄링에는 wall-clock sleep이 관여하지 않습니다.
제출된 stripe나 원시 작업이 없으면 worker는 condition variable에서 기다리고 RTL
클록을 진행하지 않습니다. 이 호스트 wall-clock 대기는 RTL 논리 대기 사이클이
아닙니다.
Model은 on-core scale cache와 resident weight-bank state를 기능적으로
포함하지만 CPU execution, host/SoC DRAM, cache timing, scratchpad, DMA,
interconnect, clock frequency를 포함하지 않는다. Host pointer access는
zero-time이다. Frontend/Verilator runtime 및 RTL counter로는
CPU/NPU 공통 시간, 물리적 ns/GHz/Fmax 또는 silicon 성능을 확립할 수 없습니다.
완료되지 않은 fence는 결정론적으로 실패하며 원시 stream을 finish하지 않고 파기합니다.

## 빌드 및 검증

독립형 기본 빌드는 의존성이 없습니다.

```sh
make check
```

인접한 Gemmini checkout과 생성된 parameter header를 사용할 수 있을 때만 집중된
adapter 계약 테스트를 빌드합니다.

```sh
make gemmini-frontend-test
# 또는 일반 check 의존성 그래프에 포함합니다.
make check ENABLE_GEMMINI_FRONTEND=1
```

DIM16 full/stripe RTL golden 및 lookahead 검사는 다음으로 실행합니다.

```sh
make gemmini-frontend-real-test
make gemmini-frontend-real-test GEMMINI_FRONTEND_DIM=32
```

인접하지 않은 layout에서는 `GEMMINI_ROOT`와 `GEMMINI_PARAMS_ROOT`를 재정의합니다.
public header의 forward-declaration test는 의도적으로 Gemmini include path 없이
컴파일됩니다.

Raw simulator 계약은 [simulator 사용법](../sim/README.md), 전체 project 구조는
[root README](../README.md)에서 확인한다.
