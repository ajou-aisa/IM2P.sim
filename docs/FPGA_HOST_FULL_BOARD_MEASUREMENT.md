# Dense FULL FPGA replay 제한 실보드 검증

2026-09-09. **승인된 configuration SRAM programming 1회와 logical GEMM 36회를 완료했다. 모든 실행의 raw signed32 및 최종 f_out float32가 reference와 bit-exact이고, 모든 core cycle이 기존 R2 production reference와 일치했다. 실패·timeout·재시도·자동 reset·자동 복구 programming은 0이다.**

검증한 범위는 **“기존 host 입력과 External reconstruction을 유지한 A8/W8/DIM16/native Q8_H1/RMD OFF Dense FULL FPGA replay 검증”**이다. Residual-enabled production ExSIA, PIPELINE, 모델 TTFT/TPOT는 미측정이다. Transport-inclusive 성능은 matched simulator보다 느렸다.

원본 artifact: [hardware-measurement-20260909T124602Z](../build/experiments/host-full-replay-20260909T103729Z/hardware-measurement-20260909T124602Z). 승인 전 [FULL 통합 보고서](FPGA_HOST_FULL_REPLAY.md), frozen candidate 및 승인 패키지는 변경하지 않았다. 이번 결과는 별도 문서와 실험 디렉터리에 기록했다.

## Programming 및 identity

- Board: Arty A7-100T. JTAG device `xc7a100t_0`, die `xc7a100t`, cable `210319BE7725A`.
- Implementation part: `xc7a100tcsg324-1`. 실제 routed DCP의 PART property로 확인했다. Package/speed grade를 JTAG die에서 추정하지 않았다.
- A8/W8, physical DIM16, nominal core25 MHz. 기존 승인된 source/RTL/DCP/bitstream을 사용했고 재빌드하지 않았다.
- Configuration SRAM programming **1회**. Flash/non-volatile write, 다른 bitstream, 자동 재프로그래밍 없음.
- Vivado programming log: 2026-09-09 21:54:04–21:54:24 KST. Programming 전·직전·직후 full SHA256이 모두 일치했다.
- Programming completion과 refresh한 15개 configuration/IR 상태를 검증했다. DONE/INIT/EOS/PLL/ISC_DONE=1, CRC/IDCODE/security/HMAC/bad-packet/over-temperature error=0. 독립적인 전체 SRAM readback 비교를 했다는 뜻은 아니다.

[Programming Tcl](../build/experiments/host-full-replay-20260909T103729Z/hardware-measurement-20260909T124602Z/program-approved-once.tcl), [원본 log](../build/experiments/host-full-replay-20260909T103729Z/hardware-measurement-20260909T124602Z/programming.log), [verification JSON](../build/experiments/host-full-replay-20260909T103729Z/hardware-measurement-20260909T124602Z/programming-verification.json), [실행 명령](../build/experiments/host-full-replay-20260909T103729Z/hardware-measurement-20260909T124602Z/programming-command.json), [승인 범위](../build/experiments/host-full-replay-20260909T103729Z/hardware-measurement-20260909T124602Z/authorization.txt). Tcl은 exclusive attempt marker를 만들고 `program_hw_devices`를 한 번만 호출했다.

| Artifact | SHA256 |
|---|---|
| 승인·실제 programmed bitstream | `c3713784732eb9f88221d921278b102589f7834499f7ca1c0eaa1307f9f02f78` |
| 실제 host-build-03 executable | `02fc99b417016edc5efb34967eb2cb473bb9ef471436a560c607c2c4b7fc768a` |
| Board source manifest | `0a82d0e307dd9cf55a6a42428a58b9b2d927352877de1607982fbdc57a26825e` |
| Routed DCP | `aa47dab9a5f52acfbb2760aa8696af69e8642462bb078d4859eab2f477342f1f` |
| Production mkFullReplay.v | `553c2339bd0f3df421a63b62ef27342f7d41694bdf1abe4caf69c0eb06f158bb` |
| final-tools manifest | `a20d4df4929c29cb3210f8aad4f79a7242ec798cc326ebb61ee2d851f96af69c` |

Host develop commit은 `7a74ac29c9c8fa8fbc5d47cf073b8b9266e3e7a9`다. 승인된 E/host-build-03와 E/final-tools/measure.py·protocol.py를 변경 없이 실행했다. Fixture는 E/deliverables의 frozen 사본이며 승인된 6개 fixture manifest/member hash와 일치했다. 초기 ggml_init 누락을 수정한 nonzero-scale fixture다. K64는 서로 다른 block scale2개, K96은3개이며 한 fixture cohort의 logical f_out2,883개 모두 nonzero임을 programming 전에 확인했다.

