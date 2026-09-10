# P2 SRAM programming 및 실제 board batch 측정

측정일: 2026-09-09. **고정 overhead는 상각됐지만, 모든 job 결과를 회수하는 현재 UART 경로에서 crossover는 없었다.** Resident B64는 **10.506064 ms/job, 0.145474×**, bulk B64는 **10.731338 ms/job, 0.142421×**다. 사용자가 제시한 **경우 2: UART 결과 payload bandwidth 병목**에 해당한다.

기존 [P1/P2 구현 기록](FPGA_P2_BATCHING.md)은 programming 전 milestone로 그대로 보존한다. 이 문서와 [hardware-measurement-20260909-135006](../build/experiments/fpga-p2-20260909-131644/hardware-measurement-20260909-135006)는 승인 후 실제 보드 실행 기록이다.

## Baseline

- Branch: `exp/pe-local-partial-int20`; HEAD: `dc4a1a621f63834d64df42ae8c24152747d97971`.
- 작업 시작 시 tracked 17 files가 dirty이고 기존 연구 문서 3개가 untracked였다. HEAD만으로 실행한 source를 식별하지 않는다. [측정 전 Git 상태](../build/experiments/fpga-p2-20260909-131644/hardware-measurement-20260909-135006/git-before.txt), [정확한 P2 입력 snapshot](../build/experiments/fpga-p2-20260909-131644/P2-reset-safe/inputs), [입력 SHA256](../build/experiments/fpga-p2-20260909-131644/P2-reset-safe/inputs-sha256.json), [shell 변경 계보](../build/experiments/fpga-p2-20260909-131644/P2-reset-safe/shell.patch)를 함께 사용한다.
- Vivado 2025.2, SW Build 6299465. 구현 part `xc7a100tcsg324-1`; JTAG device `xc7a100t_0`; Digilent cable `210319BE7725A`.
- 승인된 P2 bitstream: `a22b27e8944553d61a387e3e975caa41cdcf6d979eeefbd96ac7ce350ea6ed15`.
- 해당 route DCP: `d01f885e534b568ad372e1db923b2920d944a410ae36c0cf3fa56516a042640c`.
- 보존한 P1 bitstream: `560d5496a6b8de9f0eb5a694583085164d3c47e9bf90157fc0822ac3b5198b3c`.
- Workload: A8/W8, physical DIM16, M=N=16, K=32. 모든 job은 동일 resident A/B/scale을 사용한다. Input 1,040 bytes SHA256: `2c47e48ed72132c6cdfca1384dd3e66ecb03a733f0481a33581be41cc138a9dc`.
- Nominal FPGA clock 25 MHz, UART 1 Mbaud / 8N1. 외부 clock/baud 계측은 하지 않았다. FTDI `latency_timer`는 전후 모두 16 ms였다.

## Hypothesis

한 batch의 command/completion 왕복은 줄지만 1,024 bytes/job 결과 전송량은 유지된다. 따라서 B 증가로 고정 overhead가 상각된 뒤 약 10.24 ms/job의 nominal UART payload 하한에 접근할 것으로 예상했다.

## Isolated change / Programming

이미 승인된 P2 artifact를 **configuration SRAM에 1회만 volatile programming**했다. 이번 승인 이후 RTL/source/bitstream 변경, 재합성, 다른 bitstream programming, flash/non-volatile write, commit/push는 없었다.

[Programming Tcl](../build/experiments/fpga-p2-20260909-131644/hardware-measurement-20260909-135006/program-approved-once.tcl)은 전체 bitstream/DCP hash, checkpoint part, cable/device를 검사하고, programming 직전에 전체 bitstream hash를 다시 확인했다. `program_hw_devices`는 한 번 실행됐다. 이후 refresh한 상태에서 DONE/INIT/EOS/PLL=1, CRC/IDCODE/security/HMAC/bad-packet/over-temperature error=0을 확인했다. 종료값 0, programming 전·직전·후 hash 모두 일치했다. [원본 programming log](../build/experiments/fpga-p2-20260909-131644/hardware-measurement-20260909-135006/programming.log), [검증 JSON](../build/experiments/fpga-p2-20260909-131644/hardware-measurement-20260909-135006/programming-verification.json), [실행 명령](../build/experiments/fpga-p2-20260909-131644/hardware-measurement-20260909-135006/programming-command.json)을 보존했다.

