# 기존 호스트 계약 기반 FULL multi-tile/multi-reduction GEMM FPGA replay

2026-09-09. **보드 승인 전 완료: dense FULL의 기존 host 입력·raw 결과·최종
`f_out`을 실제 UART board-top RTL까지 연결했고, 새 후보의 25 MHz route를
통과했다. 실보드 programming·측정은 Awaiting approval이다.**

이번 지원 범위는 A8/W8/physical DIM16, native Q8_H1, EXSIA activation,
**RMD OFF dense ablation**이다. Production ExSIA 전체, residual FPGA 실행,
PIPELINE 또는 모델 TTFT/TPOT 가속을 검증한 결과가 아니다.

실험 경로 `E`는
[`build/experiments/host-full-replay-20260909T103729Z`](../build/experiments/host-full-replay-20260909T103729Z)이다.
큰 RTL·waveform·DCP·로그는 E에, 새 구현·fixture·recipe는
[`fpga/full_replay`](../fpga/full_replay/README.md)와
[`synth/FullReplay.bsv`](../synth/FullReplay.bsv)에 남겼다. Staging/commit은 없다.

| 분류 | 결과 |
|---|---|
| Implemented | FULL executor hook, owned capture/replay, bounded UART adapter/provider/top, 오류·측정 harness |
| Simulated | 6 fixture, raw 5,510개 및 logical f_out 2,883개 exact; production/assertion UART RTL, vendor board-top 모두 완료 |
| Routed | xc7a100tcsg324-1, 25 MHz, setup/hold/pulse-width 통과 |
| Board measured | **NOT RUN**. 물리 UART/JTAG 접근·programming 없음 |
| Blocked / unsupported | PIPELINE, residual, 다른 profile/format, capacity 초과, native K 비정렬 |
| Awaiting approval | 아래 특정 bitstream의 SRAM programming 및 6 fixture 측정 |

## A. 소스 보존 및 선택

시작 branch는 `exp/pe-local-partial-int20`, HEAD는
`dc4a1a621f63834d64df42ae8c24152747d97971`, upstream은
`origin/fpga/arty-a7-100t`였다. 사용자 제공 17 tracked 수정과 6 untracked
문서가 실제 시작 상태와 일치했다. 요청한 read-only Git 결과와 binary diff는
E/baseline/00.txt..09.txt, 23개 파일의 사본/hash는 E/baseline/files 및
sha256.json에 있다. [최종 보존 검사](../build/experiments/host-full-replay-20260909T103729Z/baseline/preservation-final.json)는
23/23 동일하다. 사용자 파일에 reset/stash/restore/clean을 적용하지 않았다.

네 집합을 구분했다. A=커밋된 HEAD, B=시작 당시 dirty root,
C=기존 fixed P3A frozen source, D=실제로 빌드한 이번 후보.
192개 파일의 A/B/C 전체 SHA256은 [baseline.json](../fpga/full_replay/baseline.json),
핵심 27개와 host/build 계약 29개의 **전체 64자리 SHA256·선택 경로·차이 이유**는
[source_provenance.json](../fpga/full_replay/source_provenance.json)에 있다.

| 핵심 경로/역할 | B와 C | D 선택 및 이유 |
|---|---|---|
| src/common/Arithmetic.bsv | 다름 | C. 고정 A1 sign-extend multiply 표현 유지; root signedMul로 대체하지 않음 |
| src/common/Config.bsv, scripts/im2p_config.py, sim/ffi/im2p_config.h | 동일 | C. A8/W8/D16 및 기존 수치 width/generator 유지 |
| src/core/IM2PCore.bsv | 동일 | C. 이미 수정된 activation-response capacity guard 유지 |
| src/control/MatmulScheduler.bsv | 다름 | C. 검증된 별도 board scheduler |
| src/control/WorkScheduler.bsv | 다름 | C. 검증된 fragment/block scheduler |
| synth/ResidentP0.bsv | B에는 없음 | C는 baseline으로 보존. 새 top은 FullReplay provider를 사용 |
| synth/SynthA8W8D16.bsv | 동일 | C. high-level startMatmul core 입구 사용 |
| frontend header/source | 시작 시 동일 | C에 명시적 FULL executor 두 Options field와 분기 추가 |
| frontend/tests/test_frontend.cpp | 시작 시 동일 | pinned host의 timestamp 이름에 맞춰 4개 cycle→ns field 참조 수정 |
| Resident UART PHY | frozen artifact에 있음 | 기존 p0_uart를 byte 동일 module로 추출 |
| Resident top/XDC | frozen artifact에 있음 | MMCM/reset 유지, shell 이름만 변경; XDC byte 동일 |
| Host client / packet shell | root 소스에는 없음 | 새 versionable IFR1 protocol; P2 반복 packet을 확장하지 않음 |
| Makefile / ABI / simulator bridge | 동일 | C 유지. 신규 build.py/CMakeLists/route.tcl은 명시적 입력만 사용 |

