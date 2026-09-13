# IFR3 SCU block-scale candidate

이 경로는 ABI5 / `signed-scu-sat-v2` 후보이다. 기존 IFR2 bitstream
`8aef393d040bb306e6ddf7b4b977976a9a924dbc5aec62b37b1690f0aa18c0ca`는
External/host-scaled baseline이며 이 계약을 지원하지 않는다.

수치 명세와 실제 검증 상태는 [계약](../../docs/SCU_BLOCK_SCALE_CONTRACT.md),
[수정 보고서](../../docs/SCU_BLOCK_SCALE_FIX.md)를 기준으로 한다.

## 지원 경계

FPGA 후보는 A8/W8, physical DIM16, native Q8_H1/block32, EXSIA,
명시적 RMD OFF의 FULL/live PIPELINE을 지원한다. Logical capacity는
M336/N48/K96이고 K는 32의 배수다. HP1와 residual ON은 장치 시작 전에 거부한다.
Simulator의 H1/HP1 지원과 FPGA 지원을 구분한다.

기존 quantizer와 automatic geometry를 사용한다. M321/N48/K64와 K96의
stripe는 160/160/1, event slot은 0/1/0이다. 전체 A backing을 유지하며
행 prefix를 공개한다. Event slot은 별도 activation backing 두 개를 뜻하지 않는다.

H1의 `beta = uint32(code) + uint32(R)`를 전송한다. 각 K32 block의 두
K16 partial에 RTL op4를 적용하고 각각 SCU clamp와 accumulator clamp를 거친다.
전체 K 완료 후 한 개의 final integer plane을 반환한다. Host는 integer에
shared channel S와 해당 row/stripe의 K-shared activation scale만 적용한다.

## Wire / address

UART 1 Mbaud/8N1과 기존 framing 구조를 유지한다. 모든 정수는 little endian이다.

| 항목 | IFR3 계약 |
|---|---|
| Request / response magic | `IFR3` / `OFR3`, version3 |
| Profile | `0x0810` |
| Semantic capability | response offset30의 uint16 `0x0294` |
| Capability 의미 | storage4B, H1 multiply 지원, HP1 미지원, final-domain2, saturation revision2 |
| Request header | 32B; 이후 payload와 CRC32 4B |
| Response header | 144B; 이후 payload와 CRC32 4B |
| A 주소 | logical row ×128 + K byte |
| W 주소 | K row ×64 + J byte |
| Integer scale 주소 | block ×256 + J ×4 bytes |
| C 장치 주소 | logical row ×256 + J ×4 bytes, 단일 final plane |
| C wire payload | row마다 ceil(N/16)개의 16×signed32 vector; padded lane0 |

고정 opcode는 CAP0, FULL1, RELEASE2, ABORT3, BEGIN4, PUBLISH5,
POLL6, FINISH7이다. ABORT는 protocol 오류 시험 대상이며 자동 복구 동작이 아니다.
FULL payload는 A(M×128), W(K×64), beta((K/32)×256) 순서다.
BEGIN은 W와 beta를 고정 staging하고 PUBLISH는 해당 stripe의 A(rows×128)를 보낸다.
전송 packet이나 K fragment마다 새로운 logical GEMM을 만들지 않는다.

정상 FULL/FINISH 후 frontend fence와 전체 output 검증·commit을 마친 뒤 RELEASE한다.
CRC/profile/capability/id/length/count/수치 오류는 sticky failure이며 destructor는
fd만 닫는다. 실패 뒤 RELEASE/ABORT/reset/retry를 자동 전송하지 않는다.

## 저장소

| 저장소 | 실제 allocated / 유효 범위 |
|---|---|
| Architectural accumulator | 64 KiB, A8 signed32 |
| A staging | 4096×128bit =64 KiB; 최대 유효336×96B, 주소 span336×128B |
| W staging | 512×128bit =8 KiB; 최대 유효96×48B, span96×64B |
| Integer scale staging | 256×32bit =1 KiB; 최대 유효3×48×4B, span3×256B |
| Host-visible C | 2048×512bit =128 KiB; 최대 유효336×48×4=64512B, span336×256B |

C는 K-block plane을 제거하여 legacy IFR2의 256 KiB allocation에서 줄였다.
새 scale BRAM과 제어 비용을 더하므로 전체 면적 감소를 미리 보장하지 않는다.
Scale BRAM은 32bit 한 포트에서 lane을 읽어 16-lane response를 조립한다.
SCU multiplier factor 자체는 유효 unsigned17bit다.

## 명시적 source 재현

`build.py freeze`는 고정 core base → 기존 FULL fixed-core patch → 새 SCU src delta,
명시적 sim/frontend/provider overlay, pinned host → 기존 두 host patch → 새 companion
patch 순서로 구성한다. 최종 입력의 권위 있는 manifest는
`integration-sha256.json`이다. `source-sha256.json`은 이전 FULL freeze 단계의 중간 기록이다.
현재 root 전체를 frozen board source 위에 덮어쓰지 않는다.

```sh
python3 fpga/scu_block_scale/build.py freeze /absolute/fresh-snapshot \
  --host-repo /absolute/pinned-host --params-repo /absolute/pinned-include
python3 fpga/scu_block_scale/build.py native /absolute/fresh-snapshot
python3 fpga/scu_block_scale/build.py host-fpga /absolute/fresh-snapshot --jobs 2
python3 fpga/scu_block_scale/build.py bsc /absolute/fresh-snapshot
python3 fpga/scu_block_scale/build.py sim /absolute/fresh-snapshot --jobs 2
```

`host`는 native frontend test, `host-fpga`는 실제 ggml dispatch/UART adapter를 빌드한다.
Generated/cache와 linked archive는 같은 ABI5/profile/numerical revision에서 생성한다.
아래 실행은 script가 새로 만든 PTY만 사용하며 실제 UART 장치를 열지 않는다.

```sh
python3 fpga/scu_block_scale/run_pty.py \
  --rtl /absolute/fresh-snapshot/production/obj_dir/Vscu_uart_shell \
  --host /absolute/fresh-snapshot/fpga-host-build/scu_host_dispatch \
  --out /absolute/fresh-test run 16 16 64 1 1 FULL
```

물리 실행용 FULL reference는 새 bitstream/production RTL/fixture identity와 연결해야 한다.
과거 361/39907/58695를 재사용하지 않는다. Unpinned cycle test 허용은 명시적 환경 변수와
`/dev/pts/`에만 한정된다. 이 README는 programming 승인이 아니다.

## 관측 한계

Reply의 elapsed/work/fragment/A/W/C write·ACK/wait/engine overlap은 device cycle/count다.
Publication·stripe completion tally는 검증한 host response 개수다. Global first-A와
그 시점의 published prefix만 wire에서 관측한다. 순수 compute, stripe별 first-A,
독립 device publication total은 `not_exposed`로 남긴다.
Host steady-clock ns와 device cycle을 직접 빼지 않는다. RTL simulation wall time은
실보드 시간이나 speedup이 아니다. 기존 지연 주입 진단도 자연 overlap과 구분한다.
