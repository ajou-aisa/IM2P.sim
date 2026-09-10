# Activation response publication assertion 조사 — 2026-09-09

공유 `putActivationReadResponse` method에 **pending-capacity guard 한 곳**을 추가했다. Assertion-enabled RTL은 이전 응답 publication이 막힌 상태에서 다음 응답을 수락할 준비가 됐다고 표시했다. Assertion은 그 다음 실제 write edge 전에 시뮬레이션을 종료했다. 기존 production RTL의 관측 실행에서는 같은 계약 위반이나 출력 오류가 없었다. 수정은 publication이 빨리 진행된다는 암묵적 가정을 명시적인 readiness 계약으로 바꾼다.

보류된 P3A 후보와 기존 P1/P2 artifact는 보존했다. 이번 작업은 소프트웨어 시뮬레이션과 Vivado implementation까지만 수행했다. FPGA/JTAG/UART 접근, programming, flash write, commit/push는 없다. P3B·transport 전환·추가 면적 최적화·100 MHz 작업도 없다.

## 1. Baseline과 artifact 식별

이 문서에서 `E`는 다음 새 실험 root다. 아래 상대 링크는 해당 고정 artifact를 가리킨다.

```text
build/experiments/activation-publication-20260909-154059/
```

기존 P3A root는 `build/experiments/fpga-p3a-20260909-142748/`이다. Git branch는 `exp/pe-local-partial-int20`, HEAD는 `dc4a1a621f63834d64df42ae8c24152747d97971`이다. 시작 시 tracked 17파일 수정과 untracked 문서 5개가 있었으므로 **HEAD만으로 source를 식별하지 않는다**.

- [시작 Git 상태](../build/experiments/activation-publication-20260909-154059/baseline/git-state.json), [원래 tracked diff](../build/experiments/activation-publication-20260909-154059/baseline/tracked.patch).
- `E/baseline/source`: 실제 P2/P3A core 입력 192파일. Generated configuration·BSV·provider·FFI·테스트를 포함한다.
- `E/baseline/p3a`: 기존 candidate-04의 shell/RTL/protocol/host 입력 복사본.
- [원본 파일별 hash](../build/experiments/activation-publication-20260909-154059/baseline/original-hashes.json), [복사본 hash](../build/experiments/activation-publication-20260909-154059/baseline/copy-hashes.json). 기존 candidate-04 manifest 자체의 SHA256은 `6bac05db62746862b703d264667639be4cb800c3d241ba09662a4e70c1b6698a`이다.

작업 root는 시작부터 board snapshot 전체와 같지 않았다. Board snapshot에는 A1 multiply 표현, 별도 MatmulScheduler/WorkScheduler 구현과 `synth/ResidentP0.bsv`가 있다. Root의 앞 세 파일은 다르고 ResidentP0는 root에 없다. 이 차이는 원래 manifest로 확인했으며 이번에 overlay하거나 checkout하지 않았다. **모든 후보 검증·route는 frozen board source에서 수행**했다. Root에는 공유 core guard만 반영했으므로 root에서 임의로 build한 결과를 이 후보라고 식별하면 안 된다.

| Artifact | 전체 SHA256 |
|---|---|
| 보존 P1 bitstream | `560d5496a6b8de9f0eb5a694583085164d3c47e9bf90157fc0822ac3b5198b3c` |
| 보존 P2 bitstream | `a22b27e8944553d61a387e3e975caa41cdcf6d979eeefbd96ac7ce350ea6ed15` |
| 보류 P3A bitstream | `cf071c3905d05258fe1509f6a31f82e8764e55e7dd23f2533e3dea044b0b6599` |
| P2/P3A 동일 production `mkResidentP0.v` | `c11b8dce1cb33a6aa850013cf059b52facb3398d2c13ae8e13219d42290a321a` |
| 기존 실패 assertion RTL | `ca038a841c28bf7719c530e52c32b5fea2aeb4f2e11ddc81e657d9ce9e1672f8` |

“P3A 없는 대조군”은 UART shell과 board clock top을 제외한 `mkResidentP0`다. **ResidentP0의 BRAM/provider와 core는 유지**한다. P2와 기존 P3A의 이 generated core는 byte 단위로 같다. A/B/C는 동일 frozen BSV source·provider·C++ stimulus·reference를 사용한다. 일반 Rust simulator bridge를 ResidentP0 provider와 같은 것으로 취급하지 않는다.

