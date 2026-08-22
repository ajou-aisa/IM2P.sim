# 선택적 Gemmini C++ 프런트엔드

`frontend/include/im2p_gemmini_frontend.hpp`는 `ggml_gemmini_args_t`를 IM2P C ABI에 연결하는 선택적 어댑터이며, 시뮬레이터가 이를 소유한다. ABI v3의 signed-64 provider transport를 사용하되 frozen ABI v2 layout과 raw signed-32 output은 유지한다. 기본 IM2P 빌드는 llama 헤더를 포함하지도 요구하지도 않는다.

`im2p::gemmini`가 공개하는 frontend mode는 정확히 두 개다.

- `FULL`: 모든 ExSIA stripe의 quantization/folding이 끝날 때까지 post-fold event를 수집한다. 성공 뒤 full NPU descriptor를 시작하고 fence 및 기존 8-bit RMD가 성공한 후 caller output을 한 번 publish한다.
- `PIPELINE`: NPU stream을 먼저 시작한다. Producer가 stripe folding을 commit할 때마다 post-fold event를 즉시 submit하며 quantization 전체 종료 뒤 batch publish하지 않는다. Fence 및 기존 8-bit RMD가 성공하기 전까지 output은 frontend staging에만 있다.

두 mode 외 제3 mode나 deferred execution mode는 없다.

`im2p::gemmini`가 호출자에게 제공하는 인터페이스는 다음 세 작업으로 구성된다.

- `execute(args[, mode, options])`는 `Run`을 만들고 아래에 나열한 스칼라 값과 포인터만 선택해 복사한다. `ggml_gemmini_args_t` 전체를 스냅샷하지 않는다. args 객체 자체는 이 호출 동안에만 필요하며, 기반 버퍼는 복사하지 않는다.
- `submit_stripe(run, event)`는 범위의 순서와 첫 이벤트의 `run_id`를 검증한 뒤 `run_id`, `stripe_id`, `slot`, `row_begin`, `row_end`만 복사한다. 타이밍/프로파일링 필드나 이벤트를 보존하지 않으며, 해당 `rmd_packet` 또는 ExSIA 슬롯의 소유권도 유지하지 않는다. 프런트엔드가 `backpressure`를 반환하면 이벤트가 수락되지 않은 것이다. 호출자는 데이터를 계속 보유하고 있다가 용량이 확보되면 `submit_stripe`를 다시 호출해야 한다.
- `fence(run)`는 제출을 닫고 확정된 최종 상태와 C ABI가 생성한 `im2p_work_stats_extended_t`를 그대로 반환한다. 멱등성을 보장하며 동시에 호출해도 안전하다.

이 세 고수준 작업은 worker가 전담하는 원시 저수준 C ABI 시퀀스와 분리되어 있다.

- 전체 행렬 작업용 `execute_matmul`
- 원시 스트림 생성용 `begin_striped_matmul`
- activation 가용성 공개용 `publish_stripe`
- 논리 RTL 사이클 진행용 `progress_stream`
- 완료된 stripe 백킹 스토리지 해제용 `poll_completed`
- 스트림 완료 및 통계 수집용 `finish_stream`

현재 production ExSIA route는 A8/Q8만 지원한다. A4/Q4, A16/Q16, Q8 H2/HP2 및 mixed precision은 TODO이며 worker 시작 전에 fail closed한다. 이 format들을 큐에 defer하거나 raw route로 fallback하지 않는다.

Generic frontend에서는 원시 ABI와 호환되는 `q8_h0`(`A[M,K]` INT8, `B[K,N]` INT8, 완전한 INT32 `C[M,N]`, 전치 없음, `repeating_bias`, `D` bias, activation 또는 float scaling 없음)과 `q8_0_unpacked_to_h1`, `q8_h1`, `q8_hp1`, `q8_channel`, `q8_channel_dense_sidecar`를 수치 실행 경로에서 지원한다. 전체 모드에서 A와 B는 Run 수명 동안 유효하고 변경되지 않아야 하는 차용 입력 영역이다. 파이프라인 모드의 B에도 같은 규칙을 적용한다.

