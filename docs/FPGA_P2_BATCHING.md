# P1 board 자원 gate와 P2 control batching

측정일: 2026-09-09. 이 문서는 기존 `FPGA_SCALABILITY_RESEARCH.md`와 검증 artifact를 변경하지 않는 후속 실험 기록이다. 새 실험 root는 [`build/experiments/fpga-p2-20260909-131644`](../build/experiments/fpga-p2-20260909-131644)이다.

## PHASE 0 — P1 milestone

### Baseline

- Branch: `exp/pe-local-partial-int20`.
- HEAD와 fetch 후 `origin/fpga/arty-a7-100t`: `dc4a1a621f63834d64df42ae8c24152747d97971`.
- Dirty 상태: 기존 tracked 17 files, 245 insertions / 72 deletions. 기존 문서 2개는 untracked 상태다. HEAD만으로 P1 source를 표현할 수 없다.
- [현재 working source](../build/experiments/fpga-p2-20260909-131644/milestone/source), [tracked patch](../build/experiments/fpga-p2-20260909-131644/milestone/tracked.patch), [Git 상태](../build/experiments/fpga-p2-20260909-131644/milestone/git-state.txt)를 보존했다.
- 실제 P1 source는 INT20 snapshot에 **A1 MAC + B2/B1b control의 R3 변경**, fixed-shape ResidentP0, UART shell이 추가된 상태다. 현재 working tree와 동일하지 않다. [BSV source](../build/experiments/fpga-p2-20260909-131644/milestone/p1-bsv), [실제 Vivado 입력](../build/experiments/fpga-p2-20260909-131644/milestone/board-inputs), [계보](../build/experiments/fpga-p2-20260909-131644/milestone/evidence/P0-R3/lineage.json)를 별도로 보존했다.
- Vivado 2025.2, SW Build 6299465. Target: `xc7a100tcsg324-1`.
- 승인된 P1 bitstream SHA256: `560d5496a6b8de9f0eb5a694583085164d3c47e9bf90157fc0822ac3b5198b3c`.
- P1 route DCP SHA256: `7039c06348ad524cc45967f08f38cf80b940939b96d1a05d481a4bc1c595b524`.
- Nominal clock: 25 MHz. 100 MHz 입력과 MMCM 설정에 근거하며 외부 계측값이 아니다.
- Post-route setup WNS +6.722 ns, TNS 0; hold +0.017 ns, pulse +3.000 ns.
- 기존 실제 board 측정: A8/W8, physical D16, M=N=16, K=32, 19 jobs, **4,864/4,864 outputs PASS**, FPGA와 RTL 모두 361 cycles/job.
- 기존 programming은 FPGA SRAM configuration 한 번이다. Flash write는 없었다. [Programming audit](../build/experiments/fpga-p2-20260909-131644/milestone/evidence/P1/programming-review.json)와 원본 programming log 사본을 보존했다.

보존 파일 전체의 [SHA256 manifest](../build/experiments/fpga-p2-20260909-131644/milestone/SHA256.json)를 기록하고 bitstream/DCP가 승인된 값과 일치함을 확인했다. 기존 source, 문서, 측정 artifact를 덮어쓰지 않았다.

## PHASE 1 — 실제 board-level resource

### Hypothesis

일반 D16 core OOC 수치가 fixed-shape P1 board의 integration margin을 과소평가할 수 있다.

### Isolated change

설계 변경 없이 승인된 bitstream의 route DCP를 Vivado에서 다시 열었다. [Tcl](../build/experiments/fpga-p2-20260909-131644/phase1/inspect.tcl), [command](../build/experiments/fpga-p2-20260909-131644/phase1/command.json), [새 utilization](../build/experiments/fpga-p2-20260909-131644/phase1/utilization.rpt), [hierarchy](../build/experiments/fpga-p2-20260909-131644/phase1/hierarchy.rpt)를 별도 디렉터리에 생성했다.

### Correctness / Cycle

DCP read-only 분석은 exit 0이다. 기존 board correctness와 361 cycles를 보존하며 새로운 board 실행으로 계산하지 않는다.

