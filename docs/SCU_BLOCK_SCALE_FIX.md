# SCU block scale 실행 위치 수정

작업 중인 후보다. 기존 실보드 PASS는 External/host-scaled baseline의 검증이며,
이 후보의 수치·route·보드 검증으로 재분류하지 않는다.
목표 수치 계약은 [SCU_BLOCK_SCALE_CONTRACT.md](SCU_BLOCK_SCALE_CONTRACT.md)에 둔다.

## 기준과 보존

시작 IM2P HEAD는 `3aeb5feee6872f88ec1f6a5dc0d77fb1bb8babf8`,
clean `exp/pe-local-partial-int20`이었다. 새 local `fix/scu-block-scale`에서 작업한다.
Host pin은 `7a74ac29c9c8fa8fbc5d47cf073b8b9266e3e7a9`,
include pin은 `cd2caed8d93e49d3f8873bd6c4e340fcad54b9e0`이다.
두 sibling의 tracked source는 수정하지 않는다. Host의 untracked model 디렉터리도 보존한다.

실험 증거 경로는 `build/experiments/scu-block-scale-20260910T053546Z`다.
`baseline/{core,host,params}`에 시작 status/HEAD/log/stat/check/binary diff/cached diff를,
`baseline/source.tar`와 `source-sha256.json`에 변경 전 source를 보존했다.
자동 staging/commit/push, 장치 접근, programming은 수행하지 않는다.

## 기존 구현의 실행 실패 재현

`tests/scu_block_scale/run_legacy_repro.py`는 보존된 legacy frontend와 실제 RTL simulator
archive를 링크한다. 새로운 수치 mock이나 ABI 불일치를 재현으로 사용하지 않는다.
최종 재현 증거는 `legacy-repro-02`다. 앞선 `legacy-repro-01`도 보존한다.

Synthetic native Q8_H1 M1/N1/K64, block32, A/W code1,
c=0/255, R=1, S=1/256, activation scale1에서 다음을 관측했다.

| 항목 | 기존 실행 관측 | 새 목표 |
|---|---|---|
| Descriptor op | External3 | unsigned multiply4 |
| Provider scale response | 1,1 | 1,256 |
| 실제 RTL output | block0=32, block1=32 | final integer8224 |
| Valid scalar callback 수 | 2 | 1 |
| 최종 f_out | 32.125 | 32.125 |
| 판정 | 기존 계약 PASS, 새 실행 위치 FAIL(exit2) | 아래 S2에서 별도 검증 |

각 repro 디렉터리에서 legacy-positive/target-negative 각각1 invocation을 실행했다.
총4회이며 고유 입력은1개다. 내부 SCU register는 이 재현에서 관측하지 않았다.
Descriptor/provider/실제 RTL completion을 관측한 범위를 넘겨 주장하지 않는다.

## 변경 경계

Frontend H1/HP1은 각각 op4/op5와 typed metadata를 공급한다. 반복 저장된 channel
S/R과 유효 metadata를 검증하고, final-domain callback만 M*N개 수락한다.
Host는 그 integer에 shared S와 row/stripe activation scale을 한 번 적용한다.
기존 External block reducer는 H0/명시적 legacy 경로에 남는다.

기존 scheduler의 block reset과 intermediate output은 이미 External op에만
적용된다. 새 op에서는 첫 fragment replace, 나머지 전체 K accumulate,
work당 final output 정책을 재사용한다. INT20 partial과 activation-response
capacity guard는 S1에서 변경하지 않았다.

| 계층 | 권위 있는 입력/변경 |
|---|---|
| Editable RTL | 현재 `src/`의 새 numerical delta |
| Frozen board core | base `dc4a1a621f63834d64df42ae8c24152747d97971` → 기존 `fixed-core.patch` → 새 `fpga/scu_block_scale/core.patch` |
| Simulator/frontend | 명시적으로 선택한 현재 `sim/`, `frontend/`, config/generator source |
| Host | pinned archive → 기존 host integration → producer observation → 새 host companion patch |
| Generated/cache | fresh snapshot에서 BSC/Verilator/Rust/host 재빌드; ABI5와 numerical revision 검사 |
| Hardware | `ScuPipeline.bsv` + IFR3 shell, 새 production RTL에서 검증; 기존 IFR2 bitstream 사용 불가 |

Root Arithmetic 전체를 board Arithmetic 위에 복사하지 않는다. Src delta만 적용하여
board A1 multiply 표현과 board-specific scheduler를 유지한다. 이전 deployment-lock,
approval, fixture expected, cycle reference는 historical 파일로 보존한다.

