# P3A local retention / P3B eligibility — 2026-09-09

P3A candidate-04는 기존 P2의 64 KiB 결과 메모리로 completion-only와 별도 전체 readback을 구현했다. Production RTL 수치·protocol·board-top simulation 및 25 MHz 배치배선은 **PASS**다. 별도 BSV assertion-enabled core runtime은 **FAIL**이며 해결되지 않았다. 모든 검증 gate가 통과했다는 뜻은 아니다. P3A 보드 측정은 **NOT RUN**, 새 programming은 **별도 승인 필요**다. 실제 연결 가능한 P3B DAG는 현재 구현 연산으로 확보하지 못해 **Blocked**다.

이 문서의 experiment-relative 경로는 다음 디렉터리 기준이다.

```text
build/experiments/fpga-p3a-20260909-142748/
```

[실험 index 및 최종 보존 검증](../build/experiments/fpga-p3a-20260909-142748/README.md), [승인 검토용 계획](../build/experiments/fpga-p3a-20260909-142748/approval/plan.json). 기존 P1/P2 artifact를 덮어쓰지 않았고 FPGA/JTAG/UART에 접근하지 않았다. Flash write, commit/push/merge/rebase/PR, 사용자 변경 reset/stash/삭제 없음.

## 1. 실제 P2 baseline

| 항목 | 식별 |
|---|---|
| Branch / HEAD | `exp/pe-local-partial-int20` / `dc4a1a621f63834d64df42ae8c24152747d97971` |
| Dirty source | 기존 tracked 17파일 수정, 245 insertions / 72 deletions; 기존 untracked 문서 4개 |
| 실제 P2 | `build/experiments/fpga-p2-20260909-131644/P2-reset-safe` |
| P2 bit SHA256 | `a22b27e8944553d61a387e3e975caa41cdcf6d979eeefbd96ac7ce350ea6ed15` |
| P1 bit SHA256 | `560d5496a6b8de9f0eb5a694583085164d3c47e9bf90157fc0822ac3b5198b3c` |
| P2/P3A production core RTL SHA256 | `c11b8dce1cb33a6aa850013cf059b52facb3398d2c13ae8e13219d42290a321a` |
| Target | Arty A7-100T; implementation `xc7a100tcsg324-1`; 이전 JTAG `xc7a100t_0` |
| Toolchain | Vivado 2025.2; BSC 2026.01 build 9bd39e6f; Verilator 5.051 devel rev v5.050-122-g56accae45 (mod) |
| Clock | 100 MHz board input → MMCM → nominal 25 MHz core, UART 1 Mbaud 8N1 |

HEAD만으로 source를 표현하지 않는다. `baseline/tracked.patch`, `baseline/git-state.json`, `baseline/working-source`, 실제 P2 source/generated RTL/configuration/commands와 route/bit/log를 복사한 `baseline/p2`, 원본 측정 복사본 `baseline/p2-measurement`를 함께 보존했다. 파일별 원본 hash는 `baseline/original-sha256.json`, 복사본 hash는 `baseline/SHA256.json`이다. BSV regression에 사용한 34개 core source도 P2의 실제 source와 일치함을 확인했다. Fresh Cargo target, generated RTL, simulator binary, Vivado run을 분리했다.

사용자 제공 P2 값은 원본 JSONL/CSV와 programming verification log로 재확인했다. **새 보드 측정이 아니다.** Warm-up 제외 70 samples = **1,270 jobs / 325,120 outputs PASS**. Core 361 cycles/job, batch 412 cycles/job. 이전 programming은 configuration SRAM 1회이고 DONE/INIT/CRC 등 verification PASS, flash write 없음. [재검증 결과](../build/experiments/fpga-p3a-20260909-142748/evidence/p2-revalidation.json).

## 2. UART 병목과 서로 다른 목적

| B | P2 resident median ms/job | P2 bulk median ms/job |
|---:|---:|---:|
| 1 | 26.474732 | 39.363609 |
| 2 | 18.166043 | 24.666007 |
| 4 | 14.243952 | 17.317599 |
| 8 | 12.273596 | 13.797050 |
| 16 | 11.404008 | 12.098541 |
| 32 | 10.747027 | 11.290764 |
| 64 | 10.506064 | 10.731338 |

