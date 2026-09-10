# Dense FULL 기반 live stripe PIPELINE 통합

작업 경로: `build/experiments/dense-pipeline-20260909T141302Z` (`N`).
구현·RTL 검증·25 MHz implementation을 완료했다. 새 보드 실행은 Awaiting approval이다. 새 programming, UART 장치 open/CAP,
실보드 GEMM은 실행하지 않았다. 이전 FULL의 SRAM programming 승인을 재사용하지 않는다.

## 보존한 실보드 FULL baseline

이전 작업 `build/experiments/host-full-replay-20260909T103729Z` (`E`)의
`hardware-measurement-20260909T124602Z`는 36/36 logical GEMM PASS다.
Raw 33,060회, logical f_out 17,298회, padding 3,972회는 반복을 포함한 비교 횟수다.
고유 출력 수가 아니다. 이번 실행에서 이를 새 board PASS로 재분류하지 않는다.

| Artifact | 검증한 SHA256 |
|---|---|
| Programmed FULL bitstream | `c3713784732eb9f88221d921278b102589f7834499f7ca1c0eaa1307f9f02f78` |
| host-build-03 executable | `02fc99b417016edc5efb34967eb2cb473bb9ef471436a560c607c2c4b7fc768a` |
| Board source manifest | `0a82d0e307dd9cf55a6a42428a58b9b2d927352877de1607982fbdc57a26825e` |
| Routed DCP | `aa47dab9a5f52acfbb2760aa8696af69e8642462bb078d4859eab2f477342f1f` |
| Production mkFullReplay.v | `553c2339bd0f3df421a63b62ef27342f7d41694bdf1abe4caf69c0eb06f158bb` |
| final-tools manifest | `a20d4df4929c29cb3210f8aad4f79a7242ec798cc326ebb61ee2d851f96af69c` |

`N/baseline/measured-full-verified.json`에서 위 파일, 이전 결과 manifest의 825개 파일,
source/host/tools의 각 manifest를 실제 내용으로 검증했다. `N/baseline/{core,host,params}`에
각 repository의 status/branch/HEAD/log/diff/stat/check/binary/cached/untracked 기록이 있다.
사용자 dirty 파일과 문서는 `N/baseline/root-files`에 별도로 보존했다.

Core HEAD는 `dc4a1a621f63834d64df42ae8c24152747d97971`, branch는
`exp/pe-local-partial-int20`이다. Host는 develop commit
`7a74ac29c9c8fa8fbc5d47cf073b8b9266e3e7a9`, Gemmini include는
`cd2caed8d93e49d3f8873bd6c4e340fcad54b9e0`로 고정했다. 새 develop을 fetch하거나 섞지 않았다.
Sibling host/include 원본에는 수정하지 않고, host patch는 versionable source에 남겼다.

## Source와 재현 경계

| 집합 | 내용과 선택 |
|---|---|
| Committed | Core HEAD archive. 이것만으로 board source가 되지 않는다. |
| Dirty root | PE-local partial 변경 및 기존 FULL frontend/adapter. 시작 상태를 보존했다. |
| Frozen FULL | 기존 fixed-core.patch를 HEAD archive에 적용하고 192개 frozen hash를 검증한다. |
| Integration | 위 frozen core에 명시된 frontend, DensePipeline provider, IFR2 shell/client, host patch만 적용한다. |

Arithmetic은 frozen A1 multiply, MatmulScheduler/WorkScheduler는 frozen board 구현을 사용한다.
`!activationResponsePendingReg` guard를 유지한다. Config/generator/수치 폭/DIM16을 변경하지 않는다.
`fpga/dense_pipeline/build.py`는 기존 FULL build helper를 재사용한다. 각 snapshot은 source,
host, params, simulator archive hash와 explicit replacement 목록을 저장한다. Build는 이 snapshot만 읽는다.

`N/full-reproduction`에서 기존 recipe로 FULL production BSC를 다시 실행했다.
새 RTL hash는 `fd4a2181d230ae9dec9f0d57efb6ee04aeec89d3889bbb12338f0daa8728aa2a`다.
기존 RTL과의 차이는 생성 시각 주석 한 줄뿐이다. `N/full-reproduction-rtl.diff`가 원본 비교다.
이를 bitstream byte-identical 재현으로 주장하지 않는다. 새 bitstream은 별도 artifact다.

## Host 실행 경계와 계약

새 selector는 `GGML_GEMMINI_EXECUTION_BACKEND=FPGA_UART`다. 기존 HARDWARE/IM2P_SIM의 의미를 바꾸지 않는다.
WS/A8/W8/DIM16/INT/EXSIA/native Q8_H1/block32/RMD OFF만 허용한다. RMD OFF는 명시적 dense ablation이다.
기존 IM2P_SIM ExSIA gate가 RMD ON을 요구하므로 해당 gate를 완화하지 않고 독립 selector를 추가했다.

