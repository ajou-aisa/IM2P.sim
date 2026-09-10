# Dense PIPELINE host/device 계약 검토

검토 기준은 routed **stream-02 hardware**와 frozen **stream-08 host/source**다. FULL 원본을 새 결과로 재분류하지 않는다. 최종 SHA256과 HEAD/dirty-root/frozen-FULL/stream-02/stream-08 대응은 [source_provenance.json](source_provenance.json), [최종 검증](../../build/experiments/dense-pipeline-20260909T141302Z/evidence/final-provenance-review-host08.json)에 있다. 초기 stream-04 계약 및 원본 wire CRC 기록인 [contract-review.json](../../build/experiments/dense-pipeline-20260909T141302Z/evidence/contract-review.json)은 이력으로 보존한다. 이 문서 작성 중 물리 장치를 열지 않았다.

## Source identity와 재현 경계

- `N`: `build/experiments/dense-pipeline-20260909T141302Z`.
- `H08`: `N/stream-08/host/ggml/src/ggml-gemmini`; 아래 host 줄 번호는 patch 적용 후의 이 파일이다.
- `S08`: `N/stream-08/source`; frontend/adapter는 이 frozen source를 가리킨다.
- `B02`: `N/stream-02/source`; RTL/provider/shell/constraint는 실제 routed 입력을 가리킨다.
- Host pin `7a74ac29c9c8fa8fbc5d47cf073b8b9266e3e7a9`; include pin `cd2caed8d93e49d3f8873bd6c4e340fcad54b9e0`.

`fpga/dense_pipeline/build.py:30`은 기존 FULL freeze를 호출한다. FULL recipe `fpga/full_replay/build.py:34`의 순서는 baseline HEAD archive → `fixed-core.patch` → frozen core hash 검사 → 명시한 frontend/FULL source replacement다. 이어 DensePipeline/provider와 versionable integration 파일만 명시적으로 선택한다. Host는 pin archive → `host-integration.patch` → `producer-observation.patch`; params는 별도 pin archive다. 각 snapshot의 `selection.json`, `source-sha256.json`, `integration-sha256.json`이 실제 source 집합이다. Dirty root 전체를 overlay하지 않는다.

| Source | S08 SHA256 앞 16자리 | B02와 S08 |
|---|---|---|
| src/common/Arithmetic.bsv | be54894777f7b396 | 같음 |
| src/core/IM2PCore.bsv | 8613eb740ceb5875 | 같음 |
| src/control/MatmulScheduler.bsv | 9425338cb4ddd12a | 같음 |
| src/control/WorkScheduler.bsv | 61844e9d29989d7b | 같음 |
| synth/DensePipeline.bsv | 37e4a2919f293575 | 같음 |
| fpga/dense_pipeline/dense_uart.sv | 11b4095f3f374089 | 같음 |
| frontend/src/im2p_gemmini_frontend.cpp | f0cb0aabc33838a7 | 같음 |
| fpga/dense_pipeline/ggml-gemmini-fpga.cpp | b3cc8f1cc1ed7d51 | 다름: host 변경 |

전체 hash는 JSON에서 확인한다. Core/provider/shell/clock top/XDC의 B02↔S08 내용이 같아도 새 host executable은 별도 artifact다. 최종 보고용 문서 변경을 S08 frozen manifest에 소급 반영하지 않는다. 빌드 입력인 S08 source는 그대로 보존한다. H08와 S08를 함께 빌드하여 C++ Options layout, canonical ABI v4, A8/W8/D16 include를 맞춘다. `CMakeLists.txt:22–33`의 C++20·protocol2·source fingerprint와 simulator archive 링크를 각각 기록한다. Archive가 링크됐다는 사실과 선택된 invocation의 simulator 실행 여부는 다르다.

## 실제 host entry와 수락 의미

새 selector는 `GGML_GEMMINI_EXECUTION_BACKEND=FPGA_UART`다. 기존 HARDWARE/IM2P_SIM 의미는 유지된다. Compile gate는 WS/A8/W8/DIM16/INT/EXSIA/block32/RMD OFF다. 기존 `resolve_matmul_options()`(`H08/ggml-gemmini-matmul.hpp:706`)의 FULL/STRIPE_PIPELINE 모드를 사용한다. Runtime override를 허용한 빌드에서만 `GEMMINI_MATMUL_MODE`가 유효하다. `IM2P_FPGA_DEVICE`는 명시적 transport 선택이며 UART constructor의 OPEN/CAP은 실제 실행 때 한 번 발생한다. 따라서 승인 전에는 PTY만 지정한다.