실제 도구: BSC 2026.01 build `9bd39e6f`, Verilator `5.051 devel rev v5.050-122-g56accae45 (mod)`, GCC 11.4.0, Vivado 2025.2 build 6299465. 전체 과거 환경변수는 기록돼 있지 않다. 실제 argv, 확인 가능한 environment, compiler 실행 파일 hash를 보존했으며 완전한 과거 환경 재현이라고 주장하지 않는다.

## 2. 정확한 최초 실패

기존 standalone executable을 동일 argv로 새 실행 디렉터리에서 3회 반복했다. 모두 exit 1, 로그 byte-identical, 완료 jobs 0 / 검증 outputs 0이었다. 첫 test는 `int20_widen_int32_multiply`이다. 이어질 `fragment_shift_boundary`, `random_full_range_shift`, `matched_host_benchmark_shift`는 실행 완료로 세지 않는다.

```text
Dynamic assertion failed: "src/core/IM2PCore.bsv", line 2065, column 13
activation response publication is already pending
```

조건은 demand response branch의 `!activationResponsePendingReg`다. **외부 stripe publication이 아닌, 내부 response→activation-slot publication**이다. 원본 C++의 `unexpected $finish after rising edge` 문구는 마지막 falling-edge eval에도 쓰인 부정확한 label이다. 실제 generated task는 `negedge CLK`의 `#0`에서 실행된다.

| 항목 | 최초 원본 workload |
|---|---|
| Workload | FullMatrix, A8/W8, physical D16, M=N16, K32 |
| 종료 위치 | Tick 103 post-fall; core counter 101, job counter 66 |
| Identity | Job 1, stripe 0, stripe context 1; P3A generation은 해당 없음 |
| 이미 수락된 pending | Slot 1, row 0, tag `0x100000010` |
| 다음 제공 response | Slot 1, row 1, tag `0x100000011` |
| 다음 action 결정 | Return 1, publish 0, feed 1 |
| 실제 완료된 rising-edge events | Returns 17, publications 16, feeds 5 |
| 실패 종류 | Assertion에 의한 조기 종료. Numerical mismatch/timeout/이미 commit된 overwrite의 증거는 아님 |

[원본 반복 결과·정확한 flags·clock 분석](../build/experiments/activation-publication-20260909-154059/evidence/original-repeat/README.md)에 executable hash, full compiler/link commands, 입력 및 로그가 연결돼 있다. BSC full argv는 `original-bsc-command.json`, Verilator full argv는 `original-build-command.json`, 실제 g++ 전체 flags는 `original-cxx-commands.txt`다.

## 3. A/B/C와 실제 최초 divergence

Production BSC flags는 `-u -verilog`, 기존 package path, `-steps 4000000 -steps-warn-interval 1000000 -steps-max-intervals 20 +RTS -K256M -RTS`다. B에만 `-check-assert -keep-fires -show-schedule`을 추가했다. 모든 bdir/info/vdir와 generated model/cache를 분리했다. 정확한 absolute argv/cwd는 [A](../build/experiments/activation-publication-20260909-154059/build-A/command.json), [B](../build/experiments/activation-publication-20260909-154059/build-B/command.json)에 있다.

| Build | 실제 사용 RTL와 관측 |
|---|---|
| A | 보존 production RTL `c11b8dce…321a`, assertion Action 없음 |
| B | 동일 source의 assertion RTL `1adc581d1a8729a265537026793fd78b73aff70071a85cb335e492723b8f99f7` |
| C | A의 `c11b8dce…321a`를 그대로 사용. C++에서 기존 신호를 수동 관측 |

Fresh A hash는 `e5f63fcbfc62de9990055f94bd0c63c9c12fc568bad4e408c8787094dacb9593`다. Fresh A/기존 production, fresh B/기존 assertion의 차이는 **생성 날짜 주석 4행뿐**이다. Raw hash는 다르며 byte-identical로 표현하지 않는다. 비교 diff를 `build-A/original-rtl.diff`, `build-B/original-rtl.diff`에 남겼다. C simulation은 이 fresh A 대신 원래 A 파일 자체를 사용했다.

Verilator trace build는 `--timing --assert --public-flat-rw --trace --trace-depth 1`, 기존 output-split, C++20 설정을 기록했다. `--assert`가 BSC의 생략된 dynamicAssert를 복원하지는 않는다. `-Wno-fatal`은 기존 Verilator compile warning 정책이며 BSC G0117이나 runtime assertion을 숨기지 않았다.

