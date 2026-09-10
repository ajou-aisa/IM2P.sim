# 검증된 IFR2 구현의 Jetson Nano 재현 안내

이 문서는 검증된 stream-02 bitstream과 host09-02 소스를 Nano로 옮기는 절차다. 지원 범위는 **A8/W8, physical DIM16, native Q8_H1/block32, EXSIA activation, RMD OFF Dense FULL/live PIPELINE**이다. 현재 Nano native build, Nano programming, Nano UART 실행은 **NOT RUN**이다. 기존 x86 실보드 결과는 [host09 측정 보고서](FPGA_DENSE_PIPELINE_HOST09_BOARD_MEASUREMENT.md)에 보존한다.

아래 Nano 명령은 사용자가 보드 이동 완료를 알린 뒤 실행 범위를 확정하고 사용한다. 이 문서를 작성하는 작업은 Nano 접속·설치·빌드·장치 실행을 시작하지 않는다. 이동 중 전원을 끊어도 된다. SRAM 구성은 휘발성이므로 Nano에서 동일 bitstream을 다시 로딩한다. Flash에는 쓰지 않는다.

## 1. Git 소스와 별도 bitstream 받기

Git에는 source, patch, tests, 작은 fixture와 expected 결과가 들어 있다. `.bit`, DCP, x86 실행 파일, x86 simulator archive는 Git으로 배포하지 않는다. 별도로 준비한 디렉터리의 다음 파일도 USB 또는 사용자가 정한 전송 방식으로 옮긴다.

- `dense-pipeline.bit`
- `SHA256SUMS`
- `hardware-manifest.json`
- `README`

Nano 주소나 사용자명은 여기서 가정하지 않는다. 전송 명령이 필요하면 `scp -r "$DEPLOY_DIR" "$NANO_SSH_TARGET:$NANO_DEST"`의 두 대상을 실제 값으로 정한 뒤 사용한다. 이번 준비 작업에서는 전송·SSH를 실행하지 않는다.

받은 배포 디렉터리를 `DEPLOY_DIR`, 새 소스 상위 디렉터리를 `NANO_WORK`로 지정한다. 기존 checkout과 model 디렉터리는 재사용하거나 정리하지 않는다.

```sh
set -eu
: "${DEPLOY_DIR:?받은 배포 디렉터리의 절대 경로를 지정하세요}"
: "${NANO_WORK:?새 checkout과 build를 둘 상위 디렉터리를 지정하세요}"
BIT="$DEPLOY_DIR/dense-pipeline.bit"
DEPLOY_COMMIT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_commit"])' \
  "$DEPLOY_DIR/hardware-manifest.json")
mkdir -p "$NANO_WORK"
git clone --branch exp/pe-local-partial-int20 \
  https://github.com/ajou-aisa/IM2P.sim.git "$NANO_WORK/IM2P.sim"
SRC="$NANO_WORK/IM2P.sim"
git -C "$SRC" checkout --detach "$DEPLOY_COMMIT"
test "$(git -C "$SRC" rev-parse HEAD)" = "$DEPLOY_COMMIT"
git -C "$SRC" status --short
```

실제 배포 commit은 Git 안의 문서가 자기 자신을 가리키는 대신, commit 생성 후 만든 별도 `hardware-manifest.json`의 `source_commit`에 기록한다. 이 값이 remote에서 취득되지 않으면 중단한다. branch 최신 HEAD를 대신 사용하지 않는다.

## 2. 고정 host/include 의존성 받기

[deployment-lock.json](../fpga/dense_pipeline/deployment-lock.json)이 pin과 검증 source hash의 기준이다. 새 디렉터리에 clone한다. 원본 host/include checkout에 patch를 적용하지 않는다.

```sh
DEPS="$NANO_WORK/deps-ifr2"
mkdir "$DEPS"
git clone https://github.com/ajou-aisa/llama.cpp-gemmini.git "$DEPS/llama.cpp-gemmini"
git -C "$DEPS/llama.cpp-gemmini" checkout --detach \
  7a74ac29c9c8fa8fbc5d47cf073b8b9266e3e7a9
git clone https://github.com/ajou-aisa/RISC-V-DynDNN-gemmini-include.git "$DEPS/gemmini-include"
git -C "$DEPS/gemmini-include" checkout --detach \
  cd2caed8d93e49d3f8873bd6c4e340fcad54b9e0
git -C "$SRC" cat-file -e dc4a1a621f63834d64df42ae8c24152747d97971^{commit}
```