실제 호출은 `ggml_backend_gemmini_graph_compute()`(`H08/ggml-gemmini.cpp:2515`) → `ggml_backend_gemmini_mul_mat()`(`:1254`) → 기존 route/auto tiler(`:1445`, `:1561`) → `ggml_gemmini_fpga_execute()`(`:2112`)다. 지원은 native Q8_H1 weight/F32 activation/K32·64·96/M≤336/N≤48이다. `supports_op()`(`:2737`)와 실행 전 gate(`:1304`, `:1445`)가 format/shape를 검사한다. 선택된 FPGA 실행 실패는 graph `GGML_STATUS_FAILED`(`:2599`)로 돌아간다. Host08 adapter는 원본 A의 bits8/row_offset0/rows/cols/stride/span을 device open과 accepted A replacement 전에 검사한다(`S08/ggml-gemmini-fpga.cpp:169–173`). 원본 metadata는 device open 전에 `none` 또는 residual payload가 없는 EXSIA인지 확인하고(`adapter:33,174`), FULL quantization 뒤 실행 전 다시 검사한다(`:210`). PIPELINE도 quantization 종료 후 fence 전에 검사한다(`:238`). 따라서 기존 residual metadata를 초기화로 감추지 않는다. D가 있으면 `low_D`를 int32 bias read 전에 거부한다(`:179`). 잘못된 원본 profile을 A8 staging으로 감추지 않는다. 실제 작은 ggml graph integration test와 실제 모델 capture는 구분한다.

