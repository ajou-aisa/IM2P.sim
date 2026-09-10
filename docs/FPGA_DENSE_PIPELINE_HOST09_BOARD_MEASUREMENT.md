# IFR2 host09-02 Dense FULL/live PIPELINE 제한 실보드 검증

2026-09-10 KST. **승인된 FPGA 56/56회와 이후 새 matched simulator 54/54회 PASS.** Raw signed32 1,881,568회, logical f_out float32 756,784회, padding 46,512회 exact 검사 완료. 모두 반복을 포함한 비교 횟수다. SRAM programming 1회, 실패·timeout·재시도·자동 reset·ABORT 복구·flash write 0회.

주장 범위는 **A8/W8 / physical DIM16 / native Q8_H1 block32 / EXSIA / RMD OFF의 제한된 Dense FULL/live PIPELINE 실보드 검증**이다. 모델 실행·residual-enabled·TTFT/TPOT는 NOT RUN. 순수 compute counter는 not_exposed이고 CPU quantization–FPGA compute의 자연 overlap은 미입증이다.

모든 조건의 simulator 대비 service/sustained speedup은 1 미만이다. Quantization 포함 live PIPELINE은 같은 FPGA FULL보다 service 기준 K64 9.91%, K96 6.96% 느렸다. 결과·후처리·검증을 생략하거나 비용을 사후 차감하지 않았다.

## 실행과 증거

새 결과 경로: [`dense-host09-board-20260910T021038Z`](../build/experiments/dense-host09-board-20260910T021038Z). [원본 수치·통계 분석](../build/experiments/dense-host09-board-20260910T021038Z/analysis.json), [FPGA sweep 원본 summary](../build/experiments/dense-host09-board-20260910T021038Z/board-sweep/summary.json), [새 simulator summary](../build/experiments/dense-host09-board-20260910T021038Z/matched-simulator/summary.json). 각 summary의 samples에 원본 ns 값과 반복 identity가 있다. 재분석은 `python3 build/experiments/dense-host09-board-20260910T021038Z/analyze.py`이며 장치나 backend를 실행하지 않는다.

| 단계 | 실제 완료 | 비교 raw / f_out / padding | 분류 |
|---|---:|---:|---|
| ggml FULL smoke 16/16/32 | 1 | 256 / 256 / 0 | Board measured, 비성능 |
| 지연 checkpoint live 321/48/64 | 1 | 30,816 / 15,408 / 0 | Board measured, 비성능 |
| Persistent prequantized FULL, 세 shape | 18 | 463,776 / 186,432 / 11,844 | Board measured |
| Persistent deterministic PIPELINE, K64/K96 | 12 | 462,240 / 184,896 / 11,556 | Board measured |
| Persistent quantization 포함 FULL, K64/K96 | 12 | 462,240 / 184,896 / 11,556 | Board measured |
| Persistent post-fold live PIPELINE, K64/K96 | 12 | 462,240 / 184,896 / 11,556 | Board measured |
| **FPGA 합계** | **56** | **1,881,568 / 756,784 / 46,512** | **PASS** |
| 새 matched simulator, 같은 네 조건 | 54 | 1,850,496 / 741,120 / 46,512 | Simulated, PASS |

각 fixture/조건은 warm-up 1회+측정 5회. Warm-up도 모든 correctness gate를 거쳤고 통계에서만 제외했다. Sweep은 세 logical shape/fixture를 반복했다. 기존 소프트웨어 회귀 54회/추가 mode 검사 2회와 이번 simulator 54회는 별개다.

## 고정 artifact

승인은 기존 host08/host09-01이 아닌 [host09-02 approval](../fpga/dense_pipeline/host_instrumentation/approval.json) 및 대응 frozen approval-package에 적용됐다. 기존 JSON의 작성 당시 `Awaiting NEW approval`/physical 0 상태는 덮어쓰지 않았다. 이번 승인은 사용자 대화, 실제 실행은 이 새 결과 디렉터리로 구분한다.

