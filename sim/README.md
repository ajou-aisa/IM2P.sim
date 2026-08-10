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
activation and weight data outside `valid_m`, `valid_n`, and `valid_k`, and
reads only valid output columns. It does not implement whole-tensor tiling.

## K-quant weight scales

For a quantized weight matrix `W: K x J`, scales belong to a K-group and a
weight column:

```text
K-group size = 32

W[ 0:31, 0] -> scale[0, 0]
W[ 0:31, 1] -> scale[0, 1]
W[ 0:31, 2] -> scale[0, 2]

W[32:63, 0] -> scale[1, 0]
W[32:63, 1] -> scale[1, 1]
W[32:63, 2] -> scale[1, 2]
```

One `execute_tile` call receives the current K-group's column-wise scale
vector:

```rust
scales: Some(&column_scales) // &[i8], length = valid_n
```

For `VectorMultiply`, each output uses
`C[i, j] = P[i, j] * column_scales[j]`. Every output row shares the same
scale for column `j`; callers do not provide an `M x N` scale matrix.
`VectorShift` uses the same column mapping and interprets each signed scale as
a shift exponent. `VectorBypass` accepts `scales: None`; `VectorMultiply` and
`VectorShift` require scales.

The Rust simulator pads the `valid_n` values to `DIM` and repeats that same
RTL sideband vector with every activation row. This repeated physical
delivery does not change the software-level K-group/weight-column semantics.
