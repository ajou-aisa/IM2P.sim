# IM2P FPGA 확장성·실행 성능 연구

측정 기준일: 2026-09-09. 대상은 Arty A7-100T / `xc7a100tcsg324-1`, Vivado 2025.2, A8/W8 physical DIM16, architectural INT32 accumulator 64 KiB다. Source inspection, RTL regression, synthesis, route와 승인된 P1 실제 보드19-job 측정을 완료했다. 미검증 profile·최대 Fmax·전체 모델 실행은 성공으로 간주하지 않는다.

실험 입력은 `dc4a1a621f63834d64df42ae8c24152747d97971` **그리고 보존한 INT20 working-tree diff**다. HEAD만으로 재현할 수 없다. [원본 diff](../build/experiments/fpga-research-20260909-094730/source-diff.patch), [파일별 SHA256](../build/experiments/fpga-research-20260909-094730/baseline.sha256), [고정 source](../build/experiments/fpga-research-20260909-094730/baseline)의 manifest digest는 `77e51d7f210d793e767627eb189ff756c829ee8ba094b9bea4eac69eff08bbae`다. 이후 IM2P 행 번호는 별도 표시가 없으면 이 snapshot 기준이다.

## 1. Executive result

**A8 산술 비용과 scheduler/control timing 병목을 확인했다. 실제 P1 보드는19 jobs·4,864 outputs가 PASS했다. 동일 workload에서 resident compute는 Verilator보다105.84× 빠르지만, bulk 전송 포함 speedup은0.0375×로 느리다. 현재 end-to-end 병목은 전송·host 대기다.**

| 목표를 막는 항목 | 확인한 근거 | 현재 결과와 한계 |
|---|---|---|
| Physical DIM 확장 | 실제 D32 array-only A1 합성114,079 / 63,400 Slice LUT,179.94% | 현재 D32와 D64 모두 array만으로 fit 실패. D64는 LUT715.30%, FF155.47%. 두 profile의 numerical은 미검증 |
| FPGA clock | B0 상위100은 WorkScheduler → activationSlot. R3 OOC route는 Matmul96 + weightLoad→PE4. Fixed P1 board 상위100은 scale 주소 검사→공유 PE enable | R3 OOC는50 MHz 내부 timing 만족. 실제 P1 board25 MHz는 WNS +6.722 ns, hold +0.017 ns, pulse +3.000 ns. 서로 다른 netlist이며 최대 Fmax는 미측정 |
| FPGA 자원 | A8 `signedMul`의 abs/sign 복원 회로가 RTL에 남고 A1이 일부 제거. Control과 조합해도 개선 유지 | Fresh R0 대비 R1 Slice LUT -8.68%, R3 59,953 → 53,127, -11.39%. Fresh R0는 historical source-identical B0와 동일 |
| 지속 처리율 | 실제 RTL에서 FIFO full, engine/vector, accumulator busy가 반복 관측 | 별도 accumulator 실험 II3 → II2 통과. 전체 DIM16 cycle·timing 이익은 미검증 |
| End-to-end 실행 | Local provider와 bulk publication으로 row별 PC 왕복을 제거. 실제 UART 전송·host 대기 측정 | Verilator median1.528364 ms, FPGA compute14.44 µs, resident 결과 회수26.162115 ms, bulk 전체40.783830 ms. 큰 job/batch와 transport 개선이 필요 |

Gemmini를 동일 FPGA·동일 interface·동일 constraints로 합성한 결과는 없다. 따라서 “IM2P가 Gemmini보다 X배 비효율적”이라고 정량 주장할 수 없다. 아래 결과는 IM2P 내부의 통제된 비교다.

## 2. IM2P vs Gemmini source comparison

Gemmini는 `8c3f9923a44a2fe2c7930587be297d6d4f8c09ca`, BSC는 tag `2026.01`로 고정했다. 요구한 Gemmini 9개 파일을 모두 읽고 원문·SHA256을 [reference manifest](../build/experiments/fpga-research-20260909-094730/reference/gemmini-manifest.json)에 보존했다. 전체 근거 표와 topology 설명은 [architecture reference](../build/experiments/fpga-research-20260909-094730/reference/ARCHITECTURE_REFERENCE.md)에 있다.

| Topic | IM2P file:line / function | Gemmini/BSC file:line / function | Measured implication |
|---|---|---|---|
| Integer multiply | [Arithmetic.bsv:69–86][im-arithmetic], `arithmeticMultiply`의 `signedMul`과 별도 add | [BSC Prelude.bs:1565–1596][bsc-prelude] sign 분리·abs·unsigned primMul·sign 복원. [Gemmini Arithmetic.scala:90–95][gm-arithmetic] `m1*m2+self` | Generated RTL과 A0/A1 합성에서 A8 LUT 차이 확인. 언어 자체의 우열을 뜻하지 않음 |
| MAC boundary | [PE.bsv:68–76, step][im-pe], 기존 weight-stationary 경로의 multiply/add 한 번 | [PE.scala:14–24,60–65][gm-pe], `MacUnit`으로 여러 dataflow 분기의 MAC 중복 방지 | IM2P에서 중복 dataflow MAC을 확인한 것은 아님. A2/A3가 A1보다 개선되지 않음 |
| 정밀도 경계 | [Config.bsv:9][im-config], [Arithmetic.bsv:61–66][im-arithmetic], [SystolicEngine.bsv:136][im-engine]: local20 → exact widen → architectural32 | [Configs.scala:23–35][gm-configs]: input8, weight8, acc32, spatial output20, mesh16 | INT20은 fragment 내부 exact 표현. Public ABI/architectural accumulator는 유지 |
| PE state | [PE.bsv:68–76][im-pe]: INT8 weight 두 bank, selector/valid, 매 PE activation/partial register | [PE.scala:58–71][gm-pe]: hardened WS는 input-width cType, default BOTH는 acc32 c1/c2 | Gemmini default BOTH를 IM2P WS-only 두 INT8 bank와 동일한 상태 비용으로 비교하면 안 됨 |
| Tile/Mesh | [SystolicArray.bsv:87–93][im-array], [SystolicArrayTiled.bsv:15–43,202–210][im-tiled]: D64는 16×16 tile 16개, tile 내부 PE마다 register | [Tile.scala:10–16,42–45][gm-tile] PE 간 combinational 연결, [Mesh.scala:42–112][gm-mesh] tile 사이 pipeline | 양쪽 모두 tile 구조가 있음. Gemmini default tile은 1×1이므로 기본 transport stage도 PE 단위 |
| Control locality | [SystolicArray.bsv:221–244][im-array] bank/clear broadcast, [SystolicEngine.bsv:123–162][im-engine] FIFO full일 때 skew와 array 정지 | [Mesh.scala:42–46,78–112][gm-mesh]: reset 없는 Pipe, data/valid/control/id/last 함께 전파 | 전역 fanout 차이는 source에서 확인. Local control로 바꾼 물리 timing 이익은 아직 미측정 |
| Skew/output | [InputSkew.bsv:65–118][im-skew]; [SystolicEngine.bsv:15–24,118–144][im-engine] 2-entry FIFO와 column별 valid/rowOffset/partial | [MeshWithDelays.scala:27,67,76–92,190–235][gm-delays]: output ready 제거, 역방향 delay로 output 정렬 | Gemmini Valid-only output은 현재 backpressure interface의 직접 대체물이 아님 |
| Completion metadata | [ExecuteController.bsv:67–80][im-execute] column별 issued/committed counter | [MeshWithDelays.scala][gm-delays] 정렬된 response와 tag | Row alignment의 metadata 절감 가능성은 후보. 추가 buffer/latency/예약 용량 검증 필요 |
| Accumulator | [Accumulator.bsv:75–177][im-accumulator], D single-port bank와 한 transaction | [AccumulatorMem.scala:118–130,189–199,410–422][gm-accumulator], write pipeline과 hazard guard | IM2P saturated II3 실측, 최소 retirement 변경으로 II2 실측. Gemmini도 무조건 II1은 아님 |
| Scheduler | [WorkScheduler.bsv:176–185,309–337][im-work], [MatmulScheduler.bsv:181,391][im-matmul], [IM2PCore.bsv:1160–1218][im-core] descriptor 계산과 slot metadata update | [LoopMatmul.scala:910–971,998–1022][gm-loop], 두 context와 load-A/B/D·execute·store FSM | B1/B1b/B2/R2/R3 실측. Fragment 경로를 줄이면 독립적인 Matmul 주소 계산이 지배. Routed R3에서도 확인 |
| Vector/scale | [VectorUnit.bsv][im-vector], [Scale.bsv:46–101][im-scale], [IM2PCore.bsv:1674–1733][im-core]: fragment마다 scale 후 INT32 누산 | [Arithmetic.scala:97–126][gm-arithmetic] rounding shift/clip, [Configs.scala:111–161][gm-configs] accumulator output scale | 양쪽 scaling semantics는 같지 않음. 작은 subfragment마다 scale하면 logical tiling 결과가 달라질 수 있음 |
| Memory/host | [IM2PCore.bsv:409–519,729–800,1906–1910,2027–2260][im-core]: activation slots, lookahead A/B, provider 요청, job 내 reuse | [Configs.scala:41–69][gm-configs]: scratchpad256 KiB, acc64 KiB, 최대16 in-flight DMA, [README][gm-readme] load/execute/store 분리 | Accumulator 용량이 같아도 memory system은 다름. IM2P는 channel별 선택된 요청 하나와 current/lookahead 내부 pending record를 가짐 |
| Flow/ABI | [im2p_config.py:65,114][im-config-script] public partial width는 architectural ABI; [fpga_build.sh:69–128][im-flow], [ooc.tcl:91–108][im-tcl] | [Gemmini README][gm-readme] Spike, RTL simulator, FireSim 구분 | Area는 unclocked OOC, timing은 10 ns synth+opt, route는 place+route. 단계마다 별도 주장 필요 |

## 3. Confirmed bottlenecks

1. **Control arithmetic depth:** B0 worst는 `workScheduler_totalKReg[1]/C → activationSlotRequestRow_1[0]/R`, 39 levels, CARRY4 20개, data delay 20.076 ns다. R3에서는 Matmul descriptor→output address가 post-opt 21 levels, routed 22 levels로 global worst다. Routed top100의 4개 weightLoad→PE 경로는 물리 control 배포도 남은 제한임을 보여준다. PE MAC·BRAM read·accumulator add가 이 global worst는 아니다. [Control/route 분석](../build/experiments/fpga-research-20260909-094730/control/CONTROL_REPORT.md)
2. **A8 arithmetic 표현 비용:** product-width sign extension 뒤 일반 `*`를 쓰는 A1은 standalone multiplier Slice LUT 121 → 63, 전체 코어 59,953 → 54,748을 달성했다. 전체 코어 clock 병목은 유지됐다. [산술 실험](../build/experiments/fpga-research-20260909-094730/arithmetic/REPORT.md), [R1](../build/experiments/fpga-research-20260909-094730/R1/REPORT.md)
3. **Downstream backpressure:** baseline M=N=K64 bypass에서 work-active 9,089 cycle 중 engine FIFO full 3,520 cycle, engine-result-valid와 vector-busy 동시 3,648 cycle을 관측했다. 이 조건들은 겹치므로 합산하지 않는다. [실측 데이터](../build/experiments/fpga-research-20260909-094730/performance/summary.json)
4. **Accumulator retirement bubble:** read/accept, write, completion-retire의 saturated II3을 RTL에서 확인했고, 마지막 cycle에 다음 read를 함께 허용해 II2를 얻었다. 통합 DIM16 성능 이익은 아직 측정하지 않았다. [II2 실험](../build/experiments/fpga-research-20260909-094730/memory/ACCUMULATOR_II2.md)
5. **현재 physical D32의 LUT 용량 초과:** A1 산술을 쓰는1,024 PE array-only가 post-opt114,079 Slice LUT로 XC7A100T 용량의179.94%다. 같은 결과의 WNS +0.374 ns는 unplaced 내부 timing일 뿐이며 resource fit 실패를 상쇄하지 않는다. [실제 D32 utilization](../build/experiments/fpga-research-20260909-094730/dim/D32-synth/opt-utilization.rpt)