C는 기존
`build/experiments/activation-publication-20260909-154059/work/fixed-source`다.
기존 candidate-fixed-01 manifest 24개 hash와 frozen 192개를 검증했다.
HEAD archive에 [fixed-core.patch](../fpga/full_replay/fixed-core.patch)를 적용해
192개를 C와 대조한 뒤, 신규 파일만 목록에 따라 넣었다. 통째 artifact overlay는 없다.

D의 **하드웨어**는 E/candidate-01/source 및 source-sha256.json으로 고정했다.
**Host executable**은 E/host-source-03, E/host-build-03이며 별도 hash manifest다.
마지막 framing/replay/측정 도구는 E/final-tools에 고정했다. 후속 host quantizer
초기화 수정은 hardware 파일을 바꾸지 않았고, routed D의 모든 입력은 현재
versionable hardware 파일과 일치한다. 편집 중인 root를 RTL build가 읽지 않았다.

Fixed P3A의 사용자 제공 artifact hash를 원본에서 다시 확인했다.
다음은 **과거 baseline**, 이번 후보 결과가 아니다.

| 과거 artifact | SHA256 |
|---|---|
| resident.bit | c0f21374d454090a64e4be072a686ddf999d00ce47bb566e0fdc3ae6d1065d83 |
| mkResidentP0.v | b7a127283b2c5476a46d3d162627d3c7ae5b753df6777bc738e08ff9b1f1b91f |
| route.dcp | 0a08e37497eba7e125482e74650cee0358d0eff5ff481ba3caa7a5c99630811b |

기존 45,680 LUT / 29,131 FF / 42 BRAM tiles / 22 DSP / 96.80% slices,
setup +10.444 ns / hold +0.019 ns / pulse +3 ns와 production/assertion 각
692 shapes는 기존 manifest/보고서의 검증 상태로 유지한다. 이번 실행에서
692-shape sweep을 새로 했다고 분류하지 않는다. 기존 fixed P3A 문서·manifest는
hardware_programming=false이며, 이전 P2 측정 기록을 새 P3A/FULL 측정으로 바꾸지 않았다.

**기존 361 core cycles와 412 batch-shell cycles는 M=N=16,K=32 workload의
값이며 새 multi-tile GEMM의 기대값이 아니다. 새 후보의 실보드 검증 여부는
실제 programming/측정 로그로 확인한다. 이번 후보에는 그런 로그가 없다.**

## B. Host commit, build와 계약 대응

Host `develop`은 local/remote를 확인해
`7a74ac29c9c8fa8fbc5d47cf073b8b9266e3e7a9`로 고정했다.
[remote 확인](../build/experiments/host-full-replay-20260909T103729Z/baseline/remote-develop.txt)
뒤 git archive한 E/host를 사용했다. sibling host tracked tree는 수정하지 않았고
untracked model 디렉터리는 읽어 build에 넣지 않았다.

GEMMINI_SW_PATH는 E/params로 고정한
`RISC-V-DynDNN-gemmini-include` commit
`cd2caed8d93e49d3f8873bd6c4e340fcad54b9e0`이다. 실제 gemmini.h/params는
repository root에 있고 include/ 두 파일은 forwarding header다. Host CMake가
생성한 gemmini_params.h를 include 순서 맨 앞에 둔다. 생성 header, CMake cache,
link command, 정적 archive를 hash로 묶었다. 기존 공개 ggml args와 canonical
C ABI v4는 변경하지 않았다. C++ Options 크기는 증가하므로 frontend/host는
함께 다시 빌드해야 한다.

실행 파일은 기존 llama CMake의 `ggml-gemmini`, `ggml-base` target을 실제
빌드·링크했다. CPU+HARDWARE 설정은 R0/기존 quantizer를 위한 라이브러리
설정이다. 새 runtime 이름 **FPGA_REPLAY_V1은 이번에 제안·구현한 replay
adapter 이름**이며 기존 Gemmini HARDWARE의 의미를 바꾸지 않는다.
모델 backend selection에 FPGA가 등록된 것은 아니다.

Host executable SHA256:
`02fc99b417016edc5efb34967eb2cb473bb9ef471436a560c607c2c4b7fc768a`.
기존 production simulator static archive SHA256:
`8956f8f9ee4d20295dcd072a0d1340be7b0905d8c70e440cbae636f3ecabb889`.
Archive는 test-hooks 없이 다시 빌드했고 native Verilator runtime을 포함한다.
Host/frontend 뒤 simulator archive, pthread/dl/m 순으로 링크한다. FPGA replay
run마다 linker wrap으로 sim_create=0, execute_matmul=0을 검사한다. R1은 각각 1이다.

아래 줄 번호는 D snapshot 기준이다. `H:`는 E/host의
`ggml/src/ggml-gemmini/`, `F:`는 frontend/src/im2p_gemmini_frontend.cpp,
`C:`는 canonical sim/include/im2p_sim.h, `R:`은 D의 src/, `P:`는
fpga/full_replay/다. 각 경로의 source SHA는 위 provenance JSON에 연결되어 있다.

