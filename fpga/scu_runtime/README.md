# Standard llama FPGA_UART source recipe

> This document preserves the historical source02/IFR3 overlay recipe. The
> working branches now own the bounded IFR4/main_external implementation and
> ordinary build, without applying this overlay. See the
> [current contract and build](../../docs/HOST_STRIPE_CONTRACT.md#ordinary-current-source-build).

This host-only overlay adds ordinary llama build/runtime integration to the sealed
SCU source selection. It does not change the numerical core, IFR3 packets, or
board image. Source preparation does not open UART/JTAG or program hardware.

## Source selection

```sh
python3 fpga/scu_runtime/assemble.py assemble \
  --frozen /path/to/verified/candidate-01 \
  --overlay fpga/scu_runtime/host-overlay \
  --out /large/filesystem/new-llama-fpga-source
```

The destination must not exist. The tool validates all 1,746 sealed inputs,
materializes `source/`, `host/`, and `params/`, then applies only the host overlay.
The ordinary `host/CMakeLists.txt` owns its frontend and adapter dependencies.
`provenance/host-overlay/host-before/` preserves every replaced baseline host blob.
`after.json` records each selected host hash and origin. `selected-files.json`
records the complete derived source/helper selection before any build. The original
`integration-sha256.json` remains historical; it is not the modified host manifest.

`build-x86.sh` includes the user's existing dirty defaults (WS/IM2P_SIM, debug and
cycle logging enabled, RMD backend WS). `build-options-baseline.json` records those
input hashes separately. `user-build-x86-before.patch` preserves the six existing
script edits against the pinned host. They are not folded into the old board manifest.

To apply the overlay to a separately materialized fresh host tree, use
`apply-overlay --frozen ... --overlay ... --host ... --evidence NEW`. This refuses
an altered selected host, an existing evidence directory, or the original sealed
candidate. Do not apply it twice.

## Fresh native archive and ordinary targets

Use Linux x86_64 or native Linux AArch64 with compatible C++20, Python 3.11 or
newer (the packaged negative-test helper uses `hashlib.file_digest`; the cache
also uses `zip(..., strict=True)`), CMake, Make, BSC, Verilator,
Rust/Cargo capable of the version-4 lock file, and the dependencies in
`source/sim/Cargo.lock`. The recorded x86 toolchain is GCC 11.4, CMake 3.22.1,
Python 3.11.6, Rust/Cargo 1.91.1, BSC 2026.01, and Verilator 5.051. Those versions
are historical/actual build evidence, not a claim that Nano currently has them.
The selected source archive does not contain tool installations or compiled
archives. A cold machine also needs the three registry crates pinned in
`source/sim/Cargo.lock`; installing Rust alone does not provide them. Populate an
isolated Cargo cache with `cargo fetch --locked` before the offline build, or
configure an independently verified vendor directory. The previous migration
bundle's verified Cargo vendor directory is one optional source; it is not a
required historical binary/archive input. No fetch or install runs automatically
in the script.

```sh
SEL=/large/filesystem/new-llama-fpga-source
WORK=/large/filesystem/new-native-work
# WORK is a new directory, outside the source package; retain failed attempts.
mkdir "$WORK"
mkdir -p "$WORK/tmp" "$WORK/cache/cargo"
export TMPDIR="$WORK/tmp" TMP="$WORK/tmp" TEMP="$WORK/tmp"
export XDG_CACHE_HOME="$WORK/cache" PYTHONPYCACHEPREFIX="$WORK/cache/python"
export CARGO_HOME="$WORK/cache/cargo"
# Dependency acquisition only; omit when a verified vendor/cache is configured.
cargo fetch --locked --manifest-path "$SEL/source/sim/Cargo.toml"
export CARGO_NET_OFFLINE=true
make -C "$SEL/source" -j2 gemmini-frontend-real-lib \
  BUILD_DIR="$WORK/native" \
  GEMMINI_ROOT="$SEL/host" GEMMINI_PARAMS_ROOT="$SEL/params/include" \
  IM2P_ACTIVATION_BITS=8 IM2P_WEIGHT_BITS=8 IM2P_DIM=16 \
  GEMMINI_FRONTEND_ACTIVATION_BITS=8 GEMMINI_FRONTEND_WEIGHT_BITS=8 \
  GEMMINI_FRONTEND_DIM=16 GEMMINI_FRONTEND_BLOCK_SIZE=32
MANIFEST=$(readlink -f "$WORK/native/selected/a8-w8-d16/current/real-lib.json")
BUILD_DIR="$WORK/llama-x86-fpga" BUILD_JOBS=2 "$SEL/host/build-x86.sh" \
  -DGGML_GEMMINI_EXECUTION_BACKEND=FPGA_UART \
  -DIM2P_SIM_ROOT="$SEL/source" \
  -DGGML_GEMMINI_FPGA_SIM_MANIFEST:FILEPATH="$MANIFEST" \
  -DGEMMINI_SW_PATH="$SEL/params" \
  -DGGML_BACKEND_DL=ON \
  -DGGML_NATIVE=OFF
```

