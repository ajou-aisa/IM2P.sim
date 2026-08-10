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
cycles, scale-load cycles, compute cycles, total cycles, useful MACs, useful
operations, MACs per cycle, operations per cycle, and array utilization. No
host frequency is assumed.

`execute_tile` accepts one already-selected hardware tile. It zero-pads
activation and weight data outside `valid_m`, `valid_n`, and `valid_k`, and
reads only valid output columns. It does not implement whole-tensor tiling.

## Block-wise weight scales

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

Each `execute_tile` call receives the complete block-major scale table for the
logical K range:

```rust
scales: Some(&scale_table), // scale[b * valid_n + j]
k_start: 16,               // global K origin of this hardware partial
total_k: 64,
block_size: 32,
```

Scale length is `ceil(total_k / block_size) * valid_n`. Rust pads every table
row from `valid_n` to `DIM` and preloads all rows into the single RTL
`IM2PCore`. Rust does not select the current block's scale vector.

At execution start, RTL computes `b = floor(k_start / block_size)`, rejects a
hardware partial that crosses a block boundary, and latches `scale[b,:]`.
Every SystolicArray column output is sent directly to VectorUnit. For
`VectorMultiply`, `P^(b)[i,j]` becomes `P^(b)[i,j] * scale[b,j]`.
`VectorShift` applies the signed shift to each hardware partial independently.
Accumulator then overwrites or adds the transformed contribution.

Executions cannot overlap: the previous wavefront, VectorUnit work, and
Accumulator commits must drain before the next start. Therefore the latched
scale stays aligned with all staggered column outputs from that execution.
Every hardware fragment in the same block reselects the same table row. Selection changes to `scale[b+1,:]` exactly at the next block boundary.

`VectorBypass` accepts `scales: None`; `VectorMultiply` and `VectorShift`
require the complete scale table. The synthesized table supports at most
eight scale blocks per request. Any hardware partial spanning two blocks is
rejected.

Bypass, multiply, and shift all run on the same `IM2PCore` instance; only
`TileRequest.vector_op` changes. A stale scale table has no numerical effect on
a later `VectorBypass` execution.