[사전 검사](../build/experiments/host-full-replay-20260909T103729Z/hardware-measurement-20260909T124602Z/preflight.json), [실제 입력 경로·fixture identity](../build/experiments/host-full-replay-20260909T103729Z/hardware-measurement-20260909T124602Z/approved-inputs.json), [616개 보존 hash](../build/experiments/host-full-replay-20260909T103729Z/hardware-measurement-20260909T124602Z/before-sha256.json). 최초 artifact611개 검사 뒤 운영 script5개도 고정했다.

## CAP, 실행 순서와 correctness

UART는 `/dev/serial/by-id/usb-Digilent_Digilent_USB_Device_210319BE7725-if01-port0` (`/dev/ttyUSB1`)를 사용했다. 1,000,000 baud 8N1. CAP 응답은 OFR1/version1/profile0x0810/capacity M32,N48,K96/generation0이었다. 기존 P2/P3A command는 보내지 않았다.

승인 순서의 fixture마다 warm-up1회와 측정5회를 연속 실행했다. 첫 M16/N16/K32 warm-up에서 raw256/f_out256/padding48, identity/run1/generation1, completion 및 core361cycles를 확인한 뒤 진행했다. Warm-up6개는 correctness에 포함하고 시간 통계에서만 제외했다.

| Fixture | 실행 수 | raw 비교 수 | logical f_out 비교 수 | f_out padding 비교 수 | core cycles min/median/max | R2 차이 | nominal resident μs |
|---|---:|---:|---:|---:|---|---:|---:|
| M16/N16/K32 | 6 | 1,536 | 1,536 | 288 | 361/361/361 | 0 | 14.440 |
| M16/N16/K64 | 6 | 3,072 | 1,536 | 288 | 665/665/665 | 0 | 26.600 |
| M16/N16/K96 | 6 | 4,608 | 1,536 | 288 | 971/971/971 | 0 | 38.840 |
| M32/N48/K64 | 6 | 18,432 | 9,216 | 576 | 3947/3947/3947 | 0 | 157.880 |
| M17/N19/K64 tail/stride | 6 | 3,876 | 1,938 | 2,244 | 2021/2021/2021 | 0 | 80.840 |
| Changed M16/N16/K32 | 6 | 1,536 | 1,536 | 288 | 361/361/361 | 0 | 14.440 |

총 raw **33,060회**, logical f_out **17,298회** 비교 PASS다. 반복 입력을 포함한 비교 횟수이며 고유 입력/출력 수가 아니다. f_out padding sentinel17.0f **3,972회**도 bit-exact 보존했다. 별도 사후 검증으로 raw wire의 zero padding **2,652 lanes**를 확인했다. Block callback/write/ACK는 **2,232회**다.

Raw block-major payload의 전체 길이·순서·좌표를 기존 decoder와 host callback 순서로 복원하고 모든 valid lane을 reference와 비교했다. 모든 실제 A/W request count, output works/fragments/write/ACK counters도 R2 reference와 같았다. Generation1..36, 매 RUN 뒤 matching RELEASE, 최종 generation36 RELEASE 성공이다.

매 FPGA replay에 `FPGA_REPLAY_V1 PASS` 및 simulator_creates=0/simulator_executes=0을 강제했다. Matched simulator36회는 각각 IM2P_SIM PASS 및 생성/실행1이었다. Expected 파일은 비교에만 사용했다. CPU dot product나 simulator가 FPGA 결과를 대신 생성하지 않았다. 최종 host output은 기존 External reconstruction과 성공 fence를 거쳤다.

[Correctness 집계](../build/experiments/host-full-replay-20260909T103729Z/hardware-measurement-20260909T124602Z/correctness.json), [72개 host check](../build/experiments/host-full-replay-20260909T103729Z/hardware-measurement-20260909T124602Z/host-checks.json), [실행별 raw/f_out/원본 로그](../build/experiments/host-full-replay-20260909T103729Z/hardware-measurement-20260909T124602Z/samples), [73개 실제 UART 거래](../build/experiments/host-full-replay-20260909T103729Z/hardware-measurement-20260909T124602Z/wire-observations.json), [원본 packet bytes](../build/experiments/host-full-replay-20260909T103729Z/hardware-measurement-20260909T124602Z/wire).

## 성능 측정 경계

A. Resident 시간은 실제 hardware cycles×nominal40ns다. 외부 주파수 계측값이 아니다. UART polling 횟수를 cycle로 사용하지 않았다.

B. FULL 시간은 prequantized owned fixture의 prepare/packing, input transfer, 실행/result receive, 기존 host reconstruction 및 output commit까지다. C. Simulator는 같은 fixture·production source·reconstruction/output 서비스와 같은 host subprocess 호출 범위다. Quantization/capture는 제외하고 host subprocess/file I/O는 포함했다. Residual OFF, PIPELINE/전체 모델 미측정이다. RELEASE는 output commit 이후이므로 FULL 시간 밖이며 전체 UART byte/transaction 집계에는 포함한다.