| 생성자/field | 소비자와 수명·완료 의미 |
|---|---|
| ggml MUL_MAT의 I/J/K, native W | 기존 `ggml-gemmini.cpp` args 구성 후 FPGA adapter가 선택된다. 실제 graph failure가 호출자에게 전파된다. |
| tile_I/J/K | 기존 gemmini_set_tile_ws의 DIM factor. Host stripe와 DIM output work, K16 fragment는 다른 단위다. |
| activation_rows_per_stripe | 기존 geometry. BEGIN의 stripe_rows와 각 event 행 범위를 검증한다. 임의 override가 없다. |
| A backing | 원본 producer owner를 유지하며 수락한 행은 별도 owned A에 복사한다. Frontend는 이 불변 backing을 retain한다. Event slot 2개가 A backing 2개를 뜻하지 않는다. |
| W/metadata | 기존 frontend가 필요한 W/metadata를 복사한다. FPGA W는 BEGIN 이전 bounded staging한다. |
| post-fold StripeReadyEvent | 기존 producer가 즉시 sink에 전달한다. id/slot/row/run 및 해당 theta snapshot을 검증한다. |
| frontend submit_stripe | 현재 구현은 outstanding 2에서 blocking wait한다. Sink의 false는 fatal quantizer failure이며 backpressure가 아니다. |
| physical publish | 모든 A byte staging과 CRC 완료 후 기존 core publishActivationStripe를 호출한다. Host acceptance와 별개다. |
| core completion | 최종 I/J work 및 K fragment의 output acknowledgement 이후 발생한다. UART raw 회수 후 core completion을 ACK한다. |
| adapter poll | raw CRC/id/count/순서와 reducer callback 성공 후 frontend completion을 반환한다. 그때 dense event credit이 해제된다. |
| fence/authorization | private f_out staging을 완료한 뒤 caller logical output에 commit한다. 오류 후 부분 성공 output을 공개하지 않는다. |

공개 ggml args는 바꾸지 않았다. C++ frontend Options에는 borrowed `StreamExecutor` callback table을 추가했다.
FULL과 PIPELINE hook 조합, residual hook, incomplete table은 실행 전에 거부한다. Physical progress는 hardware poll이며
simulator의 progress_stream(1)을 호출하지 않는다. 기존 simulator 경로는 같은 API/수치 의미를 유지한다.
Host/frontend를 같은 snapshot과 C++20 설정으로 다시 빌드하며, source manifest fingerprint가 build identity에 포함된다.

Raw signed32 storage는 callback에서 signed64로 sign-extension한다. 같은 K32 block의 K16 fragment는 기존
replace/accumulate를 사용한다. 서로 다른 block raw는 기존 External reducer가
double(raw) × weight_factor × activation_scale 순서로 누적한 뒤 float32로 변환한다.
Identity S=1과 host FP scale은 별개다. Raw를 block 간 먼저 정수 합산하지 않는다.

## 실제 geometry와 저장소

실제 params는 DIM16/BANK_NUM4/BANK_ROWS4096/ACC_ROWS1024다. 자동 M48은 stripe 48 하나다.
N≤48에서 조사한 최소 자동 3-stripe 후보는 M321/N48/K64 또는 K96이다.
tile factor는 (10,3,4)/(10,3,6), stripe는 160/160/1, event slot은 0/1/0이다.
Host outer는 (3,1,1), ws_inner_calls=3, RTL output works=63, K fragments=252/378이다.
공개 args의 수동 tile factor로 M33에 3 stripes를 만들 수도 있지만 실제 ggml dispatch의 자동 geometry와 구분한다.

| 저장소 | K64 | K96 |
|---|---:|---:|
| Host owned A codes | 20,544 B | 30,816 B |
| Native W (44 B/block) | 4,224 B | 6,336 B |
| Theta | 6 B | 6 B |
| Logical f_out | 61,632 B | 61,632 B |
| Host double reducer payload (DIM×DIM) | 2,048 B | 2,048 B |
| Host accepted A copy (live PIPELINE) | 20,544 B | 30,816 B |
| Device A span / allocated | 41,088 / 65,536 B | 41,088 / 65,536 B |
| Device W span / allocated | 4,096 / 8,192 B | 6,144 / 8,192 B |
| Raw valid signed32 | 123,264 B | 184,896 B |
| Device C span / allocated | 164,352 / 262,144 B | 246,528 / 262,144 B |
| Architectural accumulator allocated / work live | 65,536 / 1,024 B | 65,536 / 1,024 B |

A 두 160-row slot과 전체 A backing의 power-of-two 할당은 모두 64 KiB다. C 두 stripe retention도
전체 C 보존과 같은 256 KiB다. 따라서 전체 backing에 stripe publication을 적용하는 구성을 선택했다.
A는 host write/core read dual port, C는 core write/transport read dual port다. W는 기존 8 KiB다.
동일 long shape의 bounded FULL도 지원한다. Scale BRAM은 0이며 accumulator와 C memory를 합쳐 부르지 않는다.

