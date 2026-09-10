# IFR2 Dense streaming contract

새 후보 전용이다. 기존 실보드 IFR1 artifact와 client는 frozen FULL에 보존한다.
Little-endian, CRC32 IEEE는 header+payload 전체에 적용하며 끝에 u32로 붙인다.
Pointer는 전송하지 않는다. UART는 1 Mbaud/8N1, FPGA는 자율 25 MHz다.

Request header는 32 B다. Offset 0 magic `IFR2`, 4 version=2(u8), 5 op(u8),
6 profile=0x0810(u16), 8 run_id(u64), 16 generation(u32),
20 x(u16), 22 y(u16), 24 z(u16), 26 t(u16), 28 payload length(u32).

| op | 이름 | x/y/z/t | payload 및 완료 |
|---|---|---|---|
| 0 | CAP | 0/0/0/0 | idle only. capacity 336/48/96, 현재 generation 반환 |
| 1 | FULL | M/N/K/0 | A(M×128) 뒤 W(K×64). CRC 이후 한 번 start, 완료 후 전체 block raw |
| 2 | RELEASE | 0/0/0/0 | matching run/generation, final done, 모든 stripe completion 회수 후 자원 해제 |
| 3 | ABORT | 0/0/0/0 | 명시적 fault-test/운영 호출만 허용. 자동 호출 없음. generation은 보존 |
| 4 | BEGIN | M/N/K/stripe_rows | W(K×64). 기존 AsyncStripes descriptor 한 번 시작. A는 아직 미공개 |
| 5 | PUBLISH | row_begin/row_count/stripe_id/slot | A(row_count×128). 순서·범위·slot=id%2 검사. CRC와 staging 이후 core publication |
| 6 | POLL | 0/0/0/0 | 완료가 없으면 flags=0/payload=0. 있으면 oldest stripe raw와 identity 반환 |
| 7 | FINISH | 0/0/0/0 | 모든 row 공개·stripe raw 회수 후 최종 core done/stats. 자원은 RELEASE까지 유지 |

K는 32/64/96이다. Native scale block은 K32, core fragment는 K16이다.
BEGIN의 stripe_rows는 기존 host geometry에서 온 값이며 16의 배수다.
M321/N48의 자동 geometry는 160/160/1, event slots는 0/1/0이다.
새 job은 generation=current+1 및 nonzero run_id를 사용한다. 모든 후속 명령은
현재 run/generation과 일치해야 한다. 다른 명령·length·profile·identity·CRC는
sticky failure다. 실패 후 자동 retry/reset/ABORT는 없다.

Response header는 144 B다. 0 magic `OFR2`, 4 version=2(u8), 5 status(u8),
6 profile(u16), 8 run_id(u64), 16 generation(u32), 20 payload length(u32),
24 M(u16), 26 N(u16), 28 K(u16), 30 reserved=0(u16).
32부터 u64 7개는 cycles, fragments, output works, A reads, W reads,
C writes, C acknowledgements다. 88 reply_op(u8), 89 flags(u8; bit0=completion),
90 stripe_id(u16), 92 row_begin(u16), 94 row_count(u16),
96 publish_cycle(u64), 104 completion_cycle(u64), 112 first_activation_cycle(u64),
120 first_activation_published_rows(u64), 128 host_wait_cycles(u64), 136 overlap_cycles(u64).
Unknown flag/status는 거부한다. CAP에서만 shape는 capacity이며 나머지는 logical shape다.

FULL raw 순서는 `[K32 block][logical row][ceil(N/16)][16 signed32 lanes]`다.
POLL raw는 같은 순서에서 해당 stripe의 local row 범위만 포함한다. Tail column
wire padding은 0이어야 한다. 기존 reducer callback은 tile-I/tile-J/block/row 순서로
호출하고 signed32를 signed64로 확장한다. FP scale은 기존 host metadata에서 적용한다.

POLL의 completion record는 모든 raw를 송신한 뒤 core에서 acknowledge한다.
Host는 raw 수신·검증·reconstruction 이후에만 frontend completion을 반환한다.
Host event acceptance, device transfer, publication, raw completion, reconstruction,
event slot retirement, 최종 caller output commit은 서로 다른 경계다.
전체 A backing과 전체 block raw backing을 사용하므로 event slot 재사용은 A overwrite가 아니다.

Watchdog은 frame interbyte gap, published work의 실제 무진행, host transaction timeout을
구분한다. 미공개 producer 대기와 미회수 completion 대기는 정상 상태다.
Host poll 횟수는 RTL cycles로 기록하지 않는다. Diagnostic delay는 성능 측정에서 제거한다.
