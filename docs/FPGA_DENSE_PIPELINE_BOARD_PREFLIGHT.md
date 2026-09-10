# IFR2 Dense FULL/live PIPELINE 실보드 승인 후 preflight

상태: **Blocked — programming 전 중단**.

승인한 artifact의 hash는 모두 일치한다. 그러나 이전 승인 패키지에는 FULL cycle의 즉시 중단 검사와
persistent PIPELINE 상세 계측이 빠져 있다. 승인된 host/fixture/plan을 수정하지 않고는 최신 실행 조건을
모두 충족할 수 없어 보드에 접근하지 않았다. FPGA 고장이나 수치 실패를 관측한 결과가 아니다.

## 실제 실행량

| 작업 | 승인량 | 이번 실행량 |
|---|---:|---:|
| Configuration SRAM programming | 1 | 0 |
| FPGA logical GEMM | 56 | 0 |
| Matched simulator invocation | 54 | 0 |
| UART CAP | 승인 절차에 포함 | 0 |
| Flash / reset / ABORT / 재시도 / 자동 복구 | 금지 | 0 |

UART 경로는 호스트 파일 목록에서
`/dev/serial/by-id/usb-Digilent_Digilent_USB_Device_210319BE7725-if01-port0`가
`/dev/ttyUSB1`을 가리킴을 확인했다. 장치를 open하지 않았다. JTAG die 확인, programming,
configuration 상태 확인, 독립 SRAM readback은 모두 NOT RUN이다. Programming-attempted marker도 없다.
합성·RTL simulation·benchmark를 추가로 실행하지 않았다.

## Artifact preflight

Hardware는 stream-02, host/frontend는 stream-08이다. Root rebuild와 기존 artifact 교체는 없다.

| Artifact | 실제 SHA256 |
|---|---|
| Bitstream | `8aef393d040bb306e6ddf7b4b977976a9a924dbc5aec62b37b1690f0aa18c0ca` |
| DCP | `6141a245e4c2ca88b8147ae864974abc14c3b858a276c2a5f5beee42f5a92bd4` |
| dense_host_dispatch | `f5cd48e36e6dab5566b2ea438e265541884539eec70dcd1b1c6059fd77c3ba10` |
| persistent_replay | `f057b99fc188673cbfad29bd9957616143ad89458e5d4c5de0d583595cfd6e03` |
| Host08 integration manifest | `0bf50916cd891c766f4ef372c68d9c07494a419606bdd44c289e98954bf81454` |
| Measurement plan | `07e29d51e7ac5c027499b39289cf07e85349e0779bc24d7b6fd27917e041899d` |

Root approval.json과 frozen approval-package/approval.json도 byte-identical이다.
Plan의 4,961개 파일, package manifest 27개, versionable source manifest 61개,
test evidence manifest 234개를 모두 실제 hash로 검증했다. 이 집합은 겹치므로 고유 파일 수로 합산하지 않는다.
기존 611개 경로 불변, 변경 전 source 5개 blob의 동일 hash 백업, 이전 board 결과 825개 보존도
plan 검증 범위에 포함한다. 현재 616개 경로가 전부 불변이라고 주장하지 않는다.

승인 순서와 수량(smoke1, 진단1, sweep54, 이후 simulator54)은 일치한다.
예정 비교 raw1,881,568 / f_out756,784 / padding46,512도 재계산했다. 실제 board 비교는 모두0회다.

## 중단 근거

### 1. FULL cycle 검사 부재

고정 `stream-08/source/fpga/dense_pipeline/uart.cpp:117`의 statistics 검사는 `cycles > 0`이다.
361 / 39,907 / 58,695와의 일치 검사는 없다. `persistent_replay.cpp:328`은 SAMPLE을 flush한 뒤
다음 iteration으로 바로 진행한다. `measure.py:133–145`는 조건 process가 종료된 뒤 로그를 읽고,
`:74`도 양수 여부만 검사한다.