Core base는 `dc4a1a621f63834d64df42ae8c24152747d97971`이다. 배포 commit, core base, host pin, include pin은 서로 다른 역할이다. Freeze는 새 HEAD의 root RTL 전체를 board source라고 취급하지 않는다. 순서는 다음과 같다.

1. 고정 core base의 Git archive.
2. FULL `fixed-core.patch`와 명시된 frontend/provider/shell 입력.
3. Dense provider/adapter/host09-02 입력 overlay.
4. 고정 llama.cpp archive에 `host-integration.patch`, `producer-observation.patch`를 각각 한 번 적용.
5. 고정 Gemmini include archive.
6. Lock에 기록된 frozen numerical/host source hash 검사.

이 조합은 root INT20-local 변경과 board A1 arithmetic, MatmulScheduler/WorkScheduler의 차이를 보존한다. Activation-response capacity guard, host FULL fail-fast, owned telemetry도 고정 source에 포함된다. Root를 frozen RTL로 덮어쓰거나 `git archive HEAD`에 patch를 중복 적용하지 않는다.

## 3. Nano architecture와 toolchain 확인

Nano의 OS 또는 설치 버전을 추정하지 않는다. 보드를 열지 않고 다음 출력을 저장한다.

```sh
uname -m
getconf LONG_BIT
cat /etc/os-release
gcc --version
g++ --version
cmake --version
python3 --version
rustc -Vv
cargo --version
bsc -v
verilator --version
```

Native ARM64 절차는 `aarch64`와 64-bit userspace를 전제로 한다. Recipe에는 Python 3.11 이상, C++20 compiler, CMake 3.16 이상, Make, Git, patch, ar, Rust/Cargo, BSC, Verilator가 필요하다. Pinned llama/ggml의 CMake 최소값은 3.14이고 이 adapter의 최소값은 3.16이다. Rust crate는 edition 2021이며 Cargo는 기존 lockfile version 4를 읽어야 한다. 임의 `cargo update`로 의존성을 바꾸지 않는다.

기존 x86 환경의 참고 도구는 GCC 11, Rust 1.91.1, BSC 2026.01, Verilator 5.051 devel이다. 이것은 Nano 설치 확인 결과가 아니다. 정확한 build 실행 시 도구 경로·버전은 새 `identity.json`에 기록된다. 도구가 없거나 맞지 않으면 부족한 항목을 보고하고 멈춘다. 자동 OS 재설치, 전체 toolchain 교체 또는 수치 구현 변경으로 우회하지 않는다.

## 4. 고정 snapshot과 ARM64 simulator archive 만들기

```sh
cd "$SRC"
JOBS=2
RUN="$NANO_WORK/ifr2-aarch64-01"
python3 fpga/dense_pipeline/build.py freeze "$RUN" \
  --host-repo "$DEPS/llama.cpp-gemmini" \
  --params-repo "$DEPS/gemmini-include"
python3 fpga/dense_pipeline/build.py native-sim "$RUN" --jobs "$JOBS"
```

`RUN`은 아직 없는 새 경로여야 한다. `JOBS`는 Nano 메모리에 맞춰 조절한다. `native-sim`은 frozen source의 기존 `make verilator-a8-w8-d16`을 사용한다. BSC가 simulator top `mkSynthA8W8D16`의 Verilog를 만들고 Verilator가 native C++ 입력을 만든 뒤 Cargo가 같은 architecture의 `libim2p_sim.a`와 C++ object/runtime을 빌드한다. 이 단계는 Vivado synthesis/place/route 또는 board bitstream 생성이 아니다.