승인된 harness를 수정하지 않고 외부 운영 감시 script가 CAP capacity, 실행 직전 source hash, 실제 cycle 및 backend marker를 확인했다. 모든 전송 전에616개 hash를 검사하고 각 FPGA/simulator host completion 뒤에도 검사했다. Hash 검사와 packet 증거 저장 시간이 기존 harness timer 안에 들어가므로 아래에 **관측한 원본 시간**과 **해당 추가 감시 구간만 분리한 서비스 시간**을 함께 기록한다. 감시 wrapper는 실제 응답을 변경하지 않고 mismatch이면 다음 RUN/RELEASE 전에 중단한다.

[운영 감시 script](../build/experiments/host-full-replay-20260909T103729Z/hardware-measurement-20260909T124602Z/observe-approved.py), [운영 script hash](../build/experiments/host-full-replay-20260909T103729Z/hardware-measurement-20260909T124602Z/operation-scripts-sha256.json), [감시 시간](../build/experiments/host-full-replay-20260909T103729Z/hardware-measurement-20260909T124602Z/supervision-times.json), [원본 samples.jsonl](../build/experiments/host-full-replay-20260909T103729Z/hardware-measurement-20260909T124602Z/samples/samples.jsonl), [집계](../build/experiments/host-full-replay-20260909T103729Z/hardware-measurement-20260909T124602Z/analysis-summary.json).

모든 시간 통계는 warm-up을 제외한 fixture별5개 sample의 median [min, max], 단위ms다. Speedup은 `simulator median / FPGA median`이다. 1 미만은 FPGA FULL 경로가 더 느리다는 뜻이다.

| Fixture | 관측 FPGA FULL ms | 관측 simulator ms | 관측 speedup |
|---|---|---|---:|
| M16/N16/K32 | 325.302 [324.790, 329.292] | 138.961 [137.693, 139.702] | 0.427× |
| M16/N16/K64 | 357.357 [356.894, 357.987] | 156.000 [155.739, 156.530] | 0.437× |
| M16/N16/K96 | 387.665 [386.620, 389.518] | 151.731 [151.549, 194.028] | 0.391× |
| M32/N48/K64 | 480.571 [480.071, 482.569] | 286.924 [286.270, 287.475] | 0.597× |
| M17/N19/K64 tail/stride | 379.545 [378.749, 380.612] | 183.847 [183.226, 184.074] | 0.484× |
| Changed M16/N16/K32 | 324.561 [316.660, 325.492] | 137.621 [135.484, 138.164] | 0.424× |

추가 감시를 분리한 값은 **새로 측정한 sample이 아니라 같은 실행에서 기록한 서로 겹치지 않는 감시 구간을 뺀 파생값**이다. FPGA에서는 RUN 전후 supervision과 host 완료 뒤 hash 검사만, simulator에서는 host 완료 뒤 hash 검사만 뺐다. 실제 UART, 실행, host reconstruction, 기존 subprocess/file I/O 비용은 남겼다. 원본 값은 수정하지 않았다.

| Fixture | 감시 분리 FPGA FULL ms | 감시 분리 simulator ms | 서비스 speedup |
|---|---|---|---:|
| M16/N16/K32 | 75.404 [74.820, 76.401] | 15.885 [15.667, 15.928] | 0.211× |
| M16/N16/K64 | 107.261 [106.792, 107.787] | 31.866 [31.833, 32.004] | 0.297× |
| M16/N16/K96 | 136.947 [136.678, 138.303] | 31.962 [31.857, 64.260] | 0.233× |
| M32/N48/K64 | 230.245 [229.906, 230.733] | 164.561 [164.436, 164.888] | 0.715× |
| M17/N19/K64 tail/stride | 130.208 [130.019, 130.936] | 63.974 [63.891, 64.133] | 0.491× |
| Changed M16/N16/K32 | 76.425 [72.060, 76.990] | 15.710 [15.662, 15.900] | 0.206× |

어느 경계에서도 speedup>1은 없었다. Compute counter는14.440–157.880μs지만 UART/full-output 비용은 훨씬 컸다. Compute를 UART 구간에 다시 더하거나 서로 다른 cycle domain을 합산하지 않았다. 이전 P2의412cycles/1.528364ms를 기대값이나 분모로 쓰지 않았다.

원본 FULL 구간별 median [min,max]는 다음과 같다. Transfer 구간은 전송·compute·수신을 합친 하나의 관측 구간이며 중복 합산하지 않는다. 각 구간 median의 합이 전체 median과 정확히 같을 필요는 없다.