| 실제 edge/관측 | A | B | Fixed B |
|---|---|---|---|
| Tick 101 rise | Slot1,row0 수락 | 동일 | 동일 |
| Tick 101 post-rise | 다음 publish 선택 | Feed 때문에 publish 차단 | 동일 차단 |
| Tick 102 rise | Row0 valid=1, pending=0 | Row0 valid=0, pending=1 | 동일 pending 유지 |
| Tick 103 post-rise | 빈 stage에 row1 return 선택 | Pending row0인데 row1 return 선택 | Capacity guard가 return 차단 |
| Tick 103 fall | 진행 | Assertion 종료 | 진행 |
| Tick 115 rise | 이미 진행 | 도달 안 함 | Row0 publish |
| Tick 116 rise | 이미 진행 | 도달 안 함 | 유지된 row1 response 수락 |

최초 **결정** 차이는 tick101 post-rise의 publish, 최초 **commit된 상태** 차이는 tick102 post-rise의 pending/row-valid다. Post-rise `WILL_FIRE=1`을 그 rising edge에서 이미 수락된 이벤트로 세지 않는다.

원본 B schedule의 핵심은 `publish = pending && !feed && !return`이다. Return assertion이 pending/row-valid를 읽고 publication이 이를 쓰는 관계, publication assertion이 row-valid를 읽고 feed가 이를 지우는 관계가 각각 역방향 data dependency와 만나 conflict를 만든다. 동적 row/slot 선택 때문에 서로 다른 row에서도 publish가 feed에 막힌다. 그동안 request issuer는 다음 정상 tag를 발행하고 BRAM provider는 응답을 준비한다. 원본 method의 guard에는 pending capacity가 없어서 **그 응답을 받을 수 있다고 표시한다**. 이것이 assertion의 구체적인 위반 경로다.

[소스·모든 caller·BSC schedule 근거](../build/experiments/activation-publication-20260909-154059/evidence/source-analysis/README.md), [edge별 divergence CSV/JSON](../build/experiments/activation-publication-20260909-154059/evidence/monitor-integration-negative/README.md)을 함께 보존했다. Compiler/library bug, assertion 오기, testbench 강제 EN, 기존 production 출력 손상으로 단정하지 않는다.

## 4. 최소 재현과 clock control

**M9/N1/K1**, 단일 K fragment에서 실패한다. 유효 `M*N*K <= 9` shape 44개를 전부 검사했으며 B의 유일한 실패가 9×1×1이다. 따라서 확인한 최소 MAC-volume은 9다. 전체 8,192 shape의 전수검증은 아니다.

TEST-ONLY ResidentP0 wrapper에 shape 인자와 tail K를 추가했다. Core는 원본 그대로이며 provider latency·전체 preload·stride·reference semantics를 유지했다. Current/next fragment overlap, cross-stripe lookahead, 외부 backpressure/지연 주입은 필요 없다. Feed와 미완료 publication의 자연스러운 overlap만으로 재현된다.

최소 B는 tick68에서 slot0,row7/tag `0x100000007`을 실제 수락한다. Tick70 post-rise에서 row8/tag `0x100000008`의 return이 선택되지만 row7은 pending이다. Tick70 falling edge, core cycle68/job cycle33에서 assertion 종료한다. Job1/stripe0/context1, generation 해당 없음. 실제 returns8/publications7/feeds2이며 row8 overwrite는 아직 일어나지 않았다. Fixed B는 provider 응답을 유지하고 tick76 row7 publish, tick77 row8 수락, tick78 row8 publish 순서로 진행한다.

```sh
ACT_EXP=/home/youngshin/aisa/RISCV-DynDNN/IM2P/IM2P.sim/build/experiments/activation-publication-20260909-154059
ACT_RUN=$(mktemp -d /tmp/im2p-publication-repro.XXXXXX)
cd "$ACT_RUN"
"$ACT_EXP/shape-nominal-B/resident_shape" --shape 9 1 1 --wave --trace
```

기대 결과는 assertion FAIL/exit1이다. 새 cwd에서 control은 `shape-nominal-A/resident_shape --shape 9 1 1 --wave --trace --monitor`, 수정본은 `shape-nominal-fixed-B/resident_shape`에 같은 인자를 사용한다. [전체 build/run 명령과 최소성 증거](../build/experiments/activation-publication-20260909-154059/evidence/shape-campaign-review/README.md)를 참조한다.

