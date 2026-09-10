# Dense PIPELINE 25 MHz 구현 검토

검토 대상은 frozen `stream-02` RTL이다. 호스트 후속 수정과 별도로 식별한다.
Arty A7-100T, implementation part `xc7a100tcsg324-1`, A8/W8/DIM16,
nominal core 25 MHz에서 배치배선이 완료됐다. 새 후보의 programming과 실보드
측정은 **NOT RUN**이다. 아래 FULL 수치는 기존 검증 artifact의 결과다.

원본 경로:

- FULL: `build/experiments/host-full-replay-20260909T103729Z/candidate-01`
- PIPELINE: `build/experiments/dense-pipeline-20260909T141302Z/stream-02`
- 비교 원문·경고별 cell·hash:
  `build/experiments/dense-pipeline-20260909T141302Z/evidence/implementation-warning-review.json`

각 후보의 `vivado.log` 한 개와 `route/*.rpt`를 사용했다.
`vivado_54.backup.log`를 중복 집계하지 않았다. 도구는 Vivado 2025.2다.

| Post-route 항목 | 기존 FULL | PIPELINE |
|---|---:|---:|
| LUT | 46,124 | 47,986 |
| FF | 30,126 | 32,032 |
| RAMB36 / RAMB18 | 27 / 1 | 89 / 1 |
| BRAM36-equivalent tiles | 27.5 | 89.5 |
| DSP | 72 | 85 |
| Occupied slices / 15,850 | 15,589 (98.35%) | 15,596 (98.40%) |
| Completely empty slices | 261 | 254 |
| Control sets | 787 | 625 |
| 최대 non-clock fanout | 10,574 | 10,723 |
| Setup WNS | +10.870 ns | +12.517 ns |
| Hold WHS | +0.024 ns | +0.018 ns |
| Pulse-width slack | +3.000 ns | +3.000 ns |
| Routable / fully routed nets | 78,154 / 78,154 | 81,452 / 81,452 |
| Route errors / black boxes | 0 / 0 | 0 / 0 |
| Unconstrained internal endpoints | 0 | 0 |
| DRC warnings | 159 | 178 |

최대 fanout은 두 후보 모두
`shell/core/core_core_systolicArray_processingElements_9_9_activeWeightBankReg`다.
두 congestion 보고서 모두 level 5 초과 window를 보고하지 않는다.
새 후보의 LUT 증가 1,862개와 FF 증가 1,906개에도 control sets는 162개 줄었다.
빈 slice 254개는 여유가 작다는 증거지만 실제 route/timing 결과를 대신하지 않는다.
이번 후보는 gate를 통과했으므로 추가 면적·Fmax 최적화는 수행하지 않는다.

## 경고 ID와 원인

| ID | FULL | PIPELINE | 검토 |
|---|---:|---:|---|
| Synth 8-7071 | 21 | 29 | 미사용 MMCM 출력과 상수 getter RDY. 새 getter 8개 증가. Action handshake RDY는 연결됐다. |
| Synth 8-7023 | 2 | 2 | MMCM 선택 출력과 생성 core getter RDY의 명시적 미연결. 새 core 79개 port 중 61개 연결, FULL 43개 중 33개 연결. |
| Synth 8-6014 | 77 | 73 | 미사용 상태·순차소자 제거. `PIPELINED=0` BRAM2의 DOA_R2/DOB_R2 포함. 실제 A/C 배열은 아래 mapping에 남는다. |
| Synth 8-3936 | 1 | 2 | 공통 matrixWorkReg 898→738 bit. 새 activationSlot 주소식 14→13 bit 축소 추가. |
| Synth 8-3917 | 0 | 3 | 새 `dense_uart_shell__GCB0.P[2:0]` 상수 0. byte-count 곱셈 주변 합성 로그에서 발생한다. |
| Synth 8-3323 | 1 | 1 | 중간 DSP 추론 FULL 395 / PIPELINE 406이 240개를 초과. 후속 DSP mapping/rejection 뒤 최종 72 / 85개다. |
| DPIP-1 | 21 | 22 | DSP 입력 pipeline 권고. publication 주소 곱셈 1개 증가. |
| DPOP-1 | 69 | 78 | PREG=0 권고. lookahead 4개, stripe 주소 4개, publication 주소 1개 증가. |
| DPOP-2 | 69 | 78 | MREG=0 권고. DPOP-1과 같은 새 9개 DSP 경로. |

새 19개 DRC를 이전 159개와 동일하다고 처리하지 않았다. 새 cell 그룹은
`_0_CONCAT_publish_rowBegin_0249_MUL_0_CONCAT_co_ETC___d10252`,
`_0_CONCAT_core_core_core_matmulScheduler_lookah_ETC___d6483`,
`_0_CONCAT_core_core_core_matmulScheduler_stripe_ETC___d6408`이다.
정확한 suffix별 cell과 메시지는 JSON에 보존했다. DSP pipeline 권고는
등록단계 추가 시 scheduler 지연 변경을 수반한다. 이번 25 MHz 경로는 해당
경고를 남긴 상태로 실제 setup/hold/pulse gate를 통과했다.