후속 오류에서 기존 quantizer는 producer A 전체를 zero-fill한다. 이 동작이 수락한 stripe의
비동기 전송과 경합하지 않도록, live adapter는 수락 전 해당 행을 별도 owned A backing에 복사한다.
Frontend는 이 복사본을 retain하며 행은 다시 쓰지 않는다. 추가 복사·할당 비용은 service timer에 포함한다.
전체 양자화 완료 후 replay로 바꾸지 않는다. 초기 shared A 실험과 이 오류 수정을 적용한 최종 host는 별도로 식별한다.

표의 host 값은 각 역할의 payload이며 process 전체 peak RSS가 아니다. UART는 RELEASE 뒤에도 진단용 `last_raw`,
`run_request_`, `run_response_`를 다음 full()/begin()까지 보존한다. K64/K96 FULL에서 이 세 유효 payload는
각각123,264+45,220+123,412 B /184,896+47,268+185,044 B다. PIPELINE의 마지막 request는 BEGIN,
마지막 response는 FINISH로 작지만 vector capacity는 이전 큰 FULL의 high-water allocation을 유지할 수 있다.
Transient transfer staging, allocator overhead, test의 expected raw/f_out cache는 별도이며 device BRAM 예산에 합산하지 않는다.
모든 값은 CAP M336/N48/K96로 유한하다. RELEASE는 device ownership을 반환하며 host 진단 vector 전체를 해제한다는 계약이 아니다.

## 검증 결과 기록

- **Implemented**: 실제 ggml dispatch selector, persistent UART client, FULL/PIPELINE callback 경계, IFR2 provider/shell.
- **Simulated**: IFR1 actual RTL와 ggml dispatch FULL 2회, raw 512회/f_out 512회 exact, simulator calls 0.
- **Simulated**: 기존 FULL 6 fixture × warm-up 1/측정 1, UART/RTL와 persistent simulator 각각 12회 PASS.
  각 경로 raw 11,020회, logical f_out 5,766회, padding 1,324회다. Simulator create는 전체 1회다.
- **Simulated**: 새 IFR2 FULL smoke 2회 및 실제 producer의 1-stripe K64 PIPELINE 1회 raw/f_out exact.
- **Simulated**: fresh fixed core 154/154 regression, runtime assertion/finish/panic 0 (`N/regression-154`).
- **Implemented / host tests**: 실제 ExSIA geometry probe 4 invocation/12 events, slot0/1/0 및 미래 theta 미준비 확인.
- **Protocol unit**: IFR1 PTY 10/10, IFR2 PTY 14/14. Mock 값은 framing/ownership 증거이며 FPGA numerical 증거가 아니다.
- **Routed**: `N/stream-02`에서 새 후보 synth/opt/place/route 및 bitstream 생성 완료.
- **Board measured**: 새 후보는 NOT RUN. 이전 Dense FULL 36/36만 별도 baseline이다.

첫 host build는 target C++17에서 std::bit_cast를 사용해 실패했다. Host/frontend target의 C++20을 명시하고 새 snapshot에서 통과했다.
첫 streaming host build는 incomplete StripeReadyEvent include로 실패했으며 실제 ExSIA header를 포함해 새 snapshot에서 통과했다.
BEGIN의 이전 job telemetry 노출 가능성은 explicit zero 초기화로 수정했다. 실패 로그와 초기 snapshot은 보존했다.

## 측정과 overlap 해석

새 구현 결과는 `N/implementation-review.json`과 `N/stream-02/route`에 보존했다.

| 항목 | 기존 FULL | IFR2 후보 |
|---|---:|---:|
| LUT / FF | 46,124 / 30,126 | 47,986 / 32,032 |
| RAMB36 / RAMB18 / tiles | 27 / 1 / 27.5 | 89 / 1 / 89.5 |
| DSP | 72 | 85 |
| Occupied slices | 15,589 (98.35%) | 15,596 (98.40%) |
| Control sets | 787 | 625 |
| Max non-clock fanout | 10,574 | 10,723 |
| Setup WNS / hold WHS / pulse slack | +10.870 / +0.024 / +3.000 ns | +12.517 / +0.018 / +3.000 ns |

81,452 routable nets 모두 routed, route errors 0, black boxes 0,
unconstrained internal endpoints 0이다. CDC report는 모든 경로가 안전하게 timed되었다고 보고한다.
UART RX와 UART TX/LED의 외부 I/O delay 미지정은 내부 synchronous closure와 구분한다.
DRC warning은 기존 159개에서 178개로 증가했다: DPIP-1 21→22, DPOP-1 69→78,
DPOP-2 69→78. Pipeline register 권고이며 error/critical은 0이다. 새 AsyncStripes와
확장 주소 계산에서 유지되는 DSP 경로의 보고서를 보존했으며 warning을 숨기거나
false_path/multicycle로 통과시키지 않았다. Fit/timing gate 이후 area campaign을 확대하지 않았다.