## S1과 S2를 분리한 실제 검증

아래 경로는 별도 표시가 없으면 위 실험 디렉터리 기준이다. 비교 횟수는 반복을
포함한다. Native simulator의 provider latency와 FPGA board provider의 latency는 다르다.

| 검사 | 실제 결과 | 증거 |
|---|---|---|
| S1 typed VectorUnit | 32/64비트,7 jobs/14 groups/27 valid lanes PASS; legacy VectorUnit PASS | `build/experiments/scu-s1-rtl-tests` |
| ABI5 | C/C++ layout 실행2 PASS; Rust metadata-only typecheck는 별도 | `s1-sim-abi` |
| S1 actual C API | SCU numerical6 cases PASS, ABI4/domain 거부 | `s1-native-01/c-api-runtime` |
| S1 actual frontend FULL | raw/f_out16 exact, invalid metadata6 거부 | `s1-full-02` |
| S1 FULL/PIPELINE | H1/HP1×2 modes=4 PASS, raw/f_out61,632씩, padding65,484 | `s1-pipeline-02` |
| S2 SCU/accumulator RTL | 12 vector jobs/96 lanes/18 accumulator commits·reads PASS; legacy 관련13 checks PASS | `build/experiments/scu-s2-rtl-tests` |
| S2 actual frontend FULL | raw/f_out22 exact, metadata6 거부 | `s2-full-01` |
| S2 H1 carrier 경계 | c=0/1/127/128/200/255 × R=0/1/256/65535 × 양·음 partial,48 actual RTL raw/f_out exact | `s2-carrier-edges-01` |
| S2 FULL/PIPELINE | H1/HP1×2 modes=4 PASS, raw/f_out61,632씩, padding65,484; missing publication 후 caller output 보존 | `s2-pipeline-01` |
| S2 기존 Cargo RTL 회귀 | 29 binaries, unit42+integration121=163/163 PASS; ignored/runtime RTL assertion failure0 | `s2-regression-rtl-04/regression-summary.json` |
| Frontend mock | A8 36 tests와 HP1 exponent extent, A4/A16 compatibility PASS | `frontend-test-migration-summary.json` |
| IFR3 PTY / FULL gate | parser·ownership20, synchronous FULL gate21 PASS | `ifr3-host-transport-summary.json` |
| Make / cache | `make check`, `make cache-contract-test` PASS | `make-check-05.log`, `cache-contract.log` |

S1은 `scu-wrap-staging-v1`의 overflow-free 중간 단계다. S2는
`signed-scu-sat-v2`이고 임시 wrap을 새 production op에서 제거했다.
SCU는 49/81bit full product, accumulator add는 33/65bit 합에서 clamp한다.
기존 PE multiply/accumulate, widenPartial, legacy accumulatorAdd의 함수 body는
보존했다. `IM2PCore`의 commit 호출에 새 op의 saturation 여부만 전달한다.

기존 154라는 과거 숫자를 새 회귀 횟수로 사용하지 않았다. 현재 실제 suite는163이다.
첫 회귀는162 PASS/1 FAIL이었다. 실패는 INT32 packing test의 `(1u32 << 32)` helper
panic이었다. Mask를 `u32::MAX >> (32-bits)`로 바꾸고 fresh source/build에서
163개를 실행했다. Generated RTL 차이는 날짜 주석 한 줄이며 raw diff와
normalized hash를 함께 보존했다. 이 실행은 production BSC + Verilator `--assert`다.
BSV `-check-assert` 실행과 동일하다고 표시하지 않는다.

## 실제 RTL 연산 위치

`s2-core-monitor-03`은 production generated model의 기존 신호만 읽는다.
SCU input register → contribution commit → pending accumulator latch → 실제 BRAM
DI/write → final FFI result를 연결했다. Test-only assertion을 DUT schedule에 추가하지 않았다.

| 입력 변화 | 실제 final integer |
|---|---:|
| H1 beta1/256, A/W code1, K64 | 8224 |
| 같은 입력 beta1/257 | 8256 |
| H1 beta65536/65790 | 4202432 |
| positive saturation 뒤 negative contribution | 2147483615 |
| HP1 zero/large exponent, positive lane | 2147483647 |
| HP1 zero/large exponent, negative lane | -2147483648 |