| Artifact | 전체 SHA256 |
|---|---|
| [stream-02 bitstream](/home/youngshin/aisa/RISCV-DynDNN/IM2P/IM2P.sim/build/experiments/dense-pipeline-20260909T141302Z/stream-02/route/dense-pipeline.bit) | `8aef393d040bb306e6ddf7b4b977976a9a924dbc5aec62b37b1690f0aa18c0ca` |
| [Routed DCP](/home/youngshin/aisa/RISCV-DynDNN/IM2P/IM2P.sim/build/experiments/dense-pipeline-20260909T141302Z/stream-02/route/route.dcp) | `6141a245e4c2ca88b8147ae864974abc14c3b858a276c2a5f5beee42f5a92bd4` |
| [dense_host_dispatch](/home/youngshin/aisa/RISCV-DynDNN/IM2P/IM2P.sim/build/experiments/dense-host-instrumentation-20260909T174219Z/host-09-02/host-build/dense_host_dispatch) | `d8e602a0f102c289f19ae6e681e02c723f1b7649f16fd92ef70a012969c2e2ea` |
| [persistent_replay](/home/youngshin/aisa/RISCV-DynDNN/IM2P/IM2P.sim/build/experiments/dense-host-instrumentation-20260909T174219Z/host-09-02/host-build/persistent_replay) | `5536f7025f0d640014e867283111c97f4c24344e88ca4d0667b6f8fd09c48e85` |
| [Host/frontend integration](/home/youngshin/aisa/RISCV-DynDNN/IM2P/IM2P.sim/build/experiments/dense-host-instrumentation-20260909T174219Z/host-09-02/integration-sha256.json) | `71d526ceab9ca9d955cc04e442a6938cdd2682309c5ab8475b07015d6bd8a07c` |
| [Measurement plan](/home/youngshin/aisa/RISCV-DynDNN/IM2P/IM2P.sim/build/experiments/dense-host-instrumentation-20260909T174219Z/measurement-plan-host09-02.json) | `15d7d247ae09b51fcefd904f2a42559816d018250d3a9bc93066c4abebe5f448` |
| [FULL expected/provenance](/home/youngshin/aisa/RISCV-DynDNN/IM2P/IM2P.sim/fpga/dense_pipeline/full-cycle-reference.txt) | `de3f8d8e39bfacdca7d7ac3a53efefe2443f49a46d162e807438d5a5811723da` |
| [Final tools](/home/youngshin/aisa/RISCV-DynDNN/IM2P/IM2P.sim/build/experiments/dense-host-instrumentation-20260909T174219Z/final-tools-sha256.json) | `0c261340f6ae36ded762cff8ba8fcd504d9a73ff74ccd6de23446fadc5d85ae6` |

Host pin `7a74ac29c9c8fa8fbc5d47cf073b8b9266e3e7a9`; Gemmini include pin `cd2caed8d93e49d3f8873bd6c4e340fcad54b9e0`. Production `mkDensePipeline.v` SHA256 `b29147ce384e9384b3821c358863350f03c0696cbc8b8e286a768f2e7155b853`. 새 source 수정·재빌드·재합성·재배치배선 0회. 기존 stream-02 25 MHz route는 동일 hardware 근거이며 이번 재실행 결과로 분류하지 않는다.

## Programming과 CAP

[Programming 원본 로그](../build/experiments/dense-host09-board-20260910T021038Z/programming/programming.log), [정확한 argv/cwd](../build/experiments/dense-host09-board-20260910T021038Z/programming/command.json), [상태](../build/experiments/dense-host09-board-20260910T021038Z/programming/status.json). 시작 2026-09-10T11:14:39+09:00, 종료 2026-09-10T11:15:02+09:00. 고정 `program_sram_once.tcl`을 사용했고 CREAT EXCL marker 후 `program_hw_devices` 1회 실행. 실제 출력의 BEGIN/CONFIGURATION_STATUS_VERIFIED/COMPLETED 각 1개, return code 0, configuration register 검사 15개 PASS.