6. **실제 작은 job의 transport 지배:** P1 bulk median40.783830 ms 중 compute는361 cycles/25 MHz=0.014440 ms다. 전체에서 compute를 뺀 결합 overhead는40.769390 ms다. UART 1 Mbaud8N1에서2,080 bytes의 이론 wire floor만20.8 ms로 simulator1.528364 ms보다 크다. Host timestamp로 input+publication, command/result와 output 수신 구간을 기록했다. UART wire·USB·OS 각각의 순수 지연을 독립 분리한 측정은 아니다. [실제19-job 결과](../build/experiments/fpga-research-20260909-094730/P1/hardware-measurement-1/summary.json)

현재 D32/D64 mapping의 area 한계는 확인했지만 모든 가능한 architecture의 불가능성을 증명한 것은 아니다. DDR bandwidth와 최대 board Fmax는 미측정이다. 실제 board 결과는 fixed P1 25 MHz의 범위이며 일반 R3 OOC50 MHz와 구분한다.

## 4. Rejected hypotheses

| 가설 | 실험에 따른 판단 |
|---|---|
| `signedMul`이 baseline PE diagnostic 약49k lut/mux의 전부를 설명한다 | 기각. 전체 core LUT 개선은 8.68%이고 worst control timing은 거의 유지 |
| `signedMul`은 모든 폭에서 불리하다 | 기각. A4 multiplier 22 → 24 LUT, MAC 29 → 34 LUT로 증가 |
| Explicit signed Verilog 또는 별도 MAC boundary가 A1보다 좋다 | 이번 microbenchmark 범위에서 기각. A1/A2/A3 자원·timing 동일 |
| 작은 A1 PE의 DSP mapping이 DIM16에서도 유지된다 | 기각. 단독 PE는 DSP1, 전체 core PE는 DSP0 |
| Critical path 하나를 개선하면 timing이 닫힌다 | 기각. B1 이후 Matmul 주소 경로가 상위100개 중95개를 차지 |
| B2 descriptor pipeline만으로 global timing이 크게 개선된다 | 이번 단독 실험에서 기각. B1 대비 WNS +0.135 ns, TNS 악화, fragment cycle 증가. B1b와 결합한 R2/R3에서 평가해야 함 |
| II3을 개선하려면 forwarding/multiple outstanding이 필수다 | II2에 한해 기각. 이전 write 다음 cycle에 read하므로 최소 변경으로 가능. II1에는 적용할 수 없는 결론 |
| Resident compute 가속만으로 전송 포함 가속이 보장된다 | 실제 P1에서 기각. Compute105.84×, bulk 전체0.0375×. 작은 workload와 UART 전송량을 함께 평가해야 함 |

“Vivado가 A1/A2/A3를 동일한 회로로 최적화했다”는 것은 동일한 자원 수와 timing metric에 대한 해석이다. 일반적인 gate-level formal equivalence나 모든 profile의 물리 동일성까지 확인한 것은 아니다.

## 5. Arithmetic experiment

**Hypothesis:** BSC `signedMul`의 abs/sign 복원 표현이 A8 FPGA 산술 비용을 증가시킨다. **Isolated variable:** multiply/MAC 표현만 변경했다. A0는 기존 `signedMul`, A1은 product width로 `signExtend`한 Int operand의 일반 `*`, A2는 explicit Verilog `$signed(a)*$signed(b)`, A3는 signed multiply와 partial add를 한 module에 둔 표현이다.

Microbenchmark는 operand register → combinational 연산 → result register의 같은 두 stage, 같은 enable/valid/synchronous reset을 사용했다. Vivado 2025.2, 10 ns OOC synth+opt, rebuilt hierarchy다. DSP 강제·DONT_TOUCH·timing 예외는 사용하지 않았다. 외부 I/O unconstrained 경고는 남겨 내부 register-path 비교로 한정했다. [명령·원문·결과](../build/experiments/fpga-research-20260909-094730/arithmetic/REPORT.md)

### Numerical / RTL cycle

| 검사 | 범위 | 결과 |
|---|---|---|
| A8 A0/A1/A2/A3 multiplier + MAC | 모든 65,536 operand 조합 × INT20 partial 경계 8개, 추가 random input/control 10,000 cycle | 모두 PASS |
| A4 A0/A1/A2/A3 multiplier + MAC | 모든 256 조합 × INT12 partial 경계 8개, 추가 random 10,000 cycle | 모두 PASS |
| Actual A0/A1 PE20 | 모든 65,536 조합, INT20 min/max partial 교대 | 모두 PASS, 각각 196,608 driver cycle |
| Existing PE/작은 array | TbPE, TbSystolicArray, TbSystolicArrayWeightBanks | 양쪽 PASS, weight-bank/forwarding/INT64 경계 포함 |
| DIM16 R0/R1 | full Verilator regression | 각각154 PASS, 0 failed/ignored, 24 numerical/cycle telemetry 동일 |

`(-128,-128)`, `(-128,127)`, `(127,127)`을 포함한다. 인위적인 partial min/max 입력에 product를 더해 범위를 넘는 MAC은 baseline과 동일한 two's-complement wrap을 검증했다. 실제 D16 fragment의 exact bound 증명과 overflow test를 구분한다. Micro driver cycle은 A8 534,310, A4 12,070으로 A0~A3 동일하며 GEMM cycle을 뜻하지 않는다.

### FPGA Area / Timing

아래는 `report_utilization`의 Slice LUT와 FF다. CARRY4는 별도 primitive count다. WNS/TNS는 post-opt/unplaced이며 각 micro TNS는 0이다.

| Top | A0 LUT / FF / CARRY / DSP | A1 LUT / FF / CARRY / DSP | A2 | A3 | A0 → A1 WNS ns | Logic levels |
|---|---:|---:|---|---|---:|---:|
| Mul4 | 22 / 18 / 2 / 0 | 24 / 18 / 2 / 0 | A1 동일 | A1 동일 | +4.895 → +6.136 | 7 → 4 |
| Mac4 | 29 / 34 / 5 / 0 | 34 / 34 / 5 / 0 | A1 동일 | A1 동일 | +4.599 → +4.669 | 8 → 7 |
| Mul8 | 121 / 34 / 14 / 0 | 63 / 34 / 14 / 0 | A1 동일 | A1 동일 | +1.005 → +4.570 | 12 → 8 |
| Mac8 | 108 / 58 / 15 / 0 | 81 / 58 / 17 / 0 | A1 동일 | A1 동일 | +1.620 → +2.958 | 12 → 10 |
| Actual PE20 | 153 / 49 / 15 / 0 | 60 / 49 / 5 / 1 | 미측정 | 미측정 | +0.623 → +1.882 | 13 → 8 |
| Actual 2×2 array | 564 / 189 / 60 / 0 | 193 / 189 / 20 / 4 | 미측정 | 미측정 | +0.593 → +1.882 | 13 → 8 |

A8 MAC은 LUT가25% 줄지만 CARRY는15 → 17로 늘었다. 단독 PE의 LUT 감소에는 DSP로 연산을 옮긴 효과가 포함된다. 전체 core에서는 전역 mapping이 달라 두 arithmetic variant 모두 PE DSP가0이다. B0 DSP128은 주소 계산80 + VectorUnit48, R1 DSP120은 주소 계산72 + VectorUnit48이다. 모호한 DSP cone의 endpoint까지 추적했지만, 사라진 주소 DSP 각각이 공유됐는지 LUT로 옮겨졌는지는 확정하지 않았다. [DSP attribution](../build/experiments/fpga-research-20260909-094730/control/CONTROL_REPORT.md)

**Interpretation / Next decision:** A8에 A1을 유지할 근거가 있다. A2/A3 module은 추가 이득이 없어 채택하지 않는다. A4·A16·큰 DIM에 같은 area 개선을 가정하지 않는다. Production 원본에는 반영하지 않았으며 R1/R3 snapshot에서만 검증한다.

## 6. Control experiment

### B0: 상위 경로 전체 분류

고정 historical post-opt checkpoint를 다시 열어 상위100개의 서로 다른 worst endpoint와 각 category의 worst3개를 수집했다. CSV에는 rank, slack, source/destination, data/logic/estimated-route delay, logic levels, CARRY count, hierarchy가 있다. [B0 분석 디렉터리](../build/experiments/fpga-research-20260909-094730/control/B0-control-baseline), [수집 Tcl](../build/experiments/fpga-research-20260909-094730/control/collect_paths.tcl)

| Endpoint category | Worst slack ns | Levels | CARRY4 | Critical source → destination |
|---|---:|---:|---:|---|
| activationSlot | -10.717 | 39 | 20 | totalKReg[1] → requestRow_1[0]/R |
| Matmul / output address | -8.487 | 27 | 17 | stripeRowBeginReg[1] → outputBaseReg[61] |
| weightLoad | -7.680 | 36 | 20 | totalKReg[1] → weightLoadKCountReg[2] |
| lookahead | -7.349 | 26 | 17 | stripeRowBeginReg[1] → lookaheadWorkReg[641] |
| WorkScheduler | -5.224 | 34 | 23 | totalKReg[1] → kStartReg[29] |
| Accumulator / BRAM | -2.567 | 11 | 4 | Vector arithmetic register → RAM address |
| PE | -0.280 | 14 | 5 | weight register → local partial register |
| VectorUnit | +4.194 | 5 | 0 | engine state → DSP input |

Global top100은 모두 WorkScheduler → activationSlot이다. Category probe는 global top100 바깥의 병목도 드러낸다. Category는 겹칠 수 있으며 primitive count나 resource total로 더하지 않는다.

### B1 / B1b: cycle을 보존한 표현 변경

B1은 `remainingKReg = totalK - kStart`를 state invariant로 유지한다. 반복된 wide subtraction/add/subtraction 의존성을 줄였고 Core caller와 FSM cycle은 바꾸지 않았다. B1b는 별도 patch로 Matmul의 remaining rows/columns를 저장한다. 두 경우 모두 기존 descriptor의 positive count·uint32 overflow 금지 계약 안에서만 equivalence를 주장한다.

B1은 assertion-enabled scheduler 3개, B1b는 Matmul scheduler 2개를 통과했다. 각각100,000 deterministic legal uint32 state의 보조 검증과 full DIM16 154 test를 통과했다. R0 대비24 numerical/cycle record가 동일하다. [전체 실험과 diff](../build/experiments/fpga-research-20260909-094730/control/CONTROL_REPORT.md)

