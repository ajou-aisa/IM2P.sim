# Dense PIPELINE geometry와 core stream 계약

검증된 FULL의 host `7a74ac29c9c8fa8fbc5d47cf073b8b9266e3e7a9`, Gemmini include `cd2caed8d93e49d3f8873bd6c4e340fcad54b9e0`을 재사용한다. 새로운 tiler 또는 quantizer는 없다. 다음 명령은 기존 frozen `host-build-03`의 compile flags/include 순서와 host 정적 archive를 사용한다. UART/JTAG 접근은 없다. 출력 경로는 새 경로여야 한다.

```sh
python3 fpga/dense_pipeline/geometry_probe.py \
  --full build/experiments/host-full-replay-20260909T103729Z \
  --out build/experiments/새실험/evidence/geometry
```

`compile-argv.json`, source 복사본, 실행 파일, scan/live 로그, summary, source/tool/library SHA256을 보존한다. Source manifest의 경로는 실제 frozen 입력을 가리킨다. 원본 FULL artifact를 덮어쓰지 않는다.

## 실제 자동 geometry

Compiled parameters는 DIM16, BANK_NUM4, BANK_ROWS4096, ACC_ROWS1024다. `gemmini.h:1858`의 기존 `gemmini_set_tile_ws`를 직접 호출한 결과다. K32/64/96에서 tile_K는 각각 2/4/6이며 아래 stripe geometry는 같다.

| I/J | tile_I/J | stripe_rows | stripe_count | tail rows |
|---|---|---|---|---|
| 32/48 | 2/3 | 32 | 1 | 32 |
| 48/48 | 3/3 | 48 | 1 | 48 |
| 96/48 | 6/3 | 96 | 1 | 96 |
| 321/48 | 10/3 | 160 | 3 | 1 |
| 513/19 또는 513/32 | 16/2 | 256 | 3 | 1 |
| 1025/1 또는 1025/16 | 32/1 | 512 | 3 | 1 |

M321/N48은 조사한 N≤48 후보 중 가장 작은 자동 3-stripe shape다. 행 범위는 `[0,160)`, `[160,320)`, `[320,321)`이다. Host outer counts `(3,1,1)` 및 ws_inner_calls 3과 별도로 RTL DIM16 output works는 63, K64/96 fragments는 252/378이다.

공개 args의 nonzero tile factors는 `ggml-gemmini-args.h:394–409`와 `ggml-gemmini-matmul.cpp:391–397`에서 존중된다. tile_I=1인 M33/N19는 실제 quantizer에서 16/16/1 stripe가 된다. 그러나 ggml dispatch는 `ggml-gemmini.cpp:1530–1536`에서 항상 auto tiler를 호출한다. 수동 factor 사례를 automatic geometry 또는 실제 모델 dispatch 증거로 부르지 않는다.

## 실제 live producer 검사

`geometry_probe.cpp`는 기존 `quants::quantize_activation`과 `ExSIA::run`을 실행한다. 자동 M321/N48/K64·K96과 수동-factor M33/N19/K64·K96, 총 4 invocation·12 events에서 다음을 검사한다.

- 실제 immediate callback의 slot 순서 0/1/0.
- 서로 다른 A bytes와 theta -6/-3/0.
- 첫 callback에서 미래 theta 2개 invalid, 두 번째 callback에서 1개 invalid.
- 전체 quantizer 반환 전에 callback 실행.
- 이후 stripe quantization이 끝나도 이전 A bytes 불변.
- stripe1의 sink false가 quantizer 실패와 A 전체 zero를 발생시킴. 거부 검사 4회.

Frozen profiling OFF에서는 event quantization timestamp fields가 0이다. 이를 유효 duration으로 사용하지 않는다. 이 검사는 CPU producer의 live publication 경계만 입증하며 FPGA 실행/overlap 증거는 아니다.

`exsia.cpp:1995–2033`은 post-fold metadata를 event에 복사한다. Sequential 경로 `:2924–2930`은 sink true 후 scratch slot을 release한다. Sink false는 retry가 아니라 `StripeReadySinkFailure`이며 `:1841–1865`에서 A와 metadata를 초기화한다. Adapter는 frontend의 수락 전 backpressure를 sink 내부에서 기다리고, 최종 수락 후 true로 반환해야 한다. Event slot 두 개는 host A backing 두 개를 뜻하지 않는다. Host A는 전체 I×K의 별도 allocation이다.

## M321/N48 용량 비교

