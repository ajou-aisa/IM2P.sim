# 일반 llama FPGA_UART 사용 안내

> 아래는 봉인된 source02/IFR3 배포의 사용 이력이다. 현재 branch의
> bounded IFR4/main_external 빌드·형식·residual 계약은
> [현재 안내](../../docs/HOST_STRIPE_CONTRACT.md#ordinary-current-source-build)를 따른다.
> 아래 cycle whitelist와 과거 bitstream identity를 새 후보에 적용하지 않는다.

이 배포는 일반 `llama-cli`/`llama-perplexity`의 FPGA adapter 연결용이다.
ABI5 / IFR3 / H1 / final integer domain2 / A8/W8/D16 / EXSIA / block32 / RMD OFF를 사용한다.
Flash 자동 부팅, ARM64 실행, 전체 모델 offload는 각각 별도 검증 사항이다.
이 문서의 장치 실행 예는 **별도 물리 실행 승인 뒤에만** 사용한다.

## 1. 봉인된 source02 받기

원본 archive:
`/mnt/fpga-build/im2p/llama-fpga-runtime-20260911-02/llama-fpga-source-02.tar.gz`

- 크기: 28,300,036 bytes.
- SHA256: `b7f6026b2e9a889e39d1e51e472e7c69ab2f8c4d36f725413849eeafa43ab29e`.
- 추출된 `deployment-manifest.json` SHA256:
  `5d5f2c101cc3a53b9ab3beabfd8d0bdf3aef28a2663667cb90b78e291d8077cd`.

전달받은 archive를 먼저 hash 검증한다. `EXTRACT`는 아직 없는 새 디렉터리로 정한다.
다음 기본 tar 추출은 **위 전체 hash가 일치한 이 archive에만** 사용한다.

```bash
set -euo pipefail
ARCHIVE=/absolute/path/to/llama-fpga-source-02.tar.gz
EXTRACT=/absolute/path/to/new-source-extraction
printf '%s  %s\n' \
  b7f6026b2e9a889e39d1e51e472e7c69ab2f8c4d36f725413849eeafa43ab29e \
  "$ARCHIVE" | sha256sum --check -
mkdir "$EXTRACT"
tar --extract --gzip --file "$ARCHIVE" --directory "$EXTRACT" --no-same-owner
SEL="$EXTRACT/llama-fpga-source"
python3 "$SEL/source/fpga/scu_runtime/assemble.py" verify --root "$SEL"
```

기존 source/helper가 있다면 같은 `assemble.py extract --archive ... --out NEW`를
사용할 수 있다. 이 extractor는 link/unsafe path/duplicate/기존 destination을 거부한다.
검증된 source는 수정하지 않고, 모든 generated/build/cache output은 별도 WORK에 둔다.
Source에는 bitstream, x86 실행 파일·archive, 사용자 모델 weight가 들어 있지 않다.
기존 Flash/bitstream 전달은 별도 절차다.

## 2. 새 native dependency 빌드

필요 도구: C++20 compiler/표준 library, CMake, Make, Python3.11+, BSC,
Verilator, Rust/Cargo 및 `source/sim/Cargo.lock`의 crate source.
Vivado는 host build에 필요 없다. 설치·업그레이드는 자동 수행하지 않는다.

선택된 FPGA 경로에서 simulator 실행이 0이어도 현재 frontend는
`libim2p_sim.a`에 링크한다. 따라서 target architecture의 새 archive가 필요하다.
옛 x86 `.a/.o`를 ARM64 입력으로 복사하지 않는다.

```bash
WORK=/absolute/path/on/large-filesystem/new-native-work
mkdir "$WORK"
mkdir -p "$WORK/tmp" "$WORK/cache/cargo"
export TMPDIR="$WORK/tmp" TMP="$WORK/tmp" TEMP="$WORK/tmp"
export XDG_CACHE_HOME="$WORK/cache" PYTHONPYCACHEPREFIX="$WORK/cache/python"
export CARGO_HOME="$WORK/cache/cargo"
# crate source 취득. 검증된 vendor/cache가 있으면 해당 설정으로 대체한다.
cargo fetch --locked --manifest-path "$SEL/source/sim/Cargo.toml"
export CARGO_NET_OFFLINE=true
make -C "$SEL/source" -j2 gemmini-frontend-real-lib \
  BUILD_DIR="$WORK/native" \
  GEMMINI_ROOT="$SEL/host" GEMMINI_PARAMS_ROOT="$SEL/params/include" \
  IM2P_ACTIVATION_BITS=8 IM2P_WEIGHT_BITS=8 IM2P_DIM=16 \
  GEMMINI_FRONTEND_ACTIVATION_BITS=8 GEMMINI_FRONTEND_WEIGHT_BITS=8 \
  GEMMINI_FRONTEND_DIM=16 GEMMINI_FRONTEND_BLOCK_SIZE=32
MANIFEST=$(readlink -f "$WORK/native/selected/a8-w8-d16/current/real-lib.json")
```

`MANIFEST`는 `current` symlink가 아닌 generation의 실제 경로로 고정한다.
CMake도 source/archive/manifest REALPATH를 고정하고 hash/profile/ABI/arch를 검사한다.
Historical SCU `build.py native`/migration `native_build.py --build`는 이 변경된
ordinary host의 recipe가 아니다. 위 direct Make 명령을 사용한다.

## 3. 일반 x86 또는 native ARM64 script

아래는 Bash 명령이다. 모든 설정을 provisioning 전에 해석한다.
우선순위는 CLI > 환경변수 > 기존 cache > 기본값이다.
명시적인 DIM/precision/RMD 충돌은 거부하며 새 BUILD_DIR를 사용한다.

```bash
FPGA_ARGS=(
  -DGGML_GEMMINI_EXECUTION_BACKEND=FPGA_UART
  "-DIM2P_SIM_ROOT=$SEL/source"
  "-DGGML_GEMMINI_FPGA_SIM_MANIFEST:FILEPATH=$MANIFEST"
  "-DGEMMINI_SW_PATH=$SEL/params"
  -DGGML_BACKEND_DL=ON -DGGML_NATIVE=OFF -DGGML_CCACHE=OFF
  -DGGML_OPENMP=OFF -DGGML_GEMMINI_ENABLE_OPENMP=OFF
  -DLOG_DEBUG=0 -DLOG_CYCLE=0 -DGGML_CPU_CYCLE_LOG=0
  -DGGML_GEMMINI_DEFAULT_MATMUL_MODE=FULL
  -DGGML_GEMMINI_ALLOW_RUNTIME_MATMUL_OVERRIDE=ON
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
)
# x86_64에서 실행. CLI와 perplexity를 빌드한다.
BUILD="$WORK/llama-x86-fpga"
BUILD_DIR="$BUILD" BUILD_JOBS=2 "$SEL/host/build-x86.sh" "${FPGA_ARGS[@]}"
```

Native Linux ARM64에서는 먼저 **그 ARM64 target에서** 위 native dependency를
별도 WORK에 빌드한다. 이후 같은 `FPGA_ARGS`와 다음 실제 script를 사용한다.

```bash
BUILD="$WORK/llama-arm64-fpga"
BUILD_DIR="$BUILD" BUILD_JOBS=1 "$SEL/host/build-arm64.sh" "${FPGA_ARGS[@]}"
```

ARM script는 CLI/perplexity/quantize를 빌드한다. 이 작업의 ARM64/Nano build와
runtime은 **NOT RUN**이다. X86에서 ARM script를 실행하여 x86 ELF가 만들어진
결과를 ARM PASS라고 부르지 않는다. Native FPGA script는 host architecture를 검사한다.
Cross-build는 explicit target archive/sysroot를 쓰는 직접 CMake 경로이며 별도 target 검증이 필요하다.
CPU cycle logging을 끄는 위 값은 검증되지 않은 ARM PMU 권한을 요구하지 않도록 한 설정이다.

Build만 검토하려면 script에 `--dry-run`을 추가한다. 이때 heavy provisioning,
CMake build, UART/JTAG 접근은 없다. `--help`도 장치 접근이 없다.

다른 backend는 새 BUILD_DIR에서 명시적으로 선택한다.

| 사용 경로 | 주요 선택 |
|---|---|
| 기존 CPU | `-DGGML_GEMMINI_EXECUTION_BACKEND=HARDWARE -DGGML_GEMMINI_OPTION=CPU` |
| 기존 simulator | `-DGGML_GEMMINI_EXECUTION_BACKEND=IM2P_SIM -DGGML_GEMMINI_OPTION=WS`, 같은 selected core/params와 명시적 profile |
| SCU FPGA | 위 FPGA_ARGS. USB 연결만으로 backend가 바뀌지 않음 |

현재 x86 script에는 사용자의 기존 WS/IM2P_SIM 기본값이 보존되어 있다.
CPU/SIM 선택에 FPGA manifest 옵션을 섞지 않는다.

## 4. 장치 없는 load와 작은 fixture 생성

```bash
"$BUILD/bin/llama-cli" --list-devices
"$BUILD/bin/llama-perplexity" --help
python3 "$SEL/source/fpga/scu_runtime/test_build_options.py" \
  --out "$WORK/options-tests"
python3 "$SEL/source/fpga/scu_runtime/tests/tiny_model.py" \
  --host-source "$SEL/host" \
  --ggml-base "$BUILD/bin/libggml-base.so" \
  --out "$WORK/tiny-model"
```

목록/help/supports 검사는 UART를 열지 않는다. 생성기는 기존 GGUF writer와
`ggml_quantize_chunk(GGML_TYPE_Q8_H1)`를 사용한다. 다운로드·변환은 하지 않는다.
`tiny-h1.gguf`는 **학습되지 않은 test-only 모델**이다. H1은 K64/N16 output
projection 하나이며 나머지 F32 연산은 CPU에 남는다. 일반 모델 품질 근거가 아니다.
이 생성기가 model eval을 자동 실행하지는 않는다.

## 5. 별도 승인 후의 명시적 runtime 예

다음은 미래 실행 예다. 이번 준비 작업에서 physical UART에 실행하지 않는다.
한 물리 UART는 **한 process만** 소유해야 한다. Adapter mutex는 process 내부
보호이며 서로 다른 process를 잠그는 장치 lock은 아니다.

```bash
: "${UART_DEVICE:?Set the explicitly approved UART path; do not scan/open arbitrary devices}"
export IM2P_FPGA_DEVICE="$UART_DEVICE"
export IM2P_FPGA_FULL_REFERENCE="$SEL/source/fpga/scu_runtime/references/full-cycle-reference.txt"
export IM2P_FPGA_HARDWARE_SHA256=2c5516e2bae4d5f960697537bb1501d42470b7971be5986b6e59ad8cc6586984
export IM2P_FPGA_PRODUCTION_RTL_SHA256=1833a6a4f53e48fa18f0afb188fbd2858a7584376701a333374025174d061c26
export IM2P_FPGA_REQUIRE_COMPLETION=1
export GEMMINI_MATMUL_MODE=STRIPE_PIPELINE
"$BUILD/bin/llama-cli" -m "$WORK/tiny-model/tiny-h1.gguf" \
  --device GEMMINI -ngl 99 --prompt 'a a a a' --n-predict 2 \
  --temp 0 --ignore-eos --ctx-size 32 --batch-size 16 --ubatch-size 16 \
  --threads 1 --no-warmup
```

`--device GEMMINI -ngl 99`가 명시적 placement를 요청한다. Supports 검사는 그대로
유지한다. `IM2P_FPGA_REQUIRE_COMPLETION=1`이면 실제 FPGA completion0의 CPU-only
성공을 거부한다. Runtime mode는 **GEMMINI_MATMUL_MODE**다.
`GEMMINI_MATMUL_INVOCATION`을 mode 설정으로 사용하지 않는다.

PPL의 작은 연결 검사는 다음 형태다. 이것도 별도 승인된 장치/수량에서만 실행한다.
학습되지 않은 synthetic 모델의 점수는 모델 품질 측정이 아니다.

```bash
export GEMMINI_MATMUL_MODE=FULL
"$BUILD/bin/llama-perplexity" -m "$WORK/tiny-model/tiny-h1.gguf" \
  --device GEMMINI -ngl 99 --file "$WORK/tiny-model/ppl.txt" --chunks 1 \
  --ctx-size 32 --batch-size 16 --ubatch-size 16 --threads 1 --no-warmup
```

이 source02에는 PPL의 `--no-warmup` 옵션 지원과 FPGA warmup 오류의 nonzero
전파가 포함된다. `--no-warmup`은 숨은 추가 invocation을 피하는 명시적 선택이다.
FULL은 아래 세 shape reference만 허용한다. 다른 shape의 FULL은 actual cycle을
expected로 등록하지 않고 실행 전에 거부한다. PIPELINE elapsed에 이 cycle을 적용하지 않는다.

| FULL M/N/K | Expected cycle |
|---|---:|
| 16/16/64 | 617 |
| 321/48/96 | 52,687 |
| 17/19/64 | 1,949 |

## 6. 한계와 오류 처리

- Native H1/F32 지원 layout, K=32/64/96, N≤48, backed M≤336만 대상이다.
  CAP 이내라는 사실만으로 strict FULL reference를 통과하지 않는다.
- `LLAMA_GEMMINI_Q8_H1_ARTIFACT`는 기존 reader 설정이다. Q8_0/HP1 GGUF를 H1으로 바꾸지 않는다.
- Local GPT-2/Llama3.2-1B 모델은 Q8_HP1이며 K/N도 현재 capacity 밖이다.
  이 배포로 해당 모델 전체가 FPGA에 offload되지 않는다. 새 K 분할은 구현하지 않았다.
- FPGA HP1/residual, physical DIM64, Nano runtime, 전체 모델/PPL 품질은 미검증/미지원 범위를 유지한다.
- Missing device/reference, ABI/profile/domain/CRC/count/cycle 오류는 실패한다.
  선택된 FPGA 실패를 CPU/simulator 재실행으로 숨기지 않는다.
- 정상 RELEASE와 실패 뒤 자동 복구는 다르다. 자동 reset/retry/reprogramming은 추가하지 않는다.
  모든 cancellation에서 command0을 보장한다고 확대하지 않는다.
- 등록 banner만으로 실행을 증명하지 않는다. 실제 assigned/attempted/completed와
  test별 simulator 호출 관측을 구분한다. 미계측 fallback은 `not_instrumented`다.

상세 실행·실패 증거와 최종 L1~L7 판정은 전달된 통합 보고서를 따른다.
Build, backend load, 실제 RTL 수치 실행, Nano 실행, 물리 FPGA 검증을 서로 대신하지 않는다.