[원본 host 측정기](../build/experiments/fpga-p2-20260909-131644/hardware-measurement-20260909-135006/host_batch.py)를 수정 없이 복사해 실행했다. 측정 중 programming 호출은 0이었다. 기존 P1 route/report/programming artifact, P2 입력/artifact, root tracked source 등 **592개 파일의 SHA256이 측정 전후 동일**하다. [사전 manifest](../build/experiments/fpga-p2-20260909-131644/hardware-measurement-20260909-135006/before-sha256.json), [측정 상태](../build/experiments/fpga-p2-20260909-131644/hardware-measurement-20260909-135006/measurement-status.json), [최종 보존 검증](../build/experiments/fpga-p2-20260909-131644/hardware-measurement-20260909-135006/final-verification.json)을 참조한다.

## Correctness

Resident와 bulk 각각 B=1,2,4,8,16,32,64, 각 크기 warm-up 1회 제외 후 5회다. 중간 실패·timeout·재시도 없이 완료했다.

- 측정 70 samples: **1,270/1,270 jobs, 325,120/325,120 INT32 outputs PASS**.
- Warm-up 포함 기록 84 samples: **1,524/1,524 jobs, 390,144/390,144 outputs PASS**.
- 최초 resident input 확립을 위한 별도 B1 bulk 1 job도 검증했다. 이 실행은 sample 파일 생성 전 수행되므로 위 통계·합계에 포함하지 않는다.
- Header/version/status/count, core cycle 합계, 모든 job의 256개 출력값을 Python golden과 exact 비교했다. 이번 측정에는 workload 변경이나 새로운 numerical edge vector를 넣지 않았다.
- 응답 header의 count는 요청 batch size다. 여기서 completed jobs는 성공 completion 응답, cycle 합계, 전체 출력 검증으로 확인한 job 수이며, 별도 hardware completed counter를 직접 읽은 값은 아니다.

아래 수는 **resident/bulk 각 mode의 5회 합계**다. 두 mode 모두 동일하게 PASS했다.

| B | Expected jobs | Completed jobs | Correct outputs / total outputs |
| --- | --- | --- | --- |
| 1 | 5 | 5 | 1,280 / 1,280 |
| 2 | 10 | 10 | 2,560 / 2,560 |
| 4 | 20 | 20 | 5,120 / 5,120 |
| 8 | 40 | 40 | 10,240 / 10,240 |
| 16 | 80 | 80 | 20,480 / 20,480 |
| 32 | 160 | 160 | 40,960 / 40,960 |
| 64 | 320 | 320 | 81,920 / 81,920 |

## Cycle

모든 기록에서 core 합계/B = **361 cycles/job**, batch execution/B = **412 cycles/job**였다. 기존 P1 및 P2 RTL의 core 361과 delta 0이다. P2 batch execution은 local copy/ack/relaunch를 포함하며 P2 RTL 412와 delta 0이다. 개별 job timestamp를 읽은 것은 아니다.

표의 cycle 및 nominal 시간은 모든 sample에서 동일하여 median=min=max다. Nominal 시간은 hardware counter ÷ 25 MHz이며 외부 장비 측정값이 아니다.

