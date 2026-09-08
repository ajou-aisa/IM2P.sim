#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/fpga_build.sh [options]
  --mode rtl|area|timing|route   Default: area; timing/route use CLK at 10 ns
  --bits 4|8|16 --dim 16|32|64  Default: A8/W8 DIM16
  --hierarchy rebuilt|none      Default: rebuilt; none preserves RTL hierarchy
  --diagnostic core|fifo        Default: core; FIFO is A8 DIM16 only
  --out NEW_DIRECTORY           Default: unique directory under build/fpga
Environment: BSC, BSC_VERILOG, VIVADO, VERILATOR, PYTHON (executable paths).
Part: xc7a100tcsg324-1. Route allowed only for resource-fitting A8 DIM16 core.
Every run starts fresh. No checkpoint/report reuse, bitstream, or programming.
EOF
}

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
mode=area bits=8 dim=16 hierarchy=rebuilt diagnostic=core out=
original_args=("$@")
while (($#)); do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --mode|--bits|--dim|--hierarchy|--diagnostic|--out)
            (($# >= 2)) || fail "missing value for $1"
            key=${1#--}; printf -v "$key" '%s' "$2"; shift 2 ;;
        *) fail "unknown option: $1" ;;
    esac
done
case "$mode" in rtl|area|timing|route) ;; *) fail 'invalid mode' ;; esac
case "$bits" in 4|8|16) ;; *) fail 'bits must be 4, 8, or 16' ;; esac
case "$dim" in 16|32|64) ;; *) fail 'dim must be 16, 32, or 64' ;; esac
case "$hierarchy" in rebuilt|none) ;; *) fail 'invalid hierarchy' ;; esac
case "$diagnostic" in core|fifo) ;; *) fail 'invalid diagnostic' ;; esac
if [[ "$diagnostic" == fifo && ("$bits" != 8 || "$dim" != 16) ]]; then
    fail 'FIFO diagnostic matches A8 DIM16 only'
fi
if [[ "$mode" == route && ("$bits" != 8 || "$dim" != 16 || "$diagnostic" != core) ]]; then
    fail 'route requires A8 DIM16 core'
fi
bsc=$(command -v "${BSC:-bsc}") || fail 'BSC executable unavailable'
verilator=$(command -v "${VERILATOR:-verilator}") || fail 'Verilator executable unavailable (RTL hierarchy check)'
python=$(command -v "${PYTHON:-python3}") || fail 'Python executable unavailable (configuration consistency check)'
vivado=
if [[ "$mode" != rtl ]]; then
    vivado=$(command -v "${VIVADO:-vivado}") || fail 'Vivado unavailable; use --mode rtl for generation only'
fi
if command -v sha256sum >/dev/null; then hash=(sha256sum); else hash=(shasum -a 256); fi
if [[ -z "$out" ]]; then
    mkdir -p "$root/build/fpga"
    out=$(mktemp -d "$root/build/fpga/a${bits}-w${bits}-d${dim}-${diagnostic}-${mode}-${hierarchy}-XXXXXX")
else
    [[ ! -e "$out" ]] || fail "output already exists: $out"
    mkdir -p "$(dirname "$out")"
    out=$(cd "$(dirname "$out")" && pwd)/$(basename "$out")
    case "$out" in
        "$root/src/"*|"$root/synth/"*|"$root/scripts/"*|"$root/config/"*|"$root/sim/"*)
            fail 'output must be outside source directories' ;;
    esac
    mkdir "$out"
fi
out=$(cd "$out" && pwd)
trap 'status=$?; if ((status)); then printf "FAILED exit=%s\n" "$status" > "$out/status.txt"; fi' EXIT
mkdir -p "$out"/{source,rtl,bsc,info,primitives,reports,checkpoints}
run() {
    printf 'cd %q && ' "$PWD" >> "$out/commands.sh"
    printf '%q ' "$@" >> "$out/commands.sh"; printf '\n' >> "$out/commands.sh"
    "$@"
}
printf '%q ' "$0" "${original_args[@]}" > "$out/invocation.sh"; printf '\n' >> "$out/invocation.sh"
cd "$root"
git rev-parse HEAD > "$out/source-commit.txt"
git status --porcelain=v1 --untracked-files=all > "$out/source-status.txt"
git diff --binary HEAD > "$out/source-diff.patch"
# Compile the recorded snapshot, so later concurrent edits cannot change the run.
for path in src synth scripts config Makefile; do
    [[ ! -e "$path" ]] || cp -R "$path" "$out/source/"
