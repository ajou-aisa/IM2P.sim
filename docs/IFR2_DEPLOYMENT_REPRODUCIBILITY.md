# IFR2 구현의 Git 정리와 Nano 배포 재현성 검증

검증된 stream-02 하드웨어와 host09-02 수치·ownership 계약을 보존하면서, 고정 source와 patch에서 simulator archive와 host를 새로 빌드하는 경로를 정리했다. 이번 작업의 보드 JTAG/UART/CAP/programming/GEMM, flash write, Vivado synthesis/place/route 및 새 bitstream 생성은 모두 **0회**다. Nano native build와 Nano 실행은 **NOT RUN**이다. 이동 후 절차는 [JETSON_NANO_QUICKSTART.md](JETSON_NANO_QUICKSTART.md)를 따른다.

지원 범위는 A8/W8, physical DIM16, nominal 25 MHz, IFR2, native Q8_H1/block32, EXSIA activation, RMD OFF Dense FULL/live PIPELINE이다. 이전 [host09 보드 측정](FPGA_DENSE_PIPELINE_HOST09_BOARD_MEASUREMENT.md)의 FPGA 56/56, matched simulator 54/54와 이번 소프트웨어 재현 검사는 별도 결과다. 전체 모델, residual-enabled 실행 또는 TTFT/TPOT를 검증한 것으로 해석하지 않는다.

## Commit과 원본 상태

| Commit | 내용 |
|---|---|
| `80d09c4fe2dd02e84e8f19e8ab34bb65b398912c` | 기존 INT20-local/core/frontend 변경, 검증된 IFR1/IFR2 source·fixture·tests, 명시적 native build, Nano 안내를 선별 커밋 |
| `832b1ae532cc3c12bc034978f64b6a9904f852c6` | Portable plan 테스트의 디렉터리 이름에 대한 잘못된 가정만 수정 |

목적지는 `origin`, `refs/heads/exp/pe-local-partial-int20`이다. 위 두 commit은 이 보고서의 build/test 기준이며 최종 push 또는 remote SHA 확인을 대신하지 않는다. 최종 배포 commit은 push 확인 후 별도 배포 `hardware-manifest.json`에 기록한다. 문서가 자기 자신의 commit/hash를 포함하도록 반복 수정하지 않는다.

원본 상태·binary diff·stage 목록·실행 로그의 보존 디렉터리는 다음과 같다. 이 경로는 **이번 검증의 증거 위치**이며 다른 checkout/Nano의 필수 build 입력이 아니다.

```text
O = build/experiments/ifr2-nano-deploy-20260910T040724Z
C = O/clean-checkout
S = C/build/native-test
```

`O/baseline`, `selected-stage.json`, `staged-stat.txt`, `staged-diff.txt`에 원본과 선별 근거를 남겼다. Model/GGUF, x86 executable/archive, 대형 generated RTL, 전체 Vivado output과 반복 로그를 source commit에 포함하지 않았다. Host/include는 고정 pin과 versioned patch로 재현하며 원본 sibling checkout에 새 host commit을 만들지 않았다.

## Source 선택과 의존성

[deployment-lock.json](../fpga/dense_pipeline/deployment-lock.json)의 `verified_source_sha256` **96개**를 host09-02 frozen source와 직접 비교했다. 모두 같은 SHA다. [precommit-source-comparison.json](../build/experiments/ifr2-nano-deploy-20260910T040724Z/precommit-source-comparison.json)에 root/frozen 비교도 함께 기록했다.

| 입력 | 고정 기준 |
|---|---|
| Core base | `dc4a1a621f63834d64df42ae8c24152747d97971` |
| FULL fixed-core patch | `fpga/full_replay/fixed-core.patch` |
| Host | `ajou-aisa/llama.cpp-gemmini`, `7a74ac29c9c8fa8fbc5d47cf073b8b9266e3e7a9` |
| Gemmini include | `ajou-aisa/RISC-V-DynDNN-gemmini-include`, `cd2caed8d93e49d3f8873bd6c4e340fcad54b9e0` |
| Host patch 순서 | `host-integration.patch`, `producer-observation.patch` |
| Profile | A8/W8/D16, block32, IFR2/version2/profile0x0810, EXSIA, RMD OFF |

기존 FULL freeze는 `baseline.json.head`의 Git archive에 fixed-core patch를 한 번 적용하고 frozen hash를 검사한다. 새 HEAD의 root 전체를 archive하지 않는다. 이어 명시적인 FULL/frontend overlay, Dense overlay, pinned host archive와 두 patch, pinned include archive를 적용한다. 새 `build.py`는 FULL base와 deployment lock의 일치도 검사한다.