P2 B64 result reception은 약 **10.401 ms/job**, **98.45 kB/s**다. 8N1은 payload 1byte에 10bits이므로 1 Mbaud의 nominal maximum은 100,000 bytes/s, 1,024 bytes/job payload만 **10.24 ms/job**이다. P2 batch-total median을 B에 선형 fit한 fixed cost는 resident 약 16.38 ms/batch, bulk 약 28.84 ms/batch다. 이는 이번 protocol의 stage 실측값이 아니다.

P2는 고정 비용을 상각했지만 full-output은 UART payload 한계에서 정체했다. Historical Verilator median 1.528364 ms/job 대비 B64 resident 약 0.1455x, bulk 약 0.1424x이며 crossover가 없다. 같은 역사적 기준의 payload-only speedup 상한은 약 0.1493x다.

- **P3A:** 같은 B개 계산을 모두 수행하고 모든 결과를 local BRAM에 보존한다. 작은 completion까지와 나중의 전체 readback을 분리하는 diagnostic이다.
- **P3B:** 실제 consumer가 사용할 tensor를 local로 전달해야 한다. 독립 job 결과를 버리는 것은 해당하지 않는다.
- **T:** host가 모든 결과를 필요로 하는 경로의 bandwidth/transaction 개선이다. P3A diagnostic 결과로 T의 full-output 가속을 주장하지 않는다.

## 3. 파일별 변경과 유지한 계약

Hardware source는 고정된 `candidate-04`다. Source manifest SHA256:

```text
6bac05db62746862b703d264667639be4cb800c3d241ba09662a4e70c1b6698a
```

| 파일 / artifact | 변경 |
|---|---|
| `candidate-04/inputs/board-source/resident_uart.sv` | 유일한 hardware 변경. P3A frame/parser, reply mode, retained ownership/id/generation, READ/RELEASE, 오류 처리 |
| `candidate-04/host_p3a.py` | 기존 P2 transfer/golden helper 재사용. 엄격한 응답 검증, 별도 timing 경계, full exact comparison, JSONL/summary/fit |
| `candidate-04/test_host_p3a.py` | PTY 기반 partial I/O·mode/B·오류·timeout 검증 |
| `candidate-04/check/p3a_check.cpp` | 실제 core/UART, launch/주소/write/ACK scoreboard, reset/busy/error tests |
| `candidate-04/check/batch_check.cpp` | 기존 main을 호출하는 함수로 사용할 때 필요한 명시적 `return 0`; 검증식 변경 없음 |
| `candidate-04/PROTOCOL.md` | Wire format, 완료/보존/오류/ownership/measurement 계약 |
| `flow/board_build.tcl`, `flow/report_detail.tcl` | 기존 P2 flow 재사용, 추가 physical 보고서. 새 timing exception 없음 |
| `validation-source-05/run_check.py` | 검증 출력에서 P2/P3A capture 디렉터리 분리. Hardware candidate-04와 나머지 파일 동일 |

[Protocol 전체](../build/experiments/fpga-p3a-20260909-142748/candidate-04/PROTOCOL.md), [P2 대비 diff](../build/experiments/fpga-p3a-20260909-142748/evidence/diffs/review-manifest.json). 기존 core/primitive/top/XDC, physical D16, A8/W8, PE-local INT20, architectural INT32, accumulator/scale/publication/sticky failure 계약은 변경하지 않았다. 이미 포함된 A1 MAC, 100 MHz, physical D32 실험을 반복하지 않았다.

P2의 실제 result memory는 **1024 × 512 bits = 65,536 bytes**, 주소 `job*16+row`, job 0..63, row 0..15다. 한 synchronous write port와 한 synchronous read port를 사용하며 execution/readout 단계가 분리된다. 신규 tensor storage 예산은 **0 bytes / 0 BRAM**, control은 사전 예상 1,000 LUT/1,000 FF 이내였다. 실제 증가는 441 LUT/459 FF다.

기존 P2는 core launch → done → 16 row request/write → core acknowledge → 다음 launch를 UART TX/host ACK 없이 반복한다. 마지막 row write는 ACK 이전에 끝난다. 마지막 ACK에서 batch count/cycles를 확정한 뒤 결과 TX로 넘어간다. Batch cycle은 계산·writeback·ACK까지이며 UART 전송 완료 시점이 아니다. P3A는 START 수락 때 ownership을 획득하고, 최종 ACK에서만 결과를 공개(`retained_valid`)하여 completion을 준비한다. Core FIFO를 UART stall로 막지 않는다.

