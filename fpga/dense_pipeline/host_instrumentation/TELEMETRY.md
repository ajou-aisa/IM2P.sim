# IFR2 host09 검사·계측 계약

이 문서는 `dense-host-instrumentation-20260909T174219Z/host-09-02`의 고정 host 소스와 기존 `dense-pipeline-20260909T141302Z/stream-02` hardware를 읽어 확인한 계약이다. 새로운 device 통계를 추가하지 않는다. 보드 접근이나 새 runtime PASS를 뜻하지 않는다.

소스 기준은 다음과 같다.

- Host: `build/experiments/dense-host-instrumentation-20260909T174219Z/host-09-02/source`.
- Hardware: `build/experiments/dense-pipeline-20260909T141302Z/stream-02/source`.
- 아래 host 경로는 첫 번째 snapshot에, RTL 경로는 두 번째 snapshot에 상대적이다. 현재 root의 같은 이름보다 이 고정 소스를 우선한다.
- Hardware bitstream SHA256: `8aef393d040bb306e6ddf7b4b977976a9a924dbc5aec62b37b1690f0aa18c0ca`.
- 비교 대상은 A8/W8/DIM16/native Q8_H1/block32/EXSIA/RMD OFF의 FULL 및 Dense PIPELINE이다.

## FULL fail-fast 위치와 수명

`fpga/dense_pipeline/uart.cpp:188`의 `FullCycleReference::load`는 hardware/production RTL hash, backend, protocol, profile, FULL mode 및 세 fixture의 고정 cycle을 모두 검사한다. 파일 크기는 4096 bytes 이하이고 누락·변경·추가 token을 거부한다. 실제 cycle을 expected로 사용하는 경로는 없다.

| 고정 fixture | Board-provider FULL cycles |
|---|---:|
| M16/N16/K32 | 361 |
| M321/N48/K64 | 39,907 |
| M321/N48/K96 | 58,695 |

Persistent는 모든 예정 FULL fixture를 `OPEN/CAP` 전에 확인한다(`persistent_replay.cpp:155`). 실제 graph 경로는 해당 invocation의 reference를 device 생성 전에 확인한다(`ggml-gemmini-fpga.cpp:186`). 두 경로 모두 매 FULL 실행 전에 `expect_full`을 호출한다. Simulator는 이 reference 파일을 읽지 않고 PIPELINE elapsed에는 이 값을 적용하지 않는다. 범용 UART 테스트용 API 전체를 새로운 board measurement API로 바꾸지는 않았으며, 이 필수 정책의 적용 대상은 위 두 측정 실행파일이다.

공통 cycle gate는 `UART::full`에서 실제 RUN 응답의 CRC/profile/run/generation/shape/count 검사가 끝난 뒤, reconstruction callback 전에 실행한다(`uart.cpp:401–425`). 오류는 expected/actual, fixture, FULL mode, shape, run/generation, hardware/RTL identity, 실제 response header와 CRC를 남긴다. `completed_`와 성공 telemetry를 설정하지 않는다. 이후 catch가 sticky `error_`를 설정하고 `IM2P_ERROR`를 반환한다.

Cleanup의 정적 근거:

- FULL executor 오류는 frontend의 `final_status`로 전달된다(`frontend/src/im2p_gemmini_frontend.cpp:1240`). 성공 상태일 때만 FULL output을 commit한다(`:2084`).
- `Run::~Run`은 fence를 호출하지만 terminal 상태이면 결과만 반환한다(`:1668`, `:2053`). FULL executor를 재실행하지 않는다.
- `UART::~UART`는 POSIX `close`만 호출한다(`uart.cpp:260`).
- `release()`는 sticky error이면 exchange 전에 반환한다(`uart.cpp:589`). 자동 ABORT/reset/retry 경로가 없다.
- `Persistent::~Persistent`는 simulator handle만 정리한다(`persistent_replay.cpp:18`). FPGA의 `stream`/`sim`은 null이다.
- Persistent는 실패 fence 직후 예외를 발생시켜 RELEASE/다음 iteration으로 진행하지 않는다(`persistent_replay.cpp:276`). Parent runner는 실패한 child 뒤 다음 조건을 실행하지 않는다(`measure.py:142`).

“실패 뒤 device command 0”의 동적 증거는 `test_measurement_guard.py`의 PTY transcript로 별도 확인해야 한다. 그 테스트는 유효한 CRC를 다시 계산한 ±1 cycle 응답을 사용하며 mock parser/control 검사이다. FPGA numerical PASS로 분류하지 않는다.

## Owned snapshot과 출력 시점

`FenceResult.stats`는 값이지만 `stripe_rtl_timings`는 Run 소유 borrowed view이다(`frontend/include/im2p_gemmini_frontend.hpp:148`, `:276`). 성공 PIPELINE fence가 timing view를 고정하며 FULL/실패 fence는 빈 timing view를 준다. 실패 stats의 일부 값은 완성된 invocation의 통계가 아니다.