| Host argument/의미 | frontend → canonical/provider | RTL field/state → board 표현 |
|---|---|---|
| I/J/K | H:ggml-gemmini-args.h:230 → F:full_descriptor:1153, m/n/k; C:77 | R:core/IM2PCore.bsv:1846 startMatmul row/column/reductionCount → IFR1 u16 M/N/K, bounded |
| tile_I/J | H:args.h:388, geometry.hpp:85의 DIM factor → F:166 normalize_tile_count가 factor×DIM, extent 및 DIM으로 clamp → tile_i_rows/j_columns | R:control/MatmulScheduler.bsv:292/381 work → FullReplay:127의 16/16; tail extent는 scheduler가 제한 |
| tile_K | H:geometry.hpp:100 → F scalar snapshot만; FULL descriptor에는 없음 | R:control/WorkScheduler.bsv:298, remaining K/DIM/block 경계로 fragment 결정. packet에 수치 fragment 명령 없음 |
| activation_rows_per_stripe | H:args.h:245, activation_geometry → F:449 snapshot, activation metadata index | FULL transport는 전부 staging. stripe publication 구현으로 광고하지 않음 |
| activation_row_offset | H:args.h:244 → F:349 snapshot_activation_scales의 metadata index | A view offset과 별도. v1 fixture는 metadata offset 0만 허용, A view bytes를 직렬화 |
| A layout/stride | QuantizedActivationBuffer, F:1743 bounds → d.activations/activation_row_stride_bytes | P:capture.cpp:147에서 row copy → device 0x10000, stride128, signed8. 포인터는 전송 안 함 |
| W layout/format | native Q8_H1 [J][K/32], H:quants/common/weight_reader.cpp:511 → F:936 read_weight_i8(k,j,count) | 기존 reader로 ≤16 lanes씩 staging → device 0x20000, K×64 byte rows; 별도 quantizer 없음 |
| A/W precision/storage | H:args route bits 및 buffer bits → C:79 ABI4 A8/W8/D16, byte storage | packet profile0x0810와 version1 정확히 검사. 다른 precision/format start 전 거부 |
| block_size_k | H1 native32 → F:814 provider_block_size, F:1180 block_size32 | FullReplay:129, WorkScheduler block state32. Native quantizer/artifact reader는 K%32=0 요구 |
| Scale 종류/위치 | H1 factor=double(s_rf)×double(c_b+R), EXSIA activation exponent → F:846/866 factor, F:1014 read_scale | VectorExternal. RTL에 identity signed8 1을 공급; 실제 FP scale/metadata는 owned host에 유지 |
| Scale context/tag | F:full_descriptor의 work_context=0; C:102, sim/src/simulator/matmul.rs:254 | 전송 session의 run_id64→ScaleContext, generation32→job id. provider request tag 그대로 응답; canonical 0을 고유 run이라 주장하지 않음 |
| Accumulate/replace | F:836 External 선택 → d.vector_op | R:control/WorkScheduler.bsv:336, block 첫 fragment replace/동일 block 후속 accumulate. R:core/IM2PCore.bsv:1585 block 완료마다 C publish |
| C raw layout | C:59 signed64 provider callback `(block,row,col,count,values)`; F:1043 검증 | R:core/IM2PCore.bsv:1612/1936의 block stride. device 0x40000+(block×M+row)×256+j×4, wire signed32 |
| callback 순서 | F:1043의 row%DIM reducer, block sequence 검사 | P:capture.cpp:172에서 tile_i,tile_j,block,row 순 복구; signed32→signed64 sign extension. UART chunk는 block 의미가 아님 |
| f_out stride/layout | H:args.h:378 → F:760 private output, F:1080 reconstruction, F:788 commit | wire raw를 기존 double reducer에 입력, 마지막 block에서 float32. row/column stride holes는 그대로 유지 |
| Dense raw completion | provider 성공 후 sim ACK, sim/src/simulator/matmul.rs:464 | FullReplay writeOutput:96 BRAM write+ACK, core.matmulDone 후 reply. raw done만으로 caller output 성공 공개 안 함 |
| Residual completion/merge | H:ggml-gemmini-im2p.cpp:864, F:1400 residual_pending/semantic completion | 이번 dense RMD OFF에는 residual 없음. FULL executor+residual stage는 F:1684에서 unsupported |
| Authorization/fence | F:1991 fence 및 :2077 authorize_output_commit | 모든 CRC/id/count/raw 검사·callback 완료 뒤 성공 fence commit. 실패 시 destination sentinel 보존 |
| Ownership/lifetime | A shared owner 유지; F:638 native W/metadata deep copy; callback은 call-borrowed | owned A/W/result는 RELEASE 전 overwrite 금지. 전송 중 A 수정 금지, context는 fence/Run 파괴까지 유효 |
| Unsupported/sticky | F:178 route 분류, :270 policy, :1138 set_error | P:protocol.py packet/decode/UART.failed 및 full_uart.sv:150 CHECK. 오류 후 simulator/reference fallback 없음 |

