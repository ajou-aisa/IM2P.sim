# SCU H1 Flash/Nano preparation package

**준비 산출물이다. Flash 쓰기·보드 실행 승인이 아니다.** 원본 SCU bitstream은 SRAM 실보드5회 검증됐지만 SPI cold boot·Nano 실행은 아직 미검증이다. Flash 마킹/PCB revision·Nano 모델/OS가 확인되지 않아 boot-only bit/MCS와 ARM 실행 파일은 없다.

상세 보고서는 `docs/SCU_NANO_MIGRATION_PREPARATION.md`, 단계별 승인 요청은 `plans/approval-requests.json`, Flash 출처/명령 템플릿은 `fpga/scu_migration/flash_reference.md`다. `manifests/original/`의 절대경로는 역사적 근거이며 새 실행 입력 경로가 아니다.

## 전달·검증

전달할 파일은 `scu-h1-migration.tar.gz`, `unpack.py`, `SHA256SUMS`, `transfer.json`이다. 원래 Git source와 별도로 고정 bitstream이 archive 안에 포함된다. 이미 승인된 목적지나 USB로만 옮긴다. SSH 주소를 추측하거나 `rsync --delete`를 사용하지 않는다.

```sh
sha256sum -c SHA256SUMS
# TARGET_PARENT는 새로 만들 추출 디렉터리. 기존 파일이 있으면 거부한다.
python3 unpack.py extract scu-h1-migration.tar.gz "$TARGET_PARENT"
PKG="$TARGET_PARENT/migration-package"
python3 "$PKG/fpga/scu_migration/package.py" verify "$PKG"
```

Archive SHA와 내부 파일 manifest는 서로 다른 검증이다. Manifest는1746개의 고정 source와 원본 bit/DCP/RTL identity를 별도로 검사한다. Package 밖 symlink·device·traversal은 거부한다. 추출 뒤 파일을 편집하지 말고 build/검사 결과는 별도 RUN에 둔다.

## Nano 환경부터 확인 — 장치 거래 없음

Nano에서 사용자가 실행하거나 접속을 별도 허용한 뒤 수행한다. 지금은 원격 접속을 하지 않는다.

```sh
python3 "$PKG/fpga/scu_migration/probe.py" > nano-probe.json
python3 "$PKG/fpga/scu_migration/probe.py" \
  --compile-probe --cxx /usr/bin/g++ --out "$PROBE_RUN" --run-native \
  > nano-cxx20.json
```

`PROBE_RUN`은 새 디렉터리다. 실제 g++ 경로가 다르면 probe에서 확인한 경로를 사용한다. Probe는 USB sysfs·by-id·stat/access만 읽으며 UART/JTAG를 열지 않는다. Exact Nano 모델/OS/L4T/kernel/glibc/RAM/swap/disk/tool versions를 확인한다. missing은 unavailable이며 OS upgrade나 설치를 자동 실행하지 않는다.

Python build wrapper는3.10+, 실제 C++20 library 기능이 필요하다. CMake source minimum3.16. 현재 x86 검증 도구는 GCC11.4/CMake3.22.1/Python3.11.6/Rust1.91.1/BSC2026.01/Verilator5.051(mod)이다. Nano 요구 버전이 이미 충족됐다는 뜻은 아니다. BSC/Verilator가 없으면 native generation은 NOT RUN이며 검증되지 않은 x86 archive로 대체하지 않는다. Vivado는 Nano에 설치하지 않는다.

## ARM64 native rebuild

`uname -m`이 `aarch64`인지 먼저 확인한다. `NATIVE_RUN`은 package 밖의 새 경로다. 공간은 최소 source 약90MB, vendored crates, BSC/Verilator/Cargo/CMake build peak를 별도로 고려한다. 실제 Nano 여유 RAM/disk가 확인되지 않았으므로 성공이나 소요 시간을 예측하지 않는다.

```sh
python3 "$PKG/fpga/scu_migration/native_build.py" \
  --package "$PKG" --run "$NATIVE_RUN" --jobs 1
# 위는 dry-run. 도구/자원 확인 후 native build를 명시적으로 실행한다.
python3 "$PKG/fpga/scu_migration/native_build.py" \
  --package "$PKG" --run "$NATIVE_RUN" --jobs 1 --build
```

고정1746source만 복사하며 x86 archive 경로를 새 identity에 선택하지 않는다. 내부 순서는 기존 `build.py native`, `host --jobs1`, `host-fpga --jobs1`, `verify`다. Native cache Cargo compile은 기존 jobs1 정책이고 host CMake parallelism은 `--jobs`로 조절한다. 새 TMP/cache/Cargo/CMake output은 NATIVE_RUN 아래다. Cargo source3개는 package vendor와 lockfile 검증을 통해 offline 사용한다. Cross 환경 변수는 현재 recipe에서 지원하지 않아 거부한다.

Build 종료 후 `native-build-manifest.json`은 source·target archive·executable·ELF architecture를 기록한다. ARM hash가 x86 hash와 다른 것은 별도 target artifact다. 기존 x86 executable을 ARM에서 실행하거나 `.a`를 target 링크에 사용하지 않는다.

## ARM 비장치 검사

새 build manifest를 검토하고 그 전체 SHA를 `NATIVE_MANIFEST_SHA`에 명시한다. `TEST_RUN`도 새 경로다.

