# Native H1/HP1 full-K output

The FPGA_UART adapter selects `scu_final_integer` for native H1 and HP1 weights.
H1 uses op4 (unsigned multiply); HP1 uses op5 (left shift). Both return one
domain2 integer for each output coordinate after all K fragments of that output
work have committed. The existing frontend performs the final shared-channel
and activation-scale reconstruction in binary64 before converting to float.

`IM2P_FPGA_NUMERICAL_CONTRACT=main_external` explicitly selects the legacy
diagnostic block-output contract. The transport name does not select that
contract. The llama caller retains the source weight type when Q8_0 is
reprocessed into H1 storage, so Q8_0 keeps its existing bounded External route.
Channel-scale Bypass behavior is unchanged. Execution errors remain fatal to
the invocation; there is no numerical fallback.

No production BSV or UART shell change is required. `VectorExternal` alone
enables block-boundary reset and intermediate writeback. Current and prepared
work both use this rule. Native SCU work replaces the accumulator on its first
fragment, including a zero contribution, then saturates each scaled fragment
and each accumulator addition separately. Refills only supply A/W/S data.

IFR4 START carries the vector operation. Output domain is a host descriptor
contract, not a wire echo. The existing transport verifies the single final
plane, lane count, ordering, tags, padding, output coverage, write/ACK counts,
stripe completion, and owned-session RELEASE. Its logs distinguish valid
scalars, padding, records, acknowledgements, and protocol bytes. Constructor
CAP bytes are excluded from per-invocation byte counters.

Native SCU with RMD is rejected with
`SCU residual radix and merge contract is not enabled`. The existing frontend
guard remains, and the FPGA adapter rejects this combination before opening a
device in both FULL and PIPELINE. The residual Bypass/domain0 radix/float merge
has not been redefined. Explicit legacy External residual execution remains
available under its existing contract. Dense verification uses an explicit
`-DGGML_GEMMINI_ENABLE_RMD=OFF`; build defaults are unchanged.

## Verification entry points

- `tests/scu_block_scale/golden.py` generates independent fragment-saturation
  and reconstruction expectations. `run_frontend.py --host-build ...
  --rtl-plugin ...` tests the external-only frontend against production RTL.
- `fpga/scu_block_scale/bounded_driver.cpp` tests the production provider,
  refills, tails, saturation, and consecutive invocations without reset.
- `test_window_transport.py` tests codec/ownership rejection with a PTY mock.
  These results are not FPGA numerical evidence.
- `test_window_packets.py` and `window_uart_driver.cpp` use real UART/core RTL.
  Passive accepted-edge observations check replace/accumulate, lane0 clamp,
  final writeback, and absence of intermediate writeback. Counter snapshots
  are cumulative; per-case verification uses differences.
- `fpga/scu_runtime/tests/graph_dispatch.cpp` uses the ordinary FPGA adapter
  and independent H1/HP1 integer/float oracles. `HP1-edges` and `H1-edges` cover
  zero metadata, changing blocks, large factors, and saturation. The optional
  `IM2P_GRAPH_CAPTURE_DIR` saves inputs and independent golden files.
- `model_observer.cpp` observes actual model invocations. Input-only capture
  exits 95 before numerical execution and reports `NOT_RUN`. First-only mode
  stops after the first checked RELEASE/commit. It does not establish complete
  model evaluation or the caller's later graph-completion counter.

Physical execution requires a separately approved finite package. The graph
test's default device restriction remains PTY-only; physical execution requires
the explicit `--physical-device /dev/serial/by-id/...` argument matching the
selected `uart4:` device. Preparing or compiling this option does not open a
device or authorize a workload.

The September 2026 evidence package is
`/mnt/fpga-build/im2p/scu-fullk-common-20260914T151503Z`. Its report and machine
summary separate software readiness, retained bitstream provenance, and
physical execution. `SCU_FULLK_COMMON_READY` is not a physical PASS marker.
