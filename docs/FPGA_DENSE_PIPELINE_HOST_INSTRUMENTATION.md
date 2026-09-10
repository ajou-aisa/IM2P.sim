# IFR2 Dense host 검사·계측 보완

이번 변경은 host09-02의 동기 FULL cycle 검사와 owned telemetry다. Hardware는 검증·route된 `stream-02` 그대로다. 실제 보드 programming, UART open/CAP, FPGA GEMM은 **0회**다. 수정된 host와 새 plan은 host08 승인 artifact가 아니므로 별도 승인을 기다린다.

## 고정 입력과 재현 경계

이 문서의 경로 약어는 저장소 root에 상대적이다.

```text
H = build/experiments/dense-host-instrumentation-20260909T174219Z
N = build/experiments/dense-pipeline-20260909T141302Z
E = build/experiments/host-full-replay-20260909T103729Z
```

| 역할 | 선택 artifact | SHA256 |
|---|---|---|
| Hardware bitstream | N/stream-02/route/dense-pipeline.bit | `8aef393d040bb306e6ddf7b4b977976a9a924dbc5aec62b37b1690f0aa18c0ca` |
| Routed DCP | N/stream-02/route/route.dcp | `6141a245e4c2ca88b8147ae864974abc14c3b858a276c2a5f5beee42f5a92bd4` |
| Production RTL | N/stream-02/production/rtl/mkDensePipeline.v | `b29147ce384e9384b3821c358863350f03c0696cbc8b8e286a768f2e7155b853` |
| Hardware integration manifest | N/stream-02/integration-sha256.json | `797e0eda08a28f7eaaf738bc4566e63792e02d3492079b4fc1d70028d38845f7` |
| 새 dense_host_dispatch | H/host-09-02/host-build/dense_host_dispatch | `d8e602a0f102c289f19ae6e681e02c723f1b7649f16fd92ef70a012969c2e2ea` |
| 새 persistent_replay | H/host-09-02/host-build/persistent_replay | `5536f7025f0d640014e867283111c97f4c24344e88ca4d0667b6f8fd09c48e85` |
| 새 host/frontend integration manifest | H/host-09-02/integration-sha256.json | `71d526ceab9ca9d955cc04e442a6938cdd2682309c5ab8475b07015d6bd8a07c` |
| 새 measurement plan | H/measurement-plan-host09-02.json | `15d7d247ae09b51fcefd904f2a42559816d018250d3a9bc93066c4abebe5f448` |
| FULL expected/provenance | fpga/dense_pipeline/full-cycle-reference.txt | `de3f8d8e39bfacdca7d7ac3a53efefe2443f49a46d162e807438d5a5811723da` |
| 최종 검사·측정 도구 17개 | H/final-tools-sha256.json | `0c261340f6ae36ded762cff8ba8fcd504d9a73ff74ccd6de23446fadc5d85ae6` |

Host pin은 `7a74ac29c9c8fa8fbc5d47cf073b8b9266e3e7a9`, Gemmini include pin은 `cd2caed8d93e49d3f8873bd6c4e340fcad54b9e0`이다. 현재 dirty root나 sibling의 작업 중 파일을 컴파일에 암묵적으로 섞지 않았다. 기존 `build.py`의 FULL fixed-core patch, explicit Dense overlay, pinned host archive, host-integration patch, producer-observation patch 순서를 재사용했다. Frontend는 기존 borrowed timing view를 제공하므로 이번에는 frontend API/layout 변경이 필요하지 않았다.

실제 freeze/build는 다음 기존 recipe를 사용했다. 아래 `$H` 등은 위 절대 경로로 설정하는 실험 전용 변수다. 재현할 때 새 출력 디렉터리를 사용하며 승인 실행파일을 덮어쓰지 않는다.