| B | Core cycles 합계 | Total batch cycles | Core / batch cycles per job | Nominal core ms/batch | Nominal batch ms/batch |
| --- | --- | --- | --- | --- | --- |
| 1 | 361 | 412 | 361 / 412 | 0.014440 | 0.016480 |
| 2 | 722 | 824 | 361 / 412 | 0.028880 | 0.032960 |
| 4 | 1444 | 1648 | 361 / 412 | 0.057760 | 0.065920 |
| 8 | 2888 | 3296 | 361 / 412 | 0.115520 | 0.131840 |
| 16 | 5776 | 6592 | 361 / 412 | 0.231040 | 0.263680 |
| 32 | 11552 | 13184 | 361 / 412 | 0.462080 | 0.527360 |
| 64 | 23104 | 26368 | 361 / 412 | 0.924160 | 1.054720 |

Per-job nominal core compute = **0.014440 ms**, batch execution = **0.016480 ms**다. UART 결과 전송은 batch cycle counter 구간에 포함되지 않는다.

## Board/system — per-job end-to-end

`T_job(B) = T_batch,total(B) / B`, `S_E2E(B) = 1.528364 ms / T_job(B)`를 사용한다. 기존 단일-job Verilator median을 선형 비교 기준으로 사용했으며, 새 Verilator batch 실측값은 아니다.

Resident는 미리 적재된 input을 재사용한다. Bulk는 **동일 1,040-byte input을 batch당 한 번** 적재하고 B개 job이 반복 사용한다. Job마다 서로 다른 input을 업로드하는 workload로 해석하지 않는다.

Wall-clock 구간은 input 업로드 시작(resident는 batch command 직전)부터 마지막 결과 byte를 host에서 받은 시점까지다. Python 시작, 최초 resident 확립, 수치 검증/golden 계산은 시간 구간 밖이다. 각 cell은 **median / min / max**, 시간은 ms다.

### Resident

| B | Batch total ms | Per-job total ms | Per-job E2E speedup |
| --- | --- | --- | --- |
| 1 | 26.474732 / 25.970729 / 28.479979 | 26.474732 / 25.970729 / 28.479979 | 0.057729 / 0.053665 / 0.058849 |
| 2 | 36.332086 / 35.995924 / 36.826302 | 18.166043 / 17.997962 / 18.413151 | 0.084133 / 0.083004 / 0.084919 |
| 4 | 56.975809 / 56.604719 / 61.139449 | 14.243952 / 14.151180 / 15.284862 | 0.107299 / 0.099992 / 0.108003 |
| 8 | 98.188771 / 98.105002 / 103.457194 | 12.273596 / 12.263125 / 12.932149 | 0.124525 / 0.118183 / 0.124631 |
| 16 | 182.464134 / 180.126552 / 183.715609 | 11.404008 / 11.257910 / 11.482226 | 0.134020 / 0.133107 / 0.135759 |
| 32 | 343.904851 / 343.607489 / 344.187272 | 10.747027 / 10.737734 / 10.755852 | 0.142213 / 0.142096 / 0.142336 |
| 64 | 672.388101 / 671.982752 / 675.567917 | 10.506064 / 10.499731 / 10.555749 | 0.145474 / 0.144790 / 0.145562 |

### Bulk

| B | Batch total ms | Per-job total ms | Per-job E2E speedup |
| --- | --- | --- | --- |
| 1 | 39.363609 / 37.792409 / 41.061045 | 39.363609 / 37.792409 / 41.061045 | 0.038827 / 0.037222 / 0.040441 |
| 2 | 49.332014 / 48.350339 / 63.506625 | 24.666007 / 24.175169 / 31.753312 | 0.061962 / 0.048132 / 0.063220 |
| 4 | 69.270397 / 68.104945 / 71.222947 | 17.317599 / 17.026236 / 17.805737 | 0.088255 / 0.085835 / 0.089765 |
| 8 | 110.376400 / 109.849513 / 113.414399 | 13.797050 / 13.731189 / 14.176800 | 0.110775 / 0.107807 / 0.111306 |
| 16 | 193.576654 / 191.961762 / 208.758327 | 12.098541 / 11.997610 / 13.047395 | 0.126326 / 0.117139 / 0.127389 |
| 32 | 361.304444 / 357.893742 / 371.296333 | 11.290764 / 11.184179 / 11.603010 | 0.135364 / 0.131721 / 0.136654 |
| 64 | 686.805635 / 684.677554 / 698.739116 | 10.731338 / 10.698087 / 10.917799 | 0.142421 / 0.139988 / 0.142863 |

