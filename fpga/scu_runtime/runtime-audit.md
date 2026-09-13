# 일반 FPGA_UART 런타임 연결 조사

기준: final candidate-01의 frozen host. 파일별 원본/overlay hash는 `runtime-source-audit.json`.

| 단계 | 실제 위치·행동 | 장치 접근 |
|---|---|---|
| Backend 발견 | `common/arg.cpp:1215`의 `ggml_backend_load_all()`, `--list-devices` | 없음 |
| 등록 | `ggml-gemmini.cpp`의 `GGML_BACKEND_DL_IMPL(ggml_backend_gemmini_reg)` | 없음 |
| 초기화 | `ggml_backend_gemmini_init`; 선택 H1 artifact 파일 읽기, context 생성 | 없음 |
| 명시적 선택 | CLI `--device GEMMINI`; `llama-context.cpp`에서 실제 model architecture 전달, 중복 ACCEL 초기화 방지 | 없음 |
| Loader probe | `llama-model.cpp:weight_buft_supported`가 M512·0-byte weight buffer 생성 | 없음 |
| Supports | FPGA는 native Q8_H1/F32, shared contiguous layout, N≤48, K=32/64/96. Loader probe의 가짜 M512만 제외; 실제 backed op의 M≤336 유지 | 없음 |
| Scheduler | `ggml-backend.cpp:ggml_backend_sched_backend_from_buffer`의 buffer+supports 검사, `llama-context.cpp`의 ACCEL backend 목록 | 없음 |
| 실행 배정 | `ggml_backend_gemmini_graph_compute`의 실제 MUL_MAT node 방문을 assigned로 계측 | 이 지점에는 없음 |
| 인자/생산자 | `ggml_backend_gemmini_mul_mat`: 기존 tile/geometry, native H1 blocks, ExSIA quantizer, FULL/live 선택 | 기존 경로 유지 |
| SCU adapter | `ggml_gemmini_fpga_execute`; 호출 직전 attempted 증가. 기존 engine mutex, strict reference/identity, FULL expectation, UART 생성 | 명시한 `IM2P_FPGA_DEVICE`만 실행 시 open |
| 수치/완료 | 기존 submit/fence/authorization, final integer reconstruction, caller commit, 정상 RELEASE | 기존 IFR3 거래 |
| 성공 계수 | Adapter true 뒤 completed. Graph failure는 failed와 `GGML_STATUS_FAILED` | 재시도 추가 없음 |
| CLI 완료 | `common_fpga_execution_check`; 등록된 stats function만 조회. 실패/incomplete면 nonzero. `IM2P_FPGA_REQUIRE_COMPLETION=1`이면 completed=0도 nonzero | 없음 |

이벤트 수와 계수의 의미를 분리한다. `supports_op` 질의는 scheduler 확정 배정이 아니다. `assigned`는 실행된 graph의 nonempty MUL_MAT 방문 수다. `attempted`는 adapter 호출 수이며, preflight가 UART 제출 전 거부할 수 있다. `completed`는 정상 output commit과 RELEASE까지 끝낸 adapter 성공 수다. `failed`는 실패한 배정 node 수다. Wire transaction 수는 기존 UART telemetry다. Simulator/CPU 대체 호출은 이 counter API로 계측하지 않으며 `fallback_calls=not_instrumented`로 출력한다.

`IM2P_FPGA_TRACE=1`은 일부 static format/layout/capacity 거부 사유를 supports 질의로 출력한다. 전체 CPU node 배정 수를 계측했다고 주장하지 않는다. `--device none`은 이 host pin에서 기존 ACCEL 등록을 전역 비활성화하는 옵션이 아니다. 이번 수정은 CPU/SIM의 ACCEL 정책을 변경하지 않는다. FPGA physical path는 `IM2P_FPGA_DEVICE`로 반드시 명시한다.

## Native H1과 모델 제한

FPGA 지원 함수의 K 집합은 정확히 32/64/96이다. Native H1 blocks는 저장 code와 metadata를 그대로 읽는다. `LLAMA_GEMMINI_Q8_H1_ARTIFACT` reader가 존재하지만, 이 환경변수는 Q8_0나 Q8_HP1 GGUF를 FPGA native H1으로 자동 지원시키지 않는다. 현재 FPGA supports_op는 이 타입들을 거부한다.

| 실제 local GGUF (읽기 전용 metadata 조사) | Tensor 수/type | 대표 [K,N] | 결과 |
|---|---:|---|---|
| `models/gpt2/gpt2.Q8_HP1.gguf` | 148: F32 99, Q8_HP1 49 | QKV [768,2304], FFN up [768,3072], down [3072,768] | FPGA format 미지원, 모든 2D weight가 CAP 밖 |
| `models/llama3.2-1B/llama3.2-1B.Q8_HP1.gguf` | 147: F32 34, Q8_HP1 113 | Q [2048,2048], FFN up [2048,8192], down [8192,2048] | FPGA format 미지원, 모든 2D weight가 CAP 밖 |

둘 다 K∈{32,64,96}, N≤48에 맞는 2D tensor는 0개다. 입력 batch/prefill/decode를 줄여 M을 낮춰도 K/N이 달라지지 않는다. 모델 파일 수정/변환/다운로드 또는 전체 eval은 이 조사에서 수행하지 않았다.

FULL은 CAP 안이어도 reference fixture가 없으면 기존 adapter가 UART constructor 전에 거부한다. 현재 m16n16k64=617, m321n48k96=52687, m17n19k64=1949만 고정돼 있다. 이 값을 다른 FULL shape/PIPELINE에 적용하지 않는다. 임의 model-ready를 의미하지 않는다.

## 검증·한계

`tests/runtime_probe.cpp`는 배포한 동일 shared library를 동적 load한 뒤 9개 metadata/supports case와 stats layout을 검사한다. 반드시 부모 작업의 실제 clean backend에 링크·실행한 결과로 판정해야 한다. 여기서 수행한 syntax-only 검사는 build/link/load PASS가 아니다.

기존 CLI는 llama_decode 실패에 return 1. PPL 정상 경로는 `ppl_value=-1`을 무시하고 return 0하던 오류를 수정한다. 다른 PPL score 경로의 기록된 FPGA 실패도 공통 checker가 nonzero로 전달한다. 강제 종료/시그널/모든 cancellation에 대한 command0를 새로 주장하지 않는다. 기존 adapter의 producer 실패 시 Run 소멸 drain 가능성은 그대로다.

## 실행 mode 설정 정정

실제 `ggml-gemmini-matmul.hpp:resolve_matmul_options` 실행 mode 환경변수는 `GEMMINI_MATMUL_MODE=FULL|STRIPE_PIPELINE`이다. 기존 `llama-context.cpp`의 `GEMMINI_MATMUL_INVOCATION=stripe-pipeline` 참조는 ACCEL 우선순위에만 관여하며 실행 mode 선택이 아니다. 이를 실행 mode로 소개한 test command는 harness 오류로 정정했다. 새 alias는 추가하지 않는다. 기존 결과의 mode는 환경변수 이름 대신 실제 adapter 로그/packet으로 재판정한다.