```bash
python3 fpga/dense_pipeline/build.py freeze "$H/host-09-02" \
  --sim-archive "$E/regression-154/cargo/a8-w8-d16/debug/libim2p_sim.a"
python3 fpga/dense_pipeline/build.py host "$H/host-09-02"
python3 fpga/dense_pipeline/prepare_measurement.py \
  --experiment "$N" --board stream-02 --host "$H/host-09-02" \
  --out "$H/measurement-plan-host09-02.json"
```

실제 CMake argv/cwd/exit는 `H/host-09-02/host-configure.*`, `host-build-command.*`에 있다. Host/frontend를 함께 Release 빌드했고 C++20, A8/W8/D16, `FPGA_BUILD_ID=71d526…`를 사용했다. Simulator archive SHA는 `8956f8f9ee4d20295dcd072a0d1340be7b0905d8c70e440cbae636f3ecabb889`이다. 첫 host09-01 빌드도 보존했으며 최종 검증/승인 대상은 host09-02다.

Toolchain은 Linux x86_64, GCC 11.4.0, CMake 3.22.1, Python 3.11.6이다. 재사용한 RTL 모델은 Verilator 5.051 devel 빌드이며 실제 도구 출력은 `H/toolchain-host09.json`에 있다. 최종 PTY responder 수정은 host09-02 snapshot 이후의 테스트 전용 수정이므로 `final-tools-sha256.json`으로 따로 식별한다. 컴파일한 C++ source와 실행파일은 host09-02 고정 manifest 그대로다.

## 최소 변경과 FULL gate

| 파일 | 변경 |
|---|---|
| `uart.hpp`, `uart.cpp` | 고정 provenance parser, 공통 FULL gate, 기존 응답/송수신 경계의 owned telemetry |
| `telemetry.hpp` | Run의 stats/stripe view를 값으로 복사하고 retirement 뒤 출력 |
| `persistent_replay.cpp` | 모든 FULL fixture를 CAP 전에 검사, 매 FULL 기대값 설정, owned snapshot 및 정확한 validation marker |
| `ggml-gemmini-fpga.cpp` | 실제 host entry의 같은 기대값 설정, 작은 snapshot, 상세 로그를 adapter timer 뒤로 이동 |
| `host_dispatch.cpp` | 실제 reference 호출 counter와 run/benchmark validation 종류 표시 |
| `measure.py`, `prepare_measurement.py` | 고정 FULL reference를 plan에 seal하고 실행 환경으로 전달 |
| `test_stream_uart.py`, `test_measurement_guard.py` | 수명/부분실패와 실제 CLI/parent runner의 positive·negative PTY 검사 |
| `full-cycle-reference.txt` | bit/RTL/backend/protocol/profile/FULL/fixture별 기대 cycle의 고정 입력 |

이번 변경의 before/after diff는 `H/host-only-diff.patch`, 변경 파일 목록은 `H/changed-files.json`에 있다. RTL/provider/shell, wire 형식/opcode/payload, fixture와 expected, quantizer/tiler/reconstruction/residual은 수정하지 않았다.

실제 삽입점은 공통 `UART::full`의 RUN response 검증 직후다. CRC/profile/run/generation/shape/count를 먼저 검사하고, `uart.cpp:401–425`에서 FULL cycle을 판정한 **뒤에만** 기존 raw reducer callback을 호출한다. 따라서 `fence`의 성공 output commit과 RELEASE보다 앞선다. 작은 FULL 361, 긴 K64 FULL 39,907, 긴 K96 FULL 58,695를 검사한다. Prequantized FULL, quantization 포함 FULL, smoke/warm-up에 같은 경로가 적용된다. Simulator와 PIPELINE elapsed에는 적용하지 않는다.

실패 로그에는 expected/actual, fixture/mode/shape, run/generation, hardware/RTL identity, response header/CRC가 남는다. Sticky error와 nonzero exit 뒤 RELEASE/다음 RUN/BEGIN을 보내지 않는다. `UART::~UART`는 로컬 `close`만 한다. `Run::~Run`의 fence는 이미 terminal인 결과를 반환한다. Persistent destructor는 simulator handle만 정리한다. Parent runner는 실패한 child 직후 다음 조건으로 진행하지 않는다.

