# SCU H1 package02 제한 실보드 correctness 검증

2026-09-11 KST. 승인된 configuration SRAM programming **1회**, logical GEMM **5/5회 PASS**. Final signed32 integer와 최종 float32 f_out은 각각 **31,718회 exact**, wire padding은 **442개 검사**다. 추가 smoke, warm-up, 반복 성능 측정, matched simulator 실행은 하지 않았다.

범위는 Arty A7-100T, `xc7a100tcsg324-1`, A8/W8, physical DIM16, nominal core25MHz, UART1Mbaud/8N1, ABI5/IFR3/`signed-scu-sat-v2`/unsigned H1/final integer domain2, EXSIA activation/RMD OFF Dense FULL 및 live PIPELINE이다. 이 결과를 HP1 FPGA, residual, physical DIM64, 전체 모델/PPL/TTFT/TPOT로 확대하지 않는다.

원본 결과 디렉터리:

`/mnt/fpga-build/im2p/scu-final-20260910T082724Z/board-correctness-package02-01`

[집계·보존 결과](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/board-correctness-package02-01/results.json), [파일 preflight](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/board-correctness-package02-01/preflight.json). 이전 simulation/route 보고서와 approval package는 수정하지 않았다. 이전 문서의 Board NOT RUN은 그 문서가 봉인된 시점의 이력이다.

## 1. 승인 identity와 preflight

| 대상 | 실제 확인한 SHA256 |
|---|---|
|Package02 approval.json|`4979f9237ab49303768e004997f03bc281ee60088ab63be5ce5447e50037b690`|
|Candidate manifest|`e5272e4bd95f82b03e2e8518fd56054dda1d821a1cdf76e3f152324a00a8bcd8`|
|고정 실행 plan|`da2e9c15145a3ef2a1a479eb6a67849e7616eabef3997ba25c76ce43f2f417b3`|
|Bitstream|`2c5516e2bae4d5f960697537bb1501d42470b7971be5986b6e59ad8cc6586984`|
|Routed DCP|`828c09cf0f718c535957c72c4a69bfc954ac306e06f44c467530ef4599004995`|
|Production mkScuPipeline.v|`1833a6a4f53e48fa18f0afb188fbd2858a7584376701a333374025174d061c26`|
|Frozen integration manifest|`607827f129c61b60dffd9e9fb02fd0ceb4a7ab2abbeb25bd6b08d4d9b2782d69`|
|scu_host_dispatch|`0ca9b6465ba69682ac0b4a9a234d21ee8728f8682efd41c89eab02c5f0873f5f`|
|libim2p_sim.a|`0cf3a3c0eeada8cfa25520952d9c746f397d968a51a504e4fd8fb1a1b9d87709`|
|libim2p_gemmini_frontend.a|`a1cb9a4f7598cbeb2ef6bf6ad427ae80956a6831e90ce0122d8ce6375ad82240`|
|FULL cycle reference|`4da8b84c9d1d2f678491e3b02893bda0065cae1bd4f3cd838ded316f0dd1276c`|
|FULL cycle provenance|`7cfc1e584d076a072aab404746459e105257839346b253a628fc80c56e433987`|

Branch는 `fix/scu-block-scale`, HEAD는 `3aeb5feee6872f88ec1f6a5dc0d77fb1bb8babf8`다. 기존 unstaged source를 유지했다. Host/include pin은 각각 `7a74ac29c9c8fa8fbc5d47cf073b8b9266e3e7a9`, `cd2caed8d93e49d3f8873bd6c4e340fcad54b9e0`이며 frozen selection을 사용했다. 현재 root나 과거 host09를 재빌드하지 않았다. Native generated RTL의 과거 `not_collected/not_retained` 한계는 그대로이며 다른 RTL hash로 대체하지 않았다.

파일 preflight에서 package 연결 hash, frozen1,746개, baseline tar331blob, 기존 실패127파일, historical6artifact, 현재 source를 포함한 보존 대상2,558개를 확인했다. DCP archive의 실제 `dcp.xml`에서 part를 확인했고 programming Tcl에서도 `open_checkpoint` 후 다시 확인했다.

이번 CLI는 저장 expected 파일을 읽는 prequantized replay가 아니다. 고정 host의 `Graph::prepare(seed)`와 기존 native quantizer/ExSIA producer가 입력을 생성한다. 아래 shape/mode/seed와 frozen source·host hash가 fixture identity다. 별도 input/expected 파일을 바꾸거나 새 expected로 결과를 맞추지 않았다. 독립 G1/G2는 실제 FPGA 결과 수신 뒤 실행되며 FPGA 결과를 대신 생성하지 않는다. 실제 물리 전송 payload hash나 raw/f_out 전체 배열 dump는 이번 host가 파일로 보존하지 않아 **not_collected**다. 이전 RTL payload hash를 이번 물리 전송의 실측 hash로 쓰지 않는다.