- JTAG cable `210319BE7725A`, device `xc7a100t_0`, die part `xc7a100t` 확인.
- DCP를 열어 구현 part `xc7a100tcsg324-1`을 별도로 확인. JTAG die로 package/speed grade를 실측했다고 주장하지 않는다.
- CRC/error 0, PLL/EOS/INIT/DONE 정상. 이는 **programming/configuration 상태 검사이며 독립적인 전체 configuration SRAM readback 비교가 아니다**.
- UART `/dev/serial/by-id/usb-Digilent_Digilent_USB_Device_210319BE7725-if01-port0` → `/dev/ttyUSB1`, 1,000,000 baud/8N1.
- 승인 host의 UART constructor에서 첫 RUN 이전 CAP 응답 version2/profile0x0810/capacity336×48×96·CRC를 검사했다. Smoke 정상 진행은 이 동기 gate 통과 근거다. Raw CAP frame은 현 host가 파일로 저장하지 않아 **not_collected**; 추가 CAP 거래를 넣지 않았다. 고정 `uart.cpp:240–256, 315–329` 참조.
- 로컬 hw_server의 ARM GDB port3000 사용 중 경고 1건은 기록했다. JTAG3121 연결/programming/CAP 실패가 아니며 job 재시도는 없었다.

새 hardware 구성은 유지했다. 자동 복원·재프로그래밍·flash 작업은 수행하지 않았다. `summary.json`의 `programming:false`는 sweep runner 자체의 동작이며 전체 실험의 별도 programming 1회를 부정하지 않는다.

## Correctness·identity·완료

[Smoke 로그/검사](../build/experiments/dense-host09-board-20260910T021038Z/smoke/validation.json), [새 host09 지연 진단 로그/검사](../build/experiments/dense-host09-board-20260910T021038Z/diagnostic/validation.json). 실제 ggml `run` 경로에서 numerical PASS와 FPGA backend를 확인했다. 두 실행의 CPU reference는 FPGA 출력 획득 뒤 독립 dot/matmul 검사이며 performance sample에 넣지 않았다. Smoke dot256/matmul1, 진단 dot30,816/matmul1을 실제 marker에서 확인했다.

Persistent 54회는 저장된 expected-raw/expected-fout exact 비교다. 모든 warm-up/측정에 `validation=expected_files_exact`, `cpu_reference_calls=not_instrumented`, `cpu_reference_path=not_called_source_audit`가 출력됐다. 미계측 CPU reference 호출을 runtime 0이라고 주장하지 않는다. FPGA sweep의 simulator create/execute/stream-begin wrapper counter는 각 조건 0이며 ggml 경로도 자체 gate를 통과했다. Layer classification fallback 문자열은 수치 backend fallback이 아니다.

모든 raw/f_out/padding·CRC/profile/run/generation/stripe/row·completion gate PASS. Device generation은 smoke1, 진단2, sweep3…56으로 연속이었다. Run id는 process마다 재시작하므로 generation과 함께 해석했다. Host event slot/context는 owned metadata이며 독립 wire echo counter가 아니다.

FULL 동기 gate는 공통 `UART::full`의 응답 검사 뒤 reducer/output commit 앞에 있다. Smoke·warm-up을 포함한 실제 FULL 31회가 모두 통과했다. 실패 뒤 RELEASE/RUN/BEGIN 없음의 negative 검사는 이전 host09 소프트웨어 증거이며 이번 보드에서 실패를 주입하지 않았다. 정상 56회 모두 RELEASE를 수행했다.

| FULL shape | FPGA 실행 수 | Expected / actual cycles | 차이 | 실제 cycle × nominal 40ns |
|---|---:|---:|---:|---:|
| 16/16/32 | 7 | 361 / 361 | 0 | 14.440 µs |
| 321/48/64 | 12 | 39,907 / 39,907 | 0 | 1.596280 ms |
| 321/48/96 | 12 | 58,695 / 58,695 | 0 | 2.347800 ms |

위 값은 provider start→core matmulDone counter 기반 resident elapsed이다. 외부 주파수 계측이나 순수 MAC 시간으로 부르지 않는다. R1 simulator의 긴 FULL 39,252/58,040은 다른 provider 결과이고 FPGA gate를 적용하지 않았다. PIPELINE elapsed에는 FULL expected를 적용하지 않았다.

## 측정 범위와 환경

FPGA sweep 완료 뒤 새 simulator sweep을 순차 실행했다. [FPGA 직전 process 목록](../build/experiments/dense-host09-board-20260910T021038Z/pre-board-processes.txt), [simulator 직전 목록](../build/experiments/dense-host09-board-20260910T021038Z/pre-simulator-processes.txt)에 동시 synthesis/RTL simulation/benchmark 없음. 동일 x86_64 호스트, Linux6.8.0-111-generic. 일반 OS/데스크톱 서비스는 존재하며 CPU 격리·외부 주파수 측정은 하지 않았다.