## Board/system — host 시간 구간

각 cell은 **median / min / max**다. Descriptor는 `B,count`를 쓰는 host API 시간(µs), 나머지는 batch당 ms다. Bulk의 load command는 input+publication에 포함된다.

`start → completion`의 host 관측값으로 **start → completion header 수신**을 기록했다. 이 값에는 UART/USB/OS 지연이 포함되며 FPGA 내부 완료 순간과 다르다. Result receive는 header를 읽은 뒤 나머지 payload를 모두 읽을 때까지의 host 시간이다. 이미 버퍼에 도착한 byte가 있을 수 있으므로 순수 UART wire 시간으로 해석하지 않는다.

Descriptor 시간은 start→header의 일부다. 각 sample에서 start→header + result receive = command+result이며, bulk total에는 input+publication이 더해진다. 서로 다른 sample에서 나온 phase median들을 합하면 total median과 일치하지 않을 수 있다.

### Resident phase statistics

| B | Input+publication ms | Descriptor API µs | Start→first byte ms | Start→completion header ms | Result receive ms |
| --- | --- | --- | --- | --- | --- |
| 1 | 0.000000 / 0.000000 / 0.000000 | 43.542 / 36.165 / 44.360 | 5.551095 / 5.510021 / 5.614906 | 5.579665 / 5.536688 / 5.641961 | 20.894620 / 20.430972 / 22.884938 |
| 2 | 0.000000 / 0.000000 / 0.000000 | 43.912 / 40.783 / 57.181 | 5.627317 / 5.521585 / 10.324023 | 5.654968 / 5.550400 / 10.358685 | 30.437525 / 25.972501 / 31.170777 |
| 4 | 0.000000 / 0.000000 / 0.000000 | 41.501 / 40.527 / 61.324 | 5.534289 / 5.421180 / 5.738299 | 5.555863 / 5.429969 / 5.767718 | 51.354768 / 50.836221 / 55.708678 |
| 8 | 0.000000 / 0.000000 / 0.000000 | 43.892 / 41.415 / 48.709 | 5.563382 / 5.481618 / 5.747128 | 5.596759 / 5.508633 / 5.776662 | 92.581195 / 92.352447 / 97.948058 |
| 16 | 0.000000 / 0.000000 / 0.000000 | 41.860 / 39.719 / 84.346 | 5.935657 / 5.840329 / 5.967533 | 5.964972 / 5.870301 / 5.997020 | 176.566790 / 174.157355 / 177.718186 |
| 32 | 0.000000 / 0.000000 / 0.000000 | 43.529 / 40.332 / 45.778 | 6.188809 / 5.872229 / 7.970366 | 6.216723 / 5.894023 / 7.998068 | 337.429510 / 335.608611 / 338.078657 |
| 64 | 0.000000 / 0.000000 / 0.000000 | 41.875 / 15.843 / 84.003 | 6.663769 / 6.339751 / 6.692210 | 6.690607 / 6.367996 / 6.710436 | 665.677429 / 665.431651 / 668.876828 |

### Bulk phase statistics