## 통계와 reference marker

필드별 원천·단위·endpoint·수명·availability 및 정확한 source line은 [TELEMETRY.md](../fpga/dense_pipeline/host_instrumentation/TELEMETRY.md)에 있다.

성공 fence/commit → 정상 RELEASE → 작은 owned snapshot → Run retirement → sustained timestamp → 상세 출력 순서다. Borrowed stripe view를 소멸 뒤 읽지 않는다. Timestamp 및 snapshot 수집 비용은 실제 service/sustained에 포함하고 사후 차감하지 않는다. 기존 exchange에 timestamp만 붙였으며 명령·POLL·sleep·barrier를 계측 목적으로 추가하지 않았다.

`persistent_replay`는 warm-up까지 모두 저장된 expected-raw/expected-fout와 exact 비교한다. `validation=expected_files_exact`, `cpu_reference_calls=not_instrumented`, `cpu_reference_path=not_called_source_audit`로 표시한다. 미계측 CPU reference 호출 수를 runtime 0이라고 주장하지 않는다. 실제 ggml `run`은 FPGA 결과가 나온 뒤 독립 reference를 계산하는 기존 경로이며 관측한 dot/matmul 호출 수를 출력한다. `benchmark`는 reference warm-up과 cached check 측정을 구분한다. Layer classification fallback 문자열은 numerical backend fallback 증거가 아니다.

기존 wire의 한계도 승인 판단에 포함한다. Work/fragment/A/W/C-write/C-ACK는 device reply 값이다. Publication/stripe completion은 **검증된 응답의 host tally**이고 FINISH 성공은 device의 retirement 조건을 확인한다. 독립 device publication/stripe-ACK total, 순수 compute, per-stripe first-A는 wire에 없다. Invocation의 global first-A와 그때의 published prefix만 직접 관측한다. 누락값을 0이나 예상 workload count로 채우지 않는다. Host ns와 device cycles를 직접 빼서 overlap duration을 만들지 않는다.

## 새 소프트웨어 검증

소프트웨어 회귀는 향후 승인할 FPGA 56회/simulator 54회와 별도다. 아래 결과는 새 host09-02 검사이며 이전 host08 결과를 재분류하지 않았다.

| 계층 | 결과/근거 |
|---|---|
| 기존 IFR1 UART mock | 최종 소스 10/10 PASS, `H/tests/uart-final-09.log` |
| IFR2 partial/framing/ownership mock | 14/14 PASS, 최종 snapshot 검사 포함 `H/tests/uart-agent-snapshot-final.log` |
| Measurement parser unit | 4/4 PASS |
| 두 실제 CLI + parent fail-fast PTY | 39/39 PASS, `H/tests/host09-02-guard-02/summary.json` |
| 새 persistent simulator | 54/54 invocation PASS, `H/tests/simulator-54/summary.json` |
| 실제 production RTL numerical | 15/15 logical invocation PASS, `H/validation-summary.json` |

Fail-fast PTY는 CRC까지 유효한 FULL ±1 cycle 응답을 세 shape와 prequantized/quantization 포함 실제 CLI에 공급한다. M16 cycle 362 및 warm-up 실패 후 추가 command 0, 다음 parent condition 0을 검사했다. 기존 failure 전용 CLI로 caller output sentinel도 보존했다. 누락 reference/fixture 및 backend/profile/hardware mismatch는 CAP 이전 transaction 0이다. 올바른 FULL은 통과하고, 다른 PIPELINE elapsed는 정상 계약이면 통과하며 count/identity 오류는 실패한다. Simulator M16의 실제 344 cycle에 board 361을 강제하지 않았다.

39개 검사는 mock submitted job 45회/transaction 205회 및 별도 simulator 2회를 포함한다. Mock의 expected 응답은 parser/control 검사에만 사용했으며 RTL numerical 실행으로 세지 않는다. 최초 테스트 시도는 fake responder가 정상 empty POLL을 처리하지 못해 실패했다. 그 `attempt-failure.json`과 로그를 보존하고 테스트 responder만 수정했다. Host/RTL/fixture는 바꾸거나 실패 sample을 성공 sample로 교체하지 않았다.