6 cases에서 실제 logical lane commit24개와 BRAM write24개를 관측했다.
물리 wavefront의 padded column360개는 별도로 0을 확인했다. 이를 logical output
count에 합치지 않는다. Passive monitor 입력33개 hash는 불변이다.

`s2-mutations-02`와 `s2-boundary-mutation-01`에서는 identity factor,
uint8/uint16 절단, Bypass/External, callback block 변경, host shared-S 중복 적용,
block 경계 reset을 모두 검출했다. Descriptor/provider fault6종,
격리 frontend source mutation1종, generated-C++의 lowered RTL 제어식 mutation1종이다.
마지막 것은 BSV/Verilog 재생성 mutation과 구분한다. ABI mismatch만으로 검출한 사례는0이다.
정상 control2개가 통과했고 원본 production source는 바꾸지 않았다.

Host production `factor()`에서 H1/HP1 block FP scaling을 제거했다.
`scu_metadata()`는 decode/검증만 하며 `read_scale()`이 실제 uint32 metadata를 공급한다.
`write_output()`은 domain2/M*N final integer만 수락하여
`double(integer) * double(shared_S) * double(activation_scale)` 후 float32로 변환한다.
H0의 정당한 legacy FP reducer는 남는다. 미계측 reference 호출을 runtime0으로
주장하지 않는다. 실제 ggml FPGA 시험의 reference는 실행 결과를 얻은 뒤 독립 G1/G2
정확성 검사로 호출하며 selected matmul의 simulator create/execute/stream-begin0을 별도로 관측한다.

## 기존 입력 보존 / cross-profile

`replay_golden.py`는 기존 IFX1의 A/qs/c/R/S/theta를 그대로 읽는다. 고유8입력의
새 final expected33,699개를 독립 생성했다. 기존 K32 raw82,550개도 독립 재계산하여
일치했다. 이번 입력은 saturation0이며 새 f_out도 legacy expected와 bit-exact였다.
이 결과를 모든 입력의 old/new equality 또는 모델 품질로 확대하지 않는다.

`s2-portable-01`은 기존 S2 native archive에 기존 capture의 Fixture/bind/restore를
그대로 재사용한 test를 링크했다. 고유8입력×FULL/PIPELINE=16/16 invocation,
raw67,398/f_out67,398/padding5,176 exact PASS다. 긴 K64/K96은 automatic
stripe160/160/1, slot0/1/0, final domain/count/authorization을 검사했다.
이 검사는 deterministic replay이며 live quantization으로 부르지 않는다.

별도 `h1-carrier-edges-sat-v2` fixture는 UINT8 code와 UINT16 R 조합48개를 실제
native frontend/RTL에 공급한다. Beta0/1/255/256/65535/65536/65790을 포함하며
storage code의 signed 해석 또는 uint8 truncation으로 통과할 수 없다.
기존22개 expected와 기존 IFX1 expected는 변경하지 않았다.

`build/experiments/scu-cross-profile`에서 typed raw-core A4/D16, A16/D16, A8/D32는
각3 GEMM PASS다. Scalar 비교는 각각331/331/1163개다. DIM32의 exact local partial
최댓값524288도 확인했다. 이 provider의 factor 조합은 typed SCU stress이며
한 channel의 shared-R까지 만족하는 native model capture라는 주장은 하지 않는다.
DIM64는 BSC elaboration 뒤 C++ compile이 디스크 ENOSPC로 실패하여 runtime NOT RUN이다.
최종-source A4/A16 재실행도 같은 공간 문제로 보류됐다. 기존 PASS source snapshot은 보존했다.

## FPGA 연결과 현재 차단점

`integration-01`은 새 native archive/frontend/ggml adapter/BSC/Verilator를 실제 빌드한
고정 snapshot이다. `source-provenance.json`은21개 핵심 파일의 committed/dirty/
legacy frozen/new frozen hash를 비교한다. Root와 selected source가 다른 핵심 RTL은
Arithmetic과 두 scheduler이며, board A1/scheduler variant를 의도적으로 유지했다.

| Artifact | SHA256 |
|---|---|
| 최종 입력 integration manifest | `6e74880252b154919534bdfdf4fd27f925377c55d4a084b70c64fb2c86589190` |
| production mkScuPipeline.v | `8d3a622022fe8df65f2a8bbd2ab2041bbe2cb231f7ae592c99ade460db2c7fc6` |
| scu_host_dispatch | `821cc349a13a700bd5cbcf70a3b48eef59add8de141164fdfb4d6a921d90431c` |