문서와 코드의 차이도 반영했다. frontend README의 “입력 buffer를 복사하지
않음”은 W/metadata에는 맞지 않는다. 실제로 A owner는 retain, W/scale metadata는
deep copy한다. 기존 runtime-args observer(H:ggml-gemmini-im2p.hpp:254)는
site/layer/opaque만 전달해 payload capture에 쓸 수 없다. 없는 payload hook을
가정하지 않고, 실제 host quantizer/tiler로 만든 args를 harness의 FULL execute
직전(P:capture.cpp:221)에 선택 필드로 capture했다. Production 모델 호출을
intercept한 capture라고 표현하지 않는다.

## C–E. Fixture, adapter 및 bounded storage

연결 선택은 **C: canonical replay adapter부터 시작**, 그리고 기존 reconstruction을
유지하기 위한 **A: FULL execution 한 지점 분리**다. F:1240 run_full에 선택적
executor를 넣었다. 기본 simulator 분기는 유지되며 hook을 선택하면 simulator
handle 생성 전에 분기한다. 기존 physical Gemmini 경로나 ggml public args는
변경하지 않았다. 공개 repeat_count, generic batch API, queue 증가는 없다.

Frozen ResidentP0는 고정 resident workload만 노출하지만 내부에는 high-level
core가 있다. 새 FullReplay는 같은 mkSynthA8W8D16.startMatmul에 **logical GEMM
하나를 한 번** 제출한다. A/W를 다 staging하고 CRC 검증한 뒤 core가 모든
I/J tile·K fragment를 자율 처리한다. Host가 K fragment마다 UART 왕복하지 않는다.

Portable fixture는 [6개 소스 디렉터리](../fpga/full_replay/fixtures)에 있다.
Schema/정확한 endian·필드·wire offset은 [README](../fpga/full_replay/README.md)에
기록했다. `ggml_gemmini_args_t` 전체 메모리나 포인터를 serialize하지 않는다.
복원 시 A/weights/metadata/output은 owned buffer를 만들고 bind한다.
Native Q8_H1은 qs32/c_b/s_rf/R 39-byte wire fields에서 44-byte native block으로
복원한다. Native quantizer, activation quantizer, tiler와 W reader는 기존 코드를 재사용했다.

Scale FP metadata는 기존 External reconstruction이 host에서 소비하므로 board에
다른 scale로 변환해 보내지 않는다. 장치의 실제 S 요청에는 기존 계약대로
identity 1을 반환한다. 이 S sideband와 block transition을 실제 RTL monitor로
관찰했다. 원래 signed32 block raw를 모두 K 끝까지 정수로 합치는 변경은 없다.

| Fixture | Host tile factor I/J/K | outer I/J/K / ws_inner_calls | A staging B | W staging B | raw valid / wire B | f_out allocation B |
|---|---|---|---:|---:|---:|---:|
| 16/16/32 | 1/1/2 | 1/1/1 / 1 | 2,048 | 2,048 | 1,024 / 1,024 | 1,216 |
| 16/16/64 | 1/1/4 | 1/1/1 / 1 | 2,048 | 4,096 | 2,048 / 2,048 | 1,216 |
| 16/16/96 | 1/1/6 | 1/1/1 / 1 | 2,048 | 6,144 | 3,072 / 3,072 | 1,216 |
| 32/48/64 | 2/3/4 | 1/1/1 / 1 | 4,096 | 4,096 | 12,288 / 12,288 | 6,528 |
| 17/19/64 | 2/2/4 | 1/1/1 / 1 | 2,176 | 4,096 | 2,584 / 4,352 | 2,788 |
| changed 16/16/32 | 1/1/2 | 1/1/1 / 1 | 2,048 | 2,048 | 1,024 / 1,024 | 1,216 |

Host A는 K-byte stride, W native storage는 J×K/32×44 bytes다. 위 A/W는 padding까지
포함한 **실제 장치 staging 비용**이다. Host FP scale은 native block당 7-byte
관련 필드(c_b/s_rf/R), activation metadata는 8-byte scalar +2×theta_count다.
장치 scale BRAM은 0 bytes(identity logic), descriptor packet은 32 bytes다.

Architectural accumulator는 기존 **64 KiB allocated**, 한 DIM16 work의 live
footprint는 최대 1,024 bytes다. 별도 host-visible raw result BRAM은 **32 KiB
allocated**, block-major address span은 최대 24 KiB다. A BRAM4 KiB, W BRAM8 KiB,
control은 one invocation이다. UART input assemble16B/output latch64B/header32B와
counter/register state는 별도다. P3A 반복 retained-result 64 KiB를 복제하지
않았다. K 증가 시 A/W staging과 필요한 K32 block results는 늘지만 모든 K16
partial C를 보관하지 않는다. 큰 모델 weight 전체 materialization이나 double
buffer는 없다. Capacity 초과는 명시적으로 거부하며 shape/output을 줄이지 않는다.

CAP/RUN/RELEASE/idle ABORT 의미와 CRC32, 길이, profile, generation 검사 모두
새 IFR1 protocol로 명시했다. Generation 증가·메모리 소유권 해제는 한 logical
invocation 단위다. 버튼 reset은 generation을 유지한다. Timeout/오류 후 host
transport는 sticky failure이며 새 성공 결과로 stale completion을 쓰지 않는다.
UART framing 오류가 결과 전송 중 발견되면 성공 CRC도 유효하게 내보내지 않는다.