| Fixture | prepare/pack ms | transfer/execute/receive ms | decode/reconstruction/commit ms |
|---|---|---|---|
| M16/N16/K32 | 0.018 [0.016, 0.019] | 185.704 [185.047, 186.043] | 139.724 [139.024, 143.570] |
| M16/N16/K64 | 0.018 [0.017, 0.018] | 217.609 [217.079, 217.973] | 139.797 [139.730, 140.198] |
| M16/N16/K96 | 0.018 [0.018, 0.018] | 247.004 [246.711, 248.969] | 140.532 [139.571, 141.265] |
| M32/N48/K64 | 0.018 [0.017, 0.025] | 340.403 [339.936, 341.515] | 140.277 [139.824, 141.851] |
| M17/N19/K64 tail/stride | 0.017 [0.017, 0.018] | 240.004 [239.646, 240.718] | 139.597 [138.727, 140.520] |
| Changed M16/N16/K32 | 0.016 [0.016, 0.020] | 186.003 [184.623, 186.569] | 138.657 [132.017, 139.206] |

위 원본 구간에 포함된 추가 감시의 median은 다음과 같다. 실행별 min/max와 원시ns는 analysis-summary.json/analysis-samples.json에 있다.

| Fixture | RUN 감시 ms | FPGA host 후 hash ms | Simulator 후 hash ms |
|---|---:|---:|---:|
| M16/N16/K32 | 118.567 | 130.956 | 123.077 |
| M16/N16/K64 | 119.163 | 131.005 | 124.048 |
| M16/N16/K96 | 118.800 | 131.726 | 119.861 |
| M32/N48/K64 | 119.179 | 131.401 | 122.265 |
| M17/N19/K64 tail/stride | 118.484 | 130.782 | 119.898 |
| Changed M16/N16/K32 | 118.453 | 129.776 | 121.932 |

## 전송량, 오류, 보존

| Fixture | 입력 staging B / RUN | RUN request B | RUN response B | RELEASE request/response B | 6회 UART 합계 B |
|---|---:|---:|---|---:|
| M16/N16/K32 | 4,096 | 4,132 | 1,124 | 36/100 | 32,352 |
| M16/N16/K64 | 6,144 | 6,180 | 2,148 | 36/100 | 50,784 |
| M16/N16/K96 | 8,192 | 8,228 | 3,172 | 36/100 | 69,216 |
| M32/N48/K64 | 8,192 | 8,228 | 12,388 | 36/100 | 124,512 |
| M17/N19/K64 tail/stride | 6,272 | 6,308 | 4,452 | 36/100 | 65,376 |
| Changed M16/N16/K32 | 4,096 | 4,132 | 1,124 | 36/100 | 32,352 |

CAP1+RUN36+RELEASE36 = **73 transactions**. 실제 request **224,580 B**, response **150,148 B**, 합계 **374,728 B**다. 순수1Mbaud 8N1 line 하한은3.74728s이며 측정 전체 wall-clock은24.990s다. 이 wall-clock에는 모든 warm-up, matched simulator, hash 감시, RELEASE, file I/O도 포함된다.

실행 실패0, CRC/profile/id/count mismatch0, raw/f_out/padding mismatch0, timeout0, unexpected reset0, stale/duplicate completion0, cycle difference0, fallback0, retry0이다. 실패를 버리고 재측정하지 않았다. 첫 warm-up을 별도 추가 smoke로 중복 실행하지 않았다.

샌드박스에서 장치 경로가 보이지 않아 실제 환경에서 읽기 전용 장치 확인을 수행했다. `/dev/ttyUSB0`는 없고 승인 cable의 UART는 `/dev/ttyUSB1`이었다. 로컬 hw_server 시작 시 사용 중인 ARM GDB port3000 경고가 있었으나 JTAG3121 연결과 programming 검증은 성공했다. 이 환경 안내를 measurement failure나 재시도로 집계하지 않는다.

측정 전후 [보존 검사](../build/experiments/host-full-replay-20260909T103729Z/hardware-measurement-20260909T124602Z/preservation.json)는 **616/616 hash 동일**이다. 기존 dirty root23개, 승인 source/RTL/route/host/fixture/final-tools 및 과거 P2 programming 기록과 fixed P3A artifact를 보존했다. 새 보고서·측정 artifact 외 기존 파일 변경이 없다. Git add/commit/push와 RTL 수정/재합성은 없다.

보드는 마지막 matching RELEASE를 마쳤다. 승인된 Dense FULL SRAM configuration을 유지하며 자동 reset·복구 programming을 하지 않았다. 측정 뒤 추가 fixture/구현/programming은 수행하지 않았다.