`RunTelemetry`는 성공 상태를 확인하고 stats 및 각 stripe 요소를 소유하는 값으로 복사한다(`telemetry.hpp:11`). 실제 사용 순서는 성공 fence/output commit, 정상 RELEASE, 작은 owned snapshot, Run retirement, sustained timestamp, 상세 formatting이다.

- Persistent: `persistent_replay.cpp:276–323`.
- 실제 graph: `ggml-gemmini-fpga.cpp:251–293`.
- UART의 `telemetry()`는 mutex 안에서 값을 복사한다(`uart.cpp:228`). 다음 FULL/BEGIN은 내부 telemetry를 초기화하므로 이전 invocation의 요소가 재사용되지 않는다(`:371`, `:443`).

Snapshot 복사 비용은 sustained에 포함한다. Wire 경계의 timestamp 수집 비용은 해당 transfer/service에 포함한다. 서비스 시간에서 계측 비용을 사후 차감하지 않는다. 상세 telemetry 출력은 adapter의 service/sustained 및 persistent의 service/sustained endpoint 뒤에 있다. 별도 `dense_host_dispatch benchmark`의 바깥 `HOST_BENCH_SAMPLE`은 graph prepare/output get/check와 adapter 호출 전체를 감싸므로 adapter의 로그 비용도 포함할 수 있다. 이 바깥 측정값을 persistent service/sustained와 같은 범위로 취급하지 않는다.

`print_telemetry`는 device snapshot이 completed/released이고 final statistics가 존재해야 성공 행을 출력한다(`telemetry.hpp:41`). 부분 snapshot은 성공 통계로 출력하지 않는다. FULL cycle 오류의 response 근거는 정상 SAMPLE 대신 실패 로그에 남긴다.

## 통계 필드 표

Device cycle은 해당 invocation의 core clock 기준이다. Nominal 25 MHz에서 cycle × 40 ns는 counter 기반 환산이며 외부 주파수 측정값이 아니다. Host timestamp는 `std::chrono::steady_clock`의 ns이며 device cycle과 다른 clock domain이다.