IFR3 scale upload는 beta uint32, final output은 단일 M*N plane이다. 자세한 wire/BRAM/
수명/계측 표는 [새 FPGA README](../fpga/scu_block_scale/README.md)에 있다.

- `ifr3-packets-01`: actual UART production RTL,10 H1 cases raw10/padded lanes150 PASS.
- `ggml-ifr3-full-01`: 실제 ggml/quantizer → UART RTL → frontend reconstruction,
  M16/N16/K64 raw256/f_out256 PASS, cycle617, work1/fragment4/write·ACK16.
- `ggml-ifr3-full-k96-01`: 같은 실제 호출 경계 M321/N48/K96,
  raw15,408/f_out15,408 PASS, cycle52,687, work63/fragment378/write·ACK963.
- `ggml-ifr3-live-k64-02`: 실제 ExSIA post-fold producer → IFR3 → RTL,
  M321/N48/K64 raw15,408/f_out15,408 PASS. Stripe160/160/1·slot0/1/0,
  work63/fragment252/write·ACK963, host publication/completion tally3/3,
  fence/retirement PASS. 첫 event는 다음 stripe quantization 완료 전에 수락됐다.
- `board-top-unsandboxed-01`: vendor MMCM/BUFG + UART/provider/core에서 CAP/FULL/RELEASE
  3응답을 Verilator와 byte-exact 비교. M1/N1/K64 raw8224, padding15, cycle377,
  fragment4/work1/write·ACK1,40ns core period 확인. 이 vendor bench의 f_out은 NOT RUN이다.
- 실제 live K64는 첫 실행에서 RTL child exit1 뒤 parent가 host만 기다렸다.
  signal/OOM kill이 아니며 당시 ENOSPC와 시간상 대응하지만 stderr도 비어 있어
  정확한 child 예외 원인은 미확정이다. `termination-diagnosis.json`과 partial trace를
  보존했다. Buffered trace의 마지막 byte를 실제 protocol 정지 위치로 해석하지 않는다.
  Runner에 child 종료 감시와 parent PTY fd retirement를 추가했다.
  fake child exit1 검사에서0.103초 이내 nonzero 실패를 확인했다. 이후 동일 RTL/host의
  별도 live 실행은 위 `live-k64-02`로 분리했고 정상 완료했다.

이 live 실행의 device elapsed는26,155,216, host-wait는20,930,077,
engine/provider overlap은6,426 cycles다. Global first-A는5,187,120,
그 시점 published prefix는160 rows였다. 이들은 board-provider RTL simulation 값이며
순수 compute나 실제 CPU–FPGA overlap 시간이 아니다. Host에서는 quantization 세 stripe가
첫 device publication 응답 전에 모두 ready였다. 자연 overlap을 입증하지 않았고,
지연 checkpoint 진단은 새 SCU host에서 NOT RUN이다. FPGA pipeline elapsed를 FULL cycle과
같아야 한다고 검사하지 않았다. 입력/응답은46,200/63,704 bytes,14 transactions이며
CAP 초기화는 이 invocation tally 밖이다. RTL wall time을 실보드 성능으로 사용하지 않는다.

첫 vendor Xsim 실행도 startup Tcl exception으로 completion marker가 없었다.
같은 elaborated snapshot을 sandbox 밖에서 실행하면 정상 완료했다. 정확한 IPC syscall
원인은 추적하지 않았으며 첫 실패를 삭제하지 않았다.

현재 디스크가100%로, 관측된 여유29 MiB 시점에 DIM64 compile이 실패했다.
새 route/bitstream, 추가 대형 clean build는 공간 확보 전 NOT RUN이다.
새 resource/timing/CDC/DRC/bitstream SHA는 없으며 legacy25MHz 결과를 대입하지 않는다.
`candidate-status.json`은 **INCOMPLETE_NOT_APPROVABLE**이다. Programming 승인 패키지로
사용할 수 없다. 기존 FULL cycle361/39907/58695나 speedup도 새 후보에 사용하지 않는다.

## 미완료 범위와 결정