원래 standalone은 simulation time을 전진시키지 않았다. Generated runtime의 `resumeZeroDelay()`가 BSC `#0`를 처리하지만 일반 timed driver로는 부적절하다. 새 초기 trace driver의 half-period 20 ps도 25 MHz가 아니다. 별도 clock-only control에서 **20 ns half-period/40 ns period**를 VCD로 직접 확인했다. 최소 failure와 원래 16×16×32 A/B/C/fixed 결과는 timestamp 제외 trace가 완전히 같다. 전체 original C 692-shape도 이 nominal25MHz driver로 통과했다. Clock 변화는 해당 failure의 원인이 아니다.

## 5. Passive invariant와 최소 수정

Monitor는 pre-edge snapshot, 실제 rising-edge fire, post-edge snapshot을 사용한다. BSV rule/method를 추가하지 않는다. Accepted tag/job/slot/row/K, pending data 보존, publication count/order, valid row와 실제 engine input 값, consume 전 reallocation, 미완료 operation을 남긴 completion을 검사한다. Reset은 명시적 abort boundary다. Lookahead storage 자체의 전체 정합성을 demand monitor의 coverage로 주장하지 않는다.

일반 elastic conservation과 기존 assertion의 strict 계약을 구분했다.

```text
일반 안전 조건: accept -> (!old_pending || publish_old)
기존 strict assertion: accept -> !old_pending
```

Monitor는 두 counter를 따로 기록하고 **최종 strict gate는 pending accept가 1회라도 있으면 FAIL**한다. 안전한 동시 turnover라도 strict 계약 PASS로 처리하지 않는다. 관측한 C/fixed 실행의 strict violation과 turnover는 모두 0이다.

- Monitor unit: positive4 + negative20 = 24 checks PASS.
- 실제 C archive 연결 negative: observer의 수락 tag만 1bit 변경하자 tick47 `response tag mismatch`, exit1. DUT 입력/RTL은 그대로이며 정상 trace와 정확한 prefix다.
- A/C 정상 trace는 byte-identical하다. Observer가 production scheduling을 바꾸지 않았다.
- [Monitor 구현/계약](../build/experiments/activation-publication-20260909-154059/work/passive-monitor/README.md), [통합 negative](../build/experiments/activation-publication-20260909-154059/evidence/monitor-integration-negative/README.md), [strict 결과 판정](../build/experiments/activation-publication-20260909-154059/evidence/passive-strict-gate/README.md).

이번 production source diff는 [IM2PCore.bsv](../src/core/IM2PCore.bsv)의 공유 method 한 곳이다.

```diff
-    ) if (activationRequestValidReg || lookaheadActivationRequestValidReg);
+    ) if ((activationRequestValidReg || lookaheadActivationRequestValidReg)
+            && !activationResponsePendingReg);
```

ResidentP0와 일반 production FFI caller는 같은 RDY를 사용하므로 provider별 delay나 guard를 추가하지 않았다. RDY를 우회할 수 있는 raw test hook은 이번 실패의 caller가 아니다. Lookahead branch도 pending demand publication 동안 보수적으로 기다린다. Assertion·rule priority·numerical datapath·valid/tag·clock·expected cycle은 바꾸지 않았다. Assertion failure condition이 guard에서 거짓임을 BSC가 증명해 generated display sites는108→107이지만 원본 assertion은 그대로다. Display site 수는 assertion coverage가 아니다. G0117은 원본 production40→fixed39, assertion36→36이며 일괄 suppression은 없다.

분류는 **source readiness 계약의 누락을 assertion scheduling이 드러낸 경우**다. 기존 production의 테스트 범위에서는 publication이 제때 진행되어 위반하지 않았다. 모든 production 입력/스케줄의 형식 검증으로 일반화하지 않는다.

## 6. 회귀와 판정의 한계