- Service: 해당 invocation의 준비 및 조건별 quantization, staging/전송/실행/수신/External reconstruction, 최종 f_out commit까지.
- Sustained: 위 service와 정상 RELEASE/Run retirement까지. Snapshot/timestamp 비용은 발생한 구간에 포함했다.
- Expected 파일 로드·전체 fixture exact 비교·상세 formatting/로그·일부 객체 정리 등 범위 밖 비용이 있다. 전체 process의 모든 비용을 포함한 throughput이 아니다.
- 전체 hash 순회와 큰 증거 저장은 반복 timer 밖. CRC/id/count/error 검사는 실제 경로에 유지. 사후 비용 차감 없음.
- Prequantized 조건은 quantization/capture 제외. Quantization 포함 FULL/live PIPELINE은 동일 원본 input-f32와 기존 producer/geometry 사용. Residual OFF. 임의 stripe override 없음.
- Device open/CAP와 simulator 초기화는 각 조건의 장기 실행 process에서 한 번이며 별도 INITIALIZATION 행으로 기록. FPGA만 W를 계속 resident하게 두는 조건은 사용하지 않았다. 모든 invocation은 기존 staging/RELEASE 계약을 유지했다.
- Transaction 30초, 조건별 process 900초, 진단 checkpoint 300초를 유지. 합법적 unpublished/raw-drain 대기를 절대 2^20 elapsed 한도로 바꾸지 않았다.

## Service: 원본 5개 측정 sample의 median [min, max]

단위 ms. Speedup = 같은 조건 simulator median / FPGA median. Warm-up 제외, 지연 진단 제외.

| M/N/K | 조건 | FPGA ms | Simulator ms | Speedup |
|---|---|---:|---:|---:|
| 16/16/32 | Prequantized FULL | 67.920 [65.571, 73.146] | 10.602 [10.372, 15.738] | 0.156088× |
| 321/48/64 | Prequantized FULL | 1,710.112 [1,709.372, 1,712.047] | 1,187.031 [1,181.704, 1,200.575] | 0.694125× |
| 321/48/96 | Prequantized FULL | 2,354.283 [2,352.599, 2,354.385] | 1,792.007 [1,770.827, 1,827.617] | 0.761169× |
| 321/48/64 | Deterministic PIPELINE | 1,863.586 [1,861.963, 1,874.978] | 1,282.299 [1,281.570, 1,287.315] | 0.688081× |
| 321/48/96 | Deterministic PIPELINE | 2,510.842 [2,501.800, 2,523.220] | 1,915.047 [1,902.309, 1,926.713] | 0.762711× |
| 321/48/64 | Quantization 포함 FULL | 1,710.886 [1,709.915, 1,711.146] | 1,183.290 [1,171.700, 1,207.888] | 0.691624× |
| 321/48/96 | Quantization 포함 FULL | 2,354.787 [2,354.605, 2,355.462] | 1,746.381 [1,741.013, 1,773.069] | 0.741630× |
| 321/48/64 | Post-fold live PIPELINE | 1,880.430 [1,877.299, 1,891.488] | 1,282.959 [1,276.476, 1,285.879] | 0.682269× |
| 321/48/96 | Post-fold live PIPELINE | 2,518.606 [2,517.466, 2,521.542] | 1,893.813 [1,891.036, 1,925.991] | 0.751929× |

## Sustained: 원본 5개 측정 sample의 median [min, max]

단위 ms. 정상 RELEASE/retirement 비용을 포함한다.