위 unit 표는 최종 판정에 사용한 suite다. 중간 UART/source 검사 로그도 `H/tests/uart-agent-*`에 따로 보존했다. IFR1 초기 10개는 마지막 timestamp 순서 수정 전이었으므로 최종 소스로 10개를 다시 실행해 source 기준을 맞췄다. 이 중간 실행들을 고유 테스트나 보드 GEMM 수로 합산하지 않는다.

최초 guard 시도는 33 case PASS 뒤 responder 1 case에서 중단됐다. 저장된 request 기준 mock RUN/BEGIN 35개, request transaction 76개/response 75개다. 중단 case의 semantic completion은 불명으로 남겼다. 최종 guard의 45 submissions/205 transactions와 별도이며, 두 시도 합계 mock 제출 80개도 실제 FPGA 실행이 아니다. 원본 근거는 `H/tests/host09-02-guard/attempt-counts.json`이다.

Simulator 54회는 raw 1,850,496회/f_out 741,120회/padding 46,512회 exact 비교다. Owned Run telemetry 54개, retired stripe 72개, live post-fold event 36개를 로그에서 확인했다. Live PIPELINE의 12 invocation은 고유 run identity, slot0/1/0, row160/160/1, theta −6/−3/0을 보존한다. Deterministic replay의 fixture run_id 재사용과 구분했다. `H/tests/simulator-54/telemetry-validation.json`은 기존 로그를 검증한 결과이며 새 invocation을 추가하지 않았다.

```bash
python3 fpga/dense_pipeline/test_uart.py
python3 fpga/dense_pipeline/test_stream_uart.py
python3 fpga/dense_pipeline/test_measure.py
python3 fpga/dense_pipeline/test_measurement_guard.py \
  --host-dir "$H/host-09-02/host-build" \
  --referencefile "$PWD/fpga/dense_pipeline/full-cycle-reference.txt" \
  --fixtures "$N/final-fixtures" --out "$H/tests/host09-02-guard-02" \
  --plan "$H/measurement-plan-host09-02.json"
python3 fpga/dense_pipeline/measure.py "$H/measurement-plan-host09-02.json" \
  --run simulator --out "$H/tests/simulator-54"
```

RTL 검사는 기존 `N/stream-04/production/obj_dir/Vdense_uart_shell`을 PTY에 연결했다. 실제 UART shell/provider/core가 실행하며 예상값을 반환하는 mock이 아니다. Stream-02와 고정 hardware source 61개가 같고 production RTL 차이는 기존 BSC 날짜 주석뿐이라는 보존 근거를 유지한다. 실보드 물리 pin/clock/IO 검증으로 확대하지 않는다.

완료된 새 ggml live K64/K96 로그에서 3 stripes와 owned timing을 확인했다. K64는 모든 post-fold ready가 첫 PUBLISH write 시도보다 앞섰다. K96은 첫 write 시도 후 후속 post-fold ready가 관측돼 host producer와 transfer 시작의 일부 겹침을 보인다. 이것은 device first-A의 host 시각을 측정한 값이 아니며 FPGA compute overlap의 입증으로 사용하지 않는다. 두 clock domain을 빼서 overlap 시간을 만들지 않았다. `H/tests/completed-rtl-telemetry-audit.json`은 실제 UART trace의 CRC와 최종 header 통계를 owned 출력값에 대조한 별도 사후 감사다.