Native 생성물과 cache는 `RUN/native-aarch64`에 둔다. x86에서는 같은 recipe가 `native-x86_64`를 사용한다. Simulator archive는 `native-aarch64/cargo/a8-w8-d16/debug/libim2p_sim.a`에 생성된다. `native-aarch64/generated-sha256.json`은 생성한 Verilog/C++ 입력을 기록한다.

이번 기본 recipe는 BSC/Verilator 생성을 수행한다. 별도 검증된 generated-input 배포 경로를 제공하지 않으므로 도구가 없을 때 과거 experiment의 generated 파일이나 x86 object를 임의 복사하지 않는다. 생성 C++와 Verilator runtime은 같은 선택 버전으로 빌드한다. `--sim-archive`에 기존 PC의 `.a`를 전달하지 않는다.

## 5. 같은 frontend/host를 ARM64로 빌드

```sh
python3 fpga/dense_pipeline/build.py host "$RUN" --jobs "$JOBS"
python3 fpga/dense_pipeline/build.py verify "$RUN"
file "$RUN/host-build/dense_host_dispatch" "$RUN/host-build/persistent_replay"
sha256sum "$RUN/host-build/dense_host_dispatch" "$RUN/host-build/persistent_replay"
```

`host`는 snapshot의 llama/ggml, frontend, `dense_host_dispatch`, `persistent_replay`를 함께 빌드한다. Archive ELF architecture를 검사하므로 x86 archive를 ARM64에 링크하지 않는다. FPGA 선택 invocation에서 simulator를 실행하지 않더라도 기존 host의 simulator archive 링크 의존성은 유지한다.

`identity.json`, `integration-sha256.json`, `host-build-sha256.json`에 source, pin, architecture, 도구, 실행 파일을 기록한다. ARM64 executable SHA는 나중에 생성된다. 기존 x86 host09-02 SHA와 같아야 하는 값이 아니다. 역사적 x86 hash는 lock의 `historical_host09`에 남고, 새 build는 새 manifest로 식별한다. Protocol/profile, fixture, 고정 hardware hash 검사를 제거하지 않는다.

## 6. 받은 bitstream 확인과 Nano plan 생성

아래 검사는 파일만 읽고 UART/JTAG를 열지 않는다.

```sh
cd "$DEPLOY_DIR"
sha256sum -c SHA256SUMS
python3 - "$BIT" <<'PY'
import hashlib, pathlib, sys
actual = hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest()
expected = '8aef393d040bb306e6ddf7b4b977976a9a924dbc5aec62b37b1690f0aa18c0ca'
assert actual == expected, (expected, actual)
print(actual)
PY
cd "$SRC"
PLAN="$NANO_WORK/ifr2-aarch64-plan-01.json"
python3 fpga/dense_pipeline/prepare_measurement.py --portable \
  --host "$RUN" --bitstream "$BIT" --out "$PLAN"
python3 fpga/dense_pipeline/measure.py "$PLAN"
sha256sum "$PLAN" "$RUN/host-build-sha256.json"
```

Portable plan에는 Nano의 실제 absolute path와 새 executable SHA가 들어간다. 과거 `/home/youngshin/.../build/experiments`가 필수 입력이 아니다. `--fixtures`를 생략하면 현재 checkout의 `fpga/dense_pipeline/fixtures`를 사용한다. `input-f32.bin`과 raw/f_out expected를 포함해 기존 fixture hash를 검사한다.

Plan 생성·검사는 실행 승인이 아니다. 기존 54회/backend 구조를 기록하지만 이 단계에서는 그 sweep을 실행하지 않는다. 첫 보드 검사는 아래 세 smoke를 순서대로 검토한다.

## 7. Device 없는 parser/ownership/fail-fast 검사

```sh
TEST="$NANO_WORK/ifr2-aarch64-tests-01"
mkdir "$TEST"
python3 fpga/dense_pipeline/test_uart.py --out "$TEST/uart"
python3 fpga/dense_pipeline/test_stream_uart.py --out "$TEST/stream-uart"
python3 -m unittest discover -s fpga/dense_pipeline -p test_measure.py
python3 -m unittest discover -s fpga/dense_pipeline -p test_portable_plan.py
python3 -m unittest discover -s fpga/dense_pipeline -p test_deployment.py
python3 fpga/dense_pipeline/test_measurement_guard.py \
  --host-dir "$RUN/host-build" \
  --referencefile "$SRC/fpga/dense_pipeline/full-cycle-reference.txt" \
  --fixtures "$SRC/fpga/dense_pipeline/fixtures" \
  --out "$TEST/measurement-guard" --plan "$PLAN"
```

