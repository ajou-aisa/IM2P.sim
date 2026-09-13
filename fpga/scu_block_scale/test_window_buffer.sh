#!/usr/bin/env bash
# Standalone production WindowBuffer RTL boundary/ownership regression.
set -euo pipefail
[[ $# == 1 ]] || { echo "usage: $0 <fresh-output-directory>" >&2; exit 2; }
root=$(cd "$(dirname "$0")/../.." && pwd)
out=$1
[[ ! -e "$out" ]] || { echo "output already exists: $out" >&2; exit 2; }
mkdir -p "$out"
out=$(cd "$out" && pwd)
mkdir "$out/source" "$out/rtl" "$out/bsc" "$out/info" "$out/tmp"
export TMPDIR="$out/tmp" TMP="$out/tmp" TEMP="$out/tmp"
bsc=$(command -v "${BSC:-bsc}")
verilator=$(command -v "${VERILATOR:-verilator}")
"$bsc" -print-flags > "$out/bsc-flags.txt"
primitive_dir=${BSC_VERILOG:-$(awk '$1 == "-i" {print $2; exit}' "$out/bsc-flags.txt")/Verilog}
cp "$root/synth/WindowBuffer.bsv" "$out/source/"
cp "$root/fpga/scu_block_scale/window_buffer_driver.cpp" "$out/source/"
cp "$0" "$out/source/"
cat > "$out/source/WindowBufferTest.bsv" <<'BSV'
package WindowBufferTest;
import WindowBuffer::*;
(* synthesize *) module mkWindowBufferTest(WindowBufferIfc);
    let buffer <- mkWindowBuffer(256);
    return buffer;
endmodule
endpackage
BSV
run() {
    printf '%q ' "$@" >> "$out/commands.sh"
    printf '\n' >> "$out/commands.sh"
    "$@"
}
cd "$out"
run "$bsc" -u -verilog -p +:source -bdir bsc -info-dir info -vdir rtl \
    -g mkWindowBufferTest source/WindowBufferTest.bsv > bsc.stdout 2> bsc.stderr
cp "$primitive_dir/BRAM1.v" rtl/
run "$verilator" --cc --exe --build -j 2 --assert -Wno-fatal \
    --top-module mkWindowBufferTest --Mdir "$out/obj_dir" -CFLAGS '-O2 -std=c++20' \
    "$out/rtl/mkWindowBufferTest.v" "$out/rtl/BRAM1.v" \
    "$out/source/window_buffer_driver.cpp" > verilator.stdout 2> verilator.stderr
sha256sum source/* rtl/* obj_dir/VmkWindowBufferTest > identity.sha256
run "$out/obj_dir/VmkWindowBufferTest" > test.stdout 2> test.stderr
sha256sum --check identity.sha256 > preservation.stdout
cat test.stdout