done
mkdir -p "$out/source/sim/ffi"
cp sim/ffi/im2p_config.h "$out/source/sim/ffi/"
run "$bsc" -v > "$out/bsc-version.txt" 2>&1
run "$bsc" -print-flags > "$out/bsc-flags.txt" 2>&1
primitive_dir=${BSC_VERILOG:-$(awk '$1 == "-i" {print $2; exit}' "$out/bsc-flags.txt")/Verilog}
[[ -f "$primitive_dir/FIFO2.v" ]] || fail "missing BSC primitive directory: $primitive_dir"
primitive_dir=$(cd "$primitive_dir" && pwd)
printf '%s\n' "$primitive_dir" > "$out/bsc-primitive-path.txt"
package=SynthA${bits}W${bits}D${dim}
[[ "$diagnostic" != fifo ]] || package=SynthActivationFIFO
top=mk${package}
printf 'profile=a%s-w%s-d%s\nmode=%s\nhierarchy=%s\ndiagnostic=%s\npart=xc7a100tcsg324-1\nclock_period_ns=%s\n' \
    "$bits" "$bits" "$dim" "$mode" "$hierarchy" "$diagnostic" \
    "$([[ "$mode" == area || "$mode" == rtl ]] && echo none || echo 10)" > "$out/options.txt"
cd "$out/source"
run "$python" scripts/im2p_config.py --profile "$bits" "$bits" "$dim" > "$out/profile.json"
find src synth scripts config sim Makefile -type f ! -name '*.pyc' | LC_ALL=C sort | \
    while IFS= read -r file; do "${hash[@]}" "$file"; done > "$out/source.sha256"
run "$bsc" -u -verilog -p +:src/common:src/io:src/array:src/vector:src/accumulator:src/control:src/core:synth \
    -steps 4000000 -steps-warn-interval 1000000 -steps-max-intervals 20 +RTS -K256M -RTS \
    -bdir "$out/bsc" -info-dir "$out/info" -vdir "$out/rtl" -g "$top" "synth/$package.bsv" \
    2>&1 | tee "$out/bsc.log"
[[ -s "$out/rtl/$top.v" ]] || fail 'BSC did not produce top RTL'
shopt -s nullglob
# Conservative transitive module-name scan; Verilator and Vivado reject unresolved modules.
changed=1
while ((changed)); do
    changed=0
    for file in "$primitive_dir"/*.v; do
        name=$(basename "$file" .v)
        [[ ! -e "$out/primitives/$name.v" ]] || continue
        if grep -Eq "^[[:space:]]*$name[[:space:]]*(#|[[:alpha:]_])" "$out/rtl/"*.v "$out/primitives/"*.v; then
            cp "$file" "$out/primitives/"; changed=1
        fi
    done
done
cd "$out"
find rtl primitives -type f -name '*.v' | LC_ALL=C sort | \
    while IFS= read -r file; do "${hash[@]}" "$file"; done > generated.sha256
"${hash[@]}" source.sha256 generated.sha256 profile.json options.txt > run-inputs.sha256
"${hash[@]}" run-inputs.sha256 > run-fingerprint.sha256
run "$verilator" --version > verilator-version.txt
run "$verilator" --lint-only -Wno-fatal --top-module "$top" rtl/*.v primitives/*.v 2>&1 | tee rtl-lint.log
if [[ "$mode" == rtl ]]; then
    printf 'RTL_COMPLETE (no Vivado run)\n' > status.txt
else
    run "$vivado" -version > vivado-version.txt
    run "$vivado" -mode batch -nojournal -log vivado.log -source source/synth/flow/ooc.tcl \
        -tclargs "$out" "$top" "$mode" "$hierarchy" 2>&1 | tee vivado-console.log
    [[ -f vivado-complete.txt ]] || fail 'Vivado returned without completion marker'
    printf 'VIVADO_COMPLETE (inspect timing/DRC reports; no board closure claim)\n' > status.txt
fi
printf '%s: %s\n' "$(cat status.txt)" "$out"
