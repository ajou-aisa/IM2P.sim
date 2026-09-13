# SCU Flash migration: 공식 문서와 승인 경계

확인일: 2026-09-11. 도구 기준: Vivado 2025.2. 이 문서는 오프라인 조사와 Tcl 검토 자료다. 문서 작성 중 장치 연결, JTAG/UART 거래, JEDEC 조회, Flash readback, helper SRAM programming, Flash programming, reset을 실행하지 않았다.

현재 보드 revision, Flash 실장 부품 전체 번호, JEDEC ID, status/configuration register 값은 **Unknown**이다. 실제 부품을 확정하지 않은 상태에서는 아래 F1/F2 템플릿을 실행할 수 없다. 과거 SCU SRAM/GEMM 승인은 Flash 접근 승인이 아니다. 별도 승인이 있기 전에는 기존 Flash/non-volatile write 금지가 유지된다.

## 1. 보드와 Flash 식별

Digilent 공식 제품 페이지는 Arty A7-100T의 FPGA를 `XC7A100TCSG324-1`, QSPI Flash를 16 MB로 명시한다. 현재 판매품에는 S25FL127S 또는 S25FL128S가 실장될 수 있으며, 두 부품은 기능적으로 동일하지 않다. 이는 실제 연결 보드의 부품 식별 결과가 아니다. [Digilent 제품 사양](https://digilent.com/shop/arty-a7-100t-artix-7-fpga-development-board/)

공식 PCN의 Arty A7-100T 항목은 SKU `410-319-1`, 최초 영향 PCA `E.1`, 참조번호 `IC4`, 실장 선택지 `S25FL128SAGMFx00` / `S25FL127SABMFx00`을 기록한다. 영향 제품의 sticker로 실장 선택지를 표시한다. PCN은 SFDP 지원, 최대 동작 주파수, erase geometry 차이를 구분한다. 따라서 revision만으로 부품을 확정하지 않는다. [Digilent S25FL127S PCN](https://files.digilent.com/resources/programmable-logic/documents/S25FL127S_PCN.pdf)

공식 E.2 schematic의 6쪽은 `IC4 S25FL128SAGMFI00` SOIC16, 대체 위치 `IC3 S25FL128SAGNFI00` MLP8/NoLoad 및 둘 중 하나만 실장하는 조건을 나타낸다. Flash 전원은 3.3 V다. 회로도 명목 부품을 실제 실장 부품으로 대체하지 않는다. [Arty A7 E.2 schematic](https://digilent.com/reference/_media/programmable-logic/arty-a7/arty-a7-e2-sch.pdf)

[공식 Reference Manual](https://digilent.com/reference/programmable-logic/arty-a7/reference-manual)은 이번 웹 조회에서 HTTP 403이었다. 오래된 Micron 실장 revision에 관한 비공식 mirror 설명은 이 후보의 선택 근거로 사용하지 않았다.

Vivado 2025.2 설치 파일 `/tools/Xilinx/2025.2/Vivado/data/xicom/xicom_cfgmem_part_table.csv`를 읽기 전용으로 확인했다.

| 행 | canonical NAME | 관련 alias | 해석 |
| --- | --- | --- | --- |
| 66 | `s25fl128sxxxxxx0-spi-x1_x2_x4` | `s25fl127s-spi-x1_x2_x4` | Artix-7, 128 Mbit, Infineon |
| 128 | `s25fl128sxxxxxx1-spi-x1_x2_x4` | 없음 | 별도 부품 항목. 위 항목과 임의 교환 금지 |
| 71 | `mt25ql128-spi-x1_x2_x4` | `n25q128-3.3v-spi-x1_x2_x4` | Artix-7, 128 Mbit, Micron. 현재 보드 실장 증거 없음 |

이 표는 도구의 지원 목록이다. 자동 chip detection 결과가 아니다. 실제 부품의 suffix, erase geometry, 전압과 대조한 뒤 정확히 한 항목을 선택한다. [AMD 2025.2 Artix-7 configuration memory 목록](https://docs.amd.com/r/2025.2-English/ug908-vivado-programming-debugging/Artix-7-Configuration-Memory-Devices)

## 2. F0: 오프라인 설계와 파일 생성

원본 routed DCP/bitstream을 보존한다. 별도 Vivado 세션에서 원본 DCP를 읽고 다음 property를 기록한다. 지원하지 않는 property는 `Unknown` 또는 `UNAVAILABLE`로 남기고 기본값을 추측하지 않는다.

| 확인 대상 | 목적 |
| --- | --- |
| `PART` | 구현 package/speed grade 확인. JTAG die 이름과 별개 |
| `CONFIG_MODE`, `BITSTREAM.CONFIG.SPI_BUSWIDTH` | boot mode와 SPI 폭 확인 |
| `CONFIG_VOLTAGE`, `CFGBVS` | configuration bank 전압 계약 확인 |
| `BITSTREAM.CONFIG.CONFIGRATE`, `BITSTREAM.CONFIG.SPI_FALL_EDGE` | Flash configuration clock 조건 확인. SCU core 25 MHz와 별개 |
| `BITSTREAM.STARTUP.STARTUPCLK`, `BITSTREAM.CONFIG.UNUSEDPIN` | startup 및 helper 사용 중 I/O 조건 검토 |
| `BITSTREAM.GENERAL.COMPRESS` | 생성 옵션 identity 기록 |

실제 값은 이 문서에 추정해서 채우지 않는다. 원본 DCP에서 빈 property는 unset이며, 실제 bitstream에 기록된 값이나 공식 기본값 확인을 대신하지 않는다. 7-series의 master SPI clock/edge 의미는 [AMD UG470](https://docs.amd.com/api/khub/documents/FOs3lXmlcWxBhTIFxVKyGA/content)을 따른다. 원본 DCP를 수정하거나 bitstream을 다시 생성하면 결과는 새 artifact다. 동일 RTL이라는 이유로 기존 programming 승인을 재사용하지 않는다.

`write_cfgmem`은 파일 변환 명령이다. `-size`는 MBytes이며 power of two다. 확인된 128 Mbit Flash라면 값은 `16`, 주소 범위는 `0x000000`–`0xFFFFFF`다. `-interface`는 명시해야 하며 기본값은 SPI가 아닌 `SMAPx8`이다. [AMD UG835 2025.2 write_cfgmem](https://docs.amd.com/r/2025.2-English/ug835-vivado-tcl-commands/write_cfgmem)

```tcl
# Offline only. All variables must come from the reviewed manifest.
# Example memory_size_mbytes is 16 only after 128-Mbit capacity is confirmed.
write_cfgmem -format mcs -size $memory_size_mbytes \
    -interface $confirmed_interface \
    -loadbit [list up $approved_offset $approved_bitstream] $fresh_mcs
```

SPIx1/SPIx2/SPIx4 선택은 bitstream과 Flash 계약에 맞춘다. `write_cfgmem -interface SPIx4`만으로 기존 bitstream의 boot configuration을 수정했다고 간주하지 않는다. `-disablebitswap`, `-force`, `-quiet`를 추가하지 않는다. 생성물의 memory byte 표현은 원본 `.bit`와 다를 수 있으므로 원본 bitstream 파일을 Flash readback과 그대로 비교하지 않는다. 생성 MCS의 주소·길이·checksum을 검증하고 변환된 memory byte domain으로 비교한다. 원본/생성 파일은 각각 SHA256을 기록한다.

## 3. F1: 별도 승인 후 식별·백업할 때의 경계

**현재 F1 상태는 Blocked / NOT RUN이다.** 실제 부품과 도구의 접근 방법을 확정해야 한다. 최소 승인 내용은 정확한 cable/target/die, 도구 버전, 선택 cfgmem part, helper 파일과 hash, helper SRAM programming 횟수, Flash read 범위, timeout, 중단 조건이다. 이전 SCU 구성이 helper로 교체된다는 점을 포함한다. 종료 후 SCU 자동 복원, reset, boot, UART CAP는 포함하지 않는다.

AMD indirect programming은 FPGA에 별도 helper bitstream을 올린다. Flash 배열을 읽는 목적이어도 기존 SRAM 실행 구성을 유지하지 않는다. helper의 unused I/O pull 상태도 달라질 수 있다. `PROGRAM.UNUSED_PIN_TERMINATION`은 보드와 원본 설계 조건에 맞춰 확정해야 하며, 기본 pull-down을 그대로 안전하다고 가정하지 않는다. [AMD UG908 2025.2 programming 설명](https://docs.amd.com/r/2025.2-English/ug908-vivado-programming-debugging/Programming-a-Configuration-Memory-Device), [AMD XAPP1220의 property 명칭](https://docs.amd.com/api/khub/documents/t~dW97dP90PeLCe1FSaY8w/content)

아래는 승인된 target이 이미 열렸고, 필요한 식별·hash gate가 끝났을 때의 명령 순서다. 지금 실행 가능한 완성 script가 아니다. 모든 `$approved_*`, `$confirmed_*`, `$fresh_*`는 검토된 manifest 값이어야 한다.

```tcl
# F1 approval required before any hardware operation in this block.
set devices [get_hw_devices xc7a100t_0]
if {[llength $devices] != 1} { error "Expected exactly one approved FPGA" }
set dev [lindex $devices 0]
current_hw_device $dev

set parts [get_cfgmem_parts $confirmed_cfgmem_name]
if {[llength $parts] != 1} { error "Expected exactly one confirmed cfgmem part" }
create_hw_cfgmem -hw_device $dev [lindex $parts 0]
set cfgMem [current_hw_cfgmem]
set_property PROGRAM.ERASE 0 $cfgMem
set_property PROGRAM.BLANK_CHECK 0 $cfgMem
set_property PROGRAM.CFG_PROGRAM 0 $cfgMem
set_property PROGRAM.VERIFY 0 $cfgMem
set_property PROGRAM.UNUSED_PIN_TERMINATION $approved_unused_pin_termination $cfgMem

set helper [get_property PROGRAM.HW_CFGMEM_BITFILE $dev]
create_hw_bitstream -hw_device $dev $helper
# STOP: seal the generated/resolved helper bytes and SHA256.
# Loading is a separate stage after that exact helper receives F1 approval.
program_hw_devices $dev
# STOP on programming/configuration failure. No retry or recovery.
readback_hw_cfgmem -all -format bin -file $fresh_backup $cfgMem
```

helper 경로 문자열만으로 파일의 존재나 hash를 가정하지 않는다. 도구가 helper를 생성·선택한 뒤 실제 파일을 봉인하고, 그 파일의 승인 이후 별도 단계에서 SRAM에 올린다. 준비 단계에 hw_device 접근이 필요하면 그 접근부터 승인 대상이다. 위 템플릿을 한 번에 실행하지 않는다.

UG835 2025.2의 `readback_hw_cfgmem`은 cfgmem 객체를 **마지막 위치 인자**로 받는다. `-all`은 전체 배열, `-offset`은 시작 주소, `-datacount`는 byte 수다. 기본 형식은 MCS이므로 BIN은 명시한다. `-quiet`는 내부 오류에도 TCL_OK를 반환할 수 있어 금지한다. [AMD UG835 2025.2 readback_hw_cfgmem](https://docs.amd.com/r/2025.2-English/ug835-vivado-tcl-commands/readback_hw_cfgmem)

공식 readback 예제에 있는 `PROGRAM.ERASE/CFG_PROGRAM=1`을 F1에 복사하지 않는다. 배열 backup은 QE/status/configuration/OTP/security register의 backup을 포함하지 않는다. 16 MiB 배열 파일과 hash만으로 전체 비휘발 상태의 완전 복원 가능성을 주장할 수 없다.

또한 위 공개 문서는 helper/driver가 어떠한 비휘발 register도 변경하지 않는다는 보증을 제공하지 않는다. 따라서 **Flash 배열 readback 지원**과 **모든 non-volatile write가 0임을 입증**하는 것은 별개다. 후자가 F1 요구라면 실제 부품 및 도구 경로의 근거를 확보하기 전에는 이 템플릿을 실행하지 않는다. 공개 2025.2 Tcl 문서만으로 확인된 일반 raw JEDEC/QE register read API는 이번 조사에서 찾지 못했다. 존재하지 않는 명령을 만들어 넣거나 2026.1의 `cfgmem_util` 기능을 2025.2 기능으로 취급하지 않는다.

## 4. QE와 protection register

S25FL128S의 `CR1[1] QUAD`는 비휘발 bit다. Quad mode는 WP#/HOLD# 핀을 I/O로 전환한다. BP 보호 상태, SRWD, BPNV, TBPROT/TBPARM, FREEZE는 역할과 쓰기 수명이 다르며 일부는 OTP다. 보호를 풀기 위한 일괄 status clear/WRR를 넣지 않는다. [Infineon S25FL128S/S25FL256S datasheet, Configuration Register 및 Block Protection](https://www.infineon.com/dgdl/Infineon-S25FL128S_S25FL256S_128_Mb_16_MB_256_Mb_32_MB_FL-S_Flash_SPI_Multi-IO_3_V-DataSheet-v21_00-EN.pdf?fileId=8ac78c8c7d0d8da4017d0ecfb6a64a17)

S25FL127S도 QUAD가 비휘발이며, WP#/HOLD#와 보호 동작을 함께 검토해야 한다. sector 구성은 S25FL128S와 다르다. `TBPARM` 및 `SR2 D8h_O` 같은 OTP 항목은 일반 재설정 가능한 옵션이 아니다. 제조 설정을 바꾸는 동작은 제안하지 않는다. [Infineon S25FL127S datasheet, Configuration Register 및 Status Register 2](https://www.infineon.com/assets/row/public/documents/10/49/infineon-s25fl127s-128-mb-16-mb-3.0-v-spi-flash-memory-datasheet-en.pdf)

현재 QE/protection 값은 Unknown이다. SPIx4가 필요하다는 이유로 QE를 자동 설정하지 않는다. 필요성이 확인되면 register별 before/after 값, 비휘발 영향, 복원 가능성을 별도 승인 대상으로 제시한다. OTP/eFUSE 쓰기는 포함하지 않는다.

## 5. F2: 별도 승인 후 Flash programming할 때의 경계

**현재 F2 상태는 Blocked / NOT RUN이다.** F1의 부품·배열 backup·보호 상태 확인 후 승인 문서를 확정한다. F2는 새 MCS/BIN 및 원본 bit/DCP의 전체 SHA256, 정확한 시작 주소·유효 길이·erase sector 범위, 범위 밖 보존 방법, helper, 사용 도구를 고정한다. chip erase를 기본값으로 쓰지 않는다. `use_file`만으로 erase sector 경계 밖 byte 보존이 입증되지는 않는다.

아래는 승인된 helper가 실행 중이고 F2 gate가 통과한 이후의 명령 템플릿이다. erase/program은 비가역 영향이 있는 실제 Flash 작업이다. 현재 실행하지 않는다.

```tcl
# F2 approval required. Reuse only the verified, approved cfgMem association.
set_property PROGRAM.FILES [list $approved_mcs] $cfgMem
set_property PROGRAM.ADDRESS_RANGE {use_file} $cfgMem
set_property PROGRAM.ERASE 1 $cfgMem
set_property PROGRAM.BLANK_CHECK 1 $cfgMem
set_property PROGRAM.CFG_PROGRAM 1 $cfgMem
set_property PROGRAM.VERIFY 1 $cfgMem
program_hw_cfgmem $cfgMem
# No retry/reset/restore after any failure.
readback_hw_cfgmem -all -format bin -file $fresh_post_program_backup $cfgMem
```

UG835 2025.2는 `program_hw_cfgmem`도 cfgmem 객체를 위치 인자로 받도록 정의한다. 실행 step은 위 property로 선택한다. 본문은 `PROGRAM.FILES`, 일부 예제는 단수 `PROGRAM.FILE`을 쓰므로 실행 전 실제 2025.2 객체의 property 목록을 확인하고 지원되는 명칭을 고정한다. 오류를 숨기는 `-quiet`는 사용하지 않는다. [AMD UG835 2025.2 program_hw_cfgmem](https://docs.amd.com/r/2025.2-English/ug835-vivado-tcl-commands/program_hw_cfgmem)

최종 판정은 programming/verify 로그, 독립 readback의 memory byte 비교, 변경 대상 외 배열 보존 비교를 구분한다. helper programming/configuration status 확인은 전체 SRAM readback 검증이 아니다. Flash 배열 verify도 power-on boot 및 SCU 수치 검증을 대신하지 않는다.

Flash 부팅을 위한 reset, PROG, 전원 재인가, boot mode 변경, SCU 복원 programming 및 후속 CAP/GEMM은 별도 승인 범위와 정확한 횟수가 필요하다. 실패 첫 건에서 중단하며 자동 retry/reset/복원을 실행하지 않는다. 원본 파일과 실패 로그를 유지하고, 현재 경로 불변 검사와 변경 전 blob의 동일 hash 보존을 따로 보고한다.

## 6. 구현한 오프라인 검사기

`flash_prepare.py`는 stdlib만 사용한다. Intel HEX의 record 길이/checksum, EOF, 확장 주소, 중복/충돌, 용량 및 승인 범위를 검사한다. 이번 단일 load image는 연속 주소만 허용한다. type 00/01/02/04 이외 record는 거부한다. MCS의 load bytes를 명시된 원본 bitstream payload slice와 exact 비교한다. `.bit` container나 configuration packet의 의미를 새로 해석하는 도구는 아니다.

`load_payload.source_offset/length`는 원본 파일 내 byte 범위다. `load_payload.sha256`은 bit swap 전 slice hash다. `bitswap`은 `none` 또는 `reverse-per-byte`로 명시한다. 뒤에 허용할 `0xFF` padding 길이도 `padding_bytes`로 고정한다. `image_range`, `erase_range`는 byte 단위 반개구간 `[start, end)`다. erase region은 `offset/sector_bytes/count`로 전체 Flash를 겹침 없이 설명해야 한다.

필수 plan 필드는 다음과 같다. 실제 식별·근거 파일 없이 문자열만 채워 넣는 것은 승인 근거가 아니다.

```text
schema_version = 1
fpga_part = xc7a100tcsg324-1
flash_part = confirmed canonical catalog name
capacity_bytes = 16777216
part_evidence, geometry_evidence, boot_interface_evidence
interface, boot_interface = confirmed matching SPIx1/SPIx2/SPIx4
bitstream = {path, sha256}
load_payload = {source_offset, length, sha256, bitswap, padding_bytes, evidence}
image_range, erase_range
erase_regions = [{offset, sector_bytes, count}, ...]
tool = {path, sha256, version: "2025.2", startup_evidence}
```

`--plan`만 지정하면 기본 `--check-plan` 검사만 수행한다. 출력 파일을 만들거나 도구를 실행하지 않는다. Unknown 부품은 명시적으로 거부한다.

`--generate`는 유효 plan과 tool hash를 확인한 뒤 새 디렉터리에 Tcl/명령을 남긴다. 생성 Tcl은 version 확인과 `write_cfgmem`만 수행한다. Vivado startup 설정도 사전 검토 대상이다. `TMPDIR/TMP/TEMP`는 새 출력 디렉터리의 `tmp`로 고정한다. 원본 stdout/stderr, 명령, 시작/종료 UTC, exit code와 timeout 상태를 보존한다. 도구 실패/timeout은 재시도 없이 종료한다. 생성 MCS까지 payload 검사를 통과해야 `OFFLINE_MCS_PASS`를 기록한다. 이 표시는 Flash programming/boot PASS가 아니다.

```sh
python3 fpga/scu_migration/flash_prepare.py --plan confirmed-plan.json
python3 fpga/scu_migration/flash_prepare.py --plan confirmed-plan.json --check-mcs candidate.mcs
python3 fpga/scu_migration/flash_prepare.py --plan confirmed-plan.json --generate NEW_OUTPUT_DIRECTORY
python3 -m unittest discover -s fpga/scu_migration -p test_flash_prepare.py -v
```

`--execute`는 항상 명시적으로 거부한다. F1/F2 hardware executor는 **미구현**이다. approval 또는 backup 필드를 임의 추가해도 실제 programming에 진입하지 않는다. 부품/원본 boot 적합성이 Unknown인 현재 상태에서는 실제 MCS를 생성하지 않았다. 테스트는 작은 합성 데이터와 mock subprocess만 사용한다. 실제 Vivado 변환, Flash 접근 및 boot 검증은 **NOT RUN**이다.