| 출력/저장 필드 | 원천·단위 | 측정 endpoint 및 의미 | 직접/파생·availability·수명 |
|---|---|---|---|
| `elapsed_cycles` | reply offset32, device cycles | Provider start부터 `core.matmulDone` 관측까지. PIPELINE publication/raw-drain 대기도 포함 | 직접. 최종 RUN/FINISH reply를 owned stats로 보존 |
| `fragments` | offset40, device count | 완료한 K16 fragments | 직접. 최종 reply. Shape에서 계산한 값은 검증용 expected일 뿐 출력값 대체에 쓰지 않음 |
| `output_works` | offset48, device count | 완료한 I/J output work | 직접. 마지막 해당 work의 writeback ACK 완료 |
| `activation_reads`, `weight_reads` | offset56/64, device request count | Core A/W provider request | 직접. 최종 reply |
| `output_writes`, `output_acks` | offset72/80, device request/response count | Core C write request 및 provider `putOutputWriteResponse` | 직접. Stripe ACK와 다른 단위 |
| `host_wait_cycles` | offset128, device cycles | `MatrixWaitSchedulerDone && matmulScheduler.active && waitingForStripe` | 직접. 전체 UART 대기나 모든 raw-drain 대기의 합이 아님 |
| `overlap_cycles` | offset136, device cycles | `engine.active && (A request valid OR W request valid OR S request valid)` | 직접 OR counter. CPU quantization overlap이나 cross-stripe overlap이 아님 |
| `publication_reply_count` | Host의 성공 PUBLISH reply 누적 | Reply의 stripe/row identity 검사 후 증가 | 직접 host 관측 count. 독립 device publication total은 wire에 없음 |
| `completion_reply_count` | Host의 검증한 POLL raw completion 누적 | Identity/order 검증 및 reconstruction 성공 후 증가 | 직접 host 관측 count. 독립 device stripe-ACK total은 아님 |
| Stripe `publish_cycle` | reply offset96, device cycles | FPGA publication 수락. POLL completion에서도 같은 값을 확인 | 직접. UART 및 frontend가 각 stripe별 owned 값으로 복사 |
| Stripe `completion_cycle` | reply offset104, device cycles | 마지막 output work의 C ACK에서 기록한 raw completion | 직접. Host 수신/commit/RELEASE 시점과 다름 |
| `publish_to_completion_cycles` | Frontend completion 값, device cycles | completion − publication | 동일 domain의 파생 duration. Host ns와 직접 연산 금지 |
| Stripe id/row 범위 | Reply offset90/92/94와 수락 이벤트 | PUBLISH 및 POLL의 id/row/count 검증 | Reply identity 직접 검증. Run retirement 전에 복사 |
| Stripe slot/context/host run | 기존 event/pending/frontend identity | Slot은 id%2, context는 수락 이벤트의 run identity | Host 보존값. Wire가 slot/context를 독립 echo했다고 표현하지 않음 |
| Device `run_id`, `generation` | Reply offset8/16와 송신 요청 | 매 response의 identity 검증 | 직접 reply 검증. Host logical run과 별도 domain |
| `first_A_cycle`, `first_A_published_rows` | reply offset112/120, device cycle/rows | Invocation 최초 provider A BRAM read issue 및 그 시점의 published prefix | 직접. Invocation 전체에서 한 번, per-stripe 값이 아님 |
| `first_A_observed_host_ns` | 첫 유효한 nonzero first-A 응답을 검증한 host clock | 이미 발생한 device event를 host가 관측한 시점 | 직접 host timestamp. Device event 발생 host 시각이 아님. 관측 없으면 `unavailable` |
| `host_begin_ns` | Host steady ns | 기존 `transfer()` 진입 | 직접. 해당 exchange의 owned 값 |
| `host_send_begin_ns` | Host steady ns | 첫 POSIX write 시도 직전 | 직접 syscall 경계. EAGAIN 가능하며 첫 UART wire bit 시점이 아님 |
| `host_send_end_ns` | Host steady ns | 요청 마지막 byte가 POSIX write에 수락된 직후 | 직접 kernel 전달 경계. Device staging/publication 완료 시각이 아님 |
| `host_receive_end_ns` | Host steady ns | 응답 header/payload/CRC 마지막 byte를 읽은 직후 | 직접 host 수신 경계 |
| `host_validated_ns` | Host steady ns | CRC/profile/run/generation/status/op/기본 framing 검사 후 | 직접 transport 검사 경계. 이후 shape/count/stripe 검사나 fence까지 포함하지 않음 |
| Producer `ready_ns` / `accepted_ns` | Host steady ns | Post-fold sink 진입 / `submit_stripe` 성공 반환 후 | 직접. 두 시점 사이에는 accepted A copy, 검사 및 backpressure가 포함 |
| Graph stripe quantization start/end | Host steady ns | 기존 producer checkpoint / post-fold sink 진입 | 직접 host endpoint. 지연 진단은 별도 표시 |
| Persistent quantization start/end | Host steady ns | 기존 `quantize_activation` 호출 전체 전/후 | 직접. Persistent per-stripe quantization 시작은 `not_collected`; post-fold ready는 존재 |
| `request_bytes`, `response_bytes`, `transactions` | Host exchange 계수 | Request 시도 시 bytes/count 증가, 정상 reply 후 response bytes 증가 | 직접 host 계수. 실패 snapshot을 정상 완료 transaction 수로 표현하지 않음 |
| Prepare/transfer/reconstruction/release seconds | Host steady duration | 기존 staging/왕복/기존 reducer/RELEASE 구간 | 직접 duration. Transfer 안에 device compute가 겹칠 수 있어 단순 합산 금지 |
| Service/sustained ns | Host steady duration | 입력 준비부터 f_out commit / 정상 RELEASE와 Run retirement | 직접 duration. 전체 process throughput과 다른 범위 |

기존 `SAMPLE`의 prequantized `quantization_start_ns=0/end_ns=0`은 `quantization_included=0`과 함께 **해당 호출에서 quantization 미실행(not applicable)**을 뜻한다. 관측한 0 ns quantization이 아니다. Simulator SAMPLE의 UART bytes/transaction 0은 UART 미사용이며, UART adapter의 prepare/transfer/reconstruction/release 세부 duration 칸은 simulator에서 수집하지 않는다(`not_collected`). 해당 기존 숫자 칸을 simulator 구간별 실측으로 사용하지 않는다. 새 `RUN_TELEMETRY`의 미노출 device 필드는 별도의 `not_exposed/unavailable` 표시를 사용한다.

기존 `FPGA_UART_*_PASS`의 first-A 칸은 마지막 응답으로 갱신된 UART atomic 값이다. RELEASE가 0을 반환하면 그 칸도 0이 될 수 있으며 invocation 최초 read를 보존한 snapshot이 아니다. 이번 보고·승인의 first-A 근거는 **첫 nonzero 관측을 보존하는 `RUN_TELEMETRY` owned 값**이다. Legacy 칸의 0을 관측된 first-A cycle 0 또는 first-A 미발생으로 해석하지 않는다. 이 차이는 0-valued RELEASE를 보내는 PTY 수명 검사에서도 확인한다.