실행 전 다음 변수를 모두 unset했다: `IM2P_FPGA_ALLOW_UNPINNED_RTL_TEST`, `IM2P_FPGA_TEST_PRODUCER_OVERLAP`, `IM2P_FPGA_TEST_WAIT_FIRST_READ`, `IM2P_FPGA_TEST_FAIL_AFTER_STRIPE`. Physical transaction timeout30초, process timeout900초를 유지했다. FULL reference가 package01 경로를 참조하는 것은 package02에 명시된 동일 immutable 파일 연결이며 IFR2 reference가 아니다.

Preflight 운영 도구에서 중단1건이 있었다. 45-uart-ownership의 `lsof`는 holder를 출력하지 않았지만 다른 Docker/FUSE mount를 stat할 수 없다는 경고를 냈다. Strict wrapper가 stderr를 거부하여 `fuser` 실행 전에 중단했다. 이 원본 실패를 보존하고 별도46-uart-fuser에서 해당 UART holder 없음(exit1,stdout/stderr 없음)을 확인했다. 이때 programming/GEMM은0회였으며 실패 GEMM 재시도나 경고 suppression이 아니다.

## 2. Programming과 CAP

UART는 `/dev/serial/by-id/usb-Digilent_Digilent_USB_Device_210319BE7725-if01-port0`, 실제 장치는 `/dev/ttyUSB1`이었다. 기존 hw_server3121을 사용했으며 서비스 재시작이나 새 복구 동작은 없었다.

기존 one-shot Tcl의 운영 사본에서 bit/DCP hash literal 두 개만 승인 값으로 바꿨다. 원본 Tcl bytes와 hash를 보존했으며 host/RTL/fixture/plan/기존 programming source는 수정하지 않았다. 운영 사본은 다음 경로다.

`board-correctness-package02-01/programming/program-approved-once.tcl`

실제 명령은 `/tools/Xilinx/2025.2/Vivado/bin/vivado -mode batch -nojournal -log …/programming/vivado.log -source …/program-approved-once.tcl -tclargs …/scu-pipeline.bit …/route.dcp`다. 전체 argv/cwd/env는 [47-program-approved-once.command.json](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/evidence/47-program-approved-once.command.json)에 있다.

Programming은 UTC2026-09-10T21:51:46.185902부터21:52:08.567436까지22.381424초,exit0이었다. Cable `210319BE7725A`, chain의 유일한 device `xc7a100t_0`, die `xc7a100t`를 확인했다. DCP의 package/speed grade `xc7a100tcsg324-1`과 JTAG die 식별은 별개다. CREAT EXCL attempt marker 뒤 `program_hw_devices`를 정확히1회 호출했다. BEGIN/COMPLETED marker 각각1개, CRC/error0·PLL/EOS/INIT/DONE1 등 configuration 상태15개 PASS였다.

이는 programming/configuration 상태 확인이다. **독립적인 전체 configuration SRAM readback 비교는 수행하지 않았다.** Flash/non-volatile write와 이전 구성 복원은0회다. [Programming 원본 로그](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/evidence/47-program-approved-once.log).

각 host process의 UART constructor는 첫 RUN/BEGIN 전에 CAP version3/profile0810/semantic capability0294/capacity336×48×96, CRC·status·idle fields를 동기적으로 검사했다. 성공한 `FPGA_UART_IDENTITY` marker는 이 gate를 통과한 뒤 출력된다. Capability0294는 고정 host의 H1/final domain2/signed-SCU numerical 계약과 대조됐다. Raw CAP frame은 **not_collected**이며 CAP 성공을 SRAM readback 증거로 사용하지 않는다. 별도 관측 CAP를 추가하지 않았다.

## 3. 고정 5 GEMM 결과

첫 케이스를 승인된 초기 smoke로 사용했다. 각 process의 exit와 numerical/backend/completion marker를 확인한 뒤에만 다음 process를 실행했다. Tail 두 입력은 고정 CLI의 같은 process에서 연속 실행했다.

| 순서 | Shape / mode / seed | run / generation | Final integer exact | f_out bit-exact | Wire padding | FULL expected / actual |
|---|---|---|---:|---:|---:|---|
|1|M16/N16/K64 FULL,1|1 /1|256|256|0|617 /617|
|2|M321/N48/K96 FULL,3|1 /2|15,408|15,408|0|52,687 /52,687|
|3|M321/N48/K64 live PIPELINE,2|1 /3|15,408|15,408|0|적용하지 않음|
|4|M17/N19/K64 FULL,4|1 /4|323|323|221|1,949 /1,949|
|5|M17/N19/K64 FULL,5|2 /5|323|323|221|1,949 /1,949|
|합계|5 logical GEMM|generation1…5|31,718|31,718|442|FULL 차이0|