새 request는 20 bytes, little-endian `<4sBBHQI>`다. Magic `P3A\0`, op/mode/count/id64/generation32를 가진다. START의 mode는 FULL_RESULTS=0 또는 COMPLETION_ONLY=1, count 1..64, id nonzero, generation=0이다. READ/RELEASE는 정확한 retained id/generation을 요구한다. Reply는 48 bytes, `<4sBBBBQIHHQQIHH>`, version 3/type/status/mode/id/generation/requested/completed/core cycles/batch cycles/payload length/header length/reserved를 검사한다. 기존 L/S/B wire format은 소유된 P3A 결과가 없을 때 그대로다.

두 START mode 모두 결과를 RELEASE 전까지 보존한다. FULL은 즉시 전체 DATA도 보내며 COMPLETION은 header만 보낸다. READ는 lifetime을 끝내지 않는다. 소유 중 새 START/L/S/B는 거부하고, legacy L payload를 소비하더라도 memory에 쓰지 않는다. Generation은 configuration에서 0으로 초기화하고 START마다 증가하며 board reset에서는 유지한다. 최대값 뒤 wrap 대신 거부한다. Reset은 ownership/publication/parser/TX를 취소한다. 별도 software abort opcode는 없고 board reset이 abort다. Host는 새 64-bit id를 사용하고 configuration 간 재사용하지 않는다.

Busy 시 수신한 추가 frame 하나는 이후 BUSY로 거부한다. 첫 byte 당시 busy 상태를 보존해 뒤늦게 실행하지 않는다. 추가 outstanding frame은 sticky failure다. 불완전 P3A packet은 250,000 idle clocks, nominal 10 ms 후 BAD_FRAME이다. 마지막 ACK 또는 READ 수락 때 sticky core/framing error와 같은 edge RX error를 status 9에 반영한다. 이미 송신한 header를 소급 변경할 수는 없으며 오류는 다음 응답까지 유지된다. Host는 오류 시 retry/reset/release 없이 중단한다.

## 4. 정확성·cycle·protocol·cache 회귀

| 검사 | 결과 | 근거 / 범위 |
|---|---|---|
| `git diff --check` | PASS | 기존 사용자 diff 및 새 문서 검토 |
| `make check`, static regressions, `cargo fmt --all --check` | PASS | `evidence/light-validation/status.json` |
| `make cache-contract-test` | PASS | 실제 외부 frontend source/header 복사본; cache/PIC/semantic 계약, skip 없음 |
| `make sim-test-a8-w8-d16` | PASS | 154 tests; frozen 실제 P2 BSV; 별도 Cargo/build |
| `make bsv-test-one TOP=mkTbProfileConfig` | PASS | `-check-assert` profile/config test |
| P2 legacy numerical/UART regression | PASS | 135 jobs / 34,560 outputs, 기존 S/B 동작 |
| P3A actual-core UART scoreboard | PASS | 326 checked jobs, 115,968 exact comparisons; 모든 B/두 mode/retention/errors |
| 기존 P2 host verifier | PASS | P2 capture 9개 / 33,536 outputs, corruption/partial I/O/fit 검증 |
| P3A host unit | PASS | 7 tests; 28개 mode/input/B 조합 및 malformed 응답/timeout |
| 실제 P3A capture → host parser | PASS | 14 packets / 254 jobs / 65,024 outputs exact |
| Vendor actual board-top RTL | PASS | Vivado unisims IBUF/MMCM/BUFG; legacy B1 + P3A B1, 2 jobs / 512 outputs |
| Core / batch cycles | PASS | 같은 입력에서 361 / 412 cycles/job, delta 0 |
| BSC assertion-enabled full core runtime | **FAIL** | 첫 job 전 activation publication assertion; 0/4 jobs, 0/1,024 outputs |
| 강제로 지연시킨 result BRAM ready | NOT RUN / port 없음 | 현 BRAM은 ready 없는 always-accept write 계약. 실제 TX stall·늦은 READ는 검사 |
| Post-route SDF simulation / 실제 P3A 보드 | NOT RUN | vendor RTL simulation과 route 분석만 수행 |

P3A의 115,968은 비교 횟수다. FULL initial export와 deferred READ에서 같은 값을 다시 비교한 횟수가 포함된다. 326 jobs × 256 = 83,456 job output slots와 구분한다. P2/P3/host 검증량도 서로 중복되므로 합산해 새 독립 output 개수로 주장하지 않는다.