| 단계/데이터 | 생성자 → 소비자 / 수명·완료 조건 | 고정 source 위치 |
|---|---|---|
| I/J/K, tile factors | ggml tensor → 기존 args/auto geometry → frontend descriptor. tile factors는 DIM 단위다 | H08 `ggml-gemmini.cpp:1285,1561`; `ggml-gemmini-args.h:376` |
| Host stripe와 RTL work | M321/N48/K64·96의 tiles10/3/4·6, stripe160/160/1. frontend `min(tile*DIM,extent,DIM)`으로 canonical I/J work16/16; board도16/16 | S08 frontend `:166,1783`; B02 `synth/DensePipeline.bsv:168` |
| tile_K / K block | tile_K는 metadata. RTL은 DIM16, 남은 K, K32 scale boundary로 fragment를 나눔. 2개의 K16은 한 K32에 replace/accumulate, 다른 K32 결과는 분리 | B02 `src/control/WorkScheduler.bsv:108,115`; provider `:168` |
| HOST_READY | 실제 ExSIA folding commit 뒤 event 생성. run/stripe/slot/row 범위와 theta를 값으로 snapshot. 아직 준비 안 된 미래 theta를 사용하지 않음 | H08 `quants/act/exsia/exsia.hpp:375`; `.cpp:1990,2910,2933` |
| EVENT_ACCEPTED | `submit_stripe()`는 outstanding 2에서 **blocking**. credit 확보 후 theta→해당 row의 activation scale 복사, ready queue 등록과 next ID/row 증가가 성공해야 수락 | S08 frontend `:1957,1980,2003,2020` |
| Sink 반환 | sink true 이후 producer scratch slot release. false는 `StripeReadySinkFailure`이며 retry 요청이 아님. Adapter는 frontend 수락을 기다린 뒤 true | H08 ExSIA `.cpp:2937`; S08 adapter `Pipeline::ready():78–107` |
| Device publish backpressure | 별도의 `UART::publish()`는 pending 2이면 `IM2P_BACKPRESSURE`; 이때 transfer/identity/ownership 변경 없음. frontend worker가 POLL로 진행한 뒤 같은 event 재제출 | S08 `uart.cpp:363,377`; frontend `:1594` |
| Host A backing / accepted A | producer의 전체 A는 별도 유지. PIPELINE adapter가 I×K의 별도 owned A를 할당하고 post-fold row를 submit 전에 복사. frontend는 이 accepted A owner를 retain. Producer failure의 원본 A zero-fill이 in-flight transfer를 훼손하지 않음. slot0/1/0은 A 재사용이 아님 | S08 adapter `:95–100,213–216`; frontend `:537,547,1262` |
| W/scale ownership | native Q8_H1 packed block은 execute 시 deep copy; codes와 block factors가 함께 고정. PIPELINE activation scales는 execute 때 NaN 초기화, 수락한 event마다 row 범위만 채움 | S08 frontend `:675,846,1859,2003` |
| DEVICE_TRANSFER_COMPLETE | BEGIN은 W 전체를 먼저 보냄. PUBLISH는 stripe A만 bounded packing. UART CRC 검사 및 마지막 BRAM write 완료 뒤 publication 허용 | S08 `uart.cpp:350,380`; B02 shell `:190,223,250` |
| DEVICE_PUBLISHED | row 범위는 논리 row다. device A는 full backing, address `0x10000+row*128`, slot-local 주소로 바꾸지 않음. core는 publication prefix 밖 A를 읽지 않음 | B02 provider `:89,155,189`; shell `:60,167` |
| Host run → wire identity | Host event run_id는 completion context로 보존. UART invocation run_id/generation은 별도. core가 stripe ID/context0/1/2 생성; host는 pending identity로 original context 복원 | S08 `uart.cpp:331,371,389,421`; B02 core `:1991` |
| RAW_COMPLETE | 마지막 I/J work와 K32 output write ACK가 끝난 stripe만 core completion. shell POLL은 오래된 completion의 raw만 반환. UART 송신 drain 뒤 core completion ACK | B02 core `:2275`; MatmulScheduler `:238`; provider `:145,213`; shell `:225,279` |
| HOST_RECONSTRUCTION_COMPLETE | UART CRC/profile/run/gen/shape/count/order/padding 검사 뒤 callback 실행. signed32 raw를 signed64로 sign-extend하며 accumulator64로 변경하지 않음 | S08 `uart.cpp:184,124,399,418` |
| Dense SLOT_RETIRED | UART poll이 reconstruction까지 완료한 completion을 반환하고 frontend가 host context/row/cycle을 확인한 뒤 outstanding 감소. RMD OFF에서는 residual pending 단계 없음 | S08 frontend `:1320,1342,1374` |
| fence/authorization | 모든 stripe 제출·raw completion·FINISH 후 fence. PIPELINE은 별도 `authorize_output_commit(true)`. 이 true는 RMD OFF의 성공 조건이며 residual 실행을 뜻하지 않음 | S08 frontend `:1640,2028,2120`; adapter `:239,245` |
| caller f_out / RELEASE | frontend destination 자체를 adapter private stage로 둠. 최종 logical cells만 caller에 복사하므로 padding 보존. RELEASE 후 최종 성공; RELEASE 오류면 caller logical cells를 이전 값으로 복원 | S08 adapter `:218,259,267,274`; UART `:456` |
| Physical progress / timeout | FPGA autonomous clock과 POLL을 분리. simulator의 `progress_stream(1)`은 그대로. frame/interbyte, published core progress, host transaction timeout은 별도; unpublished 대기와 completion drain은 정상 상태 | S08 frontend `:1478`; B02 shell `:147`; S08 UART `:189` |

첫 event 수락 후 CPU는 다음 stripe quantization을 진행할 수 있다. Host08의 producer checkpoint(`adapter:129–131`, `H08/exsia.cpp:2646`)는 각 stripe 시작의 steady-clock timestamp를 항상 기록한다. `quantization_end_ns`는 post-fold sink 진입 시각(`adapter:81,105–106`)이며 `host_checkpoint_to_postfold` 구간이다. 기존 profiling-disabled event의 0 timestamp를 실제 측정값으로 바꾸어 부르지 않는다. 인위적 checkpoint/first-A 대기는 명시적 test 환경변수로만 켜고 performance loop에서는 끈다. Host timestamp와 RTL counter는 다른 clock domain이다. 첫 A read와 그때의 published rows는 FPGA counter이고, host send 시각을 그 값으로 대체하지 않는다. Causal barrier로 관찰한 overlap과 지연 없는 자연 overlap은 따로 보고한다.

## 수치·주소·live storage