이 검사는 PTY/fake transport와 simulator를 사용한다. 실제 UART 경로를 인자로 주지 않는다. Cycle ±1 mismatch 뒤 RELEASE/RUN/BEGIN 없음, parent의 다음 조건 중단, owned telemetry lifetime, partial I/O와 identity 오류를 확인한다. Mock 응답 검사는 FPGA numerical 실행 성공을 뜻하지 않는다. Guard의 simulator mode 구분 검사도 별도 logical invocation이다.

다음은 같은 native simulator에서 실제 raw/f_out/padding을 비교하는 짧은 검사다. 각 fixture에 warm-up 1회와 측정 1회가 있으며 warm-up도 correctness 검사에 포함한다. 아래 명령은 성능 비교용 54회 sweep이 아니다.

```sh
FIXTURES="$SRC/fpga/dense_pipeline/fixtures"
"$RUN/host-build/persistent_replay" simulator - 1 \
  "$FIXTURES/m16n16k32" "$FIXTURES/m321n48k64" \
  > "$TEST/simulator-full.log" 2>&1
"$RUN/host-build/persistent_replay" simulator-live-pipeline - 1 \
  "$FIXTURES/m321n48k64" > "$TEST/simulator-live-pipeline.log" 2>&1
```

첫 명령은 4 logical invocation, 둘째는 2 logical invocation이다. Exit code, 최종 PASS marker, 실제 비교·job 수를 모두 확인한다. 저장된 expected와 exact 비교하며 expected를 새 실행 결과로 덮어쓰지 않는다. ARM64 f_out이 다르면 raw, metadata, compiler arithmetic, reconstruction 순서를 분리해 조사한다. 사후 epsilon 확대나 expected 재생성으로 통과시키지 않는다.

## 8. 이동 완료와 실행 승인 후 openFPGALoader 준비