Run ID는 process마다1에서 시작하고 같은 process의 두 번째 tail만2다. Generation은 device에서1,2,3,4,5로 이어졌다. 임의 reset으로 tail 상태를 초기화하지 않았다.

| 순서 | Output works | K16 fragments | A requests | W requests | C writes / C ACK |
|---|---:|---:|---:|---:|---:|
|1|1|4|64|64|16 /16|
|2|63|378|5,778|6,048|963 /963|
|3|63|252|3,852|4,032|963 /963|
|4|4|16|136|256|34 /34|
|5|4|16|136|256|34 /34|
|합계|135|666|9,966|10,656|2,010 /2,010|

Raw/f_out 수량은 고정 host의 완료 marker에 출력된 실제 logical 비교 개수다. Staged f_out 검사와 caller output 재검사를 각각 별도 logical output으로 중복 합산하지 않았다. Wire padding442는 두 tail의17×(32−19)개씩이며, 고정 `reconstruct()`가 실제 검증된 reply extent를 순회하며 zero를 검사한 개수다. 별도의 device padding counter는 없다. Graph의 caller output은 contiguous이므로 이를 caller stride sentinel442개 검사로 표현하지 않는다.

각 raw 결과는 전체 K 완료 후 M×N signed32 integer다. K96 FULL과 K64 PIPELINE 모두15,408개이며 legacy M×N×block_count plane이 아니다. G1은 fragment별 unsigned H1 scaling/saturation/accumulation을 독립적으로 계산했고 G2는 `float(double(integer) * double(shared S) * double(activation scale))` 순서로 비교했다. 선택 FPGA 연산의 simulator create/execute/stream-begin runtime counters는 모두0이다. 독립 reference 실행5회와 scalar fragment-dot157,688회는 정당한 사후 검사이며 이를 CPU reference 호출0으로 주장하지 않는다. 이번5개 입력의 독립 G1 SCU/accumulator clamp count는0이었다. 별도 beta-edge나 saturation-overflow 보드 GEMM을 추가하지 않았다.

실행 로그의 layer classification fallback 문자열은 수치 fallback이 아니다. 실제 FPGA backend identity와 호출 counters로 판정했다. `LLAMA_GEMMINI_Q8_H1_ARTIFACT is not set`은 이 synthetic host-entry fixture가 모델 artifact 파일을 선택하지 않았다는 로그이며, 모델 실행 또는 모델 capture로 보고하지 않는다.

정확한 CLI는 고정 plan의 다음 네 명령이다. 모두 같은 frozen executable을 사용했다.

```text
scu_host_dispatch run 16 16 64 1 1 FULL
scu_host_dispatch run 321 48 96 3 1 FULL
scu_host_dispatch run 321 48 64 2 1 STRIPE_PIPELINE
scu_host_dispatch run 17 19 64 4 2 FULL
```

원본 argv/env/UTC/exit/log는 RUN/evidence의 `48-board-full-k64.*`, `49-board-full-k96.*`, `50-board-live-k64.*`, `51-board-tail-two.*`다. `timeout --signal=TERM 900s`는 process 한도를 적용했으며 실제 timeout/signal 종료는 없었다.

## 4. Live PIPELINE 관측

Actual automatic geometry는 stripe160/160/1, slot0/1/0, theta−6/−3/0이다. 유효한 post-fold event가 준비되는 즉시 수락 경로를 사용했다. Response 기반 host tally는 publication3/completion3이다. 독립 device publication-total 또는 stripe-ACK-total counter라고 부르지 않는다.

| Stripe / slot / rows | Device publication cycle | Device completion cycle | 같은 device clock의 차이 |
|---|---:|---:|---:|
|0 /0 /0…159|5,597,156|5,615,605|18,449|
|1 /1 /160…319|20,050,768|20,069,218|18,450|
|2 /0 /320|29,022,348|29,023,473|1,125|

Device elapsed는 **29,023,476cycles**, host-wait23,388,293cycles, engine/provider overlap6,426cycles다. Publication과 raw-drain 대기를 포함하므로 FULL expected 또는 이전 RTL PIPELINE elapsed와 equality를 요구하지 않았다. 각 counter를 더해 새 실행시간을 만들지 않았다. Pure compute, stripe별 first-A, cross-stripe overlap, 상세 wait, 독립 publication total은 **not_exposed**다.

Global first-A는5,597,164cycles, 당시 published prefix160이다. First-A는 첫 publication 후8cycles로 관측됐다. Host ready/accepted는 steady-clock ns, 위 publication/completion은 FPGA cycles이며 두 clock domain을 직접 빼지 않았다.

