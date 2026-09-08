#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
mkdir -p build/int32-synth-agent
test_dir=$(mktemp -d "$root/build/int32-synth-agent/flow-checks-XXXXXX")
flow="$root/scripts/fpga_build.sh"
bash -n "$flow"
"$flow" --help > "$test_dir/help.txt"
expect_failure() {
    local label=$1 expected=$2; shift 2
    local status=0
    "$@" > "$test_dir/$label.log" 2>&1 || status=$?
    [[ "$status" == "$expected" ]] || { cat "$test_dir/$label.log"; exit 1; }
}
expect_failure invalid 2 "$flow" --mode bogus
expect_failure missing_value 2 "$flow" --dim
expect_failure invalid_route 2 "$flow" --mode route --dim 64
expect_failure missing_bsc 2 env BSC="$test_dir/missing-bsc" "$flow" --mode rtl
expect_failure missing_vivado 2 env VIVADO="$test_dir/missing-vivado" "$flow" --mode area
expect_failure existing 2 "$flow" --mode rtl --out "$test_dir"
expect_failure tcl_failure 1 tclsh synth/flow/ooc.tcl
real_bsc=$(command -v "${BSC:-bsc}")
cat > "$test_dir/failing-bsc" <<'EOF'
#!/usr/bin/env bash
if [[ "$1" == -u ]]; then echo 'injected compiler failure'; exit 71; fi
exec "$FPGA_REAL_BSC" "$@"
EOF
chmod +x "$test_dir/failing-bsc"
expect_failure compiler_pipeline 71 env BSC="$test_dir/failing-bsc" FPGA_REAL_BSC="$real_bsc" \
    "$flow" --mode rtl --diagnostic fifo --out "$test_dir/compiler-failure"
grep -qx 'FAILED exit=71' "$test_dir/compiler-failure/status.txt"
[[ ! -e "$test_dir/compiler-failure/vivado-complete.txt" ]]
cat > "$test_dir/failing-vivado" <<'EOF'
#!/usr/bin/env bash
if [[ "$1" == -version ]]; then echo 'failure-test stub, not Vivado'; exit 0; fi
exit 72
EOF
chmod +x "$test_dir/failing-vivado"
expect_failure vivado_pipeline 72 env VIVADO="$test_dir/failing-vivado" \
    "$flow" --mode area --diagnostic fifo --out "$test_dir/vivado-failure"
grep -qx 'FAILED exit=72' "$test_dir/vivado-failure/status.txt"
[[ ! -e "$test_dir/vivado-failure/vivado-complete.txt" ]]
expect_failure missing_marker 2 env VIVADO=true \
    "$flow" --mode area --diagnostic fifo --out "$test_dir/vivado-no-marker"
grep -qx 'FAILED exit=2' "$test_dir/vivado-no-marker/status.txt"
"$flow" --mode rtl --diagnostic fifo --out "$test_dir/fifo" > "$test_dir/fifo.log" 2>&1
grep -qx 'RTL_COMPLETE (no Vivado run)' "$test_dir/fifo/status.txt"
[[ -s "$test_dir/fifo/primitives/FIFO2.v" && -s "$test_dir/fifo/profile.json" ]]
iverilog -g2012 -s tb_fifo2_diagnostic -o "$test_dir/fifo-test" tests/tb_fifo2_diagnostic.sv "$test_dir/fifo/primitives/FIFO2.v"
vvp "$test_dir/fifo-test" | tee "$test_dir/fifo-test.log"
grep -q 'FIFO2_DIAGNOSTIC PASS' "$test_dir/fifo-test.log"
printf 'FPGA_FLOW_CHECKS PASS %s\n' "$test_dir"