새 bitstream SHA256:
`8aef393d040bb306e6ddf7b4b977976a9a924dbc5aec62b37b1690f0aa18c0ca`.
SRAM programming은 아직 승인/실행하지 않았다.

Persistent replay는 hash 검사를 반복 timer 밖에 둔다. Prequantized owned fixture 준비부터 f_out commit까지 service,
RELEASE 및 Run 회수까지 sustained를 별도로 기록한다. UART open/CAP 및 simulator create 초기화도 별도다.
Reference/whole-fixture 비교는 성공 결과를 얻은 뒤 검사하며 timed service에서 예상값을 대신 실행하지 않는다.
실제 graph의 `validation=reference` warm-up은 CPU reference를 계산하므로 시간 통계에서 제외한다.
최종 benchmark의 measured `validation=check`는 `observer=1`이어도 저장된 raw/f_out exact 검사만 하며
reference_dot_calls/reference_matmul_calls는0이다. 단순히 observer 여부로 CPU reference 실행을 판정하지 않는다.

RTL simulation wall time은 FPGA transport 실측 시간이 아니다. 실제 보드 성능·speedup은 승인 이후 측정한다.
Nominal 25 MHz의 counter-derived 시간과 host wall-clock을 구분한다. 이전 P2 시간을 denominator로 쓰지 않는다.

진단용 producer checkpoint는 기존 quantizer stripe1 시작 직후 실행한다. 양방향 barrier가 stripe0 전송을
checkpoint 시작 이후로 제한하고, CPU 준비 지연 작업은 실제 first-A 관측 응답 이후 끝난다.
이는 지연 주입 overlap 검증이며 무지연 quantizer 성능과 별도다. 서로 다른 clock domain을 수치상 빼지 않고
publication과 응답의 인과 순서를 사용한다. 무지연 실행에서는 세 stripe의 native quantization이 첫 FPGA publication 전에 끝났다. 따라서 자연적인 CPU 양자화/FPGA 계산 overlap과 성능 향상은 입증하지 않았다. Live API 제출, 슬롯 재사용, backpressure와 최종 결과의 정확성은 별도로 통과했다.

## Transport와 미지원 범위

Wire와 timeout 계약은 `fpga/dense_pipeline/PROTOCOL.md`에 있다. A stride128/W64/C256 padding을 유지한다.
M321/N48의 A+W 입력은 K64 45,184 B, K96 47,232 B, raw 출력은 123,264/184,896 B다.
현재 shell은 한 packet의 RX와 reply TX를 순차 처리한다. Core clock은 계속 진행하지만 RX/TX full-duplex 대역폭을
가정하지 않는다. 1 Mbaud/8N1 byte 시간과 packet dependency를 기준으로 분석하며 compute 중복 합산을 하지 않는다.

Residual-enabled, physical residual, hybrid, PIPELINE weight streaming, 모델 capture/전체 모델,
TTFT/TPOT, physical DIM32/64, 새 고속 링크는 미지원/NOT RUN이다. 실제 모델 입력을 축소해 사용한 검증도 없다.

## 보드 승인 경계

최종 선택은 routed hardware `stream-02`와 host/frontend `stream-08`이다.
`fpga/dense_pipeline/approval.json`과 `N/approval-package`에 source/host/fixture/도구 hash,
implementation 결과, 시험 순서와 중단 조건을 고정하고 별도 승인을 요청한다.
승인 전 device open/CAP도 실행하지 않는다. Flash, 자동 복구 programming, 자동 retry는 금지한다.
Git staging/commit/push는 실행하지 않는다.


## 최종 선택 source와 host

상세 SHA/파일/줄 대응은 `fpga/dense_pipeline/source_provenance.json`, `CONTRACT.md`,
`N/evidence/final-provenance-review-host08.json`에 있다. Host C++의 Options layout 변경과 함께
host/frontend를 다시 빌드했다. x86_64/GNU C++ 11.4.0/CMake 3.22.1/BSC 2026.01/Verilator 5.051/Vivado 2025.2를
사용했으며 실행 파일·도구 hash는 `N/evidence/toolchain-final.json` 및 최종 plan에 고정했다.

- Hardware: `N/stream-02/source`, production RTL, route DCP/bitstream.
- Host/frontend: `N/stream-08/{host,source,params,host-build}`. 최종 원본 A view·residual metadata·low_D 검사를 포함한다.
- 수치 PTY 모델: `N/stream-04/production/obj_dir/Vdense_uart_shell`. Hardware 입력 61개가 routed source와 같다.
  Generated RTL의 생성 날짜 주석만 다르며 정규화 SHA는
  `9c150123cbdc4411dec2c5f0daa6e81e6a12edc045736615de32a7d484d6c359`다.