| Resource / timing | B0 | B1 | B1b |
|---|---:|---:|---:|
| Slice LUT | 59,953 | 59,060 | 59,634 |
| FF | 34,370 | 34,391 | 34,433 |
| DSP / RAMB36 / RAMB18 | 128 / 16 / 0 | 128 / 16 / 0 | 128 / 16 / 0 |
| WNS ns | -10.717 | -8.489 | -8.004 |
| TNS ns | -19,849.250 | -15,977.404 | -6,181.941 |
| Failing endpoints | 6,925 / 77,892 | 5,750 / 77,886 | 4,022 / 77,450 |
| Worst levels / CARRY4 | 39 / 20 | 27 / 17 | 30 / 12 |
| Worst data delay ns | 20.076 | 18.449 | 17.363 |
| Worst logic delay ns | 8.572 | 12.642 | 5.959 |
| Worst estimated route ns | 11.504 | 5.807 | 11.404 |

B1의 activationSlot slack은 -7.072 ns, weightLoad는 -4.306 ns로 개선됐다. Global worst는 Matmul 주소 경로로 옮겨갔다. Top100 분포는 Matmul/output56, 다른 Matmul25, Matmul/lookahead14, WorkScheduler/activationSlot5다. B1b는 B1보다 LUT574·FF42가 늘지만 TNS가 크게 줄었다. B1b top100은 WorkScheduler/activationSlot84, Matmul/output16이다. 이는 한 경로를 고치고 종료할 수 없는 이유다.

### B2: 실제 register stage를 둔 descriptor

B2는 current count, next start/remaining, next count를 세 stage에서 준비한다. 첫 fragment +3 cycle, 다음 fragment마다 +2 cycle이다. 긴 조합 회로를 register 하나의 D 입력으로 옮긴 형태는 피했다. 이 첫 구현은 준비를 현재 array 실행과 overlap하지 않는다. Overlap은 이후 descriptor lifetime을 다시 설계해야 하는 별도 후보다. [B2 source와 비교](../build/experiments/fpga-research-20260909-094730/control/B2-registered-descriptor), [cycle 비교](../build/experiments/fpga-research-20260909-094730/control/B2-cycle-comparison.json)

| 대표 K | R0 total / work | B2 total / work | Total delta | Compute before / after |
|---|---:|---:|---:|---:|
| 7 | 108 / 105 | 111 / 108 | +3 | 68 / 68 |
| 16 | 117 / 114 | 120 / 117 | +3 | 68 / 68 |
| 32 | 190 / 187 | 195 / 192 | +5 | 136 / 136 |
| 33 | 263 / 260 | 270 / 267 | +7 | 204 / 204 |

B2 scheduler 3개와 full DIM16 154 test가 통과했고24 numerical result가 R0와 같다. Short K16→K32 증가분은73 → 75 cycle이며 long-run steady-state II 측정으로 표현하지 않는다. 별도 M=N16, K64 workload에서 한 output work의 fragment-start 간격은 R0 평균118 cycle, R3 평균120 cycle로 측정됐다. 각각 세 간격의 평균이며 여러 output work의 전환을 섞은 값이 아니다. 실행시간 이득 조건은 `F_after/F_before > N_after/N_before`다. R3 routed 회로의 50 MHz 내부 timing은 확인했지만 R0는 같은 flow의 detail placement에서 실패해 routed frequency가 없다. 별도 fixed P1의 실제25 MHz·361-cycle 실행은11절에 기록했다. R0/R3 일반 core의 board clock 비율 비교는 아니다.

B2 첫 timing output은 실행 중 wrapper 수정 때문에 디렉터리가 재사용됐다. 해당 결과는 제외했으며 [INVALID_REUSED_OUTPUT_DIRECTORY.json](../build/experiments/fpga-research-20260909-094730/control/B2-timing/INVALID_REUSED_OUTPUT_DIRECTORY.json)에 provenance를 남겼다. 독립된 [B2-timing-fresh](../build/experiments/fpga-research-20260909-094730/control/B2-timing-fresh)는 exit0으로 완료됐다. `replacement-provenance.json`이 source/RTL/primitive 및 실행 wrapper의 불변성을 확인한다.

| Resource / post-opt timing | B1 | Fresh B2 | Delta |
|---|---:|---:|---:|
| Slice LUT | 59,060 | 58,838 | -222 |
| FF | 34,391 | 34,474 | +83 |
| DSP / RAMB36 / RAMB18 | 128 / 16 / 0 | 128 / 16 / 0 | 0 |
| WNS ns | -8.489 | -8.354 | +0.135 |
| TNS ns | -15,977.404 | -17,446.469 | -1,469.065 |
| Failing endpoints | 5,750 / 77,886 | 9,347 / 77,951 | 분모도 변화 |
| Worst levels / CARRY4 | 27 / 17 | 28 / 18 | +1 / +1 |

B2 global worst는 여전히 Matmul `stripeRowBeginReg[1] → outputBaseReg[61]`이다. Data delay18.314 ns 중 logic12.665 ns, estimated route5.649 ns이며 DSP48E1 두 단계를 지난다. 상위100 경로는 Matmul/output58, 다른 Matmul26, Matmul/lookahead16이다. Activation-slot worst -5.119 ns와 weightLoad -4.693 ns도 이제 Matmul에서 시작한다. 따라서 B2 단독은 fragment 산술을 분리했지만 global clock 최적화로는 부족하다. WNS의 작은 개선만 보고 추가 cycle과 TNS 악화를 무시하지 않는다.

**Next decision:** B2 단독 대신 B1b와 합친 R2, A1도 포함한 R3의 실측 결과로 판단한다. 실행 중 준비 overlap은 아직 구현하지 않았다. 이를 추가하려면 Core가 사용하는 current/next descriptor를 유지하면서 별도 다음 descriptor 준비 상태와 유효성 계약을 검증해야 한다. 정상 control에 false path·근거 없는 multicycle·set_max_delay로 실패를 숨기는 변경은 없다.

## 7. Combined result

R2의 selected control은 B2 WorkScheduler + B1b MatmulScheduler다. R3는 여기에 A1 arithmetic을 더한다. Accumulator II2와 P0 wrapper는 어느 R 조합에도 넣지 않았다.

| Combination | Arithmetic | Control | Full A8/D16 RTL | K7 / K16 / K32 / K33 total cycles | Slice LUT / FF / DSP / RAMB36 | Post-opt WNS / TNS ns |
|---|---|---|---|---|---|---|
| R0 | A0 | B0 | 154 PASS; fresh | 108 / 117 / 190 / 263 | 59,953 / 34,370 / 128 / 16 | -10.717 / -19,849.250 |
| R1 | A1 | B0 | 154 PASS; 24 records R0와 동일 | 108 / 117 / 190 / 263 | 54,748 / 34,805 / 120 / 16 | -10.609 / -19,309.727 |
| R2 | A0 | B2 + B1b | 154 PASS; 24 records B2와 동일 | 111 / 120 / 195 / 270 | 58,298 / 34,537 / 128 / 16 | -7.413 / -5,762.255 |
| R3 | A1 | B2 + B1b | 154 PASS; 24 records R2와 동일 | 111 / 120 / 195 / 270 | 53,127 / 34,714 / 120 / 16 | -6.982 / -3,643.562 |

R0는 독립된 [fpga-retry](../build/experiments/fpga-research-20260909-094730/R0/fpga-retry)에서 synth+opt를 exit0으로 완료했다. LUT/FF/DSP/BRAM/WNS/TNS가 source-identical historical B0와 정확히 같아 위 표는 이제 네 조합 모두 fresh 결과를 사용한다. 첫 fresh R0 합성의 ENOSPC 중단은 실패 기록으로 남기고 대체 결과와 구분한다. 기존 historical `opt.dcp` SHA256은 `51dd001733a2a1bd6fd3dcf5b0e04d0a1f1e9bd3df5c2de74afff53ccde2643b`이며 이전 B0 path census의 출처다. R0 functional 검증도 이번 fresh154 test다. 표의 resource/WNS/TNS는 모두 timing-aware post-opt 조건이며 RAMB18은 모두0이다. [R0 명령](../build/experiments/fpga-research-20260909-094730/R0/commands.txt), [R1 결과](../build/experiments/fpga-research-20260909-094730/R1/REPORT.md), [R2 검증](../build/experiments/fpga-research-20260909-094730/R2/cycle-equivalence.json), [R3 검증](../build/experiments/fpga-research-20260909-094730/R3/numerical-cycle.json), [완료된 비교 CSV](../build/experiments/fpga-research-20260909-094730/control/control-metrics.csv)

Fresh R0의 상위100 timing path도 historical B0의 모든 수집 필드와 정확히 같다. [동등성 기록](../build/experiments/fpga-research-20260909-094730/R0/path-analysis/historical-equivalence.json). R1의 LUT delta는 -5,205(-8.68%), FF +435, DSP -8, BRAM 변화0이다. Worst는 여전히 `totalKReg[1] → activationSlotRequestRow_1[0]/R`이며 levels39 → 41, CARRY20 → 23이다. WNS +0.108 ns만으로 control 개선을 주장할 수 없다.

R2는 historical B0 대비 LUT -1,655(-2.76%), FF +167, WNS +3.304 ns다. Worst는 Matmul `descriptorReg[171] → outputBaseReg[49]`, 21 levels, CARRY4 9개, DSP48E1 두 단계다. Data17.373 ns = logic10.373 ns + estimated route7.000 ns다. 상위100은 Matmul/output57, 다른 Matmul26, lookahead17이며 activationSlot -2.490 ns, weightLoad -2.225 ns로 원래 fragment 제어 경로가 줄었다.

R3는 LUT -6,826(-11.39%), FF +344, DSP -8, WNS +3.735 ns다. Worst는 Matmul `descriptorReg[177] → outputBaseReg[53]`, 21 levels, CARRY4 10개, DSP48E1 두 단계다. Data16.942 ns = logic10.366 ns + estimated route6.576 ns다. 상위100 분포는 R2와 같은57/26/17이며 category worst는 activationSlot -1.711 ns, weightLoad -1.304 ns, WorkScheduler -1.443 ns, lookahead -6.078 ns, accumulator/BRAM -2.113 ns, PE +1.913 ns다. R1/R2 delta의 합은 LUT -6,860을 예측하지만 실제 R3 delta는 -6,826이다. 조합 이득은 별도 측정값으로만 제시한다. R3의 PE-named DSP는0이며 ambiguous Core DSP의 기능별 재추적은 아직 하지 않았다.

### R3 post-route와 clock sweep

R3는 같은 optimized checkpoint에서 place/route를 완료했다. 88,458 nets가 모두 배선됐고 routing error는0이다. 아래는 **하나의 routed 회로**를 각 clock period로 재분석한 결과다. Frequency마다 별도로 배치배선한 결과가 아니다. [Route CSV/JSON](../build/experiments/fpga-research-20260909-094730/control/route-comparison.csv), [R3 route](../build/experiments/fpga-research-20260909-094730/R3/route)

| Requested clock | WNS ns | TNS ns | Failing setup endpoints | WHS ns | Worst pulse-width slack ns |
|---|---:|---:|---:|---:|---:|
| 100 MHz / 10 ns | -8.168 | -115,763.891 | 51,869 / 78,584 | +0.054 | +4.020 |
| 50 MHz / 20 ns | +1.832 | 0 | 0 / 78,584 | +0.054 | +9.020 |
| 40 MHz / 25 ns | +6.832 | 0 | 0 / 78,584 | +0.054 | +11.520 |
| 25 MHz / 40 ns | +21.832 | 0 | 0 / 78,584 | +0.054 | +19.020 |