`S08/frontend/src/im2p_gemmini_frontend.cpp:1044`의 기존 External reducer는 callback마다 `double(raw) * weight_factor * activation_scale`을 기존 block 순서로 누적한 뒤 float32로 변환한다. Q8_H1 factor는 packed `s_rf*(c_b+R)`(`:871`), activation scale은 stripe의 `2^theta`다. Device identity S=1과 host FP scale은 별개다. Wire block-major raw를 `UART::reconstruct():124`가 tileI/tileJ/block/row 순서로 callback한다. 모든 K32 raw를 먼저 정수 합산하지 않는다.

Canonical ABI는 `S08/sim/include/im2p_sim.h:41`의 v4다. Provider `:49–74`, full/stripe descriptors `:76–133`, activation stripe `:135–154`, completion `:156–172`를 사용한다. Pointer 값은 UART packet에 실리지 않는다. 요청 header32+CRC4, 응답 header144+CRC4 외 byte ordering은 [PROTOCOL.md](PROTOCOL.md)에 고정했다.

| M321/N48 live storage | K64 | K96 |
|---|---:|---:|
| Host producer A codes / native packed W | 20,544 / 4,224 B | 30,816 / 6,336 B |
| Host08 PIPELINE accepted A 추가 owned allocation | 20,544 B | 30,816 B |
| Host theta values / per-row float scale | 6 / 1,284 B | 6 / 1,284 B |
| Existing reducer double payload / factor cache | 2,048 / 256 B | 2,048 / 256 B |
| Logical f_out, 한 stage의 payload | 61,632 B | 61,632 B |
| Device A span / allocated | 41,088 / 65,536 B | 41,088 / 65,536 B |
| Device W span / allocated | 4,096 / 8,192 B | 6,144 / 8,192 B |
| Device raw valid / address span / allocated | 123,264 / 164,352 / 262,144 B | 184,896 / 246,528 / 262,144 B |
| Architectural accumulator allocated / DIM16 work live | 65,536 / ≤1,024 B | 동일 |

정정: [GEOMETRY.md](GEOMETRY.md)의 초기 `Host External reducer double values 123,264 B`는 실제 현재 live reducer allocation이 아니다. 실제 코드는 `reducers[DIM].sums[DIM]`(`frontend:806–812`)이고 double payload는 2,048 B다. Seen/ID/cache metadata와 vector capacity/allocator overhead는 별도다. Host08 PIPELINE은 원본 producer A 외에 accepted A를 추가로 보유하고 전체 invocation에서 각 유효 byte를 한 번 복사한다. 이 복사는 service timer 안에 포함된다. FULL에는 이 추가 A 복사가 없다. Host에는 native W 원본+frontend copy, frontend output stage+adapter private stage+RELEASE rollback용 previous logical output, UART `last_raw`와 response buffer도 각각 존재한다. 위 표는 이들을 하나의 shared storage라고 합치지 않는다. Output stride가 51인 portable fixture는 stage span이 logical f_out보다 크다. ExSIA 내부 CPU workspace/residual plane allocation은 RMD OFF에서도 있을 수 있으나 FPGA residual 실행을 뜻하지 않는다.

A/C는 두 event slot에 맞춰 덮어쓰는 ring 대신 bounded full backing을 선택했다. 동일 power-of-two BRAM geometry에서 두 stripe slot도 A64KiB/C256KiB가 필요하며 추가 address mux/tag가 생기기 때문이다. W는 invocation마다 다시 staging하고 RELEASE 후 재사용을 가정하지 않는다. C 주소는 `0x40000 + block*M*256 + row*256 + column*4`, wire는 유효 J tile만 반환한다. Raw completion 뒤에도 host commit 전에는 caller output을 공개하지 않는다.

## 실제 byte dependency와 1 Mbaud 하한

모든 아래 invocation byte 합계는 초기 OPEN/CAP(요청36/응답148 B) 제외다. Service는 final caller f_out commit까지; sustained는 RELEASE 및 frontend Run destruction/next-run-ready까지다(`adapter:274`). Quantization 포함 호출과 prequantized replay는 별도 timer다. Hash 전체 순회와 초기화는 반복 구간 밖에 둔다.

M321/N48의 A wire payload는 K와 무관하게 41,088 B(각 stripe20,480/20,480/128 B). W wire는 K64=4,096 B, K96=6,144 B. 유효 W codes보다 각각1,024/1,536 B가 많다. A padding은 각각20,544/10,272 B. Raw wire는 각각123,264/184,896 B이고 N48 column padding은 없다. K64 stripe raw61,440/61,440/384 B; K96은92,160/92,160/576 B다.