- Vendor board-top: `N/stream-03/board-top-unsandboxed`. 동일 설계의 실제 MMCM/unisims/25 MHz/UART 1 Mbaud를 실행했다.
- 최종 fixture: `N/final-fixtures`의 기존 6개와 M321/N48/K64, K96 두 개. 긴 두 fixture의 portable 원본은 `fpga/dense_pipeline/fixtures`에도 동일 hash로 보존했다. 기존 quantizer/reader/reference를 재사용했다.
  초기 ggml_init 수정 및 nonzero control을 유지한다. 기존 4개 binary payload는 이전 생성 결과와 32/32 동일하다.
  `input-f32.bin`은 live producer 입력이며 manifest로 별도 봉인했다.

FPGA selector는 공개 args의 precision을 A8로 덮어써 지원을 가장하지 않는다. 원본 A의 bit width/shape/stride/extent,
metadata 종류와 residual list, bias storage를 장치 open 전에 검증한다. Pipeline의 아직 준비되지 않은 theta는
execute 시점에 완성 snapshot으로 고정하지 않고 실제 post-fold event에서 확보한다.
최종 reject 26조건은 quantizer 호출 0, simulator 호출 0, caller output/metadata 보존을 확인했다.
실제 ggml graph의 F32/N49 거부 2건도 CAP를 포함한 UART bytes 0이다.

## 완료한 수치·계약 검증

아래 수치는 반복 실행을 포함한 비교 횟수다. 서로 겹치는 회귀를 하나의 고유 입력 수로 합산하지 않는다.
PTY 행은 실제 UART shell/provider/core RTL을 사용하며 mock 응답이 아니다.

| 증거 (`N/tests/` 기준) | 실행 / stripe | raw / f_out / padding exact | 범위 |
|---|---:|---:|---|
| `final-full-six` | 12 / 0 | 11,020 / 5,766 / 1,324 | IFR2 기존 FULL 6종, 각 warm-up 1+1 |
| `final08-host-full` | 2 / 0 | 512 / 512 / 0 | 최종 실제 ggml FULL, warm-up 후 reference 계산 0 |
| `final08-persistent-live-full-smoke` | 2 / 0 | 512 / 512 / 96 | 최종 persistent 실제 quantization 포함 UART FULL |
| `final08-host-pipeline` | 1 / 3 | 46,224 / 15,408 / 0 | 최종 실제 ggml live M321/N48/K96, slot0/1/0 |
| `final08-host-full-k96` | 1 / 0 | 46,224 / 15,408 / 0 | 같은 최종 host/input의 M321/N48/K96 FULL |
| `final-host-live-321` | 1 / 3 | 30,816 / 15,408 / 0 | M321/N48/K64, 서로 다른 theta, accepted A 분리 |
| `final-persistent-long-full` | 2 / 0 | 61,632 / 30,816 / 1,926 | 같은 M321/N48/K64 FULL |
| `final-persistent-long-replay` | 2 / 6 | 61,632 / 30,816 / 1,926 | 같은 입력 deterministic PIPELINE |
| `final-persistent-long-live` | 2 / 6 | 92,448 / 30,816 / 1,926 | 실제 producer live M321/N48/K96 |
| `board-m321k64` | 1 / 3 | 30,816 / 15,408 / 963 | Vendor board-top → 실제 wire decoder → 기존 reducer |

표의 `final-full-six`, `final-host-live-321`, `final-persistent-long-{full,replay,live}`는 host06 artifact다.
`final08-*`는 최종 host08이다. Host06→08 변경은 original args fail-closed gate이며 같은 수치 경로의 host08 FULL/PIPELINE도 별도로 통과했다.

각 numerical RTL 시험은 exit code와 함께 logical launch/publication/stripe ACK, 모든 sample의 exact/commit,
최종 numerical/backend marker 및 pending UART bytes 0을 검사했다. 선택된 FPGA matmul의 simulator
creates/executes/stream-begins는 0이다. 실제 graph log의 `semantic matmul classification fallback` 문자열은
기존 layer 이름 분류이며 수치 실행 fallback이 아니다.

Vendor 결과는 10 transactions, 1 launch, 3 publications/ACKs, 63 works, 252 K16 fragments,
1,926 output writes/ACKs, reset 0이다. Simulation testbench의 예정된 마지막 `$finish`를 확인했고
중간 runtime assertion/finish는 없다. `board-m321k64/numerical-host-complete.json`과 원본 응답에 보존했다.
기존 capture executable의 `FPGA_REPLAY_V1` marker는 decoded raw를 받는 host hook 이름이다.
해당 시험의 실제 wire version은 IFR2이며 marker를 version 근거로 사용하지 않았다.