A 행은 해당 stripe가 수락되기 전에 채울 수 있지만, 제출한 바이트는 이후 `fence`가 반환되거나 Run이 소멸할 때까지 유효하고 변경되지 않아야 한다. C는 Run이 차용하는 출력 영역이다. 이 영역은 유효해야 하며 Run만 단독으로 쓸 수 있어야 한다.

`fence`가 반환되거나 Run이 소멸하기 전에는 호출자가 C를 동시에 읽거나 써서는 안 된다. 프런트엔드는 Gemmini의 공식 `has_*`/route helper로 route를 분류한다. Native/provider route는 전체 tensor를 materialize하지 않고 요청받은 logical fragment만 공급하므로 RTL의 M/N tile, K fragment, block boundary, accumulate scheduling을 보존한다.

Channel route는 RTL `VectorBypass`에서 정수 dot product를 실행하고 channel scale을 host output에 한 번만 적용한다. H2/HP2 메타데이터는 internal/test route-contract 검사에 맞춰 보존하지만 public inspection API는 제공하지 않는다. `q8_h2`는 **Deprecated**이며 `q8_h2 is deprecated`, `q8_hp2`는 **Unsupported**이며 `q8_hp2 is unsupported`와 함께 `unsupported_route`를 반환한다.

두 route 모두 worker를 시작하지 않으며 원시 `q8_h0`로 fallback하지도 않는다. 프런트엔드는 전체 operand의 전치, 언패킹, 역양자화, 복사를 수행하지 않는다.

RTL Accumulator부터 bridge와 Rust provider service까지 output request는 signed 64-bit lane이다. ABI v3 provider callback은 그 값을 그대로 받는다. Raw output storage와 ABI v2 provider callback은 호환성을 위해 signed 32-bit이며, 해당 최종 경계에서만 saturation한다. Fragment, stripe, RMD staging 중간에는 32-bit narrowing이 없다.

선택해 복사하는 스칼라는 `I`, `J`, `K`, `sA`, `sB`, `sC`, `sD`, `activation_row_offset`, `activation_rows_per_stripe`, `block_size_k`, `tile_I`, `tile_J`, `tile_K`, `blocks_K`, `blocks_J`, `blocks_I`, `stripe_J`, `q8_h1_block_count`, `q8_h1_rows`, `blocks_per_row`, `q8_h2_block_count`, `q8_h2_blocks_per_row`, `q8_hp1_block_count`, `q8_hp1_blocks_per_row`, `q8_hp2_block_count`, `q8_hp2_blocks_per_row`, `weight_channel_scale_count`, `q8_channel_row_stride`, `q8_channel_row_count`, `col_stride_f_out`, `stride_f_out`, `weight_format`, `scale_B`, `scale_D`, `scale`, `bert_scale`, `transpose_A`, `transpose_B`, `full_C`, `low_D`, `repeating_bias`, `weight_i8_scale_active`, `act`다. 선택해 복사하는 포인터는 `A`, `B`, `C`, `D`, `A_fp32`, `B_fp32`, `B_blocks`, `B_scales`, `weight_channel_scales`, `q8_channel_row_base`, `q8_h1_blocks`, `q8_h2_blocks`, `q8_hp1_blocks`, `q8_hp2_blocks`, `c_b`, `s_rf`, `R`, `s_rf_stripe`, `R_stripe`, `f_out`, `model_arch`, `exsia_stripe_ready_sink`, `unpacked.blocks`다. 지원 route에서 선택한 포인터는 해당 provider가 실행 중에 직접 사용한다.

`q8_h2`와 `q8_hp2`의 선택 정보는 route-contract 검사 목적으로만 보존하며 수치 실행에는 사용하지 않는다.

`activation_row_offset`는 메타데이터로만 복사한다. 원시 ABI descriptor는 이 값을 사용하지 않으므로, 실행 시점에 A가 이미 첫 activation 행을 가리켜야 한다. `tile_I`와 `tile_J`는 Gemmini tile 수다.

각각에 `DIM`을 곱한 뒤 문제 범위와 RTL `DIM` tile 하나로 제한한다. 0은 tile 하나를 뜻하며 곱셈 오버플로는 거부한다. `tile_K`도 메타데이터로만 복사하며 원시 ABI의 reduction tiling은 변경하지 않는다.