```sh
python3 "$PKG/fpga/scu_migration/native_software_tests.py" \
  --native-run "$NATIVE_RUN" --out "$TEST_RUN" \
  --manifest-sha256 "$NATIVE_MANIFEST_SHA"
python3 "$PKG/fpga/scu_migration/native_software_tests.py" \
  --native-run "$NATIVE_RUN" --out "$TEST_RUN" \
  --manifest-sha256 "$NATIVE_MANIFEST_SHA" --run-tests
```

PTY20, FULL gate21, unsupported2, scalar22+carrier48 및 독립 G1/G2 검사다. 실제 aarch64 외 `--run-tests`를 거부한다. Mock response PASS는 보드 PASS가 아니다. 원본 FULL cycle617/52687/1949를 R1 simulator에 강제하지 않는다. 첫 실패에서 로그를 남기고 다음 시험을 실행하지 않는다.

Review-fix의 격리 mock/mutation 검사도 별도로 실행할 수 있다. 아래 TEST 경로들은 각각 새 경로다. Mutation의 의도된 실패는 검사기가 PASS 조건으로 구분하며 production source를 바꾸지 않는다.

```sh
python3 "$PKG/fpga/scu_block_scale/test_output_extent.py" \
  --snapshot "$NATIVE_RUN" --out "$EXTENT_TEST_RUN"
python3 "$PKG/fpga/scu_block_scale/test_first_a_ordering.py" \
  --snapshot "$NATIVE_RUN" --out "$FIRST_A_TEST_RUN"
```

새 cross-platform fixture도 비교한다. 이미 제공된 x86 fixture는 실제 quantizer 출력이며 과거 보드 wire capture가 아니다.

```sh
python3 "$PKG/fpga/scu_migration/portable_fixture.py" build-exporter \
  --snapshot "$NATIVE_RUN" --out "$EXPORT_BUILD" --cxx /usr/bin/c++
python3 "$PKG/fpga/scu_migration/portable_fixture.py" capture \
  --export-build "$EXPORT_BUILD" --out "$ARM_FIXTURE"
python3 "$PKG/fpga/scu_migration/portable_fixture.py" compare \
  "$PKG/fixtures/portable-x86" "$ARM_FIXTURE"
```

새 FP32입력/A/W/H1 metadata/G1/G2/wire17파일을 exact 비교한다. 입력 불일치→quantizer→packing→integer→FP 순서로 진단하며 epsilon/expected를 변경하지 않는다. 계측 불가 CPU PMU는 unavailable이다.

## Flash 준비

`plans/flash-image-template.json`은 Unknown이 남은 거부용 template다. Actual part/interface/geometry/boot settings와 payload mapping을 확인한 뒤 **새 plan**을 작성한다. 원본은 덮어쓰지 않는다.

```sh
python3 "$PKG/fpga/scu_migration/flash_prepare.py" --plan "$CONFIRMED_IMAGE_PLAN"
# 확정 plan만 offline Vivado 변환 가능. Flash를 쓰지 않는다.
python3 "$PKG/fpga/scu_migration/flash_prepare.py" \
  --plan "$CONFIRMED_IMAGE_PLAN" --generate "$NEW_IMAGE_OUTPUT"
python3 "$PKG/fpga/scu_migration/flash_prepare.py" \
  --plan "$CONFIRMED_IMAGE_PLAN" --check-mcs "$NEW_IMAGE_OUTPUT/candidate.mcs"
```

기본 check-plan, `--execute`는 항상 거부한다. 실제 F1/F2 executor는 미준비다. F1은 indirect helper가 SRAM을 바꾸는 범위, NV register 영향, backup·part·geometry를 별도로 승인받아야 한다. F2는 sector-limited erase/program/verify와 untouched byte 보존을 확정해야 한다. F3는 모든 실제 전원을 사람이 끊었다가 재인가하며 JTAG 재programming 없이 SCU 부팅을 검사한다. 기존 SRAM 승인 runner를 Flash helper 흐름에 재사용하지 않는다.

## 별도 승인 뒤 첫 Nano5 명령

**다음 블록은 준비용 명령 목록이다. 지금 실행하지 않는다.** 아직 새 ARM host hash·boot image·Nano serial identity가 없어 고정 실행 계획을 승인할 수 없다. 후속 승인 시 이 조합을 봉인하고 각 process exit/전체 numerical marker를 확인한 뒤 다음 process로 진행한다. 정상 RELEASE는 포함되며 오류 후 retry/reset/ABORT/복구 RELEASE는 없다.

환경: 명시적 Nano by-id 장치, transaction timeout30초, process timeout900초, package의 full-cycle-reference와 확인한 original/derived hardware 및 production RTL SHA를 설정한다. `IM2P_FPGA_ALLOW_UNPINNED_RTL_TEST`와 모든 진단 지연 변수를 unset한다. Constructor가 CAP를 보내므로 실행 전에 승인이 필요하다.

```text
$NATIVE_RUN/fpga-host-build/scu_host_dispatch run 16 16 64 1 1 FULL
$NATIVE_RUN/fpga-host-build/scu_host_dispatch run 321 48 96 3 1 FULL
$NATIVE_RUN/fpga-host-build/scu_host_dispatch run 321 48 64 2 1 STRIPE_PIPELINE
$NATIVE_RUN/fpga-host-build/scu_host_dispatch run 17 19 64 4 2 FULL
```

합계5GEMM, warm-up/반복 sweep 없음. Final integer/f_out31,718씩, wire padding442. FULL cycles617/52687/1949. PIPELINE stripe160/160/1 slot0/1/0; elapsed equality 없음. CAP는 Flash/SRAM 전체 readback을 증명하지 않는다. Flash·cold boot·Nano correctness 완료는 아직 NOT RUN이다.