Wire decoding은 `uart.cpp:79`, `:136`; wire packing과 sampling은 `fpga/dense_pipeline/dense_uart.sv:78`, `:94`에 있다. Provider counter와 first-A endpoint는 `synth/DensePipeline.bsv:89`, `:138`, `:163`, `:219`에 있다. Wait/overlap의 정확한 증가 조건은 `src/core/IM2PCore.bsv:530`에 있다. Raw completion은 `src/core/IM2PCore.bsv:2268`과 `src/control/MatmulScheduler.bsv:448`을 따른다.

## 현재 wire가 제공하지 않는 값과 새 승인 판단

다음 값은 canonical ABI나 core 내부에 존재하더라도 IFR2 reply에 없으므로 이번 host 변경으로 측정할 수 없다.

- `compute_cycles`와 별도 순수 active compute duration.
- `activation_wait_cycles`, `weight_wait_cycles`, `scale_wait_cycles`, `output_wait_cycles`, `drain_cycles`, `weight_preload_cycles`.
- Scale request/cache-hit/miss 및 weight-bank activation count.
- Individual A/W/S overlap, `cross_stripe_overlap_cycles`, 상세 lookahead telemetry.
- Per-stripe first-A cycle.
- 독립 device publication total 및 독립 device stripe-ACK total.

이 항목의 zero-initialized ABI storage를 실측 0으로 보고하지 않는다. 출력은 `not_exposed` 또는 위 표의 구체적인 availability 설명을 따른다. Runtime `RUN_TELEMETRY`는 대표 미노출 항목을 명시하며 나머지 미출력 항목은 이 표를 적용한다.

특히 이전 승인문의 “compute counter”, “3 publications와 해당 ACK”는 다음 제한을 새 승인 패키지에서 판단해야 한다. Work/fragment/C-write/C-ACK는 실제 device count를 확인한다. Publication/stripe completion은 검증한 reply의 host count와 순서를 확인한다. Shell의 독립 stripe ACK 수는 전송하지 않지만 FINISH 성공 조건이 `retired_stripes == next_stripe`를 요구한다(`dense_uart.sv:239`). 이는 protocol 완료 근거이지 독립 ACK counter의 실측값은 아니다. 순수 compute 및 per-stripe first-A는 제공할 수 없다. 이 제한을 명시한 동일 wire의 검증을 승인받아야 하며, 이번 범위에서 RTL/protocol을 늘리지 않는다.

## 계측이 진행 순서를 바꾸지 않는 범위

UART08 대비 packet 생성, opcode/payload/CRC, poll descriptor/timeout, PUBLISH/POLL 호출 지점은 유지한다. 새 코드는 기존 syscall 및 response 경계에 host timestamp를 읽고 작은 값을 복사한다. 추가 UART 명령, POLL, sleep 또는 barrier를 추가하지 않는다. Empty POLL은 개별 상세 event로 누적하지 않는다. 기존 응답에서 first-A가 처음 관측되면 그 사실만 저장한다.

`IM2P_FPGA_TEST_PRODUCER_OVERLAP`의 기존 진단은 성능 sweep과 분리한다. 이 진단의 준비 지연과 인과 순서를 자연적인 quantization/FPGA overlap으로 주장하지 않는다. Host ready/acceptance/response 관측과 device publication/first-A/completion은 각각 자신의 clock domain에서 해석한다. 서로 다른 domain을 직접 빼서 overlap duration을 계산하지 않는다.

## Validation과 reference marker

| 실행 경로 | 실제 검사·계산 | Marker의 근거 |
|---|---|---|
| `persistent_replay`의 모든 warm-up/측정 | 저장된 expected-raw/expected-fout와 actual raw/f_out/padding exact 비교 | `validation=expected_files_exact`. CPU reference count는 `not_instrumented`, 미호출은 `not_called_source_audit`. Runtime counter 0이라고 바꾸지 않음 |
| `dense_host_dispatch run` | FPGA 완료 후 기존 CPU MatMul과 dot reference 실행 및 비교 | `validation=cpu_reference_after_fpga`; 실제 reference 함수 호출 직후 증가한 counter 출력 |
| `dense_host_dispatch benchmark` warm-up | 위 독립 reference 계산으로 expected를 확보 | `validation=reference`; 실행된 dot/matmul count 출력 |
| 같은 benchmark의 측정 iteration | Warm-up expected와 raw/f_out exact 비교 | `validation=check`; 관측 counter 차이가 0인지 검사 |
| FPGA 경로의 simulator 사용 | Simulator 생성/execute/stream begin wrapper counter 검사 | 결과를 simulator로 대체한 fallback과 CPU 검증용 reference를 구분 |

소스 근거는 `persistent_replay.cpp:318`, `:351`, `host_dispatch.cpp:42`, `:60`, `:74`, `:86`, `:308`이다. Layer classification의 fallback 문자열은 수치 fallback counter가 아니며 그 문자열만으로 FPGA 경로 실패/성공을 판정하지 않는다.
