# FULL FPGA replay v1

Existing Q8_H1 host preparation → existing FULL frontend descriptor → bounded
UART staging → autonomous `mkSynthA8W8D16` → signed32 block results → existing
signed64 provider callback and host floating reconstruction. This is an explicit
`FPGA_REPLAY_V1` validation adapter, not a new llama backend named `HARDWARE`.
Dense EXSIA with RMD disabled is the supported profile. PIPELINE and residual
execution are rejected before starting work.

The results and approval package are in
[FPGA_HOST_FULL_REPLAY.md](../../docs/FPGA_HOST_FULL_REPLAY.md).
[source_provenance.json](source_provenance.json) identifies the exact compiled
board and host snapshots. [baseline.json](baseline.json) records HEAD, initial
dirty root and frozen source hashes. Do not build the dirty root as fixed P3A.

## Reproduce without accessing hardware

Prerequisites used: Linux x86_64, BSC 2026.01, Verilator 5.051, Vivado 2025.2,
CMake/C++20/Rust, existing pinned host and Gemmini include repositories. No new
Python dependencies. Every output directory below must be new. Run from the
IM2P.sim root; choose an absolute `full_run` path.

```bash
full_run="$PWD/build/experiments/full-replay-reproduction"
python3 fpga/full_replay/build.py freeze "$full_run"
mkdir "$full_run/host" "$full_run/params"
git -C ../llama.cpp-gemmini archive 7a74ac29c9c8fa8fbc5d47cf073b8b9266e3e7a9 | tar -x -C "$full_run/host"
git -C ../RISC-V-DynDNN-gemmini-include archive cd2caed8d93e49d3f8873bd6c4e340fcad54b9e0 | tar -x -C "$full_run/params"
python3 "$full_run/source/fpga/full_replay/build.py" bsc "$full_run"
python3 "$full_run/source/fpga/full_replay/build.py" sim "$full_run"
python3 "$full_run/source/fpga/full_replay/build.py" bsc "$full_run" --asserted
python3 "$full_run/source/fpga/full_replay/build.py" sim "$full_run" --asserted

export CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_TEST_DEBUG=0 CARGO_INCREMENTAL=0 CARGO_BUILD_JOBS=2
make -C "$full_run/source" -j2 sim-test-a8-w8-d16 BUILD_DIR="$full_run/simulator"
IM2P_REPO_ROOT="$full_run/source" IM2P_BUILD_DIR="$full_run/simulator" \
IM2P_ACTIVATION_BITS=8 IM2P_WEIGHT_BITS=8 IM2P_DIM=16 \
CARGO_TARGET_DIR="$full_run/simulator/cargo/a8-w8-d16" \
cargo build --locked --offline --manifest-path "$full_run/source/sim/Cargo.toml" --lib

cmake -S "$full_run/source/fpga/full_replay" -B "$full_run/host-build" \
  -DHOST_ROOT="$full_run/host" -DCORE_ROOT="$full_run/source" \
  -DGEMMINI_SW_PATH="$full_run/params" \
  -DSIM_ARCHIVE="$full_run/simulator/cargo/a8-w8-d16/debug/libim2p_sim.a" \
  -DCMAKE_BUILD_TYPE=Release
cmake --build "$full_run/host-build" --target full_replay -j2

full_fixtures=("$full_run/source/fpga/full_replay/fixtures/m16n16k32"
  "$full_run/source/fpga/full_replay/fixtures/m16n16k64"
  "$full_run/source/fpga/full_replay/fixtures/m16n16k96"
  "$full_run/source/fpga/full_replay/fixtures/m32n48k64"
  "$full_run/source/fpga/full_replay/fixtures/m17n19k64-stride"
  "$full_run/source/fpga/full_replay/fixtures/m16n16k32-changed")
python3 "$full_run/source/fpga/full_replay/replay.py" \
  --host "$full_run/host-build/full_replay" \
  --driver "$full_run/production/obj_dir/Vfull_uart_shell" \
  --out "$full_run/production-replay" "${full_fixtures[@]}"
python3 "$full_run/source/fpga/full_replay/replay.py" \
  --host "$full_run/host-build/full_replay" \
  --driver "$full_run/asserted/obj_dir/Vfull_uart_shell" \
  --out "$full_run/asserted-replay" "${full_fixtures[@]}"
python3 "$full_run/source/fpga/full_replay/test_protocol.py" -v
python3 "$full_run/source/fpga/full_replay/faults.py" \
  "$full_run/production/obj_dir/Vfull_uart_shell" "${full_fixtures[0]}" "$full_run/faults"
```

`replay.py` requires passive output-address/scale-block events by default.
`--numerical-only` explicitly omits that proof for an older assertion driver;
it prints `NUMERICAL_REPLAY_PASS`, never `FULL_REPLAY_PASS`.

The existing host preparation can capture a fresh fixture with
`full_replay capture NEW_DIR M N K SEED`. It invokes `gemmini_set_tile_ws`,
`quantize_activation` (EXSIA), `quantize_row_q8_h1_ref`, the existing CPU `MatMul`
reference, then the production simulator. `protocol.py seal NEW_DIR` stores
the five member hashes. Record the new executable/build/source identities
alongside a newly captured fixture; the checked-in manifests describe the
specific executable that produced those fixtures. Never copy an old build
identity to a new capture. `simulate DIR` restores owned buffers without
requantizing; `replay DIR` requires a validated `decoded.bin`; `control DIR`
tests sticky failure and unsupported routes without numerical mocks.