| 계약/오류 시험 | 실제 결과 |
|---|---|
| 기존 fixed core | 154/154, 29 suites; runtime assertion/finish/panic 0 |
| M9/N1/K1 passive monitor | production/asserted 각각 4 jobs/36 outputs, cycle162/168; native Q8_H1 지원 사례가 아님 |
| Assertion-enabled Dense provider | M321 K64 FULL+PIPELINE 2 jobs/3 stripes, raw61,632 exact, 미공개 A/지연 raw 소비 포함 |
| Frontend custom stream + 기존 단위시험 | 0/1/0, outstanding2, metadata ownership, 늦은 완료, output 실패 및 70,000 polls 통과 |
| PTY mock | IFR1 10/10, IFR2 14/14; framing/ownership 범위 |
| 실제 RTL fault | 16조건/45 transactions, 9 starts, 2 publications, 명시적 simulation reset17회 |
| 최종 original args 거부 | A view12 + metadata12 + low_D2 = 26 PASS |
| Host failure/ownership mock | K64/K96 accepted A 총81,920 B exact; ACK 지연500 ms와 producer 실패 시 caller output 보존 |
| 실제 host+RTL producer 실패 | stripe1 수락 뒤 오류 전파, caller output 전체 sentinel 유지; 자동 recovery/fallback 없음 |

Malformed CRC/length/profile/capacity/id/generation, 중복/역순/slot/row, sticky error/ABORT/stale,
interbyte watchdog, 합법적인 미공개 stripe 대기, 연속 다른 GEMM을 검사했다. Failure 시험의 의도한 오류를
수치 성공 job으로 세지 않았다. Production과 assertion-enabled cycle은 별도다.
Assertion provider는 FULL42,499 / PIPELINE44,821 cycles이며 production 기대값으로 사용하지 않는다.
강제 BRAM stall, 실제 UART break, TX 중 새 명령 전체 주입, 실제 보드 reset/fault는 NOT RUN이다.

## Persistent 측정 경계와 현재 수치

네 조건은 prequantized FULL, deterministic PIPELINE, 실제 activation quantization 포함 FULL,
실제 post-fold live PIPELINE이다. Weight와 metadata는 매 invocation 같은 정책으로 준비하며 한쪽에만
weight residency를 주지 않는다. Capture와 fixture 파일 초기 load, UART open/CAP 또는 simulator create는
초기화로 별도 기록한다. 전체 hash는 앞뒤에서 검사하고 loop마다 source tree를 순회하지 않는다.
실제 loop의 CRC/profile/run/generation/count/error 검사와 전체 raw/f_out/padding 비교는 유지한다.
Service는 해당 입력 준비부터 output commit, sustained는 RELEASE/Run retirement로 device가 다음 invocation을 받을 수 있는 시점까지다.
다음 invocation의 입력 준비는 그 호출의 service에 포함한다. 전체 fixture exact 검증·로그·fixture 객체 소멸은 이 timestamp 뒤다.
따라서 이 sustained sample을 모든 test-loop overhead를 포함한 전체 process throughput이라고 부르지 않는다.
사후 차감은 없다. Host subprocess는 조건별 한 번만 시작하고 그 안에서 여러 invocation을 실행한다.

`N/measurement-plan-host08.json`은 4조건, warm-up1+측정5, 총54 simulator/FPGA jobs를 각각 정의한다.
여기서 simulator 결과는 harness 검증 수치다. 동시 RTL simulation의 부하가 있었으므로 이를 실보드 speedup이나
격리된 최종 성능으로 사용하지 않는다. 승인 후 같은 계획의 FPGA와 simulator를 다시 대응 측정한다.
Median/min/max와 원본 sample은 `N/tests/final08-persistent-simulator`에 보존한다.

Core cycle의 endpoint도 다르다. M321/N48/K64 FULL의 production board-provider는39,907 cycles,
R1 일반 provider는39,252 cycles다. PIPELINE elapsed counter에는 다음 publication과 raw 회수 대기가 포함된다.
K64 actual host의41,576,620 및 vendor deterministic41,348,110 차이는 실제 packet/POLL 진행 조건 차이다.
이를 순수 MAC 시간이나 동일 cycle service라고 주장하지 않는다. K96 actual host PIPELINE은56,998,528 cycles다. 같은 K96 FULL은58,695 cycles이고 R1 일반 provider는58,040 cycles다.
기존 작은 FULL 6종의 새 IFR2 cycle361/665/971/3947/2021/361은 이전 R2와 같다.
P2 batch412와 이전1.528364 ms/job는 비교 기준에 사용하지 않았다.

무지연 실제 producer는 첫 stripe가 준비되자 즉시 submit한다. 첫 publication 전 미래 theta가 완성되지 않은
event를 수락하는 live API 경계, blocking backpressure와 마지막 fence는 확인했다. 그러나 이 작은 workload는
CPU 양자화가 약0.2–0.5 ms/stripe로 끝나고 첫 A stripe 전송에는 UART에서 약0.205 s가 필요하다.
따라서 지연 없는 실행의 CPU 양자화/FPGA compute overlap은 **미관측**이다. 진단용 양방향 barrier와
CPU 준비 지연을 넣은 시험만 실제 first-A 관측 이전/이후의 인과 관계를 증명한다. 이는 자연적인 속도 향상 주장이 아니다.