| 범위 | 상태 / 필요한 다음 단계 |
|---|---|
| Dense simulator H1/HP1 | Implemented / Simulated: 위 실제 FULL/PIPELINE/G1/G2 범위 |
| Residual companion | Blocked: radix plane 복원·merge clamp 순서 미결, ON 요청 명시적 거부 |
| FPGA H1 FULL | Implemented / RTL Simulated: 실 ggml FULL 및 vendor board-top 근거 |
| FPGA H1 live PIPELINE | Implemented / RTL Simulated: 위 실제 producer K64 한 invocation |
| FPGA HP1/RMD | Unsupported, start 전 거부 |
| DIM64 | ENOSPC로 runtime NOT RUN |
| M9/N1/K1 | actual S2 native raw-core4회/36값 PASS; 별도 resident passive/assertion 재빌드는 NOT RUN |
| 25MHz route / 새 bitstream | NOT RUN: 디스크 부족 |
| Board measured / Nano | NOT RUN, 장치 접근0 |
| Model quality/PPL | NOT RUN |

Residual는 `Sat(residual)` 뒤 dense add clamp와 widened residual+dense의 단일 clamp가
같지 않다. 이 선택과 radix plane 처리 순서를 결정해야 ON을 구현·검증할 수 있다.
독립 dense/residual simulator 두 개를 물리 FPGA 엔진 두 개로 취급하지 않는다.

로컬 GPT2/llama1B Q8_HP1와 WikiText 파일은 존재한다. 그러나 새 모델 실행은 하지 않았다.
기존 sibling build cache는 HARDWARE/WS/EXSIA/RMD ON이며 새 SCU/PPL 근거가 아니다.
현재 공간 부족과 residual 정책 미결을 해결한 뒤 새 ABI5 host/model build,
명시적 RMD OFF ablation 또는 승인된 residual saturation 계약으로 품질을 다시 측정해야 한다.
기존 PPL 완료 로그를 이번 bounded 조사에서 확인하지 못했으며 값을 재사용하지 않았다.

검증 fixture 오류도 보존했다. 초기 FULL의 null Run 가정, PIPELINE의 residual semantic
count 가정, DIM32 fragment 수를 잘못 가정한 oracle, 초기 mock profile define 누락 등은
각 실패 로그와 수정 이유를 남겼다. Expected epsilon이나 cycle을 DUT 결과에 맞춰
바꾸는 방식으로 통과시키지 않았다.

후속 read-only review에서는 기존 frontend의 출력 extent 검사식 자체가 SIZE_MAX에서
underflow하는 결함과 UART first-A cycle을 rows보다 먼저 공개하는 관측 race를 찾았다.
각각 start 전 overflow 거부, rows 저장 후 cycle 공개로 최소 수정했다.
`review-fixes-01`은 이전 source+안전 probe FAIL → 수정 source PASS,
frontend mock37개와 IFR3 PTY20개 PASS를 기록한다. 이 후속 수정의 linked build와
위 `integration-01` numerical 결과는 별도 identity로 관리한다.

`review-linked-02`는 immutable S2 simulator/host dependency와 새 frontend/UART를
명시적으로 선택하여 재컴파일·재링크한 소형 build다. 전체 fresh clean build로
분류하지 않는다. 기존 host archive30개 member 중28개가 byte-exact로 유지됐고,
UART와 adapter2개만 새 source/build identity로 교체됐다.

| 후속 artifact | SHA256 |
|---|---|
| source-selection manifest / embedded build ID | `2d251b474ae988a976bc18abaa497444237a58461013b468a5351c4b1f4b9650` |
| 새 frontend archive | `a1cb9a4f7598cbeb2ef6bf6ad427ae80956a6831e90ce0122d8ce6375ad82240` |
| 새 scu_host_dispatch | `150ab2f0adccca3ac29dcf928f60bde2dcd2db13db067724066c918948b1e253` |

- `review-native-02`: 새 frontend를 실제 S2 simulator에 링크하여22+48=70 numerical
  invocation, raw70/f_out70 exact, invalid metadata12 거부 PASS.
- `ggml-ifr3-reviewed-tail-01`: 새 host → 동일 production UART RTL,
  M17/N19/K64 서로 다른 입력2회, run/generation1→2, raw646/f_out646 exact PASS.
  각 invocation work4/fragment16/C write·ACK34, cycle1949.
  Decoder가 확인한 wire padding은221 lanes씩, 총442개다. Caller tensor는 contiguous이며
  별도 stride sentinel 검증은 위 portable replay 결과와 구분한다.
- `s2-raw-m9-01`: 기존 S2 native archive의 raw-core Bypass M9/N1/K1,
  서로 다른 context/input4회, raw36 exact, 각 work1/fragment1/write·ACK9/cycle158 PASS.
  Native Q8_H1 alignment 지원이나 resident-provider passive monitor 결과가 아니다.

