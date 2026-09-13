# SCU H1 Flash/Nano 이관 준비

2026-09-11. **Prepared / Software-tested, 이관 완료 아님.** 기존 SCU H1 package02의 실보드5/5 결과를 보존하고, 별도 source/Flash/Nano 준비 패키지를 만들었다. 실제 Flash 부품과 Nano 접근 정보가 없어 최종 boot image 및 ARM64 실행은 미완료다. 이 문서나 JSON의 존재는 물리 작업 승인이 아니다.

새 RUN: `/mnt/fpga-build/im2p/scu-nano-migration-20260911-01`

최종 archive hash·크기·추출 검사·보존 결과는 RUN의 `transfer.json`, `completion.json`에 별도로 봉인한다. 자기 자신을 포함한 archive hash를 문서 안에 넣는 순환 구조를 만들지 않는다.

## 1. 검증 기준과 보존

최신 근거는 [SCU 실보드 correctness 보고서](SCU_BLOCK_SCALE_BOARD_CORRECTNESS.md)다. Package02에서 configuration SRAM1회, GEMM5/5, final integer/f_out 각각31,718회 exact, wire padding442회를 확인했다. 과거 NOT RUN·monitor 실패 문서는 당시 이력으로 보존했다.

| 입력 | 확인한 SHA256 |
|---|---|
|원본 bitstream|`2c5516e2bae4d5f960697537bb1501d42470b7971be5986b6e59ad8cc6586984`|
|Routed DCP|`828c09cf0f718c535957c72c4a69bfc954ac306e06f44c467530ef4599004995`|
|Production mkScuPipeline.v|`1833a6a4f53e48fa18f0afb188fbd2858a7584376701a333374025174d061c26`|
|Frozen integration manifest|`607827f129c61b60dffd9e9fb02fd0ceb4a7ab2abbeb25bd6b08d4d9b2782d69`|
|검증된 x86 scu_host_dispatch|`0ca9b6465ba69682ac0b4a9a234d21ee8728f8682efd41c89eab02c5f0873f5f`|
|FULL cycle reference|`4da8b84c9d1d2f678491e3b02893bda0065cae1bd4f3cd838ded316f0dd1276c`|
|FULL cycle provenance|`7cfc1e584d076a072aab404746459e105257839346b253a628fc80c56e433987`|

Core branch `fix/scu-block-scale`, HEAD `3aeb5feee6872f88ec1f6a5dc0d77fb1bb8babf8`를 확인했다. 기존 unstaged SCU source는 보존했다. Host HEAD는 `7a74ac29c9c8fa8fbc5d47cf073b8b9266e3e7a9`, include HEAD는 `cd2caed8d93e49d3f8873bd6c4e340fcad54b9e0`다. Host의 `build-x86.sh`에는 기존 tracked 변경이 있고 `models/gpt2/`, `models/llama3.2-1B/`도 untracked로 존재한다. 이를 새 source에 흡수하거나 수정하지 않았다. Include checkout은 clean이다.

실제 선택은 현재 root 전체 복사가 아니다. Base core `dc4a1a621f63834d64df42ae8c24152747d97971` → FULL fixed-core patch → SCU core delta → 명시 overlay, host pin → host integration/producer observation/SCU companion patch, include pin으로 만들어진 **기존 frozen1,746파일 bytes**를 선택했다. Patch를 다시 적용하지 않아 중복 적용도 없다. Board A1 arithmetic·scheduler·activation guard와 reviewed frontend/UART가 선택된 기존 manifest를 그대로 유지한다.

원래 native build에서 생성 RTL hash를 보존하지 않은 `not_collected/not_retained` 한계는 유지한다. 별도 board production RTL 또는 Cargo test RTL hash를 그 native 생성물의 hash로 대체하지 않았다.