### Area

| Resource | P1 post-route 사용 | Device 용량 | 남은 자원 | 사용률 |
|---|---:|---:|---:|---:|
| Slice LUT | 42,878 | 63,400 | **20,522** | 67.63% |
| FF | 28,671 | 126,800 | **98,129** | 22.61% |
| BRAM36 equivalent tiles | 27.5 | 135 | **107.5** | 20.37% |
| DSP48E1 | 27 | 240 | **213** | 11.25% |
| Occupied slices | 14,005 | 15,850 | 1,845 | 88.36% |

실제 primitive 구성은 **RAMB36E1 27개 + RAMB18E1 1개**다. 남은 107.5 tiles는 215 RAMB18 equivalent다. RAMB36와 RAMB18은 같은 물리 tile을 공유하므로 각각의 available-minus-used를 더하면 안 된다. BRAM36 전용 신규 배치에 사용 가능한 완전히 빈 tile 수는 별도 placement 조건도 고려해야 한다.

| 보존된 hierarchy | LUT | FF | RAMB36 | RAMB18 | DSP |
|---|---:|---:|---:|---:|---:|
| Board top | 42,878 | 28,671 | 27 | 1 | 27 |
| ResidentP0 전체 (`shell/core`) | 42,561 | 27,737 | 27 | 1 | 27 |
| UART (`shell/uart`) | 268 | 50 | 0 | 0 | 0 |
| Shell 자체 control/serializer | 50 | 880 | 0 | 0 | 0 |
| Board 자체 glue | 1 | 4 | 0 | 0 | 0 |
| Engine activation FIFO, core 내부 | 1,611 | 258 | 0 | 0 | 0 |
| Engine result FIFO, core 내부 | 1,030 | 994 | 0 | 0 | 0 |

하위 항목은 상위 항목에 포함된다. Cross-hierarchy LUT combining 때문에 하위 LUT 합도 상위 합과 다를 수 있다. BSC에서 core control, debug/counter, PE가 많이 평탄화됐다. 이들을 이름만으로 분리한 수치를 정확한 물리 면적으로 제시하지 않는다. `weights` BRAM hierarchy의 LUT 4,387도 순수 weight storage 비용이라고 해석할 수 없다. 전체 primitive 이름·LOC·BEL은 [CSV](../build/experiments/fpga-p2-20260909-131644/phase1/primitive-hierarchy.csv)에 보존했다.

### Timing

기존 P1의 실제 25 MHz post-route timing을 보존했다. LUT 여유 32.37%와 달리 slice 점유율은 88.36%다. 따라서 자원 수만으로 P2 routing 성공을 보장하지 않는다.

### Board/system

기존 Verilator median 1.528364 ms, resident compute 361/25 MHz = 0.014440 ms, resident+result 26.162115 ms, bulk total 40.783830 ms를 유지한다. 실제 모델 Mode C는 미측정이다.

### Decision

**Keep: 현재 P1은 추가 area campaign 없이 최소 P2를 구현할 자원 gate를 통과한다.** LUT 여유 10%를 남기는 추가 LUT 예산은 14,182개다. 이는 placement 보증이나 P3의 확정 예산이 아니다. P2 전체 top의 post-route 결과로 다시 판단한다. Weight-bank와 engine FIFO 변경은 지금 수행하지 않는다.

## PHASE 2/3 — MAC 실험 재사용과 재검증

### Baseline

BSC 2026.01 build `9bd39e6f`, Vivado 2025.2. Gemmini reference commit은 `8c3f9923a44a2fe2c7930587be297d6d4f8c09ca`다. 보존된 BSC Prelude와 Gemmini PE/Arithmetic 원문을 직접 읽었다. [Reference/결과 hash](../build/experiments/fpga-p2-20260909-131644/phase1/reused-evidence-sha256.json)를 남겼다.

### Hypothesis

BSC `signedMul`은 abs/unsigned multiply/sign 복원으로 표현된다. Gemmini SInt MAC은 `m1 * m2 + self`이며 MacUnit 경계로 MAC 중복을 방지한다. 이 source 차이만으로 FPGA 효율을 단정하지 않고 기존 독립 합성을 대조했다.