| M/N/K | 조건 | FPGA ms | Simulator ms | Speedup |
|---|---|---:|---:|---:|
| 16/16/32 | Prequantized FULL | 83.909 [81.382, 87.142] | 10.609 [10.379, 15.745] | 0.126435× |
| 321/48/64 | Prequantized FULL | 1,721.402 [1,720.546, 1,721.573] | 1,187.035 [1,181.707, 1,200.578] | 0.689574× |
| 321/48/96 | Prequantized FULL | 2,363.974 [2,361.856, 2,367.258] | 1,792.010 [1,770.830, 1,827.620] | 0.758050× |
| 321/48/64 | Deterministic PIPELINE | 1,879.197 [1,873.576, 1,890.358] | 1,282.302 [1,281.573, 1,287.318] | 0.682367× |
| 321/48/96 | Deterministic PIPELINE | 2,526.703 [2,515.133, 2,541.388] | 1,915.051 [1,902.312, 1,926.716] | 0.757925× |
| 321/48/64 | Quantization 포함 FULL | 1,722.638 [1,721.666, 1,723.181] | 1,183.293 [1,171.705, 1,207.891] | 0.686907× |
| 321/48/96 | Quantization 포함 FULL | 2,364.398 [2,363.504, 2,366.374] | 1,746.384 [1,741.016, 1,773.072] | 0.738617× |
| 321/48/64 | Post-fold live PIPELINE | 1,896.355 [1,890.535, 1,907.409] | 1,282.981 [1,276.501, 1,285.902] | 0.676551× |
| 321/48/96 | Post-fold live PIPELINE | 2,535.934 [2,533.354, 2,539.643] | 1,893.817 [1,891.041, 1,925.995] | 0.746793× |

## 같은 FPGA의 PIPELINE − FULL

동일 shape/입력/조건의 median 차이. 양수는 PIPELINE이 느림.

| M/N/K | 비교 | Service 차이 ms / 증가율 | Sustained 차이 ms / 증가율 |
|---|---|---:|---:|
| 321/48/64 | Prequantized | 153.473586 / 8.9745% | 157.794785 / 9.1666% |
| 321/48/64 | Quantization 포함 | 169.544212 / 9.9097% | 173.716554 / 10.0843% |
| 321/48/96 | Prequantized | 156.559548 / 6.6500% | 162.729716 / 6.8837% |
| 321/48/96 | Quantization 포함 | 163.818949 / 6.9568% | 171.536041 / 7.2550% |

## Device 관측과 availability

아래 cycle 통계는 측정 5회 median [min,max]. Host wait는 scheduler waitingForStripe endpoint, overlap은 engine active와 A/W/S request의 OR counter다. 두 값은 비가산이며 CPU quantization overlap 시간으로 해석하지 않는다.

| M/N/K | 조건 | Elapsed cycles | Host-wait cycles | Engine/provider overlap cycles |
|---|---|---:|---:|---:|
| 16/16/32 | Prequantized FULL | 361 [361, 361] | 0 [0, 0] | 32 [32, 32] |
| 321/48/64 | Prequantized FULL | 39,907 [39,907, 39,907] | 0 [0, 0] | 6,672 [6,672, 6,672] |
| 321/48/96 | Prequantized FULL | 58,695 [58,695, 58,695] | 0 [0, 0] | 10,724 [10,724, 10,724] |
| 321/48/64 | Deterministic PIPELINE | 43,788,652 [43,708,728, 44,157,476] | 38,452,822 [38,371,673, 38,756,446] | 6,111 [6,111, 6,111] |
| 321/48/96 | Deterministic PIPELINE | 59,408,650 [59,231,501, 59,816,622] | 53,787,494 [53,732,943, 54,193,891] | 10,143 [10,143, 10,143] |
| 321/48/64 | Quantization 포함 FULL | 39,907 [39,907, 39,907] | 0 [0, 0] | 6,672 [6,672, 6,672] |
| 321/48/96 | Quantization 포함 FULL | 58,695 [58,695, 58,695] | 0 [0, 0] | 10,724 [10,724, 10,724] |
| 321/48/64 | Post-fold live PIPELINE | 44,133,824 [44,124,750, 44,535,721] | 38,778,670 [38,664,195, 39,102,442] | 6,111 [6,111, 6,111] |
| 321/48/96 | Post-fold live PIPELINE | 59,772,196 [59,717,346, 59,782,871] | 54,113,265 [54,102,715, 54,187,740] | 10,143 [10,143, 10,143] |

PIPELINE elapsed는 publication/raw-drain 대기 포함. 같은 device domain에서만 ×40ns 환산할 수 있고 순수 compute 시간이 아니다. 모든 PIPELINE에서 stripe publication→raw completion 차이는 K64 19,889/19,890/1,089 cycles, K96 29,069/29,070/1,557 cycles였다. 첫 A는 첫 publication+8 cycles, 당시 published prefix160. 이는 invocation global first-A이며 stripe별 first-A는 미노출이다.