Simulation은 B=1/2/4/8/16/32/64, 두 mode, 전체 retained readback, 첫/마지막 word, address 0..1023, commit/launch/ACK 수, id/generation, B=0/65/255, 잘못된 mode/identity, 중복 start, busy READ, 소유 중 L/S/B, delayed READ, TX stall, 불완전 frame, reset/abort 및 stale generation, generation exhaustion, INT20 경계를 검사했다. 동일 resident 입력이라는 기능 한계 때문에 job별 서로 다른 정상 입력을 지원한다고 주장하지 않는다. 대신 모든 launch와 row write 주소·값·순서·ACK를 scoreboard로 관측했다. Late malformed UART stop bit를 writeback 중과 retained idle에 넣은 두 사례도 status 9와 전체 readback exact match로 확인했다.

원본 `sim-04/check/numerical-status.json`은 exit 0이다. 뒤의 원본 P2 host 단계는 P2 glob이 새 48-byte P3A packet까지 읽은 fixture 충돌로 실패했다. 데이터를 분리한 새 run에서 **기존 verifier 그대로 PASS**했다. 원본 실패와 capture를 보존했다. 재현용 `validation-source-05`는 runner의 capture 분리 7줄만 변경했고 syntax PASS다. 이 runner 수정 뒤 전체 numerical simulation을 불필요하게 반복하지 않았다. [분리 검증](../build/experiments/fpga-p3a-20260909-142748/evidence/host-protocol-validation/README.md).

Vendor board-top은 100 MHz input/40 ns core period, 4-edge reset release, button reset/MMCM lock loss·relock과 stale generation rejection을 검사했다. Vendor model을 대체하지 않았다. Sandbox xsim 초기화 실패 후 같은 compiled snapshot을 권한 확장 환경에서 실행하여 PASS했다. 보드 접근이 아니다. [원본 명령·범위](../build/experiments/fpga-p3a-20260909-142748/evidence/board-top-sim/candidate-04/README.md).

**BSV assertion 한계:** production P2/P3A generated RTL에는 BSC dynamic assertion site가 0개다. 기존 compile에 `-check-assert`가 없었기 때문이다. 별도 `-check-assert -keep-fires -show-schedule` 생성물에는 108 display sites가 있으나 runtime에서 `IM2PCore.bsv:2065`, `activation response publication is already pending`으로 중단됐다. `$finish(0)`를 monitor가 실패 exit 1로 잡았다. Assertion compile G0117은 36개, production은 40개이고 G0010/G0009 등도 보존했다. Assertion을 끄거나 우선순위를 바꾸지 않았다.

실패는 P3A/UART 없는 ResidentP0 단독에서 재현된다. Assertion build의 publication은 feed/return에 의해 차단되며 production schedule과 다르다. 기존 row 0 pending에 다음 정상 row 1 tag가 준비된 상태에서 실패했고, 다음 response commit 전 중단됐다. 중복 tag/응답은 관측되지 않았다. **P3A가 유발한 오류, BSC bug, 무해한 false positive, production 안전성 완전 입증 중 어느 것으로도 확정하지 않는다.** Production의 대응 internal trace와 전체 assertion 통과는 미완료다. 초기 compile README의 NOT RUN은 당시 기록이며 최종 상태는 아래 runtime **FAIL**이다. [실패](../build/experiments/fpga-p3a-20260909-142748/evidence/assertion-core/standalone-run02/README.md), [schedule 진단](../build/experiments/fpga-p3a-20260909-142748/evidence/assertion-core/turnover-diagnostic01/README.md).

## 5. 전체 board-level 자원·packing·timing

동일 Vivado 2025.2 / part / top / 25 MHz / XDC의 post-route 비교다. OOC core나 post-opt 값을 섞지 않았다.