| 새 production RTL 실행 | Logical jobs | Raw exact | f_out exact | Padding exact |
|---|---:|---:|---:|---:|
| 실제 ggml 작은 FULL benchmark | 2 | 512 | 512 | 0 |
| Persistent 작은 FULL | 2 | 512 | 512 | 96 |
| 실제 ggml live PIPELINE K64 | 1 | 30,816 | 15,408 | 0 |
| 실제 ggml live PIPELINE K96 | 1 | 46,224 | 15,408 | 0 |
| 실제 ggml quantization 포함 FULL K96 | 1 | 46,224 | 15,408 | 0 |
| Persistent FULL K64/K96, 각각 warm-up1+측정1 | 4 | 154,080 | 61,632 | 3,852 |
| Persistent deterministic PIPELINE, 같은 반복 | 4 | 154,080 | 61,632 | 3,852 |
| 합계 | **15** | **432,448** | **170,512** | **7,800** |

반복을 포함한 비교 횟수다. 총 stripe publication/ACK는 각각 18개이며, 모든 실행의 남은 UART output은 0이다. FULL의 실제 cycle은 작은 shape 4회 모두 361, 긴 K64 2회 모두 39,907, 긴 K96 3회 모두 58,695로 reference 차이 0이다. PIPELINE의 관측 elapsed는 K64 41,576,620/K96 56,998,528이며 FULL 기대값을 적용하지 않았다. 각각 host-wait 36,348,637/51,751,717, engine/provider overlap 6,111/10,143이다. 순수 compute counter는 미노출이다. K64 252/K96 378 fragments, 각 63 works, C write/ACK 1,926/2,889를 확인했다.

긴 persistent의 실제 FINISH/RUN header, stripe timing, owned 출력값 대조는 `cycle-telemetry-validation.json`과 `owned-telemetry-validation.json`에 있다. 정상 RTL 실행의 numerical/runtime failure, timeout, retry, unexpected reset, fallback은 모두 0이다. 의도적인 negative case 및 최초 fake responder 구현 오류 1건은 위에서 따로 보고했다. 전체 새 실제 simulator 실행은 suite 54회와 mode 구분 검사 2회이며, 향후 보드 측정용 simulator 54회와 별도다.

```bash
IM2P_FPGA_FULL_REFERENCE="$PWD/fpga/dense_pipeline/full-cycle-reference.txt" \
python3 fpga/dense_pipeline/run_pty.py \
  --rtl "$N/stream-04/production/obj_dir/Vdense_uart_shell" \
  --host "$H/host-09-02/host-build/persistent_replay" \
  --out "$H/tests/rtl-persistent-full-long" \
  uart @PTY 1 "$N/final-fixtures/m321n48k64" "$N/final-fixtures/m321n48k96"
```

각 실행의 정확한 argv/PTY/host·RTL hash/status는 각 `H/tests/rtl-*/commands.json`, `identity.json`, `status.json`에 있다. UART를 bit 단위로 시뮬레이션하므로 software PTY transaction timeout 1800초/전체 process 3600초를 사용했다. 실제 보드 계획의 transaction 30초/condition process 900초/diagnostic checkpoint 300초는 바꾸지 않았다. 소프트웨어 수치 회귀는 병렬 RTL 부하를 포함하므로 여기의 wall time과 simulator 54회 수치를 최종 보드 speedup 분모로 쓰지 않는다.

## Hardware와 보존

Host-only 변경이므로 재합성/재배치배선은 수행하지 않았다. 기존 stream-02 25 MHz route를 같은 hardware의 근거로 참조한다: LUT 47,986, FF 32,032, RAMB36 89/RAMB18 1, DSP 85, occupied slices 15,596/15,850, control sets 625, max non-clock fanout 10,723, WNS +12.517 ns, WHS +0.018 ns, pulse +3.000 ns. Route errors/black boxes/unconstrained internal endpoints 0. DRC DPIP-1 22, DPOP-1 78, DPOP-2 78 warning 및 기존 CDC 검토는 원본 report에 보존했다. 새 route 결과라고 부르지 않는다.