| B | Input+publication ms | Descriptor API µs | Start→first byte ms | Start→completion header ms | Result receive ms |
| --- | --- | --- | --- | --- | --- |
| 1 | 12.905946 / 11.947227 / 14.929273 | 46.533 / 17.894 / 51.444 | 5.271136 / 5.258231 / 5.733874 | 5.282439 / 5.267125 / 5.756615 | 20.357969 / 20.275203 / 22.872660 |
| 2 | 12.541919 / 11.523226 / 26.931276 | 50.964 / 45.039 / 53.801 | 5.459328 / 5.364664 / 7.109152 | 5.479983 / 5.391560 / 7.139165 | 31.097098 / 28.810200 / 32.773683 |
| 4 | 12.294351 / 11.264705 / 13.933524 | 51.059 / 46.834 / 64.488 | 5.687093 / 5.445494 / 5.699734 | 5.715801 / 5.469565 / 5.727757 | 51.472328 / 51.113862 / 51.573622 |
| 8 | 12.491502 / 12.105561 / 15.948411 | 50.416 / 49.294 / 54.063 | 5.639648 / 5.576417 / 5.796544 | 5.668280 / 5.599730 / 5.827433 | 92.216618 / 91.652472 / 92.398674 |
| 16 | 12.447701 / 12.164710 / 26.176951 | 49.925 / 47.182 / 67.401 | 5.723428 / 5.643527 / 5.804666 | 5.746865 / 5.657233 / 5.832261 | 175.402325 / 173.881283 / 176.876923 |
| 32 | 13.396991 / 11.786507 / 27.484251 | 52.738 / 24.416 / 58.729 | 5.943460 / 5.813253 / 6.016803 | 5.969101 / 5.832908 / 6.042290 | 338.454461 / 337.944468 / 343.521427 |
| 64 | 12.460802 / 10.854687 / 26.654148 | 55.517 / 22.946 / 67.526 | 6.493543 / 6.390123 / 8.709880 | 6.521037 / 6.404419 / 8.736244 | 666.451607 / 664.044817 / 669.336961 |

## Bytes / UART transactions

Batch descriptor는 command `B` 1 byte + count 1 byte다. Resident는 TX 2 bytes, RX `24 + 1024B` bytes, **1 protocol round-trip/batch**다. Bulk는 load `L` 1 byte + input 1,040 bytes를 추가하므로 TX 1,043 bytes, RX `26 + 1024B` bytes(load ack 2 bytes 포함), **2 protocol round-trips/batch**다. 다음 byte 수는 모든 sample에서 일정하다.

| B | Command/descriptor bytes resident / bulk | Input bytes resident / bulk | Result bytes | RX bytes resident / bulk | Round-trips resident / bulk |
| --- | --- | --- | --- | --- | --- |
| 1 | 2 / 3 | 0 / 1040 | 1024 | 1048 / 1050 | 1 / 2 |
| 2 | 2 / 3 | 0 / 1040 | 2048 | 2072 / 2074 | 1 / 2 |
| 4 | 2 / 3 | 0 / 1040 | 4096 | 4120 / 4122 | 1 / 2 |
| 8 | 2 / 3 | 0 / 1040 | 8192 | 8216 / 8218 | 1 / 2 |
| 16 | 2 / 3 | 0 / 1040 | 16384 | 16408 / 16410 | 1 / 2 |
| 32 | 2 / 3 | 0 / 1040 | 32768 | 32792 / 32794 | 1 / 2 |
| 64 | 2 / 3 | 0 / 1040 | 65536 | 65560 / 65562 | 1 / 2 |

Host가 job/row마다 요청하지 않는다. B개 job 결과는 하나의 응답 stream으로 온다. 아래 actual OS API counts는 **median / min / max**다. Read call 증가분은 stream chunking이며 per-job protocol 왕복이 아니다. USB packet/transaction 수는 별도 USB trace를 수집하지 않아 미측정이다.