| 검사 | 결과 | 수치·cycle |
|---|---|---|
| 원본 standalone 반복 | 예상 failure 재현 3/3 | 첫 job 완료 전 assertion, exit1 |
| 원본 A/C 16×16×32 | PASS | 각4 jobs/1024 outputs,361 cycles/job |
| 원본 B 16×16×32 | FAIL | 첫 job 완료 전 assertion |
| Fixed production/assertion, 같은 workload | PASS | 각4 jobs/1024 outputs,361 cycles/job |
| 원본 A / nominal C,692 shapes | 각692/692 PASS | 각2768 jobs/58448 outputs |
| 원본 B,692 shapes | 407 PASS/285 FAIL | 모든 failure 동일 assertion |
| Fixed A/B + passive monitor,692 shapes | 각각692/692 PASS | 각2768 jobs/58448 outputs |
| 기존 A8/W8/D16 Rust 회귀 | 154/154 PASS | 기존 numerical/cache/protocol/cycle expected 유지 |
| `mkTbProfileConfig`, BSC assertions | PASS | Profile contract; activation coverage로 합산 안 함 |
| 기존 관련 BSV assertion 회귀 | 4/4 PASS | ActivationBuffer / Lookahead / Matrix / MatrixScale |
| P2 FULL_RESULTS/legacy UART RTL, production/assertion | 각각 PASS | 각135 jobs/34560 outputs, B1/2/4/8/16/32/64, core361/batch412 cycles/job |
| P3A COMPLETION_ONLY/FULL/retained READ/RELEASE, production/assertion | 각각 PASS | 각326 jobs,115968 exact 비교, core361/batch412 cycles/job |
| Saved RTL legacy packet decoder / P3A host PTY | 각각 PASS | Production/assertion 각각 packet exact·corruption 검증 + 기존7 tests |
| Actual board-top vendor RTL | PASS | P2 B1 + P3A B1,512 outputs,core361/batch412 |

Production 수정 전후 2768 case의 cycle delta는 모두0이다. Fixed assertion B는 fixed production A보다 **76 shape/304 case에서 +3..18 cycles**다. 최소9×1×1은 production162/assertion168이다. 원래 B가 통과했던1628 case는 fixed B와 같은 cycle이다. Assertion compile을 production cycle과 항상 같다고 주장하지 않는다.

세 full-grid monitor cohort(original C/fixed A/fixed B)는 각각32952 returns/publications/feeds,2768 completed jobs,strict violation0,unobserved feed data0이다. Strict gate는 standalone6회와 full-grid2076회를 각각 expected count·실제 exit·PASS marker·assertion/finish 문구까지 확인한다. Synthetic strict violation/누락 job/거짓 PASS도 거부한다.

P3A의115968은 FULL과 deferred READ의 반복 비교를 포함하므로 고유 output 수라고 부르지 않는다. B64는 launch64/row commit1024/ack64,core23104/batch26368이다. Completion-only 초기 payload0, deferred READ65536 bytes를 확인했다. 지연 READ, 자연 UART TX stall, busy START/READ 거부, reset, identity/generation 경계, writeback/retained-idle 시점의 late UART error를 검사했다. Generation exhaustion은 simulation에서 counter를 UINT32_MAX로 설정한 경계 검사이며, 실제 2³² batch를 실행한 것이 아니다. 별도 late-error 소규모 재현은2 jobs/512 outputs이며 전체 회귀에도 포함되므로 중복 합산하지 않는다. 외부에서 강제한 result-BRAM backpressure는 ready port가 없어 NOT RUN이다.

두 board simulation의 build/late-errors/numerical 단계는 모두 통과했다. 후속 host-P2 socket unit의 `sendall`이 sandbox `EPERM`을 만나고 host transfer watchdog이5초 뒤 종료했다. 이는 실제 FPGA timeout이나 RTL 수치 실패가 아니다. 권한 확대 재시도는 실행 결과 없이 중단됐으므로 **동일 socket test의 재검사는 NOT RUN**이다. 최초 통합 runner의 exit1과 로그를 보존했다.

추가 권한 확대 없이 동일 saved RTL capture를 기존 decoder로 exact 비교하고 corrupt packet 거부를 재검증했다. 기존 `test_host_p3a.py -v`는 변경 없이 production/assertion 각각7 tests PASS다. PTY에서 공통 `host.transfer`의 partial read/write를 검사하므로 해당 coverage는 확보했으나 원래 socket test 전체가 통과했다고 표현하지 않는다. [Host 검증·정확한 실행 및 입력 hash](../build/experiments/activation-publication-20260909-154059/evidence/fixed-uart-regression-review/README.md)에 별도 결과를 남겼다. 보드 수치 회귀를 다시 실행하거나 source를 바꿔 환경 실패를 숨기지 않았다.