| Resource | P2 | P3A | Delta | P3A device 사용률 / 잔여 |
|---|---:|---:|---:|---|
| Slice LUT | 43,362 | 43,803 | +441 | 69.09% / 19,597 |
| LUT as logic | 43,234 | 43,675 | +441 | 128 SRL과 별도 |
| LUT as memory | 128 | 128 | 0 | 전부 SRL; distributed RAM 0 |
| FF | 28,830 | 29,289 | +459 | 23.10% / 97,511 |
| RAMB36 | 41 | 41 | 0 | RAMB18과 같은 physical tile 자원 공유 |
| RAMB18 | 2 | 2 | 0 | 위 RAMB36 잔여와 독립 합산하지 않음 |
| BRAM36-equivalent tiles | 42 | 42 | 0 | 31.11% / 93 |
| DSP | 27 | 27 | 0 | 11.25% / 213 |
| Occupied slices | 14,808 | 15,137 | +329 | 15,850 중 95.50%; 빈 slice 713 |
| Unique control sets | 488 | 512 | +24 | LUT 잔여량과 packing 여유가 다름 |
| Setup WNS / TNS ns | +6.985 / 0 | +6.142 / 0 | −0.843 / 0 | setup failing endpoints 0 |
| Hold WHS / THS ns | +0.029 / 0 | +0.009 / 0 | −0.020 / 0 | hold failing endpoints 0 |
| Pulse WPWS / TPWS ns | +3.000 / 0 | +3.000 / 0 | 0 / 0 | whole-board pulse failing endpoints 0 |

P3A routable nets **73,876/73,876 routed**, routing errors 0, unresolved black boxes 0. DRC는 P2와 cell 이름을 포함한 상세 내용이 동일하다: DPIP-1 19, DPOP-1 23, DPOP-2 23의 기존 DSP 경고 **65개**, critical/error 0. 새 정상 경로 false path/multicycle이나 warning suppression을 추가하지 않았다. Tool-wide 103 warnings와 DRC의 65 warnings는 집계 범위가 다르다.

No-clock/internal unconstrained paths/loops/multiple-clock 항목은 0이다. 기존 UART RX 1개 input delay와 LED4/UART TX의 output delay 5개는 지정되지 않은 외부 asynchronous I/O다. `report_cdc`의 safely timed 문구는 분석 대상에 한정되며 input delay 없는 UART RX를 포함한 완전한 외부 CDC 증명이 아니다. 기존 2-FF UART synchronizer, async reset과 동기 release 구조는 byte-identical source 검토와 vendor board-top reset/lock simulation으로 별도 확인했다. 실제 metastability MTBF/외부 clock 실측은 NOT RUN이다.

Control sets/high-fanout/path/primitive packing/congestion 보고서를 보존했다. 최악 setup은 `scaleOutstandingReg` → PE activation register set pin, 25 logic levels, data delay 33.240 ns다. 큰 fanout은 기존 reset 7,756 / enable 7,460 / active-bank 6,555 loads다. Congestion의 보고된 두 단계에서 level 3 초과 window 없음은 **zero congestion**을 뜻하지 않는다. 95.50% occupied slices와 +0.009 ns hold는 다음 기능 추가나 새 run의 통과를 보장하지 않는다. 이번 P3A physical gate는 **Keep**이고 추가 area campaign은 중단했다. [독립 route 검토](../build/experiments/fpga-p3a-20260909-142748/evidence/p3a-route-review/README.md).

## 6. Completion-only와 full-output 시간의 분리

**P3A FPGA wall-clock은 두 mode 모두 NOT RUN**이다. 아래 값은 새로 실행한 **Verilator direct simulation API** 시간이다. 각 B warm-up 1회 제외 + 5회, 측정 635 jobs / 162,560 outputs exact PASS다.

| B | Diagnostic batch median ms | min | max | Full host export batch median ms | min | max |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1.279919 | 1.272264 | 1.292390 | 1.280577 | 1.272930 | 1.293179 |
| 2 | 2.555043 | 2.525387 | 2.935463 | 2.556288 | 2.526781 | 2.938842 |
| 4 | 5.077405 | 5.063369 | 6.061643 | 5.080387 | 5.066382 | 6.064634 |
| 8 | 9.837812 | 9.698796 | 11.116566 | 9.842541 | 9.704182 | 11.123628 |
| 16 | 20.214514 | 19.448882 | 20.756112 | 20.226372 | 19.459443 | 20.767043 |
| 32 | 40.140916 | 39.301849 | 41.362658 | 40.161330 | 39.329893 | 41.383680 |
| 64 | 81.519811 | 79.721763 | 82.818812 | 81.574728 | 79.761181 | 82.871680 |