The existing cache recipe receives the absolute `BUILD_DIR`; its BSC, Verilator,
Cargo target, generated headers, temporary generations, and published archives
stay beneath `WORK/native`. CMake outputs stay beneath `WORK/llama-x86-fpga`.
Tool installations are read-only dependencies, not output directories.

Do not run the historical `fpga/scu_block_scale/build.py native` or the migration
`native_build.py --build` against this overlaid source tree. Those wrappers verify
the old whole-host manifest and correctly reject the changed host. The copied
migration helper is used only by `assemble.py` to validate/materialize the original
sealed source. The direct Make command above fingerprints and builds the selected
new host inputs; ordinary host CMake then owns frontend/adapter linkage.

On native Linux ARM64 use the same fresh archive recipe and `build-arm64.sh` with
a new `WORK` and `BUILD_DIR`. Do not carry x86 `.a`, `.o`, or executables into that build.
ARM64 build/runtime is **NOT RUN** until executed and recorded on that target.
For FPGA builds, native Linux ARM scripts require `uname -m` of `aarch64` or
`arm64`; the x86 script requires `x86_64`. Help/dry-run can be inspected on either
host. Cross toolchains use direct CMake with explicit target artifacts; native scripts
reject toolchain overrides before provisioning. No processor spoofing or ARM
`try_run` on x86 is allowed.

Options resolve before provisioning: explicit `-DNAME[:TYPE]=value`, environment,
existing CMake cache, then script defaults. A same-level `IM2P_DIM`/canonical DIM
conflict is rejected. An identity-changing cache requires a fresh `BUILD_DIR`.
Explicit FPGA conflicts are rejected; otherwise the FPGA profile supplies
WS/INT/EXSIA/A8/W8/DIM16/block32/RMD OFF. `GGML_GEMMINI_DEQUANT_FP_TEST` must be OFF.
`--dry-run` prints the effective configuration and does no provisioning or build.
Presets, `-C`, `-U`, `-S`, `-B`, `-G`, and native-script toolchain overrides require
direct CMake. Environment and CLI paths with spaces remain single arguments.

Mode remains separate: `-DGGML_GEMMINI_DEFAULT_MATMUL_MODE=FULL` or
`STRIPE_PIPELINE`. Existing platform-specific mode/override defaults remain in
place. The existing runtime override is `GEMMINI_MATMUL_MODE=FULL` or
`GEMMINI_MATMUL_MODE=STRIPE_PIPELINE`, subject to
`GGML_GEMMINI_ALLOW_RUNTIME_MATMUL_OVERRIDE`. Selecting FPGA neither opens a device
nor scans USB devices. Runtime needs
explicit device and strict hardware/RTL/FULL-reference identity; see the final
integration report for tested runtime commands, model capacity, and dispatch scope.

## Source transfer

Seal the assembled source tree with its source selection unchanged. The build
recipe above keeps generated outputs in separate `WORK`; never add them to the
source package. Sealing rejects new files or changed source hashes.

```sh
python3 fpga/scu_runtime/assemble.py seal \
  --root "$SEL" --archive /large/filesystem/llama-fpga-source.tar.gz
python3 fpga/scu_runtime/assemble.py extract \
  --archive /large/filesystem/llama-fpga-source.tar.gz \
  --out /large/filesystem/new-extraction
python3 /large/filesystem/new-extraction/llama-fpga-source/tools/scu_runtime/assemble.py \
  verify --root /large/filesystem/new-extraction/llama-fpga-source
```

New runtime test tools are retained under `source/fpga/scu_runtime`, matching their
existing source-relative imports. The reviewed overlay is also retained there;
`host/` is the ordinary ready-to-configure source tree. For example, after source
extraction, this device-free script check uses no experiment path:

```sh
python3 "$SEL/source/fpga/scu_runtime/test_build_options.py" \
  --out "$WORK/options-tests"
```

`build_tests.py`, `test_cmake_contract.py`, and `test_standard_fail_closed.py` take
explicit selected source/build/manifest/reference paths. Run them after a native
build using their `--help` arguments. `run_rtl.py` additionally needs a separately
built production RTL simulator; it is not a physical-board runner. Its executable
is not copied into the source package.

The transfer result includes archive and extracted-file manifest hashes, bytes,
and executable modes. Extraction rejects links, device files, duplicates, unsafe
paths, and existing destinations. The source package does not contain a bitstream,
old x86 archive, model weights from untracked directories, or build caches. Frozen
tracked vocabulary/test fixtures remain part of the exact source selection.