| Mode | B | write calls | read calls | select calls | Effective result receive B/s |
| --- | --- | --- | --- | --- | --- |
| resident | 1 | 1 / 1 / 1 | 5 / 5 / 5 | 6 / 6 / 6 | 49007.8 / 44745.6 / 50120.0 |
| resident | 2 | 1 / 1 / 1 | 7 / 7 / 7 | 8 / 8 / 8 | 67285.4 / 65702.6 / 78852.6 |
| resident | 4 | 1 / 1 / 1 | 11 / 10 / 11 | 12 / 11 / 12 | 79758.9 / 73525.3 / 80572.5 |
| resident | 8 | 1 / 1 / 1 | 19 / 19 / 19 | 20 / 20 / 20 | 88484.5 / 83636.2 / 88703.7 |
| resident | 16 | 1 / 1 / 1 | 35 / 34 / 35 | 36 / 35 / 36 | 92792.1 / 92190.9 / 94075.8 |
| resident | 32 | 1 / 1 / 1 | 66 / 66 / 67 | 67 / 67 / 68 | 97110.7 / 96924.2 / 97637.5 |
| resident | 64 | 1 / 1 / 1 | 130 / 130 / 130 | 131 / 131 / 131 | 98450.1 / 97979.2 / 98486.4 |
| bulk | 1 | 2 / 2 / 2 | 6 / 6 / 6 | 8 / 8 / 8 | 50299.7 / 44769.6 / 50505.0 |
| bulk | 2 | 2 / 2 / 2 | 8 / 8 / 8 | 10 / 10 / 10 | 65858.2 / 62489.2 / 71085.9 |
| bulk | 4 | 2 / 2 / 2 | 12 / 12 / 12 | 14 / 14 / 14 | 79576.7 / 79420.4 / 80134.8 |
| bulk | 8 | 2 / 2 / 2 | 20 / 20 / 20 | 22 / 22 / 22 | 88834.3 / 88659.3 / 89381.1 |
| bulk | 16 | 2 / 2 / 2 | 36 / 35 / 36 | 38 / 37 / 38 | 93408.1 / 92629.4 / 94225.2 |
| bulk | 32 | 2 / 2 / 2 | 68 / 67 / 68 | 70 / 69 / 70 | 96816.6 / 95388.5 / 96962.7 |
| bulk | 64 | 2 / 2 / 2 | 131 / 130 / 132 | 133 / 132 / 134 | 98335.7 / 97911.8 / 98692.1 |

Effective result bandwidth = `1024B / result_receive_host_seconds`다. B64 resident **98,450.1 B/s**, bulk **98,335.7 B/s**다. Nominal 1 Mbaud 8N1 payload 최대 **100,000 B/s**와 가깝다. 이 bandwidth 역시 host 수신 구간 기준이며 line rate 외부 실측은 아니다.

## Fixed overhead / batch와 per-job 분해

각 B의 5회 total median에 `T_batch(B) = L_fixed + slope × B`를 최소제곱 fit했다.

| Mode | L_fixed ms/batch | Slope ms/job | R² |
| --- | --- | --- | --- |
| resident | 16.376739448 | 10.252687463 | 0.99998476 |
| bulk | 28.842550816 | 10.300246435 | 0.99996676 |

`L_fixed`는 합성된 고정 비용 추정치이며 독립 측정한 command latency가 아니다. Resident 약 16.38 ms는 FTDI latency_timer 16 ms와 크기가 유사하지만, driver 설정을 바꾸는 인과 실험을 하지 않았으므로 같은 원인이라고 확정하지 않는다. Bulk intercept는 batch당 한 번의 input 적재도 포함한다.

`T(B)=L_fixed+B*T_compute+B*Din/BW_in+B*Dout/BW_out` 관점에서 이번 bulk의 input은 batch당 한 번이므로 input 항이 고정 비용에 포함된다. 동일 payload만 사용한 이 sweep으로 각 bandwidth와 모든 latency 항을 독립 식별할 수는 없다.

다음은 median 기준 ms/job다. Fitted fixed/B는 모델값, result receive는 관측값이므로 둘을 서로 독립된 항으로 더하지 않는다. Nominal core 시간은 모든 B에서 0.014440 ms/job, local 작업 포함 batch 시간은 0.016480 ms/job다.