Root Arithmetic의 표현과 board A1 표현, MatmulScheduler/WorkScheduler의 차이는 patch에 남긴다. Root를 frozen RTL로 덮어쓰지 않았다. Config/generator, activation-response capacity guard, 최종 frontend, UART FULL fail-fast와 owned telemetry의 검증 source는 유지했다. 기존 simulator 원천의 `src/synth/sim` 115파일도 host09 frozen과 동일하므로 simulator 수치 구현을 새로 만들 필요가 없었다.

새 dependency checkout은 명시적인 원격과 pin에서 취득했다. Clean freeze는 `--host-repo`와 `--params-repo`로 이 경로를 전달한다. 원본 working tree 또는 과거 `host-full-replay` experiment의 archive를 build 입력으로 사용하지 않는다. `prepare_measurement.py --portable`는 새 snapshot, 새 archive/host, committed fixture, FULL reference와 동일 bitstream을 seal한다. 과거 616파일/825로그/DCP 검사는 기존 역사적 plan 모드에만 남는다.

## Clean checkout의 실제 빌드

`git clone --no-hardlinks --branch exp/pe-local-partial-int20`으로 별도 checkout을 만들었다. 최초 snapshot은 구현 commit `80d09c4…`에서 생성했다. 그 뒤 `832b1ae…`로 이동한 변경은 테스트 파일 하나이며, snapshot의 build/host/numerical source를 바꾸지 않았다.

다음은 원본 command JSON의 경로를 `O/C/S`로 줄인 실제 실행 순서다. 각 변수는 해당 디렉터리의 절대 경로로 설정한다.

```bash
cd "$C"
python3 fpga/dense_pipeline/build.py freeze "$S" \
  --host-repo "$O/dependencies/host" \
  --params-repo "$O/dependencies/params"
python3 fpga/dense_pipeline/build.py native-sim "$S" --jobs 2
python3 fpga/dense_pipeline/build.py host "$S" --jobs 2
```

`native-sim`은 `S/source`에서 기존 `make verilator-a8-w8-d16 BUILD_DIR=S/native-x86_64`를 실행했다. 이후 A8/W8/D16, 새 `CARGO_TARGET_DIR`, `CARGO_PROFILE_DEV_DEBUG=0`, `CARGO_PROFILE_TEST_DEBUG=0`, `CARGO_INCREMENTAL=0`, jobs2로 `cargo build --locked --manifest-path sim/Cargo.toml --lib`를 실행했다. 기존 x86 archive를 복사하지 않았다. Host는 새 archive를 링크하고 frontend와 함께 Release/C++20으로 빌드했다.

Make/BSC/Verilator 생성, Cargo archive, CMake configure, 두 host target build가 모두 exit0으로 완료됐다. 실제 argv/cwd/exit는 `S/native-x86_64/{generate,archive-build}.*`, `S/host-configure.*`, `S/host-build-command.*`에 있다. ELF member의 x86_64 architecture도 확인했다. 이 검사는 일반적인 archive의 선택 member 확인이며 모든 mixed-architecture archive에 대한 독립 감사라는 뜻은 아니다.

[build-boundary-audit.json](../build/experiments/ifr2-nano-deploy-20260910T040724Z/build-boundary-audit.json)은 lock96개 일치, host compile command79개의 source가 모두 새 snapshot 안에 있음, 과거 experiment의 execution 입력0개, 새 archive의 snapshot 내부 위치, 두 executable의 ELF x86_64를 확인했다. 원본 experiment 경로는 역사 비교를 읽을 때만 사용했다.

| 새 x86 artifact | SHA256 |
|---|---|
| Native `libim2p_sim.a` | `7962632db68bf114e541fe3134933e76b6848376487e4f931c793b7dc3cc6389` |
| `dense_host_dispatch` | `af961a115afad30e1d3409aab7d96a054f1e328f94892daadf289a5da65ff66b` |
| `persistent_replay` | `6813c9d52ab5d509a0199762cfd44de11a167c856ccb5a5b422a2fef40956d40` |
| Integration manifest | `3e2aa0a112843b69f454eec26957ac4c6a13a66716ed0c551c0556ab6a40b319` |

이 파일들은 역사적 host09-02 executable과 다른 새 재현 build다. Snapshot 경로, 추가된 recipe/test 입력, integration build ID, 새 native archive와 tool metadata가 달라졌으므로 전체 binary hash 동일성을 주장하지 않는다. 대신 검증 수치 source 96개, profile/ABI, 실제 수치 replay를 각각 확인한다. 새 simulator `mkSynthA8W8D16.v`는 이전 simulator RTL과 raw SHA가 다르지만 [원본 diff](../build/experiments/ifr2-nano-deploy-20260910T040724Z/native-rtl-raw-diff.txt)는 **BSC 생성 날짜 주석 한 줄**뿐이다. 이를 새 board bitstream 생성 또는 board RTL 변경으로 분류하지 않는다.

