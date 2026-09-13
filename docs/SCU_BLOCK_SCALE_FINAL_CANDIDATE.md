# SCU H1 final candidate 실행 기록

최신 상태: **fresh build / DIM64 / M9 / ggml·IFR3 /25MHz route PASS, bitstream 생성 완료**.
[최종 implementation 결과](#final-implementation-완료--2026-09-10)와 문서 마지막 승인 경계를 참조한다.
**Board measured: NOT RUN.** 새 package의 별도 실행 승인을 기다린다.
아래 공간 부족·monitor 중단 기록은 이전 시도의 원본 이력이다.

2026-09-10 UTC. 이번 실행 상태는 **BLOCKED_DISK_CAPACITY**다. 새로운 build,
regression, implementation 또는 bitstream은 생성하지 않았다. 아래 이전 수치 결과는
[개발 보고서](SCU_BLOCK_SCALE_FIX.md)의 과거 근거이며 이번 final build의 PASS가 아니다.

## 1. Disk / Build location

시작 Git 상태, binary diff와 filesystem 원본 출력:

`/tmp/scu-final-preflight-20260910T073211Z/`

- Repository: `/home/youngshin/aisa/RISCV-DynDNN/IM2P/IM2P.sim`.
- 실제 filesystem: `/dev/sda3`, ext4. `df -hT`는 전체454G, 사용430G, 여유25M, 사용률100%다.
- Gate 측정값: available **25,849,856 bytes = 24.65 MiB**.
- 필요한 운영 여유: **32,212,254,720 bytes = 30 GiB**. Gate FAIL.
- Inode 사용률13%다. 이번 제한은 inode가 아니라 사용 가능한 byte 용량이다.
- `/`, repository, `/home/youngshin`, `/tmp`, `/srv`, `/mnt`, `/tools` 모두 같은 장치다.
  조사한 경로에서 별도 build filesystem을 찾지 못했다.
- Final build RUN과 TMPDIR는 만들거나 설정하지 않았다. 위 `/tmp` 경로는 작은
  preflight 증거만 저장하며 충분한 별도 build 공간이 아니다.
- 파일 삭제·압축·기존 압축본 해제는0회다.

상위 깊이를 제한한 `du -x -B1 --max-depth=2` 조사 결과:

| 경로 | 할당 용량 |
|---|---:|
| `build/` 전체 | 16.746 GiB |
| `build/experiments/` 전체 | 15.761 GiB |
| `build/experiments/scu-block-scale-20260910T053546Z` | 2.123 GiB |
| `build/experiments/dense-pipeline-20260909T141302Z/stream-02` | 0.435 GiB |
| `build/experiments/host-full-replay-20260909T103729Z/candidate-01` | 0.492 GiB |

이 경로들은 서로 포함 관계가 있으므로 합산하지 않는다. 보존해야 할 source,
fixture, executable/archive, 실패 로그, bitstream/DCP가 섞여 있다. 디렉터리 전체를
삭제 대상으로 제안하지 않는다. `build/` 전체 용량도30 GiB보다 작으므로 이곳의
일부 중간 파일 정리만으로 이번 gate를 충족할 수 없다.

삭제 검토 후보는 기존 Verilator `obj_dir`의 재생성 가능한 `*.gch` 캐시다.
구체적인 파일, 할당 byte와 hardlink 조건은 preflight의 `cleanup-candidates.json`에
기록한다. 보존 대상인 generated RTL/C++, linked archive/executable, manifest와
원본 로그는 제외한다. 삭제는 실행하지 않았으며 사용자 승인 전에는 실행하지 않는다.
이 후보 정리는 보조 수단이다. 다음 build에는 실제30 GiB 이상 여유가 있는 filesystem이
필요하며 가능하면40 GiB 이상을 확보한다.

`O=build/experiments/scu-block-scale-20260910T053546Z`일 때 우선 검토 후보는 다음과 같다.
디렉터리 전체가 아니라 표시한 `*.gch`만 해당한다.

| 파일군 | 개수 | 실제 할당 byte |
|---|---:|---:|
| `O/integration-01/production/obj_dir/*.gch` | 2 | 198,680,576 |
| `O/s2-board-01/production/obj_dir/*.gch` | 2 | 198,680,576 |
| `O/{s2-core-monitor-01,s2-core-monitor-02,s2-core-monitor-03,s2-boundary-mutation-01}/obj_dir/*.gch` | 8 | 574,963,712 |
| 합계 | 12 | 972,324,864 |

모두 서로 다른 inode이며 link count1이다. 승인 후 삭제할 경우 예상 확보량은
약927.28 MiB로, gate 충족에는 부족하다. 일부 과거 input manifest는 이 캐시의 hash도
기록하므로 향후 삭제한다면 원본 manifest를 보존하고 availability 변경을 별도 기록해야 한다.
현재는 이12개도 그대로 보존했다. Cargo `.o`와 DIM64 generated 입력은 보류군으로
분류했으며 우선 삭제 제안에서 제외했다.

## 2. Source identity

- Branch: `fix/scu-block-scale`.
- HEAD: `3aeb5feee6872f88ec1f6a5dc0d77fb1bb8babf8`.
- 시작 tracked 수정57개와 기존 SCU untracked source/test/fixture/report를 유지했다.
- Host pin: `7a74ac29c9c8fa8fbc5d47cf073b8b9266e3e7a9`.
- Include pin: `cd2caed8d93e49d3f8873bd6c4e340fcad54b9e0`.
- Host/include tracked 변경은 없다. Host의 기존 untracked model 두 경로를 보존했다.

현재 editable source에서 frontend의 `column_offset == SIZE_MAX` 사전 거부와
UART의 published rows 저장 후 first-A cycle 공개 순서를 확인했다. ABI5,
`signed-scu-sat-v2`, IFR3 H1/final-domain capability `0x0294`도 소스에서 확인했다.
이번에는 이 확인을 새로운 linked/generated artifact 검증이라고 부르지 않는다.

현재 source401개 hash를 `reviewed-source-before.json`에 기록했다. 기존 고정 base,
FULL patch, SCU delta, 명시적 overlay의 재현 방식은 유지하며 patch를 갱신하지 않았다.
새 authoritative freeze와 final source-selection manifest는 공간 gate 뒤 NOT RUN이다.

## 3. Fresh build

`freeze`, `native`, `host`, `verify` 모두 **NOT RUN**이다. 이전 `integration-01`과
`review-linked-02` archive를 새로운 final artifact로 복사하거나 재분류하지 않았다.
Final simulator/frontend/host hash는 아직 없다.

## 4. DIM / cross-profile

A8 D16/D32/D64, A4 D16, A16 D16의 이번 final runtime 검사는 모두 **NOT RUN**이다.
이전 DIM64 ENOSPC를 numerical FAIL로 재분류하지 않는다. INT20/21/22 및 기존
typed metadata/saturation 계약을 변경하지 않았다.

## 5. Final RTL numerical

새 final source의 factor1/256, final8224, carrier edge, saturation, 전체 Cargo RTL
회귀와 review-fix runtime 재검사는 **NOT RUN**이다. 과거163/163 또는 frontend70건을
이번 실행 횟수에 포함하지 않는다. 이번 신규 numerical invocation은0회다.

## 6. Final ggml / IFR3

새 host와 새 production RTL의 K64/K96 FULL, live stripe160/160/1, tail/연속 invocation은
**NOT RUN**이다. 새 cycle reference나 payload/callback 측정값은 없다.

## 7. FPGA implementation

Synthesis/opt/place/route/bitstream 모두 **NOT RUN**이다. 새 utilization, timing,
DRC/CDC, congestion 또는 warning 결과는 없다. 과거 IFR2/SCU simulation 값을 새 route
결과로 사용하지 않는다. Gate 실패 뒤 Vivado를 시작하지 않았다.

## 8. Final artifacts

새 production RTL/DCP/bitstream/host/cycle-reference/approval manifest는 **없다**.
현재 문서는 programming 승인 package가 아니다. Build 공간 확보 전에는 후보가
완료됐다고 판정할 수 없다.

## 9. Support matrix

| 범위 | 현재 범위 / 이번 final 검증 |
|---|---|
| Dense H1 simulator | 기존 구현 Supported, 이번 final build NOT RUN |
| Dense H1 FPGA FULL/PIPELINE | 기존 RTL simulation 근거 있음, 이번 Routed 아님 |
| HP1 simulator | 기존 구현 Supported, 이번 final build NOT RUN |
| HP1 FPGA | Unsupported, 기존 capability/start 거부 유지 |
| Residual CPU-side | 이번 작업에서 변경 없음 / out of scope |
| Residual FPGA | Out of scope, 기존 요청 거부 유지 |
| DIM64 runtime | 이번 NOT RUN |
| Board measured | NOT RUN |
| PPL/model quality | NOT RUN / out of scope |

Residual 정책과 FPGA HP1은 이번 candidate의 blocker 또는 완료 조건이 아니다.
현재 실제 blocker는 filesystem free-space gate 하나다.

## 10. Preservation / Git / Board

이번 preflight에서 `baseline/source.tar`의 원본331 blob을 기존 hash와 비교했다.
Historical bitstream/DCP/production RTL/host executable2개/host manifest, 총6개도
전체 SHA256을 다시 비교해 일치했다. 근거는 `preservation.json`이다.
이는 현재 수정된 root 파일이 모두 과거와 동일하다는 뜻이 아니다.

시작·최종 status/stat/check/binary diff/cached diff를 preflight에 저장한다.
`git diff --check` PASS, staged 변경0이다. 이번 source/RTL/host 수정은 없으며
새 문서는 이 보고서뿐이다.
`final-state.json`은 시작 source401개가 모두 동일 hash로 남았는지와 마지막 여유 공간을
기록한다. Build는 시작하지 않았으므로 build 후 용량 측정값은 없다.

Staging/commit/push, reset/stash/clean, 새 합성/route, JTAG/UART 장치 open/CAP,
programming/실보드 GEMM, flash write는 모두0회다.

다음 최소 작업은 실제30 GiB 이상 여유 공간 확보 또는 충분한 별도 filesystem 경로
지정이다. 그 뒤 새로운 RUN에서 freeze부터 재개한다. 기존 증거를 삭제하며 build를
반복하지 않는다.

## 새 ext4 attempt — 2026-09-10T08:13:11Z

이전 `BLOCKED_DISK_CAPACITY` 기록은 위에 보존한다. 새 build filesystem의 공간과
쓰기 조건은 통과했지만, 이번 지시의 별도 root 여유 조건에서 중단했다.
현재 상태는 **BLOCKED_ROOT_HEADROOM**이다.

- 실제 host mount: `/mnt/fpga-build`, `/dev/sda1`, ext4, `rw,relatime`.
- Owner: `youngshin:youngshin`, mode755.
- Probe 당시 build filesystem available: **469,931,028,480B = 437.66 GiB**.
- youngshin UID1001로 probe를 생성하고 write/sync/read 검증 후 그 probe만 제거했다.
  Cache나 기존 자료는 삭제하지 않았다.
- Root `/dev/sda3` available: **84,148,224B = 80.25 MiB**. 요청한 최소 수 GiB 여유에
  미달한다. 이 조건을 건너뛰어 native/DIM64/Vivado를 실행하지 않았다.
- Sandbox는 새 mount를 read-only로 보여 줬으나 실제 host namespace에서 rw를 확인했다.

새 RUN은 `/mnt/fpga-build/im2p/scu-final-20260910T081311Z`다. `evidence/`에
시작 status/branch/HEAD/stat/check/binary diff/cached diff와 `preflight.json`을 저장했다.
`tmp/`는 준비했지만 build를 시작하지 않았고 새 object/archive/RTL을 생성하지 않았다.
기록 후 build filesystem available469,930,856,448B, root83,308,544B였다.

Branch와 HEAD는 `fix/scu-block-scale`, `3aeb5feee6872f88ec1f6a5dc0d77fb1bb8babf8`
그대로다. Tracked 수정57개, staged0, `git diff --check` PASS다. 이번 probe에서
baseline331 blob/historical6개/current401개 전체 hash를 다시 검사하지 않았으며,
그 숫자를 새 보존 검사 횟수로 사용하지 않는다.

새 freeze/native/host/verify, cross-profile runtime, final numerical/ggml regression,
synthesis/place/route/bitstream/approval package는 모두 **NOT RUN**이다.
Residual 및 FPGA HP1은 out of scope이며 이번 blocker가 아니다.

확인된 root 정리 후보는
`/home/youngshin/.cache/vscode-cpptools/a3479aa07bace140f26ffdcc5ddc2e4f/.browse.VC.db`
한 파일이다. 이번 stat에서 actual allocation13,072,699,392B, link count1이었다.
사용 여부를 이번에 다시 확인하지 않았고, 삭제 승인이 없어 그대로 보존했다.
Cache 정리가 승인된다면 사용 상태를 재확인한 뒤 승인된 파일만 처리해야 한다.

Cache 삭제·압축·자료 이동·staging/commit/push·JTAG/UART·CAP·programming·실보드 GEMM·
flash write는0회다. Root에 수 GiB 이상의 여유가 확보된 뒤 fresh build를 재개한다.

## Fresh attempt — 2026-09-10T08:27:24Z

상태: **BLOCKED_PASSIVE_MONITOR**. Fresh build와 아래 완료 검사는 이번 실행 결과다.
이전 `integration-01`/`review-linked-02` PASS를 재사용하지 않았다.
M9/N1/K1 production/passive 검사 실패 뒤 추가 실행과 Vivado를 시작하지 않았다.
이 문서는 programming 승인 package가 아니다.

### Disk / 출력 위치

```text
RUN=/mnt/fpga-build/im2p/scu-final-20260910T082724Z
FINAL=$RUN/candidate-01
TMPDIR=TMP=TEMP=$RUN/tmp
```

실제 host namespace의 mount는 `/dev/sda1`, ext4, `rw,relatime`이다.
Sandbox가 같은 경로를 read-only로 표시하므로 승인된 RUN command wrapper를 host
namespace에서 실행했다. BSC/Verilator/Cargo/CMake 및 Python/XDG 임시·cache 출력은
새 filesystem 아래에 격리했다. 도구 설치 경로는 기존 것을 사용했다.

| 시점 | root available bytes | build filesystem available bytes |
|---|---:|---:|
| 새 RUN 시작 | 13,149,818,880 | 469,930,012,672 |
| fresh build 후 checkpoint | 13,145,276,416 | 469,674,881,024 |
| 실패 후 보존 검사 | 13,144,133,632 | 468,369,305,600 |

마지막 `du -sh "$RUN"`은1.5G였다. 공간 부족으로 중단한 것이 아니다.
추가 disk cleanup은0회다. 앞 단계에서 별도 승인받아 삭제했던 IntelliSense DB는
마지막 관측에서도 재생성되지 않았다. 기존 real-lib cache recipe가 **이번 새 transaction의
임시 staging**을 정리하는 동작과 기존 자료 삭제를 구분한다.

### Source / fresh build identity

Branch는 `fix/scu-block-scale`, HEAD는 `3aeb5feee6872f88ec1f6a5dc0d77fb1bb8babf8`다.
기존 tracked 수정57개와 SCU untracked 자료를 보존했다. Staged 변경은 없다.

선택 순서는 `dc4a1a621f63834d64df42ae8c24152747d97971` archive → FULL fixed patch →
SCU core.patch → 명시 overlay → pinned host archive와 host/producer/SCU companion patch →
pinned include다. 현재 root RTL 전체를 overlay하지 않았다. `core.patch`는 현재 reviewed
src delta와 byte-exact여서 갱신하지 않았다.

- Host pin: `7a74ac29c9c8fa8fbc5d47cf073b8b9266e3e7a9`.
- Include pin: `cd2caed8d93e49d3f8873bd6c4e340fcad54b9e0`.
- 두 sibling의 tracked 변경0, 기존 host model directories 보존.
- ABI5 / IFR3 / `signed-scu-sat-v2` / unsigned H1 / final integer domain2.
- Reviewed frontend extent 및 UART rows-before-cycle 수정 포함.
- Board A1 Arithmetic·두 scheduler·activation-response capacity guard·S2 SCU delta 포함.
- 권위 있는 `integration-sha256.json`의1,746개 입력을 freeze 후 및 중단 후 전수 검증했다.
  `source-sha256.json`은 앞선 FULL freeze의 중간 기록이다.

실제 명령은 `$RUN/evidence/{01-freeze,02-native,03-host,04-verify,05-identity}.*`에
argv/cwd/env/start/end/exit/stdout/stderr로 보존했다.

```sh
python3 fpga/scu_block_scale/build.py freeze "$FINAL" \
  --host-repo /home/youngshin/aisa/RISCV-DynDNN/IM2P/llama.cpp-gemmini \
  --params-repo /home/youngshin/aisa/RISCV-DynDNN/IM2P/RISC-V-DynDNN-gemmini-include
cd "$FINAL"
python3 source/fpga/scu_block_scale/build.py native "$FINAL"
python3 source/fpga/scu_block_scale/build.py host "$FINAL" --jobs 2
python3 source/fpga/scu_block_scale/build.py verify "$FINAL"
```

네 단계 모두 exit0. Native cache는 `state=rebuild reason=missing`이며, 과거 experiment의
`.a/.o`를 입력으로 사용하지 않았다. Cargo의3개 crate source만 Cargo.lock checksum과
대조해 새 cache에 준비했다. Host/frontend/simulator 모두 이번 RUN에서 컴파일했다.
도구는 BSC2026.01/9bd39e6f, Verilator5.051 devel(mod), GCC11.4.0, CMake3.22.1,
Rust/Cargo1.91.1, Python3.11.6이며 원본 버전 출력은 `tool-versions.log`에 있다.

**Native generated RTL의 byte hash는 not_collected/not_retained다.** 기존 real-lib cache는
archive2개와 manifest를 publish하고 transaction staging을 제거한다. source/config/tool
fingerprint·새 컴파일 로그·archive hash는 보존됐지만 native 생성 Verilog/C++/.o는 남지 않았다.
아래 별도 Cargo 생성 RTL의 hash를 native 생성 RTL hash로 소급하지 않는다.
이번에 cache 동작을 수정하거나 이미 끝난 native build를 재실행하지 않았다.

### Cross-profile runtime

실제 명령은 frozen source cwd의 `make -j2 bsv-test-one TOP=mkTbScuProfile`이다.
각각 별도 `BUILD_DIR=$RUN/cross-profile/<profile>`, 해당 `SCU_*` define,
기존 BSC step/stack flags, 1800초 command timeout을 사용했다.

| Profile | Partial / accumulator | 완료 GEMM | Scalar exact | Works | Fragments | Writes / ACK |
|---|---|---:|---:|---:|---:|---:|
| A8/D16 | 20 / 32 | 3 | 331 | 7 | 25 | 40 / 40 |
| A8/D32 | 21 / 32 | 3 | 1,163 | 7 | 13 | 72 / 72 |
| A8/D64 | 22 / 32 | 3 | 4,363 | 7 | 13 | 136 / 136 |
| A4/D16 | 12 / 32 | 3 | 331 | 7 | 25 | 40 / 40 |
| A16/D16 | 36 / 64 | 3 | 331 | 7 | 25 | 40 / 40 |
| 합계 | | 15 | 6,519 | 35 | 101 | 328 / 328 |

모두 실제 Bluesim runtime PASS. D64는520.19초에 build/runtime을 완료했다. DIM64 내부
tile의 partial 폭, sign extension, K32 metadata 경계, saturation, final scalar 수를 검사했다.
물리 D32/D64 구현이나 host f_out 검사가 아니다. Assertion-enabled/provider stall bench의
cycle을 production board cycle reference로 쓰지 않는다.

기존 bench에는 A8/D16 선택자가 없어 테스트용 define 분기만 추가했다. 원본·수정본·diff는
`evidence/cross-profile-bench/`에 보존했고 DUT source는 바꾸지 않았다.
경고 집계 regex 누락은 원본 JSON을 보존한 `cross-profile-results-02.json`에서 수정했다.
각 profile의 G0009=20/G0010=35/G0036=9/G0117=36, 추가 G0024는 D32=1/D64=7이다.
수치 검사를 재실행하거나 경고를 숨기지 않았다.

### Final numerical / protocol 결과

| 실제 새 실행 | 결과 / 단위 |
|---|---|
| `make sim-test-a8-w8-d16`, 새 BUILD_DIR | 29 test binaries,163/163 PASS, failed/ignored/filtered0 |
| FULL22 + H1 carrier48 | independent golden raw/f_out70개 exact, padding140, 실행 전 metadata 거부12 |
| H1/HP1 FULL·deterministic PIPELINE | 완료4, raw/f_out 각61,632, padding65,484 exact; 의도된 partial1에서 caller 보존 |
| Passive production core monitor | 6 cases, SCU commit24, accumulator BRAM write24, final raw6 PASS |
| Descriptor/provider/frontend mutation | control2 PASS, 의도한 fault6 + shared-S 중복 source mutation1 검출 |
| Lowered RTL block-reset mutation | 첫 partial invocation의 contribution2 뒤 예상 실패 검출; production source 불변 |
| UART PTY | 20/20 PASS |
| FULL fail-fast/provenance PTY | 21/21 PASS; 의도한 오류 뒤 다음 장치 명령0 |
| Host companion | route assertion104, legacy callback5회, CMake probe4건; 서로 다른 단위로 기록 |
| Frontend mock | 기존37개 + HP1 selector PASS |

실제 frontend `h1_boundary`: op4, metadata1/256, raw8224, domain2, callback1,
f_out bits1107329024(`0x42008000`,32.125), padding 보존. `golden.py --expected … --check …`로
독립 비교했다. Frontend callback 관측을 내부 SCU 관측으로 대신하지 않았다.
별도 passive core monitor에서 metadata1/256, partial16, contribution16/16/4096/4096,
누산16/32/4128/8224, final BRAM/FFI raw8224를 직접 확인했다.
Carrier0/1/255/256/65535/65536/65790, signed multiply/shift 및 accumulation saturation도
새 RTL과 golden에서 검사했다.

정상 frontend numerical 합계는 완료74 invocations, raw/f_out 각61,702회,
padding65,624회다. Mutation의 의도된 실패와 부분 실행은 별도이며 이 합계에 넣지 않는다.
각 계층의 test·GEMM·scalar·callback 횟수를 합쳐 하나의 test 수로 표현하지 않는다.

Reviewed fix의 추가 test-only runner 두 개를 versionable 위치에 남겼다.

- `fpga/scu_block_scale/test_first_a_ordering.py`: 동일 valid PTY response에서 이전 cycle-first
  mutant는 cycle101/rows0로 실패. 수정본은 중간 cycle0/rows16, 최종101/16으로 PASS.
  각 CAP/BEGIN/PUBLISH3개 이후 명령0. Observer thread를 두 store 사이에 배치한 결정적
  검사이며 자연 race/overlap 성능 측정이 아니다. 최초 `first-a-ordering-01`의 C++ test
  인자 평가 순서 오류와 로그는 보존했고, test-only 수정본 `02`에서 검사했다.
- `fpga/scu_block_scale/test_output_extent.py`: guard 제거 mutant는 SIZE_MAX에서
  executor1로 의도된 FAIL. Reviewed는 SIZE_MAX/MAX-1/MAX-2에서 executor0·simulator0·
  caller sentinel 보존. 인접 두 경계는 mutant도 기존 다른 guard로 거부한다.

두 runner는 freeze 후 추가된 검증 도구이며 별도 input hash로 봉인했다. Final DUT 입력을
수정하지 않았다. 추가 portable8-input cohort은 frozen legacy fixture12개 누락으로 NOT RUN.
해당 root tracked 원본은 모두 존재하고 hash가 일치한다. 목록은
`evidence/optional-portable-input-audit.json`이다. 필수 ggml의 seed 기반 실제 quantizer 입력
생성 경로는 이 파일들에 의존하지 않으며 frozen tree를 임의 보강하지 않았다.

### 중단 원인: M9 passive monitor packing

`14-activation-guard.status.json`: exit1,61.6876초.

- Production/plain: M9/N1/K1 4/4 jobs,36 outputs, hold 포함108 comparisons PASS. 각162 cycles.
- Production/passive: 첫 job tick41에서 `activation monitor tick 41: work bounds` FAIL.
  완료 job/output0, response return/publication0.
- Assertion-enabled cohort: NOT RUN. 실패 뒤 수정·재실행·다음 cohort0.

| 필드 | 현재 generated RTL | 기존 observer |
|---|---|---|
| iCount | `[706:675]` | `[705:674]` |
| stripeContext | `[834:771]` | `[833:770]` |
| stripeId | `[866:835]` | `[865:834]` |

`VectorOp`가2bit에서3bit로 늘었지만
`tests/scu_block_scale/activation_guard/resident_shape.cpp`의 고정 bit offset은 이전 값이다.
`WorkTypes.bsv`의 pack layout과 실제 generated RTL의 소비 slice가 이를 확인한다.
iCount9/jCount1은 이전 decoder에서 `(9 << 1) | (1 >> 31) = 18`로 해석된다.
**18은 정적 추론**이며 원본 trace에 required_rows 자체는 기록되지 않았다.
실제 tick41 관측은 current_slot0/feed_row0/state2/context2/pending0이다.
검사는 activation response가 발생하기 전에 실패했다. DUT 수치/activation capacity guard
위반을 입증한 실패가 아니지만, passive/assertion 회귀를 PASS로 처리할 수도 없다.

원본은 `validation/activation-guard/{summary.json,commands.json}` 및
`production/{plain,passive}/{run.log,trace.jsonl}`에 있다. 실패 trace SHA256:
`53fafec247d4806aa84307a7c890758e5ea188a86f6b4581ad56e8a162316d32`.
사용자의 첫 오류 중단 조건에 따라 observer를 고치거나 성공할 때까지 재실행하지 않았다.

### FPGA / cycle / artifact 상태

Fresh `host-fpga`, production `bsc`, UART `sim` compile은 exit0이다. UART build는
guard 실패 전 시작됐고 정상 종료했지만 새 host/RTL invocation은 아직 실행하지 않았다.
따라서 M16/N16/K64 FULL, M321/N48/K96 FULL, M321/N48/K64 live160/160/1,
M17/N19/K64 tail·서로 다른 연속2 invocation은 이번 RUN에서 모두 **NOT RUN**이다.
앞선 deterministic frontend PIPELINE을 실제 ggml live producer 검사로 재분류하지 않는다.

새 FULL cycle reference는 없다. 과거377/617/1949/52687이나 IFR2의361/39907/58695를
새 final reference로 고정하지 않았다. 위 M9 및 cross-profile cycle은 다른 wrapper/provider
서비스의 관측값이다.

Vivado synth/opt/place/route/bitstream은 **NOT RUN**. 새 LUT/FF/BRAM/DSP, slice,
fanout/congestion, WNS/WHS/pulse, DRC/CDC, unconstrained endpoint 결과가 없다.
동일25MHz 목표의 Tcl route 관측 wrapper만 준비하고 격리 stub6건을 검사했다.
실제 Vivado 실행·constraint 변경·기존 DCP의 incremental 재사용은0회다.

| 이번 artifact | 전체 SHA256 |
|---|---|
| Frozen integration manifest | `607827f129c61b60dffd9e9fb02fd0ceb4a7ab2abbeb25bd6b08d4d9b2782d69` |
| Native simulator archive | `0cf3a3c0eeada8cfa25520952d9c746f397d968a51a504e4fd8fb1a1b9d87709` |
| Native frontend archive | `a1cb9a4f7598cbeb2ef6bf6ad427ae80956a6831e90ce0122d8ce6375ad82240` |
| scu_frontend_final | `77fbb7e86a66cce760b829e35bbdba3103556f24944116e27a0a174176647f54` |
| scu_host_dispatch | `0ca9b6465ba69682ac0b4a9a234d21ee8728f8682efd41c89eab02c5f0873f5f` |
| production mkScuPipeline.v | `1833a6a4f53e48fa18f0afb188fbd2858a7584376701a333374025174d061c26` |
| Vscu_uart_shell | `52cf181b1523a9d0c320312c073e1482912f26071c73e1d2de32cd1ba35ed050` |
| 별도 Cargo mkSynthA8W8D16.v | `dc1e46a36d9e9086703d9f819622e57ac4d3c90ee11f9cfd382e6da966a1317a` |
| Routed DCP / bitstream / FULL cycle reference | NOT RUN / 없음 |

크기·선택 경로·추가 source/test hashes·최종 disk 및 보존 증거는
`evidence/partial-artifacts.json`에 있다. 이는 실패 시점의 부분 artifact 기록이며
immutable routed candidate 또는 board approval package가 아니다.

### Support / 보존 / 다음 최소 작업

| 범위 | 이번 상태 |
|---|---|
| Dense H1 simulator | Fresh FULL/PIPELINE numerical PASS |
| HP1 simulator | Fresh FULL/PIPELINE 및 typed raw-core 범위 PASS |
| Dense H1 FPGA FULL/live PIPELINE | Fresh RTL/host compile 완료, 최종 UART numerical·Route NOT RUN |
| FPGA HP1 | Unsupported |
| Residual CPU-side | unchanged / out of scope |
| Residual FPGA | out of scope |
| DIM64 | 실제 assertion-enabled Bluesim runtime PASS |
| Board measured / PPL | NOT RUN |

Baseline source.tar의331개 blob 및 historical artifact6개의 full hash를 시작/종료에 다시
확인했다. 현재 경로가 옛 baseline과 같다는 뜻이 아니라 원본 blob을 동일 bytes로 보존했다는
뜻이다. Frozen1,746개 입력은 불변이다. 기존 disk failure·fixture·expected·bitstream/DCP·
log를 삭제하거나 덮어쓰지 않았다. 이번 source 변경은 A8/D16 bench 선택자, 위 test-only
runner2개와 이 보고서다. Numerical RTL·host production·core.patch 변경0.

`git diff --check` PASS. 시작/종료 status/stat/binary/cached diff는 RUN/evidence에 보존한다.
최종 보존 집계의 첫 script는 manifest의 `{sha256, bytes}` record를 hash 문자열과
비교하는 형식 오류로 실패했다. 그 로그를 보존하고 `sha256` 필드 비교로 집계만 정정했다.
Source/test/build 재실행이나 DUT 수정은 하지 않았다.
Git staging/commit/push/reset/stash/clean0. JTAG·physical UART open·CAP·SRAM programming·
board GEMM·flash write0. PTY protocol 검사는 실장치 실행량에 포함하지 않는다.

다음 최소 작업은 observer의3개 packed field를 ABI5 layout에 맞추고 layout 검사를 추가한 뒤,
새 결과 경로에서 M9 production/passive/asserted를 재검증하는 것이다. SCU saturation이나
scheduler를 바꿀 근거는 없다. 이 gate가 통과한 뒤 최종 ggml/IFR3 입력들을 실행하고 새
cycle reference를 확보한 다음25MHz implementation으로 진행해야 한다.

## Monitor overlay 승인 후 재개 — 2026-09-10

이 section은 위 BLOCKED 기록 이후의 별도 실행이다. Monitor와 최종 ggml/IFR3 gate는 PASS이며, 아래 기록 시점의 Vivado는 진행 중이다. 최종 implementation 판정은 이어지는 결과 section을 기준으로 한다.

### 승인된 monitor layout 수정과 M9 gate 재검증 — 2026-09-10

상태: **Implemented / Simulated PASS**. 변경은 versionable test overlay에만 남겼다. Frozen `candidate-01`의 integration manifest SHA256 `607827f129c61b60dffd9e9fb02fd0ceb4a7ab2abbeb25bd6b08d4d9b2782d69`와 입력 1,746개는 불변이다. 기존 실패 artifact 127개도 동일 hash로 보존했다. 기존 frozen manifest를 갱신하거나 DUT를 편집하지 않았다. [Overlay·보존 manifest](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/evidence/monitor-layout-01/overlay-manifest.json), [before/after patch](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/evidence/monitor-layout-01/monitor-overlay.patch).

`WorkTypes.MatmulWork#(16)`의 실제 pack 폭은 899bit다. `VectorOp` 3bit 이후에 있는 필드에 옛 offset을 적용한 observer 오류를 수정했다. 같은 record를 읽는 필드는 각각 검증했으며 전체 offset에 일괄 보정을 적용하지 않았다.

| 필드 | 수정된 offset/width | 실제 bit slice | 검증 근거 |
|---|---|---|---|
| `iCount` |675/32|[706:675]|실제 BSV pack + production/assertion RTL 소비 slice|
| `jCount` |643/32|[674:643]|실제 BSV pack + production/assertion RTL 소비 slice|
| `stripeContext` |771/64|[834:771]|실제 BSV pack; 해당 provider의 소비 논리에서는 최적화됨|
| `stripeId` |835/32|[866:835]|실제 BSV pack; 해당 provider의 소비 논리에서는 최적화됨|
| `vectorOp` |64/3|[66:64]|독립 pack fixture와 generated RTL의 layout 교차검증|

`work_layout.hpp`에 이름 있는 정의와 899bit/29word·field 범위 검사를 두었다. Runner는 실제 generated `matrixWorkReg[898:0]`, iCount/jCount/vectorOp 소비 slice와 BSV oracle을 확인한 후에만 observer를 컴파일·실행한다. Observer는 검증된 폭을 compile definition으로 받아 startup에 검사한다. 기존 `matrixStateReg <= 1`에서는 required rows를 0으로 보는 유효 조건을 유지했다. Trace에 layout identity, iCount/jCount, stripe/context, work active, required rows를 추가했고 기존 slot/feed/response/pending/publication 관측값을 유지했다.

변경 파일의 전체 SHA256은 다음과 같다. Before는 변경하지 않은 frozen candidate, after는 root의 versionable test overlay다.

| 파일 | Before SHA256 | After SHA256 |
|---|---|---|
|`activation_guard/resident_shape.cpp`|`971040a21dafc5c1c7bf4b5118b767c9c42bb39fe5263ffd98012d52c14b523a`|`eb975910cfc10269926309d801a52202bc922cdf4766863d52c82d4b62240040`|
|`run_activation_guard.py`|`e2ac475560658f9d9ad9aa0643adbfbca04509abf139930b7a8ff8b96981d6f5`|`51eb1946b9d81085adf93d7c9d8c201400f75060386a385c6f63d9b45c5d9b37`|
|`activation_guard/work_layout.hpp`|신규 test source|`cfa4ea0c74055488e7e9dae3f8124a3c2d53599414b3223fc50a8ef73279f092`|
|`activation_guard/TbWorkLayout.bsv`|신규 test source|`bdce05dbfe1625e479e49d50a0bd0d3ee7cbbb55ec0a79303f576226321351c4`|
|`activation_guard/test_work_layout.cpp`|신규 test source|`1b7d18c625a70a0a2960fa7bab8dadde1f0359b7a8da6f0628fa3dc6b06e9c4d`|

공통 prefix는 `tests/scu_block_scale/`다. `ResidentP0.bsv`는 `8af4abe058d2a84d6aa4c008f4493891ce91a65a7f07ea45547eb4721f836c8b`, `activation_monitor.hpp`는 `710b9bba211e7019b5a2a4f75e2062b94094d0602458bebd6e07eedccefd119b`로 before/after 동일하다. Provider/stimulus, bounds·conservation invariant, DUT arithmetic/scheduler/guard/ABI/host reconstruction/expected numerical 값은 바꾸지 않았다.

#### 독립 pack/decode 및 negative control

`TbWorkLayout.bsv`는 frozen source의 실제 `WorkTypes`를 import하고 알려진 값을 struct field로 넣어 `pack(work)`한다. C++ 수동 offset을 encoder에서 재사용하지 않는다. 이 fixture는 별도 Bluesim 실행 파일이며 numerical DUT에 rule/action을 추가하지 않는다. `iCount=1/9/16`, 상위 bit가 설정된 서로 다른 job/stripe/context, 인접 field의 nonzero 값을 사용했다. 실제 GEMM에서 허용되지 않는 넓은 identity/row 패턴은 pack unit test 안에서만 사용했다.

결과: 3 vectors × 8 fields = **24 field 비교 PASS**, 옛 iCount/context/stripe decoder의 **9 mismatch** 확인. Record 폭·저장 word 수·0/65bit field 폭·record 밖 offset·offset overflow·상위 identity bit를 잃는 폭을 거부했다. Monitor에는 정상 local snapshot 1개와 잘못된 count/response tag/request job control을 적용했다. **Expected rejection 총 24건**은 의도된 검사 결과이며 예상하지 못한 regression 실패가 아니다. [실제 layout 검사 로그](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/validation/activation-guard-layout-01/layout/check.log).

특히 실제 BSV pack의 `iCount=9,jCount=1` vector를 옛 offset으로 decode하면 `required_rows=18`이다. 이 값으로 기존 monitor의 `work bounds` failure를 재현했다. 이는 **새 독립 pack fixture에서 확인한 합성 counterexample**이다. 기존 실패 trace는 required_rows를 기록하지 않았으므로 18을 과거 trace의 실측값으로 취급하지 않는다. 기존 trace SHA256 `53fafec247d4806aa84307a7c890758e5ea188a86f6b4581ad56e8a162316d32`는 그대로다.

#### 실제 재실행 명령과 cohort 결과

명령 label `21-monitor-layout-m9`, UTC 2026-09-10 11:23:07.406887 시작, 11:24:36.998989 종료, exit 0, 89.592초. Cwd는 IM2P repository root다. TMPDIR/TMP/TEMP와 모든 generated 결과는 `/mnt/fpga-build` 아래다. [전체 argv/env/status](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/evidence/21-monitor-layout-m9.status.json).

```sh
python3 tests/scu_block_scale/run_activation_guard.py \
  --snapshot /mnt/fpga-build/im2p/scu-final-20260910T082724Z/candidate-01 \
  --out /mnt/fpga-build/im2p/scu-final-20260910T082724Z/validation/activation-guard-layout-01 \
  --production-build /mnt/fpga-build/im2p/scu-final-20260910T082724Z/validation/activation-guard/production \
  --jobs 2
```

Production은 보존한 generated RTL bytes와 primitives를 재사용하고 새 observer를 별도 디렉터리에서 컴파일했다. Assertion cohort는 동일 frozen source/provider/stimulus에 `-check-assert`를 적용해 별도 생성했다. 모든 host method에서 실제 RDY/EN을 지켰으며 build 간 수락 cycle을 강제하지 않았다. 각 variant의 plain/passive는 전체 `trace.jsonl`이 byte-exact했다. [검증 summary](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/validation/activation-guard-layout-01/summary.json), [각 하위 compile/run 명령](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/validation/activation-guard-layout-01/commands.json).

| M9/N1/K1 cohort | 완료 jobs | 전체 numerical outputs | 3회 hold 포함 비교 | 각 job cycles | passive returns/publications/feeds |
|---|---:|---:|---:|---|---|
|Production/plain|4|36|108|162/162/162/162|비활성|
|Production/passive|4|36|108|162/162/162/162|36/36/36|
|Assertion/plain|4|36|108|168/168/168/168|비활성|
|Assertion/passive|4|36|108|168/168/168/168|36/36/36|
|합계|16|144|432|build별 구분|관측한 두 cohort만 집계|

16 jobs는 네 numerical case를 네 cohort에서 실행한 횟수다. 고유 GEMM 16개라고 표현하지 않는다. 두 passive cohort 각각 job completion 4, strict pending violation 0, legal pending turnover 0, feed data unobserved 0이다. Runtime assertion failure·unexpected `$finish`는 네 cohort 모두 0이었다. 유효 데이터·tag/slot/row·pending/publication/feed conservation을 기존 invariant 그대로 검사했다. Native Q8_H1은 K32 alignment가 필요하므로 이 K1 raw-core 테스트를 native host route 지원 사례로 분류하지 않는다.

생성 RTL SHA256:

- Production `mkResidentP0.v`: `c775b3fa391f92fa5bc5a704262eb8242000580c0c09140e48bfc259cdb29d94` — 이전 실패 당시 DUT와 byte-identical.
- Assertion `mkResidentP0.v`: `67202351f40fc6c1d4389c161e9e1c10b229053847f98b86f50847898630a935` — 이번 별도 assertion build.

#### 이번 assertion의 +6 cycles 관측

과거 IFR2의 168을 기대값으로 넣지 않았다. 이번 actual trace 네 case 모두 다음 endpoint를 보였다. 값은 각 invocation의 `job_cycle`, host wall-clock이 아니다.

| Endpoint | Production | Assertion |
|---|---:|---:|
|첫 activation feed|31|31|
|마지막 activation feed|39|41|
|fragment finish|122|128|
|job completion|162|168|

Assertion에서 row7 publication은31→38, row8은34→40으로 늦어졌다. Generated RTL은 production의 `publish = pending`과 assertion의 `publish = pending && !WILL_FIRE_feed`를 직접 보여준다. Assertion schedule도 publication의 blocking rule로 `feedBufferedMatrixActivation`을 기록한다. [Production RTL](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/validation/activation-guard-layout-01/production/rtl/mkResidentP0.v:15205), [Assertion RTL](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/validation/activation-guard-layout-01/assertion/rtl/mkResidentP0.v:15238), [Assertion schedule](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/validation/activation-guard-layout-01/assertion/info/mkResidentP0.sched:14294).

마지막 feed 이후 fragment finish까지는83→87cycles, 그 뒤 completion까지는40cycles로 동일했다. Assertion schedule에는 `completeAccumulatorUpdate`가 `engine_advanceArray`를 막는 추가 관계도 있다. 이 관계는 잔여 +4cycles의 engine/commit 직렬화 설명과 일치한다. 다만 현재 trace는 두 rule의 fire를 기록하지 않으므로 +4를 직접 계측한 rule별 stall 횟수로 단정하지 않는다. [Engine schedule](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/validation/activation-guard-layout-01/assertion/info/mkResidentP0.sched:221), [Production trace](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/validation/activation-guard-layout-01/production/plain/trace.jsonl), [Assertion trace](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/validation/activation-guard-layout-01/assertion/plain/trace.jsonl).

#### 기존 fresh 검증 근거의 유지

이번 monitor-only 수정으로 이미 같은 RUN에서 통과한 freeze/native/host/verify, 5-profile runtime, production RTL 163개, SCU numerical/carrier/saturation/mutation/review-fix 결과를 새 실행으로 재계수하지 않았다. Frozen 1,746개 불변을 근거로 각 원래 source/artifact·실행 시각에 연결된 이전 단계 증거를 유지한다. Native generated RTL hash의 `not_collected/not_retained` 한계도 그대로이며 별도 Cargo RTL hash로 대체하지 않는다. 관련 observer를 쓰는 M9 gate만 이번 overlay로 다시 실행했다. DUT source 변경 및 physical board 접근 0회; staging/commit/push 0회.


### Final ggml / IFR3 UART RTL

변경하지 않은 fresh `scu_host_dispatch`와 `Vscu_uart_shell`을 사용했다. 실제 ggml quantizer/post-fold producer, UART bit-level shell, production SCU core/provider, 기존 shared FP reconstruction을 모두 통과했다. PTY는 script가 새로 만들었으며 물리 UART를 열지 않았다.

| 실제 실행 | 논리 GEMM | final raw / f_out exact 비교 | wire padding | works | K16 fragments | C write / ACK | cycle 관측 |
|---|---:|---:|---:|---:|---:|---:|---|
|M16/N16/K64 FULL, seed1|1|256 / 256|0|1|4|16 / 16|617|
|M321/N48/K96 FULL, seed3|1|15,408 / 15,408|0|63|378|963 / 963|52,687|
|M321/N48/K64 live PIPELINE, seed2|1|15,408 / 15,408|0|63|252|963 / 963|26,155,216 elapsed|
|M17/N19/K64 FULL, seed4→5, 같은 process|2|646 / 646|442|8|32|68 / 68|1,949 / 1,949|
|합계|5|31,718 / 31,718|442|135|666|2,010 / 2,010|domain별 구분|

Count는 반복을 포함한다. Graph의 caller output은 contiguous이므로 이번 ggml 실행의 wire padding442를 caller stride sentinel442로 바꾸어 부르지 않는다. Caller-stride sentinel과 오류 시 caller output 보존은 동일 frozen frontend의 앞선 fresh 검사 근거를 유지한다. 이번 positive ggml invocation을 failure-injection 검사로 재분류하지 않는다.

모든 실행에서 ABI5/IFR3/profile0810/capability0294/domain2, run/generation, CRC, 최종 M×N raw, write/ACK, 정상 RELEASE 및 pending output bytes0을 확인했다. Tail 두 invocation의 payload hash는 서로 다르며 run/generation은1/1 다음2/2다. K96 FULL과 K64 live의 유효 raw는 각각15,408개로, block_count plane이 아니다. Wire의 signed32 zero-padding까지 재검사했다.

선택된 FPGA 수치 경로의 runtime simulator create/execute/stream-begin counters는 모두0이다. `SCU_INDEPENDENT_REFERENCE`는 실제 FPGA RTL 결과를 얻은 뒤 독립 G1/G2를 계산하는 정당한 검사다. 이 CPU reference 호출을0으로 주장하지 않는다. 로그의 layer classification fallback 문자열은 수치 backend fallback이 아니다. Reference는 output 대신 반환되지 않았다. 실제 raw exact와 최종 f_out bit-exact 검사는 frozen host에서 수행하며 추가 결과 검사기는 보존 UART trace의 CRC/metadata/count/identity/padding을 검사한다.

실제 source에서 설정된 명령은 다음과 같다. 공통 runner는 `$FINAL/source/fpga/scu_block_scale/run_pty.py`, RTL은 `$FINAL/production/obj_dir/Vscu_uart_shell`, host는 `$FINAL/fpga-host-build/scu_host_dispatch`다. `--out`은 각기 새 디렉터리다.

```text
run 16 16 64 1 1 FULL
run 321 48 96 3 1 FULL
run 321 48 64 2 1 STRIPE_PIPELINE
run 17 19 64 4 2 FULL
```

실제 전체 argv/env/start/end/exit와 원본 로그는 `evidence/22-ggml-full-k64.*`, `25-ggml-full-k96.*`, `27-ggml-live-k64.*`, `29-ggml-tail-two.*`에 있다. 각 결과의 후검사는24/26/28/30번 evidence다. [종합 gate와 전송·결과 hash](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/evidence/final-ggml-route-gate.json). 전체 PTY 전송은 CAP 포함 input114,856B, output132,488B,26transactions였다. 이 wall-clock은 bit-level RTL simulation 시간이며 board 성능이나 speedup이 아니다.

Live stripe는 actual automatic geometry160/160/1, event slot0/1/0이며 theta는-6/-3/0이었다. Publication/completion 응답 각각3개를 확인했다. Host frontend run0과 device run1/generation1은 서로 다른 identity namespace이며 stripe0/1/2로 대응한다. 첫 event 수락은 마지막 stripe quantization 완료보다 빨랐으므로 사전 양자화된 event 전체 replay가 아니다. 다만 이번 trace에서는 모든 stripe 준비가 첫 device publication transfer 시작보다 빨랐다. 자연 CPU quantization/FPGA compute overlap을 입증하지 않았다.

Device publication cycles는5,187,112 /18,224,840 /26,154,088, completion cycles는5,205,561 /18,243,290 /26,155,213이었다. Global first-A는5,187,120, 당시 published prefix160이다. Elapsed26,155,216에는 합법적인 publication/raw-drain 대기가 들어가며 FULL equality를 적용하지 않았다. 직접 reply의 host-wait20,930,077과 engine/provider overlap6,426을 기록하되 서로 더하거나 CPU-FPGA overlap 시간으로 해석하지 않는다. 순수 compute와 stripe별 first-A, 독립 publication-total은 not_exposed다. Host ns와 device cycles를 직접 빼지 않았다.

FULL617/52,687/1,949는 이번 final source/provider에서 수치·protocol PASS와 함께 새로 관측했다. 과거 SCU 숫자와 일치하지만 과거 기대값을 gate에 넣지 않았다. Endpoint는 provider가 start 시 기록한 `core.rtlCycleCount`와 `core.matmulDone` 관측 시 latch한 값의 차이다. 새 bitstream identity가 생긴 뒤에만 물리 실행용 reference와 결합한다. 현재 host는 reference/identity/fixture 누락 시 device constructor 전에 거부하고, FULL cycle mismatch는 reconstruction/commit/RELEASE 이전에 실패한다. PIPELINE에는 FULL expected cycle을 적용하지 않는다.

### Implementation 시작 gate

2026-09-10T11:56:54Z 실제 mount `/dev/sda1`, ext4, rw. Root available12,791,713,792B, RUN filesystem467,854,807,040B, RUN2.0GiB였다. Frozen1,746개 및 기존 기록 artifact16개 hash 불변, 모든 필수 runtime gate PASS를 확인한 뒤에만32-route를 시작했다. Generated RTL/primitives/UART/board-top/XDC/Tcl 각각의 hash는 위 종합 gate의 `implementation_inputs`에 기록했다. 기존 DCP는 입력으로 사용하지 않는다.

```sh
python3 /mnt/fpga-build/im2p/scu-final-20260910T082724Z/evidence/run.py \
  32-route "$FINAL" /tools/Xilinx/2025.2/Vivado/bin/vivado -mode batch -nojournal \
  -source /mnt/fpga-build/im2p/scu-final-20260910T082724Z/evidence/route-wrapper/route_observed.tcl \
  -tclargs "$FINAL" scu
```

관측 wrapper는 frozen route Tcl SHA를 검사하고 기존 synth/opt/place/route/bitstream 명령을 그대로 실행한다. Phase 전후 df/du와 source별 입력을 보존한다. Timing exception 추가·warning suppression·DUT 수정·incremental checkpoint 재사용은 없다. 이 기록 시점에서는 새 route 성공이나 programming 승인을 주장하지 않는다.

### Final implementation 완료 — 2026-09-10

위 진행 기록 이후 synth/opt/place/route/bitstream 모두 완료했다. Vivado2025.2 Build6299465, implementation part `xc7a100tcsg324-1`, physical A8/W8/D16, MMCM core25MHz, UART1Mbaud/8N1이다. 실행32-route는 UTC11:57:04.040994→12:05:18.290650,494.249552초,exit0이었다. `route/complete.txt`는 `ROUTED; NOT PROGRAMMED`다. 기존 IFR2 DCP/bitstream을 input으로 사용하지 않았다.

| Final post-route resource | 실제 값 |
|---|---:|
|Slice LUT / Logic LUT|51,299 /51,211|
|LUT memory|88 SRL, distributed LUTRAM0|
|FF / latch|33,422 /0|
|RAMB36 / RAMB18 / equivalent tiles|60 /3 /61.5|
|DSP48|66|
|Occupied slices|15,815/15,850,99.78%|
|Empty slices|35, 전체−occupied로 계산|
|Control sets|826|
|Max non-clock fanout|10,136, activeWeightBankReg|
|Fully routed / routable nets|86,599 /86,599|
|Unrouted / partially routed / route-error nets|0 /0 /0|
|Black boxes / node overlaps|0 /0|

| Timing endpoint | Slack / aggregate | Failing / total endpoints |
|---|---|---:|
|Setup|WNS+10.883ns,TNS0|0/87,963|
|Hold|WHS+0.027ns,THS0|0/87,963|
|Pulse width|WPWS+3.000ns,TPWS0|0/33,648|
|Internal unconstrained / no_clock / loops|0 /0 /0|—|

Vertical/horizontal routing utilization은23.2523%/25.3638%다. Placer와 initial-router congestion report에는 level5를 넘는 window가 없었다. 이를 전체 congestion0으로 표현하지 않는다. Fit/timing이 통과했으므로 추가 area/timing campaign은 수행하지 않았다.

DRC는 ERROR0/CRITICAL0, **warning142건**이다: DPIP-1 input-pipeline 권고22, DPOP-1 PREG 권고60, DPOP-2 MREG 권고60. DRC 무경고라고 보고하지 않는다. 별도 stdout warning111줄의 ID별 수는 Synth8-7071:29,8-7023:2,8-6014:74,8-3936:2,8-3917:3,8-3323:1이다. 원본 log와 instance별 DRC report를 모두 보존했다.

8-7071/7023은 미사용 MMCM output11개와 BSV RDY18개의 연결 경고다. 해당 RDY18개는 generated RTL에서 constant1임을 확인했다. 8-6014는 unused sequential element 제거,8-3936은 unused-bit trim,8-3917은 constant output bit 경고다. MatmulWork899→739 trim은 synthesis가 사용하지 않는 상위 identity bits를 제거한 결과이며 pre-synthesis observer의899bit 계약 변경이 아니다. 8-3323의 preliminary448DSP/240 경고는 resource-management mapping 이전 값이다. 실제 final DSP는66이며 fit/timing 결과로 판정했다. Warning을 숨기거나 source arithmetic을 변경하지 않았다.

Clock report는 sys_clk_pin10ns, MMCM clock25 40ns, feedback10ns를 기록한다. 유효 timing exception은 없으며 새 false_path/multicycle을 추가하지 않았다. 외부 uart_rx input1개, uart_tx/LED output5개에는 I/O delay가 지정되지 않았다. 내부 synchronous closure와 외부 비동기 I/O 한계를 분리한다. CDC의 `All paths are Safely Timed.`는 양 끝에 clock이 정의된 분석 범위이며 Timing38-314에 따라 unconstrained uart_rx는 제외된다. 이를 외부 UART 전체 CDC 검증으로 확대하지 않는다. MMCM LOCKED→release_reset CLR의 asynchronous recovery/removal 경로도 보고서에 남아 있다. `report_methodology`는 NOT RUN, hierarchy utilization과 level5 이하 상세 congestion은 frozen recipe에서 not_collected/not_exposed다.

원본과 항목별 출처: [route audit](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/approval-package-01/route-audit.json), [route report directory](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/candidate-01/route), [실행 로그](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/evidence/32-route.log).

#### Disk checkpoint

모든 build/generated/temp/implementation output은 `/dev/sda1` ext4,rw의 RUN 아래에 두었다. TMPDIR/TMP/TEMP는 RUN/tmp다. Phase별 df와 RUN du 원본은 `evidence/route-observations`에 있다.

| Checkpoint | Root available bytes | RUN filesystem available bytes |
|---|---:|---:|
|Synthesis 직전|12,791,664,640|467,854,716,928|
|Synthesis 후|12,791,439,360|467,643,142,144|
|Opt 후|12,791,373,824|467,643,129,856|
|Place 후|12,794,114,048|467,600,826,368|
|Route 후|12,792,279,040|467,600,789,504|
|Bitstream 후|12,790,657,024|467,530,776,576|
|최종 보존 검사 UTC12:27:24|12,792,373,248|467,740,770,304|

ENOSPC0, 추가 삭제/압축/정리0. 마지막 관측에서 승인 후 삭제됐던 IntelliSense DB는 다시 생성되지 않았다. Filesystem 여유 변화 전체를 이 작업의 파일 생성량으로 해석하지 않는다.

#### Historical IFR2 비교

아래 왼쪽은 과거 External/host-scaled bitstream의 보고값이며 새 SCU 검증이 아니다.

| 항목 | Historical IFR2 | 이번 IFR3 |
|---|---:|---:|
|LUT|47,986|51,299|
|FF|32,032|33,422|
|BRAM equivalent tiles|89.5|61.5|
|DSP|85|66|
|Occupied slices|98.40%|99.78%|
|Setup WNS ns|+12.517|+10.883|

Legacy block-result C allocation256KiB→final-result128KiB, 새 H1 scale staging1KiB를 구분한다. Architectural accumulator64KiB는 별도이며 A64KiB/W8KiB 역할도 유지한다. SCU multiplier/extended saturation/control 비용이 추가된다. Hierarchy attribution을 새로 수집하지 않았으므로 BRAM−28 또는 LUT+3,313 전체를 특정 storage/SCU 하나의 비용으로 단정하지 않는다.

### Candidate artifact와 새 FULL reference

새 package: [approval-package-01](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/approval-package-01). 기존 frozen manifest, IFR2 approval와 bitstream은 수정하지 않았다. 원본 artifact 경로·크기·hash는 candidate-manifest에, phase별 argv/env/UTC/exit는 원본 evidence에 있다.

| Artifact | SHA256 |
|---|---|
|Frozen integration manifest|`607827f129c61b60dffd9e9fb02fd0ceb4a7ab2abbeb25bd6b08d4d9b2782d69`|
|Production mkScuPipeline.v|`1833a6a4f53e48fa18f0afb188fbd2858a7584376701a333374025174d061c26`|
|Routed DCP|`828c09cf0f718c535957c72c4a69bfc954ac306e06f44c467530ef4599004995`|
|Bitstream,3,825,913bytes|`2c5516e2bae4d5f960697537bb1501d42470b7971be5986b6e59ad8cc6586984`|
|scu_host_dispatch|`0ca9b6465ba69682ac0b4a9a234d21ee8728f8682efd41c89eab02c5f0873f5f`|
|libim2p_sim.a|`0cf3a3c0eeada8cfa25520952d9c746f397d968a51a504e4fd8fb1a1b9d87709`|
|libim2p_gemmini_frontend.a|`a1cb9a4f7598cbeb2ef6bf6ad427ae80956a6831e90ce0122d8ce6375ad82240`|
|scu_frontend_final|`77fbb7e86a66cce760b829e35bbdba3103556f24944116e27a0a174176647f54`|
|CAP/protocol source|`10bd105f3b1f5b0084fc0a7f5cd1632eb0e28732d3a508f1dfc057a123c2d9ee`|
|FULL cycle reference|`4da8b84c9d1d2f678491e3b02893bda0065cae1bd4f3cd838ded316f0dd1276c`|
|FULL cycle provenance|`7cfc1e584d076a072aab404746459e105257839346b253a628fc80c56e433987`|
|Candidate manifest|`e5272e4bd95f82b03e2e8518fd56054dda1d821a1cdf76e3f152324a00a8bcd8`|

FULL reference는 이번에 검증한 M16/N16/K64:617, M321/N48/K96:52,687, M17/N19/K64:1,949만 포함한다. 실제 UART reply/fixture 입력 hash, source/provider, 측정 endpoint, ABI5/IFR3/profile0810/capability0294/numerical revision2, 새 RTL/bitstream과 함께 고정했다. M1/N1/K64의 과거377을 새로 측정하지 않았으므로 이번 물리 reference에는 넣지 않았다. Native generated RTL은 여전히 not_collected/not_retained이며 다른 Cargo RTL hash로 대체하지 않았다.

실제 frozen `UART` source의 파일 parser만 별도 test executable로 링크하여 reference3개를 로드했다. 누락 fixture, hardware identity mismatch, RTL identity mismatch의 expected rejection3개도 PASS다. 실행은35-reference-loader-build/36-reference-loader-check이고 UART constructor는 호출하지 않는 소스 경로다. 이는 장치 검증이나 새로운 numerical execution이 아니다. 기존 FULL gate21개와 stale protocol 검사는 동일 production source의 앞선 근거로 유지한다.

Physical host의 FULL reference 로딩·shape/identity 검사는 UART constructor 이전, actual FULL cycle 판정은 reconstruction/commit/RELEASE 이전에 있다. 실패 destructor는 fd close만 하며 ABORT/reset/recovery 명령을 보내지 않는다. Text reference parser는 fixture 이름/shape와 hardware/RTL을 묶고, payload hash는 별도 provenance/승인 plan이 고정한다. Actual cycle을 runtime expected로 학습하는 기능은 만들지 않았다. PIPELINE elapsed는 FULL equality 대상이 아니다.

### 최종 지원·보존·승인 경계

| 대상 | 상태 |
|---|---|
|Dense H1 simulator FULL/PIPELINE|Supported; 동일 fresh source의 numerical PASS 유지|
|Dense H1 FPGA FULL/live PIPELINE|Simulated / Routed; 이번 ggml5개 PASS|
|HP1 simulator|이전 fresh 단계 PASS 유지|
|HP1 FPGA|Unsupported, capability/start reject 유지|
|Residual CPU-side / FPGA|unchanged/out of scope / out of scope|
|DIM64 runtime|PASS; local partial22bit, 물리 DIM64 구현 아님|
|Board measured / PPL/model quality / Nano|NOT RUN|
|Natural CPU/FPGA overlap|이번 live simulation에서 입증하지 못함|
|새 board execution|Awaiting explicit approval|

Fresh core163개,5-profile runtime15jobs/6,519scalar, SCU beta1/256→8224→32.125와 carrier/saturation/mutation/review-fix 근거는 같은 frozen source의 이전 실행 시각에 연결한다. 이번 monitor 수정 때문에 이를 재실행한 것으로 세지 않는다. 이번 신규 M9는16jobs/144outputs, 실제 ggml/IFR3는5jobs/31,718raw/31,718f_out exact와 wire-padding442다. 의도된 old-layout/invalid-identity/reference negative rejection은 unexpected regression failure와 구분했다. 이전 work-bounds 실패 로그도 남겼다.

[보존 검사](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/approval-package-01/preservation.json): baseline 원본 tar331blob, historical artifact6개, frozen1,746개, 과거 monitor 실패127파일 hash 불변이다. 원본331blob 보존은 현재 모든 source 경로가 예전과 같다는 뜻이 아니다. 최초 editable402파일 중 이번에 변경한 기존 파일은 observer/runner/이 보고서뿐이며 새 layout test3개를 별도 overlay로 기록했다. Production source 변경0, 추가 삭제0, staged0, git diff --check PASS. 최종 status/stat/binary/cached diff는 `evidence/git-final-monitor-01`에 보존하며 package test-evidence manifest가 이를 연결한다.

새 approval은 이 bitstream의 **configuration SRAM1회 + correctness GEMM5회**만 제안한다. 순서: 작은 K64 FULL1, 긴 K96 FULL1, K64 live160/160/1 하나, 서로 다른 tail2개. Warm-up/성능 sweep은 없다. 앞선 simulation과 같은 seed/geometry/host/reference로 raw/f_out 각31,718, wire padding442 비교를 계획한다. 예상 전송량은 위 RTL trace의114,856/132,488bytes/26transactions이며 physical PIPELINE POLL/elapsed 차이는 허용된 protocol 내에서 따로 관측한다. Transaction30초/process900초, 첫 오류에서 중단, 정상 성공 RELEASE만 허용한다. 실패 뒤 RELEASE/ABORT/reset/retry/reprogramming/flash는 금지한다.

현재 권한은 software/RTL simulation/FPGA implementation까지다. [최종 approval.json](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/approval-package-02/approval.json)과 [정확한 실행 계획](/mnt/fpga-build/im2p/scu-final-20260910T082724Z/approval-package-02/proposed-board-plan.json)의 별도 승인이 있어야 장치 확인·SRAM loading·CAP·GEMM을 시작할 수 있다. Physical device path/JTAG identity는 이번에 조사하지 않았다. **JTAG/UART physical open/CAP/programming/board GEMM/flash 모두0회**, staging/commit/push0회다. 이 보고 이후 추가 구현·implementation·장치 실행 없이 중단한다.

최종 문서 검토에서 첫머리의 과거 `route NOT RUN` 상태 문구를 수정했다. 수정 전 보고서 bytes는 `evidence/final-report-before-status-correction.md`에 같은 SHA로 보존했다. 기존 package01은 그대로 두고, 수정된 보고서와 최종 Git 증거를 연결한 **approval-package-02**를 최종 승인 대상으로 구분했다. 두 패키지의 DUT/RTL/DCP/bitstream/host/reference/실행 계획은 동일하다. Package02는 원본 candidate01 및 package01의 동일 artifact 경로도 참조하는 로컬 승인 묶음이다. 모든 경로가 과거와 불변이라는 뜻이 아니라, 변경 전 보고서 blob과 원래 봉인 bytes를 보존한다는 뜻이다. 마지막 Git 기록은 `evidence/git-final-monitor-02`다.