### Isolated change

이미 A0/A1/A2/A3 harness와 multiplier → MAC → PE → 2×2 array → full D16 실험이 존재한다. 동일 실험을 새로 설계하지 않고 [harness 사본](../build/experiments/fpga-p2-20260909-131644/mac-check)을 보존했다. 이번 새 실행은 해당 generated RTL의 A8 exhaustive correctness 검사다. 이전 합성값과 이번 재실행을 구분한다.

### Correctness

새 [Icarus 명령](../build/experiments/fpga-p2-20260909-131644/mac-check/check-command.json)과 [결과](../build/experiments/fpga-p2-20260909-131644/mac-check/fresh-numerical-a8.log): **65,536 operand pairs × 8 INT20 partial 경계 = 524,288 MAC cases**, random 10,000 cycles, A0/A1/A2/A3 exact PASS. `(-128,-128)`, `(-128,127)`, `(127,127)`을 포함한다.

R3 core의 기존 **154 tests PASS, 0 failed/ignored**를 [로그 hash와 합계](../build/experiments/fpga-p2-20260909-131644/phase1/core-regression-evidence.json)로 재확인했다. 이 154개는 이번 새 실행이라고 표현하지 않는다. P2에서 core generated RTL/primitive는 바꾸지 않았다.

### Cycle

Microbench의 동일 enable/valid/reset, latency 2, driver 534,310 cycles를 재확인했다. 기존 A0/A1 full D16의 24 numerical/cycle records는 동일했다. P1의 361 cycles는 별도 R3 control을 포함하므로 A0/A1 일반 core의 cycle과 혼합하지 않는다.

### Area

아래는 보존된 독립 실험의 실제 Vivado 결과다. Micro는 10 ns post-opt, full core도 동일 10 ns timing-aware post-opt 비교다.

| Resource | A0 A8 multiplier | A1 | Delta |
|---|---:|---:|---:|
| LUT | 121 | 63 | -58 |
| FF | 34 | 34 | 0 |
| CARRY4 | 14 | 14 | 0 |
| BRAM | 0 | 0 | 0 |
| DSP | 0 | 0 | 0 |

| Resource | A0 A8 MAC | A1 | Delta |
|---|---:|---:|---:|
| LUT | 108 | 81 | -27 |
| FF | 58 | 58 | 0 |
| CARRY4 | 15 | 17 | +2 |
| BRAM | 0 | 0 | 0 |
| DSP | 0 | 0 | 0 |

A2/A3는 A1과 같은 micro 자원/timing 결과였다. PE는 153 LUT/DSP0에서 60 LUT/DSP1, 2×2 array는 564 LUT/DSP0에서 193 LUT/DSP4였다. 이 DSP 선택은 full core로 그대로 확장되지 않았다.

| Resource | Full D16 A0 | Full D16 A1 | Delta |
|---|---:|---:|---:|
| LUT | 59,953 | 54,748 | **-5,205 (-8.68%)** |
| FF | 34,370 | 34,805 | +435 |
| RAMB36 | 16 | 16 | 0 |
| RAMB18 | 0 | 0 | 0 |
| DSP | 128 | 120 | -8 |

### Timing

Micro multiplier WNS +1.005 → +4.570 ns, MAC +1.620 → +2.958 ns다. Full D16 WNS -10.717 → -10.609 ns로 control 병목은 유지됐다. 모두 unplaced post-opt 값이며 post-route Fmax가 아니다.

### Decision

**Keep A1:** A8에서는 full D16 자원 개선 근거가 있다. P1에 이미 포함돼 있으므로 다시 적용할 절감량이 아니다. A2/A3 추가 module은 채택하지 않는다. A4에서는 area가 악화된 기존 결과도 유지한다. SignedMul이 PE 비용 전체를 설명한다는 가설은 기각한다. 현재 margin이 충분하므로 weight/FIFO 단독 변경은 보류한다.

## PHASE 7 — P2 최소 control batching 후보