실제 도구는 BSC 2026.01/build9bd39e6f, Verilator 5.051 devel, GCC 11.4.0, Rust/Cargo 1.91.1, CMake 3.22.1, Python 3.11.6이다. 전체 경로·버전은 `S/identity.json`의 `native_toolchain`에 있다. 이 값은 현재 x86 검증 결과이며 Nano의 설치 상태를 뜻하지 않는다.

## 장치 없는 검사 결과

| 검사 | 결과 | 증거 |
|---|---|---|
| Deployment architecture/source guard | 5/5 PASS | `O/clean-tests-02/deployment.*` |
| Portable plan seal/relocation | 5/5 PASS | `O/clean-tests-02/portable-plan.*` |
| Measurement parser | 4/4 PASS | `O/clean-tests-02/measure.*` |
| Static check regression | PASS marker | `O/clean-tests-03/static-check.*` |
| UART PTY parser | 10/10 PASS | `O/clean-tests-03/uart.*` |
| IFR2 ownership/telemetry PTY | 14/14 PASS | `O/clean-tests-03/stream-uart.*` |
| 두 새 host CLI와 parent fail-fast | 39/39 PASS | `O/clean-tests-04/guard/summary.json` |
| Native simulator numerical replay | 18/18 invocation PASS | `O/clean-numerical/summary.json` |
| ARM64/Nano native build·실행 | NOT RUN | 보드 이동 후 별도 진행 |
| 이번 FPGA programming/GEMM | NOT RUN, 0회 | 장치 없는 검증만 수행 |

대표 검사 명령은 다음과 같다. 명령마다 새로운 출력 디렉터리를 사용했다.

```bash
python3 -m unittest discover -s fpga/dense_pipeline -p test_deployment.py
python3 -m unittest discover -s fpga/dense_pipeline -p test_portable_plan.py
python3 -m unittest discover -s fpga/dense_pipeline -p test_measure.py
python3 tests/test_static_check.py
python3 fpga/dense_pipeline/test_uart.py --out "$O/clean-tests-03/uart"
python3 fpga/dense_pipeline/test_stream_uart.py --out "$O/clean-tests-03/stream-uart"
python3 fpga/dense_pipeline/test_measurement_guard.py \
  --host-dir "$S/host-build" \
  --referencefile "$C/fpga/dense_pipeline/full-cycle-reference.txt" \
  --fixtures "$C/fpga/dense_pipeline/fixtures" \
  --out "$O/clean-tests-04/guard" --plan "$O/portable-plan.json"
```

39개 guard case는 mock logical submission45회/transaction205회와 별도 simulator invocation2회를 포함한다. 유효 CRC 응답의 FULL cycle ±1, warm-up 실패, 미등록 provenance, PIPELINE count/identity를 검사했다. 실패 뒤 추가 장치 command는0이며 parent는 다음 조건을 실행하지 않았다. 정상 PIPELINE elapsed에는 FULL expected를 강제하지 않았다. Owned telemetry는 Run retirement 뒤에도 검증했다. 이 mock 출력은 parser/control 근거이며 실제 FPGA numerical 결과가 아니다.

별도의 native simulator 검사18/18회에서는 raw signed32 **616,832회**, f_out float32 **247,040회**, padding **15,504회** exact 비교를 통과했다. Prequantized FULL은 작은/긴 K64/긴 K96 각각 warm-up1+검사1, 총6회다. Deterministic PIPELINE, quantization 포함 FULL, live PIPELINE은 긴 K64/K96 각각 같은2회씩으로 조건별4회다. 모두 합해18회이며 guard의 simulator2회를 더한 이번 실제 simulator invocation은20회다. 비교 횟수는 반복을 포함하며 고유 출력 수가 아니다.

```bash
F="$C/fpga/dense_pipeline/fixtures"
"$S/host-build/persistent_replay" simulator - 1 \
  "$F/m16n16k32" "$F/m321n48k64" "$F/m321n48k96"
"$S/host-build/persistent_replay" simulator-pipeline - 1 \
  "$F/m321n48k64" "$F/m321n48k96"
"$S/host-build/persistent_replay" simulator-live - 1 \
  "$F/m321n48k64" "$F/m321n48k96"
"$S/host-build/persistent_replay" simulator-live-pipeline - 1 \
  "$F/m321n48k64" "$F/m321n48k96"
```