따라서 사용자 승인 §5의 “설명되지 않는 FULL cycle 차이에서 다음 실행 중단”을 보장하지 않는다.
외부 tail/kill은 다음 invocation 제출과 경쟁한다. 실행 중 다음 명령을 차단하는 기존 ACK/credit 옵션도 없다.

재현: 승인된 summarize 함수에 M16/N16/K32의 cycle362를 넣은 synthetic sample 6개를 전달했다.
기준361과 다르지만 parser가 받아들였다. 이 검사는 parser-only 반례이며 FPGA/simulator numerical 실행이나
PASS 증거가 아니다. 입력과 판정은 별도 preflight 경로에 보존했다.

### 2. 승인한 persistent 계측값의 미노출

UART는 work/fragments/output ACK와 stripe identity를 검사하지만, persistent SAMPLE은 elapsed cycle만 출력한다.
wait/overlap/work, stripe별 publication/first-A 관측은 출력하지 않는다. Frontend stripe timing view도
Run.reset 이후 유효하지 않다. 기존 CLI/env에 이 자료를 회수하는 옵션이 없다.

실제 ggml 진단 1회는 LIVE_STRIPE와 first-A를 출력할 수 있다. 하지만 이를 persistent sweep 대신 실행하면
승인 plan 및 측정 경계가 달라진다. 새 PTY proxy/ptrace/임의 대기 또는 host binary 변경을 몰래 추가하지 않았다.
따라서 승인 §7/9의 무지연 producer 관계와 PIPELINE wait/작업 counter를 지금 artifact로 모두 보고할 수 없다.

### 3. Reference marker 범위

`validation=reference/check`와 `reference_dot_calls/reference_matmul_calls`는 dense_host_dispatch의 benchmark
명령에 있다. 승인 smoke/진단은 run, sweep은 persistent_replay다. Persistent는 warm-up을 포함해 저장된
expected-raw/expected-fout과 비교하며 reference 계산을 호출하지 않는다. 해당 literal marker가 실행 로그에
존재한다고 보고해서는 안 된다. 코드상 호출 경로 확인과 실제 marker를 구분해야 한다.

## 필요한 최소 후속 변경 — 미적용

수치 core/RTL/bitstream/fixture를 바꿀 필요는 없다. 다음 host 계측 변경은 현재 승인에서 금지되어 적용하지 않았다.

1. Persistent FULL fence 직후, RELEASE/다음 invocation 전에 shape별 production cycle을 검사한다.
   불일치하면 해당 response/상태를 보존하고 즉시 종료한다. 자동 ABORT/reset/retry는 하지 않는다.
2. Run retirement 전에 3개 stripe timing과 기존 work/wait/overlap/ACK 값을 작게 보관한다.
   Service/sustained timestamp 뒤 출력하여 대형 파일 저장을 timer 안에 넣지 않는다.
3. 기존 publication/response 경계의 host timestamp와 device observation을 보존해 producer-ready와 인과 관계를
   보고한다. 자연 overlap을 만들기 위한 지연은 넣지 않는다.

이후 변경한 host/frontend를 함께 재빌드·검증하고 새 executable/manifest hash를 제출해야 한다.
현재 사용자가 host 수정 금지를 명시했으므로 변경과 새 artifact 사용은 별도 허용이 필요하다.
이전 구현 보고서 및 승인 패키지는 수정하지 않았다.

## 측정 및 최종 상태

새 FULL cycle 측정, PIPELINE counter, median/min/max, speedup, 자연 overlap은 모두 **NOT RUN**이다.
이전 RTL/simulator 값을 실보드 결과나 새 matched simulator 분모로 재사용하지 않는다.
Source/host/fixture/plan 수정, git staging/commit/push는0이다. 양 repository와 include의 git diff --check는 PASS다.

이번 결과는 **artifact preflight PASS / execution contract Blocked**다. 승인한 programming 1회와 workload는
아직 실행하지 않았다. 승인이 없어서 멈춘 것이 아니라 승인된 실행파일의 검사·계측 누락 때문에 멈췄다.

원본 preflight 증거: `build/experiments/dense-pipeline-20260909T141302Z/board-preflight-20260909T170306Z`.