### Baseline

고정된 P1의 실제 Vivado 입력에서 시작했다. 변경은 [UART shell](../build/experiments/fpga-p2-20260909-131644/P2-reset-safe/inputs/board-source/resident_uart.sv) 하나다. [Diff](../build/experiments/fpga-p2-20260909-131644/P2-reset-safe/shell.patch), [전체 입력 hash](../build/experiments/fpga-p2-20260909-131644/P2-reset-safe/inputs-sha256.json)를 보존했다.

### Hypothesis

한 descriptor로 여러 job을 실행하고 모든 결과를 저장한 뒤 한 응답으로 반환하면 per-job host round-trip을 줄일 수 있다.

### Isolated change

- 기존 `L` bulk load와 `S` 단일 실행을 유지한다.
- `B` + count byte(`1..64`)가 하나의 run-length descriptor다. FPGA가 같은 resident A/B/scale job을 count번 실행한다.
- 64 KiB BRAM 후보 배열에 job 순서대로 모든 INT32 결과를 저장한다. 각 job의 결과를 복사한 뒤 core를 acknowledge하고 다음 job을 실행한다.
- 응답은 `<2sBBIQQ>` 24-byte header와 `B × 1,024` bytes 결과다. Header에는 `P2`, version 2, status, completed count, core-cycle sum, batch execution cycles가 있다.
- Batch execution counter에는 local result copy/ack/relaunch가 포함된다. UART result serialization은 포함되지 않는다.
- Count 0/65..255는 오류로 반환한다. 미완성 descriptor는 실행하지 않는다. Batch 중 새 command는 기존 busy 정책에 따라 무시한다.

이 후보는 **동일 입력을 반복하는 control-batching 측정용**이다. 서로 다른 A/B를 갖는 descriptor FIFO나 input-slot 선택은 아직 구현하지 않았다. 이를 일반 heterogeneous job queue로 표현하지 않는다. Core datapath, dual weight banks, engine FIFO, scheduler, scaling, architectural INT32는 유지한다.

### Correctness

[실제 UART + R3-P0 Verilator 검사](../build/experiments/fpga-p2-20260909-131644/P2-reset-safe-reproduction/check/batch_check.cpp), [로그](../build/experiments/fpga-p2-20260909-131644/P2-reset-safe-reproduction/check/numerical.log), [합계](../build/experiments/fpga-p2-20260909-131644/P2-reset-safe-reproduction/check/summary.json): **135 completed jobs, 34,560/34,560 INT32 outputs PASS**.

검사 범위는 batch 1/2/4/8/16/32/64, 기존 resident/bulk/reuse, 부분 upload 미공개, descriptor 수신 중 pause, invalid count, busy command 무시, descriptor/실행 중 reset 복구, INT20 경계와 fragment별 shift다. Python golden은 저장된 batch 결과 33,536개를 별도로 decode했다. [Host 검사](../build/experiments/fpga-p2-20260909-131644/P2-reset-safe/test_host_batch.py)는 손상 packet 거부, partial I/O, 선형 fit도 확인했다. 실제 FPGA 실행 결과는 아니다.

재현 runner의 첫 host 검사는 sandbox의 local socketpair `EPERM`으로 실패했다. 해당 log를 보존하고, 승인된 sandbox 밖 실행에서 같은 검사가 PASS한 [최종 log](../build/experiments/fpga-p2-20260909-131644/P2-reset-safe-reproduction/check/host-escalated.log)를 별도로 기록했다. 물리 장치 접근은 없었다.

### Cycle

| Metric | P1 | P2 | Delta |
|---|---:|---:|---:|
| Core cycles/job | 361 | 361 | 0 |
| 기존 S UART shell cycles | 260,627 | 260,627 | 0 |
| Batch execution cycles/job | 해당 없음 | 412 | local copy/ack/relaunch 포함 |

Batch 64는 core sum 23,104 cycles, batch execution 26,368 cycles다. Nominal 25 MHz에서 각각 0.924160 ms와 1.054720 ms다. 이 값은 RTL cycle 기반 예측이며 board wall-clock 실측이 아니다.