모든 sweep 지점에서 hold TNS와 failing endpoint는0이다. Routed worst는 Matmul `descriptorReg[188] → outputBaseReg[63]`, 22 levels, CARRY4 13개, logic10.187 ns와 **실제 routed delay7.929 ns**다. Routed 상위100은 Matmul96개(output51/기타22/lookahead23), weightLoad→PE4개다. Post-opt의 estimated route와 구분하며, 이 새 physical control 분포의 fanout 최적화 이익은 아직 측정하지 않았다.

50 MHz에서 지정된 내부 OOC setup/hold/pulse 검사는 통과했다. 그러나 `HD.CLK_SRC`가 지정되지 않은 clock 경고, input2,785개/output5,105개의 I/O delay 부재가 남아 있다. Upstream clock placement·clock skew 추정과 외부 interface timing이 완전하지 않으므로 board timing closure 또는 board Fmax로 표현하지 않는다. Unconstrained 경로는 보고서에 남겼으며 synchronous 경로를 숨기는 예외는 없다. 실제 FPGA workload 실행 전에는 R3의 wall-clock speedup을 주장하지 않는다.

같은 flow의 fresh R0는 `[Place 30-487]` detail placement packing 오류로 exit1 종료했다. 해당 단계에서 available slice10,229개에 unplaced cell이 요구하는10,486개를 배치하지 못했다(257개 부족, device total15,850 slice, control set559). 같은10 ns optimized checkpoint에서 배치 전 clock을25 ns로 완화한40 MHz 독립 retry도 available10,232개/required10,300개로68개가 부족해 exit1 종료했다. 이 두 flow의 실패는 전체 LUT utilization fit만으로 physical fit을 보장할 수 없다는 실측 사례다. 모든 clock·placement strategy에서 R0가 불가능하다는 증명은 아니다. R0 routed frequency/Fmax는 **N/A**이며 실패를 추정 Fclk로 채우지 않는다. R3의 route 성공과 R0의 이 구현 실패는 비교할 수 있지만 둘의 측정 clock 비율은 계산할 수 없다. [R0 원문 로그](../build/experiments/fpga-research-20260909-094730/R0/route-console.log), [40 MHz 독립 retry](../build/experiments/fpga-research-20260909-094730/R0/route-40mhz)

기존 unclocked area 결과 INT32-local62,476 LUT → INT20-local57,782 LUT(-7.51%)와 이 표의 timing-aware 결과는 합성 조건이 다르다. 섞어 계산하지 않는다. `lut/mux` diagnostic은 LUT1~6+MUXF7/8의 이름 count이며 Slice LUT utilization이 아니다. [기존 INT20 기록](PE_LOCAL_PARTIAL_INT20.md)

## 8. DIM scalability

Physical DIM은 동시 존재하는 PE 수, logical DIM은 software-visible tile/block, emulated target DIM은 모사하려는 target state/time이다. Physical D16로 logical D64 workload를 실행해도 physical D64 accelerator 또는 cycle-accurate D64 emulator가 되지는 않는다.

### Analytical resource budget

아래는 실제 source topology의 선언된 state와 geometry다. FF/LUT 합성 결과나 엄밀한 resource lower bound가 아니다. [계산기](../build/experiments/fpga-research-20260909-094730/reference/dim_budget.py), [CSV](../build/experiments/fpga-research-20260909-094730/reference/dim-budget.csv)

| 항목 | D16 | D32 | D64 |
|---|---:|---:|---:|
| Physical PE | 256 | 1,024 | 4,096 |
| A8 local partial bits | 20 | 21 | 22 |
| Weight 두 bank data bits | 4,096 | 16,384 | 65,536 |
| Weight valid/selector bits | 768 | 3,072 | 12,288 |
| Activation pipeline data+valid bits | 2,304 | 9,216 | 36,864 |
| Partial pipeline data+valid bits | 5,376 | 22,528 | 94,208 |
| PE 전체 declared state bits | 12,544 | 51,200 | 208,896 |
| Core activation 두 slot data bits | 4,096 | 16,384 | 65,536 |
| Core lookahead A+B data bits | 4,096 | 16,384 | 65,536 |
| InputSkew declared / observable delay bits | 2,064 / 975 | 8,224 / 3,999 | 32,832 / 16,191 |
| Nominal full-width partial-add CARRY4 | 1,280 | 6,144 | 24,576 |
| Result FIFO bits/entry; depth2 | 608 | 1,248 | 2,560 |
| Issued+committed counter bits | 160 | 384 | 896 |
| Accumulator banks × rows × width | 16×1024×32 | 32×512×32 | 64×256×32 |
| Separate-bank BRAM geometry | 16 RAMB36 | 32 RAMB18 | 64 RAMB18 |
| BRAM36 tile equivalent | 16 | 16 | 32 |