`Synth 8-3917`은 새 메시지다. 원본 `vivado.log:1761–1772`에는
`dense_uart.sv:235`의 wide multiplier, `p_0_out`, `output_words_reg` 추론 다음
상수 port 경고가 이어진다. byte-count 하위 상수 bit라는 분류는 이 로그와
`reply_bytes <= (...) << 6`에 근거한 추론이다. GCB 내부 연결을 별도 netlist로
역추적한 증거는 아니다. 최종 latch 0, protocol 길이/주소·RTL 수치 검증과
timing 결과를 함께 사용했다. ERROR 및 CRITICAL WARNING은 두 canonical
Vivado 로그에서 0건이다.

## BRAM 역할·port·충돌 경계

| 저장소 | FULL 할당 | PIPELINE 할당 | 새 실제 mapping |
|---|---:|---:|---:|
| Architectural accumulator | 64 KiB | 64 KiB | 16 × RAMB36 |
| A staging | 4 KiB | 64 KiB | 14 × RAMB36 + 1 × RAMB18 |
| W staging | 8 KiB | 8 KiB | 2 × RAMB36 |
| Host-visible raw result | 32 KiB | 256 KiB | 57 × RAMB36 |
| Scale | identity logic | identity logic | scale BRAM 0 |

이 표는 논리 배열 할당과 Vivado의 실제 packing을 구분한다. 새 A는
4096×128 bit, C는 4096×512 bit dual-port 배열이다. W는 512×128 bit,
accumulator는 16개의 1024×32 bit 배열이다. A/C의 word 주소 여유와 padding은
유효 payload가 아니다. CAP M336/N48/K96의 유효 raw는
`4 × 336 × 48 × 3 = 193,536 bytes`, device raw span은
`256 × 336 × 3 = 258,048 bytes`다.

새 A/C는 설치된 BSC 2026.01 `lib/Verilog/BRAM2.v`와 SHA256이 같은 primitive를
사용한다. primitive의 `syn_ramstyle="no_rw_check"`는 새로 추가한 attribute가
아니다. Vivado는 사용하지 않는 A-port readback을 최적화하면서 A/C의 쓰기
port를 READ_FIRST, 읽기 port를 WRITE_FIRST로 보고한다. 따라서 primitive의
WRITE_FIRST 설명만으로 cross-port same-address 동작을 보장하지 않는다.

현재 transport 경로는 같은 주소의 동시 read/write 결과에 의존하지 않는다.

- A port A는 미공개 logical row만 쓴다. port B의 core는 공개된 row만 읽는다.
  `DensePipeline.bsv:155`의 assertion은 `write_row >= publishedRows`를 검사한다.
  shell은 다음 canonical stripe의 payload를 받은 뒤 CRC 성공 시에만 공개한다.
  이미 공개한 행은 invocation 동안 유지한다. Event slot 0/1/0 재사용은 device
  A 주소 재사용이 아니다. 잘못된 CRC는 publication 없이 sticky error를 만든다.
- C port A는 core output request를 쓰고 같은 규칙에서 output ACK를 반환한다.
  shell은 `stripeCompletionValid` 이후에만 해당 stripe C를 port B에서 읽는다.
  후속 stripe는 다른 logical row/plane에 쓴다. C는 invocation 동안 보존한다.
  `POLL` 전송 중 이전 stripe를 읽는 동안 후속 stripe 쓰기가 가능하다.
- W는 RUN 전에만 적재하며 RUN 중 단일 port로 읽는다.

이 경계는 실제 UART shell을 통한 호출에 적용된다. BSV `requestOutput` 직접
호출에는 row authorization 검사가 없으므로 임의의 비신뢰 provider 호출까지
확장한 보장은 아니다. 원본 Vivado 로그에 collision 경고는 없지만 이것을
충돌 부재의 증명으로 사용하지 않는다.

별도 fresh assertion-enabled provider 검증은 M321/N48/K64에서 FULL 1회와
160/160/1-row PIPELINE 1회를 실행했다. 이전 raw의 지연 읽기와 다음 A staging,
output backpressure를 포함해 raw 61,632회 exact 비교를 통과했다.
runtime assertion/finish는 0건이다. 결과는
`evidence/assertions-new/summary.json`에 있다. 이는 bounded RTL simulation이며
exhaustive formal proof, post-route RAM collision simulation은 **NOT RUN**이다.

## Clock·외부 I/O·승인 경계

XDC는 FULL과 byte-identical이며 SHA256은
`8f9797530db5b6042d9ba9cd21db9843d6cb87f6c1b845fee808dc73e532bfb3`다.
새 false_path/multicycle/max-delay 예외는 없다. MMCM에서 25 MHz가 유도된다.
두 CDC 보고서는 `All paths are Safely Timed.`를 기록한다.

`check_timing`의 `uart_rx` input delay 1개, `uart_tx` 및 LED output delay 5개는
외부 비동기 I/O 항목으로 그대로 남겼다. 내부 endpoint 0건과 구분한다.
이 보고서는 외부 UART 파형 계측이나 보드 CDC 검증을 완료했다는 뜻이 아니다.
새 bitstream은 별도 manifest/hash로 승인해야 하며, 기존 FULL 승인을 재사용하지
않는다. 이 검토에서는 programming, 물리 UART open/CAP/job을 실행하지 않았다.
