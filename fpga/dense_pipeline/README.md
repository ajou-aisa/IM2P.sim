# Dense FULL / live PIPELINE integration

검증 범위는 A8/W8/DIM16, native Q8_H1, EXSIA activation, RMD OFF다.
`FPGA_UART`는 새 명시적 host selector다. HARDWARE/IM2P_SIM의 뜻을 바꾸지 않는다.
모델 실행 증거와 residual/PIPELINE 전체 모델 성능은 포함하지 않는다.

실제 결과·식별값: [통합 보고서](../../docs/FPGA_DENSE_PIPELINE_INTEGRATION.md).
계약: [CONTRACT.md](CONTRACT.md), wire: [PROTOCOL.md](PROTOCOL.md), geometry: [GEOMETRY.md](GEOMETRY.md).

## 재현

기존 FULL의 `fixed-core.patch` 및 freeze helper를 재사용한다. Host/include pin은 build.py에 명시된다.
새 output 경로만 사용한다. Freeze는 `baseline.json`의 고정 commit archive→fixed core patch→명시적 frontend/provider/shell→
host integration patch→producer observation patch 순서다. Root와 frozen source를 암묵적으로 혼합하지 않는다.

```sh
python3 fpga/dense_pipeline/build.py freeze NEW \
  --host-repo PINNED_HOST_CHECKOUT --params-repo PINNED_INCLUDE_CHECKOUT
python3 fpga/dense_pipeline/build.py native-sim NEW --jobs 2
python3 fpga/dense_pipeline/build.py host NEW --jobs 2
python3 fpga/dense_pipeline/build.py verify NEW
```

`deployment-lock.json`은 base/host/include pin과 검증된 수치·host 입력 SHA를 고정한다.
`native-sim`은 기존 Makefile의 BSC/Verilator 생성과 Cargo 빌드를 사용한다. Board bitstream을
생성하지 않는다. Native object/cache는 `NEW/native-$(uname -m)`에 둔다. 기존 `--sim-archive`는
역사 재현용이며 Nano 안내에서는 사용하지 않는다. x86 archive를 ARM64에 링크하지 않는다.
Git 소스와 별도 bitstream의 전달·ARM64 빌드·향후 SRAM 절차는
[Nano Quickstart](../../docs/JETSON_NANO_QUICKSTART.md)를 기준으로 한다.

`bsc`, `sim`, `board-sim`, `route`는 기존 RTL 개발용 stage로 남아 있다. 배포 준비나 Nano
첫 실행에는 board synthesis/route가 필요하지 않다. 이번 배포에서는 실행하지 않았다.

`host-build/dense_host_dispatch`는 실제 ggml matmul graph를 실행한다. `run ... FULL|STRIPE_PIPELINE`은
완료 후 기존 CPU numerical reference를 비교한다. `benchmark ...`는 warm-up에서 reference를 만들고
측정 5회에는 저장된 raw/f_out exact 비교만 한다. Host graph prepare/get 및 RELEASE를 포함한다.
FPGA로 선택한 matmul에서 simulator creates/executes/stream begins가 발생하면 실패한다.

`host-build/persistent_replay`는 portable fixture를 복원한다. 명시적 backend는 다음과 같다.

| backend suffix | 입력 서비스 |
|---|---|
| 없음 | Prequantized FULL |
| `-pipeline` | Prequantized deterministic stripe replay |
| `-live` | 실제 기존 activation quantization을 포함한 FULL |
| `-live-pipeline` | 기존 producer의 즉시 post-fold sink를 사용하는 PIPELINE |

각 suffix 앞에는 `uart` 또는 `simulator`를 지정한다. Simulator는 독립 실행 backend다.
Live 입력 `input-f32.bin`은 capture가 기존 float input을 little-endian float32로 저장한 것이다.
기존 IFX1 payload는 그대로이며, 추가 파일도 최종 fixture/tool manifest에 포함한다.
Live PIPELINE은 accepted A에 별도 owned backing을 사용해 producer 실패의 zero-fill로부터 격리한다.

## 보드 접근 없는 검증

```sh
python3 fpga/dense_pipeline/run_pty.py --rtl NEW/production/obj_dir/Vdense_uart_shell \
  --host NEW/host-build/dense_host_dispatch --out NEW_TEST run 321 48 64 7 1 STRIPE_PIPELINE
python3 fpga/dense_pipeline/run_pty.py --rtl NEW/production/obj_dir/Vdense_uart_shell \
  --host NEW/host-build/persistent_replay --out NEW_REPLAY uart-live-pipeline @PTY 1 FIXTURE
python3 fpga/dense_pipeline/test_measure.py
```

PTY 모델은 실제 UART shell/provider/core RTL을 실행한다. Mock protocol tests와 numerical RTL test는 구분한다.
MMCM/board wrapper까지 포함한 vendor simulation은 `board-sim` stage로 별도 실행한다.
Assertion-enabled 결과는 production cycle/성능을 대신하지 않는다.

## 측정과 승인

`measure.py PLAN.json`은 전체 hash/profile을 확인하며 device를 열지 않는다.
이동한 checkout에서는 과거 experiment 로그 없이 plan을 생성한다.

```sh
python3 fpga/dense_pipeline/prepare_measurement.py --portable \
  --host NEW --bitstream RECEIVED_BIT --out NEW_PLAN.json
python3 fpga/dense_pipeline/measure.py NEW_PLAN.json
```

Plan 생성은 실행 승인이 아니다. 기존 네 조건/54회 계획을 봉인할 뿐 자동 실행하지 않는다.
`--run simulator --out NEW`는 한 process에서 warm-up 1회+측정 5회를 실행한다.
Hash 순회는 실험 전후에만 한다. 모든 실행의 CRC/id/count/raw/f_out/padding 검사는 유지한다.
Service와 RELEASE/Run 회수까지의 sustained timer, 초기화를 구분한다. 사후 비용 차감은 하지 않는다.

`--run board`는 별도 승인 후에만 사용한다. 이 도구는 programming/reset/retry를 하지 않는다.
SRAM programming, CAP/device open 및 실제 job 모두 새 사용자 승인이 필요하다.
기존 승인 bitstream으로 자동 복구하거나 flash에 쓰지 않는다.