FULL의 simulator R1 cycle은 작은344, 긴 K64 39,252, 긴 K96 58,040이다. Board-provider reference361/39,907/58,695를 simulator에 강제하지 않았다. Deterministic PIPELINE은 K64 39,831/K96 58,655, live PIPELINE은 K64 39,831 또는39,832/K96 58,655 또는58,656을 관측했다. 이 값은 이번 correctness 검사의 실제 counter이며 physical elapsed 기대값이나 성능 개선 주장이 아니다. 기존 54회 성능 sweep과 FPGA numerical/154개 core 회귀를 재실행한 것으로 보고하지 않는다.

실패 시도도 보존했다. 최초 portable unit은 출력 경로에 `experiments` 문자열이 있다는 이유만으로 실패했다. 경로의 실제 소유 범위를 검사하도록 테스트만 수정한 commit이 `832b1ae…`다. 최초 static regression은 기존 테스트가 기대하는 sibling host 경로가 없어 실패했다. `O/dependencies/host`의 명시된 pin을 `git archive`한 별도 입력을 그 경로에 제공한 뒤 통과했다(`O/static-test-host-input.json`). 이 입력은 legacy static test에만 필요하며 portable snapshot/native build의 숨은 의존성은 아니다. 최초 unittest discovery의 0개 발견도 검증 PASS 또는 테스트 횟수로 집계하지 않았다.

## 동일 bitstream 배포 사본

원본 stream-02 `.bit`를 새 배포 디렉터리에 bytes 그대로 복사했다. 크기는 **3,825,915 bytes**, SHA256은 다음과 같다.

```text
8aef393d040bb306e6ddf7b4b977976a9a924dbc5aec62b37b1690f0aa18c0ca
```

실제 사본 경로:

```text
/home/youngshin/aisa/RISCV-DynDNN/IM2P/IM2P.sim/build/experiments/ifr2-nano-deploy-20260910T040724Z/distribution/dense-pipeline.bit
```

[bitstream-copy.json](../build/experiments/ifr2-nano-deploy-20260910T040724Z/bitstream-copy.json)에 원본·사본·크기·전체 hash 비교를 기록했다. 구현 part는 `xc7a100tcsg324-1`, nominal25MHz, UART1,000,000baud/8N1, CAP version2/profile0x0810/capacity336×48×96이다. DCP SHA `6141a245e4c2ca88b8147ae864974abc14c3b858a276c2a5f5beee42f5a92bd4`와 production `mkDensePipeline.v` SHA `b29147ce384e9384b3821c358863350f03c0696cbc8b8e286a768f2e7155b853`는 기존 하드웨어 provenance다.

Git source와 `.bit` 전달은 별개다. 최종 source commit을 기록한 `hardware-manifest.json`, `SHA256SUMS`, `README`를 같은 distribution 디렉터리에 작성하여 함께 전달한다. 전체 DCP, x86 실행 파일, 과거 측정 로그를 Nano 첫 실행의 필수 payload로 묶지 않는다. 이번 작업에서 Nano 전송/SSH 또는 openFPGALoader detect/programming을 실행하지 않는다.

## 보존과 whitespace 판정

[preservation-after.json](../build/experiments/ifr2-nano-deploy-20260910T040724Z/preservation-after.json)의 기존 artifact8,065개 중 현재 경로8,060개는 동일하다. 허용된 packaging 변경5개는 이전 blob을 같은 hash로 보존했다. 별도 root source156개는 현재 경로150개 동일, 허용 변경6개이며, 변경 전 blob156/156도 동일하다. 두 집합은 겹칠 수 있으므로 합쳐 고유 보존 파일 수로 표현하지 않는다. “모든 경로가 불변”과 “변경 전 bytes가 보존됨”을 구분한다.

최초 staged `git diff --cached --check`는 exit2, 진단75개였다. 그중74개는 보존한 세 patch 파일의 필수 빈 context line이며, 나머지1개는 검증된 `fpga/full_replay/uart.sv`의 EOF 빈 줄이다. Patch 적용과 하드웨어 bytes를 유지하려고 이 파일들을 정규화하지 않았다. 나머지 경로의 check는 exit0이다. 이 예외와 원본 출력은 `O/staged-check-classification.json`, `O/staged-check.txt`에 남겼다. 전체 staged check를 무조건 PASS라고 보고하지 않는다.

Nano에서는 source와 host/frontend/simulator/runtime을 ARM64로 다시 빌드해야 한다. ARM64 hash, 실제 PMU validity, SRAM 로딩과 첫 FULL/live PIPELINE 결과는 아직 없다. 사용자가 보드 이동 완료를 알리기 전에는 이 단계로 자동 진행하지 않는다.