새 source의 완전한 freeze/native/board 재빌드와25MHz route는 공간 확보 후 수행해야 한다.
후속 reviewed host의 긴 live/지연 진단은 아직 NOT RUN이다. 이전 host의 live PASS를
이 새 executable의 실행 결과로 바꾸어 기록하지 않는다.

`preservation-final.json`에서 변경 전 tracked blob331개를 `baseline/source.tar` 안에서
동일 hash로 확인했고 historical bitstream/DCP/production RTL/host executable2개/host manifest
총6개를 다시 hash 비교했다. 현재 수정된 root 경로가 모두 불변이라는 주장은 하지 않는다.
과거616개 artifact 전체를 이번에 다시 검사했다고도 표시하지 않는다.

최종 Git 기록은 실험 경로의 `final-git/{core,host,params}/`에 있다. 현재 IM2P는
`fix/scu-block-scale`, HEAD는 기준 `3aeb5feee6872f88ec1f6a5dc0d77fb1bb8babf8` 그대로다.
관련 tracked 수정과 새 SCU source/test/fixture/report는 unstaged 상태다. Host/include
원본 checkout의 tracked 변경은 없고 기존 untracked model 경로를 보존했다.
세 저장소의 `git diff --check`가 통과했으며 staging/commit/push는 하지 않았다.
이번 작업의 실제 장치 접근·programming·실보드 GEMM·flash write는 모두0회다.

최종 source hash는 `source-provenance-final.json`과 `final-source-sha256.json`에서
초기 manifest와 분리한다. 새 carrier-edge case 생성 옵션을 추가한 `golden.py`는
초기 fixture 생성 당시 파일과 전체 hash가 다르다. `golden-evolution.json`에서
G1/G2 및 metadata decode/clamp 함수 본문이 동일함을 확인한다. 과거 fixture의
생성 provenance나 expected를 새 DUT 출력으로 덮어쓰지 않았다.

주요 실제 실행 명령과 exit/marker는 다음 원본 증거에 보존했다. 실험 경로는
이 문서 첫머리의 `scu-block-scale-20260910T053546Z`를 기준으로 한다.

| 검사 | 명령 / 결과 파일 |
|---|---|
| Fresh frozen native/host/production RTL | `integration-01/*command.json`, `integration-01/production/*.status.json` |
| 수정 전 실행 위치 FAIL | `legacy-repro-02/`의 실행 로그와 상태 |
| SCU passive 내부 관측 | `s2-core-monitor-03/commands.json` 및 trace/result |
| 동일 기존 입력의 새 G1/G2 FULL/PIPELINE | `s2-portable-01/commands.json`, `status.json` |
| 실제 ggml K96 FULL / K64 live | `ggml-ifr3-full-k96-01/commands.json`, `ggml-ifr3-live-k64-02/commands.json` |
| 후속 host 명시적 재링크 | `review-linked-02/commands.json`, `dependency-provenance-audit.json` |
| 후속 frontend70건 | `review-native-02/command.json`, `status.json` |
| 후속 host tail2건 | `ggml-ifr3-reviewed-tail-01/commands.json`, `status.json`, `padding-evidence.json` |
| Raw M9/N1/K1 | `s2-raw-m9-01/`의 command/status |

디스크 공간 확보와 residual saturation 정책 응답을 기다린다. 현재 후보는 전체
수정 완료나 programming 승인 준비 완료 상태가 아니다.

## 재현 명령

새 checkout에는 고정 base Git object와 pinned host/include repository, 설치된
BSC/Verilator/C++/Rust/CMake/Python이 필요하다. 이전 experiment archive는 입력이 아니다.

```sh
python3 fpga/scu_block_scale/build.py freeze /absolute/fresh-snapshot \
  --host-repo /absolute/pinned-host --params-repo /absolute/pinned-include
python3 fpga/scu_block_scale/build.py native /absolute/fresh-snapshot
python3 fpga/scu_block_scale/build.py host /absolute/fresh-snapshot --jobs 2
python3 fpga/scu_block_scale/build.py verify /absolute/fresh-snapshot
```

`refresh-core-patch`는 개발 중 명시적 editable base와 현재 src delta를 갱신하는
명령이다. Freeze/build는 versioned patch를 사용하며 `git archive HEAD`에 의존하지 않는다.
S1 snapshot의 revision은 `scu-wrap-staging-v1`이다. 최종 production revision은
SCU와 accumulator saturation까지 검증한 뒤 `signed-scu-sat-v2`로 구분한다.