Diagnostic은 같은 실제 FSM/core/BRAM/final ACK까지 실행하고 retained_valid를 관측한다. Full export는 이어서 전체 INT32 little-endian 결과를 새 host-owned vector로 복사한다. Descriptor를 simulation API로 staging하며 `412*B+2` ticks다. UART parser/bit serialization, model 생성/preload/reference/exact check/RELEASE는 성능 endpoint 밖이다. 반복 사이 DRAIN test hook과 실제 RELEASE handler를 사용한다. 이 값은 P3B DAG나 UART simulation wall-clock이 아니다. 다른 검증/build와 병행했고 CPU governor powersave/부하 snapshot도 보존했다. 무부하 통제 benchmark라고 주장하지 않는다.

B64 full export는 median **1.274605 ms/job**다. FPGA의 같은 서비스 경계가 미측정이므로 speedup을 계산하지 않는다. 서로 다른 mode의 median 차이를 개별 stage 시간으로 쓰지 않는다. [Raw CSV/JSONL 및 실행 조건](../build/experiments/fpga-p3a-20260909-142748/evidence/matched-simulator/README-candidate-04.md).

보드용 host harness는 `T_submit`, start→첫 completion byte, completion header 수신, start→header 완료, deferred READ command/header/payload, start→모든 결과 host 확보, RELEASE를 monotonic ns로 각각 기록한다. Completion diagnostic은 deferred readback을 제외하고 full-result-available은 포함한다. Exact check와 RELEASE까지 포함한 ownership service도 별도 기록한다. 모든 INT32 결과를 lifetime 종료 전에 확인한다. API calls, bytes, protocol round trips, timeout/failure/retry, nominal cycles/25MHz 및 별도 fit이 포함된다. 제공한 bit hash 인자는 provenance이며 live configuration attestation이 아니다.

## 7. P3B 실제 DAG 및 matched 비교

`../llama.cpp-gemmini`의 실제 IM2P 경로를 읽었다. Branch `develop`, HEAD `f20f89db9c16264b32da5b0e2c10b6357f4aae80`; 기존 dirty build script/untracked model을 보존했다. 관련 외부 source 7개와 hash/상태는 `evidence/application/source` 및 `source-provenance.json`에 고정했다.

| 후보 producer → consumer | 실제 중간 처리 | 현재 판단 |
|---|---|---|
| FFN up/gate → down | FP32 복원, SiLU/multiply, 다시 activation quantization | 현 FPGA에 SiLU/동일 quantization 없음 |
| Q/K projection → attention KQ | RoPE/reshape/norm/cache, dtype/layout 처리 | 직접 INT32→INT8 연결 불가 |
| KQ → V attention | mask/Softmax | 기존 FPGA 연산으로 대체 불가 |
| 이전 layer → 다음 layer | residual add/RMSNorm | CPU 경계를 건너뛸 수 없음 |
| Optional LoRA A → B | FP32 복원/재양자화; 실제 adapter 활성 여부 미확인 | 직접 연결 가능성 미증명 |
| Dense block partial + RMD → output | Scale metadata 기반 CPU float reduction/merge, simulator handle 경계 | 기존 integer scale만으로 동등하지 않음 |

실제 frontend `write_output`은 integer result를 scale/double reduction 후 float로 복원하며 다음 activation 입력은 CPU 양자화를 거친다. Core의 integer multiply/shift는 그 rounding/clipping/group/outlier policy와 같지 않다. 실제 backend도 P2 UART device backend가 아니라 IM2P simulator archive를 호출한다.

판정은 **Blocked: 현재 연산 집합만으로 의미가 보존되는 실제 local chain 없음**이다. Synthetic 반복 DAG나 마지막 결과만 반환하는 대체 workload를 만들지 않았다. P3B RTL, 새 DAG의 matched simulator/FPGA 비교, 제거된 실제 애플리케이션 transfer bytes, 전체 모델 Mode C는 모두 **NOT RUN**이다. Shape/layout/dtype/scale/lifetime/CPU/기존·신규 연산/host visibility의 상세 matrix와 source line 근거는 [eligibility 보고서](../build/experiments/fpga-p3a-20260909-142748/evidence/application/README.md)에 있다.

## 8. 성능 모델 및 crossover

새 protocol에서 P2의 16.38/28.84 ms fixed cost를 재사용하지 않는다. 보드 실측 후 mode/input별 새 timestamp로 다음 식을 fit한다.

```text
T_diagnostic(B) = L_new + T_input(B) + T_execute(B) + T_completion(B)
T_all_results(B) = actual interval from start to final host-visible result
T_ownership(B) = actual interval through exact verification and RELEASE
T_result_payload(B) >= B * 1024 / 100000 seconds
```