공식 board 표는 `arty_a7_100t`/`xc7a100tcsg324`의 SRAM 지원을 표시한다. 이는 Nano 실측 성공 근거가 아니다. [공식 board 지원표](https://trabucayre.github.io/openFPGALoader/compatibility/board.html)

Nano의 실제 OS에서 `apt-cache policy openfpgaloader`로 후보를 확인한다. 공식 Debian/Ubuntu 설치 명령은 `sudo apt install openfpgaloader`다. 패키지가 없으면 다음 공식 source build를 사용할 수 있다. 설치 여부를 사용자와 확정하기 전에 자동 실행하지 않는다. [공식 설치 안내](https://trabucayre.github.io/openFPGALoader/guide/install.html)

```sh
sudo apt install git gzip libftdi1-2 libftdi1-dev libusb-1.0-0-dev \
  libhidapi-hidraw0 libhidapi-dev libudev-dev zlib1g-dev \
  cmake pkg-config make g++
git clone --branch v1.1.1 --single-branch \
  https://github.com/trabucayre/openFPGALoader.git "$NANO_WORK/openFPGALoader-v1.1.1"
git -C "$NANO_WORK/openFPGALoader-v1.1.1" rev-parse HEAD
mkdir "$NANO_WORK/openFPGALoader-v1.1.1/build-aarch64"
cd "$NANO_WORK/openFPGALoader-v1.1.1/build-aarch64"
cmake -G 'Unix Makefiles' -DENABLE_LIBGPIOD=OFF ..
cmake --build . -- -j "$JOBS"
./openFPGALoader -V
./openFPGALoader --help
sha256sum ./openFPGALoader
```

`v1.1.1`은 여기서 source와 CLI를 검토한 후보 버전이다. 실제 tag commit, executable SHA와 dependency 버전을 Nano 기록에 남긴다. `ENABLE_LIBGPIOD=OFF`는 사용하지 않는 GPIO cable driver를 제외하며 USB/FTDI 경로를 사용한다. Local executable을 직접 사용할 수 있다. USB 권한은 공식 udev 안내를 따라 확인하고 UART group 권한과 구분한다. [v1.1.1 build 설정](https://github.com/trabucayre/openFPGALoader/blob/v1.1.1/CMakeLists.txt), [공식 udev 안내](https://trabucayre.github.io/openFPGALoader/guide/install.html#udev-rules)

## 9. 승인된 SRAM programming 한 번

공식 기본 형태는 `openFPGALoader -b arty_a7_100t "$BIT"`이며 SRAM 로딩이다. `-f`, `--write-flash`를 사용하지 않는다. [공식 첫 실행 안내](https://trabucayre.github.io/openFPGALoader/guide/first-steps.html)

여러 장치에서 첫 USB adapter를 임의 선택하지 않는다. 실제 FTDI serial과 UART by-id 경로를 Nano에서 확인한다. 이전 PC의 ttyUSB 번호나 Vivado cable suffix를 그대로 FTDI serial이라고 추정하지 않는다. v1.1.1은 `--ftdi-serial`을 제공한다. 설치 버전의 `--help`를 확인한다. 현재 master의 `--usb-serial-num`과 혼용하지 않는다. `-d /dev/ttyUSBx`는 udev-enabled build의 다른 adapter 선택 방법이며, `--index-chain`은 USB가 아닌 JTAG chain 내부 선택이다. [공식 FTDI 선택 안내](https://trabucayre.github.io/openFPGALoader/guide/first-steps.html#programming-an-standalone-fpga), [v1.1.1 CLI](https://github.com/trabucayre/openFPGALoader/blob/v1.1.1/src/main.cpp)

이동 완료, 단일 대상 확인, bitstream 전체 hash 재확인, 사용자 실행 범위 확정 뒤에만 아래 명령을 **한 번** 실행한다. `LOADER`는 실제 설치한 executable, `FTDI_SERIAL`은 확인한 대상 serial, `PROGRAM_LOG`는 아직 없는 로그 경로다.

```sh
: "${LOADER:?사용할 openFPGALoader executable의 절대 경로를 지정하세요}"
: "${FTDI_SERIAL:?확인한 단일 FTDI serial을 지정하세요}"
: "${PROGRAM_LOG:?새 programming 로그 경로를 지정하세요}"
test ! -e "$PROGRAM_LOG"
"$LOADER" -b arty_a7_100t --ftdi-serial "$FTDI_SERIAL" \
  "$BIT" > "$PROGRAM_LOG" 2>&1
```

실패하면 중단하고 로그와 보드 상태를 보존한다. 자동 retry/reset/reprogramming/복원은 없다. JTAG에서 확인한 `xc7a100t` die와 manifest의 구현 part `xc7a100tcsg324-1`을 별도로 기록한다.

v1.1.1 Xilinx `.bit` 경로는 구성 전송과 JSTART 뒤 `ir`, `isc_done`, `isc_ena`, `init`, `done`을 출력한다. `done=0`이면 STAT를 출력하지만 이 함수에서 실패 exit를 강제하지 않으므로 **exit 0만으로 성공 판정하지 않는다**. `init=1`, `done=1` 등 실제 상태를 검토하고 이상 상태에서는 CAP를 보내지 않는다. 이 경로는 기존 Vivado의 15개 상태 검사나 독립 SRAM 전체 readback 비교와 다르다. `--verify`는 SPI Flash 전용이다. [v1.1.1 Xilinx SRAM 경로](https://github.com/trabucayre/openFPGALoader/blob/v1.1.1/src/xilinx.cpp#L805-L941), [verify 옵션](https://github.com/trabucayre/openFPGALoader/blob/v1.1.1/src/main.cpp#L957)

## 10. CAP와 첫 세 smoke

Programming 상태를 확인하고 해당 smoke 실행 승인을 받은 뒤 다음을 **한 명령씩** 실행한다. 매번 전체 결과와 완료를 확인한 다음 다음 명령으로 진행한다. 세 명령을 자동 loop나 성공할 때까지 retry하는 script로 묶지 않는다.

```sh
cd "$SRC"
: "${UART_DEVICE:?Nano에서 확인한 UART by-id 경로를 지정하세요}"
export IM2P_FPGA_DEVICE="$UART_DEVICE"
export IM2P_FPGA_FULL_REFERENCE="$SRC/fpga/dense_pipeline/full-cycle-reference.txt"
export IM2P_FPGA_TIMEOUT_SECONDS=30
"$RUN/host-build/dense_host_dispatch" run 16 16 32 1 1 FULL
```

Host constructor는 device를 열며 IFR2 CAP version 2, profile `0x0810`, capacity `336×48×96`을 검사한다. CAP는 SRAM 전체 bitstream readback 증거가 아니다. 작은 FULL은 raw/f_out exact, identity, 완료, board-provider **361 cycles**를 확인한다.

```sh
"$RUN/host-build/dense_host_dispatch" run 321 48 64 7 1 FULL
```

긴 FULL은 raw/f_out exact와 board-provider **39,907 cycles**를 확인한다. FULL cycle gate는 reducer/output commit과 RELEASE 전에 실행된다. 불일치 시 다음 장치 명령을 보내지 않는다.

```sh
"$RUN/host-build/dense_host_dispatch" run 321 48 64 7 1 STRIPE_PIPELINE
```

Live PIPELINE은 기존 producer와 자동 geometry의 stripe `160/160/1`, event slot `0/1/0`을 사용한다. 전체 raw/f_out, stripe/slot/row identity, publication/completion, fence를 확인한다. FULL expected cycle이나 이전 x86 PIPELINE elapsed를 이 실행에 강제하지 않는다. R1 simulator cycle과 board-provider cycle도 구분한다.

선택 FPGA 연산에서 CPU/simulator fallback을 허용하지 않는다. `run` 완료 후 독립 reference 계산은 correctness 검사이며 persistent performance sample과 섞지 않는다. Layer classification 문자열만으로 수치 fallback을 판정하지 않는다. Runtime counter와 실제 실행 경로를 확인한다. 세 smoke 이후 반복 수와 비교 조건은 사용자가 정한다. 기존 FPGA 56회+simulator 54회 sweep을 자동 시작하지 않는다.

## 11. 결과와 한계 기록

Raw는 signed32 exact, f_out은 float32 bit-exact, fixture padding은 sentinel 보존을 유지한다. Quantizer/tiler/External reconstruction/RMD OFF 의미를 바꾸지 않는다. Native f_out 차이가 있으면 raw와 scale metadata, compiler arithmetic, reducer 순서부터 분리해 조사한다.

Service는 입력 준비부터 최종 output commit까지이며 sustained는 정상 RELEASE/Run retirement를 포함한다. 전체 process의 파일 비교·로그·객체 정리 모든 비용을 포함한 throughput이라고 표현하지 않는다. 전체 hash 검사는 반복 timer 밖에 두되 실행 중 CRC/profile/id/count 검사는 유지한다.

ARM CPU cycle source/PMU 권한이 없으면 기존 validity 정책대로 unavailable을 기록한다. Host wall-clock이나 FPGA counter를 CPU cycle로 대체하지 않는다. Device elapsed, wait, engine/provider overlap, host response tally의 원천은 [telemetry 계약](../fpga/dense_pipeline/host_instrumentation/TELEMETRY.md)을 따른다. Host ns와 device cycles를 직접 빼지 않는다. Pure compute, stripe별 first-A, 독립 publication/stripe-ACK counter는 미노출 항목이다.

Nano 성능은 같은 Nano에서 같은 서비스로 새 측정한 simulator와 비교한다. 이전 x86 simulator 시간을 분모로 쓰지 않는다. UART1Mbaud/8N1, nominal core25MHz와 profile을 유지한다. 이 절차는 residual-enabled ExSIA, 전체 모델, TTFT/TPOT 검증이 아니다. 현재 ARM64 build/runtime, openFPGALoader programming, Nano CAP/GEMM/성능은 모두 **NOT RUN**이다.