| Shape | 완료 works | K16 fragments | A/W requests | C writes / C ACK |
|---|---:|---:|---:|---:|
| 16/16/32 | 1 | 2 | 32 / 32 | 16 / 16 |
| 321/48/64 | 63 | 252 | 3,852 / 4,032 | 1,926 / 1,926 |
| 321/48/96 | 63 | 378 | 5,778 / 6,048 | 2,889 / 2,889 |

표는 실제 최종 device reply에서 읽은 반복별 고정 count이다. FPGA sweep54 합계는 works3,030/fragments15,132/A231,312/W242,112/C-write115,656/C-ACK115,656. Smoke+진단 포함56 합계는 works3,094/fragments15,386/A235,196/W246,176/C-write117,598/C-ACK117,598이다. C vector write count와 raw scalar 비교 수는 다른 단위다.

Publication/completion reply host tally는 sweep72/72, 진단 포함75/75. 각 PIPELINE 3/3이며 순서·row·identity를 검사했다. 독립 device publication/stripe-ACK total이 아니다. FINISH 성공은 shell의 retired_stripes==next_stripe 조건과 별도 host 검사 근거다.

| 필드 | 원천·단위/endpoint | Availability·lifetime |
|---|---|---|
| Elapsed/work/fragments/A/W/C-write/C-ACK | Device reply, core cycles/count | 직접 값. 완료 Run의 owned snapshot |
| host_wait/engine-provider overlap | Device reply, 위 endpoint의 cycles | 직접 값. 전체 대기/CPU overlap 아님 |
| Publication/raw completion cycles | 기존 PUBLISH/POLL reply, device cycles | Stripe별 owned 순서/row 보존 |
| Publication/completion 총수 | 검증된 reply의 host tally | 독립 device counter 아님 |
| Global first-A/published prefix | 기존 reply, 최초 A BRAM read issue | invocation당 1개, owned RUN_TELEMETRY가 기준 |
| Slot/context/host run | 수락 이벤트의 owned metadata | Wire가 독립 echo했다고 주장하지 않음 |
| Ready/accepted, write/receive/validated ns | Host steady clock, 기존 경계 | 직접 host timestamp. Device event 시각과 별도 |
| 순수 compute/per-stripe first-A | wire 없음 | not_exposed |
| 독립 device pub/stripe-ACK total | wire 없음 | not_exposed |
| 상세 wait/개별 overlap/scale-cache/lookahead | wire 없음 | not_exposed |
| 전용 backpressure duration/count | 이번 로그에 없음 | not_collected; ready→accepted로 제한적 관측 |
| Raw CAP frame | 검사 후 host가 파일 저장하지 않음 | not_collected; constructor gate 성공 근거 |

RUN 소멸 뒤 borrowed pointer를 읽지 않았으며 54 owned Run과 72 owned stripe가 sample identity별로 보존됐다. 0-valued legacy first-A 칸보다 RUN_TELEMETRY를 사용했다. Simulator UART 세부 time은 not_collected이며 기존 숫자0을 실측 duration으로 해석하지 않는다. 전체 [필드 계약](../fpga/dense_pipeline/host_instrumentation/TELEMETRY.md)은 변경하지 않았다.

## 주입 진단과 자연 실행

새 host09-02 지연 진단 1회가 실제 board에서 처음 실행돼 PASS했다. Device publication5,335,933 < first-A5,335,941 < raw completion5,355,822. Host checkpoint 시작1,539,983,732,899,069ns < first-A 응답 관측1,539,984,586,386,704ns < checkpoint 종료1,539,984,586,391,418ns < stripe1 ready1,539,984,586,605,283ns. 따라서 의도적으로 연장한 CPU 준비 window 중 FPGA 처리가 발생했다. Host ns와 device cycles를 직접 빼지 않았다. 진단 elapsed44,136,550/host-wait38,759,746/engine-overlap6,111 cycles는 별도 보존하며 성능 통계에 넣지 않았다.

무지연 live12회는 기존 producer의 immediate post-fold 수락, stripe160/160/1, slot0/1/0, theta−6/−3/0을 확인했다. 12회 모두 device publish0 < first-A < complete0 < publish1 < complete1 < publish2 < complete2 < final elapsed였다. 마지막 slot0의 수락은 첫 stripe 결과 응답 뒤였다.