## 실패 시도와 보존

초기 C++17/불완전 header build, BEGIN telemetry 초기화, optional run_id 변환, accepted A의 producer zero-fill 경합,
original A/metadata/low_D 거부 누락을 각각 새 snapshot/test에서 수정했다. 마지막 host08 이후 C++/RTL을 변경하지 않았다.
Vendor xsim의 sandbox Tcl 실패는 원본을 보존하고 별도 승인된 software simulation에서 실행했다. 물리 board 접근은 없다.
초기 measurement plan은 기존 root source hash 5개가 달라 preflight에서 중단했다. 최초 parser의 marker/fixture 검증
실패도 보존했고 plan과 parser를 고쳤다. Vendor reconstruction wrapper는 실제 `FPGA_REPLAY_V1 PASS`를
잘못된 이름으로 찾았던 assertion만 실패했으며, 원본 numerical 프로세스는 성공했다. 실제 f_out 전체 byte와 marker를
확인한 수정 판독을 별도 기록했다. 실패 실행을 지우거나 automatic retry로 숨기지 않았다.

이전616 hash 목록 중611개는 원래 경로에서 그대로다. 의도적으로 변경한 frontend header/source/test,
FULL capture.cpp와 route.tcl의 원래5개 blob은 `N/baseline/root-files`에 동일 hash로 보존했다.
이전 board 측정 결과825개도 그대로다. 따라서 “616 현재 경로가 모두 불변”이라고 보고하지 않는다.
시작 시17개 사용자 tracked dirty 파일은 이번 작업에서 변경하지 않았다.

## 완료 상태와 승인할 경계

- **Implemented**: explicit ggml FPGA FULL/PIPELINE, persistent transport/measurement, 실제 producer event 및 immutable A/metadata.
- **Simulated**: R0/reference·R1 simulator·실제 R2 shell/provider/core·vendor board-top, raw/f_out/padding 및 오류 회귀.
- **Routed**: A8/W8/DIM16/25 MHz/xc7a100tcsg324-1; 위 새 bitstream의 route/timing/DRC/CDC 검토 완료.
- **Board measured**: 이전 Dense FULL만36/36. 새 IFR2 후보는0회.
- **NOT RUN**: 무지연 실제 보드 overlap/성능, 실제 모델 capture/모델 validation/TTFT/TPOT, residual-enabled 및 hybrid.
- **Blocked**: 구현/fit을 막는 미해결 blocker 없음. 자연적인 quantization/compute overlap 입증은 현재 workload에서 미달이다.
- **Awaiting approval**: 새 bitstream의 SRAM1회 programming과 별도로 명시한 제한된 IFR2 FULL/PIPELINE UART 실행.

승인 후에도 hash/profile/count/identity/raw/f_out/padding/timeout/reset/stale 오류 첫 건에서 중단한다.
Flash/non-volatile write, 자동 재프로그래밍/복원/reset/retry, source/fixture 변경은 하지 않는다.

### 최종 host08 simulator 원본 통계

54/54 logical invocations, raw1,850,496 / f_out741,120 / padding46,512 exact. 모든4,961 hash 보존.
아래 service/sustained는 ms, 각 셀은 median [min, max]다. Warm-up1회는 correctness에 포함하고 시간에서는 제외했다.

| 조건 | fixture | Service ms | Sustained ms | R1 cycles |
|---|---|---:|---:|---:|
| simulator | m16n16k32 | 11.075 [11.045, 11.127] | 11.081 [11.051, 11.133] | 344 |
| simulator | m321n48k64 | 1224.907 [1222.369, 1227.495] | 1224.913 [1222.372, 1227.498] | 39252 |
| simulator | m321n48k96 | 1825.125 [1812.952, 1833.462] | 1825.127 [1812.955, 1833.465] | 58040 |
| simulator-pipeline | m321n48k64 | 1348.270 [1344.196, 1354.325] | 1348.273 [1344.199, 1354.327] | 39831 |
| simulator-pipeline | m321n48k96 | 2020.340 [1987.874, 2035.011] | 2020.343 [1987.877, 2035.019] | 58655 |
| simulator-live | m321n48k64 | 1224.824 [1223.245, 1242.415] | 1224.827 [1223.248, 1242.418] | 39252 |
| simulator-live | m321n48k96 | 1815.572 [1812.105, 1820.509] | 1815.574 [1812.108, 1820.512] | 58040 |
| simulator-live-pipeline | m321n48k64 | 1341.431 [1334.683, 1351.798] | 1341.460 [1334.704, 1351.827] | 39832 |
| simulator-live-pipeline | m321n48k96 | 1995.190 [1983.324, 1999.537] | 1995.193 [1983.327, 1999.546] | 58656 |

