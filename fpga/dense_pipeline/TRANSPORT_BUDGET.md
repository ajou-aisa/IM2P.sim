# IFR2 Dense FULL/PIPELINE 전송 예산

이 문서는 완료된 실제 board-top RTL/PTY 실행의 packet과 counter를 집계한다.
물리 보드 전송 시간·성능 측정은 **NOT RUN**이다. UART 하한은 nominal
1,000,000 baud, 8N1에 따른 계산이며 실측값이 아니다. 새 packing·weight residency·
고속 링크는 구현하지 않았다.

공통 경로 `N`은
`build/experiments/dense-pipeline-20260909T141302Z`다. 입력·출력은 각 실행의
`trace/host-uart.bin`, `trace/device-uart.bin`에서 IFR2 header/payload/CRC를
다시 분리했다. 모든 frame의 CRC, 길이, run_id별 합계를 확인했다.
`host.log`의 metrics 및 `rtl.log`의 누적 byte 수와 일치한다.

| 근거 경로 (`N/tests/` 아래) | 경로·횟수 | shape | host |
|---|---|---|---|
| `final-persistent-long-full` | prequantized FULL, warm-up 1 + 실행 1 | M321/N48/K64 | stream-06 persistent_replay |
| `final-persistent-long-replay` | deterministic PIPELINE, warm-up 1 + 실행 1 | M321/N48/K64 | stream-06 persistent_replay |
| `final-persistent-long-live` | live quantization PIPELINE, warm-up 1 + 실행 1 | M321/N48/K96 | stream-06 persistent_replay |
| `final08-host-pipeline` | 실제 ggml dispatch, 1 invocation | M321/N48/K96 | stream-08 dense_host_dispatch |

이 횟수는 correctness 실행이다. 승인 후 계획한 warm-up 1 + 측정 5의
실보드 performance sweep을 완료했다는 뜻이 아니다. 마지막 두 경로는
geometry/byte 예산이 같지만, 서로 같은 입력을 사용한 성능 비교라고 주장하지
않는다. 실제 RTL driver는 모두 `stream-04/production/obj_dir/Vdense_uart_shell`이다.

## 실제 invocation별 packet

아래는 **CAP 제외, RELEASE 포함**한 sustained transaction의 byte 수다.
request 32-byte header + CRC 4-byte, response 144-byte header + CRC 4-byte를
모두 센다. Service는 마지막 RELEASE request/response 36/148 bytes를 제외한다.

| 경로 | Request bytes | Response bytes | Transactions | Core elapsed cycles |
|---|---:|---:|---:|---:|
| K64 FULL | 45,256 | 123,560 | 2 | 39,907 |
| K64 deterministic PIPELINE | 45,688 | 125,336 | 14 | 41,576,620 |
| K96 live PIPELINE | 47,736 | 186,968 | 14 | 56,998,528 |
| K96 ggml PIPELINE | 47,736 | 186,968 | 14 | 56,998,528 |

FULL은 FULL/RELEASE 각 1회다. 세 PIPELINE 기록은 invocation마다
BEGIN 1회, PUBLISH 3회, POLL 8회, FINISH 1회, RELEASE 1회다.
POLL 8회 중 raw completion은 3회, empty response는 5회다. Empty polling은
미래 보드에서도 반드시 5회라는 보장이 없으며, 이 기록의 실제 수량이다.

PUBLISH row 범위는 0–159, 160–319, 320이다. 각 request는
20,516 / 20,516 / 164 bytes다. Raw POLL response는 K64에서
61,588 / 61,588 / 532 bytes, K96에서 92,308 / 92,308 / 724 bytes다.
이 수량은 transport chunk이며 K16 fragment별 별도 command가 아니다.

프로세스 최초 CAP 1회는 request 36B/response 148B다. 누적 trace는 다음과 같다.

| 기록 | Logical jobs | 전체 request bytes | 전체 response bytes |
|---|---:|---:|---:|
| persistent K64 FULL | 2 | 90,548 | 247,268 |
| persistent K64 PIPELINE | 2 | 91,412 | 250,820 |
| persistent K96 live PIPELINE | 2 | 95,508 | 374,084 |
| ggml K96 PIPELINE | 1 | 47,772 | 187,116 |