## F–G. 동일 fixture의 실제 검증

R0는 기존 `MatMul(reference).run_full()` CPU numerical path의 f_out과 기존
Gemmini `matmul_cpu_int32`의 K32 raw block reference다. 새 dot-product/quantizer를
만들지 않았다. R1은 같은 frozen production core의 기존 simulator/provider
bridge와 기존 frontend reconstruction이다. R2는 packet bytes→실제 UART
shell/provider/core→raw response decoding→같은 frontend reconstruction이다.
별도로 실제 Arty top/IBUF/BUFG/MMCM은 Vivado `unisims_ver`로 실행했다.

**최종 PASS artifact:**

| 검증 | 근거 |
|---|---|
| R0 capture + production R1/R2, passive address/scale proof | [production-final/summary.json](../build/experiments/host-full-replay-20260909T103729Z/production-final/summary.json), 각 r1/r2.log 및 events.json |
| Assertion-enabled UART numerical | [asserted-final/summary.json](../build/experiments/host-full-replay-20260909T103729Z/asserted-final/summary.json), `NUMERICAL_REPLAY_PASS`, 12 transactions |
| 실제 board-top RTL + 최종 reconstruction | [board-top-fixed-reconstruction/SUMMARY.md](../build/experiments/host-full-replay-20260909T103729Z/board-top-fixed-reconstruction/SUMMARY.md), status.json, source/transport hashes |
| 기존 A8/W8/D16 전체 154개 | [regression-154/README.md](../build/experiments/host-full-replay-20260909T103729Z/regression-154/README.md), 29 suites, failure/assertion marker 0 |
| M9/N1/K1, assertion benches, P2/P3A | [evidence-legacy/README.md](../build/experiments/host-full-replay-20260909T103729Z/evidence-legacy/README.md) |
| 기존 frontend 계약 + 새 hook 실패/거부 | E/frontend-contract.log의 PASS, E/control.log |
| 실제 UART fault RTL | [faults-01/summary.json](../build/experiments/host-full-replay-20260909T103729Z/faults-01/summary.json), 20 transactions |
| partial I/O, integrity/measurement log gate | E/protocol-final-tests.log, 4/4 |

각 fixture의 logical invocation은 1이다. 아래는 shape 추측이 아니라 실제
core counters, output address와 fragment block state를 검사한 값이다.

| Shape | RTL works | K fragments | block transitions | A/W/S requests | C writes/acks | R1 cycles | R2 production cycles | assertion cycles |
|---|---:|---:|---:|---|---|---:|---:|---:|
| 16/16/32 | 1 | 2 | 0 | 32/32/1 | 16/16 | 344 | 361 | 376 |
| 16/16/64 | 1 | 4 | 1 | 64/64/2 | 32/32 | 648 | 665 | 710 |
| 16/16/96 | 1 | 6 | 2 | 96/96/3 | 48/48 | 954 | 971 | 1046 |
| 32/48/64 | 6 | 24 | 6 | 384/384/11 | 192/192 | 3881 | 3947 | 4217 |
| 17/19/64 | 4 | 16 | 4 | 136/256/7 | 68/68 | 1987 | 2021 | 2111 |
| changed 16/16/32 | 1 | 2 | 0 | 32/32/1 | 16/16 | 344 | 361 | 376 |

합계: logical6, works14, fragments54, block transitions13, C writes/acks372.
Host outer/ws count1과 RTL fragments24는 서로 다른 단위임을 M32/N48에서
확인했다. S request는 cache/reuse 때문에 단순 works×blocks와 같지 않다.
실제 output address는 모든 write에 대해
`0x40000+(block*M+row)*256+j*4` 및 tile_i,tile_j,block,row 순서로 exact 검사했다.
연속 6개 RUN은 A/W/S metadata를 바꿔 전달했고 reset 없이 run/generation이
증가하며 다른 f_out을 얻었다.

M17/N19는 DIM tail 및 f_out column stride2/row stride41을 통과했다.
나머지도 row stride J+3이므로 padding sentinel 보존을 함께 검사했다.
Native K33은 quantizer 이전에 거부했고 E/native-k33-rejection.log에 있다.
Low-level K tail은 기존 M9/N1/K1 및 154개 raw-core 회귀로 별도 검증했다.

수치 비교는 처음부터 **raw signed32 exact, f_out float32 bit-exact**로 고정했다.
호스트 callback signed64는 raw int32 sign-extension이며 accumulator width가
64-bit가 된 것이 아니다. External block마다 기존 wrap/replace 경계를 유지하고,
기존 reducer가 `double(raw)*weight_factor*activation_scale`을 순서대로 더해
마지막 block에서 float32로 cast한다. 사후 epsilon 확대는 없다. K64 모든 열은
서로 다른 scale2개, K96 모든 열은 3개다. logical f_out 2,883개 모두 nonzero다.