원본 절대경로 manifest는 `manifests/original/`에 보존하고 새 상대경로 mapping을 `manifests/derived/relocation.json`에 기록한다. 이전2,558경로는 실제 파일 hash를 다시 확인했다. 새 검사 범위에는 board report/results/evidence manifest3개가 추가되며, 중복 manifest 항목을 고유 파일 수로 더하지 않는다. Baseline331은 현재 root 동일성 주장이 아닌 tar 내 원본 blob 보존이다. 최종 재검사 결과는 `completion.json`에 기록한다.

## 2. 저장소·환경

실제 escalated namespace에서 `/mnt/fpga-build`=`/dev/sda1`, ext4, rw를 확인하고 새 probe 파일을 작성·읽었다. 시작 available은 build467,738,259,456bytes, root12,977,332,224bytes다. Sandbox의 read-only 표시와 실제 mount를 구분했다. 기존 자료 삭제·압축·이동은 없다.

새 TMPDIR/TMP/TEMP, Vivado workdir, C++ test outputs, Python cache, 패키지와 추출 경로는 RUN 아래다. 도구 설치 위치 `/tools/Xilinx/2025.2`는 그대로 사용했다. 실제 Nano 주소·접속 권한이 확인되지 않아 SSH나 network scan은 하지 않았다.

## 3. 보드 식별과 Flash 설정

| 항목 | 상태 |
|---|---|
|FPGA 구현 part|`xc7a100tcsg324-1`, 원본 DCP에서 확인|
|물리 PCB revision / Flash 마킹·suffix|Needs identification|
|실제 Flash 용량·sector geometry·보호/QE 상태|Needs identification|
|현재 configuration jumper / 전원 공급 / 기존 Flash 데이터|Unknown, 사용자 확인 필요|
|Nano 모델·OS·USB host 포트·전원 예산|Needs target access / identification|