기존 byte strides A128/W64/C256을 유지한 계산이다. 실제 BRAM mapping, mux, control sets, routing은 implementation으로 검증해야 한다.

| 저장소 | K64 | K96 |
|---|---:|---:|
| Host A codes | 20,544 B | 30,816 B |
| Host native Q8_H1 backing(44 B/block) | 4,224 B | 6,336 B |
| Host theta values | 6 B | 6 B |
| Host logical f_out | 61,632 B | 61,632 B |
| Host External reducer double payload (DIM×DIM) | 2,048 B | 2,048 B |
| Host accepted A copy (live PIPELINE) | 20,544 B | 30,816 B |
| Device A address span | 41,088 B | 41,088 B |
| Device full A power-of-two allocation | 65,536 B | 65,536 B |
| Device A two 160-row slots power-of-two allocation | 65,536 B | 65,536 B |
| W address span / 기존 allocation | 4,096 / 8,192 B | 6,144 / 8,192 B |
| Valid raw signed32 block bytes | 123,264 B | 184,896 B |
| C full address span | 164,352 B | 246,528 B |
| C full power-of-two allocation | 262,144 B | 262,144 B |
| C two stripes power-of-two allocation | 262,144 B | 262,144 B |
| Architectural accumulator allocation | 65,536 B | 65,536 B |
| DIM16 work accumulator live upper bound | 1,024 B | 1,024 B |
| Identity scale BRAM | 0 | 0 |

Full A와 two-slot A의 allocation이 같으므로 첫 후보는 full A와 publication prefix가 더 단순하다. C도 two-slot retention으로 allocation이 줄지 않는다. C 한 stripe만 보존하면 128KiB로 줄지만 raw drain/retirement와 다음 writeback 사이 gate가 추가로 필요하다. Host ExSIA 내부 residual int32 plane은 RMD OFF에서도 현재 할당된다. Device residual computation을 뜻하지 않는다.

## Frozen core의 실제 stream 입구

아래 core 파일은 `host-full-replay-20260909T103729Z/candidate-01/source` 기준이다. Root scheduler와 혼합하지 않는다.

| 계약 | 실제 동작과 위치 |
|---|---|
| Mode | `WorkTypes.bsv:30–33`의 `AsyncStripes`. `StripedMatrix`라는 RTL enum은 없다 |
| Logical start | `IM2PCore.bsv:1891–1916`이 하나의 MatmulDescriptor를 scheduler에 제출 |
| Device publication | `IM2PCore.bsv:1991–2018`의 `publishActivationStripe(rowBegin,rowCount,rowStride)` |
| ID/context | core가 nextStripeId 0/1/2를 생성하고 stripeContext도 해당 ID로 설정. host run/slot identity는 adapter가 별도로 보존 |
| A 주소 | matrixActivationBase + rowBegin×matrixActivationStride. publication의 rowStride는 stripe 내부 접근에 사용. Full A는 base0x10000, stride128 유지 가능 |
| Published bounds | `MatmulScheduler.bsv:332–355`: rowBegin==publishedRows, rows>0, end≤logical M, 주소 overflow 검사 |
| Queues | `MatmulScheduler.bsv:83–84`의 stripe FIFO2, completion FIFO2. active stripe와 lookahead register는 별도 |
| Publication ready | stripe FIFO notFull. 이를 host semantic capacity 또는 device A backing 수와 같다고 해석하지 않음 |
| Lookahead | `MatmulScheduler.bsv:174–181`, `:390–453`. 공개된 다음 stripe만 저장. 조건에 따라 현재 stripe 마지막 work 전에 다음 A/W/S를 읽을 수 있음 |
| Raw output address | `IM2PCore.bsv:1612–1641`: outputBase + block×(logical M×outputRowStride) + row×outputRowStride. C stride256이면 block plane은 logical M×256 |
| Raw completion | `IM2PCore.bsv:2275–2282`: 마지막 K block의 마지막 output row ack 뒤 completeWork. `MatmulScheduler.bsv:238–262`가 마지막 I/J work 뒤 stripe completion enqueue |
| Completion backpressure | `MatmulScheduler.bsv:183–187`: completion FIFO가 full이면 work advancement가 정지. Provider/wrapper는 completion을 지속 회수해야 함 |
| Final matrix done | `IM2PCore.bsv:1652–1663`: pending C request/read 없음과 accumulator idle 확인. Caller output commit 또는 host reconstruction 완료를 뜻하지 않음 |
| Queue drain | Scheduler acknowledge/start가 completion FIFO를 clear하지 않음. 다음 invocation 전에 completion 전부 consume 필요 |
| Timebase | `IM2PCore.bsv:603–605`: matrixCycle=cycleReg−matrixStartCycleReg. publish/completion cycles는 같은 device work-relative domain |
| Wait counter | `IM2PCore.bsv:531–555`: 총 cycles는 미공개 대기도 포함. stripeHostWait는 제한된 상태 조건이므로 모든 host wait를 완전히 측정한다고 가정하지 않음 |
| 내부 A guard | `IM2PCore.bsv:2048`의 `!activationResponsePendingReg` 유지. Host publication과 response-slot publication은 별개 |