Bounded native K32 block은 overflow-free다. 경계값은 기존 suite의
`deliberate_a8_full_k_overflow_wraps_to_int32_min`,
`external_block_reset_preserves_raw_blocks_before_reconstruction`,
`full_arithmetic_wraps_at_profile_width_before_final_output`,
`raw_full_output_preserves_profile_wrap_and_final_saturation` 등으로 검증했다.
이 overflow test를 새 board protocol에서 실행했다고 주장하지 않는다.

Production과 assertion의 cycle 차이는 위처럼 분리했다. 합성에 assertion
RTL을 쓰지 않았다. 검사 허용 기준은 양쪽 raw/f_out/count 정확성과 runtime
assertion/finish 없음이며, cycle 동등성은 요구하지 않았다. R1과 R2의 차이는
기존 simulator service와 실제 BRAM provider의 pipeline/control latency가 다르기
때문이다. Board-top과 production UART RTL의 core cycle은 같은 값이었다.
실보드 UART/backpressure latency에 대해 이 동등성을 미리 보장하지 않는다.

Vendor simulation은 1493.30 s wall time 동안 실제100 MHz 입력/MMCM25 MHz/
UART1 Mbaud를 실행했다. 완료 marker는
`BOARD_TOP_COMPLETE transactions=12 jobs=6 period_ns=40 uart_baud=1000000 vendor_unisims=true`.
Runtime ERROR/FATAL/assertion 0, simulation time625,799,355 ns다.
이 wall time은 simulator 실행 비용이며 FPGA 측정 시간이 아니다.

M9 최소 재현은 production/asserted 각각 4 jobs/36 outputs, returns/publications/
feeds36, monitor 위반0이다. Cycles162/168이며 기존 합성용 clock과 별개의
synthetic 20 ps half-period testbench다. 별도 BSC assertion benches4/4는 DIM2
semantic bench다. Legacy UART production/asserted 각각 P2 135 jobs/34,560 outputs,
P3A326 jobs/115,968 comparisons, late-error2 PASS. Decoder 각9 captures/27 corrupt
reject, PTY각7/7 PASS. 기존 artifact 입력650개와 archive638개 hash를 보존했다.
이는 새 IFR1 bitstream이 P2/P3A wire command를 지원한다는 뜻이 아니다.

## 실패 기록과 fault coverage

초기 capture는 `ggml_init` 전에 native weight quantizer를 호출해 FP16 lookup
table이 초기화되지 않았다. s_rf와 logical f_out이 모두0이었다. **이 초기
f_out PASS는 scale 검증으로 무효 처리했다.** ggml_init을 기존 quantizer 앞에
옮기고 양수·서로 다른 scale 검사를 추가했다. 현재 소스 fixture와 최종 R0/R1/R2는
모두 수정된 nonzero metadata를 사용한다. 원본 로그/fixture는 삭제하지 않았다.

수정 전후 A/W staging6개와 UART request12개가 byte-identical임을 확인했기
때문에 vendor RTL의 실제 raw 응답은 그대로 유지하고, 수정된 host metadata로
reconstruction을 다시 실행했다. VectorExternal의 FPGA S 값은 기존 계약대로
1이므로 FP metadata 수정이 hardware stimulus를 바꾸지 않는다. 최종 host03은
6회 모두 simulator 생성/실행0이고 expected는 비교에만 사용했다.

그 밖의 실패도 보존했다: 초기 W reader에 count48을 넘긴 실패는 기존 ≤16
callback 제한에 맞춰 분할했고, 초기 provider의 BSV implicit-guard 결합은 A/W/S/C
각 request rule에서 오류를 검사하도록 수정했다. Native External도 S sideband가
필요함을 확인해 identity 응답을 추가했다. 이 hardware 수정은 candidate-01을
고정하기 전에 완료했다. 기존 frontend test timestamp 필드 불일치와 capture
control lambda의 반환형 compile 오류도 수정 후 재빌드했다.

Sandbox xsim은 exit0인데 Tcl 오류로 completion marker가 없었다. PASS로
취급하지 않고 software simulation 권한으로 재실행했다. Vendor 완료 뒤 구형
host01에 call-counter stdout이 없어 후처리가 중단된 기록도 보존했다. 최종
host03 후처리의 actual response exact 비교가 최종 근거다. Legacy wrapper의
timeout/후처리 NameError는 실제 RTL 완료 기록과 분리해 남겼다.