마지막 stripe quantization 종료는 host1610423647405877ns, 첫 publication transfer 경계 시작은1610423647427058ns였다. 따라서 이번에는 전체 quantization 종료가 첫 A 전송보다 앞섰으며 자연 CPU quantization/FPGA compute overlap을 관측하지 못했다. 첫 event 수락이 전체 quantization 종료보다 빨랐다는 live producer 사실과 자연 overlap은 다르다. 세 번째 event ready→accepted546.554276ms는 전체 수락 구간이며 순수 backpressure 시간으로 단정하지 않는다. 지연 주입은 없었다.

## 5. 전송·오류·완료의 의미

고정 host가 출력한 invocation metrics의 합계는 request114,712bytes, response131,896bytes,22transactions다. 정상 RELEASE를 포함하고 constructor CAP는 제외한다. 성공한 constructor4회의 고정 CAP frame36/148bytes를 코드에서 파생해 더하면114,856/132,488bytes,26transactions다. 이는 별도 wire analyzer 측정값이 아니다. 전체 raw response payload는 보존되지 않았으므로 사후 배열 재비교가 아니라 실행 중 exact 검사와 성공 marker가 근거다.

첫 FULL의 service/sustained 관측은93,103,324/108,897,220ns, 긴 FULL은1,122,248,593/1,134,917,119ns, live는1,271,794,280/1,286,128,516ns, 두 tail은105,366,524/121,099,936ns와105,097,524/120,891,605ns다. 독립 reference observer도 포함된 단발 correctness 실행의 원본 관측이다. Median/min/max, throughput, speedup 또는 성능 sweep 결과로 해석하지 않는다.

Programming 실패0, GEMM 실패0, CRC/framing/profile/domain/identity/count 오류0, FULL cycle mismatch0, timeout0, unexpected reset0, retry0, ABORT/reset 복구0, 재프로그래밍0, numerical fallback0이다. 앞서 기록한 lsof preflight wrapper 중단1건은 별도로 남겼다.

고정 `UART::full`의 cycle gate는 reconstruction·caller commit·RELEASE 이전에 있다. Wire 오류는 sticky하며 preserved PTY tests가 오류 뒤 추가 transaction0을 검증한다. 실제 raw/G2/fallback observer도 fence 이후, caller commit·RELEASE 전에 실행되므로 실패하면 다음 iteration 없이 nonzero로 종료한다. Frontend completion 오류는 final_status를 실패로 고정하고 worker/FINISH를 중단한다. 이번5회는 positive correctness 실행이므로 board에서 caller-output-preservation fault를 새로 주입한 것으로 보고하지 않는다. 그 근거는 보존된 software tests와 실제 선택 source 순서다.

일반적인 외부 producer 취소나 잘못된 host post-fold event가 fence 전에 발생하는 별도 경로까지 “모든 host cancellation 후 command0”으로 확대하지 않는다. 해당 경우 destructor가 이미 수락한 stripe를 처리할 수 있는 소스 경로가 있다. 이번 고정 입력·진단 변수 unset 범위의 UART/numerical 오류와는 구분하며, 그런 취소 실험은 수행하지 않았다.

마지막 장치 응답은 generation5의 정상 RELEASE 성공이다. Host Run은 retired되고 fd는 정상 종료 시 닫혔다. 이후 CAP/status/JTAG 거래를 추가하지 않았다. Physical pending UART bytes는 **not_collected**이며 이전 RTL pending0을 재사용하지 않는다. 최종 protocol 상태의 근거는 마지막 검증된 RELEASE ACK이며 별도 사후 장치 재조회가 아니다.

## 6. 보존·Git·종료

실행 후2,558개 기존 경로의 hash를 다시 확인해 모두 일치했다. Baseline source tar의331blob, historical artifact6개, frozen1,746개, 기존 monitor 실패127파일을 보존했다. 원본 blob 보존과 현재 모든 source가 과거 HEAD와 같다는 주장은 다르다. 이번 board 실행 동안 source/host/fixture/expected/plan/bitstream 변경0, 새 build/synthesis/route0, 추가 자료 삭제0이다.

기존 branch/HEAD·57개 modified tracked files·기존 untracked source를 유지했고 `git diff --check` PASS, staging/commit/push0이다. 이 새 보고서만 추가했다. 실행 직후 원본 Git 상태는 `board-correctness-package02-01/git-after`, 보고서 추가 후 최종 상태는 `git-final`에 보존한다. 기존 sealed final-candidate 보고서와 package01/02를 수정하지 않았다.

승인된 5회 correctness 검증을 끝냈다. 추가 programming/GEMM/성능 측정은 하지 않는다. Flash write0회. FPGA HP1/residual/physical DIM64/model quality/PPL/Nano 검증은 NOT RUN 또는 out of scope로 남긴다.