예를 들어 K64 FULL 누적 request는 `2 × 45,256 + 36`이다.
CAP의 device open/profile 확인은 초기화 경계이며 반복 service 시간과 구분한다.
RELEASE는 지속 처리율에서 제외하지 않는다.

## 유효 code와 wire padding

기존 device stride를 그대로 사용한다. A row stride 128B, W row stride 64B,
C device row span 256B다. 출력 wire는 실제 `ceil(N/16)`개의 DIM16 word만 보낸다.
N48에서는 세 word, 즉 raw row당 192B다. Device의 사용하지 않는 네 번째
word 64B는 wire에 실리지 않는다.

| 저장 내용 | K64 유효 bytes | K64 wire bytes | K96 유효 bytes | K96 wire bytes |
|---|---:|---:|---:|---:|
| A signed8 code | 20,544 | 41,088 | 30,816 | 41,088 |
| W signed8 code | 3,072 | 4,096 | 4,608 | 6,144 |
| K32 block raw signed32 | 123,264 | 123,264 | 184,896 | 184,896 |

W의 유효 code bytes는 native packed block 파일 크기와 다르다. 기존 reader가
native Q8_H1을 읽어 finite fixture의 signed8 W를 staging한다. FPGA scale은
identity 1이다. FP weight factor와 stripe theta는 host의 기존 immutable
metadata/reconstruction에 남으므로 scale payload를 보내지 않는다고 scale
계산이 사라지는 것은 아니다.

K64 padding은 A 20,544B + W 1,024B, K96은 A 10,272B + W 1,536B다.
세 stripe로 나눠도 A 총 41,088B는 변하지 않는다. W는 BEGIN 전에 전체를
보내며, invocation마다 다시 전송한다. 현재 RELEASE/다음 generation 사이에
weight reuse를 약속하는 API는 없다. 다른 W나 metadata가 같은 프로세스에
들어올 수 있으므로 residency를 가정해 전송량을 줄이지 않았다.

## UART dependency 하한

1 Mbaud/8N1은 byte당 start 1 + data 8 + stop 1, 최소 10µs다.
현재 `dense_uart.sv`는 RX/LOAD에서만 request bytes를 소비한다.
HEADER/OUTPUT/CRC/DRAIN의 reply 전송이 끝난 뒤 RX로 돌아간다.
host `UART::exchange`도 request 전체 송신 후 response 전체 수신을 수행한다.
따라서 현재 protocol에서 host→device와 device→host 시간을 서로 겹친다고
가정할 수 없다. 물리 UART RX/TX pin이 따로 있다는 사실만으로 full-duplex
packet 처리를 주장하지 않는다. 응답 도중 다음 command를 밀어 넣는 것은
지원되지 않는다.

관측한 `events.jsonl`에서는 stripe1 publication이 stripe0 raw 전송의
`stripe_ack` 이후이며, stripe2도 같은 순서다. 현재 worker가 앞 stripe의 raw를
먼저 회수하므로 다음 A 전송과 앞 raw 송신이 동시에 진행된 증거는 없다.
K96 stripe0 계산 29,069 cycles는 nominal 1.162760ms로, publication ACK
148B의 wire 하한 1.480000ms보다 짧다. 이 작은 계산 구간이 ACK 송신과
겹칠 수 있다는 사실과 CPU의 후속 quantization overlap은 별개다.

현재 실제 transaction 수량에서 순수 wire 하한은 다음과 같다.

| 경로 | Service wire 하한 | RELEASE 포함 wire 하한 |
|---|---:|---:|
| K64 FULL | 1.686320 s | 1.688160 s |
| K64 PIPELINE | 1.708400 s | 1.710240 s |
| K96 PIPELINE | 2.345200 s | 2.347040 s |
| K96 FULL, 동일 shape의 계산 예산 | 2.323120 s | 2.324960 s |

K96 FULL 행은 이 문서의 완료 trace 집계 대상이 아니다. 계약상 request
47,304B / response 185,192B / 2 transactions의 계산 예산이며 cycle·성능
측정값을 채워 넣지 않았다. 별도 K96 FULL 실행 결과는 해당 실행의 완료
기록에서 판단한다.

FULL은 모든 A/W 전송·CRC 이후 start하며 done 이후 raw를 반환한다.
동일 provider/counter 조건의 K64에서는 `39,907 × 40ns = 1.596280ms`를
wire 하한에 더할 수 있다. 따라서 UART+resident dependency 하한은 service
1.687916280s, RELEASE 포함 1.689756280s다. 실제 host preparation,
reconstruction, scheduling, framing/FSM 간격은 이 하한에 포함되지 않는다.