Digilent PCN은 Arty A7-100T PCA E.1부터 S25FL128S/S25FL127S 대체 실장을 기록하며 erase geometry 차이를 명시한다. E.2 schematic의 명목 부품이나 Vivado 지원 목록만으로 실제 실장 부품을 확정하지 않는다. [공식 PCN](https://files.digilent.com/resources/programmable-logic/documents/S25FL127S_PCN.pdf), [E.2 schematic](https://digilent.com/reference/_media/programmable-logic/arty-a7/arty-a7-e2-sch.pdf).

정확한 PCB revision, Flash 전체 마킹/부품 sticker, jumper 현재 상태, 현재·이동 후 전원 공급, Flash 데이터 보존 요구를 확인해야 한다. JEDEC 밀도만으로 suffix/geometry가 모두 결정된다고 가정하지 않는다. 실제 JEDEC 조회도 이번에는 하지 않았다. Reference manual 웹 접근은403이었으므로 jumper 방향·전압·극성을 임의로 적지 않았다. 확인한 상세 출처와 2025.2 Tcl 템플릿은 [flash_reference.md](../fpga/scu_migration/flash_reference.md)에 있다.

Vivado2025.2에서 원본 DCP를 **offline open_checkpoint**했다. CFGBVS=`VCCO`, CONFIG_VOLTAGE=`3.3`. CONFIG_MODE, BITSTREAM.CONFIG.SPI_BUSWIDTH/CONFIGRATE, BITSTREAM.STARTUP.STARTUPCLK 및 조사한 DONE/GWE/GTS 속성은 빈 값, 즉 checkpoint에 명시값이 없었다. DCP는 원래 write_bitstream 전에 저장됐다. 이를 원본 bitstream에 encode된 기본값의 실측으로 간주하지 않는다. 원본 JTAG PASS도 SPI cold-boot PASS가 아니다.

첫 검사기는 offline 세션에 없는 `help readback_hw_cfgmem`에서 exit1이었다. 원본 로그·부분 property report를 보존했다. Hardware Manager를 열지 않고, offline 지원 명령의 help만 조회하도록 검사기 범위를 수정한 새 실행은 PASS다. 설계·DCP·bitstream은 바꾸지 않았다. 도구 명령 목록/공식 문서 확인 없이 별도 bitstream packet parser로 부팅 적합성을 추정하지 않았다.

**Boot-only .bit / 최종 .mcs: NOT GENERATED.** 부품과 SPI/startup 적합성이 미확정이므로 최대 SPI폭/rate를 선택하지 않았다. RTL/netlist/배치/배선 최적화 또는 bitstream 재생성은0회다. 원본 `.bit`3,825,913bytes를 그대로 배포 사본으로 보존했다. 새 boot-only image가 필요해지면 같은 routed DCP의 속성 변경 전후 및 cell/net/route/clock 동일성 근거를 별도로 확보해야 한다.

`flash_prepare.py`는 확정된 part/interface/용량/geometry/주소·payload mapping·tool hash를 받아 공식 `write_cfgmem`만 offline 호출한다. `-size`는 MBytes이며 기본 interface가 SPI가 아님을 설치2025.2 help에서도 확인했다. Bitswap·header 제외·padding 범위를 명시하고 MCS memory bytes와 비교한다. MCS 텍스트 hash와 Flash readback binary hash를 같다고 요구하지 않는다. [AMD UG835 write_cfgmem](https://docs.amd.com/r/2025.2-English/ug835-vivado-tcl-commands/write_cfgmem).

검사기는 checksum/length/EOF/extended address/중복·충돌/범위초과/누락 byte/명시 payload mapping을 검사한다. 현재는 single contiguous image만 지원하며 gap이나 새 HEX record를 조용히 허용하지 않는다. 부품 미확정 plan은 파일 생성·도구 호출 전에 거부한다. 검사 PASS는 Flash 프로그램 PASS가 아니다.

## 4. F1/F2/F3 승인 준비

`approval-requests.json`의 세 단계는 분리돼 있다. 물리 실행기는 현재 제공하지 않으며 `flash_prepare.py --execute`는 항상 거부한다. 실제 part·보호 상태가 없는 상태에서 완성 Flash programmer로 광고하지 않는다.

- **F1**: 정확한 cable/part/geometry/helper hash·SRAM load 수·read 범위를 고정한 뒤 별도 승인. Array backup과 보호/QE/OTP 상태는 별개다. Backup 실패 시 erase 승인을 보류한다.
- **F2**: verified backup, exact MCS·주소·sector 경계·범위 밖 보존 정책을 확정한 뒤 별도 승인. Whole-chip erase는 기본값이 아니다. Tool verify, memory-domain readback, untouched 영역 비교를 구분한다.
- **F3**: F2 성공 후 사람에 의한 모든 전원 실제 차단·revision에 맞는 Master SPI 설정·재인가. JTAG reprogramming 없이 DONE/CAP와 M16/N16/K64 FULL seed1 cycle617 검사. 독립 cold boot2회 ×1GEMM, 총2회를 제안하며 현재 실행0회다.

Indirect Flash readback도 helper SRAM load로 현재 SCU 구성을 바꿀 수 있다. Array readback 문서만으로 helper/driver의 모든 비휘발 register 부작용이 없다고 보증하지 않는다. QE는 후보 Flash에서 비휘발 bit이고 일부 geometry/protection 설정은 OTP다. 자동 unlock/QE/status clear를 넣지 않는다. [AMD readback_hw_cfgmem](https://docs.amd.com/r/2025.2-English/ug835-vivado-tcl-commands/readback_hw_cfgmem), [Infineon S25FL127S datasheet](https://www.infineon.com/assets/row/public/documents/10/49/infineon-s25fl127s-128-mb-16-mb-3.0-v-spi-flash-memory-datasheet-en.pdf).

USB 또는 외부 전원이 남으면 완전 cold boot가 아니다. PROG/reset은 실제 전원 차단의 대체물이 아니다. Flash에는 회로 구성이 저장되며 이전 A/W/scale/Run/output은 복원되지 않는다. 새 CAP에서 generation을 시작하고 예전1…5를 강제하지 않는다.

## 5. ARM64 경로

로컬에는 AArch64 GCC/binutils/sysroot/loader/QEMU와 Rust ARM64 target이 없다. Nano 모델·OS·접속 정보도 없다. **ARM64 built/target-executed: NOT RUN.** 검증된 x86 `.a`를 ARM 링크에 쓰거나 cross 설정으로 architecture gate를 우회하지 않았다.

선택한 최소 경로는 Nano native rebuild다. `native_build.py`가 sealed source만 새 RUN에 materialize하고 원본 identity는 `identity-origin.json`으로 보존한다. 새 machine/target archive identity를 생성하면서 source manifest SHA는 유지한다. 기존 recipe의 native → host → host-fpga → verify를 호출한다. Default는 검사/dry-run이며 `--build`가 필요하다. 기존 RUN 재사용·source tamper·cross 환경은 거부한다.

필요 link 입력은 ggml-gemmini/ggml-base, frontend, `libim2p_sim.a`, Verilator C++ runtime, C++/pthread/dl/math다. FPGA 경로 simulator call0과 링크 의존성0은 다르다. Native recipe는 BSC → Verilator generation → C++/Cargo → frontend → CMake host 순서다. 검증 generated C++만 재사용하는 별도 CLI는 없으므로 Nano에 BSC/Verilator가 없으면 그 단계는 미완료다. Nano에 Vivado를 설치하지 않는다.

현재 x86 검토 도구: GCC11.4, Clang14, CMake3.22.1, Python3.11.6, Rust1.91.1, BSC2026.01, Verilator5.051(mod). 이는 Nano 버전 추정이나 모든 최소 버전 보증이 아니다. SCU CMake minimum3.16, C++20을 요구한다. 실제 `std::bit_cast`, `starts_with`, endian/from_chars compile·native 실행 probe를 준비하고 x86에서 PASS했다. 이관 Python build/test wrapper는 Python3.10+ 경로를 사용한다. Rust lockfile4는 유지하며 cargo update는 하지 않는다.

Cargo.lock의 cc1.4.2/find-msvc-tools0.1.10/shlex2.0.1 source3개를 기존 cache에서 `cargo vendor --locked --offline`으로 포함했다. Target object/cache가 아니다. 최종 package manifest 검증 후 새 RUN의 CARGO_HOME/config에서 vendor source를 참조한다. GGML_NATIVE=OFF 유지, 임의 dotprod/i8mm/SVE·fast-math 옵션은 추가하지 않았다. AArch64 quantizer branch와 PMU는 실제 Nano에서 별도 검증해야 한다. PMU/perf가 불가하면 기존 unavailable 정책을 유지하고 wall-clock/FPGA cycle로 대체하지 않는다.

## 6. 비장치 검사와 새 fixture

이관한 source를 새 경로에 materialize한 뒤 실제 C++ compile/PTY 실행으로 UART20건과 FULL gate21건을 통과했다. ±1 cycle·backend/profile/ABI/IFR2/semantic 거부, 오류 뒤 다음 장치 명령0, owned snapshot 및 partial I/O의 **mock/host 근거**다. 새 board numerical PASS가 아니다.

같은 이관 source에서 기존 `test_output_extent.py`와 `test_first_a_ordering.py`도 새 결과 경로로 실행했다. SIZE_MAX/근접값3건의 reviewed guard는 executor/simulator 실행0·caller output 보존, guard 제거 mutant는 의도된 실패다. First-A rows-before-cycle은 PASS, cycle-first mutant는 stale rows를 검출했다. Frozen DUT를 수정하지 않은 격리 test-only 관측이며 자연적인 timing 측정이 아니다.

새 test-only exporter는 기존 `ggml_init` 순서, native H1 quantizer, ExSIA quantizer와 자동 tiler를 사용한다. M16/N16/K64, 새 seed73 계열의 명시적 dyadic FP32 파일에서 A/W codes, H1 c/R/S/beta, theta, 독립 G1/G2와 IFR3 wire bytes를 보존한다. Package의 `fixtures/portable-x86/`는 새 수치 이관 비교용이며 과거5회 보드 payload capture가 아니다. Exporter는 GEMM을 실행하지 않는다. 실제 생성·비교 결과와 tool hash는 fixture manifest 및 completion에 기록한다. ARM 캡처와 exact byte 비교는 아직 NOT RUN이다.

실제 x86 capture는 PASS이며 automatic tile1/1/4, stripe_rows16, stripe1개다. 독립 G1/G2256개, caller padding48개, fragment reference1024개를 생성했고 final zero 및 saturation count는0이다. Exporter SHA는 `d7068d2478bdd7da9d609ec0813c76cacec9ba4cb508f9ae696a4917d4b2c493`이다. Build 뒤 Python wrapper의 helper identity 검사만 강화했고, 그 before/after hash를 capture manifest에 기록했다. C++ exporter와 링크 archive는 그대로다. 준비 도구 전체 단위검사43개 PASS는 별도 mock/소프트웨어 결과다.

`native_software_tests.py`는 새 ARM manifest/hash 및 ELF machine183을 검사한다. 명시적 `--run-tests`로 PTY20 → FULL gate21 → unsupported2 → scalar22/G1/G2 → carrier48/G1/G2를 실행한다. 첫 실패에서 후속 검사를 중단한다. 실제 AArch64 플랫폼 외 실행은 거부하며 기본 dry-run이다. PIPELINE/live producer의 ARM runtime은 이 작은 suite의 PASS 범위가 아니다.

새 준비 도구의 단위검사는 mock image/오류·package extraction·source/ELF 거부·probe/fixture 비교를 다룬다. 최종 실행 수는 RUN completion에 기록한다. Original production source/quantizer/reconstruction은 수정하지 않았다.

## 7. Portable package와 실행 순서

Package는 `host/frozen/{source,host,params}`, 원본 manifest, `hardware/original.bit`, x86 offline용 `flash/x86/{route.dcp,mkScuPipeline.v}`, 상대경로 tool/tests, vendor source, 새 fixture, 승인 요청 plan을 포함한다. x86 실행 파일·`.a/.o`·전체 build/experiment/cache는 넣지 않았다. Sealed host archive에 들어 있던 tracked vocabulary/test text는 source identity 보존을 위해 유지했고, 원래 checkout의 untracked 모델 weights는 복사하지 않았다.

`package.py`는 외부로 나가는 symlink, archive traversal/중복/link/device/special mode를 거부한다. 파일 hash·size·executable mode inventory와 외부 전달 archive SHA를 각각 기록한다. 빈 새 경로에 실제 추출한 뒤 verify, source materialization과 offline smoke를 수행한다. 같은 filesystem 경로 이동을 공간 확보로 계산하지 않았다.

Nano 주소는 추측하지 않는다. Archive와 `transfer.json`을 USB 또는 사용자가 지정한 기존 승인 경로로 복사한다. 원격 전송은 이번에0회다. 새 디렉터리로만 추출하며 `rsync --delete`, destination 전체 덮어쓰기를 사용하지 않는다.

Package README에 다음 실제 명령을 제공한다: verify → read-only probe → C++20 probe → 새 native build → ARM manifest/ELF 확인 → device-free tests → 새 ARM fixture export/비교. 필요한 tool이 없으면 정확한 probe 결과와 설치안을 검토하고, OS/JetPack/system compiler를 자동 교체하지 않는다.

## 8. Nano5 계획과 물리 연결

USB host 포트의 기존 Arty USB-UART를 사용한다. GPIO UART/고속 link로 바꾸지 않는다. Nano와 x86 USB host를 Y cable로 동시에 연결하지 않는다. 전원은 실제 revision manual과 Nano 예산 확인 후 결정한다. `/dev/ttyUSB1`을 Nano에 고정하지 않는다. `/dev/serial/by-id`, sysfs VID/PID/interface/serial, 계정 접근권한 및 점유를 새로 확인한다. Permission·ModemManager 문제는 좁은 변경안을 별도 승인받으며 전역 stop/chmod666/driver unbind는 하지 않는다.

기존 UART는 open 후 termios CS8/CREAD/CLOCAL,1Mbaud, TCSANOW를 적용하고 CAP를 즉시 전송한다. 명시적 DTR/RTS toggle/ioctl/tcflush와 reset 코드는 없다. 그러나 driver의 open 동작과 실제 배선의 영향까지 소스만으로 보증하지 않는다. HUPCL bit를 설정하지 않는 것과 보드 reset 무영향의 실측은 다르다.

CAP expected는 version3/profile0810/capability0294/capacity336×48×96이다. CAP는 boot image SHA나 전체 Flash/SRAM readback 증거가 아니다. Boot-only bit가 생기면 source/route 동일성 근거와 **새 명시적 hardware provenance/reference**를 만들고 별도 승인받아야 한다. ALLOW_UNPINNED 우회는 사용하지 않는다.

제안 명령은 기존 CLI 네 번, logical GEMM5개다. 마지막 두 seed는 같은 process다.

```text
scu_host_dispatch run 16 16 64 1 1 FULL
scu_host_dispatch run 321 48 96 3 1 FULL
scu_host_dispatch run 321 48 64 2 1 STRIPE_PIPELINE
scu_host_dispatch run 17 19 64 4 2 FULL
```

FULL cycle617/52687/1949를 해당 shape와 연결한다. PIPELINE은 stripe160/160/1, slot0/1/0, works63/fragments252이며 x86 elapsed29,023,476 equality를 강제하지 않는다. 예상 비교 integer/f_out 각각31,718, wire padding442, warm-up0·성능 반복0이다. 기존5회는 실제 saturation clamp가 발생한 board coverage가 아니다. Boundary GEMM을 추가하지 않는다.

Host의 reference load는 UART constructor 전, FULL actual cycle gate는 reducer/output commit/RELEASE 전이다. 정상 RELEASE와 실패 후 복구 명령은 구분한다. Hash/CRC/domain/identity/count/numeric/cycle/timeout/reset/fallback의 첫 오류에서 이후 process를 실행하지 않고 로그를 보존한다. Generic 외부 producer cancellation 전부의 destructor drain까지 무명령으로 보증하는 것은 기존 검증 범위가 아니며, 현재 ordinary5 계획에는 그런 injection이 없다.

## 9. 상태와 종료

| 항목 | 상태 |
|---|---|
|Board-proven original source/bit 이관 사본|Prepared / hash verified|
|DCP offline configuration 조회|Software-tested, 명시하지 않은 SPI 설정은 Unknown|
|Flash part/geometry/protection|Needs identification|
|Boot-only bit / 최종 MCS|NOT GENERATED|
|F1/F2 physical executor|NOT READY, 별도 식별·승인 필요|
|Nano environment|Needs target access|
|ARM64 full build/runtime|NOT RUN|
|Native build/ARM test recipe·portable fixture|Prepared; 실행한 x86 범위만 Software-tested|
|Flash backup/erase/program/verify·helper SRAM|모두0회|
|JTAG/UART physical open/CAP/cold boot/board GEMM|모두0회|
|Git staging/commit/push·기존 자료 삭제|모두0회|

이관 완료 조건인 “전원 재인가 후 JTAG 재programming 없이 SCU 부팅, ARM64 host의5개 correctness 유지”는 아직 검증하지 않았다. 다음 입력은 실제 Flash/PCB 식별 정보와 Nano 모델·OS/probe·허용 접속 경로다. Part/image/range/host가 확정된 뒤에만 F1/F2/F3/Nano5의 정확한 조합으로 별도 물리 작업 승인을 요청한다.
