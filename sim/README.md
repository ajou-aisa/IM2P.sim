# IM2P RTL simulator

This crate drives BSC-generated INT8 RTL through Verilator:

```text
BSV -> Verilog -> Verilator C++ -> C ABI -> Rust
```

Build and run each configuration from repository root:

```bash
make verilator-int8x16
make sim-test-int8x16
make verilator-int8x32
make sim-test-int8x32
```

`IM2P_DIM` selects one generated configuration per Cargo build. The simulator
counts one rising RTL clock edge as one cycle. `TileStats` reports weight-load
cycles, compute cycles, total cycles, useful MACs, useful operations, MACs per
cycle, operations per cycle, and array utilization. No host frequency is
assumed.

`execute_tile` accepts one already-selected hardware tile. It zero-pads
activation, weight, and scale lanes outside `valid_m`, `valid_n`, and
`valid_k`, and reads only valid output columns. It does not implement
whole-tensor tiling.