PIPELINE은 publication ACK, POLL 요청·응답, 후속 A staging 중 core가 자율
진행할 수 있다. elapsed cycle에는 이 전송·대기 구간이 이미 포함된다.
따라서 `wire 시간 + PIPELINE cycles × 40ns`로 더하면 중복 계산이다.
표의 wire 합계만 하한으로 사용하며, 필요한 잔여 compute/producer wait는
실제 dependency에 따라 별도로 평가해야 한다.

관측한 empty POLL 5회를 제거한다는 분석상 가정에서는 invocation당
920B, nominal 9.2ms가 줄어든다. 최소 BEGIN/PUBLISH/POLL/FINISH/RELEASE
9 transactions의 K64 wire 하한은 1.701040s, K96은 2.337840s다.
이 경로는 **미구현 분석**이다. 현재 client 수량을 사후 보정하지 않는다.

## Counter endpoint와 overlap 해석

`DensePipeline.bsv`는 start의 `core.rtlCycleCount`를 저장하고
`core.matmulDone`에서 차이를 고정한다. FULL은 입력 preload 이후 실행 구간이다.
PIPELINE은 BEGIN 이후 첫 A 전송/publication 대기와 후속 stripe 대기를 포함한다.
둘의 cycle 값을 같은 MAC-only 시간으로 비교하지 않는다.

| Counter | K64 FULL | K64 PIPELINE | K96 PIPELINE |
|---|---:|---:|---:|
| Elapsed cycles | 39,907 | 41,576,620 | 56,998,528 |
| Nominal counter time | 1.596280 ms | 1.663064800 s | 2.279941120 s |
| K16 fragments | 252 | 252 | 378 |
| DIM output works | 63 | 63 | 63 |
| A provider requests | 3,852 | 3,852 | 5,778 |
| W provider requests | 4,032 | 4,032 | 6,048 |
| C writes / ACKs | 1,926 / 1,926 | 1,926 / 1,926 | 2,889 / 2,889 |
| stripeHostWaitCycles | 0 | 36,348,637 | 51,751,717 |
| overlapCycles | 6,672 | 6,111 | 10,143 |
| First A read / published rows | 6 / 321 | 5,187,120 / 160 | 5,187,120 / 160 |

`IM2PCore.bsv:551`의 host-wait counter는 scheduler가 stripe를 기다리는
상태를 센다. `overlapCycles`는 engine active와 activation/weight/scale 요청의
동시 상태다. **CPU quantization과 FPGA 실행의 overlap counter가 아니다.**
이 counter들은 비가산이며 elapsed에서 모두 빼서 새 compute 시간을 만들지 않는다.

K96 ggml 기록의 publication→raw completion cycle은 stripe0
5,187,112→5,216,181, stripe1 33,646,280→33,675,350, stripe2
56,996,968→56,998,525다. 각 raw completion까지 29,069 / 29,070 / 1,557
cycles다. 아직 공개하지 않은 다음 행을 기다리는 큰 간격과 구분된다.
모든 raw가 host에서 reconstruction·최종 commit됐다는 증거는 별도 numerical
marker와 identity/count 검사다. 마지막 raw completion 하나로 최종 f_out을
받았다고 간주하지 않는다.

현재 지연 주입 없는 실행에서 **자연스러운 CPU quantization/FPGA compute
overlap은 관측하지 못했다.** 실제 post-fold event는 0/1/0 순서로 즉시 제출됐지만,
CPU는 후속 stripe의 수치 준비를 빠르게 끝내고 세 번째 event credit에서 기다렸다.
`final08-host-pipeline`의 세 quantization window는 각각 536,512ns,
511,852ns, 3,841ns다. 세 번째 event의 accepted timestamp가 크게 늦다는 것은
backpressure 증거이며 CPU가 계속 quantization했다는 증거가 아니다.
Persistent live의 전체 quantization_end에도 마지막 sink의 blocking 시간이
포함되므로 이를 유용한 CPU 계산 overlap으로 해석하지 않는다.