Completion header 48 bytes의 nominal UART wire 시간은 0.48 ms/batch, START 20 bytes는 0.20 ms/batch다. 이는 host latency 포함 실측이나 speedup 예상치가 아니다. COMPLETION_ONLY는 START completion 뒤 READ header/payload와 RELEASE가 따로 필요하다. FULL은 START DATA 뒤 RELEASE다. Resident의 소유권 전체 protocol round trip은 각각 3/2회, bulk는 L publication 1회를 추가한다. Bulk input은 batch당 1,040 bytes, L command 포함 TX 1,041 bytes/ACK RX 2 bytes다. Resident timed batch의 input 전송은 0 bytes이며 최초 publication은 별도 기록한다. Header/command와 payload bytes, OS API call 수를 구분해 기록한다.

UART/readback/compute 구간을 overlap 가정으로 임의 차감하지 않는다. 실행 cycle counter는 host polling/TX를 포함하지 않는다. Nominal core 361/25MHz = **14.44 us/job**, batch 412/25MHz = **16.48 us/job**이며 외부 clock 실측이 아니다.

현재 결론: **P2 full-output crossover 없음. P3A diagnostic/full-output crossover 미측정. P3B 및 Mode C 미측정.** 위 Verilator API와 향후 FPGA 서비스 경계를 맞춘 뒤 각자 speedup을 계산해야 한다.

## 9. Full-output transport T

Historical 1.528364 ms/job보다 빨라지려면 output 1,024 bytes만으로 effective payload **669,997 bytes/s 초과**, 8N1 wire rate **6.700 Mbaud 초과**가 필요하다. Compute/command/input/OS 고정 비용은 추가다. P2 B64 bulk의 모든 UART bytes 기준 wire-only 하한은 약 6.809 Mbaud다. 이는 새 P3B 성능 보장이 아니다.

공식 Digilent Arty 문서/Arty-A7 schematic, FTDI FT2232HQ, TI DP83848J와 현재 top을 대조했다. 현재 연결은 UART다. FT2232HQ IC의 UART 최대 12 Mbaud가 이 보드·현재 RTL·Linux 설정에서 검증된 지원 속도라는 뜻은 아니다. 25 MHz integer divider에서 DIV4=6.25 Mbaud, DIV3=8.333 Mbaud이며 후자는 3 clocks/bit뿐이다. RX 동기화/샘플링/baud matching을 검증해야 한다.

Arty A7의 onboard 10/100 Ethernet은 MAC/MDIO/packet/CRC/CDC/IM2P adapter/host driver가 필요하다. 100 Mb/s raw의 1,024-byte 시간 81.92 us는 framing/RTT를 제외한다. 예산상 RX/TX 각 2 KiB는 총 4 KiB/2 RAMB18로 시작할 수 있지만 합성값은 아니다. AMD EthernetLite 공식 OOC 예제 604~609 LUT/687 FF/4 RAMB36은 다른 speed grade/package/Vivado2016.1 조건이므로 현재 통합 비용으로 쓰지 않는다.

FT2232HQ FIFO 40 MB/s 사양은 기존 JTAG/UART 배선을 소프트웨어만 바꿔 얻는 경로가 아니다. 외부 FT600/601 bridge의 보유/연결도 확인하지 않았다. Compression이나 INT32 precision 축소를 구현하지 않았고 임의 출력에 압축률을 가정하지 않았다. 실제 transport 전환/보드 실험은 **NOT RUN**이다. [공식 출처·재현 계산·예산](../build/experiments/fpga-p3a-20260909-142748/evidence/transport/README.md).

## 10. 판정·미검증·승인 경계·다음 명령

| 구분 | 판정 |
|---|---|
| P2 source/artifact 보존 | Keep |
| P3A minimal RTL/host/retention 및 production regression | Keep |
| P3A resource/25 MHz routed gate | Keep; occupied slices 95.50%, hold +0.009 ns |
| Assertion-enabled core runtime gate | **FAIL / 원인 해결 미완료** |
| P3A programming/보드 측정 | **Awaiting approval**; 위 검증 한계 포함한 별도 명시 승인 필요 |
| 실제 P3B local chain | Blocked |
| T | 검토/대역폭 예산 완료; 구현/측정 NOT RUN |