| Fault | 이번 coverage |
|---|---|
| Partial send/receive | PTY에서 매번1 byte, 실제 protocol.UART 전송 함수. Framing용이며 numerical mock PASS 아님 |
| 잘못된 length/profile/capacity/CRC | 실제 UART RTL 거부; core launch 없음 검사 |
| Duplicate/stale generation, 다른 run id, 늦은 완료 | RTL 요청 identity/RELEASE 검사 + decoder 응답 mismatch/중복 payload 거부 |
| Partial frame timeout, sticky error, idle abort | 실제 UART RTL 수행 |
| Reset/이전 generation 재사용 | RTL reset 수행, generation 보존 및 stale 거부 |
| 연속 서로 다른 A/W/S | 6개 수치 fixture의 연속 RUN/RELEASE |
| Output backpressure | 실제 UART TX ready stall과 output latch 보존 수행 |
| 강제 BRAM write-ACK backpressure | **NOT RUN**. 새 provider의 write+ACK는 같은 edge, 주입 포트 없음 |
| RUN 중 abort packet | **NOT RUN/unsupported**. idle abort만 지원; 버튼 reset 별도 |
| UART stop-bit break fault 주입 | **NOT RUN**. PHY 오류 sticky logic은 존재하지만 이 입력 주입은 안 함 |
| 원래 socket 기반 legacy test | **NOT RUN**. 새 PTY 경로 결과와 구분 |

마지막 read-only 검토에서 measurement가 exit0만 확인하는 누락과 passive
events 파일이 없을 때 검사를 생략하는 누락을 찾았다. 측정은 numerical/backend
PASS marker까지 강제했고, FULL replay는 events 누락 시 실패하도록 고쳤다.
Assertion의 기존 driver는 `--numerical-only`로 명시해 별도 결과 이름을 쓴다.
수정된 고정 도구로 production/assertion 6개를 다시 완료했다.

## H. 새 candidate의 implementation 및 승인 패키지

Fresh source/generated/cache/report 경로에서 Vivado2025.2로 synth/opt/place/route와
bitstream 생성을 완료했다. Target은 **Arty A7-100T / xc7a100tcsg324-1 / 25 MHz**다.
[Route reports](../build/experiments/host-full-replay-20260909T103729Z/candidate-01/route)는
수치 datapath나 timing exception으로 실패를 숨기지 않은 실제 결과다.

| Post-route 항목 | 이번 candidate |
|---|---:|
| LUT / FF | 46,124 / 30,126 |
| RAMB36 / RAMB18 / equivalent tiles | 27 / 1 / 27.5 |
| DSP | 72 |
| Occupied slices | 15,589 / 15,850 = **98.35%** |
| Control sets | 787 |
| 최대 non-clock fanout | 10,574, activeWeightBankReg |
| Congestion | 기본 level5 초과 window 없음; 더 낮은 level은 이 보고서 범위 밖 |
| Setup WNS / Hold WHS / Pulse-width | **+10.870 / +0.024 / +3.000 ns** |
| Timing failing endpoints | 0 |
| Routed nets / route errors | 78,154 / 0 |
| Black boxes / unconstrained internal endpoints | 0 / 0 |
| CDC | report_cdc: All paths are Safely Timed |
| DRC | 159 warnings: DPIP-1 21, DPOP-1 69, DPOP-2 69; critical/error0 |

DRC 경고는 DSP pipeline 권고이며 숨기지 않았다. Vivado 전체102 warnings와
DRC159 violations는 다른 집계다. 정상 synchronous 경로에 새 false_path나
multicycle을 넣지 않았다. 기존 XDC 그대로 UART RX input delay1개, UART TX와
LED output delay5개는 비동기 외부 I/O로 남는다. 내부 closure와 외부 UART
baud/CDC 신뢰성을 동일한 timing 검증으로 취급하지 않는다. 실제 occupied slice
여유가261개뿐이므로 이후 interface 확장은 새 구현 결과가 필요하다.

새 bitstream:
[`E/candidate-01/route/full-replay.bit`](../build/experiments/host-full-replay-20260909T103729Z/candidate-01/route/full-replay.bit)

**SHA256 `c3713784732eb9f88221d921278b102589f7834499f7ca1c0eaa1307f9f02f78`**

Routed DCP SHA256:
`aa47dab9a5f52acfbb2760aa8696af69e8642462bb078d4859eab2f477342f1f`.
Production mkFullReplay.v SHA256:
`553c2339bd0f3df421a63b62ef27342f7d41694bdf1abe4caf69c0eb06f158bb`.
Core patch SHA256:
`edc4e9eab7d66adb23ffee88c30a41c087b6c73b19f41127605ee1b99c0a1c5c`.
Board source manifest SHA256:
`0a82d0e307dd9cf55a6a42428a58b9b2d927352877de1607982fbdc57a26825e`.

**승인 요청 대상**은 이 hash의 bitstream만 configuration SRAM에 programming,
programming verification 뒤 아래6 fixture를 smoke→K64→K96→multi-tile→tail/stride→
changed 순서로 측정하는 것이다. JTAG die 식별과 implementation package/speed
grade를 따로 기록한다. Flash/non-volatile write 및 이전 bitstream 자동 복구는
금지한다. 측정 중 승인 source/bitstream/host/fixture를 바꾸지 않는다.

Fixture당 warm-up1회 제외, 측정5회, 총36 logical GEMM이다. 매번 FULL 입력을
전송하며 resident replay counter와 transport-inclusive 시간을 분리한다.