XC7A100T는63,400 LUT,126,800 FF,240 DSP,135 BRAM36 tile을 제공한다. D64의 현재 PE source state만208,896 bit이고 core staging도 크게 늘어난다. 실제 FF 최적화·SRL/RAM inference를 확인하기 전 이 값만으로 fit 결과를 확정하지 않는다. 한 DSP/PE는 D16에서도256 > 240이므로 전역 natural mapping이 필수 측정 항목이다. [AMD CLB resource](https://docs.amd.com/r/en-US/ug474_7Series_CLB/7-Series-FPGA-CLB-Resources), [AMD DS180](https://docs.amd.com/api/khub/documents/2LByHkO~nSZXcei2D55fTg/content)

Weight-bank/clear/step fanout은 D² PE에 닿는다. DIM64 tile은16×16이지만 partial이 tile row 사이로 이어지므로 각 tile도22bit가 필요하다. Power-of-two exact local width는 `productBits + log2(DIM)`이며 A4는12/13/14, A16은36/37/38이다. 이 폭 증명은 해당 full-core profile의 RTL numerical PASS를 뜻하지 않는다.

### Generated physical RTL와 미측정 항목

| DIM | Physical 구조 확인 | BSC / Icarus elaboration | Numerical/cycle | Synthesis / timing |
|---|---|---|---|---|
| D16 | 256 PE full core | 기존 회귀 실행 | R0/R1/R2/R3 각각 A8 full154 PASS | B0/B1/B1b/B2/R0/R1/R2/R3 post-opt 측정. R3 routed 내부 OOC는50 MHz 통과. 별도 fixed P1 board25 MHz timing 및19-job 동작 통과 |
| D32 | 1,024 unique PE, INT21, 9,905,849 RTL bytes | PASS / PASS | 아직 미측정 | Array-only synth+opt 완료. LUT114,079로 fit 실패. WNS +0.374 ns는 unplaced 내부 추정치 |
| D64 | 16 tile × 256 PE, 모든 tile INT22, 2,555,164 RTL bytes | PASS / PASS | 아직 미측정 | Array-only synth+opt 완료. LUT453,499/FF197,136으로 fit 실패. WNS +0.269 ns는 unplaced 추정치 |

D32 첫 BSC 실행은 stack overflow였고 같은 source에 `+RTS -K32M -RTS`를 적용한 fresh retry가 성공했다. Compiler runtime 실패를 FPGA fit 실패로 해석하지 않았다. D32/D64 top에는 skew·scheduler·vector·accumulator·staging·host shell이 없다. Array-only 결과를 full accelerator 결과로 표현하지 않는다. [DIM 명령·hash·상태](../build/experiments/fpga-research-20260909-094730/dim/README.md), [generated RTL 구조 검사](../build/experiments/fpga-research-20260909-094730/dim/structure.json)

실제 [D32 array-only 결과](../build/experiments/fpga-research-20260909-094730/dim/D32-synth)는 Slice LUT114,079(179.94%), FF48,193, DSP0, RAMB36/18 모두0, CARRY4 20,687이다. DSP 강제 없이 Vivado가 선택한 결과다. 실제10 ns clock의 post-opt WNS +0.374 ns, TNS0, WHS +0.256 ns이며 internal setup endpoint47,905개가 통과했지만 배치배선하지 않았다. Input1,525개/output995개에 I/O delay가 없다는 경고도 유지한다. **현재 D32 구현은 resource fit 실패**이며 이 timing을100 MHz physical D32 성공으로 표현하지 않는다. Array-only의 독립 port observability와 전역 mapping 때문에 그 LUT가 별도 full core의 엄밀한 하한이라는 주장도 하지 않는다. 더 큰 logical workload는 검증된 physical D16에서 실행하는 방향이 현실적인 다음 후보이다.

[D64 actual array-only](../build/experiments/fpga-research-20260909-094730/dim/D64-synth)도 합성·opt가 exit0으로 완료됐다. Slice LUT453,499(715.30%), FF197,136(155.47%), DSP0, BRAM0, CARRY4 83,648이다. 10 ns post-opt WNS +0.269 ns, TNS0, WHS +0.256 ns는 배치할 수 없는 netlist의 추정치이며 physical timing 성공이 아니다. D16은 이번에 route까지 성공한 가장 큰 power-of-two physical 구성이다. DIM17–31의 정확한 최대치는 탐색하지 않았다. D32/D64의 다른 architecture 가능성까지 부정하지 않는다. [측정 JSON](../build/experiments/fpga-research-20260909-094730/dim/measured-physical-results.json)

100 MHz에서 매 cycle 한 row를 받는다는 **가정의 interface 수요**는 D16/32/64 activation1.6/3.2/6.4 GB/s, INT32 output6.4/12.8/25.6 GB/s다. 이는 측정 bandwidth가 아니다. Reuse, 파형의 유효 lane 수, accumulator 및 FIFO stall 때문에 평균은 달라진다.

Logical D32/D64는 physical D16에서 M/N tile과 K fragment를 순차 실행할 수 있다. 정사각 GEMM을16³ subproblem으로 세면 각각8/64개다. Core가 여러 K fragment를 한 startMatmul 안에서 처리할 수 있으므로 이 수는 host command 수가 아니다. 현재 scale은 각 D16 fragment에 적용된다. 더 큰 logical fragment의 scale boundary를 보존하려면 INT32/64 temporary에 subfragment를 먼저 합치고 그 경계에서 scale하는 지원을 명시적으로 구현해야 한다. `(1>>1)+(1>>1)=0`, `(1+1)>>1=1`의 차이를 P0에서도 시험했다.

## 9. Memory / accumulator throughput

Simulator의 C++ driver에만 probe를 추가하고, CLK=0 evaluate 이후 posedge 직전에 `workActive`인 cycle을 샘플했다. Probe는 합성 RTL을 바꾸지 않는다. R0는 frozen baseline, R3는 A1+B2+B1b generated model에 같은 probe/workload를 사용했다. BSC FIFO2의 `full_reg`는 FULL_N이므로 `!full_reg`가 full이다. [Probe patch](../build/experiments/fpga-research-20260909-094730/performance/simulation-probes.patch), [독립 검토](../build/experiments/fpga-research-20260909-094730/performance/REVIEW.md)

| R0 M=N=K64, physical D16 | Bypass | Shift -1 | 조건 |
|---|---:|---:|---|
| Work-active cycles | 9,089 | 9,121 | 분모 |
| Engine execution residence | 7,232 | 7,232 | Compute state residence |
| Drain residence | 6,144 | 6,144 | Compute의 부분집합 |
| Engine FIFO full | 3,520 (38.73%) | 1,920 (21.05%) | executionActive && FIFO full |
| Engine result valid와 vector busy 동시 | 3,648 (40.14%) | 1,984 (21.75%) | Shift는 이미 수락된 FIFO head 유지 시간 포함 |
| Vector/accumulator 동시 busy | 1,920 (21.13%) | 0 | vector busy && accumulator state != Idle |
| Accumulator busy | 4,736 (52.11%) | 4,736 (51.92%) | read/completion을 포함한 non-Idle residence |
| Fragment 수 | 64 | 64 | Physical D16 fragment |
| Activation / weight / output request 수 | 1,024 / 1,024 / 256 | 1,024 / 1,024 / 256 | 실제 provider transaction count |

이 값은 겹치는 상태 조건이며 additive stall attribution이 아니다. `compute`는 productive MAC cycle이 아니라 MatrixExecute 상태 체류이고 drain을 포함한다. Shift에서 engine FIFO head는 vector가 이미 수락한 뒤에도 유지된다([IM2PCore.bsv:1705–1722][im-core]). 따라서 engine/vector 카운터를 새 결과의 수락 대기량으로 해석하거나 두 모드의 독립 stall 원인으로 직접 비교하지 않는다. Vector busy는 resultValid와 같고 accumulator commit은 Idle에서만 가능하므로 vector/accumulator 동시 busy는 현 구조의 blocked commit 경계를 실제로 관측한다. Shift에서 이 값이0이라고 accumulator 병목이 없다는 뜻은 아니다. 기존 activation/weight/output wait counter도 outstanding residence이지 외부 host RTT 측정이 아니다. [R0 원자료](../build/experiments/fpga-research-20260909-094730/performance/workload.csv), [R3 원자료](../build/experiments/fpga-research-20260909-094730/performance/workload-R3.csv)

양쪽 CSV는 각각18 workload × 5 measured sample = 90행이며 input/scale/golden과 sample key가 같다. 각 workload의 architectural counter는5회 모두 안정적이다. R0→R3에서 compute/drain/fragments/request 및 위 backpressure/busy 카운터는 모두 같다. M=N=K64의 work-active는 bypass9,089 → 9,231, Shift9,121 → 9,263으로 늘었다. R3가 accumulator throughput을 개선했다는 증거는 없다.

M=N16, K64 한 work의4 fragment에서는 시작 간격3개의 합이 R0 354 cycle, R3 360 cycle이고 평균118 → 120 cycle이다. 여러 M/N work를 섞은 전역 fragment-start 평균에는 output-work 전환이 포함된다. 예를 들어 M=N=K64 bypass 평균140.269841 → 142.507937 cycle을 같은 steady-state K-fragment II로 부르지 않는다. PE peak MAC 수와 이 측정된 유효 fragment 처리율도 구분한다.

### 단일 output work의 K4096 지속 처리율

후속 실험은 M=N16, K4096 한 output work의256 physical D16 fragment를 실행했다. 기존 example·probe·RTL은 유지하고 새 Cargo client의 shape만 바꿨다. R0/R3 각각 Bypass/Shift-1을6회 실행해 첫 회를 제외했고, warm-up을 포함한24회·6,144개 INT32 결과가 모두 golden과 일치했다. 각5개 측정 sample의 cycle/counter도 동일하다. [Long-K 원문·재현 자료](../build/experiments/fpga-research-20260909-094730/performance/long-k/REPORT.md), [비교 JSON](../build/experiments/fpga-research-20260909-094730/performance/long-k/comparison.json)

| Mode | Total cycles R0 → R3 | Delta | 255개 간격 합 R0 → R3 | 간격 평균 R0 → R3 |
|---|---:|---:|---:|---:|
| Bypass | 30,312 → 30,825 | +513 (+1.692399%) | 30,090 → 30,600 | 118.000000 → 120.000000 |
| Shift -1 | 30,440 → 30,953 | +513 (+1.685283%) | 30,216 → 30,726 | 118.494118 → 120.494118 |

여기에는 output-work 전환이 섞이지 않는다. 총513 cycle 증가는 초기3 + 이후255 fragment마다2 cycle과 일치하고, 간격 합 증가는510 cycle이다. Shift는128 scale block을 사용하며 Bypass보다 total128 cycle, 간격 합126 cycle이 많다. 따라서 짧은 K64의118/120을 모든 scaled workload에 그대로 적용하지 않는다. Aggregate sum으로 개별 간격의 최솟값·최댓값이나 모든 간격의 동일성을 주장할 수는 없다.

R0/R3의 compute28,928, drain24,576, A/W/output 요청4,096/4,096/16과 backpressure/busy 카운터는 각 모드에서 그대로다. 이 결과는 descriptor 준비의 장기 cycle 비용을 확인하며 accumulator throughput 개선을 뜻하지 않는다. 같은 clock에서는 R3 cycle이 더 많고, 다른 clock의 실행시간은 `cycles/frequency`와 실제 transfer를 따로 측정해야 한다. 이 K4096 workload는 fixed K32 P0/P1 보드 wrapper에서 실행한 결과가 아니다.

### 별도 memory experiment: completion turnover

`Accumulator.bsv` 한 파일의 세 지점만 바꿔 Completed를 consume하는 cycle에 다음 commit을 받는다. Read E, write E+1, 다음 read E+2이므로 동일 bank/address RAW에 forwarding이 필요 없다. ReadRow/preload guard는 Idle-only로 유지했다. Old completion의 valid mask는 consume edge까지 유지한다. [후보 patch](../build/experiments/fpga-research-20260909-094730/memory/candidate.patch), [상세 결과](../build/experiments/fpga-research-20260909-094730/memory/ACCUMULATOR_II2.md)

| Metric | M0 | M1 |
|---|---:|---:|
| Saturated commit II | 3 cycle | 2 cycle |
| 8개 acceptance cycles | 23,26,29,32,35,38,41,44 | 23,25,27,29,31,33,35,37 |
| Transaction rate | 1/3 per cycle | 1/2 per cycle |
| Acceptance → write latency | 1 cycle | 1 cycle |
| Area / WNS / TNS | 이번 실험 미측정 | 이번 실험 미측정 |

독립 Icarus RTL micro, 기존 TbAccumulator, 기존 TbCoreBramBoundary가 M0/M1에서 모두 PASS다. 동일 주소 반복 RMW, sparse mask, INT32 wrap/INT64 범위, completion5-cycle hold, read3-cycle hold, read/preload collision-ready 검사를 포함한다. BSC `-check-assert -show-schedule`로 consumeCompletion이 commit보다 먼저 schedule되는 것을 확인했다.

새 consume-enable → commit-ready 조합 경로가 생긴다. Full DIM16 regression·통합 timing·실제 workload cycle 이익은 아직 없다. 따라서 transaction rate1.5×를 전체 core speedup으로 표현하지 않으며 R3에 섞지 않았다. Row alignment, forwarding, II1, banked outstanding은 이 결과 이후 별도 측정할 후보로 남긴다.

## 10. Host–FPGA architecture

### P0: 구현한 resident prototype

[ResidentP0.bsv](../build/experiments/fpga-research-20260909-094730/P0/source/synth/ResidentP0.bsv)는 기존 core를 감싸고 A/B/scale/output provider를 로컬에서 종료한다. M=N16, K32, scaleBlock32 고정이다. A32×128bit BRAM, B32×128bit BRAM, output16×512bit BRAM, scale128bit register를 사용한다. 원래 A0/control을 사용한 P0의 실제 post-opt 결과는 LUT52,342, FF27,306, DSP96, RAMB36 27개와 RAMB18 1개다. 10 ns WNS -10.777 ns, TNS -215,055.547 ns로 timing은 실패한다. Wide/shallow local memory는 작은 byte 용량보다 많은 BRAM tile을 사용한다. 고정 descriptor와 wrapper가 함께 바뀌므로 area 차이를 autonomy 하나의 효과로 돌리지 않는다. [P0 합성 결과](../build/experiments/fpga-research-20260909-094730/P0/FPGA_REPORT.md)

Host는 A/B 각32 word와 scale을 preload하고 `start(Bool shift)`를 한 번 호출한다. False는 VectorMultiply, True는 VectorShift다. Running 동안 host load는 허용하지 않으며 row-provider response를 host가 처리할 필요가 없다. Done은 core cycle count 차이와 retained result를 제공한다. Output read는 done 이후 가능하고 response는 consume까지 유지한다. A/B preload valid는 acknowledge 이후에도 유지돼 다음 job이 재사용할 수 있다. [설계·주소·ownership·검증](../build/experiments/fpga-research-20260909-094730/P0/README.md)

| RTL job | 검증 목적 | 결과 |
|---|---|---|
| INT20 widening + multiply | A/B=-128, 양·음 full-range scale, fragment-local262,144 이후 INT32 누산 | 256 outputs PASS,356 cycle |
| Fragment shift boundary | 각 fragment +1, shift -1의 경계 보존 | 256 outputs PASS,356 cycle |
| Random full-range shift | Signed A/B, shift exponent -128부터127 경계 포함 | 256 outputs PASS,356 cycle |
| Host benchmark와 동일 데이터 | A[index]=(37index+11)mod256, B=(71index+19)mod256 signed, shift -1 | 256 outputs PASS,356 cycle |

네 job 총1,024 outputs가 통과했다. Missing preload, unpublished output, held response, stable completion도 검사했다. 결과는 generated RTL의 Verilator 실행이며 보드 실행이 아니다. 최종 source/hash/명령/로그는 [P0 attempt3](../build/experiments/fpga-research-20260909-094730/P0/attempt3)에 있다. Fixed shape를 넘어서는 descriptor engine, DDR controller, transport는 이 prototype의 범위 밖이다.

같은 wrapper 아래 core의 세 파일만 R3로 바꾼 별도 **P0-R3**도 같은4 job·1,024 outputs를 통과했다. 모든 job은361 cycle로, 원래 P0의356 cycle보다5 cycle 늘었다. Wrapper·입출력·golden test는 동일하다. 실제 P1 보드는 이 검증된 R3 core를 사용했다. [P0-R3 source lineage](../build/experiments/fpga-research-20260909-094730/P0-R3/lineage.json), [실제 수치/cycle 로그](../build/experiments/fpga-research-20260909-094730/P0-R3/attempt1/numerical.log)

### P1: 구현·RTL 검증한 fixed-shape bulk ingress

최소 P1을 별도 UART shell로 구현했다. Host는 `L`과 A512+B512+scale16, 총1,040 payload byte를 연속 전송한다. Device는 수신 중 publication을 무효화하고, 65개 local write가 모두 완료된 뒤 ack 한 번을 반환한다. 그 뒤 `S` 한 byte로 실행하고 결과1,036 byte를 묶어 회수한다. Row마다 host ack를 요구하지 않는다. 같은 buffer는 다음 `S`에서 재사용된다.

Mock의 partial pause·overflow·publication 검사와 host PTY raw8N1 검사가 통과했다. **실제 R3-P0 generated RTL + UART**에서도 resident, changed bulk, reuse 3 jobs·768 outputs가 통과했고 각361 core cycle이었다. UART command/result interval은260,627 simulated shell cycles이며 core latency와 다르다. 이 validation의 UART simulation wall-clock을 일반 simulator workload의 비교 기준으로 사용하지 않는다. [P1 protocol·ownership·검증](../build/experiments/fpga-research-20260909-094730/P1/board-source/README.md), [실제 RTL 보고서](../build/experiments/fpga-research-20260909-094730/P1/full-rtl/REPORT.md)

범위는16×16×32, 한 outstanding transaction이다. DDR, variable job descriptor, double buffering, CRC/retry/resynchronization은 구현하지 않았다. Framing 오류나 미완료 payload는 reset 후 전체 전송이 필요할 수 있다. 이것은 publication correctness의 prototype이며 production transport integrity를 대신하지 않는다. 1 Mbaud8N1에서 bulk+ack+start+result 총2,080 byte의 wire floor는20.8 ms다. 실제19-job 검증에서 bulk 전체 median40.783830 ms를 측정했다. 세부 구간과 통계는11절에 있다.

### 실제 board 구현·timing·programming

Frozen P1은100 MHz board 입력에서 MMCM/BUFG로25 MHz core clock을 만들고 실제 UART/LED/reset pin을 사용한다. Post-route LUT42,878, FF28,671, DSP27, RAMB36=27/RAMB18=1이다. Setup WNS +6.722 ns/TNS0, hold +0.017 ns, pulse +3.000 ns이며71,933개 routable net 모두 배선됐다. 이 fixed-shape/VectorShift shell은 일반 R3보다 기능·주소 계산이 상수화되므로 area 차이를 P1의 순수 개선으로 표현하지 않는다.

Top100 setup path는 모두 scale 주소 검사→공유 PE CE다. Worst data32.748 ns=logic11.831+실제 route20.917 ns,24 levels/CARRY4 14개/DSP2개다. 최대 enable net의 fanout7,460, route5.530 ns는 전체 data의16.89%다. Global fanout만을 전체 지연 원인으로 단정하지 않는다. [Board timing 검토](../build/experiments/fpga-research-20260909-094730/P1/BOARD_TIMING_REVIEW.md)

`check_timing` 내부 미제약 endpoint는0이지만 timing summary에는 source clock이 없는233개가 별도로 있었다. 전부 열거해228개는 GND에 연결된 DSP A[29]가 무등록 DIRECT cascade ACOUT[29]로 전달되는 상수0 경로,4개는 MMCM LOCKED→비동기 reset,1개는 UART 첫 synchronizer로 분류했다. 비동기 출력5개와 MMCM feedback1개도 기록했다. 정상 동기 경로에 예외를 추가하지 않았다. CDC의 “All paths are Safely Timed”는 input-delay 없는 외부 UART까지 인증하지 않는다. DRC error/critical warning0, DSP pipeline warning65개는 보존했다. [상수·비동기 경로 증명](../build/experiments/fpga-research-20260909-094730/P1/board-unconstrained-review/classification.json)

사용자 승인 후 SHA256 `560d5496a6b8de9f0eb5a694583085164d3c47e9bf90157fc0822ac3b5198b3c`를 programming 직전·직후 확인하고 configuration SRAM만 한 번 프로그래밍했다. Flash/fuse 쓰기는 없다. 지정 cable과 `xc7a100t_0`, JTAG die `xc7a100t`, checkpoint part `xc7a100tcsg324-1`이 일치했다. JTAG는 package/speed를 읽지 못하며 bitstream header의 CSG324와 DCP의-1을 별도로 대조했다. EOS/DONE=1, CRC/IDCODE error=0, Vivado exit0이다. [Programming 원문](../build/experiments/fpga-research-20260909-094730/hardware/approved-program-1/programming.log), [독립 검토](../build/experiments/fpga-research-20260909-094730/P1/programming-review.json)

이 bitstream을 그대로 유지해 실제19 jobs·4,864 outputs가 PASS했다. 변경된 bulk payload가 변경된 golden 결과를 만들었고 다음 default bulk 및 reuse도 통과했다. 매 job hardware counter는361로 RTL과 일치한다. 측정 중 RTL/source/bitstream을 수정하지 않았다.

확장할 streaming 계약에서는 Host는 free ingress buffer를 소유해 contiguous A/weight/scale stripe를 전송한다. Device memory에 전송이 완료됐다는 fence 이후 descriptor를 publish한다. FPGA는 published buffer만 읽고 job/stripe completion까지 immutable lifetime을 보장한다. Output은 device가 완성한 뒤 host transfer 완료까지 보유한다. 재사용 weight/scale에는 generation과 마지막 consumer를 기록한다. 기존 ExSIA AsyncStripes publication/completion 의미를 유지해야 한다.

Descriptor에는 device offset, length/stride, precision, logical scale boundary, job/generation이 필요하다. 내부 row request는 scratchpad/DDR가 서비스한다. Row마다 PC 왕복을 요구하는 transport는 구현하지 않는다. Double buffering은 실제 transfer/compute overlap을 확인할 때 도입한다.

| Memory option | 가능한 범위 | 필요한 실측 |
|---|---|---|
| BRAM-only resident | 현재 P0 같은 작은 working set | Mapped resource, routed clock, board cycle |
| DDR staging + BRAM scratchpad | BRAM보다 큰 layer/stripe와 reuse | MIG calibration, arbitration, bandwidth, shell resource/timing |
| Ethernet/UART + local buffer | Batched stripe ingress/egress | 실제 transport rate/RTT, publication, overlap |

Arty A7-100T는 DDR3L256 MB,16-bit333 MHz/667 MT/s,10/100 Mb/s Ethernet,USB-UART를 제공한다. 이론상 raw DDR 약1.334 GB/s, Ethernet line-rate12.5 MB/s이며 protocol/controller 손실 전 값이다. USB connector를 bulk USB accelerator endpoint로 가정하지 않는다. [Digilent 공식 사양](https://digilent.com/shop/arty-a7-100t-artix-7-fpga-development-board/)

인접 `llama.cpp-gemmini` checkout의 실제 GGUF header도 읽었다. Q8_HP1은 해당 checkout의 `GGML_QUANT_SIZES` 기준32개 값당40 byte이므로 단순 INT8 payload보다 크다. 모든 tensor offset/크기를 파일 경계와 대조하고 겹침이 없음을 검사했다. [실제 model/tensor inventory](../build/experiments/fpga-research-20260909-094730/reference/local-model-memory-inventory.json), [재현 script](../build/experiments/fpga-research-20260909-094730/reference/model_inventory.py)

| 로컬 모델 | 전체 tensor storage | 대표 tensor | DDR 의미 |
|---|---:|---|---|
| GPT-2 Q8_HP1 |158,046,144 B (150.72 MiB)|768×3072 FFN weight 2.8125 MiB|Weight만 용량상 가능. KV·activation·staging·예약 공간을 합쳐야 하므로 전체 resident 실행은 미검증|
| Llama3.2-1B Q8_HP1 |1,544,953,984 B (1473.38 MiB)|2048×8192 FFN weight20 MiB; embedding328,335,360 B|Embedding 하나도256 MiB보다 큼. 전체 모델 resident 불가; layer/stripe staging 필요|

GGUF storage가 존재한다고 FPGA에서 그 quantization/ExSIA 경로를 실행한 것은 아니다. Weight와 scale/residual 변환, CPU layer 경계, 실제 MIG usable memory와 bandwidth는 별도 통합 검증 대상이다.

## 11. Verilator vs FPGA performance

### 실제 측정한 simulator workload wall-clock

Host는 Intel i5-12400F, Linux6.8, BSC2026.01, Verilator5.051 devel, g++11.4, Rust/Cargo1.91.1이다. 아래 수치는 **quiet-final 재측정**이다. 기존 compiled executable을 순차 실행했고 연구 작업의 synthesis·route·compile을 동시에 실행하지 않았다. 외부 machine activity와 CPU scheduling/frequency까지 통제한 것은 아니다. Release benchmark가 deterministic input 준비·simulator construction 이후 `execute_matmul`만 Instant로 측정하고 검증은 interval 밖에서 수행했다. BSC/Verilator/C++/Rust compile, process startup을 제외했다. R0/R3 각각 shape/mode당 warm-up1회 이후5회,18 group 총90 sample이다. 실행 interval에는 API 내부 validation/descriptor 작성·provider service·결과 처리·probe 등 실제 runtime이 포함된다. [Quiet-final 명령·binary hash·실행 환경](../build/experiments/fpga-research-20260909-094730/performance/quiet-final/provenance.json), [benchmark](../build/experiments/fpga-research-20260909-094730/performance/source/sim/examples/research_wallclock.rs), [host/tool 기록](../build/experiments/fpga-research-20260909-094730/performance/host.txt)

| M × N × K | Bypass cycles R0 / R3 | Bypass R0 median ms | Bypass R3 median ms | Shift -1 cycles R0 / R3 | Shift R0 median ms | Shift R3 median ms |
|---|---:|---:|---:|---:|---:|---:|
| 1×1×7 | 108 / 111 | 1.552505 | 0.522705 | 110 / 113 | 0.532280 | 0.545386 |
| 1×1×16 | 117 / 120 | 0.810677 | 1.144376 | 119 / 122 | 0.622575 | 0.596839 |
| 1×1×32 | 190 / 195 | 1.016038 | 0.974780 | 192 / 197 | 1.017083 | 0.978863 |
| 1×1×33 | 263 / 270 | 1.416793 | 1.353117 | 265 / 272 | 1.409029 | 1.351193 |
| 16×16×16 | 222 / 225 | 1.166190 | 1.106400 | 224 / 227 | 1.177159 | 1.114749 |
| 16×16×32 | 340 / 345 | 1.806562 | 1.712724 | 342 / 347 | 1.815185 | 1.721907 |
| 16×16×64 | 576 / 585 | 3.081857 | 2.938678 | 578 / 587 | 3.087896 | 2.937121 |
| 32×32×64 | 2,292 / 2,326 | 11.141341 | 10.592198 | 2,300 / 2,334 | 11.150385 | 11.572667 |
| 64×64×64 | 9,092 / 9,234 | 44.086443 | 42.037557 | 9,124 / 9,266 | 44.212731 | 42.077545 |

각 sample의 numerical output을 같은 fragment-wise reference와 비교했다. 위 큰 shape도 physical D16에서 실행한 logical workload다. 일부 짧은 sample의 편차가 여전히 커서 median만으로 안정적인 speedup을 단정하지 않는다. Target cycle이 증가해도 generated model의 software 실행은 짧아질 수 있으며 이를 FPGA 이익으로 해석하지 않는다. 이전 동시 부하 실험은 보존하고 최종 simulator 비교에는 위 재측정값을 사용한다. [R0 CSV](../build/experiments/fpga-research-20260909-094730/performance/quiet-final/R0-workload.csv), [R3 CSV](../build/experiments/fpga-research-20260909-094730/performance/quiet-final/R3-workload.csv), [R0 median/min/max](../build/experiments/fpga-research-20260909-094730/performance/quiet-final/R0-summary.json), [R3 median/min/max](../build/experiments/fpga-research-20260909-094730/performance/quiet-final/R3-summary.json)

P0와 P0-R3도 이미 빌드한 executable을 각각6회 실행하고 첫 회를 제외했다. 아래는 모두 **matched M=N16,K32,Shift-1** job이다. A는 `(37×index+11) mod256`, B는 `(71×index+19) mod256`을 signed INT8로 해석하고 scale은 전부-1이다. 각 D16 fragment에 shift를 적용한 뒤 INT32로 합산한다. P1의 기본 resident 데이터와 동일한 수치 workload이며 다른 bulk 입력/scale의 timing에 재사용하지 않는다.

| Resident RTL variant | Core cycles | Verilator interval median ns | Min / max ns | 측정 sample |
|---|---:|---:|---:|---:|
| P0, A0/original control | 356 | 1,287,181 | 1,282,858 / 2,617,352 | 6회 중 첫 회 제외5회 |
| P0-R3, A1/B2/B1b | 361 | **1,528,364** | **1,115,562 / 2,045,142** | 6회 중 첫 회 제외5회 |

따라서 선택된 P0-R3 resident workload의 simulator numerator는 **1.528364 ms**다. Preload·결과검증·compile·process startup은 이 내부 resident interval 밖이다. Resident C++는 `-O1 -g0`, provider benchmark는 release build이고 memory provider/측정 경계도 달라 둘의 차이를 같은 조건의 simulator speedup으로 단정하지 않는다. P0-R3 resident361 cycle과 R3 host-provider347 cycle도 구분한다. [P0 quiet summary](../build/experiments/fpga-research-20260909-094730/performance/quiet-final/P0-summary.json), [P0-R3 quiet summary](../build/experiments/fpga-research-20260909-094730/performance/quiet-final/P0-R3-summary.json)

### 실제 FPGA 19-job 측정

Resident6회 → changed bulk1회 → default bulk6회 → reuse6회를 실행했다. 각6회 그룹은1회 warm-up을 제외한5회로 median/min/max를 계산했다. Changed bulk는 correctness만 평가하며 다른 데이터의 simulator 시간으로 speedup을 만들지 않았다. 모든19회는 A8/W8, physical D16, M=N16/K32, partial20/acc32, hardware361 cycles였다. 기본 payload는 위 P0-R3 simulator와 동일하다.

| 측정 범위 | Median ms | Min ms | Max ms | 동일 Verilator median 대비 speedup |
|---|---:|---:|---:|---:|
| Verilator P0-R3 workload 실행 |1.528364|1.115562|2.045142|기준|
| Mode A: FPGA resident compute, cycles/clock |0.014440|0.014440|0.014440|**105.8424×**|
| Resident: start+compute+결과 회수 전체 |26.162115|25.763585|26.205845|0.058419×|
| Mode B: bulk input+publication+start+compute+결과 회수 전체 |40.783830|39.700838|41.374776|**0.037475×**|
| Reuse: local data 재사용+start+compute+결과 회수 전체 |25.993695|25.849374|26.270528|0.058797×|
| Mode C: CPU/ExSIA/model 전체 경로 |미측정|미측정|미측정|N/A|

Mode A 시간은 **실제 hardware counter361 / 구현된 nominal25 MHz =14.44 µs**로 구했다. MMCM/generated clock과 routed timing을 검증했으며 외부 frequency counter로 oscillator 오차를 계측한 값은 아니다. 나머지 FPGA interval은 host `monotonic_ns` 실측이다. 이105.84×는 FPGA workload execution wall-clock과 software Verilator simulation의 비교이며 target architecture 자체가105.84× 빨라졌다는 뜻이 아니다.

`resident_speedup = 1.528364 / 0.014440 = 105.8424`.
`end_to_end_speedup = 1.528364 / 40.783830 = 0.037475`.
후자는 **이 fixed kernel의 전송 포함** 속도이며 전체 llama.cpp/ExSIA 모델 Mode C가 아니다. Bulk는 simulator보다 약26.68배 오래 걸렸다.

| Host 관측 구간, median ms | Resident | Bulk | Reuse |
|---|---:|---:|---:|
| Input 전송 시작→publication ack 완료 |0|14.761331|0|
| S command→전체 reply 완료 |26.161721|26.050323|25.992839|
| Command OS enqueue |0.019904|0.018350|0.017250|
| Command→첫 reply host read |5.282722|5.309087|5.311539|
| 첫 output payload read→마지막 read |20.862199|20.738392|20.681831|
| 전체 시간−cycles/clock, 결합 overhead 추정 |26.147675|40.769390|25.979255|

Per-job byte count는 resident/reuse에서 H→F1(S), F→H1,036(header12+INT32 output1,024)이고, bulk는 H→F1,042(L+payload1,040+S), F→H1,038(ack2+reply1,036)이다. 전체19 jobs는 input payload7,280 B, load command7 B, start19 B, ack14 B, reply19,684 B를 전송했다. Input publication 이후 실행하고 done 이후 결과를 보내므로 구현한 overlap은 없다. 독립 구간의 median 합은 전체 interval median과 같을 필요가 없다.

Input 구간에는 reverse ack도 포함된다. Output timestamp는 USB/OS read 반환 시점으로 실제 UART 첫/마지막 bit 시점이 아니다. Frozen protocol에는 별도 start ack/compute boundary marker가 없어 순수 command wire·output wire·USB queueing을 독립 분리할 수 없다. 해당 wire-time field는 null이며 결합 overhead를 특정 원인의 직접 실측값으로 바꾸지 않았다. `latency_timer=16 ms`는 read-only로 확인했지만 조정하거나 지연 기여분을 단독 측정하지 않았다. UART2,080 bytes의 계산상20.8 ms wire floor만으로도 simulator 시간을 넘으므로 작은 job의 end-to-end 목표에는 batching·reuse·transport 개선이 필요하다.

양쪽 timer에서 compile/preload preparation/golden/numerical verification/process startup을 제외했다. FPGA host port open/configuration도 제외했다. Simulator는 보존한 executable6회 실행/첫 회 제외 정책이며 board도 각 mode6회/첫 회 제외다. Simulator runtime 편차는 위 min/max에 남겼다. [실제 per-job 원본](../build/experiments/fpga-research-20260909-094730/P1/hardware-measurement-1/samples.jsonl), [모든 구간 median/min/max](../build/experiments/fpga-research-20260909-094730/P1/hardware-measurement-1/summary.json), [측정 코드·정의](../build/experiments/fpga-research-20260909-094730/P1/measurement-v2/README.md)

검토용 [19-job CSV](../build/experiments/fpga-research-20260909-094730/P1/hardware-measurement-1/export/per-job.csv), [구간별 통계 CSV](../build/experiments/fpga-research-20260909-094730/P1/hardware-measurement-1/export/phase-statistics.csv), [비교 CSV](../build/experiments/fpga-research-20260909-094730/P1/hardware-measurement-1/export/comparison.csv)를 보존했다. 독립 검토에서 모든 통계·cycle·payload/golden 차이·hash·timestamp 산술을 재계산해 일치했다. Physical UART reply는 실행 중256개 값 전부를 golden과 비교했으나 raw packet 자체를 파일에 남기지는 않았다. 따라서 offline 검토는 보존한 decoder와 실행 로그의 검증이며 실제 수신값4,864개를 파일에서 다시 replay한 검증은 아니다. [독립 검토 범위](../build/experiments/fpga-research-20260909-094730/P1/hardware-measurement-1/independent-review.json)

FireSim은 target clock과 host FPGA clock을 분리하고 한 target cycle에 필요한 host cycle 수가 변할 수 있다. Physical D16로 D64 state/time을 모사한다면 같은 구분이 필요하다. FPGA execution/emulation wall-clock이 simulator보다 빨라지는 것과 target architecture 자체의 cycle 개선은 다른 주장이다. [FireSim 공식 개요](https://docs.fires.im/en/latest/Golden-Gate/Overview.html)

## 12. What can be claimed

- 보존한 INT20 baseline 대비 A1 A8/D16 core의 timing-aware post-opt Slice LUT가59,953 → 54,748로8.68% 감소했고 full154 test와24 numerical/cycle record가 일치했다.
- B1의 post-opt control WNS가 -10.717 → -8.489 ns로 개선됐고 cycle은 유지됐다. B1b는 area 증가와 함께 WNS -8.004 ns, TNS -6,181.941 ns를 얻었다.
- R3는 fresh R0 대비 timing-aware post-opt Slice LUT59,953 → 53,127(-11.39%), WNS -10.717 → -6.982 ns, TNS -19,849.250 → -3,643.562 ns를 얻었다. Full154 test를 통과했고 descriptor 준비에 첫3, 이후 fragment당2 cycle을 더 사용한다.
- R3의 실제 배선은 routing error0개다. Routed LUT53,478, FF34,848이며, 같은 회로의50 MHz 분석에서 내부 setup WNS +1.832 ns, TNS0, hold +0.054 ns를 얻었다. OOC clock-source/I/O 한계 때문에 보드 closure나 최대 Fmax로 표현하지 않는다.
- Accumulator 단독 saturated commit II3 → II2를 generated RTL에서 확인했다. Focused Core/BRAM 검증까지 통과했다.
- P0 fixed16×16×32 workload를 local provider로 자율 실행하는 RTL prototype에서4 job·1,024 outputs가 PASS했다.
- 실제 P1 fixed-shape board 구현의25 MHz synchronous setup/hold/pulse가 통과했다. Clock 없는233 endpoint는 상수228+비동기5로 모두 분류했고 예외를 추가하지 않았다. 실제 보드19 jobs·4,864 outputs가 PASS, 모든 hardware361 cycles가 RTL과 일치했다.
- 동일 데이터와1 warm-up+5 sample 정책의 Verilator median1.528364 ms 대비 hardware cycles/nominal25 MHz의 resident compute14.44 µs는105.84×다. 실제 bulk 전송 포함 median40.783830 ms의 speedup은0.0375×로, end-to-end kernel 가속은 달성하지 못했다.
- D32 physical array만으로 Slice LUT114,079/63,400(179.94%)을 사용해 현재 구현의 fit 실패를 확인했다. D64도 LUT453,499/FF197,136으로 현재 mapping의 fit 실패를 확인했다. D32/D64 local21/22bit wiring과 physical PE 수를 확인했다. R0/R3 각각18 workload group,90 sample의 Verilator workload wall-clock을 측정했다.

## 13. What cannot yet be claimed

- 최대 post-route Fmax,100 MHz timing closure, 외부 비동기 pin의 synchronous I/O timing은 주장하지 않는다. 실제 P1의25 MHz timing·19-job 동작과 R3 OOC 내부50 MHz를 구분한다. Nominal clock은 외부 계측한 oscillator frequency가 아니다. Post-opt estimated route를 실제 routed delay로 쓰지 않는다.
- D32/D64 full-core fit, numerical profile PASS, physical DIM scaling 성능은 아직 주장할 수 없다. Source-state bit 수나 elaboration 성공은 synthesis fit이 아니다.
- R2/R3 physical delta는 독립된 실제 합성값이다. 다른 DIM·다른 clock·다른 shell로 그대로 일반화하지 않는다.
- 실제 P1 fixed kernel의 resident/전송 포함 결과를 다른 shape·정밀도·DDR·Ethernet으로 일반화하지 않는다. 전송/compute overlap, 순수 UART wire/USB/OS 지연 분리, llama.cpp/ExSIA Mode C end-to-end speedup은 미측정이다.
- Gemmini 대비 동일 조건 resource/timing 우위, A4/A16에 대한 A8 area 이익, II2의 전체 DIM16 speedup은 검증하지 않았다.
- Production Verilog의 `dynamicAssert` 활성화와 기존 G0117 action-shadowing은 별도 validation task다. 소규모 실험의 `-check-assert` 성공이 production 전체 validation을 대신하지 않는다. 경고를 suppress하지 않았다.

사용자가 승인한 bitstream을 volatile programming했고 workload 측정을 완료했다. Flash/nonvolatile memory는 쓰지 않았다. Package/speed는 bit header와 구현 DCP의 근거이며 JTAG silicon ID의 증명 범위를 확대하지 않는다. 로컬 모델의 tensor 크기는 확인했지만 전체 모델 execution은 측정하지 않았다.

## 14. Recommended production architecture

아래는 검증 수준별 권고다. 어떤 후보도 원본 production source에 자동 반영하지 않았다.

**다음 결정:** 이번19-job 측정으로 PE/Fmax를 먼저 더 바꿀 근거는 약해졌다. 현재 compute는 bulk 전체의 약0.0354%다. 다음 실험은 frozen P1을 기준으로 host/USB 지연을 분리하고, 이후 여러 GEMM을 한 descriptor로 실행해 weight/scale 및 중간 결과를 local에 유지하는 batch 범위를 설계하는 순서가 맞다. UART의 wire floor 자체가 크므로 driver latency 조정만으로 bulk 가속을 보장할 수 없다. 더 빠른 physical transport와 DDR staging은 실제 layer 크기·reuse traffic을 기준으로 선택한다. 이번 측정 이후 추가 RTL 변경·재합성·재프로그래밍은 하지 않았다.

| 요소 | 검증된 기반 / 유지할 계약 | 후속 후보와 채택 조건 |
|---|---|---|
| PE arithmetic | A8 local20 exact, engine에서 INT32 widen, architectural32/ABI 유지. A1은 full numerical/area 근거 있음 | A1을 A8 candidate로 유지. 다른 폭은 독립 검증. A2/A3 추가 module 불필요 |
| Physical tile | D16 fixed P1 board 동작 확인. D32/D64 현재 array-only mapping은 자원 초과 | D16을 측정된 출발점으로 유지. Logical tiling의 cycle과 scale boundary를 별도 보존 |
| Pipeline/control | 기존 PE transport cycle과 valid/bank 의미 유지. B1/B1b cycle-preserving 검증 완료 | B2는 routed frequency 이익과 cycle 비용 비교. 실행 중 next descriptor 준비 overlap은 별도 설계 |
| Address generation | Residual critical path가 Matmul/Core wide address 계산임을 확인 | Bounded count와 address update/pipeline을 단계별 실험. 임의 narrowing 금지 |
| Accumulator | 64 KiB logical capacity, bank/address/INT32 wrap 유지 | II2는 focused candidate. Full D16·R3 regression와 consume→ready timing 이후 판단 |
| Output path | Column별 valid/offset과 backpressure 보존 | Row alignment는 register·metadata·stall 비용 측정 후 판단. Gemmini Valid-only interface 직접 이식 금지 |
| Local memory | P0 FPGA-local A/B/scale/output provider와 complete-before-read 계약 | DDR staging+BRAM scratchpad는 shell resource, calibration, measured bandwidth가 확보될 때 확대 |
| Host interface | 실제 P1 one start/local reuse/done·cycles·results 및 publication 검증 | 다중 job batch와 중간 결과 local 유지로 전송 상각. 빠른 transport/DDR는 traffic과 timing 측정 후 결정. Per-row physical RPC 금지 |
| Logical scale | 현재 physical fragment별 scale semantics를 reference로 유지 | 큰 logical fragment는 wider temporary에 먼저 누산 후 정확한 scale boundary에서 변환 |

### 재현과 실험 보존

실험은 `build/experiments/fpga-research-20260909-094730` 아래 source snapshot·patch·tool version·command·generated hash·numerical/cycle log·Vivado report를 보존한다. 완료 marker/exit status가 없는 실행은 최종 수치에서 제외했다. 새 측정은 항상 새 output directory를 사용한다.

```sh
# Repository root에서 고정 source의 회귀와 timing을 새 디렉터리로 실행한다.
IM2P_RESEARCH="$PWD/build/experiments/fpga-research-20260909-094730"
cd "$IM2P_RESEARCH/R1/source"
CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_TEST_DEBUG=0 CARGO_INCREMENTAL=0 \
  CARGO_BUILD_JOBS=2 make sim-test-a8-w8-d16 \
  BUILD_DIR="$IM2P_RESEARCH/reproduce-R1/sim-build"
scripts/fpga_build.sh --mode timing --bits 8 --dim 16 --hierarchy rebuilt \
  --out "$IM2P_RESEARCH/reproduce-R1/fpga"
```

P0와 accumulator는 repository root에서 다음과 같이 fresh path를 지정해 재실행한다. 각각 실행 당시 정확한 BSC/Icarus/Verilator 명령도 JSON으로 저장돼 있다.

```sh
python3 build/experiments/fpga-research-20260909-094730/P0/run_check.py \
  /tmp/im2p-p0-fresh
python3 build/experiments/fpga-research-20260909-094730/memory/run_checks.py \
  build/experiments/fpga-research-20260909-094730/memory/source \
  /tmp/im2p-memory-m0-fresh 3
python3 build/experiments/fpga-research-20260909-094730/memory/run_checks.py \
  build/experiments/fpga-research-20260909-094730/memory/ii2/source \
  /tmp/im2p-memory-m1-fresh 2
```

Arithmetic의 generation/synthesis script와 `summary.csv`, control의 `collect_paths.tcl`/`parse_paths.py`, DIM의 `generate_rtl.py`/`check_structure.py`, performance의 `commands.txt`가 각 실험의 재현 entry다. Parameter·working directory·source/hash는 해당 상세 report를 따른다. 기존 artifact directory를 덮어쓰지 않는다.

공유 filesystem ENOSPC 때문에 fresh R0와 첫 B1 합성이 실패했고 P0 첫 compile도 중단됐다. Source·RTL·로그를 남긴 뒤 새 directory에서 가능한 실험을 재개했다. 승인된 정리는 이번 세션이 만든 R0/B1 Cargo debug output1.27 GiB였으며 기존 사용자 source를 제거하지 않았다. 이후 보존한 executable/hash와 retry provenance는 해당 directory에 기록했다. [정리 기록](../build/experiments/fpga-research-20260909-094730/performance/cleanup.txt), [R1 executable hash](../build/experiments/fpga-research-20260909-094730/R1/test-executables-before-cleanup.json)

## 15. Git state

초기 `git fetch origin` 후 HEAD와 `origin/fpga/arty-a7-100t`는 모두 `dc4a1a621f63834d64df42ae8c24152747d97971`이었다. 작업 branch는 `exp/pe-local-partial-int20`다. INT20은 commit에 포함되지 않은 working-tree 변경이므로17 tracked file의 diff를 snapshot으로 보존했다. `docs/PE_LOCAL_PARTIAL_INT20.md`도 유지했다. [초기 status](../build/experiments/fpga-research-20260909-094730/source-status.txt), [HEAD](../build/experiments/fpga-research-20260909-094730/source-commit.txt), [remote SHA](../build/experiments/fpga-research-20260909-094730/remote-commit.txt)

실험 변경은 build 아래 독립 source/patch에만 적용했고 이 통합 문서를 새로 추가했다. Production tracked source는 수정하지 않았다. 기존17-file 변경은245 insertions/72 deletions이며 보고서 작성 직전 `git diff --check`는 출력이 없었다. Vivado log/journal 같은 session artifact는 source baseline으로 취급하지 않는다.

```sh
git branch --show-current
git status --short
git diff --check
```

자동 commit/push/merge/rebase/PR 생성, reset/stash/restore는 수행하지 않았다. Board programming은 명시적 사용자 승인 후 해당 SHA256 한 개의 SRAM configuration에만 수행했다. 측정 후 초기191개 source file, frozen baseline191개, board input12개, 원래 route/timing/CDC/DRC report9개의 hash가 모두 보존됐다. [측정 후 보존 검사](../build/experiments/fpga-research-20260909-094730/P1/hardware-measurement-1/post-measurement-preservation.json). 최종 branch/status/diff-check는 실험 root의 `final-git-state.txt`에 기록했다.

[im-arithmetic]: ../build/experiments/fpga-research-20260909-094730/baseline/src/common/Arithmetic.bsv
[im-config]: ../build/experiments/fpga-research-20260909-094730/baseline/src/common/Config.bsv
[im-pe]: ../build/experiments/fpga-research-20260909-094730/baseline/src/array/PE.bsv
[im-skew]: ../build/experiments/fpga-research-20260909-094730/baseline/src/array/InputSkew.bsv
[im-array]: ../build/experiments/fpga-research-20260909-094730/baseline/src/array/SystolicArray.bsv
[im-tiled]: ../build/experiments/fpga-research-20260909-094730/baseline/src/array/SystolicArrayTiled.bsv
[im-engine]: ../build/experiments/fpga-research-20260909-094730/baseline/src/array/SystolicEngine.bsv
[im-accumulator]: ../build/experiments/fpga-research-20260909-094730/baseline/src/accumulator/Accumulator.bsv
[im-work]: ../build/experiments/fpga-research-20260909-094730/baseline/src/control/WorkScheduler.bsv
[im-matmul]: ../build/experiments/fpga-research-20260909-094730/baseline/src/control/MatmulScheduler.bsv
[im-execute]: ../build/experiments/fpga-research-20260909-094730/baseline/src/control/ExecuteController.bsv
[im-core]: ../build/experiments/fpga-research-20260909-094730/baseline/src/core/IM2PCore.bsv
[im-vector]: ../build/experiments/fpga-research-20260909-094730/baseline/src/vector/VectorUnit.bsv
[im-scale]: ../build/experiments/fpga-research-20260909-094730/baseline/src/vector/Scale.bsv
[im-config-script]: ../build/experiments/fpga-research-20260909-094730/baseline/scripts/im2p_config.py
[im-flow]: ../build/experiments/fpga-research-20260909-094730/baseline/scripts/fpga_build.sh
[im-tcl]: ../build/experiments/fpga-research-20260909-094730/baseline/synth/flow/ooc.tcl
[bsc-prelude]: https://github.com/B-Lang-org/bsc/blob/2026.01/src/Libraries/Base1/Prelude.bs#L1565
[gm-pe]: https://github.com/ucb-bar/gemmini/blob/8c3f9923a44a2fe2c7930587be297d6d4f8c09ca/src/main/scala/gemmini/PE.scala
[gm-arithmetic]: https://github.com/ucb-bar/gemmini/blob/8c3f9923a44a2fe2c7930587be297d6d4f8c09ca/src/main/scala/gemmini/Arithmetic.scala
[gm-tile]: https://github.com/ucb-bar/gemmini/blob/8c3f9923a44a2fe2c7930587be297d6d4f8c09ca/src/main/scala/gemmini/Tile.scala
[gm-mesh]: https://github.com/ucb-bar/gemmini/blob/8c3f9923a44a2fe2c7930587be297d6d4f8c09ca/src/main/scala/gemmini/Mesh.scala
[gm-delays]: https://github.com/ucb-bar/gemmini/blob/8c3f9923a44a2fe2c7930587be297d6d4f8c09ca/src/main/scala/gemmini/MeshWithDelays.scala
[gm-accumulator]: https://github.com/ucb-bar/gemmini/blob/8c3f9923a44a2fe2c7930587be297d6d4f8c09ca/src/main/scala/gemmini/AccumulatorMem.scala
[gm-configs]: https://github.com/ucb-bar/gemmini/blob/8c3f9923a44a2fe2c7930587be297d6d4f8c09ca/src/main/scala/gemmini/Configs.scala
[gm-loop]: https://github.com/ucb-bar/gemmini/blob/8c3f9923a44a2fe2c7930587be297d6d4f8c09ca/src/main/scala/gemmini/LoopMatmul.scala
[gm-readme]: https://github.com/ucb-bar/gemmini/blob/8c3f9923a44a2fe2c7930587be297d6d4f8c09ca/README.md