| B | Fixed/B resident / bulk | Result receive/job resident / bulk | Core compute/job | Total/job resident / bulk |
| --- | --- | --- | --- | --- |
| 1 | 16.376739 / 28.842551 | 20.894620 / 20.357969 | 0.014440 | 26.474732 / 39.363609 |
| 2 | 8.188370 / 14.421275 | 15.218763 / 15.548549 | 0.014440 | 18.166043 / 24.666007 |
| 4 | 4.094185 / 7.210638 | 12.838692 / 12.868082 | 0.014440 | 14.243952 / 17.317599 |
| 8 | 2.047092 / 3.605319 | 11.572649 / 11.527077 | 0.014440 | 12.273596 / 13.797050 |
| 16 | 1.023546 / 1.802659 | 11.035424 / 10.962645 | 0.014440 | 11.404008 / 12.098541 |
| 32 | 0.511773 / 0.901330 | 10.544672 / 10.576702 | 0.014440 | 10.747027 / 11.290764 |
| 64 | 0.255887 / 0.450665 | 10.401210 / 10.413306 | 0.014440 | 10.506064 / 10.731338 |

## Area / Timing

이번에는 RTL/bitstream을 변경하지 않았으므로 재합성하지 않았다. 아래는 programming한 동일 hash의 기존 post-route 결과이며, 측정 전후 delta는 0이다. [Utilization](../build/experiments/fpga-p2-20260909-131644/P2-reset-safe/board/route-utilization.rpt), [timing](../build/experiments/fpga-p2-20260909-131644/P2-reset-safe/board/route-timing-summary.rpt), [DRC](../build/experiments/fpga-p2-20260909-131644/P2-reset-safe/board/route-drc.rpt)를 보존했다.

| Resource | P1 | P2 | P1→P2 delta | P2 사용률 | P2 남은 자원 |
| --- | --- | --- | --- | --- | --- |
| LUT | 42,878 | 43,362 | +484 | 68.39% | 20,038 |
| FF | 28,671 | 28,830 | +159 | 22.74% | 97,970 |
| RAMB36 | 27 | 41 | +14 | shared BRAM pool | 아래 tile equivalent |
| RAMB18 | 1 | 2 | +1 | shared BRAM pool | 아래 tile equivalent |
| BRAM36 equivalent tiles | 27.5 | 42 | +14.5 | 31.11% | 93 |
| DSP | 27 | 27 | 0 | 11.25% | 213 |
| Occupied slices | 14,005 | 14,808 | +803 | 93.43% | 1,042 |

Nominal 25 MHz: P2 WNS **+6.985 ns**, TNS **0**, WHS **+0.029 ns**, THS **0**, WPWS **+3.000 ns**. 기존 DSP pipelining DRC warning 65개는 유지됐고 새 BRAM reset 경고는 없었다. UART/LED 비동기 외부 pin delay 제약의 기존 한계도 동일하다. Programming/측정 중 예상 밖 오류는 없었다.

## Decision

**Keep — P2 보드 correctness 및 command batching 효과 확인. Mode B > 1 목표는 미달성.**