| Fixture | RUN+RELEASE request B | response B | 합계 B |
|---|---:|---:|---:|
| 16/16/32 | 4,168 | 1,224 | 5,392 |
| 16/16/64 | 6,216 | 2,248 | 8,464 |
| 16/16/96 | 8,264 | 3,272 | 11,536 |
| 32/48/64 | 8,264 | 12,488 | 20,752 |
| 17/19/64 | 6,344 | 4,552 | 10,896 |
| changed | 4,168 | 1,224 | 5,392 |

6 fixture 1회 합계62,432 B, warm-up 포함6회와 최초 CAP136 B를 합쳐 **374,728 B**다.
1 Mbaud 8N1의 순수 wire 하한은3.74728 s이며 전체 측정 wall time 예측이 아니다.
Host deadline은 transaction당5 s, device interbyte watchdog10.48576 ms,
core watchdog671.08864 ms다. CRC/profile/id/count/numerical mismatch, timeout,
unexpected reset, source/hash 변경에서 즉시 중단하고 자동 retry/programming하지 않는다.

[`measure.py`](../fpga/full_replay/measure.py)는 승인 bit SHA와 별도 programming log를
요구한다. 이 작업에서는 --help와 software log gates만 실행했다. 실제 UART를
열지 않았다. Programming log 존재만으로 live SRAM 일치를 추정하지 않고,
별도의 programming/verification 기록을 먼저 검토해야 한다.

측정 domain은 다음처럼 고정한다. Resident는 staging 뒤 hardware cycles×40 ns
(nominal25 MHz). Transport-inclusive FULL은 **prequantized owned fixture**의
prepare/pack, input transfer, start/execute/result receive, 기존 reconstruction,
output commit까지다. Host subprocess/file I/O 비용도 포함한다. 최초 quantization/
capture 비용은 이 harness loop에서 제외되므로 모델 호출 전체 비용이라고
표현하지 않는다. Matched simulator는 같은 fixture/production core/reconstruction/
output 확보 범위와 같은 subprocess overhead를 사용하며 UART는 없다.
Quantization까지 포함하는 모델 path 성능 측정은 후속 항목이다.

Median/min/max, 실패·재시도 수를 남기고, 실패는 첫 건에서 종료한다. 이번
승인 전에는 physical performance sample이 없어 speedup을 계산하지 않았다.
이전 P2의1.528364 ms/job를 새 workload 분모로 사용하지 않는다. CPU cycles,
dense RTL cycles, residual simulator cycles 또는 host poll 수를 합산하지 않는다.
Completion-only를 전체 f_out service와 비교하지 않는다.

## I. PIPELINE 후속 연결 계약 / 미지원

현재 FPGA FULL executor는 PIPELINE을 실행 전에 unsupported로 거부한다.
기존 `progress_stream(1)`은 simulator 한 cycle 전진이며 physical FPGA polling
API로 의미를 바꾸지 않았다. FULL pre-stage 완료를 software pipeline과 동등하다고
주장하지 않는다.

H:quants/act/exsia/exsia.hpp:375 StripeReadyEvent에는 run/stripe/slot/row 범위와
immutable activation metadata, owned residual handles, 시간 endpoint가 있다.
F:1920 submit_stripe는 identity/order/row bounds를 검사하고 metadata snapshot을
보관한다. F:594의 event slot2개는 semantic outstanding capacity다. Host는
H:ggml-gemmini.cpp:1545에서 A backing을 한 번 할당하므로 두 개의 재사용 가능한
activation buffer라는 의미가 아니다.

Residual-enabled 경로 F:1552는 raw in-flight1 및 residual_pending 정책을 사용한다.
F:1400의 residual/checked merge가 끝나야 semantic completion/outstanding release가
된다. Raw dense completion과 semantic capacity2를 같은 queue depth로 취급하면
안 된다. 기존 일반 frontend lookahead와 residual-enabled 진행 정책도 다르다.
별도 dense/residual simulator handle이 두 physical core를 의미하지 않는다.

후속 구현은 기존 activation_geometry의 stripe_rows로 **최소3 stripes**를 만든다.
새 임의 tiler를 만들지 않고, 0/1/0 event slot 재사용에서 run/stripe identity를
검사한다. Delayed publication, semantic capacity backpressure/retry, accepted A
overwrite 방지, immutable theta/scale, residual pending을 각각 지연시키고 최종
fence/authorize 뒤에만 output commit하는지 검사한다. Quantization ready와 FPGA
transfer complete는 별도 상태이며, 전송/실행이 끝나기 전 A backing을 재사용하지 않는다.

현재 미지원은 residual-enabled ExSIA, residual CPU/simulator hybrid, FPGA residual,
arbitrary activation metadata offset/view capture, 다른 precision/format, physical
DIM32/64, K%32≠0 native capture, capacity 초과, streaming model weights,
PIPELINE/queue extension이다. 여기서 residual을 조용히 끄거나 대체한 성공은 없다.
처음부터 이름과 manifest에 dense RMD OFF ablation을 고정했다. Residual을 같은
FPGA core에 넣으려면 기존 merge/semantic completion 계약과 자원 직렬화부터
증명해야 하며 이번 FULL 최소 경계의 후속 작업이다.