K 실행은 `K`와 `block_size_k`로 결정한다.

파이프라인 모드에서는 전용 worker 하나가 모든 원시 simulator 및 stream 호출을 단독으로 소유한다. 호출자 큐는 `Options::queue_capacity`로 제한하며, 용량을 넘으면 프런트엔드 backpressure를 반환한다. 원시 `IM2P_BACKPRESSURE`는 내부에서 처리한다.

이때 동일한 밀집 이벤트 메타데이터를 유지하고 RTL logical cycle 하나를 진행한 뒤 완료를 poll하고 재시도한다. RTL logical cycle 하나는 상승 에지 하나를 포함하는 완전한 RTL clock period 하나다. `im2p_progress_stream(stream, 1)`는 동시에 준비된 A/W/S/C 응답을 포함해 scheduler가 어떤 상태이든 정확히 그 주기 하나를 진행한다.

원시 작업이 flight 상태로 남아 있는 동안 worker는 대기하지 않는다. 논리 완료가 계속 부족하면 보수적인 논리 한도인 최소 65536 사이클 또는 그보다 큰 설정값 `Options::max_stalled_cycles`에 도달했을 때 종료한다. 이 한도는 검증된 M=1,N=64,K=4096 RTL 실행을 포괄한다.

watchdog은 worker의 단조 증가하는 matched completion 카운터가 진행될 때만 재설정한다. 호출자가 큐 점유율을 바꿔도 이러한 진행을 숨길 수 없다. `max_stalled_cycles`는 matched completion 없이 worker가 진행하는 반복 횟수를 제한하며 각 `progress` 호출은 정확히 논리 사이클 하나를 진행한다.

경과한 호스트 시간은 제한하지 않는다. 스케줄링에는 wall-clock sleep이 관여하지 않는다. 제출된 stripe나 원시 작업이 없으면 worker는 condition variable에서 기다리며 RTL 클록을 진행하지 않는다.

이 호스트 wall-clock 대기는 RTL의 논리 대기 사이클이 아니다. 성능 cycle의 기준값은 IM2PCore 내부 RTL telemetry다. External C++ Host는 `execute`/`submit_stripe`/`fence`를 사용하고, Simulation Bridge는 clock과 A/W/S/C I/O를 구동하며 RTL counter를 읽는다.

Frontend worker 반복 횟수와 native provider wall-clock은 `total_cycles`에 반영하지 않는다. Model은 on-core scale cache와 resident weight-bank state를 기능적으로 포함하지만 CPU execution, host/SoC DRAM, cache timing, scratchpad, DMA, interconnect, clock frequency는 포함하지 않는다. Host pointer access는 zero-time이다.

Frontend/Verilator runtime과 RTL counter만으로는 CPU/NPU 공통 시간, 물리적 ns/GHz/Fmax, silicon 성능을 확립할 수 없다. 완료되지 않은 fence는 결정론적으로 실패하며 원시 stream을 finish하지 않고 파기한다.

## 빌드 및 검증

독립형 기본 빌드에는 의존성이 없다.

```sh
make check
```

인접한 Gemmini checkout과 생성된 parameter header를 사용할 수 있을 때만 adapter 계약 테스트를 빌드한다.

```sh
make gemmini-frontend-test
make gemmini-frontend-test-sanitized  # ASan + UBSan lifecycle
make gemmini-frontend-tsan-test       # producer/worker race audit
# 또는 일반 check 의존성 그래프에 포함한다.
make check ENABLE_GEMMINI_FRONTEND=1
```

DIM16/DIM32/DIM64 full/stripe RTL golden 및 lookahead 검사는 다음 명령으로 실행한다.

```sh
make gemmini-frontend-real-test
make gemmini-frontend-real-test GEMMINI_FRONTEND_DIM=32
make gemmini-frontend-real-test GEMMINI_FRONTEND_DIM=64
```

인접하지 않은 layout에서는 `GEMMINI_ROOT`와 `GEMMINI_PARAMS_ROOT`를 재정의한다. public header의 forward-declaration test는 의도적으로 Gemmini include path 없이 컴파일한다.

Raw simulator 계약은 [simulator 사용법](../sim/README.md), 전체 project 구조는 [root README](../README.md)에서 확인한다.