하지만 세 post-fold ready는 12회 모두 첫 publication의 마지막 POSIX write 수락·publication 응답·first-A 응답 관측보다 앞섰다. 9/12회는 첫 write **시도** 뒤 73.761–476.018µs에 마지막 ready, 3/12회는 write 시도 이전에 모두 ready였다. Host 준비와 전송 구간의 일부 겹침은 관측됐지만 실제 device publication/compute 시각에 CPU quantization 연산이 겹쳤다고 판정할 수 없다.

마지막 ready는 quantization 함수 시작 뒤 K64 1.460311–1.911665ms, K96 1.863154–2.218069ms였다. 이후 stripe2 ready→accepted 구간은 K64 849.955294–855.573398ms, K96 1162.950200–1169.640282ms. 함수 전체 K64 851.896155–857.145495ms/K96 1165.119133–1171.753272ms에는 submit/backpressure 대기가 포함됐다. 이는 순수 CPU quantization 소요가 아니며, 전용 counter가 없으므로 정확한 credit 대기시간/재시도 횟수로 바꾸지 않는다.

**주입 진단의 인과 PASS, 자연 live의 수락 대기·slot 재사용 PASS, 자연 CPU quantization–FPGA compute overlap 미입증**을 구분한다. 더 좋은 overlap을 만들기 위한 추가 지연/수정/재실행은 하지 않았다.

## Transport와 구간별 관측

호출당 실제 host exchange 계수, 정상 RELEASE 포함·CAP 제외. 아래 byte/transaction 값은 각 조건의 6회 모두 동일했다. PIPELINE의 14 거래는 이번 관측값이며 고정 protocol 기대값으로 바꾸지 않는다.

| M/N/K | 조건 | Request bytes | Response bytes | Transactions |
|---|---|---:|---:|---:|
| 16/16/32 | Prequantized FULL | 4,168 | 1,320 | 2 |
| 321/48/64 | Prequantized FULL | 45,256 | 123,560 | 2 |
| 321/48/96 | Prequantized FULL | 47,304 | 185,192 | 2 |
| 321/48/64 | Deterministic PIPELINE | 45,688 | 125,336 | 14 |
| 321/48/96 | Deterministic PIPELINE | 47,736 | 186,968 | 14 |
| 321/48/64 | Quantization 포함 FULL | 45,256 | 123,560 | 2 |
| 321/48/96 | Quantization 포함 FULL | 47,304 | 185,192 | 2 |
| 321/48/64 | Post-fold live PIPELINE | 45,688 | 125,336 | 14 |
| 321/48/96 | Post-fold live PIPELINE | 47,736 | 186,968 | 14 |

56회 전체 CAP 제외 request2,306,672 / response7,587,248 bytes, 412 transactions. 각 6개 physical process의 CAP36/148 bytes·1 transaction을 더한 protocol 유도 총계는 request2,306,888 / response7,588,136 bytes, 418 transactions. CAP 추가분은 현 wire 형식과 확인된 생성 횟수의 파생값이며 UART tap 실측이 아니다. 실패가 없어 시도/완료 차이는 없었다.

아래 단위 ms, 측정 5회 median. 각 min/max는 analysis.json의 host_components_seconds에 보존. Transfer에는 device 실행이 겹칠 수 있으므로 resident cycle 시간과 단순 합산하지 않는다.

| M/N/K | 조건 | Adapter prepare | Transfer | Reconstruction | RELEASE |
|---|---|---:|---:|---:|---:|
| 16/16/32 | Prequantized FULL | 0.125800 | 67.535000 | 0.009279 | 15.836100 |
| 321/48/64 | Prequantized FULL | 1.211220 | 1707.600000 | 0.764412 | 11.165600 |
| 321/48/96 | Prequantized FULL | 1.379360 | 2351.650000 | 0.966997 | 9.679870 |
| 321/48/64 | Deterministic PIPELINE | 1.308130 | 1860.890000 | 0.807063 | 15.133500 |
| 321/48/96 | Deterministic PIPELINE | 1.361470 | 2507.890000 | 1.050930 | 15.850300 |
| 321/48/64 | Quantization 포함 FULL | 1.254660 | 1707.240000 | 0.691459 | 11.743400 |
| 321/48/96 | Quantization 포함 FULL | 1.238250 | 2350.240000 | 0.990589 | 9.409540 |
| 321/48/64 | Post-fold live PIPELINE | 1.376980 | 1876.050000 | 0.689123 | 15.836800 |
| 321/48/96 | Post-fold live PIPELINE | 1.388160 | 2514.930000 | 1.234850 | 17.565200 |