154 tests는 production BSC flags다. Verilator `--assert`만으로 BSC assertion-enabled라고 부르지 않는다. 별도 [관련 BSV assertion 회귀4개](../build/experiments/activation-publication-20260909-154059/evidence/fixed-related-bsv/README.md)는 실제 `-check-assert`로 지연 activation·다음 stripe lookahead·full/async matrix·scale 계약을 검사했다. 기존 physical DIM2/A8/W8/Int16 partial semantic bench이며 DIM16/INT20 검증과 구분한다. 각각 최종 PASS marker1개, assertion failure0,timeout0; G0117은36/35/56/53건 그대로 남는다. [관련 경로별 coverage](../build/experiments/activation-publication-20260909-154059/evidence/path-coverage/README.md)는 외부 stripe publication 지연, output ACK 지연, scale cache 검사와 내부 activation pending 검사를 구분한다. FULL/STRIPE_PIPELINE는 scheduler 경로이고 FULL_RESULTS/COMPLETION_ONLY는 reply mode다.

검사기는 exit0만 사용하지 않는다. BSC `$finish` 자체가 실패 exit를 보장하지 않으므로 매 eval의 `gotFinish()`, assertion/fatal 로그, 전체 test/job/output 완료 수를 함께 검사한다. 원본 반복의 exit1은 C++ harness가 예상치 못한 finish를 거부한 결과다. Board-top 최초 sandbox 실행은 exit0이었으나 필수 PASS marker가 없어 정확히 FAIL 처리했고, 동일 compiled snapshot의 정상 runtime 재실행 결과를 따로 기록했다.

초기 FST 링크의 `lz4.h` 부재, VCD destructor 수명 오류, monitor의 idle 상태 field 해석 오류, 로그 parser의 `-Wno-fatal` 오인식도 원로그를 보존했다. Native VCD/local owner/유효 상태 관측/정확한 runtime 패턴으로 각각 수정했다. 실패 시도를 PASS로 덮어쓰지 않았다.

## 7. Production RTL 영향과 새 route

Production generated RTL은 바뀌었으므로 기존 보류 bitstream은 수정본을 포함하지 않는다. 새 input/source/model/route 디렉터리로 재빌드했다.

| 새 artifact | 전체 SHA256 |
|---|---|
| Fixed production RTL | `b7a127283b2c5476a46d3d162627d3c7ae5b753df6777bc738e08ff9b1f1b91f` |
| Fixed assertion simulation RTL | `e16005480f4a1f35b7041c33623f00a64f00e7605b8ce896f3e7acfe1f93e1e9` |
| Fixed routed bitstream | `c0f21374d454090a64e4be072a686ddf999d00ce47bb566e0fdc3ae6d1065d83` |
| Fixed routed DCP | `0a08e37497eba7e125482e74650cee0358d0eff5ff481ba3caa7a5c99630811b` |

실제 implementation input의 변경 파일은 `rtl/mkResidentP0.v` 하나다. UART shell·board top·primitive·XDC·Vivado flow는 원본과 byte-equal이다. Assertion RTL은 simulation 전용이며 area/bitstream에 사용하지 않았다. [Active source](../build/experiments/activation-publication-20260909-154059/candidate-fixed-01/active-source.json), [candidate manifest](../build/experiments/activation-publication-20260909-154059/candidate-fixed-01/source-sha256.json), [독립 route 검토](../build/experiments/activation-publication-20260909-154059/evidence/fixed-route-review/README.md).

| Board-level resource/check | 보류 P3A | Fixed P3A | Delta |
|---|---:|---:|---:|
| LUT | 43803 | 45680 | +1877 |
| FF | 29289 | 29131 | -158 |
| BRAM36-equivalent tiles | 42 | 42 | 0 |
| RAMB36 / RAMB18 | 41 / 2 | 41 / 2 | 0 |
| DSP | 27 | 22 | -5 |
| Occupied slices /15850 | 15137 (95.50%) | 15343 (96.80%) | +206 |
| LUT logic / SRL | 43675 /128 | 45592 /88 | +1917 /-40 |
| Control sets | 512 | 554 | +42 |
| Setup WNS | +6.142 ns | +10.444 ns | +4.302 ns |
| Hold WHS | +0.009 ns | +0.019 ns | +0.010 ns |
| Pulse-width slack | +3.000 ns | +3.000 ns | 0 |