기존 simulator도 core context를 그대로 host에 노출하지 않는다. `sim/src/simulator/striped/provider.rs:272–307`은 stripe ID/row bounds를 확인한 후 `published.stripe.stripe_context`로 completion context를 복원한다. FPGA adapter도 같은 명시적 대응을 사용할 수 있다. `progress_stream(1)`은 `sim/src/c_api/stream.rs:434–458`의 실제 simulation cycle 진행이며 물리 polling으로 재정의하지 않는다.

## Persistent IFR1 transport의 검사 범위

`uart.hpp/cpp`는 기존 IFR1 FULL의 Open/CAP을 constructor에서 한 번 수행한다. Descriptor staging, RUN, 실제 response 검증과 기존 provider callbacks 이후 full()이 반환한다. Caller가 frontend fence/output commit을 끝낸 뒤 release()를 호출한다. 실패 뒤 자동 ABORT/reset/retry는 없다. 시간 통계의 최종 caller commit endpoint는 host integration harness가 기록한다.

```sh
python3 fpga/dense_pipeline/test_uart.py --out build/experiments/새실험/evidence/uart-pty
```

이 PTY 검사는 partial I/O, callback order, signed32 sign-extension, sequential generation, CRC/profile/id/count/length/capacity/timeout/sticky failure, wire padding, RELEASE 대신 중복 RUN response의 거부를 검사한다. Mock payload를 사용하므로 numerical FPGA PASS 증거가 아니다.

## 새 bounded provider의 입구

`synth/DensePipeline.bsv`의 `mkDensePipeline`은 기존 frozen core를 하나 사용한다. M≤336/N≤48/K32·64·96을 대상으로 FULL과 AsyncStripes를 선택한다. A는 `mkBRAMCore2(4096, False)`의 128-bit words, C는 같은 depth의 512-bit words, W는 기존 512×128-bit single-port BRAM이다. 두 BRAM2 포트는 같은 core clock을 사용한다.

- `loadActivation`은 A port A에 unpublished rows를 쓴다. Core activation provider는 A port B를 읽는다. 전송 완료와 CRC 확인 뒤 `publish(rowBegin,rowCount)`를 호출하는 것은 shell의 책임이다.
- `loadWeight`는 invocation 실행 전만 허용한다. W는 invocation 동안 불변이다.
- `start`는 하나의 logical descriptor를 제출한다. Core 내부 scheduler가 I/J work와 K16 fragment, K32 raw block writeback을 유지한다.
- Core는 C port A에 쓰고 transport는 C port B를 읽는다. `requestOutput`의 address가 raw-complete rows에 속하는지는 shell이 검증해야 한다. Provider는 completed-stripe output만 읽는다는 계약을 바꾸지 않는다.
- `stripeCompletionValid`는 completion head를 한 번 관측한 다음 공개된다. `completedRows`는 그 head의 row end를 기록한다. Host ACK가 오래 지연되면 뒤 completion head의 실제 완료보다 늦을 수 있다. FIFO depth를 근거 없이 확대하지 않았다.
- `firstActivationCycle`은 최초 activation request를 provider가 수락한 device work-relative cycle이다. `firstActivationPublishedRows`는 같은 순간의 publication prefix다. Host send 시각을 대신 사용하는 counter가 아니다.
- `hostWaitCycles`와 `overlapCycles`는 기존 core counter를 노출한다. 기존 counter의 비가산 의미를 유지한다.
- 최종 `acknowledge`는 모든 stripe completion 및 output response를 consume한 뒤만 허용한다. 자동 reset은 없다.

Production provider의 runtime shape/order 검증은 shell의 pre-start/pre-publication gate와 함께 사용한다. BSV dynamic assertions는 별도 assertion-enabled 빌드에서 검사한다. BSC compile 성공만으로 numerical/runtime PASS 또는 route/board PASS를 주장하지 않는다.