## 원본 보존·Git·실제 명령

[Preflight 결과](../build/experiments/dense-host09-board-20260910T021038Z/preflight/result.json), [before hash](../build/experiments/dense-host09-board-20260910T021038Z/before-sha256.json), [postflight](../build/experiments/dense-host09-board-20260910T021038Z/postflight/result.json): **7,917개 현재 경로 모두 동일 hash**. 승인 plan4983 항목, frozen package45, host/source/fixture/tool/test 증거와 과거 승인/실패 기록을 포함한다. 기존 package에 새 programming-attempted.txt만 생성됐고 기존 sealed member는 불변이다.

이전 host09 수정 단계의 root8 경로는 당시 변경된 상태이며 변경 전 blob57개를 baseline에 같은 hash로 보관했다(49 unchanged current+8 prior changed). 이전 FULL616 항목은 current611+이전 baseline의 원본5개로 구분했다. Old host08/source/approval/preflight failure와 FULL board825 결과도 보존 검사를 통과했다. “현재 모든 과거 경로가 처음부터 불변”이라는 주장이 아니다. 이번 **보드 실행 단계**에서는 source/host/fixture/plan 경로 변경 0개다.

| 저장소 | 실제 상태/변경 |
|---|---|
| IM2P.sim | `exp/pe-local-partial-int20`, HEAD `dc4a1a621f63834d64df42ae8c24152747d97971`. 기존 tracked modified20, diff516 insertions/107 deletions 유지 |
| llama.cpp-gemmini | develop pin 유지. tracked diff 없음, 기존 untracked models/gpt2·models/llama3.2-1B 유지 |
| Gemmini include | pin 유지, clean |

양 저장소의 status/log/diff --stat/diff --binary/cached/untracked/diff --check를 [시작](../build/experiments/dense-host09-board-20260910T021038Z/preflight)과 [측정 직후](../build/experiments/dense-host09-board-20260910T021038Z/postflight)에 저장해 byte-identical 확인했다. `git diff --check` 모두 PASS, index 변경 없음. 이 보고서만 새 versionable 문서로 추가되며 실제 최종 Git 출력은 final-git에 별도 저장한다. 자동 add/commit/tag/push/merge/rebase/PR 없음.

실제 명령은 [programming](../build/experiments/dense-host09-board-20260910T021038Z/programming/command.json), [smoke](../build/experiments/dense-host09-board-20260910T021038Z/smoke/command.json), [diagnostic](../build/experiments/dense-host09-board-20260910T021038Z/diagnostic/command.json), [board](../build/experiments/dense-host09-board-20260910T021038Z/board-command.json), [simulator](../build/experiments/dense-host09-board-20260910T021038Z/simulator-command.json)에 env/argv/timeout과 함께 저장했다. 각 sweep의 *.command.json에는 실제 persistent child CLI가 있다. 소스 재빌드 대신 승인된 경로의 실행파일을 그대로 사용했다.

## 최종 분류

| 분류 | 이번 결과 |
|---|---|
| Implemented | 기존 host09-02 그대로 사용. 추가 구현/수정/재빌드 없음 |
| Simulated | 보드 후 새 matched simulator54 PASS. 이전 software/RTL 회귀와 구분 |
| Routed | 불변 stream-02의 기존 25MHz route 참조. 이번 재route 아님 |
| Board measured | SRAM1회, FPGA56 PASS, 위 제한된 FULL/live PIPELINE |
| NOT RUN / not_exposed | 모델/residual/TTFT/TPOT, 독립 SRAM 전체 readback, 순수 compute/per-stripe first-A counter |
| 미입증 | 자연 CPU quantization–FPGA compute overlap, speedup>1 |
| Blocked / Awaiting approval | 이번 승인 범위는 모두 완료. 추가 구현·programming·측정은 수행하지 않음 |

첫 오류 중단 조건은 한 번도 발생하지 않았다. 실패 sample 삭제·성공할 때까지 retry·자동 reset/ABORT/복구/programming 0회. 승인된 56+54 이후 추가 GEMM은 실행하지 않았다.