Part는 두 DCP XML 모두 `xc7a100tcsg324-1`, clock40ns/25MHz다. JTAG die 확인을 새로 수행한 결과가 아니다. Fixed remaining은 LUT17720,FF97669,BRAMtiles93,DSP218,완전히 빈slice507이다. LUT72.05%와 slice96.80%는 다른 지표다. LUT 증가에는 unforced DSP mapping27→22와 packing 변화가 포함되므로 guard의 literal gate count로 해석하지 않는다. 추가 area campaign은 하지 않았다.

Setup/hold/pulse failing endpoints0, route73796/73796, route errors0, black boxes0, unconstrained internal endpoints0이다. CDC report는 `All paths are Safely Timed.`이며 새로운 false path/multicycle는 없다. DRC error/critical0이지만 DSP pipeline advisory55건은 남는다. Threshold3 이상의 congestion window는 없으며 모든 수준의 congestion0이라는 뜻은 아니다. 최대 non-clock fanout10351도 남는다.

외부 `uart_rx` input delay와 `uart_tx`,LED4개 output delay는 기존과 같이 미제약이다. RX2FF/reset-release4FF 구조와 실제 동배치, vendor MMCM/reset simulation을 별도로 검토했다. 이것을 external I/O synchronous closure나 MTBF 측정으로 표현하지 않는다.

## 8. 승인 상태와 남은 범위

**Programming 보류를 유지한다.** 기존 `cf071c39…b6599`는 보존된 원래 후보이며 guard 수정본으로 사용할 수 없다. 새 `c0f21374…065d83`도 board-proven이 아니다. 사용자에게 새 전체 hash와 검증 결과를 제시한 뒤 명시적인 별도 승인 없이는 프로그래밍하거나 원래 bitstream으로 복원하지 않는다.

새 board numerical/cycle/wall-clock, P3A diagnostic/full-output 시간, 실제 crossover는 **NOT RUN**이다. 기존 P2 실측을 새 측정으로 재분류하지 않았다. Post-route SDF simulation, 모든 입력의 형식 검증, arbitrary delayed activation provider와 모든 lookahead storage invariant는 이번 근거에 포함되지 않는다.

기존 source snapshot·generated RTL·bits·reports와 root의 다른 사용자 변경은 유지한다. 새 RTL fix와 본 보고서 외의 조사/monitor/재현 harness는 `E`에 별도 보존한다. 자동 commit/push는 없다. 재프로그래밍 판단은 이번 수정의 별도 board 승인 단계에서만 재개한다.

최종 보존 검사에서 기존 P3A 원본449파일, 현재 baseline 복사223파일, 이전 baseline 복사284파일은 불변이었다. P1/P2 측정 manifest592개 중 유일한 차이는 artifact가 아닌 editable root의 이번 `IM2PCore.bsv` 수정이다. 기존 bitstream·route/report/programming 파일은 일치한다. Fixed production/assertion candidate의 manifest 대상24파일씩과 frozen source192파일씩도 일치한다. 최초 보존 checker가 editable root와 board snapshot이 같다고 잘못 가정한 FAIL 결과를 보존했고, 기존 manifest와 대조해 원래 차이와 이번 guard 수정만을 별도로 분류했다.

Git 확인 결과는 [최종 보존 검사와 실제 명령 출력](../build/experiments/activation-publication-20260909-154059/evidence/final-review/preservation-final.json)에 있다. [Structured summary](../build/experiments/activation-publication-20260909-154059/evidence/final-review/summary.json)는 source manifest의 전체 SHA256, 실패 identity, 회귀·cycle·route·잔여 제한을 연결한다.

```text
git branch --show-current
exp/pe-local-partial-int20

git rev-parse HEAD
dc4a1a621f63834d64df42ae8c24152747d97971

git status --short
기존 tracked 수정17개 유지, 기존 untracked 문서5개 유지
이번 추가: ?? docs/ACTIVATION_PUBLICATION_ASSERTION.md
기존 수정 파일 src/core/IM2PCore.bsv에 guard diff 한 곳 추가

git diff --check
exit 0, 출력 없음
```

위 status 설명은 압축 요약이며 실제 파일별 `git status --short` 출력은 연결된 JSON에 그대로 있다. 새 문서의 trailing whitespace와 local link도 별도로 검사했다. Root 전체 source가 board snapshot과 같다는 주장이나 editable root 전체에 대한 새 build 결과는 포함하지 않는다.