FULL은 `RUN(request all A/W) → full raw response → host commit → RELEASE`. PIPELINE은 `BEGIN(W) → PUBLISH(A stripe)/POLL(raw stripe) → FINISH → host commit → RELEASE`. 최소 protocol 거래는9개이며 **empty POLL p개를 추가**한다. 실제 frontend의 poll drain/retry 때문에 p=0을 실제 실행 횟수라고 주장하지 않는다.

| 경로, sustained | 요청 B | 응답 B | 거래 | 총 B | nominal wire 하한 |
|---|---:|---:|---:|---:|---:|
| K64 FULL | 45,256 | 123,560 | 2 | 168,816 | 1.68816 s |
| K96 FULL | 47,304 | 185,192 | 2 | 232,496 | 2.32496 s |
| K64 PIPELINE protocol p=0 하한 | 45,508 | 124,596 | 9 | 170,104 | 1.70104 s |
| K96 PIPELINE protocol p=0 하한 | 47,556 | 186,228 | 9 | 233,784 | 2.33784 s |
| K64 PIPELINE 실제 기록 p=5 | 45,688 | 125,336 | 14 | 171,024 | 1.71024 s |
| K96 PIPELINE p=5 조건부 예산 | 47,736 | 186,968 | 14 | 234,704 | 2.34704 s |

각 empty POLL은 요청36+응답148=184 B, 1.84 ms의 wire 하한을 더한다. Service 값은 각 행에서 RELEASE 184 B/1거래/1.84 ms를 뺀 byte 하한이다. 실제 시간에서 비용을 사후 차감하는 방식이 아니라 정해진 packet 집합의 이론적 전송량 계산이다.

실제 K64 기록은 `N/tests/stream03-live-overlap-m321k64/trace`다. CAP 포함15 request/15 response를 재파싱했고 CRC30개를 검증했다. CAP 제외 op 순서는 `BEGIN,PUBLISH,POLL(nonempty),POLL(empty),POLL(empty),PUBLISH,POLL(nonempty),POLL(empty),POLL(empty),PUBLISH,POLL(nonempty),POLL(empty),FINISH,RELEASE`다. K96의 p=5 행은 동일 empty-poll 수일 때의 예산이며 측정값이 아니다.

UART 1 Mbaud/8N1의 payload 한계는100,000 B/s다. 현재 shell은 RX/LOAD → CHECK → reply HEADER/OUTPUT/CRC → DRAIN → RX로 돌아가며, host도 다음 transaction을 현재 reply 뒤에 보낸다(`B02/dense_uart.sv:7,154,260,279`, `S08/uart.cpp:184`). 따라서 이 protocol의 request+response byte 시간은 **합산**한다. RX/TX 물리선이 별도라는 이유로 `max(TX,RX)`를 쓰지 않는다. Core는 RX/TX 동안 자율 실행할 수 있으므로 wire 시간에 이미 포함된 compute를 다시 더하지 않는다. Per-byte turnaround/BRAM read gap/CRC/control/host scheduling 비용은 이 하한 밖이며 실제 elapsed는 더 길다.

PIPELINE total device cycles는 미공개 stripe 대기와 output drain 영향을 포함한다. 예를 들어 위 software-RTL 기록의41,576,620 cycles는 순수 MAC resident time이 아니다. 40 ns를 곱하면 nominal device elapsed 환산이지 외부 clock 계측값도, host wall-clock도 아니다. RTL simulation host wall593 s를 보드 FULL/PIPELINE 성능으로 해석하지 않는다.

## 지원 경계

이 문서의 코드 검토·기록 wire 검사는 Implemented/Simulated 계약 증거다. Routing 결과는 B02 artifact에 한정하며 현재 검토가 새 route 또는 Board measured 결과를 만들지 않는다. 실제 보드 programming/CAP/jobs는 Awaiting approval이다. Production residual-enabled ExSIA, 모델 capture/TTFT/TPOT, layer chaining, weight-resident 최적화, wire 압축 또는 full-duplex packet 처리는 미지원이다. 추가 UART 최적화는 실제 padding/W 재전송/raw 의존성을 근거로 별도 분석하며 이번 protocol correctness 변경에 섞지 않는다.