1. B1→B64 resident total/job은 26.474732→10.506064 ms, bulk는 39.363609→10.731338 ms로 줄었다. 고정 command/round-trip 비용 상각은 실제로 발생했다.
2. B64 result receive는 resident 10.401210 ms/job, bulk 10.413306 ms/job이다. Payload당 nominal UART 하한 `1024 × 10 / 1000000 = 10.24 ms/job`에 근접했다. **세 해석 중 경우 2가 현재 주요 병목을 설명한다.**
3. 모든 측정 B에서 speedup < 1이다. 같은 1,024-byte/job 결과량과 nominal 1 Mbaud를 유지하면 payload 하한만으로도 speedup 상한은 **0.149254×**다. 따라서 이 조건에서는 batch size만 늘려 Verilator 1.528364 ms/job을 넘는 crossover가 생기지 않는다. 측정 범위 밖 실험 결과를 주장하는 것은 아니며, 이는 해당 조건의 전송량 하한에 따른 판단이다.
4. **Completion-only 경로는 이 bitstream에 없다.** `B`는 항상 전체 결과를 반환한다. 읽기를 생략해도 전체 UART 전송은 계속된다. 완료 전에 보낸 다음 command는 무시되므로 독립 completion-only 실험이 되지 않는다. 새 기능은 추가하지 않았다.
5. B64 core compute fraction은 resident **0.137444%**, bulk **0.134559%**다. 100 MHz timing이나 더 큰 physical DIM을 지금 우선할 근거가 없다. 실제 model Mode C와 P3 local residency 성능은 미측정이다.
6. 후속 검토 대상은 결과 traffic 감소/local residency 또는 transport 변경이다. Slice occupancy 93.43%이므로 P3 자원 여유를 LUT/BRAM 잔량만으로 보장하지 않는다. 이번 승인은 여기서 종료하며 추가 RTL·driver 설정·bitstream 변경은 수행하지 않았다.

## 재현 및 artifact

실제 보드 측정은 다음 argv로 한 번 실행했다. **기록용 명령이며 이번 승인에 따른 재실행은 하지 않는다.**

```sh
python3 build/experiments/fpga-p2-20260909-131644/hardware-measurement-20260909-135006/host_batch.py \
  --port /dev/serial/by-id/usb-Digilent_Digilent_USB_Device_210319BE7725-if01-port0 \
  --out build/experiments/fpga-p2-20260909-131644/hardware-measurement-20260909-135006/sweep \
  --samples 5 --timeout 10 \
  --bitstream-sha256 a22b27e8944553d61a387e3e975caa41cdcf6d979eeefbd96ac7ce350ea6ed15
```

보드 접근 없이 기존 raw data를 다시 검산하는 명령:

```sh
python3 build/experiments/fpga-p2-20260909-131644/hardware-measurement-20260909-135006/export/analyze.py
```

이 스크립트는 warm-up 수, job/output 수, cycles, bytes, round-trip 및 기존 summary 통계를 assert하고 export 파일만 재생성한다. 실측은 다시 하지 않는다.

- [Raw 84 samples](../build/experiments/fpga-p2-20260909-131644/hardware-measurement-20260909-135006/sweep/samples.jsonl), [원본 summary](../build/experiments/fpga-p2-20260909-131644/hardware-measurement-20260909-135006/sweep/summary.json), [input/argv provenance](../build/experiments/fpga-p2-20260909-131644/hardware-measurement-20260909-135006/sweep/provenance.json).
- [Sample CSV](../build/experiments/fpga-p2-20260909-131644/hardware-measurement-20260909-135006/export/per-sample.csv), [14개 mode/batch 전체 median/min/max CSV](../build/experiments/fpga-p2-20260909-131644/hardware-measurement-20260909-135006/export/per-batch.csv), [phase 통계 CSV](../build/experiments/fpga-p2-20260909-131644/hardware-measurement-20260909-135006/export/phase-statistics.csv).
- [독립 분석 JSON: fit/residual/R² 및 관측 한계](../build/experiments/fpga-p2-20260909-131644/hardware-measurement-20260909-135006/export/analysis.json), [stdlib 분석 스크립트](../build/experiments/fpga-p2-20260909-131644/hardware-measurement-20260909-135006/export/analyze.py).
- [측정 orchestration](../build/experiments/fpga-p2-20260909-131644/hardware-measurement-20260909-135006/measure_once.py), [최종 검증](../build/experiments/fpga-p2-20260909-131644/hardware-measurement-20260909-135006/final-verification.json).

Input payload hash는 source의 payload 생성 함수와 함께 보존했다. Host에 전달한 bitstream hash만으로 live configuration을 증명한다고 주장하지 않으며, 실제 programming log와 configuration status 검증을 함께 근거로 사용한다.
