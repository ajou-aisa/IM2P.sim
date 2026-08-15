# RTL 통합 테스트

Cargo는 모든 `sim/tests/*.rs` 파일을 자동으로 검색한다. 테스트 파일을 추가해도
집계기나 레지스트리를 수정할 필요가 없다.

| 파일 | 테스트 | 담당 범위 |
|---|---:|---|
| `rtl_basic.rs` | 6 | Bypass, 부호/0 데이터, 전체 타일, M/N/K 끝부분 |
| `rtl_scaling.rs` | 6 | Multiply, Shift, 열 스케일, M 행 공유 |
| `rtl_k_blocks.rs` | 5 | B8/B16/B32/B64, 9/17/128 블록, 경계 |
| `rtl_scale_fetch.rs` | 8 | 요청 시 로드, 현재 히트, context/reset, J stride/offset |
| `rtl_prefetch.rs` | 4 | 다음 prefetch/hit, 중복 없음, 마지막 블록 |
| `rtl_runtime.rs` | 2 | 동일 Core에서 runtime operation 전환 |
| `rtl_cycle_accounting.rs` | 2 | 정확한 edge/eval/pulse 의미와 동시 A/W/S/C response |
| `rtl_random.rs` | 3 | 결정론적 다중 block Multiply/Shift |
| `rtl_validation.rs` | 14 | 범위, layout, C 측 scale 검증, buffer, response identity |
| `rtl_stats.rs` | 4 | cycle, fetch, work, utilization 불변 조건 |
| `rtl_full_matmul.rs` | 7 | 초과 크기 전체 matrix, 끝부분, stride, CPU golden |
| `rtl_writeback.rs` | 6 | 유효 영역 write와 guard/padding 보존 |
| `rtl_work_scheduler.rs` | 2 | 실제 RTL 다중 I/J/K traversal과 simulator 재사용 |
| `rtl_memory_provider.rs` | 2 | address 기반 stride A/W/C와 channel counter |
| `rtl_weight_preload.rs` | 2 | inactive bank preload overlap과 bank accounting |
| `rtl_work_stats.rs` | 1 | RTL wait/compute/drain/preload/overlap counter |
| `rtl_async_stripes.rs` | 7 | publish gating, FIFO pressure, 준비 상태, 오류, 동등성 |
| `rtl_stripe_completion.rs` | 2 | writeback barrier와 순서가 보장된 completion context |
| `rtl_async_output_tiles.rs` | 1 | 비동기 N-tile output column offset |
| `rtl_stripe_lookahead.rs` | 5 | publish 기반 A/W/S 준비, 지연/padding, partial stripe, resident resource reuse |

`rtl_stripe_lookahead.rs`는 다른 `sim/tests/*.rs` integration test와 함께 자동
검색된다. 이 사례들은 실제 Verilated Core를 구동하며 publish-to-prepare timing
signal, padding된 A/W/C layout에서의 지연 publish, 불필요한 재요청 없는 partial
weight 준비, scale miss, 단일 immediate lookahead와 resident resource reuse
경로를 다룬다. 이 문서는 해당 사례를 별도 registry로 취급하거나 runtime 결과를
보고하지 않는다.

`c_api_smoke.c`는 `make c-api-test`로 strict C11로 compile되고 Rust static
library에 link되며 blocking API와 cooperative API 모두를 통해 실행된다. 표에는
Cargo가 자동 검색하는 실제 RTL Rust test file 20개와 test 89개가 포함된다. C API
process 검사는 추가 external surface 검증 항목이다.

`common/`에는 shape/fragment type, scale row builder, 결정론적 fixture, 독립 CPU
golden arithmetic, runner, assertion이 포함된다.

## 산술 테스트 추가

`KBlockScaleMatrix::from_fn`과 `run_case`를 사용하고 반환된
`output`/`expected`를 비교한다.

```rust
#[test]
fn my_k_block_case() -> Result<(), SimError> {
    let scales = KBlockScaleMatrix::from_fn(256, 32, 4, |block, column| {
        ((block + column) % 5) as i8 - 2
    });
    let result = run_case(&mut Im2pSimulator::new()?, Case {
        shape,
        activations: &activations,
        weights: &weights,
        scales: Some(&scales),
        column_offset: 0,
        valid_columns: 4,
        context: 1,
        operation: VectorOp::Multiply,
    })?;
    assert_eq!(result.output, result.expected);
    Ok(())
}
```

Block arithmetic 사례는 `rtl_k_blocks.rs`에 추가한다. Request/cache 사례는
`rtl_scale_fetch.rs` 또는 `rtl_prefetch.rs`에 추가한다. Validation 전용 사례는
`rtl_validation.rs`에 둔다.

## Scheduler/provider coverage 추가

High-level integration test는 Verilated `IM2PCore`를 실행해야 하며,
`MatmulScheduler` 또는 `WorkScheduler`의 Rust mirror를 만들면 안 된다.

- Full matrix 사례는 검증된 `MatrixView`, `MatrixViewMut`, `MatmulWork` 값을 구성한
  뒤 `execute_matmul`을 한 번 호출한다.
- Stripe 사례는 `begin_striped_matmul`을 호출하고 CPU completion row만
  publish하며, pending A/C event를 처리하고 `progress`로 RTL logical cycle을
  진행한 뒤 순서가 보장된 completion event를 소비한다.
- Test는 event/state handshake를 사용한다. OS thread, async runtime, sleep,
  wall-clock deadline, Rust I/J/K tiling loop는 금지한다.
- `npu_ready()`는 RTL publish FIFO capacity를 의미한다. `host_available()`는
  publish된 host data가 완료되지 않은 RTL work와 계속 연결되어 있음을 의미한다.
- Output completion은 일치하는 C request tag가 확인된 뒤에만 관찰할 수 있다.

DIM 간 변경에는 생성된 두 차원을 모두 실행한다.

```bash
make sim-test-int8x16
make sim-test-int8x32
```

상위 simulator 계약은 [simulator 사용법](../README.md), 전체 검증 범위는
[검증 가이드](../../docs/VERIFICATION.md)에서 확인한다.