`H/baseline-sha256.json`은 수정 전 root 파일과 `H/baseline`의 동일 hash blob을 연결한다. 기존 host08 executable/manifest/approval, 이전 preflight 실패 증거, stream-02 bit/DCP/RTL/fixture는 원래 경로의 hash를 검사한다. 허용된 host 파일은 현재 경로가 변경됐으므로 **모든 현재 경로 불변**과 **변경 전 blob 보관**을 구분한다. 기존 FULL의 root 변경 5개도 이전 baseline의 원본 blob으로 보존한다. 최종 보존/Git 증거는 새 approval package에 둔다. 자동 staging/commit/push 및 사용자 변경 제거는 없다.

`H/preservation-final-audit.json`에서 수정 전 blob 57개, 현재 경로 49개 불변/허용 변경 8개를 확인했다. 기존 versionable manifest 61개는 현재 53개와 baseline 원본 8개로 보존했다. Old package 27개, host08 source 1,710개, hardware integration 1,693개, generated RTL/primitives 5개, fixture 98개, preflight 실패 증거 25개를 검사했다. 이전 FULL 611개 현재 경로 + 원본 blob 5개 및 보드 결과 825개도 일치한다. 중복 manifest 항목을 합쳐 고유 artifact 개수라고 부르지 않는다.

Git 기록은 `H/git-final`에 있다. IM2P branch `exp/pe-local-partial-int20`, HEAD `dc4a1a621f63834d64df42ae8c24152747d97971`이다. 기존 tracked dirty 20개(초기 17개와 이전 frontend 3개)의 binary diff는 이번 작업 시작과 byte-for-byte 동일하다. 이번 host 소스는 기존 untracked `fpga/` 아래에 남겼다. Host sibling은 `develop`/위 고정 pin이며 untracked `models/gpt2/`, `models/llama3.2-1B/`를 보존했다. Include sibling은 clean이다. 세 저장소의 `git diff --check`는 exit 0, cached diff는 모두 비어 있다. 실제 모델 실행/capture 검증을 뜻하지 않는다.

## 새 보드 승인 범위

새 package는 `fpga/dense_pipeline/host_instrumentation/approval.json` 및 `H/approval-package`에 별도로 둔다. 기존 `fpga/dense_pipeline/approval.json`을 덮어쓰지 않는다.

승인 후 계획은 동일 hardware의 SRAM programming 1회, 실제 ggml 작은 FULL smoke 1회, 준비 지연 진단 live PIPELINE 1회, 무지연 persistent sweep 54회다. FPGA 총 56회, 이후 부하 없이 순차 matched simulator 54회다. 긴 shape는 M321/N48/K64 및 K96, 자동 stripe160/160/1, slot0/1/0이며 workload/fixture 수량을 바꾸지 않는다. 예상 반복 비교는 FPGA raw 1,881,568회, logical f_out 756,784회, padding 46,512회다.

순수 compute/per-stripe first-A/독립 publication·stripe-ACK total이 미노출이라는 위 제한을 포함해 새 승인을 요청한다. 새 실행파일/plan 승인 전에는 UART open/CAP도 실행하지 않는다. 첫 hash/CRC/profile/identity/count/numerical/timeout/진행 오류에서 중단하고 RELEASE/ABORT/reset/retry/reprogramming으로 복구하지 않는다. Flash/non-volatile write는 금지다.

현재 상태: **Implemented** host 검사·계측, **Simulated** 완료된 소프트웨어 검사, **Routed** 기존 동일 hardware 근거, **Board measured 0**, 새 보드 성능/자연 overlap **NOT RUN**, **Awaiting approval**. 최종 주장 범위는 A8/W8/DIM16/native Q8_H1/EXSIA/RMD OFF Dense FULL/live PIPELINE에 한정한다.

새 host09의 지연 주입 진단은 이번 소프트웨어 회귀에서 NOT RUN이다. 이전 host08의 진단 결과를 새 결과로 재분류하지 않았다. 신규 승인 후 계획에 포함된 1회 진단에서 다시 확인한다. 같은 이유로 hardware가 불변인 기존 154개/core assertion 결과는 이전 근거로만 참조하며 이번 host 회귀 횟수에 넣지 않았다.