전체 gate PASS를 전제로 programming 승인을 요청하지 않는다. 먼저 assertion 검증 한계를 검토할 수 있도록 후보와 실패 근거를 함께 제출한다. 이 후보에 대한 새 승인이 없다면 기존 P2 구성을 유지한다. P1/P2의 과거 승인은 P3A에 적용하지 않는다.

후보 bitstream은 `implementation-04/board/resident.bit`다.

```text
SHA256 cf071c3905d05258fe1509f6a31f82e8764e55e7dd23f2533e3dea044b0b6599
Route DCP 3ae386a366d21a4ebeab725237825ceffba1035cfc3659d8652cfc0fea0bc1b1
JTAG xc7a100t_0 / die xc7a100t
Implemented part xc7a100tcsg324-1
Nominal core 25 MHz / UART 1 Mbaud 8N1
```

JTAG die와 artifact의 package/speed grade는 별도로 확인한다. [검토용 plan/hash-locked script](../build/experiments/fpga-p3a-20260909-142748/approval/plan.json)는 **실행하지 않았다**. 승인 후 계획은 SRAM programming 1회, FULL_RESULTS/COMPLETION_ONLY × resident/bulk × B1/2/4/8/16/32/64, 각 warm-up 1회 제외 + 5회다. 같은 resident payload를 사용하고 completion 이후 deferred full readback/exact check를 RELEASE 전에 수행한다. Checksum은 추가하지 않았다.

예상 보드 변화는 기존 P2 SRAM 구성이 P3A로 교체되고 local input/result publication이 초기화되는 것이다. Hash/device/part/verification/DRC/runtime/status/cycle/result mismatch, 10초 sample timeout이면 중단한다. Retry/자동 reset/복원 programming/flash write 없음. Source/bitstream을 측정 중 변경하지 않는다. Linux serial 장치 경로는 승인 후 실제 연결을 재확인한다.

검증 재현은 아래 명령을 experiment root에서 실행한다. `NEW_*`는 존재하지 않는 새 디렉터리다. 원본 로그·정확한 argv·tool version·파일 hash는 해당 evidence의 JSON에 있다.

```sh
# 보드 접근 없는 RTL/host 전체 회귀. P2/P3 capture 경로 충돌 수정 runner.
python3 -B validation-source-05/run_check.py NEW_SIM

# 이미 생성된 frozen capture의 host 검증만 재현.
python3 -B evidence/host-protocol-validation/run.py candidate-04 sim-04/check NEW_HOST_CHECK

# 같은 frozen candidate의 새 implementation. 기존 implementation-04는 보존.
/tools/Xilinx/2025.2/Vivado/bin/vivado -mode batch -nojournal \
  -log NEW_IMPL.log -source flow/board_build.tcl \
  -tclargs candidate-04/inputs NEW_IMPL

# 별도 승인을 받은 programming/verification 이후에만 실행할 보드 측정 명령.
python3 -B candidate-04/host_p3a.py --port /dev/serial/by-id/VERIFIED_DEVICE \
  --out NEW_BOARD_MEASUREMENT --samples 5 --timeout 10 \
  --bitstream-sha256 cf071c3905d05258fe1509f6a31f82e8764e55e7dd23f2533e3dea044b0b6599
```

`--bitstream-sha256`는 host log 식별자이므로 programming 직전/직후 hash/device/part/status 검증을 대체하지 않는다. Assertion 실패 재현 명령은 `evidence/assertion-core/standalone-run02/build-command.json`, `run-argv.json` 및 turnover 진단 README에 보존했다. 검증 실패를 해결하려면 원래 P2 source/schedule을 별도 실험으로 다루어야 하며 이 candidate에 조용히 섞지 않는다.

## 11. Git 상태와 보존

Branch는 `exp/pe-local-partial-int20`, HEAD는 위 P2 baseline과 동일하다. 기존 tracked 17개 변경과 untracked 문서 4개를 유지했다. 이번 root 변경은 새 문서 `docs/FPGA_P3A_RESEARCH.md` 추가뿐이고 구현·raw artifact는 ignored experiment root에 있다. 새 기능을 HEAD에 포함했다고 표현하지 않는다.

최종 `git branch --show-current`, `git status --short`, `git diff --check`와 원본/복사본/후보 파일별 hash 검증 결과는 `final-verification.json`에 기록한다. 자동 commit/push 및 사용자 변경 삭제 없음.