For actual board-top simulation, copy fixtures into a fresh directory, prepare
an itinerary with `protocol.py prepare ... --itinerary PATH`, then run:

```bash
python3 "$full_run/source/fpga/full_replay/build.py" board-sim "$full_run" --itinerary PATH
# After BOARD_TOP_COMPLETE transactions=12 jobs=6:
python3 "$full_run/source/fpga/full_replay/protocol.py" finish COPIED_FIXTURE_DIRS
# Run the host executable's replay mode on each copied fixture.
python3 "$full_run/source/fpga/full_replay/build.py" route "$full_run"
```

`board-sim` uses Xilinx `unisims_ver` for the real MMCM/clock/reset top. It can
take about 25 minutes for the six fixtures. Check the completion marker and
counts, not xsim exit status alone. `route` only creates reports/DCP/bitstream;
it has no hardware-manager commands.

## Portable fixture

All integers are little-endian, packed without alignment padding. `fixture.bin`:
magic `IFX1`; 13 unsigned64 fields `(I,J,K,tile_I,tile_J,tile_K,stripe_rows,
activation_metadata_row_offset,A_byte_stride,f_out_row_stride,
f_out_column_stride,run_id,theta_count)`; signed16 `e_s,rho`; signed32 `sigma`;
`theta_count` signed16 values; `I*A_byte_stride` activation bytes; then
`J*(K/32)` native Q8_H1 blocks, each `(qs[32] signed8,c_b unsigned8,
s_rf IEEE754 float32,R unsigned16)`, 39 bytes. Owned native blocks are 44 bytes.
No pointers or struct padding are serialized. This version accepts metadata
row offset zero, A stride K, M≤32, N≤48, K∈{32,64,96}, f_out row stride≤128
float elements and column stride 1 or 2. Frontend validation checks geometry
and metadata consistency after restoration.

`staging.bin` contains zero-padded A rows of 128 bytes and decoded W rows of
64 bytes. W is obtained using the existing frontend weight reader, at most
16 lanes per call. `expected-raw.bin` contains valid signed32 lanes in
`[K/32][I][J]` order. `expected-fout.bin` includes row/column stride padding
initialized to float32 17. Comparisons are exact bytes. Hashes cover all five
members, including the JSON manifest. Expected files never generate an
execution result.

## Wire protocol

UART: existing 1,000,000 baud 8N1 PHY, 25 MHz core. All multibyte fields little-endian.
Request header 32 bytes = Python struct `<4sBBHQIHHHHI`:

| Offset | Field |
|---:|---|
| 0 | `IFR1` |
| 4,5,6 | version u8=1, operation u8, profile u16=0x0810 |
| 8,16 | run id u64, generation u32 |
| 20,22,24,26 | M,N,K u16; reserved u16=0 |
| 28 | payload byte length u32 |

Operations: 0 capability (returns current generation and capacity 32/48/96),
1 logical RUN, 2 RELEASE, 3 idle ABORT/reset recovery. Control shapes/length
are zero. RUN payload is all staged A/W, followed by CRC32 IEEE over header
and payload. Every control packet also carries CRC32. RUN starts only after
all writes and CRC validation. No packet names a K fragment.

Reply: 96-byte header + payload + CRC32. Offset0 `OFR1`; offset4 version;
5 status; 6 profile; 8 run id; 16 generation; 20 length; 24/26/28 M/N/K;
30 reserved; 32..87 seven u64 counters `(cycles,fragments,works,A_requests,
W_requests,output_writes,output_acks)`; 88..95 reserved. Payload consists of
64-byte vectors in `[K/32][I][ceil(J/16)][16 signed32]` order, padded lanes zero.
Control responses have no payload. Reply status: 0 success, 2 header/profile/
capacity, 3 CRC, 5 owned/busy, 6 identity/generation/release, 7 provider/late
input, 8 framing/sticky timeout, 9 core watchdog.

One invocation owns the staging/result memory until matching RELEASE or
explicit recovery. Generation must equal the previous generation+1; zero
run id and generation exhaustion reject RUN. Button reset preserves the
generation counter; reconfiguration begins a new session. Stale responses
fail before host output commit. UART errors poison transport. No automatic
retry, simulator fallback or programming recovery exists.

Input interbyte watchdog: 262,144 core cycles (10.48576 ms). Core watchdog:
16,777,216 cycles (671.08864 ms). Host transaction deadline defaults to 5 s
wall time. FPGA executes freely; host select/poll calls are not RTL cycles.
ABORT packets during RUN and forced provider ACK backpressure are unsupported
in v1. Button reset is the available running-core reset mechanism.

## Approved physical measurements only

`measure.py --help` documents required arguments. Do not open a physical UART
or program a device until the user approves the new full 64-character bitstream
hash and fixture schedule. The harness requires that approved hash and an
independent SRAM programming/verification log. It never programs, writes flash,
or restores an older image. Hash argument alone does not prove the live image.

The measurement starts with a prequantized owned fixture. It reports pack,
transfer/execute/receive, reconstruction/commit and same-fixture simulator
wall time separately, including process/file overhead. Quantization/capture
cost is excluded and must be reported separately for an eventual model path.
One warmup plus at least five measurements per fixture; stop on first error.