### Area / Timing

첫 후보는 route를 통과했지만 새 result BRAM enable에 `REQP-1839/1840` 비동기 reset 경고가 생겨 programming 대상에서 제외했다. 독립된 `P2-reset-safe` 후보는 BRAM enable의 직접 `reset_n` gating만 제거한다. Synchronous shell state가 reset 시 publication을 무효화하며, 다음 batch는 반환할 모든 row를 다시 쓴다. Reset 도중 저장 내용은 유효 결과로 취급하지 않는다. 수정본을 새 디렉터리에서 다시 빌드하고 검사했다.

합성과 place/route는 기존 P1과 같은 part, clock, hierarchy, 제약을 사용한다. [실행 명령](../build/experiments/fpga-p2-20260909-131644/P2-reset-safe/board-command.json)을 보존했다. DSP mapping 강제나 새로운 timing exception은 추가하지 않았다.

최종 `P2-reset-safe`의 실제 [post-route utilization](../build/experiments/fpga-p2-20260909-131644/P2-reset-safe/board/route-utilization.rpt)과 [검토 JSON](../build/experiments/fpga-p2-20260909-131644/P2-reset-safe/board-review.json)은 다음과 같다.

| Resource | P1 before | P2 after | Delta | P2 남은 자원 |
|---|---:|---:|---:|---:|
| LUT | 42,878 | 43,362 | **+484** | **20,038 (31.61%)** |
| FF | 28,671 | 28,830 | +159 | 97,970 |
| RAMB36 | 27 | 41 | +14 | 아래 shared pool 참조 |
| RAMB18 | 1 | 2 | +1 | 아래 shared pool 참조 |
| BRAM36 equivalent tiles | 27.5 | 42 | **+14.5** | **93 (68.89%)** |
| DSP | 27 | 27 | 0 | 213 |
| Occupied slices | 14,005 | 14,808 | +803 | 1,042 (6.57%) |

추가 result memory는 64 KiB다. P2 LUT 사용률은 68.39%, FF 22.74%, BRAM 31.11%, DSP 11.25%다. LUT 여유는 충분하지만 slice 점유율은 **93.43%**다. 이번 P2 자체의 routing 성공은 확인했지만 이를 P3 placement 여유 보증으로 일반화하지 않는다.

| Timing | P1 | P2 |
|---|---:|---:|
| Nominal clock | 25 MHz | 25 MHz |
| Setup WNS | +6.722 ns | **+6.985 ns** |
| TNS | 0 | 0 |
| Hold WHS | +0.017 ns | **+0.029 ns** |
| THS | 0 | 0 |
| Pulse WPWS | +3.000 ns | +3.000 ns |

[Routed timing](../build/experiments/fpga-p2-20260909-131644/P2-reset-safe/board/route-timing-summary.rpt), [route status](../build/experiments/fpga-p2-20260909-131644/P2-reset-safe/board/route-status.rpt), [DRC](../build/experiments/fpga-p2-20260909-131644/P2-reset-safe/board/route-drc.rpt)를 보존했다. Build exit 0이며 새 BRAM reset 경고는 0이다. 기존과 같은 DSP pipelining warning 65개는 남아 있다. UART/LED 비동기 외부 pin의 input/output delay 미설정은 기존과 동일하다. 내부 timing closure를 외부 pin의 synchronous timing 또는 최대 Fmax로 표현하지 않는다.

최종 후보 bitstream SHA256은 **`a22b27e8944553d61a387e3e975caa41cdcf6d979eeefbd96ac7ce350ea6ed15`**다. Route DCP SHA256은 `d01f885e534b568ad372e1db923b2920d944a410ae36c0cf3fa56516a042640c`다. [Bitstream](../build/experiments/fpga-p2-20260909-131644/P2-reset-safe/board/resident.bit)은 생성만 했고 보드에 올리지 않았다. 해당 hash와 target/cable을 확인하는 [SRAM programming script](../build/experiments/fpga-p2-20260909-131644/P2-reset-safe/program_after_approval.tcl)를 준비했으며 아직 실행하지 않았다.