FPGA actual wall-clock 및 matched speedup: **NOT RUN / Awaiting approval**. RTL emulation wall time으로 대신 계산하지 않았다.

### 최종 Git 기록

`N/final-git/{core,host,params}`에 status/branch/HEAD/log/diff/stat/check/binary/cached/untracked를 저장했다.
Core는 tracked20개 modified: 기존 사용자17개 + frontend3개다. Tracked diff 전체는516 insertions/107 deletions이며 기존 사용자 변경을 포함한다.
이번 tracked frontend 변경은 header/source/tests다. 새 provider/adapter/tests/patch/recipe/report와 portable fixture는 일반 소스 트리에 untracked로 남겼다.
Sibling host는 기존 `models/gpt2/`, `models/llama3.2-1B/` untracked만 있고 tracked diff는0이다. Include repository는 clean이다.
세 repository 모두 git diff --check PASS, staged diff0. Git add/commit/tag/push/merge/rebase/PR 생성 없음.

### 제안하는 보드 실행량

`fpga/dense_pipeline/approval.json`의 순서는 다음과 같다.

1. 전체 hash/profile preflight 후 승인 bitstream을 SRAM에1회 programming하고 CRC/EOS/PLL/DONE/JTAG die/implementation part를 확인한다.
2. 실제 ggml FULL M16/N16/K32 1회 smoke. CAP와 전체 raw/f_out/identity 완료가 맞아야 다음으로 간다.
3. 실제 ggml live M321/N48/K64 1회, 준비 지연을 주입해3stripes/slot0·1·0/backpressure/fence를 검사한다. Performance sample이 아니다.
4. 지연 환경을 제거하고4조건 persistent sweep54회: 각 fixture warm-up1+측정5. 작은FULL6회, 긴K64/K96의4조건 각각6회다.
5. 같은4조건·정책의 matched simulator54회와 전체 hash postflight를 수행한다.

총 FPGA56 logical GEMM, 별도 matched simulator54회. FPGA 전체 예상 raw1,881,568 / f_out756,784 / padding46,512 비교이며 반복을 포함한다.
Physical transaction timeout30s, 조건별 process900s, 진단 checkpoint300s. Core/interbyte2^20 cycles(명목41.94304ms)는 합법적인 unpublished/raw-drain 대기를 구분한다.
무한 watchdog 해제·자동 retry/reset/reprogramming 없이 첫 오류에서 중단한다. Fixture별 wire 예산과 실제 순차 RX/TX 근거는 `fpga/dense_pipeline/TRANSPORT_BUDGET.md`에 있다.

승인 범위는 native Q8_H1/EXSIA/RMD OFF Dense FULL 및 live PIPELINE이다. 실제 모델·residual-enabled ExSIA 또는 속도 향상을 승인/입증한 것으로 확대하지 않는다.


최종 host executable SHA256:

| Artifact | SHA256 |
|---|---|
| `stream-08/host-build/dense_host_dispatch` | `f5cd48e36e6dab5566b2ea438e265541884539eec70dcd1b1c6059fd77c3ba10` |
| `stream-08/host-build/persistent_replay` | `f057b99fc188673cbfad29bd9957616143ad89458e5d4c5de0d583595cfd6e03` |
| Host08 integration manifest | `0bf50916cd891c766f4ef372c68d9c07494a419606bdd44c289e98954bf81454` |
| Host08 measurement plan | `07e29d51e7ac5c027499b39289cf07e85349e0779bc24d7b6fd27917e041899d` |
| 새 routed DCP | `6141a245e4c2ca88b8147ae864974abc14c3b858a276c2a5f5beee42f5a92bd4` |

승인 파일에 명시된 executable만 사용한다. Root를 다시 빌드한 결과로 교체하지 않는다.
`N/evidence/final-hardware-identity.json`은 stream02/03/04/08의 실제 hardware input61개 동일성을 확인한다.
Host07의 계획·sample·provenance도 별도 보존했으며 최종 host08 결과로 덮어쓰지 않았다.


최종 host08의 K96 FULL/PIPELINE trace를 직접 다시 비교했다. A/W staging47,232 B가 동일하고,
실제 회수 raw signed32 46,224개도 모두 동일하다. 두 실행은 각각 기존 host reconstruction/reference의
f_out15,408개 bit-exact를 통과했다. 근거: `N/evidence/final08-full-pipeline-equality.json`.
따라서 긴 동일 logical shape의 board-provider FULL 비교도 Simulated이며, 작은 GEMM으로 분할한 대체 비교가 아니다.
K96 FULL은 CAP 제외 request47,304/response185,192 B,2 transactions,58,695 core cycles다.
전체 FPGA physical measurement는 여전히 NOT RUN이다.