Host `steady_clock`과 FPGA cycle은 다른 domain이다. PTY 실행의 수백 초
`service_ns`는 Verilator 실행 비용과 OS scheduling을 포함한다. 이를
실보드 UART 성능이나 simulator 대비 가속비로 쓰지 않는다. 인위적 producer
delay로 입증한 별도 causal overlap과 지연 없는 performance 검증도 구분한다.

## 진행성·timeout·후속 분석

`dense_uart.sv:149`의 published-work watchdog은 20-bit counter다.
nominal `2^20 / 25MHz = 41.943040ms` 동안 A/W/fragment/C-ACK progress가
없을 때 sticky error를 만든다. 다만 idle/done, pending stripe completion,
`published_rows == completed_rows`인 합법적 producer 대기에서는 리셋한다.
미공개 stripe를 기다리는 시간을 core deadlock으로 취급하지 않는다.

RX/LOAD의 별도 20-bit interbyte watchdog은 진행 중 frame의 byte 간격을
검사한다. 따라서 수초짜리 정상 전체 payload를 41.943ms 제한으로 잘라내지
않으며, 전송 중 단절은 검출한다. Host transaction timeout은 별도 wall clock이다.
PTY에서는 느린 RTL 모델을 위해 긴 host timeout을 사용했고, 이 값을 물리 보드의
timeout으로 복사하지 않는다. 승인 패키지의 physical timeout은 위 최대 packet
전송 시간, 합법적 producer wait, 처리 여유를 근거로 따로 고정해야 한다.

개선 후보는 별도 작업으로 남긴다. K96에서 raw 184,896B 자체가 nominal
1.848960s를 요구한다. W residency만 도입해도 이 반환량은 줄지 않는다.
Native raw block 순서와 External reducer의 double accumulation 순서를
유지해야 하므로 모든 K block을 정수 합산하는 방법은 대안이 아니다.
Padding 제거, 불필요한 empty POLL 감소, 명시적 W residency, RX/TX 동시 처리에는
각각 다른 protocol/lifetime/FSM 변경과 재검증이 필요하다. 이번 correctness
후보에 포함하지 않았다.

대표 원본 SHA256:

| trace | SHA256 |
|---|---|
| K64 FULL host-uart | `700d30946023eb36953673c9093de0e897f47e47edb1d40a88770c9ac1c5b851` |
| K64 FULL device-uart | `49aca72c1415b412cf68f4e79021b71ce6cb036214f69e0dc80430c8faef16e4` |
| K64 PIPELINE host-uart | `c103836803bda0cd56c5316336fb35d87b535cd3e870375cde8550e8a84bd112` |
| K64 PIPELINE device-uart | `4ce69120ef703ef44796adbce58485c42737415db3d8b01fa7ebef8b02ac134f` |
| K96 persistent live host-uart | `a69756858ff5a944d5c919454703051c5978e0f9d1254459f2fc01ad7e6a4990` |
| K96 persistent live device-uart | `9db26920caf2eec7045042776786ff6f3d6c5cb6d3df1bf116ed66d84e23218b` |
| K96 ggml host-uart | `5da60f6ade50616f2ec26e5cc8a9d2d7ad4eed57369136c9800bea5bc6cad42b` |
| K96 ggml device-uart | `e055645e4236b1d9bc0487f0f7b9c6612505cd4de74d036d970df73bcdde955b` |

집계 중 원본 packet·log·host·RTL을 수정하지 않았다. 새 board job, programming,
flash write, 모델/PIPELINE 성능 가속 주장은 없다.


## 최종 host08 K96 FULL 추가 완료

`N/tests/final08-host-full-k96`가 완료됐다. 실제 ggml FULL1 job, raw46,224/f_out15,408 exact,
simulator calls0이다. CAP 제외·RELEASE 포함 request47,304 B/response185,192 B/2 transactions,
core58,695 cycles로 위 K96 FULL의 wire 예산과 일치한다. CAP 포함 trace는47,340/185,340 B다.
Nominal counter-derived resident 시간은2.347800 ms이며, 순차 wire+resident 하한은
service2.325467800 s/sustained2.327307800 s다. 실제 host wall-clock은 RTL emulation의 값이므로
물리 보드 성능으로 사용하지 않는다. 최종 동일 host08 K96 FULL/PIPELINE A/W payload47,232 B와
raw46,224 lanes가 직접 일치한다(`N/evidence/final08-full-pipeline-equality.json`).