### Board/system

[측정 도구](../build/experiments/fpga-p2-20260909-131644/P2-reset-safe/host_batch.py)는 resident/bulk 각각 batch 1..64에 warm-up 1회와 5회 sample을 기록한다. Input+publication, descriptor write API, first-byte/header 도착, result 수신, total wall-clock, FPGA counters, 실제 read/write/select call 수, protocol round-trip 수를 저장한다.

`T(B)=intercept+slope*B`를 median에 fit한다. 이 실험만으로 input bandwidth, output bandwidth, 고정 지연을 모두 독립 추정할 수 없다. Descriptor write 시간은 OS queueing 시간이며 순수 wire 시간으로 표현하지 않는다. Port와 실제 programming에 사용한 hash는 명시적으로 전달해야 한다.

### Decision

**Keep as a candidate; needs board measurement:** P2 RTL correctness와 전체 board post-route gate는 통과했다. 별도 사용자 승인 후 실제 보드 batch sweep이 필요하다. 기존 P1 보드 configuration은 이 작업에서 변경하지 않았다.

## Result return과 후속 판단

기존 P1의 결과 반환은 word-by-word register RPC가 아니다. Host는 `S` 한 byte를 쓰고 1,036-byte packet을 `read_exact`로 받는다. FPGA 내부에서는 16개의 row read를 수행하지만 host와 row별 왕복하지 않는다. OS read call 수는 USB/TTY chunking에 따라 달라진다. 이미 bulk 반환이 있으므로 동일 기능을 새로 추가할 필요는 없다.

기존 실제 측정의 FTDI `latency_timer` 기록은 16 ms다. 이는 추가 host delivery 지연의 후보이며, 26 ms 전체를 단독으로 설명하는 인과 실험은 아니다. 이번 작업에서 driver 설정은 바꾸지 않았다.

1 Mbaud, 8N1의 순수 결과 payload wire floor는 **1,024 × 10 / 1,000,000 = 10.24 ms/job**다. P2도 모든 job 결과를 반환하므로 이 per-job 비용이 남는다. 기존 단일-job Verilator median 1.528364 ms를 선형 기준으로 놓으면, payload wire floor만으로 speedup 상한은 약 **0.1493×**다. 이는 actual batch Verilator 실측이나 measured crossover가 아니라 분석적 상한이다. 결과를 모두 반환하는 현재 UART 경로에는 batching만으로 Mode B > 1이 되는 crossover를 기대할 근거가 없다.

따라서 실제 sweep으로 fixed overhead가 얼마나 상각되는지 확인한 뒤, independent transport microbenchmark와 결과 traffic 감소/P3 local residency를 판단한다. 더 빠른 transport도 별도 후보다. P3의 chaining semantics와 Mode C workload는 아직 구현·측정하지 않았다. Physical D32/D64 확장은 이 작업 범위에 포함하지 않으며 logical tiling의 scale boundary도 변경하지 않았다.

현재 UART 결과량을 유지하면 core compute fraction의 분석적 상한도 약 0.141%다. 이는 P2 보드 실측값이 아니다. 따라서 100 MHz timing 작업을 다시 우선할 근거는 아직 없다.

## 재현

아래 명령은 새 output directory만 사용하며 hardware를 programming하지 않는다.

```sh
python3 build/experiments/fpga-p2-20260909-131644/P2-reset-safe/run_check.py /tmp/im2p-p2-check-fresh

/tools/Xilinx/2025.2/Vivado/bin/vivado -mode batch -nojournal \
  -log /tmp/im2p-p2-board-fresh.log \
  -source build/experiments/fpga-p2-20260909-131644/P2-reset-safe/inputs/board_build.tcl \
  -tclargs build/experiments/fpga-p2-20260909-131644/P2-reset-safe/inputs /tmp/im2p-p2-board-fresh
```

자동 commit/push/merge/rebase/PR 생성은 수행하지 않는다. 실제 programming과 보드 측정 결과는 별도 승인 및 실행 이후에만 추가한다.
